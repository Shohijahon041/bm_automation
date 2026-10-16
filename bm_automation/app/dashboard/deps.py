"""FastAPI dependency'lar — autentifikatsiya, scope, rate limiting.

Eski server.py'dagi _resolve_scope, _enforce_role, _rate_limit_post
logikasi shu yerga ko'chirilgan. Har bir router endpoint'i Depends()
orqali ishlatadi.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.sections import SECTION_KEYS
from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard.deps")

# ---------------------------------------------------------------------------
# Konstantalar
# ---------------------------------------------------------------------------

_SESSION_MAX_AGE_S = 12 * 3600
_PBKDF2_ITERATIONS = 120_000

_ROLE_RANK = {"VIEWER": 0, "DISPATCHER": 1, "MANAGER": 2, "DIRECTOR": 3, "ADMIN": 4}

# Sahifa (bo'lim) kalitlari — `app/core/sections.py` dan olinadi (bot va
# panel bir xil nomlardan foydalanadi). Har bir dashboard foydalanuvchisiga
# shu kalitlardan iborat ro'yxat biriktirilishi mumkin: ro'yxat bo'lsa —
# foydalanuvchi FAQAT shu bo'limlarni ko'radi (rol beradigan imkoniyatlar
# ichidan qisqartiriladi, kengaytirilmaydi), ro'yxat yo'q bo'lsa — faqat rol
# bo'yicha (eski xatti-harakat).
PAGE_KEYS: tuple[str, ...] = SECTION_KEYS

# API prefiksi -> sahifa kaliti. Faqat sahifaga xos (1:1) endpointlar
# kiritilgan: umumiy agregat endpointlar (`/api/summary`, `/api/routes`,
# `/api/vehicles`, `/api/attendance`) bir nechta sahifaga xizmat qiladi,
# ularni sahifa bo'yicha to'sish noto'g'ri bo'lardi.
PAGE_API_PREFIXES: dict[str, str] = {
    "/api/drivers": "drivers",
    "/api/appeals": "appeals",
    "/api/electricity": "electricity",
    "/api/rejects": "rejects",
    "/api/brutto": "brutto",
    "/api/route-skm": "tariffs",
    "/api/kmrate": "tariffs",
    "/api/insights": "insights",
    "/api/myai": "myai",
    "/api/sms": "sms",
    "/api/admin": "admin",
    "/api/logs": "logs",
    "/api/disk": "disk",
    "/api/documents": "documents",
    "/api/users": "users",
    "/api/settings": "settings",
    "/api/dashboard-users": "dusers",
    "/api/video": "video",
}

_GET_ROLE_MIN: dict[str, int] = {
    "/api/users": 4, "/api/users/": 4, "/api/group-stats": 4,
    "/api/dispatchers": 4, "/api/disk": 4, "/api/backup": 4,
    "/api/logs": 4, "/api/settings/env": 4, "/api/settings/companies": 4,
    "/api/settings": 4, "/api/dashboard-users": 2, "/api/myai": 4,
    "/api/insights": 4, "/api/admin": 4, "/api/brutto": 2,
    "/api/rejects/tariff": 2, "/api/rejects": 2, "/api/kmrate": 2,
    "/api/route-skm": 2, "/api/electricity/price": 2, "/api/electricity": 2,
    "/api/drivers": 2, "/api/salary": 2, "/api/check": 3, "/api/sms": 3,
    "/api/documents": 3, "/api/me": 0,
    "/api/video": 1, "/api/video/cameras": 1, "/api/video/relay": 1,
    "/api/video/archive": 2, "/api/video/export": 4,
}

_POST_ROLE_MIN: dict[str, int] = {
    "/api/settings": 4, "/api/settings/env": 4, "/api/settings/companies": 4,
    "/api/dashboard-users": 2, "/api/admin": 4, "/api/myai": 4,
    "/api/sms": 3, "/api/documents": 3, "/api/rejects/tariff": 2,
    "/api/kmrate": 2, "/api/route-skm": 2, "/api/sync": 2,
    "/api/electricity/price": 4, "/api/users": 4, "/api/dispatchers": 4,
    "/api/video/cameras": 4, "/api/video/relay": 4, "/api/video/archive": 4,
    "/api/video/export": 4,
}

_ROUTE_SCOPED_PREFIXES = (
    "/api/summary", "/api/rejects", "/api/brutto", "/api/routes",
    "/api/route-skm", "/api/month", "/api/attendance", "/api/drivers",
    "/api/electricity", "/api/salary", "/api/export", "/api/check",
    "/api/appeals", "/api/kmrate", "/api/rejects/tariff",
    "/api/sms", "/api/documents", "/api/vehicles",
)

# ---------------------------------------------------------------------------
# Sessiya boshqaruvi
# ---------------------------------------------------------------------------

_SESSION_LOCK = threading.Lock()
_sessions: dict[str, dict] = {}


def _sessions_file() -> Path:
    return Path("state") / "dashboard_sessions.json"


_loaded_mtime: float | None = None


def _load_sessions() -> None:
    """Sessiyalar faylini o'qiydi, lekin fayl o'zgarmagan bo'lsa — xotiradagi
    nusxani qoldiradi. Har bir HLS segment so'rovida diskdan qayta o'qishni
    yo'q qiladi (100+ kamera oqimida muhim)."""
    global _sessions, _loaded_mtime
    p = _sessions_file()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        _sessions = {}
        _loaded_mtime = None
        return
    if _loaded_mtime == mtime:
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        _sessions = data if isinstance(data, dict) else {}
        _loaded_mtime = mtime
    except (OSError, json.JSONDecodeError):
        _sessions = {}
        _loaded_mtime = None


