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
# Sessiya muddati SILANADI: 10+ daqiqadan keyin kelgan har bir faol so'rov
# muddatni to'ldiradi — panel ochiq turgan foydalanuvchi 12 soatdan keyin
# chetlab yuborilmaydi. Mutlaq chek esa sessiyani 7 kundan oshmay qayta
# tugaydi (eski parollar uchun ham xavfsiz chegara).
_SESSION_ABS_MAX_S = 7 * 24 * 3600
_SESSION_TOUCH_S = 600
_MAX_SESSIONS_PER_USER = 8
# OWASP tavsiyasi (2023+): PBKDF2-SHA256 kamida 600 000 iteratsiya. Eski
# 120 000 lik hash'lar qabul qilinadi va muvaffaqiyatli login'da qayta
# hisoblanadi (`needs_rehash`), shuning uchun parollarni almashtirish shart emas.
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_LEGACY_ITERATIONS = 120_000
_PBKDF2_PREFIX = "pbkdf2_sha256"
PASSWORD_MIN_LEN = 8
_COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1",
    "qwerty123", "parol123", "admin123", "bmbrutto", "dtransport",
}


def password_error(password: str, username: str = "") -> str:
    """Yangi parolni tekshiradi; xato matni yoki bo'sh satr qaytaradi."""
    pw = str(password or "")
    if len(pw) < PASSWORD_MIN_LEN:
        return f"Parol kamida {PASSWORD_MIN_LEN} belgi bo'lishi kerak"
    if len(pw) > 128:
        return "Parol 128 belgidan oshmasligi kerak"
    low = pw.lower()
    if low in _COMMON_PASSWORDS:
        return "Bu parol juda oddiy — boshqa parol tanlang"
    uname = str(username or "").strip().lower()
    if uname and low == uname:
        return "Parol loginga o'xshamasligi kerak"
    return ""

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
    "/api/knowledge": "knowledge",
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
    "/api/knowledge": 4,
    "/api/sync/status": 2, "/api/sync/monthly/status": 2,
    "/api/video": 1, "/api/video/cameras": 1, "/api/video/relay": 1,
    "/api/video/archive": 2, "/api/video/export": 4,
}

# Eng uzun prefiks ustun: `/api/video/archive` `/api/video` dan oldin
# tekshirilishi kerak (aks holda umumiy qoida aniqroq qoidani bosib ketadi).
_GET_ROLE_ORDER = tuple(sorted(_GET_ROLE_MIN.items(), key=lambda kv: -len(kv[0])))

_POST_ROLE_MIN: dict[str, int] = {
    "/api/settings": 4, "/api/settings/env": 4, "/api/settings/companies": 4,
    "/api/backup": 4,
    "/api/dashboard-users": 2, "/api/admin": 4, "/api/myai": 4,
    "/api/sms": 3, "/api/documents": 3, "/api/rejects/tariff": 2,
    "/api/kmrate": 2, "/api/route-skm": 2, "/api/sync": 2,
    "/api/electricity/price": 4, "/api/users": 4, "/api/dispatchers": 4,
    "/api/video/cameras": 4, "/api/video/relay": 4, "/api/video/archive": 4,
    "/api/video/export": 4,
    "/api/knowledge": 4,
}

