"""BM API uchun yagona HTTP-klient qatlami (production-grade).

Barcha HTTP logika (auth, retry, backoff, rate-limit, loglash, korrelyatsiya)
shu layerga yig'ilgan. Business logic hech qachon to'g'ridan-to'g'ri
`requests` ishlatmaydi — faqat shu klient orqali.

Asosiy xususiyatlar:
  * GET / POST / PUT / DELETE + `download` (stream)
  * timeout (har so'rov uchun)
  * xavfsiz (idempotent) so'rovlarda 5xx / tarmoq xatolari uchun
    exponential backoff + jitter bilan retry
  * 4xx avtomatik takrorlanmaydi; 429 (rate-limit) Retry-After'ga rioya qiladi
  * HTTP status handling va JSON validatsiya
  * auth: access token -> 401 bo'lsa refresh -> refresh ishlamasa OneID login
    -> yangi tokenlar saqlanadi -> original so'rov bir marta qayta bajariladi
  * strukturli loglash (status, response time, endpoint, success/failure)
  * har so'rov uchun correlation ID (X-Correlation-ID)
  * tarmoq xatolari aniq xatolarga aylantiriladi
  * SECURITY: token / password / Authorization header hech qachon
    log yoki exception message'ga chiqmaydi.

Xatolik sinflari:
  * BMApiError        — HTTP status >= 400 bo'lgan API xatosi
  * BMAuthError       — avtorizatsiya bilan bog'liq xatolar (BMApiError vorisi)
  * BMRateLimitError  — 429 rate-limit (BMApiError vorisi)
  * BMJSONError       — server noto'g'ri JSON qaytardi (BMApiError vorisi)
"""

from __future__ import annotations

import logging
import os
import random
import time
import uuid
from typing import Any, Callable, Optional

import requests

from ..config.settings import USER_MGMT, get_config
from ..core.tokens import load_tokens, save_tokens
from ..utils.logger import get_logger

API_ERROR_MSG = {
    400: "noto'g'ri so'rov",
    401: "avtorizatsiya talab qilinadi",
    403: "ruxsat yo'q",
    404: "topilmadi",
    409: "ziddiyat",
    422: "ma'lumotlar yaroqsiz",
    429: "juda ko'p so'rov",
    500: "server xatosi",
}

# --- Backward-compat konstantalar (eski kodlar uchun) ---
NETWORK_RETRIES = 3
NETWORK_RETRY_DELAY = 1.0

# --- Retry / backoff ---
DEFAULT_TIMEOUT = 120.0
DEFAULT_DOWNLOAD_TIMEOUT = 180.0
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
RETRY_BACKOFF_MULTIPLIER = 2.0
RETRY_MAX_DELAY = 30.0
RETRY_JITTER = 0.15

# Faqat shu metodlar transient xatolarda takrorlanadi (idempotent).
# POST takrorlanmaydi — ikkilamchi yozuv (dublikat) xavfi tufayli.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

# Log yoki exception'ga tushganda yashiradigan kalitlar.
_SENSITIVE_KEYS = frozenset({
    "password", "oldpassword", "newpassword", "passcode", "secret",
    "access_token", "refresh_token", "authorization", "api_key", "token",
})

_DEFAULT_ERROR = "noma'lum xato"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


class BMApiError(Exception):
    """HTTP status >= 400 bo'lgan API xatosi."""

    def __init__(self, status: int, code: Any = None, message: Any = None, detail: str = ""):
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(
            f"API xatosi (HTTP {status}): {code or message or detail or API_ERROR_MSG.get(status, _DEFAULT_ERROR)}"
        )


class BMAuthError(BMApiError):
    """Avtorizatsiya/avtentifikatsiya bilan bog'liq xato."""


class BMRateLimitError(BMApiError):
    """HTTP 429 — so'rovlar limiti (rate-limit)."""

    def __init__(self, status: int = 429, retry_after: float | None = None,
                 message: str = "juda ko'p so'rov", detail: str = ""):
        self.retry_after = retry_after
        super().__init__(status, message=message, detail=detail)