def _save_sessions() -> None:
    global _loaded_mtime
    try:
        from ..utils.io import atomic_write
        p = _sessions_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(_sessions, ensure_ascii=False))
        try:
            _loaded_mtime = p.stat().st_mtime
        except OSError:
            _loaded_mtime = None
    except Exception:  # noqa: BLE001
        pass


def _prune_sessions() -> None:
    now = time.time()
    dead = [t for t, s in _sessions.items() if float(s.get("expires_at") or 0) <= now]
    for t in dead:
        _sessions.pop(t, None)


def create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        _load_sessions()
        _prune_sessions()
        _sessions[token] = {
            "username": user.get("username") or "",
            "user_id": int(user.get("id") or 0),
            "role": user.get("role") or "VIEWER",
            "company": user.get("company") or "",
            "expires_at": time.time() + _SESSION_MAX_AGE_S,
        }
        _save_sessions()
    return token


def drop_session(token: str) -> None:
    if not token:
        return
    with _SESSION_LOCK:
        _load_sessions()
        _sessions.pop(token, None)
        _save_sessions()


def session_user(token: str) -> dict | None:
    with _SESSION_LOCK:
        _load_sessions()
        s = _sessions.get(token)
        if not s:
            return None
        if float(s.get("expires_at") or 0) <= time.time():
            _sessions.pop(token, None)
            _save_sessions()
            return None
        company = s.get("company") or ""
        role = s.get("role") or "VIEWER"
        is_admin = role == "ADMIN"
        return {
            "username": s.get("username") or "",
            "user_id": int(s.get("user_id") or 0),
            "role": role,
            "company": company,
            # company_id har doim yangilanadi (kompaniya keyin qo'shilgan
            # bo'lsa ham sessiya ishlashda davom etadi).
            "company_id": 0 if is_admin else _company_id(company),
            "is_admin": is_admin,
            "routes": None if is_admin else _company_routes(company),
        }


def _company_id(company: str) -> int:
    try:
        from ..core import tenant
        return tenant.company_id_for_name(company or "")
    except Exception:  # noqa: BLE001
        return 0


def _company_routes(company: str) -> list[str]:
    """Kompaniyaning barcha yo'nalishlari (1 kompaniya = N yo'nalish).

    Manba: profiles.json (barcha shu nomdagi yozuvlar) + DB `profiles`
    fallback — batafsil: `core.tenant.company_routes`.
    """
    try:
        from ..core import tenant
        routes = tenant.company_routes(company or "")
        if routes:
            return routes
    except Exception:  # noqa: BLE001
        pass
    # Zaxira: eski xatti-harakat (bitta aktiv profil)
    from ..core.profiles import get_profile
    profiling = get_profile(company or "")
    rid = str((profiling or {}).get("routeVariantId") or "").strip()
    return [rid] if rid else []


# ---------------------------------------------------------------------------
# Parol
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str = "") -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"),
        salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, salt: str, expected: str) -> bool:
    if not expected or not salt:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"),
        salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), (expected or "").lower())


# ---------------------------------------------------------------------------
# Login brute-force himoyasi
# ---------------------------------------------------------------------------

_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW_S = 15 * 60
_LOGIN_BLOCK_S = 15 * 60
_LOGIN_MIN_INTERVAL_S = 1.0
_LOGIN_LOCK = threading.Lock()
_login_fails: dict[str, list[float]] = {}
_login_blocked_until: dict[str, float] = {}