_POST_ROLE_ORDER = tuple(sorted(_POST_ROLE_MIN.items(), key=lambda kv: -len(kv[0])))

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
        # Sessiya tokenlari ochiq matnda — fayl faqat egasiga o'qiladi.
        try:
            if os.name != "nt":
                os.chmod(p, 0o600)
        except OSError:
            pass
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
    name = str(user.get("username") or "").strip().lower()
    with _SESSION_LOCK:
        _load_sessions()
        _prune_sessions()
        now = time.time()
        # Bitta akkaunt uchun sessiyalar soni cheklangan: eng eski
        # sessiyalar yopiladi (cheksiz JSON o'sishi va token ko'payishi).
        same = sorted(
            ((float(s.get("last_seen") or 0), t)
             for t, s in _sessions.items()
             if str(s.get("username") or "").strip().lower() == name),
            key=lambda x: x[0])
        for _, old in same[:max(0, len(same) - _MAX_SESSIONS_PER_USER + 1)]:
            _sessions.pop(old, None)
        _sessions[token] = {
            "username": user.get("username") or "",
            "user_id": int(user.get("id") or 0),
            "role": user.get("role") or "VIEWER",
            "company": user.get("company") or "",
            "expires_at": now + _SESSION_MAX_AGE_S,
            # Silash muddati uchun qo'shimcha qo'yilmalar (eski yozuvlarda
            # yo'q — birinchi tekshiruvda `setdefault` bilan to'ldiriladi).
            "last_seen": now,
            "created_at": now,
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


def drop_user_sessions(username: str, keep_token: str = "") -> int:
    """Bir foydalanuvchining BARCHA sessiyalarini yopadi (joriy saqlanadi).

    Parol o'zgarganda eski parol bilan kirish imkoniyatini darhol
    yo'qotish uchun ishlatiladi: `keep_token` (joriy qurilma) bundan
    mustasno. Yopilgan sessiyalar soni qaytariladi.
    """
    name = str(username or "").strip().lower()
    if not name:
        return 0
    closed = 0
    with _SESSION_LOCK:
        _load_sessions()
        for token, s in list(_sessions.items()):
            if token == keep_token:
                continue
            if str(s.get("username") or "").strip().lower() != name:
                continue
            _sessions.pop(token, None)
            closed += 1
        if closed:
            _save_sessions()
    return closed


def refresh_session_cookie(request: Request, response: Response) -> bool:
    """Faol `bm_session` cookie'sini qayta yangilaydi (muddat silanadi).

    Brauzer cookie'si ham 12 soatlik bo'ladi: panel ochiq turganda SPA
    har ~30 soniyada `/api/me` so'radi — shu so'rovda kuki ham yangilanadi,
    shuning uchun "kuki yo'qolgan" holat faqat haqiqiy tanaffusdan keyin
    ro'y beradi. Sessiya o'zi yopilgan bo'lsa (parol o'zgargan) — kuki
    qayta yozilmaydi.
    """
    token = (request.cookies.get("bm_session") or "").strip()
    if not token or not session_user(token):
        return False
    response.set_cookie(
        "bm_session", token, path="/", httponly=True, samesite="lax",
        secure=request_is_https(request), max_age=_SESSION_MAX_AGE_S)
    return True


def session_user(token: str) -> dict | None:
    with _SESSION_LOCK:
        _load_sessions()
        s = _sessions.get(token)
        if not s:
            return None
        now = time.time()
        created = float(s.get("created_at") or 0)
        if float(s.get("expires_at") or 0) <= now or (
                created and now - created > _SESSION_ABS_MAX_S):
            _sessions.pop(token, None)
            _save_sessions()
            return None
        # Silash: faol foydalanuvchi muddatini to'ldiradi (yozuv har 10
        # daqiqada bir marta saqlanadi — so'rov boshiga diskka yozilmaydi).
        if now - float(s.get("last_seen") or 0) > _SESSION_TOUCH_S:
            s["last_seen"] = now
            s.setdefault("created_at", now)
            s["expires_at"] = now + _SESSION_MAX_AGE_S
            _save_sessions()
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
            # Super (egasi) — faqat `DASHBOARD_ADMIN_USER`/`super` bayrog'i
            # bo'lgan akkaunt. Ba'zi bo'limlar (masalan "Bilim bazasi")
            # faqat shu akkauntga ko'rinadi.
            "is_super": bool(is_admin and is_super_username(
                s.get("username") or "")),
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

def _pbkdf2_hex(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"),
        salt.encode("utf-8"), iterations).hex()


def hash_password(password: str, salt: str = "") -> tuple[str, str]:
    """Yangi hash: `pbkdf2_sha256$<iteratsiya>$<hex>` + salt."""
    salt = salt or secrets.token_hex(16)
    digest = _pbkdf2_hex(password, salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}${digest}", salt


def _split_hash(expected: str) -> tuple[int, str]:
    """Hash yozuvini (iteratsiya, hex) juftligiga ajratadi.

    Format qo'yilmagan eski yozuvlar (sof hex) 120 000 iteratsiya bilan
    hisoblangan deb qabul qilinadi — parol almashtirmasdan ko'chish uchun.
    """
    raw = str(expected or "").strip().lower()
    parts = raw.split("$")
    if len(parts) == 3 and parts[0] == _PBKDF2_PREFIX:
        try:
            return int(parts[1]), parts[2]
        except ValueError:
            return 0, ""
    return _PBKDF2_LEGACY_ITERATIONS, raw


def verify_password(password: str, salt: str, expected: str) -> bool:
    if not expected or not salt:
        return False
    iterations, digest_hex = _split_hash(expected)
    if iterations <= 0 or not digest_hex:
        return False
    return hmac.compare_digest(_pbkdf2_hex(password, salt, iterations), digest_hex)


def needs_rehash(expected: str) -> bool:
    """Hash eskirgan (kam iteratsiya yoki formatsiz) bo'lsa True."""
    iterations, digest_hex = _split_hash(expected)
    return bool(digest_hex) and iterations < _PBKDF2_ITERATIONS


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

_SUPER_LOCK = threading.Lock()
_SUPER_TTL_S = 30.0
_super_cache: tuple[float, set[str]] | None = None


def super_admin_names() -> set[str]:
    global _super_cache
    now = time.monotonic()
    with _SUPER_LOCK:
        if _super_cache and now - _super_cache[0] < _SUPER_TTL_S:
            return set(_super_cache[1])
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
    with _SUPER_LOCK:
        _super_cache = (time.monotonic(), set(names))
    return names


def invalidate_super_cache() -> None:
    global _super_cache
    with _SUPER_LOCK:
        _super_cache = None


def is_super_username(username: str) -> bool:
    u = str(username or "").strip().lower()
    return bool(u) and u in super_admin_names()


# ---------------------------------------------------------------------------
# Foydalanuvchi bo'limlari (per-user ruxsatlar)
# ---------------------------------------------------------------------------

_ACCESS_LOCK = threading.Lock()
_ACCESS_TTL_S = 20.0
# username -> (storage nusxasi, vaqt, pages, telegram_chat_id)
_access_cache: dict[str, tuple[object, float, list | None, str]] = {}


def invalidate_user_access(username: str = "") -> None:
    """Bo'lim/Telegram keshi yozuvini tozalaydi ('' = butun kesh)."""
    with _ACCESS_LOCK:
        if username:
            _access_cache.pop(str(username).strip().lower(), None)
        else:
            _access_cache.clear()


def page_for_path(path: str) -> str:
    """API yo'li qaysi sahifaga tegishli (topilmasa '')."""
    p = str(path or "")
    best = ""
    page = ""
    for prefix, name in PAGE_API_PREFIXES.items():
        if len(prefix) > len(best) and p.startswith(prefix):
            best = prefix
            page = name
    return page


def _user_access(username: str) -> tuple[list | None, str]:
    """Akkauntning (pages, telegram_chat_id) juftligi — bitta so'rovda.

    Keshlanadi: `Scope` har so'rovda chaqiradi, admin panel o'zgartirganda
    `invalidate_user_access` darhol yangilaydi. DB xatosida cheklov
    SAQLANADI (fail-closed) — vaqtinchalik uzilish ruxsatni kengaytirmaydi.
    """
    u = str(username or "").strip().lower()
    if not u:
        return None, ""
    try:
        from ..db.storage import get_storage
        st = get_storage()
    except Exception:  # noqa: BLE001
        return [], ""
    now = time.monotonic()
    with _ACCESS_LOCK:
        cached = _access_cache.get(u)
        if cached and cached[0] is st and now - cached[1] < _ACCESS_TTL_S:
            return cached[2], cached[3]
    try:
        row = st.dashboard_user_get(username=u)
    except Exception:  # noqa: BLE001
        return [], ""
    pages: list | None = None
    tg = ""
    if row:
        is_admin_row = str(row.get("role") or "").upper() == "ADMIN"
        try:
            data = json.loads(str(row.get("data") or "{}"))
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            tg = str(data.get("telegram_chat_id") or "")
            raw = data.get("pages")
            if not is_admin_row and isinstance(raw, list):
                pages = [str(p) for p in raw if str(p) in PAGE_KEYS]
    with _ACCESS_LOCK:
        if len(_access_cache) > 500:
            _access_cache.clear()
        _access_cache[u] = (st, now, pages, tg)
    return pages, tg


def user_pages(username: str) -> list | None:
    """Foydalanuvchi uchun aniq belgilangan bo'limlar (None = rol bo'yicha).

    `dashboard_users.data` JSON'idagi `pages` ro'yxati o'qiladi. Ro'yxat
    bo'lmasa (yoki ADMIN) — None (rol bo'yicha cheklov).
    """
    return _user_access(username)[0]


def user_tg(scope: dict | None) -> str:
    """Sessiya egasining bog'langan Telegram chat_id'si ('' = bog'lanmagan).

    Admin panelda foydalanuvchi akkauntiga Telegram ID biriktirilishi mumkin:
    o'sha Telegram akkaunt uchun bot ham shu foydalanuvchining bo'lim
    ruxsatlarini qo'llaydi (`roles.pages_for`).
    """
    return _user_access((scope or {}).get("username") or "")[1]


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

def _env_file() -> Path:
    """`.env` manzili (testlarda `monkeypatch` bilan almashtiriladi)."""
    return Path(".env")


def sync_env_admin_password(username: str, password: str) -> bool:
    """Bootstrap ADMIN parolini `.env`ga sinxronlaydi (yagona manba — 1 yo'l).

    `ensure_bootstrap_admin()` har restartda `DASHBOARD_ADMIN_PASS`ni
    haqiqiy parol deb oladi: panel orqali o'zgarGAN parol `.env`ga
    yozilmasa, keyingi restart uni eski qiymatga qaytarib yuboradi
    (foydalanuvchi "parolim ishlamayapti" deb qoladi). Shu sababli
    bootstrap akkaunt paroli o'zgarganda `.env` ham yangilanadi —
    qayta yozish uchun hech narsa qolmaydi.
    """
    want_user = (os.environ.get("DASHBOARD_ADMIN_USER") or "").strip().lower()
    if not want_user or not password:
        return False
    if str(username or "").strip().lower() != want_user:
        return False
    path = _env_file()
    try:
        if not path.exists():
            return False
        from dotenv import set_key
        set_key(str(path), "DASHBOARD_ADMIN_PASS", password)
        os.environ["DASHBOARD_ADMIN_PASS"] = password
        log.info("Bootstrap admin parolini .env ga sinxronlandi")
        return True
    except Exception:  # noqa: BLE001
        log.warning("DASHBOARD_ADMIN_PASS .env ga yozilmadi", exc_info=True)
        return False


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
            return
        # Mavjud akkaunt: parol sinxronlanadi VA `super` bayrog'i KAFOLATLANADI.
        # Ilgari (bayroq joriy etilishidan oldin) yaratilgan ADMIN qatorida
        # `data` bo'sh bo'lardi — natijada hech kim super bo'lmay, "Bilim
        # bazasi" bo'limi va `/api/knowledge` hammaga yopiq qolardi.
        kwargs: dict[str, Any] = {"active": 1}
        if not verify_password(password, str(existing.get("salt") or ""),
                               str(existing.get("password_hash") or "")):
            kwargs.update(salt=salt, password_hash=digest)
            log.info("Bootstrap SUPER ADMIN paroli .env dan yangilandi: %s", username.lower())
        try:
            data = json.loads(str(existing.get("data") or "{}"))
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["super"] = True
        if str(existing.get("role") or "").strip().upper() != "ADMIN":
            # Super huquqi faqat ADMIN rolida ishlaydi — rol ham tiklanadi
            # (bo'limlar ro'yxati ADMIN uchun ma'nosiz, olib tashlanadi).
            data.pop("pages", None)
            kwargs.update(role="ADMIN", company="")
            log.info("Bootstrap akkaunt ADMIN rolga o'tkazildi: %s", username.lower())
        st.dashboard_user_update(
            int(existing["id"]), data=json.dumps(data, ensure_ascii=False),
            **kwargs)
        invalidate_super_cache()
    except Exception:  # noqa: BLE001
        log.warning("Bootstrap ADMIN yaratilmadi (DB mavjud emas?)")


# ---------------------------------------------------------------------------
# Scope dependency (FastAPI)
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def _dashboard_token() -> str:
    return (os.environ.get("DASHBOARD_TOKEN") or "").strip()


def _same_token(given: str, expected: str) -> bool:
    """Tokenlarni vaqt bo'yicha doimiy solishtirish (timing leak yo'q)."""
    if not given or not expected:
        return False
    return hmac.compare_digest(str(given), str(expected))


def _session_token_from_request(request: Request, creds: HTTPAuthorizationCredentials | None) -> str:
    if creds and creds.credentials and not _same_token(creds.credentials, _dashboard_token()):
        return creds.credentials
    cookie = request.cookies.get("bm_session", "")
    return cookie.strip()


def _session_token_from_headers(request: Request) -> str:
    """Middleware uchun sessiya tokeni (FastAPI creds hali yo'q).

    `resolve_scope` bilan bir xil ustuvorlik: Bearer sessiya tokeni (lekin
    `DASHBOARD_TOKEN` emas) cookie'dan oldin tekshiriladi. Aks holda
    admin cookie'si bilan birga yuborilgan kompaniya Bearer tokeni tenant
    nazoratidan chetlab o'tardi.
    """
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        if tok and not _same_token(tok, _dashboard_token()):
            return tok
    return (request.cookies.get("bm_session") or "").strip()


def _is_loopback(request: Request) -> bool:
    client = request.client
    if not client:
        return False
    host = (client.host or "").lower().rstrip(".")
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.") or "::ffff:127." in host


_HOST_ALLOW_DEFAULT = {
    "localhost", "127.0.0.1", "::1", "testserver",
    "bmbrutto.uz", "www.bmbrutto.uz",
}


def host_allowed(host_header: str) -> bool:
    """Host header ruxsat etilganmi (DNS rebinding / virtual-host himoyasi).

    `DASHBOARD_ALLOWED_HOSTS=*` — nazorat o'chiriladi. Aks holda: env
    ro'yxati + standart nomlar + `*.bmbrutto.uz` + IP-literal + server
    hostname'i. Bo'sh Host ham rad etiladi (HTTP/1.1 da majburiy).
    """
    raw = (os.environ.get("DASHBOARD_ALLOWED_HOSTS") or "").strip()
    extra = {h.strip().lower() for h in raw.split(",") if h.strip()}
    if "*" in extra or raw == "*":
        return True
    host = (host_header or "").strip()
    if not host:
        return False
    try:
        from urllib.parse import urlsplit
        name = (urlsplit("//" + host).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not name:
        return False
    if name in extra or name in _HOST_ALLOW_DEFAULT:
        return True
    if name.endswith(".bmbrutto.uz"):
        return True
    try:
        import ipaddress
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        pass
    try:
        import socket
        return name == socket.gethostname().lower()
    except OSError:
        return False


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
        return {"username": "test", "role": "ADMIN", "company": "",
                "is_admin": True, "is_super": True, "routes": None}

    token = _session_token_from_request(request, creds)
    session = session_user(token) if token else None
    if session:
        # Per-user bo'limlar ro'yxati keshlangan holda o'qiladi: admin
        # sozlamani o'zgartirsa `invalidate_user_access` keshni darhol
        # tozalaydi. ADMIN uchun cheklov yo'q — DB so'rovi qilinmaydi.
        session = dict(session)
        session["pages"] = (None if session.get("is_admin")
                            else user_pages(session.get("username")))
        return session

    dt = _dashboard_token()
    if dt:
        auth = (creds.credentials if creds else "") or ""
        cookie_token = request.cookies.get("bm_token", "")
        if _same_token(auth, dt) or _same_token(cookie_token, dt):
            # Loopback/token egasi — server operatori, shuning uchun super.
            return {"username": "ADMIN", "role": "ADMIN", "company": "",
                    "is_admin": True, "is_super": True, "routes": None}
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
            order = _GET_ROLE_ORDER if request.method == "GET" else _POST_ROLE_ORDER
            min_needed = 1
            for prefix, mn in order:
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


async def optional_scope(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any] | None:
    """Auth ixtiyoriy endpointlar uchun scope (login bo'lmasa `None`)."""
    scope = await resolve_scope(request, creds)
    if scope:
        request.state.scope = scope
    return scope


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


def _audit_value(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def audit_event(action: str, request: Request | None = None, *,
                user: str = "", target: str = "", ok: bool = True,
                detail: str = "") -> None:
    """Xavfsizlik/mutatsiya hodisasini logga yozadi (parol/token yozilmaydi)."""
    ip = client_ip(request) if request is not None else "?"
    log.info("AUDIT action=%s ok=%s user=%s target=%s ip=%s detail=%s",
             _audit_value(action, 80), "1" if ok else "0",
             _audit_value(user), _audit_value(target), ip,
             _audit_value(detail))
    if action in ("login.fail", "login.blocked"):
        try:
            from .alerts import notify_login_failure
            notify_login_failure(_audit_value(user), ip, _audit_value(detail))
        except Exception:  # noqa: BLE001 - ogohlantirish audit yozuvini buzmidi
            log.warning("login ogohlantirishi yuborilmadi")


def _tenant_denied(reason: str, request: Request, scope: dict,
                   company: str) -> Any:
    audit_event("tenant.denied", request,
                user=str((scope or {}).get("username") or ""),
                target=company, ok=False, detail=reason)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=403,
        content={
            "ok": False,
            "detail": "Kompaniya faol emas yoki topilmadi. "
                      "Administratorga murojaat qiling.",
        },
    )


def tenant_guard(request: Request):
    """Kompaniya tenant holatini fail-closed tekshiradi (403).

    Qoidalar:
    - faqat /api/ yo'llar (login/logout mustasno);
    - sessiya tokeni cookie yoki Bearer header'da bo'lishi mumkin; ADMIN
      (super) tegilmaydi;
    - non-admin uchun kompaniya majburiy va `companies` jadvalida mavjud
      bo'lishi kerak; noma'lum/xato holat ham 403 (fail-closed).

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
    token = _session_token_from_headers(request)
    if not token:
        return None
    try:
        session = session_user(token)
    except Exception as exc:  # noqa: BLE001
        log.warning("Tenant sessiyasini o'qib bo'lmadi: %s", exc)
        return None
    if not session:
        return None
    company = str(session.get("company") or "").strip()
    if session.get("is_admin"):
        request.state.tenant = {
            "company": company,
            "company_id": int(session.get("company_id") or 0),
            "state": None,
        }
        return None
    if not company:
        return _tenant_denied("missing-company", request, session, company)
    try:
        from ..core import tenant
        row = tenant.company_state(company)
        active = tenant.company_active(row)
    except Exception as exc:  # noqa: BLE001
        log.warning("Tenant holatini tekshirib bo'lmadi (%s): %s", company, exc)
        return _tenant_denied("lookup-error", request, session, company)
    request.state.tenant = {
        "company": company,
        "company_id": int(session.get("company_id") or 0),
        "state": row,
    }
    if row is None:
        return _tenant_denied("unknown-company", request, session, company)
    if not active:
        return _tenant_denied("inactive-company", request, session, company)
    return None


def client_ip(request: Request) -> str:
    if request.client:
        return (request.client.host or "").lower().rstrip(".") or "?"
    return "?"
