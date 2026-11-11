"""Ko'p-firmali tizim: firma bo'yicha client va login.

Har bir kompaniya:
  - o'z kredensiallariga ega bo'lsa (username/password) -> alohida BMClient
    quriladi va token shu kompaniya nomi ostida alohida faylda saqlanadi;
  - aks holda adminning asosiy tokeni ishlatiladi va (agar profileId bo'lsa)
    login_by_profile orqali shu profil tokeni olinadi.

`client_for_profile` barcha repo/dashboard qatlamlari uchun yagona kirish
nuqtasi. Har doim YANGI client qaytaradi — chaqiruvchi uni o'zi boshqaradi.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from ..config.settings import Config, get_config
from . import token_health
from .tokens import _jwt_exp, company_tokens_path, load_tokens
from .profiles import get_profile

log = logging.getLogger("bm_automation.companies")

__all__ = ["client_for_profile", "client_for_name", "company_token_path",
           "company_token_state", "own_client", "route_accessible"]


def company_token_path(name: str):
    return company_tokens_path(name)


def _own_client(profile: dict, auto_relogin: bool = True) -> "object":
    """Kompaniyaning o'z kredensiallari bilan client quriladi."""
    from ..api.client import BMClient

    base = get_config().base_url
    cfg = Config(
        base_url=base,
        username=str(profile.get("username") or ""),
        password=str(profile.get("password") or ""),
        organization=str(profile.get("name") or ""),
    )
    client = BMClient(cfg, token_path=str(company_token_path(profile["name"])))
    client.auto_relogin = auto_relogin
    client.login()
    return client


def own_client(profile: dict):
    """Kompaniyaning O'Z kredensiallari bilan client (fallbacksiz).

    `client_for_profile`dan farqli — admin tokeniga qaytmaydi: onboarding
    paytida `bmUsername`/`bmPassword` haqiqatan ishlashini tekshirish uchun.
    """
    if not (str(profile.get("username") or "").strip()
            and str(profile.get("password") or "").strip()):
        raise ValueError("Kompaniya kredensiallari yo'q")
    return _own_client(profile)


def route_accessible(client, route_id: str) -> bool:
    """Token berilgan yo'nalishga kira oladimi (boshqa firma route'i 500/403)."""
    return _detect_via_route(client, str(route_id or "").strip())


def _admin_client(profile: dict, auto_relogin: bool = True) -> "object":
    """Adminning asosiy tokeni; profileId bo'lsa shu profilga o'tiladi."""
    from ..api.client import BMClient

    client = BMClient()
    client.auto_relogin = auto_relogin
    client.login()
    pid = str(profile.get("profileId") or "").strip()
    if pid:
        client.login_for_profile(pid, fallback_to_main=True)
    return client


def _allow_admin_fallback() -> bool:
    """Kompaniya tokeni olinmasa admin tokeniga o'tishga ruxsat bormi.

    Standart holat — YO'Q: aks holda boshqa kompaniya ma'lumoti
    ko'rinishi mumkin. Faqat vaqtinchalik tiklash uchun
    `BM_ALLOW_COMPANY_ADMIN_FALLBACK=1` qo'lda yoqiladi.
    """
    return str(os.getenv("BM_ALLOW_COMPANY_ADMIN_FALLBACK", "")).strip().lower() \
        in {"1", "true", "yes", "on"}


def client_for_profile(profile: dict, auto_relogin: bool = True):
    """Profil (kompaniya) uchun authenticated client qaytaradi.

    Kompaniyaning o'z kredensiallari bo'lsa faqat o'z tokeni ishlatiladi.
    Token olinmasa xato ko'tariladi — boshqa kompaniya tokeniga jimgina
    o'tilmaydi. Istisno: `BM_ALLOW_COMPANY_ADMIN_FALLBACK=1` (vaqtinchalik).
    Kredensiali yo'q eski profillar uchun admin tokeni ishlatiladi.
    `auto_relogin=False` — OneID brauzer loginiga tushmaslik uchun
    (fon/onboarding vazifalarida).
    """
    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("Profil nomi ko'rsatilmagan")
    own = bool((profile.get("username") or "").strip()
               and (profile.get("password") or "").strip())
    try:
        if own:
            try:
                return _own_client(profile, auto_relogin=auto_relogin)
            except Exception as exc:  # noqa: BLE001 - sababni aniq ko'rsatamiz
                if not _allow_admin_fallback():
                    raise RuntimeError(
                        f"kompaniya tokeni olinmadi: {exc}. Boshqa kompaniya "
                        "tokeniga o'tilmadi; login/parolni tekshiring"
                    ) from exc
                client = _admin_client(profile, auto_relogin=auto_relogin)
                _warn_foreign_token(name, client, str(exc))
                return client
        client = _admin_client(profile, auto_relogin=auto_relogin)
        _warn_foreign_token(name, client, "kredensial yo'q")
        return client
    except Exception as exc:
        raise type(exc)(f"[{name}] client qurish xatosi: {exc}") from exc