def login_allowed(client_ip: str, username: str) -> bool:
    key = f"{username.strip().lower()}|{client_ip}"
    now = time.monotonic()
    with _LOGIN_LOCK:
        until = _login_blocked_until.get(key, 0.0)
        if now < until:
            return False
        fails = [t for t in _login_fails.get(key, []) if now - t < _LOGIN_WINDOW_S]
        if len(fails) >= _LOGIN_MAX_FAILS:
            _login_blocked_until[key] = now + _LOGIN_BLOCK_S
            _login_fails[key] = []
            return False
        last = fails[-1] if fails else 0.0
        if now - last < _LOGIN_MIN_INTERVAL_S:
            return False
        _login_fails[key] = fails
        return True


def login_fail(client_ip: str, username: str) -> None:
    key = f"{username.strip().lower()}|{client_ip}"
    now = time.monotonic()
    with _LOGIN_LOCK:
        fails = [t for t in _login_fails.get(key, []) if now - t < _LOGIN_WINDOW_S]
        fails.append(now)
        _login_fails[key] = fails


def login_success(client_ip: str, username: str) -> None:
    key = f"{username.strip().lower()}|{client_ip}"
    with _LOGIN_LOCK:
        _login_fails.pop(key, None)
        _login_blocked_until.pop(key, None)


# ---------------------------------------------------------------------------
# POST rate limiting
# ---------------------------------------------------------------------------

_POST_MIN_INTERVAL_S = 1.0
_POST_LOCK = threading.Lock()
_last_post_at: dict[str, float] = {}


def rate_limit_post(endpoint: str) -> bool:
    now = time.monotonic()
    with _POST_LOCK:
        last = _last_post_at.get(endpoint, 0.0)
        if now - last < _POST_MIN_INTERVAL_S:
            return False
        _last_post_at[endpoint] = now
        return True


# ---------------------------------------------------------------------------
# Super admin
# ---------------------------------------------------------------------------

def super_admin_names() -> set[str]:
    names = set()
    env_u = str(os.environ.get("DASHBOARD_ADMIN_USER") or "").strip().lower()
    if env_u:
        names.add(env_u)
    try:
        from ..db.storage import get_storage
        st = get_storage()
        for u in st.dashboard_users_list():
            try:
                data = json.loads(str(u.get("data") or "{}"))
            except (TypeError, ValueError):
                data = {}
            if data.get("super"):
                names.add(str(u.get("username") or "").lower())
    except Exception:  # noqa: BLE001
        pass
    return names


def is_super_username(username: str) -> bool:
    u = str(username or "").strip().lower()
    return bool(u) and u in super_admin_names()


# ---------------------------------------------------------------------------
# Foydalanuvchi bo'limlari (per-user ruxsatlar)
# ---------------------------------------------------------------------------

def page_for_path(path: str) -> str:
    """API yo'li qaysi sahifaga tegishli (topilmasa '')."""
    p = str(path or "")
    for prefix, page in PAGE_API_PREFIXES.items():
        if p.startswith(prefix):
            return page
    return ""