class BMJSONError(BMApiError):
    """Server JSON emas / noto'g'ri JSON qaytardi."""


class BMClient:
    """bm.dtransport.uz API bilan ishlash uchun yagona klient.

    Login -> token saqlash -> har bir so'rovga Bearer token ->
    401 bo'lsa refresh -> refresh ishlamasa OneID login -> original so'rov
    bir marta qayta bajariladi.
    """

    def __init__(self, config=None):
        self.config = config or get_config()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Language": "uz",
                "User-Agent": "bm-automation/0.1",
            }
        )
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.logger: logging.Logger = get_logger("bm_automation.http")
        # Bir nechta so'rovdan iborat bajarilish uchun session-level ID.
        self.correlation_id: str = uuid.uuid4().hex
        # refresh ishlamaganda OneID brauzer loginini avtomatik bajarish
        # (BM_AUTO_RELOGIN=0 bilan o'chiriladi).
        self.auto_relogin: bool = _env_flag("BM_AUTO_RELOGIN", default=True)
        # Har bir response uchun chaqiriladigan hook (monitoring/statistika).
        self.response_hook: Optional[Callable[[dict], None]] = None

    # ===================== AUTH =====================

    def set_tokens(self, access_token: str, refresh_token: str | None = None) -> None:
        """Tokenlarni xotiraga va tokens.json'ga yozadi."""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session.headers["Authorization"] = f"Bearer {access_token}"
        save_tokens(
            access_token,
            refresh_token,
            base_url=self.config.base_url,
            organization=getattr(self.config, "organization", ""),
        )

    def load_tokens_from_file(self) -> bool:
        data = load_tokens()
        if data and data.get("access_token"):
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token")
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            return True
        return False

    def login(self, force: bool = False) -> None:
        """Saqlangan token bor bo'lsa ishlatadi, aks holda login qiladi."""
        if not force and self.load_tokens_from_file():
            return
        url = f"{self.config.base_url}{USER_MGMT}/user-profile/login"
        cid = self._new_cid()
        start = time.monotonic()
        try:
            resp = self._send(
                "POST", url,
                json={"username": self.config.username, "password": self.config.password},
                timeout=60, cid=cid,
            )
        except requests.RequestException as exc:
            raise BMAuthError(0, message="login so'rovi tarmoq xatosi") from exc
        self._monitor(cid=cid, method="POST", url=url,
                      status=resp.status_code, duration_ms=(time.monotonic() - start) * 1000)
        try:
            data = self._parse(resp, cid=cid)
        except BMApiError as exc:
            raise BMAuthError(exc.status, code=exc.code, message=exc.message, detail=exc.detail) from exc
        if not isinstance(data, dict):
            raise BMAuthError(200, message="login javobi noto'g'ri format")
        self.set_tokens(data.get("access_token"), data.get("refresh_token"))

    def login_via_oneid(self, code: str) -> dict:
        """OneID code ni token + profillar ro'yxatiga almashtiradi."""
        url = f"{self.config.base_url}{USER_MGMT}/user-profile/login-via-oneid/profiles"
        cid = self._new_cid()
        start = time.monotonic()
        resp = self._send("POST", url, params={"code": code}, timeout=60, cid=cid)
        self._monitor(cid=cid, method="POST", url=url,
                      status=resp.status_code, duration_ms=(time.monotonic() - start) * 1000)
        return self._parse(resp, cid=cid)

    def login_by_profile(self, profile_id) -> None:
        """Tanlangan kompaniya (profile) uchun token oladi.

        Token faqat xotirada o'rnatiladi, tokens.json YOZILMAYDI — aks holda
        asosiy (OneID) token muddatidan oldin bekor bo'lib qoladi.
        """
        url = f"{self.config.base_url}{USER_MGMT}/user-profile/get-token-by-profile/{profile_id}"
        cid = self._new_cid()
        start = time.monotonic()
        resp = self._send("POST", url, timeout=60, cid=cid)
        self._monitor(cid=cid, method="POST", url=url,
                      status=resp.status_code, duration_ms=(time.monotonic() - start) * 1000)
        data = self._parse(resp, cid=cid)
        if isinstance(data, dict) and data.get("access_token"):
            self.access_token = data["access_token"]
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
        else:
            raise BMAuthError(200, message="get-token-by-profile javobi noto'g'ri")

    def account_authorities(self) -> Any:
        """Token amal qilishini tekshiradi; 401 bo'lsa refresh/relogin qiladi."""
        return self.request("GET", f"{USER_MGMT}/user-profile/account-authorities", timeout=60)

    def _refresh(self) -> None:
        """Refresh token orqali yangi access token oladi va saqlaydi."""
        if not self.refresh_token:
            raise BMAuthError(401, message="refresh token mavjud emas")
        url = f"{self.config.base_url}{USER_MGMT}/user-profile/refresh-token"
        cid = self._new_cid()
        start = time.monotonic()
        try:
            resp = self._send("POST", url,
                              json={"refreshToken": self.refresh_token},
                              timeout=60, cid=cid)
        except requests.RequestException as exc:
            raise BMAuthError(401, message="refresh so'rovi tarmoq xatosi") from exc
        self._monitor(cid=cid, method="POST", url=url,
                      status=resp.status_code, duration_ms=(time.monotonic() - start) * 1000)
        try:
            data = self._parse(resp, cid=cid)
        except BMApiError as exc:
            raise BMAuthError(exc.status, code=exc.code, message=exc.message, detail=exc.detail) from exc
        if not isinstance(data, dict):
            raise BMAuthError(200, message="refresh javobi noto'g'ri format")
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", self.refresh_token)
        if not new_access:
            raise BMAuthError(200, message="refresh orqali token olinmadi")
        self.set_tokens(new_access, new_refresh)

    def _refresh_or_relogin(self) -> None:
        """401'da: avval refresh; ishlamasa OneID brauzer login."""
        try:
            self._refresh()
        except Exception:
            self.logger.warning("refresh token ishlamadi; OneID login qilinmoqda")
            if not self.auto_relogin:
                raise
            from ..auth.browser_login import browser_login
            browser_login(headless=True, timeout=300)
            self.load_tokens_from_file()
            if not self.access_token:
                raise BMAuthError(401, message="OneID login access token bermadi")

    def _ensure_auth(self) -> None:
        if not self.access_token:
            self.login()

    # ===================== LOW-LEVEL =====================

    def _new_cid(self) -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _build_url(base_url: str, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{base_url}{path}"

    def _send(self, method: str, url: str, *, params=None, json=None,
              timeout=None, cid: str | None = None, stream: bool = False) -> requests.Response:
        """Bitta so'rovni correlation ID header bilan jo'natadi."""
        headers = {"X-Correlation-ID": cid or self._new_cid()}
        return self.session.request(method, url, params=params, json=json,
                                    timeout=timeout, headers=headers, stream=stream)

    def _monitor(self, *, cid, method, url, status=None, duration_ms=None, error=None) -> None:
        """Har bir response: status, response time, endpoint, success/failure."""
        if error is not None:
            self.logger.warning("[%s] %s %s XATO: %s", cid, method, url, error)
        else:
            outcome = "OK" if (status is not None and status < 400) else "XATO"
            self.logger.info(
                "[%s] %s %s -> HTTP %s | %.0f ms | %s",
                cid, method, url, status, duration_ms, outcome,
            )
        if self.response_hook is not None:
            try:
                self.response_hook({
                    "cid": cid,
                    "method": method,
                    "url": url,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": error,
                })
            except Exception:
                self.logger.debug("response_hook ishlamadi", exc_info=True)

    def _redact(self, text: str) -> str:
        """Matndan access/refresh tokenlarni yashiradi (security)."""
        if not text:
            return text
        for secret in (self.access_token, self.refresh_token):
            if secret and secret in text:
                text = text.replace(secret, "***")
        return text

    def _decode_json(self, resp: requests.Response) -> Any:
        if resp.status_code == 204:
            return None
        content_type = (resp.headers.get("Content-Type") or "").lower()
        try:
            return resp.json()
        except ValueError:
            if resp.status_code >= 400:
                return None  # xatolik matni JSON bo'lmasa _parse resp.text ishlatadi
            if "application/json" in content_type:
                raise BMJSONError(resp.status_code, message="server JSON qaytarmadi")
            return None

    def _parse(self, resp: requests.Response, cid: str | None = None) -> Any:
        body = self._decode_json(resp)
        if resp.status_code >= 400:
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else None
            detail = ""
            if isinstance(body, dict):
                detail = str(body.get("data") or "")[:300]
            elif resp.text:
                detail = resp.text[:300]
            if resp.status_code == 429:
                raise BMRateLimitError(429, retry_after=self._retry_after_seconds(resp))
            raise BMApiError(
                resp.status_code,
                code=code,
                message=self._redact(str(message)) if message else None,
                detail=self._redact(detail),
            )
        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    # ===================== RETRY / BACKOFF =====================

    def _backoff_seconds(self, attempt: int) -> float:
        delay = RETRY_BASE_DELAY * (RETRY_BACKOFF_MULTIPLIER ** attempt)
        delay = min(delay, RETRY_MAX_DELAY)
        return max(0.1, delay + random.uniform(-RETRY_JITTER, RETRY_JITTER))

    def _backoff_sleep(self, attempt: int) -> None:
        delay = self._backoff_seconds(attempt)
        self.logger.info("retry uchun kutish: %.1f s (urinish %d)", delay, attempt + 1)
        time.sleep(delay)

    @staticmethod
    def _retry_after_seconds(resp: requests.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    # ===================== PUBLIC REQUEST API =====================

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Any = None,
        retries: int = MAX_RETRIES,
        timeout: float | tuple | None = None,
    ) -> Any:
        """Asosiy so'rov metodi.

        Retry qoidalari:
          * 4xx — hech qachon avtomatik takrorlanmaydi (401 alohida: auth).
          * 5xx va tarmoq xatolari — faqat idempotent metodlarda exponential
            backoff bilan `retries` marta takrorlanadi.
          * 429 — Retry-After'ga rioya qilib (idempotent) takrorlanadi.
          * POST takrorlanmaydi (dublikat xavfi).

        Auth: 401 -> refresh -> refresh ishlamasa OneID login -> yangi tokenlar
        saqlanadi -> original so'rov bir marta qayta bajariladi.
        """
        method = method.upper()
        retries = max(0, int(retries or 0))
        timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        idempotent = method in _IDEMPOTENT_METHODS
        url = self._build_url(self.config.base_url, path)

        self._ensure_auth()
        auth_replayed = False
        status: Optional[int] = None
        attempt = 0

        while attempt <= retries:
            cid = self._new_cid()
            start = time.monotonic()
            try:
                resp = self._send(method, url, params=params, json=json, timeout=timeout, cid=cid)
            except requests.RequestException as exc:
                duration_ms = (time.monotonic() - start) * 1000
                self._monitor(cid=cid, method=method, url=url,
                              duration_ms=duration_ms, error=type(exc).__name__)
                if idempotent and attempt < retries:
                    self._backoff_sleep(attempt)
                    attempt += 1
                    continue
                raise BMApiError(0, message=f"tarmoq xatosi: {type(exc).__name__}") from exc

            status = resp.status_code
            duration_ms = (time.monotonic() - start) * 1000
            self._monitor(cid=cid, method=method, url=url, status=status, duration_ms=duration_ms)

            # ---- Auth: 401 -> refresh/relogin -> bir marta qayta bajarish ----
            if status == 401 and not auth_replayed:
                try:
                    self._refresh_or_relogin()
                except BMAuthError:
                    raise
                except Exception as exc:
                    raise BMAuthError(401, message="token yangilab bo'lmadi") from exc
                auth_replayed = True
                continue  # attempt sarflanmaydi

            if status == 401:
                raise BMAuthError(401, message="token yangilangandan keyin ham avtorizatsiya rad etildi")

            # ---- Rate-limit ----
            if status == 429:
                retry_after = self._retry_after_seconds(resp)
                if idempotent and attempt < retries:
                    delay = retry_after if retry_after is not None else self._backoff_seconds(attempt)
                    self.logger.info("rate-limit: %.1f s kutish (Retry-After)", delay)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise BMRateLimitError(429, retry_after=retry_after)

            # ---- 5xx: faqat idempotent ----
            if status >= 500 and idempotent and attempt < retries:
                self._backoff_sleep(attempt)
                attempt += 1
                continue

            return self._parse(resp, cid=cid)

        raise BMApiError(status or 500, message="so'rov yakuniy muvaffaqiyatsiz")

    def download(self, path: str, out_file: str, params: Optional[dict] = None,
                 timeout: float | tuple | None = None) -> str:
        """Faylni stream bilan yuklab oladi (Excel/hisobot)."""
        timeout = timeout if timeout is not None else DEFAULT_DOWNLOAD_TIMEOUT
        url = self._build_url(self.config.base_url, path)
        self._ensure_auth()
        auth_replayed = False

        while True:
            cid = self._new_cid()
            start = time.monotonic()
            try:
                resp = self._send("GET", url, params=params, timeout=timeout, cid=cid, stream=True)
            except requests.RequestException as exc:
                duration_ms = (time.monotonic() - start) * 1000
                self._monitor(cid=cid, method="GET", url=url,
                              duration_ms=duration_ms, error=type(exc).__name__)
                raise BMApiError(0, message=f"yuklab olish: tarmoq xatosi: {type(exc).__name__}") from exc

            status = resp.status_code
            duration_ms = (time.monotonic() - start) * 1000
            self._monitor(cid=cid, method="GET", url=url, status=status, duration_ms=duration_ms)

            if status == 401 and not auth_replayed:
                self._refresh_or_relogin()
                auth_replayed = True
                continue

            if status >= 400:
                raise BMApiError(status, detail=f"yuklab olish muvaffaqiyatsiz: {status}")

            try:
                with open(out_file, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
            except requests.RequestException as exc:
                raise BMApiError(0, message=f"yuklab olish to'xtadi: {type(exc).__name__}") from exc
            return out_file

    # ===================== CONVENIENCE =====================

    def get(self, path: str, params: Optional[dict] = None,
            retries: int = MAX_RETRIES, timeout: float | tuple | None = None) -> Any:
        return self.request("GET", path, params=params, retries=retries, timeout=timeout)

    def post(self, path: str, json: Any = None, params: Optional[dict] = None,
             timeout: float | tuple | None = None) -> Any:
        return self.request("POST", path, params=params, json=json, timeout=timeout)

    def put(self, path: str, json: Any = None, timeout: float | tuple | None = None) -> Any:
        return self.request("PUT", path, json=json, timeout=timeout)

    def delete(self, path: str, timeout: float | tuple | None = None) -> Any:
        return self.request("DELETE", path, timeout=timeout)

    def list_all(self, path: str, page_param: str = "page", size_param: str = "size",
                 page_size: int = 100, max_pages: int = 100) -> list:
        """Paginated GET; sahifalangan listni to'liq yig'adi."""
        page = 0
        all_items: list = []
        while page < max_pages:
            data = self.get(path, params={page_param: page, size_param: page_size})
            items, total = self._extract_items(data)
            all_items.extend(items)
            if not items or (total is not None and len(all_items) >= total):
                break
            page += 1
        return all_items

    @staticmethod
    def _extract_items(data: Any):
        if isinstance(data, dict):
            content = data.get("content") or data.get("items") or data.get("list") or data.get("data") or data.get("rows")
            total = data.get("totalElements") or data.get("total") or data.get("totalItems")
            if isinstance(content, list):
                return content, total
        if isinstance(data, list):
            return data, len(data)
        return [], None