def _iso_ts(value: str) -> int:
    """ISO vaqtni epoch sekundga aylantiradi; xato bo'lsa 0."""
    try:
        return int(datetime.fromisoformat(str(value)).timestamp())
    except Exception:  # noqa: BLE001
        return 0


def company_token_state(company: dict) -> dict:
    """Dashboard uchun kompaniya tokenining holati.

    `company` — `grouped_profiles()` elementi: name, routes, has_credentials.
    Tokenning o'zi qaytarilmaydi — faqat holat, muddat va vaqtlar.
    """
    name = str(company.get("name") or "").strip()
    path = company_token_path(name)
    data = load_tokens(path=path) or {}
    access = str(data.get("access_token") or "")
    organization = str(data.get("organization") or "").strip()
    route_list = list(company.get("routes") or [])
    route_count = int(company.get("routeCount") or len(route_list) or 0)
    profile_ids = sorted({str(r.get("profileId") or "").strip()
                          for r in route_list if str(r.get("profileId") or "").strip()})

    now = time.time()
    exp = _jwt_exp(access)
    expires_ts = int(exp or _iso_ts(data.get("expires_at") or ""))
    remaining_s = max(0, int(expires_ts - now)) if expires_ts else 0
    last_refresh_at = str(data.get("last_refresh_at") or data.get("obtained_at") or "")
    last_refresh_ts = _iso_ts(last_refresh_at)
    if not last_refresh_ts and path.exists():
        last_refresh_ts = int(path.stat().st_mtime)

    has_credentials = bool(company.get("has_credentials"))
    # Token haqiqatan shu kompaniyaniki ekanini tekshirish: refresh tarixi
    # bo'lmasa ham token faylidagi organization firma nomiga mos bo'lishi kerak.
    org_matches: bool | None = None
    if access and organization and name:
        org_matches = _same_company(name, organization)

    if not has_credentials:
        status = "no_credentials"
    elif not access:
        status = "missing"
    elif remaining_s <= 0:
        status = "expired"
    elif org_matches is False:
        status = "wrong_company"
    elif remaining_s < 600:
        status = "soon"
    else:
        status = "ok"

    # Oxirgi muvaffaqiyatli refresh: timer/panel yozgan tarix bo'lsa o'sha,
    # aks holda token faylining o'z vaqti (tarix hali bo'sh bo'lsa ham panel
    # to'ldirilgan ko'rinadi).
    health = token_health.get(name)
    last_ok_at = health["last_ok_at"] or last_refresh_at
    last_ok_ts = _iso_ts(last_ok_at) or last_refresh_ts

    return {
        "name": name,
        "status": status,
        "valid": bool(access) and remaining_s > 0,
        "has_token": bool(access),
        "has_credentials": has_credentials,
        "username": str(company.get("username") or ""),
        "organization": organization,
        "organization_matches": org_matches,
        "last_refresh_at": last_refresh_at,
        "last_refresh_ts": last_refresh_ts,
        "last_ok_at": last_ok_at,
        "last_ok_ts": last_ok_ts,
        "last_ok_result": health["last_ok_result"],
        "last_attempt_at": health["last_attempt_at"],
        "last_attempt_ts": _iso_ts(health["last_attempt_at"]),
        "last_result": health["last_result"],
        "last_error": health["last_error"],
        "consecutive_errors": health["consecutive_errors"],
        "expires_at": str(data.get("expires_at") or ""),
        "expires_ts": expires_ts,
        "remaining_s": remaining_s,
        "route_count": route_count,
        "profile_ids": profile_ids,
    }


def _same_company(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or a in b or b in a))


def _warn_foreign_token(name: str, client, error: str = "") -> None:
    """Kompaniya o'z tokenini ololmagan bo'lsa ogohlantiradi.

    Bunday holatda admin tokeni ishlatiladi — u boshqa kompaniyaniki
    bo'lishi mumkin. Jim ishlash firma ma'lumotlarini aralashtirib
    yuborishi mumkin, shuning uchun log'ga aniq yoziladi va yechim
    ko'rsatiladi.
    """
    try:
        data = load_tokens(path=getattr(client, "token_path", None)) or {}
        org = str(data.get("organization") or "").strip()
    except Exception:  # noqa: BLE001
        org = ""
    if _same_company(name, org):
        return
    log.warning(
        "[%s] kompaniya tokeni yo'q (sabab: %s); hozir %s tokeni "
        "ishlatilmoqda. Tuzatish: python -m bm_automation "
        "login-browser --company \"%s\" --visible",
        name, error or "noma'lum", org or "asosiy", name)