def user_pages(username: str) -> list | None:
    """Foydalanuvchi uchun aniq belgilangan bo'limlar (None = rol bo'yicha).

    `dashboard_users.data` JSON'idagi `pages` ro'yxati o'qiladi. Ro'yxat
    bo'lmasa yoki noto'g'ri bo'lsa — None (cheklov yo'q).
    """
    u = str(username or "").strip().lower()
    if not u or is_super_username(u):
        return None
    try:
        from ..db.storage import get_storage
        row = get_storage().dashboard_user_get(username=u)
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    try:
        data = json.loads(str(row.get("data") or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pages = data.get("pages")
    if not isinstance(pages, list):
        return None
    return [str(p) for p in pages if str(p) in PAGE_KEYS]


def user_tg(scope: dict | None) -> str:
    """Sessiya egasining bog'langan Telegram chat_id'si ('' = bog'lanmagan).

    Admin panelda foydalanuvchi akkauntiga Telegram ID biriktirilishi mumkin:
    o'sha Telegram akkaunt uchun bot ham shu foydalanuvchining bo'lim
    ruxsatlarini qo'llaydi (`roles.pages_for`).
    """
    u = str((scope or {}).get("username") or "").strip().lower()
    if not u:
        return ""
    try:
        from ..db.storage import get_storage
        row = get_storage().dashboard_user_get(username=u)
    except Exception:  # noqa: BLE001
        return ""
    if not row:
        return ""
    try:
        data = json.loads(str(row.get("data") or "{}"))
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("telegram_chat_id") or "")


def effective_pages(scope: dict | None) -> list | None:
    """Sessiya uchun amalda ishlaydigan bo'limlar (None = cheklovsiz)."""
    if not scope or scope.get("is_admin"):
        return None
    if str(scope.get("role") or "").upper() == "ADMIN":
        return None
    pages = scope.get("pages")
    if not isinstance(pages, list):
        return None
    return [str(p) for p in pages if str(p) in PAGE_KEYS]


def page_allowed(scope: dict | None, page: str) -> bool:
    """Sessiya shu bo'limga kira oladimi (rol + per-user ro'yxati)."""
    if not scope or scope.get("is_admin"):
        return True
    pages = effective_pages(scope)
    return pages is None or str(page) in pages


# ---------------------------------------------------------------------------
# Bootstrap admin
# ---------------------------------------------------------------------------

def ensure_bootstrap_admin() -> None:
    username = (os.environ.get("DASHBOARD_ADMIN_USER") or "").strip()
    password = (os.environ.get("DASHBOARD_ADMIN_PASS") or "").strip()
    if not username or len(password) < 4:
        return
    try:
        from ..db.storage import get_storage
        st = get_storage()
        if not st.enabled:
            return
        existing = st.dashboard_user_get(username=username.lower())
        digest, salt = hash_password(password)
        if not existing:
            st.dashboard_user_add(username=username.lower(), salt=salt,
                                  password_hash=digest, role="ADMIN",
                                  company="", active=1, data='{"super": true}')
            log.info("Bootstrap SUPER ADMIN akkaunt yaratildi: %s", username.lower())
        elif not verify_password(password, str(existing.get("salt") or ""), str(existing.get("password_hash") or "")):
            st.dashboard_user_update(int(existing["id"]), salt=salt, password_hash=digest, active=1)
            log.info("Bootstrap SUPER ADMIN paroli .env dan yangilandi: %s", username.lower())
    except Exception:  # noqa: BLE001
        log.warning("Bootstrap ADMIN yaratilmadi (DB mavjud emas?)")


# ---------------------------------------------------------------------------
# Scope dependency (FastAPI)
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def _dashboard_token() -> str:
    return (os.environ.get("DASHBOARD_TOKEN") or "").strip()


def _session_token_from_request(request: Request, creds: HTTPAuthorizationCredentials | None) -> str:
    if creds and creds.credentials and creds.credentials != _dashboard_token():
        return creds.credentials
    cookie = request.cookies.get("bm_session", "")
    return cookie.strip()


def _is_loopback(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    host = (client.host or "").lower().rstrip(".")
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.") or "::ffff:127." in host


def request_is_https(request: Request) -> bool:
    """So'rov HTTPS orqali kelganmi (reverse-proxy `X-Forwarded-Proto`ni ham hisobga oladi).

    Cookie'ga `Secure` bayrog'ini faqat TLS bo'lganda qo'yish uchun ishlatiladi
    (plain HTTP'da qo'yilsa login buziladi).
    """
    try:
        if request.url.scheme == "https":
            return True
    except Exception:  # noqa: BLE001
        pass
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


async def resolve_scope(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any] | None:
    """So'rov egasini aniqlaydi: sessiya birinchi, DASHBOARD_TOKEN ikkinchi.

    BM_TEST_MODE=1 faqat testlar uchun. Avtomatik bypass bo'lishi uchun
    so'rov loopback (127.0.0.1/::1) bo'lishi kerak — tashqi tarmoqdan
    test rejimida ham kirish qilmaslik uchun.
    """
    if os.environ.get("BM_TEST_MODE") == "1":
        # TestClient so'rovlari 'testclient' client host bilan keladi — bu
        # FastAPI test rejimiga xos, xavfsiz holda ruxsat etiladi.
        if not (_is_loopback(request) or client_ip(request) == "testclient"):
            log.warning("BM_TEST_MODE bypass tashqi so'rovdan bloklandi: %s",
                        client_ip(request))
            return None
        return {"username": "test", "role": "ADMIN", "company": "", "is_admin": True, "routes": None}

    token = _session_token_from_request(request, creds)
    session = session_user(token) if token else None
    if session:
        # Per-user bo'limlar ro'yxati har so'rovda o'qiladi: admin sozlamani
        # o'zgartirsa darhol kuchga kiradi (sessiya qayta kirishni talab
        # qilmaydi). ADMIN / super uchun cheklov yo'q.
        session = dict(session)
        session["pages"] = user_pages(session.get("username"))
        return session

    dt = _dashboard_token()
    if dt:
        auth = (creds.credentials if creds else "") or ""
        cookie_token = request.cookies.get("bm_token", "")
        if auth == dt or cookie_token == dt:
            return {"username": "ADMIN", "role": "ADMIN", "company": "", "is_admin": True, "routes": None}
    return None


class Scope:
    """Autentifikatsiya + rol tekshiruvi dependency'si."""

    def __init__(self, min_role: int | None = None):
        self._min_role = min_role

    async def __call__(
        self,
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> dict[str, Any]:
        scope = await resolve_scope(request, creds)
        if not scope:
            raise HTTPException(401, "Kirish talab qilinadi")
        path = request.url.path
        min_needed = self._min_role
        if min_needed is None:
            table = _GET_ROLE_MIN if request.method == "GET" else _POST_ROLE_MIN
            min_needed = 1
            for prefix, mn in table.items():
                if path.startswith(prefix):
                    min_needed = mn
                    break
        if not scope.get("is_admin"):
            rank = _ROLE_RANK.get(scope.get("role") or "VIEWER", 0)
            if rank < min_needed:
                raise HTTPException(403, "Sizda bu bo'limga ruxsat yo'q")
            # Per-user bo'limlar: foydalanuvchiga aniq ro'yxat berilgan bo'lsa,
            # sahifaga xos API'lar ham yopiladi (faqat menyuni yashirish
            # yetarli emas — to'g'ridan-to'g'ri so'rov ham bloklanadi).
            page = page_for_path(path)
            if page and not page_allowed(scope, page):
                raise HTTPException(
                    403, f"«{page}» bo'limiga ruxsat berilmagan")
        request.state.scope = scope
        return scope


require_auth = Scope()


def get_scope(request: Request) -> dict[str, Any]:
    return getattr(request.state, "scope", None) or {}


def scope_routes(scope: dict) -> set[str] | None:
    if not scope or scope.get("is_admin"):
        return None
    return set(str(x) for x in (scope.get("routes") or []))


def force_route(scope: dict, path: str, params: dict) -> dict:
    """Non-admin uchun route paramni o'z korxona yo'nalishlariga kesadi.

    Kompaniyada bir nechta yo'nalish bo'lsa — vergul bilan birlashtiriladi
    (metrics/export vergul ajratuvchini qo'llab-quvvatlaydi).
    """
    if not scope or scope.get("is_admin"):
        return params
    if not any(path.startswith(p) for p in _ROUTE_SCOPED_PREFIXES):
        return params
    routes = [str(r).strip() for r in (scope.get("routes") or []) if str(r).strip()]
    rid = ",".join(routes) if routes else "\x00_qamrovsiz\x00"
    params = dict(params)
    params["route"] = rid
    return params


def tenant_guard(request: Request):
    """Bloklangan/muddati o'tgan kompaniya sessiyasini to'xtatadi (403).

    Qoidalar:
    - faqat /api/ yo'llar (login/logout mustasno);
    - faqat `bm_session` cookie bor so'rovlar; ADMIN (super) tegilmaydi;
    - noma'lum kompaniya (jadvalda yo'q) — fail-open (o'tkaziladi);
    - har qanday xato — o'tkaziladi (dashboard to'xtab qolmasligi uchun).

    Ruxsat etilgan so'rovlar uchun `request.state.tenant` to'ldiriladi.
    Qaytaradi: 403 JSONResponse yoki None.
    """
    if os.environ.get("BM_TEST_MODE") == "1":
        return None
    path = request.url.path or ""
    if not path.startswith("/api/"):
        return None
    if path.startswith("/api/login") or path.startswith("/api/logout"):
        return None
    cookie = request.cookies.get("bm_session", "")
    if not cookie:
        return None
    try:
        session = session_user(cookie)
        if not session:
            return None
        company = str(session.get("company") or "")
        from ..core import tenant
        row = tenant.company_state(company) if company else None
        request.state.tenant = {
            "company": company,
            "company_id": int(session.get("company_id") or 0),
            "state": row,
        }
        if session.get("is_admin"):
            return None
        if company and not tenant.company_active(row):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "detail": "Kompaniya faol emas (bloklangan yoki obuna "
                              "muddati tugagan). Administratorga murojaat qiling.",
                },
            )
    except Exception:  # noqa: BLE001
        return None
    return None


def client_ip(request: Request) -> str:
    if request.client:
        return (request.client.host or "").lower().rstrip(".") or "?"
    return "?"