def client_for_name(name: str, username: str = "", password: str = ""):
    """Kompaniya nomi bo'yicha client. (username/password berilmasa
    saqlangan profil kredensiallaridan foydalanadi.)"""
    profile = get_profile(name)
    if not profile:
        raise ValueError(f"Kompaniya topilmadi: {name}")
    if username and password:
        profile = dict(profile)
        profile["username"] = username
        profile["password"] = password
    return client_for_profile(profile)


def _norm(s: str) -> str:
    """Firma nomlarini solishtirish uchun normalizatsiya.

    Qo'shtirnoqlar (tiltli va tekis) hisobga olinmaydi: BM'da profil nomi
    `"ASL SUNDAY" MCHJ` ko'rinishida, sozlamalarda esa `ASL SUNDAY MCHJ`
    bo'lishi mumkin — ikkalasi bitta firma.
    """
    import re

    cleaned = re.sub(r"[\u201c\u201d\"']", "", str(s or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _detect_via_route(client, route_id: str) -> bool:
    """Berilgan token bilan route'ga kirish mumkinligini tekshiradi.

    Boshqa firmaga tegishli routeVariantId joriy token bilan 500/403
    qaytaradi, o'z firmasining route'i 200 -> qaysi firma ekanini shu
    orqali aniqlaymiz.
    """
    from ..repositories.gross_repo import GrossRepository
    from ..api.client import BMApiError

    from datetime import date

    today = date.today().isoformat()
    try:
        data = GrossRepository(client).route(route_id, today, today)
        return isinstance(data, dict) and bool(data)
    except BMApiError as exc:
        # Boshqa firma route'i 403/500 qaytaradi — bu normal "manu emas".
        log.debug("route %s kirish yoq (%s): %s", route_id, exc.status, exc)
        return False
    except Exception as exc:  # noqa: BLE001
        # Tarmoq/timeout xatosi ham False qaytaradi — aniqlash javobi
        # "firmani topa olmadim" bo'lib qoladi; sabab jurnaldan ko'rinadi.
        log.warning("route %s tekshiruvi xato: %s", route_id, exc)
        return False


def detect_company(username: str, password: str):
    """Yangi login/parol bilan qaysi firma ekanini avtomatik aniqlaydi.

    BM API'da username/password autentifikatsiya OneID sabab ishlamaydi
    (401), shuning uchun firma `profiles.json` da saqlangan kredensiallar
    bilan solishtirib topiladi. Agar kredensial oldindan saqlangan bo'lmasa,
    API orqali ham sinab ko'riladi (ba'zi firmalar to'g'ridan-to'g'ri login
    qilishsa).

    Qaytaradi: (profile, client) — topilmasa (None, None).
    """
    from ..api.client import BMClient
    from ..config.settings import Config, get_config
    from .profiles import all_profiles

    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return None, None

    # 1) Saqlangan kredensiallar bilan solishtirish (asosiy usul).
    for p in all_profiles():
        saved_u = str(p.get("username") or "").strip()
        saved_p = str(p.get("password") or "").strip()
        if saved_u and saved_u.lower() == username.lower() and saved_p == password:
            return p, None  # client keyin client_for_profile orqali olinadi

    # 2) Fallback: to'g'ridan-to'g'ri API login (agar ishlasa).
    base = get_config().base_url
    cfg = Config(
        base_url=base,
        username=username,
        password=password,
        organization="",
    )
    client = BMClient(cfg, token_path=str(company_tokens_path("_detect")))
    try:
        client.login(force=True)
    except Exception:
        return None, None

    candidates = []
    for p in all_profiles():
        rid = str(p.get("routeVariantId") or "").strip()
        if not rid:
            continue
        if _detect_via_route(client, rid):
            candidates.append(p)

    # Bir nechta mos kelsa (masalan admin tokeni) — orgName bilan solishtiramiz.
    if len(candidates) > 1:
        name = _norm(username)
        for p in candidates:
            if name and name in _norm(p.get("name") or ""):
                return p, client
        for p in candidates:
            org = _norm(p.get("orgName") or p.get("routeName") or "")
            if org and name in org:
                return p, client
        return None, client
    if candidates:
        return candidates[0], client
    return None, client
