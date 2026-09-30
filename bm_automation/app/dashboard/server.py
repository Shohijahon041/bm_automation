"""Web dashboard — stdlib HTTP server (qo'shimcha bog'liqliksiz).

Ishga tushirish:
    python -m bm_automation dashboard --port 8080

Yo'nalishlar (JSON API):
    GET  /                 -> SPA (web/index.html)
    GET  /api/summary      -> to'liq dashboard xulosasi (filterlar bilan)
    GET  /api/routes       -> filter dropdown uchun yo'nalishlar
    GET  /api/month        -> oy tarixi (kunlik statistika, ?month=YYYY-MM)
    GET  /api/health       -> tizim holati (BM API, DB, Telegram, Scheduler)
    POST /api/sync         -> BM API'dan ma'lumotni DB'ga sync (best-effort)
    GET  /api/export       -> CSV/XLS/XLSX/PDF eksport
    GET  /api/check        -> sayt ma'lumotlari ↔ DB solishtirish

Auto-refresh brauzer tomonida (JS setInterval) amalga oshiriladi.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard")

WEB_DIR = Path(__file__).parent / "web"
INDEX_FILE = WEB_DIR / "index.html"
DOCUMENT_DIR = Path("data") / "driver_documents"
PHOTO_DIR = Path("data") / "driver_photos"
DOCUMENT_FIELDS = {
    "passport_front": "passport_front_path",
    "passport_back": "passport_back_path",
    "license_front": "license_front_path",
    "license_back": "license_back_path",
}
PHOTO_FIELD = "photo_path"
DOCUMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_STARTED = time.time()

# /api/sync chaqiruvlarini cheklash (abuse'dan himoya).
_SYNC_MIN_INTERVAL_S = 20.0
_SYNC_LOCK = threading.Lock()
_last_sync_at = 0.0

# POST endpointlar uchun global rate limiting.
_POST_MIN_INTERVAL_S = 1.0
_POST_LOCK = threading.Lock()
_last_post_at: dict[str, float] = {}

# Dashboard autentifikatsiya tokeni (DASHBOARD_TOKEN env var).
_DASHBOARD_TOKEN = ""

# Sync natijalarini tushunarli tilda ko'rsatish uchun entity -> o'zbekcha izoh.
_SYNC_ENTITY_LABELS = {
    "routes": "Yo'nalishlar",
    "vehicles": "Avtobuslar",
    "drivers": "Haydovchilar",
    "driver_profiles": "Haydovchi profillari",
    "driver_work_logs": "Haydovchi ish jurnali",
    "duties": "Navbatchiliklar",
    "trips": "Reyslar",
    "waybills": "Yo'l varaqalari",
    "route_daily": "Kunlik masofa hisoboti",
}


def _sync_entity_label(entity: str) -> str:
    """SyncResult.entity nomini o'zbekcha izohga aylantiradi."""
    if entity.startswith("profiles/"):
        return f"{entity.split('/', 1)[1]} profili"
    return _SYNC_ENTITY_LABELS.get(entity, entity or "?")


def _sync_lines(results: list[dict]) -> list[str]:
    """Sync natijalarini oddiy tilda qatorlar qilib beradi."""
    lines: list[str] = []
    for r in results or []:
        label = _sync_entity_label(str(r.get("entity") or ""))
        if r.get("error"):
            lines.append(f"✗ {label}: {r['error']}")
            continue
        parts = []
        if r.get("inserted"):
            parts.append(f"qo'shildi {r['inserted']}")
        if r.get("updated"):
            parts.append(f"yangilandi {r['updated']}")
        if r.get("deleted"):
            parts.append(f"o'chirildi {r['deleted']}")
        if not parts:
            parts.append("o'zgarish yo'q")
        lines.append(f"• {label}: {', '.join(parts)}")
    return lines


def _fmt_date(ds: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (progress/izoh uchun)."""
    try:
        return date.fromisoformat(ds).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ds


def _check_dashboard_token(handler: BaseHTTPRequestHandler) -> bool:
    """DASHBOARD_TOKEN sozlangan bo'lsa token tekshiradi. True = ruxsat."""
    if not _DASHBOARD_TOKEN:
        return True
    auth = handler.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip() == _DASHBOARD_TOKEN
    cookie = ""
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_token":
            cookie = kv[1].strip()
    return cookie == _DASHBOARD_TOKEN


# ------------------------------------------------------------- login/sessiya
# Ko'p korxonali tizim: dashboard akkauntlari `dashboard_users` jadvalida,
# sessiyalar `state/dashboard_sessions.json` faylida (12 soat). DASHBOARD_TOKEN
# ADMIN master-bypass bo'lib qoladi (localhost avto-auth uchun).

_SESSION_MAX_AGE_S = 12 * 3600
_PBKDF2_ITERATIONS = 120_000
_SESSION_LOCK = threading.Lock()
_sessions: dict[str, dict] = {}

# Rol -> daraja (chipga ko'tarilish tartibida). VIEWER faqat kirish uchun.
_ROLE_RANK = {"VIEWER": 0, "DISPATCHER": 1, "MANAGER": 2, "DIRECTOR": 3,
              "ADMIN": 4}

# GET endpoint -> talab qilinadigan rol darajasi (kiritilmagani = DISPATCHER).
_GET_ROLE_MIN: dict[str, int] = {
    "/api/users": 4,
    "/api/users/": 4,
    "/api/group-stats": 4,
    "/api/dispatchers": 4,
    "/api/disk": 4,
    "/api/backup": 4,
    "/api/logs": 4,
    "/api/settings/env": 4,
    "/api/settings/companies": 4,
    "/api/settings": 4,
    "/api/dashboard-users": 2,
    "/api/myai": 4,
    "/api/insights": 4,
    "/api/admin": 4,
    "/api/brutto": 2,
    "/api/rejects/tariff": 2,
    "/api/rejects": 2,
    "/api/kmrate": 2,
    "/api/route-skm": 2,
    "/api/electricity/price": 2,
    "/api/electricity": 2,
    "/api/drivers": 2,
    "/api/salary": 2,
    "/api/check": 3,
    "/api/sms": 3,
    "/api/documents": 3,
    "/api/me": 0,
}

# POST endpoint -> talab qilinadigan rol darajasi.
_POST_ROLE_MIN: dict[str, int] = {
    "/api/settings": 4,
    "/api/settings/env": 4,
    "/api/settings/companies": 4,
    "/api/dashboard-users": 2,
    "/api/admin": 4,
    "/api/myai": 4,
    "/api/sms": 3,
    "/api/documents": 3,
    "/api/rejects/tariff": 2,
    "/api/kmrate": 2,
    "/api/route-skm": 2,
    "/api/sync": 2,
    "/api/electricity/price": 4,
    "/api/users": 4,
    "/api/dispatchers": 4,
}

# Route-ga bog'liq endpointlar: non-admin foydalanuvchi uchun `route`
# parametri doim o'z korxonasiga kesiladi (boshqa korxona ma'lumotini
# ko'rishning oldini olish uchun).
_ROUTE_SCOPED_PREFIXES = (
    "/api/summary", "/api/rejects", "/api/brutto", "/api/routes",
    "/api/route-skm", "/api/month", "/api/attendance", "/api/drivers",
    "/api/electricity", "/api/salary", "/api/export", "/api/check",
    "/api/appeals", "/api/kmrate", "/api/rejects/tariff",
    "/api/sms", "/api/documents", "/api/vehicles",
)


def _sessions_file() -> Path:
    return Path("state") / "dashboard_sessions.json"


def _load_sessions() -> None:
    global _sessions
    p = _sessions_file()
    if not p.exists():
        _sessions = {}
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        _sessions = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        _sessions = {}


def _save_sessions() -> None:
    try:
        from ..utils.io import atomic_write
        p = _sessions_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(_sessions, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - sessiya yozilmasa ham server davom etadi
        pass


def _session_token(handler: BaseHTTPRequestHandler) -> str:
    """So'rovdan sessiya tokenini oladi (Bearer yoki bm_session cookie)."""
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth.startswith("Bearer "):
        tok = auth[7:].strip()
        if tok and tok != _DASHBOARD_TOKEN:
            return tok
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_session":
            return kv[1].strip()
    return ""


def _prune_sessions() -> None:
    now = time.time()
    dead = [t for t, s in _sessions.items()
            if float(s.get("expires_at") or 0) <= now]
    for t in dead:
        _sessions.pop(t, None)


def _create_session(user: dict) -> str:
    token = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        _load_sessions()
        _prune_sessions()
        _sessions[token] = {
            "username": (user.get("username") or ""),
            "role": (user.get("role") or "VIEWER"),
            "company": (user.get("company") or ""),
            "expires_at": time.time() + _SESSION_MAX_AGE_S,
        }
        _save_sessions()
    return token


def _company_routes(company: str) -> list[str]:
    """Kompaniya (profiles.json `name`) yo'nalish ID'lari ro'yxati."""
    from ..core.profiles import get_profile
    profiling = get_profile(company or "")
    rid = str((profiling or {}).get("routeVariantId") or "").strip()
    return [rid] if rid else []


def _super_admin_names() -> set[str]:
    """Super admin foydalanuvchi nomlari (kichik harfda).

    Super admin = `.env` dagi ``DASHBOARD_ADMIN_USER`` (bootstrap) yoki
    ``data.super`` ugli akkaunt. Uni hech kim (o'zi ham) o'chira olmaydi;
    o'chirish huquqi faqat super adminda.
    """
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
    except Exception:
        pass
    return names


def _is_super_username(username: str) -> bool:
    u = str(username or "").strip().lower()
    return bool(u) and u in _super_admin_names()


def _session_user(token: str) -> dict | None:
    """Sessiya tokeni bo'yicha foydalanuvchi qamrovini qaytaradi."""
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
        return {
            "username": s.get("username") or "",
            "role": role,
            "company": company,
            "is_admin": role == "ADMIN",
            "routes": None if role == "ADMIN" else _company_routes(company),
        }


def _drop_session(handler: BaseHTTPRequestHandler) -> None:
    token = _session_token(handler)
    if not token:
        return
    with _SESSION_LOCK:
        _load_sessions()
        _sessions.pop(token, None)
        _save_sessions()


def _resolve_scope(handler: BaseHTTPRequestHandler) -> dict | None:
    """So'rov egasini aniqlaydi: sessiya birinchi, DASHBOARD_TOKEN ikkinchi.

    Loopback'da sahifaga in'ektsiya qilingan master token har bir API
    chaqiruvida Authorization header orqali keladi; sessiya bilan kirilgan
    foydalanuvchi (masalan DISPATCHER) token tufayli ADMIN bo'lib qolmasligi
    uchun sessiya ustun turadi. Token faqat sessiya bo'lmaganda ADMIN beradi.
    """
    if os.environ.get("BM_TEST_MODE") == "1":
        # Test rejimida to'liq ADV-ni beramiz — eski testlar to'g'ridan-to'g'ri
        # handler'ga murojaat qiladi va autentifikatsiyasiz ishlaydi. Haqiqiy
        # kirish testlari BM_TEST_MODE'ni o'chirib oladi.
        return {
            "username": "test",
            "role": "ADMIN",
            "company": "",
            "is_admin": True,
            "routes": None,
        }
    token = _session_token(handler)
    session = _session_user(token) if token else None
    if session:
        return session
    if _DASHBOARD_TOKEN and _check_dashboard_token(handler):
        return {
            "username": "ADMIN",
            "role": "ADMIN",
            "company": "",
            "is_admin": True,
            "routes": None,
        }
    return None


def _hash_password(password: str, salt: str = "") -> tuple[str, str]:
    """Parolni salt bilan xeshlaydi -> (hash, salt)."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"),
        salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return digest.hex(), salt


def _verify_password(password: str, salt: str, expected: str) -> bool:
    if not expected or not salt:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"),
        salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), (expected or "").lower())


def _ensure_bootstrap_admin() -> None:
    """Birinchi ishga tushirishda Super ADMIN akkauntini yaratadi.

    Agar `dashboard_users` jadvalida akkauntlar bo'lmasa va env'da
    DASHBOARD_ADMIN_USER/PASS berilgan bo'lsa — ADMIN yaratiladi
    (idempotent; faqat bitta) va `data.super` da belgilanadi: hech kim
    (o'zi ham) uni o'chira olmaydi, o'chirish huquqi faqat unda.
    Keyingi adminlar Dashboard foydalanuvchilari sahifasi orqali qo'shiladi.
    """
    username = (os.environ.get("DASHBOARD_ADMIN_USER") or "").strip()
    password = (os.environ.get("DASHBOARD_ADMIN_PASS") or "").strip()
    if not username or len(password) < 4:
        return
    try:
        from ..db.storage import get_storage
        st = get_storage()
        if not st.enabled:
            return
        if st.dashboard_users_count() > 0:
            return
        digest, salt = _hash_password(password)
        st.dashboard_user_add(username=username.lower(), salt=salt,
                              password_hash=digest, role="ADMIN",
                              company="", active=1, data='{"super": true}')
        log.info("Bootstrap SUPER ADMIN akkaunt yaratildi: %s",
                 username.lower())
    except Exception:  # noqa: BLE001 - DB bo'lmasa server davom etadi
        log.warning("Bootstrap ADMIN yaratilmadi (DB mavjud emas?)")


def _rate_limit_post(endpoint: str) -> bool:
    """POST endpoint uchun rate limiting. True = ruxsat, False = bloklangan."""
    now = time.monotonic()
    with _POST_LOCK:
        last = _last_post_at.get(endpoint, 0.0)
        if now - last < _POST_MIN_INTERVAL_S:
            return False
        _last_post_at[endpoint] = now
        return True


def _image_ext(data: bytes) -> str | None:
    """Rasm fayl sehri (magic bytes) bo'yicha haqiqiy kengaytmani qaytaradi."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _parse(qs: dict) -> dict:
    return {k: v[0] for k, v in qs.items()}


def _report_month(qs: dict) -> date | None:
    """Brutto-master hisobot oyi (sana lug'atdan yoki yo'q)."""
    from datetime import datetime
    if not qs:
        return None
    month = (qs.get("month") or [""])
    month = month[0] if isinstance(month, list) else month
    month = month or ""
    if month:
        try:
            return datetime.strptime(month[:7], "%Y-%m").date()
        except ValueError:
            return None
    day = ""
    for key in ("from", "to"):
        val = qs.get(key) or [""]
        val = val[0] if isinstance(val, list) else val
        if val.strip():
            day = val.strip()
            break
    if day:
        try:
            return date.fromisoformat(day[:10])
        except ValueError:
            return None
    return None


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BM-Dashboard/1.0"

    # ---------------------------------------------------------------- helpers

    def _enforce_role(self, path: str) -> bool:
        """So'rov yo'li uchun rol chegarasini tekshiradi (403 uchun)."""
        scope = getattr(self, "_scope", None)
        if not scope or scope.get("is_admin"):
            return True
        rank = _ROLE_RANK.get(scope.get("role") or "VIEWER", 0)
        table = _GET_ROLE_MIN if self.command == "GET" else _POST_ROLE_MIN
        needed = 1
        for prefix, mn in table.items():
            if path.startswith(prefix):
                needed = mn
                break
        return rank >= needed

    def _force_route(self, path: str, qs: dict, payload: dict | None = None) -> None:
        """Non-admin foydalanuvchi uchun `route` paramni o'z korxonasiga kesadi.

        Kompaniya qamrovi bo'sh bo'lsa hech narsaga mos kelmaydigan qiymat
        o'rnatiladi (fail-closed) — aks holda route bo'sh qolib, butun baza
        ochilib qolardi.
        """
        scope = getattr(self, "_scope", None)
        if not scope or scope.get("is_admin"):
            return
        if not any(path.startswith(p) for p in _ROUTE_SCOPED_PREFIXES):
            return
        routes = list(scope.get("routes") or [])
        rid = routes[0] if routes else "\x00_qamrovsiz\x00"
        if qs is not None:
            qs["route"] = [rid]
        if payload is not None:
            payload["route"] = rid

    _FIN_FIELDS = ("skm", "km_rate", "tariff_no_vat", "tariff_vat")

    def _strip_finance(self, items: list | None) -> list | None:
        """Moliyaviy maydonlarni DISPATCHER/VIEWER uchun o'chirib tashlaydi.

        Nusxa (copy) qaytaradi — kesh so'ralgan butun ob'ektni buzmaydi
        (summary `_summary_cache` da saqlanadi).
        """
        if not items:
            return items
        scope = getattr(self, "_scope", None)
        if not scope or scope.get("is_admin"):
            return items
        rank = _ROLE_RANK.get(scope.get("role") or "VIEWER", 0)
        if rank >= 2:
            return items
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append({k: v for k, v in item.items()
                            if k not in self._FIN_FIELDS})
            else:
                out.append(item)
        return out

    def _route_allowed(self, route_id: str) -> bool:
        """route_id qamrovda bo'lsa True (admin doim True)."""
        scopes = self._scope_routes()
        if scopes is None:
            return True
        return str(route_id or "") in scopes

    def _driver_write_allowed(self, payload: dict, driver_id: str = "") -> bool:
        """Haydovchiga yozish uchun route qamrovini tekshiradi.

        Yangi haydovchi uchun payload.driver_id/route_id, mavjud uchun
        joriy route ham hisobga olinadi (boshqa korxona route'iga ko'chirish
        yoki u yerda yangi qayd ochishning oldini olish).
        """
        if self._scope_routes() is None:
            return True
        from ..db.storage import get_storage
        did = str(payload.get("driver_id") or driver_id or "").strip()
        existing = {}
        if did:
            existing = get_storage().find("drivers", external_id=did) or {}
        route = str(payload.get("route_id")
                    or existing.get("route_id") or "").strip()
        return self._route_allowed(route)

    def _scope_routes(self) -> set | None:
        """Non-admin uchun qamrovdagi route'lar; admin/yo'q bo'lsa None."""
        scope = getattr(self, "_scope", None)
        if not scope or scope.get("is_admin"):
            return None
        return set(str(x) for x in (scope.get("routes") or []))

    @staticmethod
    def _doc_in_scope(storage, driver_id: str, scopes: set) -> bool:
        """Xujjat tegishli haydovchi qamrov route'ida bo'lsa True."""
        if not driver_id:
            return False
        row = storage.find("drivers", external_id=driver_id)
        return bool(row and str(row.get("route_id") or "") in scopes)

    def _driver_in_scope(self, storage, driver_id: str, scopes: set) -> bool:
        """Haydovchi qamrov route'ida bo'lsa True (shaxsiy ma'lumot himoyasi)."""
        if not driver_id:
            return False
        row = storage.find("drivers", external_id=driver_id)
        return bool(row and str(row.get("route_id") or "") in scopes)

    @staticmethod
    def _appeal_driver(storage, aid: int) -> str:
        """Murojaat egasining haydovchi ID'sini qaytaradi (topilmasa '')."""
        try:
            rows = storage.query(
                "SELECT driver_id FROM driver_appeals WHERE id = "
                + storage.db.ph, (str(aid),), limit=1)
            if rows:
                return str(rows[0].get("driver_id") or "")
        except Exception:  # noqa: BLE001 - jadval yo'q bo'lsa ham xavfsiz
            pass
        return ""

    def _me(self) -> None:
        scope = getattr(self, "_scope", None)
        if not scope:
            self._json({"ok": False, "error": "Kirish talab qilinadi"}, 401)
            return
        from ..core.profiles import get_profile, all_profiles
        company_name = scope.get("company") or ""
        profile = get_profile(company_name) if company_name else None
        self._json({
            "ok": True,
            "username": scope.get("username") or "",
            "role": scope.get("role") or "VIEWER",
            "is_admin": bool(scope.get("is_admin")),
            "super": _is_super_username(scope.get("username") or ""),
            "login_type": "session",
            "company": {
                "name": company_name,
                "routeName": str((profile or {}).get("routeName") or ""),
                "routeVariantId": str((profile or {}).get("routeVariantId") or ""),
            },
            "companies": [str(p.get("name") or "") for p in all_profiles()],
        })

    def _login(self) -> None:
        """POST /api/login — username+parolni tekshirib sessiya ochadi."""
        try:
            payload = self._payload()
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        from ..db.storage import get_storage
        username = str(payload.get("username") or "").strip().lower()
        password = str(payload.get("password") or "")
        if not username or not password:
            self._json({"ok": False, "error": "Login va parol kiriting"}, 400)
            return
        user = get_storage().dashboard_user_get(username=username)
        if not user or int(user.get("active") or 0) == 0:
            self._json({"ok": False, "error": "Login yoki parol noto'g'ri"}, 401)
            return
        if not _verify_password(password, str(user.get("salt") or ""),
                                str(user.get("password_hash") or "")):
            self._json({"ok": False, "error": "Login yoki parol noto'g'ri"}, 401)
            return
        role = str(user.get("role") or "VIEWER").strip().upper()
        company = str(user.get("company") or "").strip()
        token = _create_session({"username": username, "role": role,
                                 "company": company})
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Set-Cookie",
            f"bm_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_MAX_AGE_S}",
        )
        body = json.dumps({
            "ok": True, "username": username, "role": role,
            "company": company,
        }, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _logout(self) -> None:
        """POST /api/logout — sessiyani o'chiradi, cookie'ni tozalaydi."""
        _drop_session(self)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
                         "bm_session=; Path=/; HttpOnly; Max-Age=0")
        body = b'{"ok": true}'
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _dashboard_users_api(self, payload: dict) -> dict:
        """Dashboard foydalanuvchilarini boshqarish (MANAGER+).

        Qoidalar:
          - O'chirish faqat SUPER ADMIN'da; super admin'ni hech kim
            (o'zi ham) o'chira olmaydi.
          - ADMIN foydalanuvchi yaratish / rolga o'tkazish faqat super
            admin'da.
          - Non-admin (MANAGER/DIRECTOR) faqat o'z korxonasi qatorlarini
            ko'radi va boshqaradi (o'chirishdan tashqari); belgilagan roli
            o'z darajasidan past bo'lishi shart.
        """
        from ..db.storage import get_storage
        from ..core.profiles import all_profiles
        st = get_storage()
        action = str(payload.get("action") or "").strip()
        profiles = all_profiles()
        by_name = {str(p.get("name") or "").lower(): p for p in profiles}
        by_rvid = {
            str(p.get("routeVariantId") or ""): p
            for p in profiles if (p.get("routeVariantId") or "")}

        def _resolve_company(val: str) -> str:
            """Profil nomi yoki routeVariantId -> saglanadigan profil nomi."""
            val = (val or "").strip()
            if not val:
                return ""
            p = by_name.get(val.lower())
            if not p:
                p = by_rvid.get(val)
            return str((p or {}).get("name") or "")

        scope = getattr(self, "_scope", {}) or {}
        caller = str(scope.get("username") or "").strip().lower()
        caller_is_admin = bool(scope.get("is_admin"))
        caller_company = str(scope.get("company") or "")
        caller_rank = _ROLE_RANK.get(str(scope.get("role") or "VIEWER"), 0)
        super_names = _super_admin_names()
        caller_is_super = caller in super_names

        if action == "list":
            rows = []
            for u in st.dashboard_users_list():
                c = str(u.get("company") or "")
                if not caller_is_admin and c != caller_company:
                    continue
                profile = by_name.get(c.lower()) if c else None
                uname = str(u.get("username") or "").lower()
                rows.append({
                    "id": u.get("id"),
                    "username": u.get("username"),
                    "role": u.get("role"),
                    "company": c,
                    "company_route": str((profile or {}).get("routeName") or ""),
                    "active": int(u.get("active") or 0) == 1,
                    "super": uname in super_names,
                })
            if caller_is_admin:
                comps = [
                    {"name": str(p.get("name") or ""),
                     "routeVariantId": str(p.get("routeVariantId") or ""),
                     "routeName": str(p.get("routeName") or "")}
                    for p in profiles]
            else:
                p = by_name.get(caller_company.lower()) or {}
                comps = ([{"name": caller_company,
                           "routeVariantId": str(p.get("routeVariantId") or ""),
                           "routeName": str(p.get("routeName") or "")}]
                         if caller_company else [])
            return {"ok": True, "users": rows, "companies": comps,
                    "is_super": caller_is_super}
        if action in ("create", "update"):
            username = str(payload.get("username") or "").strip().lower()
            password = str(payload.get("password") or "")
            row_id = payload.get("id")
            try:
                row_id = int(row_id) if row_id not in (None, "", "0") else 0
            except (TypeError, ValueError):
                row_id = 0
            if action == "create":
                role = str(payload.get("role") or "VIEWER").strip().upper()
                if role not in _ROLE_RANK:
                    role = "VIEWER"
                company = _resolve_company(str(payload.get("company") or ""))
                if role == "ADMIN" and not caller_is_super:
                    return {"ok": False, "error":
                            "ADMIN foydalanuvchi yaratish huquqi faqat "
                            "Super Adminda"}
                if not caller_is_admin:
                    company = caller_company
                    if _ROLE_RANK.get(role, 0) >= caller_rank:
                        return {"ok": False, "error":
                                "O'z darajangizdan past rol yarata olasiz"}
                if role != "ADMIN" and not company:
                    return {"ok": False, "error": "Noto'g'ri kompaniya tanlandi"}
                if role == "ADMIN":
                    company = ""
                if not username:
                    return {"ok": False, "error": "Login kiritish shart"}
                if st.dashboard_user_get(username=username):
                    return {"ok": False, "error": "Bu login allaqachon mavjud"}
                if len(password) < 4:
                    return {"ok": False, "error": "Parol kamida 4 belgi"}
                digest, salt = _hash_password(password)
                uid = st.dashboard_user_add(
                    username=username, salt=salt, password_hash=digest,
                    role=role, company=company,
                    active=1 if payload.get("active") else 1)
                return {"ok": bool(uid), "id": uid}
            cur = st.dashboard_user_get(row_id=row_id) if row_id else None
            if not cur and username:
                cur = st.dashboard_user_get(username=username)
            if not cur:
                return {"ok": False, "error": "Foydalanuvchi topilmadi"}
            row_id = int(cur.get("id") or row_id or 0)
            cur_name = str(cur.get("username") or "").lower()
            cur_role = str(cur.get("role") or "VIEWER").strip().upper()
            cur_company = str(cur.get("company") or "")
            target_is_super = cur_name in super_names
            if target_is_super and not caller_is_super:
                return {"ok": False, "error":
                        "Super adminni boshqa foydalanuvchi "
                        "tahrirlay olmaydi"}
            if username and cur_name != username:
                existing = st.dashboard_user_get(username=username)
                if existing and int(existing.get("id")) != row_id:
                    return {"ok": False, "error": "Bu login allaqachon mavjud"}
            # Super admin qatori: rol/kompaniya/faollik daxlsiz (parol mumkin).
            if target_is_super:
                if str(payload.get("role") or "").strip().upper() \
                        not in ("", "ADMIN"):
                    return {"ok": False, "error": "Super admin roli daxlsiz"}
                if payload.get("active") not in (None, ""):
                    return {"ok": False, "error":
                            "Super admin faolligini o'chirib bo'lmaydi"}
                if payload.get("company") not in (None, ""):
                    return {"ok": False, "error":
                            "Super admin kompaniyasi daxlsiz"}
                if password:
                    digest, salt = _hash_password(password)
                    ok = st.dashboard_user_update(
                        row_id=row_id, salt=salt, password_hash=digest)
                else:
                    ok = True
                return {"ok": ok}
            role = str(payload.get("role") or cur_role).strip().upper()
            if role not in _ROLE_RANK:
                role = "VIEWER"
            if role == "ADMIN" and not caller_is_super:
                return {"ok": False, "error":
                        "ADMIN rolga o'tkazish huquqi faqat Super Adminda"}
            if not caller_is_admin:
                if cur_company != caller_company:
                    return {"ok": False, "error":
                            "Boshqa korxona foydalanuvchisini "
                            "tahrirlay olmaysiz"}
                if role != cur_role and _ROLE_RANK.get(role, 0) >= caller_rank:
                    return {"ok": False, "error":
                            "O'z darajangizdan past rol belgilang"}
                target_company = caller_company
            else:
                if payload.get("company") not in (None, ""):
                    target_company = _resolve_company(
                        str(payload.get("company") or ""))
                else:
                    target_company = cur_company
            if role != "ADMIN" and not target_company:
                return {"ok": False, "error": "Noto'g'ri kompaniya tanlandi"}
            if role == "ADMIN":
                target_company = ""
            if payload.get("active") not in (None, ""):
                active = int(payload.get("active") or 0)
            else:
                active = int(cur.get("active") or 0)
            ok = st.dashboard_user_update(
                row_id=row_id, username=username or None, role=role,
                company=target_company, active=active)
            if password:
                digest, salt = _hash_password(password)
                st.dashboard_user_update(row_id=row_id, salt=salt,
                                         password_hash=digest)
            return {"ok": ok}
        if action == "delete":
            if not caller_is_super:
                return {"ok": False, "error":
                        "O'chirish huquqi faqat Super Adminda"}
            try:
                row_id = int(payload.get("id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            if not row_id:
                uname = str(payload.get("username") or "").strip().lower()
                if not uname:
                    return {"ok": False, "error": "id yoki username kerak"}
                cur = st.dashboard_user_get(username=uname)
                if not cur:
                    return {"ok": True}  # allaqachon o'chirilgan
                row_id = int(cur.get("id") or 0)
            row = st.dashboard_user_get(row_id=row_id)
            if not row:
                return {"ok": True}
            uname = str(row.get("username") or "").lower()
            if uname in super_names:
                return {"ok": False, "error": "Super adminni o'chirib bo'lmaydi"}
            if uname == caller:
                return {"ok": False, "error": "O'zingizni o'chira olmaysiz"}
            return {"ok": st.delete_dashboard_user(row_id)}
        return {"ok": False, "error": "Noma'lum action"}

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Mijoz (brauzer) javob paytida ulanishni uzdi — server xatosi emas.
            # Xavotirni ko'tarmasdan sekin qaytamiz.
            log.info("Mijoz javobni o'qiimasdan ulanishni yopdi (%s)", self.path)

    def _file(self, path: Path, content_type: str, code: int = 200) -> None:
        if not path.exists():
            self._json({"error": "fayl topilmadi"}, 404)
            return
        stat = path.stat()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    log.info("Mijoz faylni o'qiimasdan ulanishni yopdi (%s)", self.path)
                    return

    def _is_loopback(self) -> bool:
        """Mijoz shu kompyuterdan (localhost) kiryaptimi?"""
        hint = (self.client_address[0] if self.client_address else "") or ""
        hint = hint.lower().rstrip(".")
        return (
            hint in ("127.0.0.1", "::1", "localhost")
            or hint.startswith("127.")
            or "::ffff:127." in hint
        )

    def _serve_index(self) -> None:
        """SPA sahifasini beradi.

        Token(lar) o'rnatilgan bo'lsa va mijoz malakali (loopback) bo'lsa —
        token sahifaga qo'shiladi, brauzer uni localStorage/cookie'ga o'zi
        yozadi va foydalanuvchi tokenni qo'lda kiritmaydi. Tashqaridan kirgan
        mijozlarga token in'ektsiya qilinmaydi (himoya saqlanadi).
        """
        if INDEX_FILE.exists():
            body = INDEX_FILE.read_bytes()
        else:
            self._json({"error": "fayl topilmadi"}, 404)
            return
        auto_auth = bool(_DASHBOARD_TOKEN and self._is_loopback())
        if auto_auth:
            token_lit = json.dumps(_DASHBOARD_TOKEN)
            script = (
                "<script>(function(){try{var t=" + token_lit + ";"
                'if(t){localStorage.setItem("bm-token",t);'
                'document.cookie="bm_token="+encodeURIComponent(t)'
                '+"; path=/; SameSite=Lax";}}catch(e){}})();'
                "</script>"
            ).encode("utf-8")
            head_idx = body.lower().find(b"<head")
            if head_idx >= 0:
                tag_end = body.find(b">", head_idx)
                if tag_end >= 0:
                    body = body[:tag_end + 1] + script + body[tag_end + 1:]
                else:
                    body = script + body
            else:
                body = script + body
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if auto_auth:
            # Server tomonidan cookie — JS'ga bog'liq emas. API chaqiruvlari
            # Authorization header'siz ham cookie orqali ishlaydi.
            self.send_header(
                "Set-Cookie",
                f"bm_token={_DASHBOARD_TOKEN}; Path=/; SameSite=Lax",
            )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            log.info("Mijoz sahifani o'qiimasdan ulanishni yopdi (%s)", self.path)

    def _send_bytes(self, data: bytes, content_type: str, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            log.info("Mijoz faylni o'qiimasdan ulanishni yopdi (%s)", self.path)

    def _send_xlsx(self, filename: str, data: bytes) -> None:
        self._send_bytes(data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         filename)

    def _send_zip(self, files: list, zipname: str) -> None:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.writestr(f["filename"], f["data"])
        self._send_bytes(buf.getvalue(), "application/zip", zipname)

    def _admin_ym(self, qs: dict) -> tuple[int, int]:
        """Dashboard admin bo'limi uchun (year, month) qiymatini qaytaradi."""
        def _v(key, default):
            raw = (qs.get(key) or [""])[0].strip()
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        year = _v("year", date.today().year)
        month = _v("month", date.today().month)
        if not 1 <= month <= 12:
            month = 1
        return year, month

    def _sse_events(self) -> None:
        """Server-Sent Events stream for MyAI agent events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            from ..myai.events import get_event_bus
            bus = get_event_bus()
            for line in bus.events_sse(timeout=30):
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("SSE xatosi")

    def _payload(self) -> dict:
        """JSON request body'ni kichik, aniq xatoliklar bilan o'qiydi."""
        raw_len = (self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(raw_len)
        except (TypeError, ValueError):
            raise ValueError("Content-Length noto'g'ri")
        if length < 0 or length > 7 * 1024 * 1024:
            raise ValueError("So'rov hajmi noto'g'ri")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except (TypeError, ValueError) as exc:
            raise ValueError("JSON formati noto'g'ri") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON obyekt bo'lishi kerak")
        scope = getattr(self, "_scope", None)
        if scope and not scope.get("is_admin"):
            self._force_route(self.path, None, payload)
        return payload

    @staticmethod
    def _driver_id(value: str) -> str:
        driver_id = str(value or "").strip()
        if not driver_id or len(driver_id) > 128:
            raise ValueError("Haydovchi ID kiritilishi kerak")
        return driver_id

    @staticmethod
    def _text(value, limit: int = 500) -> str:
        return str(value or "").strip()[:limit]

    def _driver_filters(self, qs: dict) -> dict:
        return _parse(qs)

    def _driver_detail(self, driver_id: str, qs: dict) -> dict:
        from . import metrics as m

        detail = m.Metrics().driver_detail(self._driver_id(driver_id), self._driver_filters(qs))
        if detail is None:
            return {"ok": False, "error": "Haydovchi topilmadi"}
        return {"ok": True, **detail}

    def _update_km_rate(self, payload: dict) -> dict:
        """Yo'nalish uchun 1 km narxini yangilaydi (route darajasida saqlanadi).

        Haydovchilar bo'lmagan yo'nalishlar uchun ham ishlaydi. Haydovchi
        profilidagi shaxsiy km_rate bo'lsa u route darajasidagi qiymatdan
        ustun turadi.
        """
        from ..core.bot_settings import set_route_km
        route_id = str(payload.get("route_id") or "").strip()
        km_rate = payload.get("km_rate")
        if not route_id:
            return {"ok": False, "error": "route_id kerak"}
        try:
            km_rate = float(km_rate)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Noto'g'ri km narxi"}
        if not math.isfinite(km_rate) or km_rate < 0:
            return {"ok": False, "error": "km narxi manfiy yoki yaroqsiz"}
        km_rate = min(km_rate, 10_000_000_000.0)
        set_route_km(route_id, km_rate)
        return {"ok": True, "updated": 0, "route_id": route_id,
                "km_rate": km_rate}

    def _update_elec_price(self, payload: dict) -> dict:
        """1 kVt/soat elektr narxini o'rnatadi (so'm)."""
        from ..core.bot_settings import set_elec_price, ELEC_KWH_PER_KM
        raw = payload.get("rate")
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Narx son bo'lishi kerak"}
        if not math.isfinite(rate) or rate < 0:
            return {"ok": False, "error": "Narx manfiy yoki yaroqsiz"}
        rate = min(rate, 100_000_000_000.0)
        set_elec_price(rate)
        return {"ok": True, "rate": rate, "kwh_per_km": ELEC_KWH_PER_KM}

    def _update_rejects_tariff(self, payload: dict) -> dict:
        """Yo'nalish uchun 1 km boshang'ich narxini o'rnatadi (so'm/km)."""
        from ..core.bot_settings import set_route_tariff
        rid = str(payload.get("route_id") or "").strip()
        if not rid:
            return {"ok": False, "error": "route_id kerak"}
        out = {}
        for key in ("no_vat", "vat"):
            raw = payload.get(key)
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": "Narx son bo'lishi kerak"}
            if not math.isfinite(val) or val < 0:
                return {"ok": False, "error": "Narx manfiy yoki yaroqsiz"}
            out[key] = min(val, 100_000_000_000_000.0)
        saved = set_route_tariff(rid, out["no_vat"], out["vat"])
        return {"ok": True, "route_id": rid, **saved}

    def _update_route_skm(self, payload: dict) -> dict:
        """Yo'nalish (firma) uchun 1 mashina-km SKM narxini o'rnatadi."""
        from ..core.bot_settings import set_route_skm
        rid = str(payload.get("route_id") or "").strip()
        if not rid:
            return {"ok": False, "error": "route_id kerak"}
        raw = payload.get("skm")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "SKM son bo'lishi kerak"}
        if not math.isfinite(val) or val < 0:
            return {"ok": False, "error": "SKM manfiy yoki yaroqsiz"}
        val = min(val, 100_000_000_000_000.0)
        saved = set_route_skm(rid, val)
        return {"ok": True, "route_id": rid, "skm": saved}

    def _save_driver(self, payload: dict, driver_id: str = "") -> dict:
        """Haydovchi va uning ichki profil sozlamalarini saqlaydi."""
        from ..db.models import json_loads
        from ..db.storage import get_storage

        did = self._driver_id(driver_id or payload.get("driver_id"))
        storage = get_storage()
        if not storage.enabled:
            raise ValueError("DB rejimi o'chirilgan")

        existing = storage.find("drivers", external_id=did) or {}
        name = self._text(payload.get("full_name"), 160) or existing.get("full_name") or did
        storage.save_driver(
            external_id=did, full_name=name,
            tin=self._text(payload.get("tin"), 32) or existing.get("tin", ""),
            route_id=self._text(payload.get("route_id"), 128) or existing.get("route_id", ""),
            data=json_loads(existing.get("data")),
        )
        fields = {
            key: self._text(payload[key], 500) for key in (
                "phone", "passport_number", "passport_issued_by", "passport_expiry",
                "license_number", "license_category", "license_expiry",
                "blacklist_reason", "notification_target", "notes",
            ) if key in payload
        }
        for key in ("rating", "km_rate"):
            if key in payload:
                raw = payload[key]
                if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                    fields[key] = 0.0 if key == "km_rate" else 5.0
                    continue
                try:
                    val = float(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"{key} son bo'lishi kerak")
                if not math.isfinite(val):
                    raise ValueError(f"{key} qiymati yaroqsiz")
                if key == "rating":
                    fields[key] = min(max(val, 0.0), 5.0)
                else:
                    fields[key] = min(max(val, 0.0), 10_000_000_000.0)
        for key in ("blacklisted", "notification_enabled"):
            if key in payload:
                fields[key] = bool(payload[key])
        storage.save_driver_profile(did, **fields)
        return self._driver_detail(did, {})

    def _save_document(self, driver_id: str, payload: dict) -> dict:
        """Rasmni lokal, gitignore qilingan katalogka saqlaydi.

        Hujjatlar URL emas, dashboard'ning o'z API manzili orqali qaytariladi.
        Bu passport/litsenziya rasmlarini boshqa joyga yubormasdan saqlaydi.
        """
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        kind = str(payload.get("kind") or "").strip()
        if kind not in DOCUMENT_FIELDS:
            raise ValueError("Hujjat turi noto'g'ri")
        data_url = str(payload.get("data_url") or "")
        match = re.fullmatch(r"data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)", data_url)
        if not match or match.group(1).lower() not in DOCUMENT_TYPES:
            raise ValueError("Faqat JPG, PNG yoki WEBP rasm yuklang")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Rasm ma'lumoti buzilgan") from exc
        if not raw or len(raw) > 5 * 1024 * 1024:
            raise ValueError("Rasm 5 MB dan kichik bo'lishi kerak")
        ext = _image_ext(raw)
        if not ext:
            raise ValueError("Fayl rasm emas (JPG/PNG/WEBP bo'lishi kerak)")
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", did)[:80] or "driver"
        DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
        target = DOCUMENT_DIR / f"{safe_id}_{kind}{ext}"
        target.write_bytes(raw)
        storage = get_storage()
        if not storage.enabled:
            raise ValueError("DB rejimi o'chirilgan")
        storage.save_driver_profile(did, **{DOCUMENT_FIELDS[kind]: target.as_posix()})
        return {"ok": True, "document": kind,
                "url": f"/api/drivers/{did}/document/{kind}"}

    def _document(self, driver_id: str, kind: str) -> None:
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        if kind not in DOCUMENT_FIELDS:
            self._json({"error": "Hujjat turi topilmadi"}, 404)
            return
        profile = get_storage().find("driver_profiles", driver_id=did) or {}
        raw_path = str(profile.get(DOCUMENT_FIELDS[kind]) or "")
        if not raw_path:
            self._json({"error": "Rasm yuklanmagan"}, 404)
            return
        path = Path(raw_path)
        try:
            path.resolve().relative_to(DOCUMENT_DIR.resolve())
        except ValueError:
            self._json({"error": "Rasm manzili xavfsiz emas"}, 403)
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._file(path, mime)

    def _save_photo(self, driver_id: str, payload: dict) -> dict:
        """Haydovchi rasmini lokal katalogka saqlaydi.

        Rasm URL emas, dashboard'ning o'z API manzili orqali qaytariladi —
        rasm boshqa joyga yuborilmaydi (BM API'da haydovchi fotosi yo'q).
        """
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        data_url = str(payload.get("data_url") or "")
        match = re.fullmatch(r"data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)", data_url)
        if not match or match.group(1).lower() not in DOCUMENT_TYPES:
            raise ValueError("Faqat JPG, PNG yoki WEBP rasm yuklang")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Rasm ma'lumoti buzilgan") from exc
        if not raw or len(raw) > 5 * 1024 * 1024:
            raise ValueError("Rasm 5 MB dan kichik bo'lishi kerak")
        ext = _image_ext(raw)
        if not ext:
            raise ValueError("Fayl rasm emas (JPG/PNG/WEBP bo'lishi kerak)")
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", did)[:80] or "driver"
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        target = PHOTO_DIR / f"{safe_id}{ext}"
        target.write_bytes(raw)
        storage = get_storage()
        if not storage.enabled:
            raise ValueError("DB rejimi o'chirilgan")
        storage.save_driver_profile(did, **{PHOTO_FIELD: target.as_posix()})
        return {"ok": True, "url": f"/api/drivers/{did}/photo"}

    def _photo(self, driver_id: str) -> None:
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        profile = get_storage().find("driver_profiles", driver_id=did) or {}
        raw_path = str(profile.get(PHOTO_FIELD) or "")
        if not raw_path:
            self._json({"error": "Rasm yuklanmagan"}, 404)
            return
        path = Path(raw_path)
        try:
            path.resolve().relative_to(PHOTO_DIR.resolve())
        except ValueError:
            self._json({"error": "Rasm manzili xavfsiz emas"}, 403)
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._file(path, mime)

    def _save_work_log(self, driver_id: str, payload: dict) -> dict:
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        log_date = self._text(payload.get("date"), 10) or date.today().isoformat()
        try:
            date.fromisoformat(log_date)
        except ValueError as exc:
            raise ValueError("Sana YYYY-MM-DD formatida bo'lishi kerak") from exc
        storage = get_storage()
        try:
            distance = float(payload.get("distance_km") or 0)
            trips = int(payload.get("trip_count") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Km yoki reys soni noto'g'ri") from exc
        if not math.isfinite(distance) or distance < 0 or distance > 1_000_000:
            raise ValueError("Km qiymati yaroqsiz")
        if trips < 0 or trips > 100_000:
            raise ValueError("Reys soni yaroqsiz")
        storage.save_driver_work_log(
            log_date, did, self._text(payload.get("vehicle_id"), 128),
            distance, trips, self._text(payload.get("note"), 500),
        )
        return self._driver_detail(did, {"month": log_date[:7]})

    def _delete_work_log(self, driver_id: str, payload: dict) -> dict:
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        log_date = self._text(payload.get("date"), 10)
        if not log_date:
            raise ValueError("Sana kiritilishi kerak")
        try:
            date.fromisoformat(log_date)
        except ValueError as exc:
            raise ValueError("Sana YYYY-MM-DD formatida bo'lishi kerak") from exc
        removed = get_storage().delete_driver_work_log(
            log_date, did, self._text(payload.get("vehicle_id"), 128))
        if not removed:
            raise ValueError("Bunday km qaydi topilmadi")
        return self._driver_detail(did, {"month": log_date[:7]})

    def _save_fine(self, driver_id: str, payload: dict) -> dict:
        from ..db.storage import get_storage

        did = self._driver_id(driver_id)
        fine_date = self._text(payload.get("date"), 10) or date.today().isoformat()
        try:
            date.fromisoformat(fine_date)
            amount = float(payload.get("amount") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Sana yoki jarima summasi noto'g'ri") from exc
        if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
            raise ValueError("Jarima summasi yaroqsiz")
        get_storage().add_driver_fine(did, fine_date, amount,
                                      self._text(payload.get("reason"), 500))
        return self._driver_detail(did, {"month": fine_date[:7]})

    def _test_notification(self, driver_id: str) -> dict:
        from ..db.storage import get_storage
        from ..notifications.telegram import send_message

        did = self._driver_id(driver_id)
        storage = get_storage()
        profile = storage.find("driver_profiles", driver_id=did) or {}
        driver = storage.find("drivers", external_id=did) or {}
        target = str(profile.get("notification_target") or "").strip()
        if not profile.get("notification_enabled") or not target:
            raise ValueError("Bildirishnomani yoqing va Telegram chat ID kiriting")
        if not target.isdigit() or len(target) > 32:
            raise ValueError("Telegram chat ID faqat raqamlardan iborat bo'lishi kerak")
        name = html.escape(str(driver.get("full_name") or did))
        message = (f"🚌 <b>Ishga chiqish eslatmasi</b>\n\n"
                   f"{name}, iltimos belgilangan vaqtda ishga chiqing.")
        try:
            send_message(message, chat_id=target)
            storage.record_notification(channel="telegram", target=target,
                                        message=message, status="SENT")
        except Exception as exc:  # Telegram sozlanmagan bo'lsa foydalanuvchiga aytamiz
            storage.record_notification(channel="telegram", target=target,
                                        message=message, status="FAILED")
            raise ValueError(f"Bildirishnoma yuborilmadi: {exc}") from exc
        return {"ok": True, "message": "Sinov bildirishnomasi yuborildi"}

    def _document_autofill(self, driver_id: str) -> dict:
        """Haydovchi profilidan xujjat maydonlarini avto-to'ldirish uchun ma'lumot."""
        from ..db.storage import get_storage
        from datetime import date as _date
        storage = get_storage()
        did = self._driver_id(driver_id)
        profile = storage.find("driver_profiles", driver_id=did) or {}
        driver = storage.find("drivers", external_id=did) or {}
        # Route name
        route_id = driver.get("route_id", "")
        route_name = ""
        if route_id:
            route = storage.find("routes", external_id=route_id)
            route_name = route.get("name", "") if route else ""
        return {
            "ok": True,
            "driver_id": did,
            "full_name": driver.get("full_name", ""),
            "tin": driver.get("tin", ""),
            "route_name": route_name,
            "phone": profile.get("phone", ""),
            "passport_number": profile.get("passport_number", ""),
            "passport_issued_by": profile.get("passport_issued_by", ""),
            "passport_expiry": profile.get("passport_expiry", ""),
            "license_number": profile.get("license_number", ""),
            "license_category": profile.get("license_category", ""),
            "license_expiry": profile.get("license_expiry", ""),
            "today": _date.today().isoformat(),
        }

    def _export(self, qs: dict) -> None:
        from . import export as ex

        p = _parse(qs)
        fmt = (p.get("format") or "csv").lower()
        scope = (p.get("scope") or "trips").lower()
        if fmt not in {"csv", "xls", "xlsx", "pdf"}:
            self._json({"error": "format noto'g'ri"}, 400)
            return
        if scope not in {"trips", "today", "routes", "vehicles", "drivers",
                         "all", "distance", "schedule", "attendance", "rating",
                         "electricity", "rejects", "tabel", "settings",
                         "tariffs", "brutto"}:
            self._json({"error": "scope noto'g'ri"}, 400)
            return
        try:
            filters = {k: p[k] for k in ("date", "from", "to", "month", "route",
                                         "vehicle", "driver", "status") if p.get(k)}
            data = ex.build_export(filters, fmt, scope)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 400)
            return
        fname = ex.filename(fmt, scope, p.get("date", "") or p.get("month", ""))
        self.send_response(200)
        self.send_header("Content-Type", ex.content_type(fmt))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{fname}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            log.info("Mijoz eksport faylni o'qiimasdan ulanishni yopdi (%s)", self.path)
            return

    # ------------------------------------------------------------------- HTTP

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        self._scope = _resolve_scope(self)
        # Static fayllar autentifikatsiyasiz; /api/* eskimizda kirish talab qilinadi.
        if path.startswith("/api/") and path not in ("/", "/index.html", "/favicon.ico"):
            if not self._scope:
                self._json({"error": "Kirish talab qilinadi"}, 401)
                return
            if not self._enforce_role(path):
                self._json({"error": "Sizda bu bo'limga ruxsat yo'q"}, 403)
                return
            if not self._scope.get("is_admin"):
                self._force_route(path, qs)
        try:
            if path in ("/", "/index.html"):
                self._serve_index()
                return
            elif path.startswith("/vendor/"):
                vendor_file = (WEB_DIR / path.lstrip("/")).resolve()
                if vendor_file.is_relative_to(WEB_DIR.resolve()) and vendor_file.exists() and vendor_file.is_file():
                    ct = "application/javascript" if path.endswith(".js") else "text/css"
                    self._file(vendor_file, ct)
                else:
                    self._json({"error": "fayl topilmadi"}, 404)
            elif path.endswith(".css") or path.endswith(".js"):
                web_file = (WEB_DIR / path.lstrip("/")).resolve()
                if web_file.is_relative_to(WEB_DIR.resolve()) and web_file.exists() and web_file.is_file():
                    ct = "application/javascript" if path.endswith(".js") else "text/css"
                    self._file(web_file, ct)
                else:
                    self._json({"error": "fayl topilmadi"}, 404)
            elif path == "/api/summary":
                from . import metrics as m
                rep = m.summary(_parse(qs))
                if isinstance(rep, dict) and rep.get("routes"):
                    rep["routes"] = self._strip_finance(rep["routes"])
                self._json(rep)
            elif path == "/api/rejects":
                from . import metrics as m
                rep = m.Metrics().not_accepted_km_report(
                    m.parse_filters(_parse(qs)))
                rep["ok"] = True
                self._json(rep)
            elif path == "/api/brutto":
                from . import metrics as m
                self._json(m.brutto(_parse(qs)))
            elif path == "/api/brutto/master":
                from . import metrics as m
                from ..brutto.master import master_report
                br = m.brutto(_parse(qs))
                agg = br.get("agg") or {}
                mode = (qs.get("mode") or ["multiplicative"])[0]
                mode = mode if mode in ("additive", "multiplicative") else "multiplicative"
                rep = master_report(
                    skm=float(br.get("skm") or br.get("base_skm") or 0),
                    lr=float(agg.get("lr") or 0),
                    lf=float(agg.get("lf") or 0),
                    kamal=int(agg.get("kamal") or 0),
                    kstjb=int(agg.get("kstjb") or 0),
                    kmaq=int(agg.get("kmaq") or 0),
                    report_month=_report_month(qs),
                    mode=mode,
                )
                rep["ok"] = True
                self._json(rep)
            elif path == "/api/rejects/tariff":
                from ..core.bot_settings import route_tariff
                rid = (qs.get("route") or [""])[0].strip()
                self._json({"ok": True, "route_id": rid,
                            **route_tariff(rid)})
            elif path == "/api/routes":
                from . import metrics as m
                routes = m.route_options()
                if not self._scope.get("is_admin"):
                    allowed = set(self._scope.get("routes") or [])
                    routes = [r for r in routes if str(r.get("id") or "") in allowed]
                routes = self._strip_finance(routes)
                self._json({"ok": True, "routes": routes})
            elif path == "/api/me":
                self._me()
                return
            elif path == "/api/dashboard-users":
                self._json(self._dashboard_users_api({"action": "list"}), 200)
                return
            elif path == "/api/route-skm":
                from ..core.bot_settings import route_skm
                rid = (qs.get("route") or [""])[0].strip()
                skm = route_skm(rid)
                self._json({"ok": True, "route_id": rid, "skm": skm})
            elif path == "/api/users":
                from ..core.bot_users import get_users, user_count
                users = get_users()
                # Har bir foydalanuvchining kimligi — Telegram chat_id va
                # telefon raqami bo'yicha haydovchi profilidan aniqlanadi.
                try:
                    from ..db.storage import get_storage as _gs
                    from ..notifications.ops.roles import resolve_role as _rr, role_label as _rl
                    _st = _gs()
                    if _st.enabled:
                        for u in users:
                            try:
                                cid = int(u.get("chat_id"))
                            except (TypeError, ValueError):
                                continue
                            try:
                                prof = _st.find_driver_by_telegram(cid) or {}
                            except Exception:  # noqa: BLE001
                                prof = {}
                            if not prof and str(u.get("phone") or "").strip():
                                try:
                                    prof = _st.find_driver_by_phone(
                                        str(u["phone"]).lstrip("+").removeprefix("998")) or {}
                                except Exception:  # noqa: BLE001
                                    prof = {}
                            if prof:
                                base = _st.find("drivers",
                                                external_id=str(prof.get("driver_id") or "")) or {}
                                u["driver_name"] = base.get("full_name") or \
                                    str(prof.get("driver_id") or "")
                                u["driver_phone"] = str(prof.get("phone") or "")
                                u["driver_id"] = str(prof.get("driver_id") or "")
                                u["linked"] = True
                            try:
                                u["resolved_role"] = _rl(_rr(cid))
                            except Exception:  # noqa: BLE001
                                pass
                except Exception as exc:  # noqa: BLE001
                    log.debug("bot user kimligi aniqlanmadi: %s", exc)
                self._json({"ok": True, "count": user_count(),
                            "users": users})
            elif path == "/api/group-stats":
                from ..core.group_stats import group_stats
                self._json({"ok": True, **group_stats()})
            elif path == "/api/dispatchers":
                from ..core.bot_users import get_users
                from ..db.storage import get_storage
                from ..notifications.ops.roles import resolve_role, role_label
                storage = get_storage()
                db_enabled = storage.enabled
                # Rol bo'yicha dispetcherlar + ularga biriktirilgan yo'nalishlar
                dispatchers = []
                seen = set()
                for u in get_users():
                    role = (u.get("role") or "").upper()
                    if not any(k in role for k in ("DISPATCHER", "ADMIN", "MANAGER")):
                        continue
                    try:
                        cid = int(u.get("chat_id"))
                    except (TypeError, ValueError):
                        continue
                    if cid in seen:
                        continue
                    seen.add(cid)
                    routes = storage.dispatcher_routes(cid) if db_enabled else []
                    dispatchers.append({
                        "chat_id": u.get("chat_id"),
                        "first_name": u.get("first_name", ""),
                        "username": u.get("username", ""),
                        "role": role,
                        "routes": routes,
                    })
                # Biriktirilgan yo'nalishi bor, lekin hali botni ishga tushirmagan
                # chat'lar ham ro'yxatda ko'rinsin (tahrirlash uchun).
                if db_enabled:
                    for row in storage.dispatcher_routes():
                        try:
                            cid = int(row.get("dispatcher_chat_id"))
                        except (TypeError, ValueError):
                            continue
                        if cid in seen:
                            continue
                        seen.add(cid)
                        dispatchers.append({
                            "chat_id": str(cid),
                            "first_name": "",
                            "username": "",
                            "role": role_label(resolve_role(cid)).replace("🛡 ", "").replace("📡 ", ""),
                            "routes": storage.dispatcher_routes(cid),
                        })
                self._json({"ok": True, "dispatchers": dispatchers})
            elif path.startswith("/api/users/"):
                parts = [unquote(x) for x in path.split("/") if x]
                if len(parts) == 3 and parts[2].isdigit():
                    from ..core.bot_users import get_user
                    uid = int(parts[2])
                    user = get_user(uid)
                    if user:
                        self._json({"ok": True, "user": user})
                    else:
                        self._json({"ok": False, "error": "Foydalanuvchi topilmadi"}, 404)
                elif len(parts) == 4 and parts[3] == "photo":
                    from ..core.bot_users import get_user
                    from ..notifications.telegram import get_profile_photo_url
                    uid = int(parts[2])
                    user = get_user(uid) or {}
                    photo_id = user.get("photo_file_id", "")
                    url = get_profile_photo_url(uid, photo_id)
                    if url:
                        self.send_response(302)
                        self.send_header("Location", url)
                        self.end_headers()
                    else:
                        self._json({"ok": False, "error": "Rasm topilmadi"}, 404)
                elif len(parts) == 4 and parts[3] == "driver-info":
                    from ..core.bot_users import get_user
                    from ..db.storage import get_storage
                    uid = int(parts[2])
                    user = get_user(uid) or {}
                    chat_id = user.get("chat_id", uid)
                    storage = get_storage()
                    dp = storage.find_driver_by_telegram(chat_id) if storage.enabled else None
                    driver_info = None
                    if dp:
                        did = str(dp.get("driver_id", ""))
                        base = storage.find("drivers", external_id=did) if storage.enabled else {}
                        driver_info = {
                            "driver_id": did,
                            "name": (base or {}).get("full_name", ""),
                            "route_id": (base or {}).get("route_id", ""),
                            "phone": dp.get("phone", ""),
                            "km_rate": dp.get("km_rate", 0),
                            "rating": dp.get("rating", 5),
                            "notification_enabled": dp.get("notification_enabled", 0),
                        }
                    self._json({"ok": True, "driver": driver_info})
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
            elif path == "/api/appeals":
                from . import metrics as m
                report = m.Metrics().appeals_report(_parse(qs))
                scopes = self._scope_routes()
                if scopes is not None:
                    from ..db.storage import get_storage
                    storage = get_storage()
                    report["appeals"] = [a for a in report["appeals"]
                                         if self._driver_in_scope(
                                             storage,
                                             str(a.get("driver_id") or ""),
                                             scopes)]
                self._json({"ok": True, **report})
            elif path == "/api/drivers":
                from . import metrics as m
                self._json({"ok": True, **m.Metrics().driver_directory(self._driver_filters(qs))})
            elif path == "/api/drivers/list":
                from ..db.storage import get_storage
                storage = get_storage()
                if storage.enabled:
                    rows = storage.query("SELECT d.external_id, d.full_name, d.route_id, "
                        "dp.telegram_chat_id FROM drivers d "
                        "LEFT JOIN driver_profiles dp ON dp.driver_id = d.external_id "
                        "ORDER BY d.full_name")
                    drivers = [{"driver_id": r.get("external_id", ""),
                               "name": r.get("full_name", ""),
                               "route_id": r.get("route_id", ""),
                               "telegram_chat_id": r.get("telegram_chat_id", "")} for r in rows]
                    scopes = self._scope_routes()
                    if scopes is not None:
                        drivers = [d for d in drivers
                                   if str(d.get("route_id") or "") in scopes]
                    self._json({"ok": True, "drivers": drivers})
                else:
                    self._json({"ok": False, "error": "DB rejimi o'chirilgan"}, 400)
            elif path.startswith("/api/vehicles/"):
                parts = [unquote(x) for x in path.split("/") if x]
                if len(parts) == 3:
                    from . import metrics as m
                    vid = m.Metrics().resolve_vehicle(parts[2], _parse(qs)) or parts[2]
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        row = get_storage().find("vehicles", external_id=vid)
                        if not row or str(row.get("route_id") or "") not in scopes:
                            self._json({"ok": False,
                                        "error": "Avtobus topilmadi"}, 404)
                            return
                    detail = m.Metrics().vehicle_detail(vid, _parse(qs))
                    if detail:
                        self._json({"ok": True, **detail})
                    else:
                        self._json({"ok": False, "error": "Avtobus topilmadi"}, 404)
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
            elif path.startswith("/api/drivers/"):
                parts = [unquote(x) for x in path.split("/") if x]
                scopes = self._scope_routes()
                if scopes is not None:
                    from ..db.storage import get_storage
                    if not self._driver_in_scope(get_storage(),
                                                 parts[2], scopes):
                        self._json({"error": "haydovchi topilmadi"}, 404)
                        return
                # /api/drivers/{id}/photo
                if len(parts) == 4 and parts[3] == "photo":
                    self._photo(parts[2])
                # /api/drivers/{id}/document/{kind}
                elif len(parts) == 5 and parts[3] == "document":
                    self._document(parts[2], parts[4])
                elif len(parts) == 3:
                    response = self._driver_detail(parts[2], qs)
                    self._json(response, 200 if response.get("ok") else 404)
                else:
                    self._json({"error": "haydovchi API manzili topilmadi"}, 404)
            elif path == "/api/month":
                from . import metrics as m
                self._json({"ok": True, **m.Metrics().monthly(_parse(qs))})
            elif path == "/api/attendance":
                self._json(self._attendance(_parse(qs)))
            elif path == "/api/health":
                from . import metrics as m
                sys = m.Metrics().system()
                self._json({"ok": True, "uptime_s": int(time.time() - _STARTED),
                            "system": sys})
            elif path == "/api/disk":
                self._json(self._disk_usage())
            elif path == "/api/sync/monthly/status":
                self._json(self._sync_status())
            elif path == "/api/sync/status":
                st = self._DAILY_SYNC_BG
                if st["running"]:
                    self._json({"ok": True, "running": True, "progress": st["progress"]})
                elif st["result"]:
                    self._json({"ok": True, "running": False, **st["result"]})
                else:
                    self._json({"ok": True, "running": False})
            elif path == "/api/backup/status":
                from ..db.backup import get_backup_state
                from ..config.settings import backup_enabled, backup_db_settings
                state = get_backup_state()
                state["enabled"] = backup_enabled()
                if backup_enabled():
                    dsn = backup_db_settings().get("dsn", "")
                    if "@" in dsn:
                        state["host"] = dsn.split("@")[1].split("/")[0]
                self._json({"ok": True, **state})
            elif path == "/api/backup/run":
                from ..db.backup import run_backup
                tables = _parse(qs).get("tables", "")
                table_list = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
                result = run_backup(tables=table_list)
                self._json(result)
            elif path == "/api/settings":
                self._json(self._get_settings())
            elif path == "/api/settings/companies":
                self._json(self._get_companies())
            elif path == "/api/settings/env":
                self._json(self._get_env_settings())
            elif path == "/api/electricity":
                from . import metrics as m
                self._json({"ok": True, **m.Metrics().electricity_report(_parse(qs))})
            elif path == "/api/electricity/price":
                from ..core.bot_settings import elec_price, ELEC_KWH_PER_KM
                self._json({"ok": True, "rate": elec_price(),
                            "kwh_per_km": ELEC_KWH_PER_KM})
            elif path == "/api/export":
                if not self._scope.get("is_admin"):
                    escope = (_parse(qs).get("scope") or "trips").lower()
                    if escope in ("settings", "tariffs"):
                        # Global sozlamalar/barcha firma tariflari — faqat ADMIN.
                        self._json({"error": "Sizda bu bo'limga ruxsat yo'q"}, 403)
                        return
                self._export(qs)
            elif path == "/api/check":
                from . import check as ck
                self._json(ck.run_check(_parse(qs)))
            # --- MyAI Agent ---
            elif path.startswith("/api/myai/task/"):
                parts = [unquote(x) for x in path.split("/") if x]
                if len(parts) == 4 and parts[3]:
                    from ..myai.state import get_task, get_logs
                    task = get_task(parts[3])
                    if task:
                        # Include logs in response
                        task["logs"] = get_logs(parts[3], limit=30)
                        self._json({"ok": True, "task": task})
                    else:
                        self._json({"ok": False, "error": "Vazifa topilmadi"}, 404)
                elif len(parts) == 5 and parts[3] == "logs" and parts[4]:
                    from ..myai.state import get_logs
                    logs = get_logs(parts[4], limit=50)
                    self._json({"ok": True, "logs": logs})
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
            elif path == "/api/myai/tasks":
                from ..myai.state import list_tasks, count_tasks
                try:
                    limit = min(max(int((qs.get("limit") or ["20"])[0]), 1), 200)
                    offset = max(int((qs.get("offset") or ["0"])[0]), 0)
                except (TypeError, ValueError):
                    limit, offset = 20, 0
                status = (qs.get("status") or [""])[0].strip()
                q = (qs.get("q") or [""])[0].strip()
                tasks = list_tasks(limit=limit, status=status, offset=offset, q=q)
                self._json({"ok": True, "tasks": tasks,
                            "total": count_tasks(status=status, q=q),
                            "limit": limit, "offset": offset})
            elif path == "/api/myai/status":
                try:
                    from ..myai.providers.factory import get_provider
                    from ..myai.state import get_stats
                    stats = get_stats()
                    provider = get_provider()
                    self._json({"ok": True, "provider": provider.name if provider else None,
                                "stats": stats})
                except Exception as exc:
                    log.warning("MyAI status xatosi: %s", exc)
                    self._json({"ok": True, "provider": None,
                                "stats": {"total": 0, "active": 0, "completed": 0, "failed": 0}})
            elif path == "/api/myai/registry":
                from ..myai.registry import all_agents
                self._json({"ok": True, "agents": all_agents()})
            elif path == "/api/myai/activity":
                from ..myai.events import get_event_bus
                limit = int((qs.get("limit") or ["50"])[0])
                events = get_event_bus().recent(limit)
                self._json({"ok": True, "events": events})
            elif path == "/api/myai/events":
                self._sse_events()
            elif path == "/api/myai/task/cancel":
                payload = self._payload()
                tid = (payload.get("task_id") or "").strip()
                if not tid:
                    self._json({"ok": False, "error": "task_id kerak"}, 400)
                    return
                from ..myai.state import update_task
                update_task(tid, status="cancelled", error="Foydalanuvchi tomonidan bekor qilindi")
                from ..myai.events import emit_task
                emit_task("task.cancelled", task_id=tid, message="Bekor qilindi")
                try:
                    from ..myai.orchestrator import get_orchestrator
                    get_orchestrator().cancel_task(tid)
                except Exception:
                    pass
                self._json({"ok": True})
            elif path == "/api/myai/task/pause":
                payload = self._payload()
                tid = (payload.get("task_id") or "").strip()
                if not tid:
                    self._json({"ok": False, "error": "task_id kerak"}, 400)
                    return
                try:
                    from ..myai.orchestrator import get_orchestrator
                    ok = get_orchestrator().pause_task(tid)
                    if ok:
                        from ..myai.events import emit_task
                        emit_task("task.paused", task_id=tid)
                        self._json({"ok": True, "action": "paused"})
                    else:
                        self._json({"ok": False, "error": "allaqachon pauzada"})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, 500)
            elif path == "/api/myai/task/resume":
                payload = self._payload()
                tid = (payload.get("task_id") or "").strip()
                if not tid:
                    self._json({"ok": False, "error": "task_id kerak"}, 400)
                    return
                try:
                    from ..myai.orchestrator import get_orchestrator
                    ok = get_orchestrator().resume_task(tid)
                    if ok:
                        from ..myai.events import emit_task
                        emit_task("task.resumed", task_id=tid)
                        self._json({"ok": True, "action": "resumed"})
                    else:
                        self._json({"ok": False, "error": "pauzada emas"})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, 500)
            elif path == "/api/insights":
                from ..notifications.ops import self_review, openrouter
                days = _parse(qs).get("days")
                a = self_review.analyze(days=int(days) if days else None)
                llm_text = None
                ai_stats = None
                if openrouter.configured():
                    try:
                        digest = self_review._digest(a)
                        llm_text = self_review._llm_recs(digest)
                        ai_stats = openrouter.last_stats()
                    except Exception:
                        ai_stats = openrouter.last_stats()
                a["recommendations"] = (
                    [llm_text] if llm_text
                    else self_review._rule_recs(a))
                self._json({"ok": True, "enabled": self_review.enabled(),
                            "ai_configured": openrouter.configured(),
                            "ai_model": openrouter.model(),
                            "ai_stats": ai_stats or {},
                            "insights": a})
            elif path == "/api/admin/summary":
                from .admin_service import salary_summary
                year, month = self._admin_ym(qs)
                self._json({"ok": True, **salary_summary(year, month)})
            elif path == "/api/admin/drivers":
                from .admin_service import _drivers
                year, month = self._admin_ym(qs)
                drivers = _drivers(year, month)
                self._json({"ok": True, "drivers": drivers})
            elif path == "/api/admin/avans":
                from ..db.storage import get_storage
                from .admin_service import avans_data
                month = (qs.get("month") or [""])[0]
                driver_id = (qs.get("driver_id") or [""])[0]
                route_id = (qs.get("route_id") or [""])[0]
                self._json({"ok": True,
                            **avans_data(get_storage(), month=month,
                                         driver_id=driver_id,
                                         route_id=route_id)})
            elif path == "/api/admin/fines":
                from ..db.storage import get_storage
                from .admin_service import fines_data
                month = (qs.get("month") or [""])[0]
                driver_id = (qs.get("driver_id") or [""])[0]
                route_id = (qs.get("route_id") or [""])[0]
                self._json({"ok": True,
                            **fines_data(get_storage(), month=month,
                                         driver_id=driver_id,
                                         route_id=route_id)})
            elif path == "/api/admin/staff":
                from ..db.storage import get_storage
                from .admin_service import staff_summary, STAFF_POSITIONS
                year, month = self._admin_ym(qs)
                company = (qs.get("company") or [""])[0]
                data = staff_summary(get_storage(), year, month)
                if company:
                    data["staff"] = [s for s in data["staff"]
                                     if s["company"] == company]
                self._json({"ok": True, "positions": STAFF_POSITIONS, **data})
            elif path == "/api/admin/staff/download":
                from .admin_service import staff_workbook
                company = (qs.get("company") or [""])[0]
                year, month = self._admin_ym(qs)
                data = staff_workbook(company, year, month)
                safe = re.sub(r"[^\w\-]+", "_", company) or "firma"
                name = f"xodimlar_{safe}_{year:04d}-{month:02d}.xlsx"
                self._send_xlsx(name, data)
            elif path == "/api/admin/download":
                from .admin_service import salary_workbook
                route_id = (qs.get("route_id") or [""])[0]
                year, month = self._admin_ym(qs)
                if not route_id:
                    self._json({"error": "route_id kerak"}, 400)
                    return
                data = salary_workbook(route_id, year, month)
                rname = re.sub(r"[^\w\-]+", "_", route_id)
                name = f"ish_haqi_{rname}_{year:04d}-{month:02d}.xlsx"
                self._send_xlsx(name, data)
            elif path == "/api/admin/download-all":
                from .admin_service import salary_all_workbook
                year, month = self._admin_ym(qs)
                res = salary_all_workbook(year, month)
                files = [f for f in res["files"] if f["data"]]
                if not files:
                    self._json({"error": "Ma'lumot topilmadi"}, 404)
                    return
                self._send_zip(files, f"oylik_ish_haqi_{year:04d}-{month:02d}.zip")
            elif path == "/api/salary":
                from_date = (qs.get("from") or [""])[0]
                to_date = (qs.get("to") or [""])[0]
                if not from_date or not to_date:
                    self._json({"error": "from va to parametrlari kerak"}, 400)
                    return
                from .export import salary_export, salary_filename, content_type
                scopes = self._scope_routes()
                data = salary_export(
                    from_date, to_date,
                    route=" ".join(sorted(scopes)) if scopes is not None else "")
                name = salary_filename(from_date, to_date)
                self.send_response(200)
                self.send_header("Content-Type", content_type("xlsx"))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    log.info("Mijoz ish haqi faylni o'qiimasdan ulanishni yopdi (%s)", self.path)
                    return
            elif path == "/api/logs":
                from ..utils.logger import FILE_LOG
                lines = int((qs.get("lines") or ["200"])[0])
                lines = min(lines, 2000)
                level_filter = (qs.get("level") or [""])[0].upper()
                search = (qs.get("q") or [""])[0]
                source = (qs.get("source") or ["bm"])[0].strip().lower()

                def _tail_lines(path: Path, n: int) -> list[str]:
                    """Fayl oxiridan ~n qatorni tez o'qish."""
                    try:
                        size = path.stat().st_size
                    except OSError:
                        return []
                    if size == 0:
                        return []
                    read_n = min(size, max(n * 200 + 8192, 65536))
                    try:
                        with open(path, "rb") as _f:
                            _f.seek(size - read_n)
                            raw = _f.read(read_n)
                        return raw.decode("utf-8", errors="replace").splitlines()[-n:]
                    except OSError:
                        return []

                bot_log = Path("state") / "watchdog_bot.out.log"
                wd_log = Path("state") / "watchdog.log"
                if source == "bot":
                    all_lines = _tail_lines(bot_log, lines)
                elif source == "watchdog":
                    all_lines = _tail_lines(wd_log, lines)
                elif source == "all":
                    # Barcha manbalar: har biridan oxirgi qatorlar, prefiks bilan.
                    per = max(lines // 3, 20)
                    bm_tail = _tail_lines(FILE_LOG, per)
                    all_lines = ([f"[BOT] {l}" for l in _tail_lines(bot_log, per)]
                                 + [f"[WD] {l}" for l in _tail_lines(wd_log, per)]
                                 + [f"[BM] {l}" for l in bm_tail])
                else:
                    # Standart: bm.log (asosiy tizim logi)
                    try:
                        size = FILE_LOG.stat().st_size
                    except OSError:
                        size = 0
                    if size == 0:
                        all_lines = []
                    else:
                        # taxminan 120 belgi/satr — kerakli satrlardan biroz ko'proq
                        read_n = min(size, max(lines * 160 + 8192, 65536))
                        try:
                            with open(FILE_LOG, "rb") as _f:
                                _f.seek(size - read_n)
                                raw = _f.read(read_n)
                            all_lines = raw.decode("utf-8", errors="replace").splitlines()
                        except OSError:
                            all_lines = []
                if level_filter:
                    all_lines = [l for l in all_lines
                                 if f"| {level_filter}" in l]
                if search:
                    sl = search.lower()
                    all_lines = [l for l in all_lines if sl in l.lower()]
                tail_lines = all_lines[-lines:]
                self._json({"ok": True, "lines": tail_lines,
                            "total": len(all_lines), "source": source})
            # --- Haydovchi SMS jurnali ---
            elif path == "/api/sms-log":
                from ..notifications.sms_notify import sms_log
                try:
                    limit = int((qs.get("limit") or ["200"])[0])
                except (TypeError, ValueError):
                    limit = 200
                status = (qs.get("status") or [""])[0].strip().upper()
                data = sms_log(limit=min(max(limit, 1), 1000), status=status)
                scopes = self._scope_routes()
                if scopes is not None:
                    data["rows"] = [r for r in data["rows"]
                                    if str(r.get("route_id") or "") in scopes]
                    data["count"] = len(data["rows"])
                    counts: dict[str, int] = {}
                    for r in data["rows"]:
                        st = (r.get("status") or "UNKNOWN").upper()
                        counts[st] = counts.get(st, 0) + 1
                    counts["total"] = data["count"]
                    data["counts"] = counts
                self._json({"ok": True, **data})
            elif path == "/api/sms/routes":
                from ..core.profiles import all_profiles
                from ..notifications.sms_notify import sms_route_list
                profiles = all_profiles()
                seen = {}
                for p in profiles:
                    rid = str(p.get("routeVariantId") or "").strip()
                    if rid and rid not in seen:
                        seen[rid] = (str(p.get("routeName")
                                         or p.get("name") or "")
                                     or rid)
                scopes = self._scope_routes()
                if scopes is not None:
                    seen = {rid: name for rid, name in seen.items()
                            if rid in scopes}
                self._json({"ok": True, "routes": sms_route_list(
                    routes=list(seen), route_names=seen)})
            # --- Xujjatlar bo'limi (documents) ---
            elif path == "/api/documents":
                from ..documents.manager import get_manager
                mgr = get_manager()
                category = (qs.get("category") or [""])[0]
                status = (qs.get("status") or [""])[0]
                search = (qs.get("q") or [""])[0]
                driver_id = (qs.get("driver_id") or [""])[0]
                docs = mgr.list_documents(category=category, status=status,
                                          search=search, driver_id=driver_id)
                scopes = self._scope_routes()
                if scopes is not None:
                    from ..db.storage import get_storage
                    storage = get_storage()
                    docs = [d for d in docs if self._doc_in_scope(
                        storage, str(d.get("driver_id") or ""), scopes)]
                # JSON serializable qilish
                for d in docs:
                    if "created_at" in d:
                        d["created_at"] = str(d["created_at"])
                    if "updated_at" in d:
                        d["updated_at"] = str(d["updated_at"])
                self._json({"ok": True, "documents": docs})
            elif path == "/api/documents/templates":
                from ..documents.templates import list_templates, CATEGORIES
                self._json({"ok": True, "templates": list_templates(),
                            "categories": dict(CATEGORIES)})
            elif path == "/api/documents/autofill":
                driver_id = (qs.get("driver_id") or [""])[0]
                if not driver_id:
                    self._json({"ok": False, "error": "driver_id kerak"}, 400)
                    return
                scopes = self._scope_routes()
                if scopes is not None:
                    from ..db.storage import get_storage
                    if not self._driver_in_scope(get_storage(), driver_id,
                                                 scopes):
                        self._json({"error": "haydovchi topilmadi"}, 404)
                        return
                self._json(self._document_autofill(driver_id))
            elif path.startswith("/api/documents/"):
                parts = [unquote(x) for x in path.split("/") if x]
                # /api/documents/{id}
                if len(parts) == 3 and parts[2].isdigit():
                    from ..documents.manager import get_manager
                    doc = get_manager().get_document(int(parts[2]))
                    if doc:
                        scopes = self._scope_routes()
                        if scopes is not None:
                            from ..db.storage import get_storage
                            if not self._doc_in_scope(
                                    get_storage(),
                                    str(doc.get("driver_id") or ""), scopes):
                                doc = None
                    if doc:
                        if "created_at" in doc:
                            doc["created_at"] = str(doc["created_at"])
                        if "updated_at" in doc:
                            doc["updated_at"] = str(doc["updated_at"])
                        self._json({"ok": True, "document": doc})
                    else:
                        self._json({"error": "Xujjat topilmadi"}, 404)
                # /api/documents/{id}/html
                elif len(parts) == 4 and parts[3] == "html":
                    from ..documents.manager import get_manager
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        doc = get_manager().get_document(int(parts[2]))
                        if not doc or not self._doc_in_scope(
                                get_storage(),
                                str(doc.get("driver_id") or ""), scopes):
                            self._json({"error": "Xujjat topilmadi"}, 404)
                            return
                    html = get_manager().get_html(int(parts[2]))
                    if html:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        try:
                            self.wfile.write(html.encode("utf-8"))
                        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                            log.info("Mijoz xujjat o'qiimasdan ulanishni yopdi (%s)", self.path)
                            return
                    else:
                        self._json({"error": "Xujjat topilmadi"}, 404)
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
            else:
                self._json({"error": "yo'nalish topilmadi"}, 404)
        except Exception:  # noqa: BLE001 - server yiqilmaydi
            log.exception("dashboard so'rov xatosi: %s", self.path)
            self._json({"ok": False, "error": "Serverda xatolik yuz berdi"}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/api/login", "/api/logout"):
            if path == "/api/login":
                self._login()
            else:
                self._logout()
            return
        self._scope = _resolve_scope(self)
        if not self._scope:
            self._json({"error": "Kirish talab qilinadi"}, 401)
            return
        if not self._enforce_role(path):
            self._json({"error": "Sizda bu bo'limga ruxsat yo'q"}, 403)
            return
        if not _rate_limit_post(path):
            self._json({"error": "Juda ko'p so'rov — bir oz kuting"}, 429)
            return
        try:
            if path == "/api/dashboard-users":
                result = self._dashboard_users_api(self._payload())
                self._json(result, 200 if result.get("ok") else 400)
                return
            elif path == "/api/kmrate":
                payload = self._payload()
                if not self._route_allowed(payload.get("route_id")):
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
                result = self._update_km_rate(payload)
            elif path == "/api/sync":
                result = self._run_sync(self._payload())
            # --- MyAI Agent ---
            elif path == "/api/myai/task":
                payload = self._payload()
                request_text = (payload.get("request") or "").strip()
                if not request_text:
                    self._json({"ok": False, "error": "request kiritilmagan"}, 400)
                    return
                from ..myai.state import create_task
                task = create_task(request_text)
                task_id = task["task_id"] if isinstance(task, dict) else task.task_id
                import threading
                def _run_myai():
                    try:
                        from ..myai.orchestrator import get_orchestrator
                        orch = get_orchestrator()
                        import asyncio
                        asyncio.run(orch.run_task(task_id))
                    except Exception:
                        log.exception("MyAI task xatosi #%s", task_id)
                threading.Thread(target=_run_myai, daemon=True).start()
                result = {"ok": True, "task_id": task_id}
            elif path == "/api/sync/monthly":
                result = self._run_sync_monthly(self._payload())
            elif path == "/api/settings":
                result = self._update_settings(self._payload())
            elif path == "/api/settings/env":
                result = self._update_env_settings(self._payload())
            elif path == "/api/settings/companies":
                result = self._company_action(self._payload())
            elif path == "/api/sms/retry":
                from ..notifications.sms_notify import retry_failed_sms
                payload = self._payload()
                try:
                    limit = int((payload.get("limit") or 50))
                except (TypeError, ValueError):
                    limit = 50
                result = retry_failed_sms(limit=min(max(limit, 1), 200))
                result = {"ok": True, **result}
            elif path == "/api/sms/send":
                from ..notifications.sms_notify import send_and_log
                payload = self._payload()
                phone = str(payload.get("phone") or "").strip()
                text = str(payload.get("text") or "").strip()
                if not phone or not text:
                    self._json({"ok": False,
                                "error": "Telefon va matn kiritilishi kerak"}, 400)
                    return
                result = send_and_log(
                    phone=phone, text=text[:500],
                    name=str(payload.get("name") or phone))
                result = {"ok": True, **result}
            elif path == "/api/sms/routes":
                from ..notifications.sms_notify import sms_route_set
                payload = self._payload()
                route_id = str(payload.get("route_id") or "").strip()
                enabled = payload.get("enabled")
                if not route_id and enabled is None:
                    self._json({"ok": False,
                                "error": "route_id yoki enabled kerak"}, 400)
                    return
                if not self._route_allowed(route_id):
                    self._json({"ok": False, "error": "Yo'nalish topilmadi"}, 404)
                    return
                result = sms_route_set(route_id=route_id,
                                       enabled=enabled if enabled is not None
                                       else None)
                self._json({"ok": True, **result})
                return
            elif path == "/api/admin/avans":
                from ..db.storage import get_storage
                from .admin_service import avans_add
                payload = self._payload()
                res = avans_add(
                    get_storage(),
                    driver_id=str(payload.get("driver_id") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                    amount=payload.get("amount"),
                    pay_date=str(payload.get("pay_date") or "").strip(),
                    route_id=str(payload.get("route_id") or "").strip(),
                    route_name=str(payload.get("route_name") or "").strip(),
                    note=str(payload.get("note") or "").strip())
                code = 200 if res.get("ok") else 400
                self._json(res, code)
                return
            elif path == "/api/admin/avans/delete":
                from ..db.storage import get_storage
                from .admin_service import avans_remove
                payload = self._payload()
                try:
                    row_id = int(payload.get("id") or 0)
                except (TypeError, ValueError):
                    row_id = 0
                res = avans_remove(get_storage(), row_id)
                self._json(res, 200 if res.get("ok") else 400)
                return
            elif path == "/api/admin/fines/delete":
                from ..db.storage import get_storage
                from .admin_service import fine_remove
                payload = self._payload()
                try:
                    row_id = int(payload.get("id") or 0)
                except (TypeError, ValueError):
                    row_id = 0
                res = fine_remove(get_storage(), row_id)
                self._json(res, 200 if res.get("ok") else 400)
                return
            elif path == "/api/admin/staff":
                from ..db.storage import get_storage
                payload = self._payload()
                st = get_storage()
                row_id = payload.get("id")
                name = str(payload.get("name") or "").strip()
                position = str(payload.get("position") or "").strip()
                company = str(payload.get("company") or "").strip()
                salary_type = str(payload.get("salary_type") or "oylik").strip()
                try:
                    rate = float(payload.get("rate") or 0)
                except (TypeError, ValueError):
                    rate = 0.0
                try:
                    days = int(payload.get("days") or 0)
                except (TypeError, ValueError):
                    days = 0
                note = str(payload.get("note") or "").strip()
                ok = False
                if row_id is not None and str(row_id) not in ("", "0"):
                    try:
                        rid = int(row_id)
                    except (TypeError, ValueError):
                        rid = 0
                    ok = st.staff_update(rid, name=name, position=position,
                                         company=company,
                                         salary_type=salary_type,
                                         rate=rate, days=days, note=note)
                    self._json({"ok": ok, "id": rid},
                               200 if ok else 400)
                    return
                if not name:
                    self._json({"ok": False, "error": "Ism kiritilmagan"}, 400)
                    return
                if salary_type not in ("oylik", "kunbay"):
                    salary_type = "oylik"
                new_id = st.staff_add(name=name, position=position,
                                      company=company, salary_type=salary_type,
                                      rate=rate, days=days, note=note)
                if not new_id:
                    self._json({"ok": False, "error": "Xodim saqlanmadi"}, 400)
                    return
                self._json({"ok": True, "id": new_id})
                return
            elif path == "/api/admin/staff/delete":
                from ..db.storage import get_storage
                payload = self._payload()
                try:
                    row_id = int(payload.get("id") or 0)
                except (TypeError, ValueError):
                    row_id = 0
                res = {"ok": get_storage().delete_staff(row_id), "id": row_id}
                self._json(res, 200 if res.get("ok") else 400)
                return
            elif path == "/api/settings/companies":
                result = self._update_company_creds(self._payload())
            elif path == "/api/electricity/price":
                result = self._update_elec_price(self._payload())
            elif path == "/api/rejects/tariff":
                payload = self._payload()
                if not self._route_allowed(payload.get("route_id")):
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
                result = self._update_rejects_tariff(payload)
            elif path == "/api/route-skm":
                payload = self._payload()
                if not self._route_allowed(payload.get("route_id")):
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
                result = self._update_route_skm(payload)
            elif path == "/api/appeals":
                payload = self._payload()
                from ..db.storage import get_storage
                storage = get_storage()
                if not storage.enabled:
                    self._json({"ok": False, "error": "DB rejimi o'chirilgan"}, 400)
                    return
                action = str(payload.get("action") or "add").strip()
                try:
                    aid = int(payload.get("id") or 0)
                except (TypeError, ValueError):
                    aid = 0
                scopes = self._scope_routes()
                if scopes is not None and action in ("update", "reply", "delete"):
                    if not aid:
                        self._json({"ok": False,
                                    "error": "Murojaat ID xato"}, 400)
                        return
                    if not self._driver_in_scope(
                            storage, self._appeal_driver(storage, aid),
                            scopes):
                        self._json({"ok": False,
                                    "error": "Murojaat topilmadi"}, 404)
                        return
                if action in ("update", "reply"):
                    if not aid:
                        self._json({"ok": False, "error": "Murojaat ID xato"}, 400)
                        return
                    if action == "reply":
                        result = storage.appeal_update(
                            aid, reply=str(payload.get("reply") or "").strip(),
                            status=str(payload.get("status") or "JAVOB_YOZILDI").upper())
                    else:
                        result = storage.appeal_update(
                            aid, title=payload.get("title"),
                            text=payload.get("text"),
                            status=payload.get("status"),
                            reply=payload.get("reply"))
                    self._json({"ok": bool(result), "id": aid})
                elif action == "delete":
                    if not aid:
                        self._json({"ok": False, "error": "Murojaat ID xato"}, 400)
                        return
                    self._json({"ok": storage.appeal_delete(aid), "id": aid})
                else:
                    did = str(payload.get("driver_id") or "").strip()
                    if not did:
                        self._json({"ok": False,
                                    "error": "Haydovchi ko'rsatilmagan"}, 400)
                        return
                    if scopes is not None and not self._driver_in_scope(
                            storage, did, scopes):
                        self._json({"ok": False,
                                    "error": "Haydovchi topilmadi"}, 404)
                        return
                    aid = storage.appeal_add(
                        did, title=str(payload.get("title") or "").strip(),
                        text=str(payload.get("text") or "").strip(),
                        status=str(payload.get("status") or "YANGI").upper())
                    self._json({"ok": bool(aid), "id": aid})
                return
            elif path == "/api/drivers":
                payload = self._payload()
                if not self._driver_write_allowed(payload):
                    self._json({"error": "haydovchi topilmadi"}, 404)
                    return
                result = self._save_driver(payload)
            elif path.startswith("/api/drivers/"):
                parts = [unquote(x) for x in path.split("/") if x]
                if len(parts) != 4:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
                driver_id, action = parts[2], parts[3]
                scopes = self._scope_routes()
                if scopes is not None:
                    from ..db.storage import get_storage
                    if not self._driver_in_scope(get_storage(),
                                                 driver_id, scopes):
                        self._json({"error": "haydovchi topilmadi"}, 404)
                        return
                payload = self._payload()
                if action == "profile":
                    # Boshqa korxona route'iga ko'chirishga yo'l qo'ymaymiz.
                    if scopes is not None and not self._driver_write_allowed(
                            payload, driver_id):
                        self._json({"error": "haydovchi topilmadi"}, 404)
                        return
                    result = self._save_driver(payload, driver_id)
                elif action == "photo":
                    result = self._save_photo(driver_id, payload)
                elif action == "document":
                    result = self._save_document(driver_id, payload)
                elif action == "work-log":
                    result = self._save_work_log(driver_id, payload)
                elif action == "work-log-delete":
                    result = self._delete_work_log(driver_id, payload)
                elif action == "fine":
                    result = self._save_fine(driver_id, payload)
                elif action == "notification-test":
                    result = self._test_notification(driver_id)
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
            # --- Xujjatlar bo'limi (documents) POST ---
            elif path == "/api/documents":
                payload = self._payload()
                scopes = self._scope_routes()
                if scopes is not None and payload.get("driver_id"):
                    from ..db.storage import get_storage
                    if not self._driver_in_scope(
                            get_storage(),
                            str(payload.get("driver_id") or ""), scopes):
                        self._json({"error": "haydovchi topilmadi"}, 404)
                        return
                from ..documents.manager import get_manager
                result = get_manager().create_document(
                    template_key=payload.get("template_key", ""),
                    fields=payload.get("fields", {}),
                    title=payload.get("title", ""),
                    status=payload.get("status", "draft"),
                    driver_id=payload.get("driver_id", ""),
                )
            # --- Bot foydalanuvchilari ---
            elif path.startswith("/api/dispatchers/") and path.rstrip("/").endswith("/routes"):
                from ..db.storage import get_storage
                parts = [unquote(x) for x in path.split("/") if x]
                # /api/dispatchers/{id}/routes
                if len(parts) == 4 and parts[3] == "routes" and parts[2].isdigit():
                    storage = get_storage()
                    if not storage.enabled:
                        self._json({"ok": False, "error": "DB rejimi o'chirilgan"}, 400)
                        return
                    uid = int(parts[2])
                    payload = self._payload()
                    routes = payload.get("routes") or []
                    # Har bir route: {route_id, route_name, company, phone}
                    cleaned = []
                    for r in routes:
                        if isinstance(r, dict) and (r.get("route_id") or r.get("id")):
                            cleaned.append({
                                "route_id": str(r.get("route_id") or r.get("id") or ""),
                                "route_name": str(r.get("route_name") or r.get("name") or ""),
                                "company": str(r.get("company") or ""),
                                "phone": str(r.get("phone") or ""),
                            })
                    # Faqat mavjud (profiles.json routeVariantId) yo'nalishlarni
                    # qabul qilamiz; kompaniya/nom bo'sh bo'lsa avtomatik to'ldiramiz.
                    from . import metrics as m
                    opts = {str(o.get("id") or ""): o for o in m.route_options()}
                    cleaned = [
                        {"route_id": c["route_id"],
                         "route_name": c["route_name"] or opts[c["route_id"]].get("route_name", ""),
                         "company": c["company"] or opts[c["route_id"]].get("company", ""),
                         "phone": c["phone"]}
                        for c in cleaned if c["route_id"] in opts
                    ]
                    storage.set_dispatcher_routes(uid, cleaned)
                    self._json({"ok": True, "routes": storage.dispatcher_routes(uid)})
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
            elif path.startswith("/api/users/"):
                parts = [unquote(x) for x in path.split("/") if x]
                if len(parts) == 4 and parts[3] == "role":
                    from ..core.bot_users import set_role
                    uid = int(parts[2])
                    payload = self._payload()
                    role = (payload.get("role") or "").strip()
                    if not role:
                        self._json({"ok": False, "error": "Rol ko'rsatilmagan"}, 400)
                        return
                    ok = set_role(uid, role)
                    self._json({"ok": ok})
                elif len(parts) == 4 and parts[3] == "delete":
                    from ..core.bot_users import remove_user
                    uid = int(parts[2])
                    ok = remove_user(uid)
                    self._json({"ok": ok})
                elif len(parts) == 4 and parts[3] == "link-driver":
                    from ..core.bot_users import get_user
                    from ..db.storage import get_storage
                    uid = int(parts[2])
                    payload = self._payload()
                    driver_id = str(payload.get("driver_id") or "").strip()
                    storage = get_storage()
                    if not storage.enabled:
                        self._json({"ok": False, "error": "DB rejimi o'chirilgan"}, 400)
                        return
                    if driver_id:
                        storage.link_driver_telegram(driver_id, uid)
                        self._json({"ok": True, "message": f"Haydovchi {driver_id} Telegram {uid} ga bog'landi"})
                    else:
                        # unlink
                        storage.db.execute(
                            "UPDATE driver_profiles SET telegram_chat_id = '' "
                            "WHERE telegram_chat_id = " + storage.db.ph,
                            (str(uid),))
                        self._json({"ok": True, "message": "Bog'lanish o'chirildi"})
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
            elif path == "/api/documents/ai-generate":
                payload = self._payload()
                from ..documents.manager import get_manager
                result = get_manager().ai_generate(
                    template_key=payload.get("template_key", ""),
                    context=payload.get("context", {}),
                    custom_prompt=payload.get("prompt", ""),
                )
            elif path.startswith("/api/documents/"):
                parts = [unquote(x) for x in path.split("/") if x]
                # /api/documents/{id}
                if len(parts) == 3 and parts[2].isdigit():
                    from ..documents.manager import get_manager
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        doc = get_manager().get_document(int(parts[2]))
                        if not doc or not self._doc_in_scope(
                                get_storage(),
                                str(doc.get("driver_id") or ""), scopes):
                            self._json({"error": "Xujjat topilmadi"}, 404)
                            return
                    payload = self._payload()
                    result = get_manager().update_document(
                        doc_id=int(parts[2]),
                        fields=payload.get("fields"),
                        title=payload.get("title"),
                        status=payload.get("status"),
                    )
                # /api/documents/{id}/delete
                elif len(parts) == 4 and parts[3] == "delete":
                    from ..documents.manager import get_manager
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        doc = get_manager().get_document(int(parts[2]))
                        if not doc or not self._doc_in_scope(
                                get_storage(),
                                str(doc.get("driver_id") or ""), scopes):
                            self._json({"error": "Xujjat topilmadi"}, 404)
                            return
                    result = get_manager().delete_document(int(parts[2]))
                # /api/documents/{id}/attach
                elif len(parts) == 4 and parts[3] == "attach":
                    from ..documents.manager import get_manager
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        doc = get_manager().get_document(int(parts[2]))
                        if not doc or not self._doc_in_scope(
                                get_storage(),
                                str(doc.get("driver_id") or ""), scopes):
                            self._json({"error": "Xujjat topilmadi"}, 404)
                            return
                    payload = self._payload()
                    result = get_manager().attach_file(
                        int(parts[2]), payload.get("file_path", ""))
                # /api/documents/{id}/detach
                elif len(parts) == 4 and parts[3] == "detach":
                    from ..documents.manager import get_manager
                    scopes = self._scope_routes()
                    if scopes is not None:
                        from ..db.storage import get_storage
                        doc = get_manager().get_document(int(parts[2]))
                        if not doc or not self._doc_in_scope(
                                get_storage(),
                                str(doc.get("driver_id") or ""), scopes):
                            self._json({"error": "Xujjat topilmadi"}, 404)
                            return
                    payload = self._payload()
                    result = get_manager().detach_file(
                        int(parts[2]), payload.get("file_path", ""))
                else:
                    self._json({"error": "yo'nalish topilmadi"}, 404)
                    return
            else:
                self._json({"error": "yo'nalish topilmadi"}, 404)
                return
            self._json(result)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception:  # noqa: BLE001
            log.exception("sync xatosi")
            self._json({"ok": False, "error": "Serverda xatolik yuz berdi"}, 500)

    def _attendance(self, params: dict) -> dict:
        """Haydovchilar ishga chiqish kalendarini qaytaradi."""
        from ..db.storage import get_storage
        storage = get_storage()
        if not storage.enabled:
            return {"ok": False, "error": "DB rejimi o'chirilgan"}
        month = params.get("month") or ""
        if not month:
            from datetime import date
            month = date.today().strftime("%Y-%m")
        route_filter = params.get("route", "")
        from_date = month + "-01"
        # oxirgi kunni hisoblash
        parts = month.split("-")
        y, m = int(parts[0]), int(parts[1])
        if m == 12:
            to_date = f"{y + 1}-01-01"
        else:
            to_date = f"{y}-{m + 1:02d}-01"
        # trips jadvalidan haydovchilar ish kunlarini olish
        ph = storage.db.ph
        query = (
            "SELECT d.external_id AS driver_id, d.full_name, d.route_id, "
            "  t.date, COUNT(*) AS trip_count "
            "FROM trips t "
            "JOIN drivers d ON d.external_id = t.driver_id "
            f"WHERE t.date >= {ph} AND t.date < {ph} "
            "  AND t.status IN ('ACCEPTED','APPROVED') "
        )
        args: list = [from_date, to_date]
        if route_filter:
            query += f" AND d.route_id = {ph} "
            args.append(route_filter)
        query += " GROUP BY d.external_id, d.full_name, d.route_id, t.date ORDER BY d.full_name, t.date"
        try:
            rows = storage.db.query(query, args) or []
        except Exception:
            log.exception("attendance query error")
            return {"ok": False, "error": "Query xatosi"}
        # Haydovchilar bo'yicha guruhlash
        drivers_map: dict = {}
        for r in rows:
            did = r["driver_id"]
            if did not in drivers_map:
                drivers_map[did] = {
                    "driver_id": did,
                    "full_name": r["full_name"],
                    "route_id": r["route_id"],
                    "work_days": {},
                }
            drivers_map[did]["work_days"][r["date"]] = r["trip_count"]

        # Qo'lda kirilgan ish kunlari (driver_work_logs, note != 'AVTO') ham
        # kalendarga kiritiladi — tizimda reys qayd etilmagan kuni haydovchi
        # ishlagan deb ko'rinadi. Shu kuni allaqachon trips bo'lsa,
        # trips miqdori ustun turadi (qo'lda qayd qo'shilmaydi).
        # Qo'lda qayd kiritilgan (driver, sana) juftlari eslab qolinadi —
        # AVTO (brutto-route) hisobi ularni ustiga yozmaydi.
        manual_keys: set = set()
        try:
            wl_query = (
                "SELECT d.external_id AS driver_id, d.full_name, d.route_id,"
                "  w.date, COALESCE(w.trip_count, 0) AS trip_count"
                " FROM driver_work_logs w"
                " JOIN drivers d ON d.external_id = w.driver_id"
                f" WHERE w.note <> {ph} AND w.driver_id <> ''"
                f"  AND w.date >= {ph} AND w.date < {ph} ")
            wl_args: list = ["AVTO", from_date, to_date]
            if route_filter:
                wl_query += f" AND d.route_id = {ph} "
                wl_args.append(route_filter)
            for r in storage.db.query(wl_query, wl_args) or []:
                did = r["driver_id"]
                if did not in drivers_map:
                    drivers_map[did] = {
                        "driver_id": did,
                        "full_name": r["full_name"],
                        "route_id": r["route_id"],
                        "work_days": {},
                    }
                manual_keys.add((did, r["date"]))
                if r["date"] not in drivers_map[did]["work_days"]:
                    drivers_map[did]["work_days"][r["date"]] = r["trip_count"]
        except Exception:
            log.exception("attendance work_log query error")

        # AVTO (brutto-route / route_daily) ish kunlari — saytdagi rasmiy
        # hisob: faqat ishlangan (working_day) kunlar kalendarga kiritiladi.
        # Trip bilan takrorlangan kunda sayt qiymati ustun turadi; qo'lda
        # (dlog) qayd kiritilgan kun o'zgarmaydi.
        try:
            avto_query = (
                "SELECT d.external_id AS driver_id, d.full_name, d.route_id,"
                "  w.date, COALESCE(w.trip_count, 0) AS trip_count"
                " FROM driver_work_logs w"
                " JOIN drivers d ON d.external_id = w.driver_id"
                f" WHERE w.note = {ph} AND w.driver_id <> ''"
                f"  AND w.date >= {ph} AND w.date < {ph}"
                f"  AND COALESCE(w.working_day, 0) > 0 ")
            avto_args: list = ["AVTO", from_date, to_date]
            if route_filter:
                avto_query += f" AND d.route_id = {ph} "
                avto_args.append(route_filter)
            for r in storage.db.query(avto_query, avto_args) or []:
                did = r["driver_id"]
                if did not in drivers_map:
                    drivers_map[did] = {
                        "driver_id": did,
                        "full_name": r["full_name"],
                        "route_id": r["route_id"],
                        "work_days": {},
                    }
                if (did, r["date"]) not in manual_keys:
                    drivers_map[did]["work_days"][r["date"]] = r["trip_count"]
        except Exception:
            log.exception("attendance avto query error")
        drivers = sorted(drivers_map.values(), key=lambda d: d["full_name"])
        return {"ok": True, "month": month, "drivers": drivers}

    _DAILY_SYNC_BG: dict = {"running": False, "progress": "", "result": None}

    def _run_sync(self, payload: dict) -> dict:
        """BM API -> DB sync (background thread — OneID login 1-3 daqiqa olishi mumkin).

        ``date`` (bitta kun) yoki ``from``/``to`` (oralik) qabul qiladi.
        Natijalar ``lines`` da tushunarli o'zbekcha izoh bilan qaytadi.
        """
        global _last_sync_at
        if self._DAILY_SYNC_BG["running"]:
            return {"ok": False, "error": "Sinxronlash davom etmoqda",
                    "progress": self._DAILY_SYNC_BG["progress"]}

        with _SYNC_LOCK:
            now = time.monotonic()
            if now - _last_sync_at < _SYNC_MIN_INTERVAL_S:
                return {"ok": False, "error": "Sync juda tez-tez chaqirildi — kutib turing"}

        from datetime import date as _date, timedelta as _td
        from ..db.storage import get_storage

        route = str(payload.get("route") or "").strip()
        date_str = str(payload.get("date") or "").strip()
        from_str = str(payload.get("from") or "").strip()
        to_str = str(payload.get("to") or "").strip()

        try:
            if from_str and to_str:
                d_from = _date.fromisoformat(from_str)
                d_to = _date.fromisoformat(to_str)
                if d_to < d_from:
                    return {"ok": False, "error": "'to' sana 'from'dan oldin bo'lishi mumkin emas"}
            elif date_str:
                d_from = d_to = _date.fromisoformat(date_str)
            else:
                d_from = d_to = _date.today()
        except ValueError:
            return {"ok": False, "error": "Sana formati noto'g'ri (YYYY-MM-DD)"}

        storage = get_storage()
        if not storage.enabled:
            return {"ok": False, "error": "DB rejimi o'chirilgan"}

        self._DAILY_SYNC_BG["running"] = True
        self._DAILY_SYNC_BG["progress"] = "Login..."
        self._DAILY_SYNC_BG["result"] = None

        _route = route
        _d_from, _d_to = d_from, d_to
        _from_s, _to_s = d_from.isoformat(), d_to.isoformat()

        def _bg():
            global _last_sync_at
            try:
                from ..api.client import BMClient
                from ..db import sync as db_sync

                import datetime as _dt

                self._DAILY_SYNC_BG["progress"] = "Token olinmoqda..."
                client = BMClient()
                client.login()

                db = storage.db
                results = []
                results.append(db_sync.sync_routes(storage, client).as_dict())
                cur = _d_from
                total = (_d_to - _d_from).days + 1
                day_no = 0
                while cur <= _d_to:
                    ds = cur.isoformat()
                    day_no += 1
                    self._DAILY_SYNC_BG["progress"] = f"{_fmt_date(ds)} ({day_no}/{total})"

                    def _sync_day(day=ds):
                        day_results = []
                        if _route:
                            for r in db_sync.sync_route_day(storage, client, _route, day):
                                day_results.append(r.as_dict())
                            day_results.append(db_sync.sync_waybills(
                                storage, client, _route, day, day).as_dict())
                            day_results.append(db_sync.sync_work_logs(
                                storage, day, day, _route).as_dict())
                        else:
                            for r in db_sync.sync_all_profiles(storage, client, day):
                                day_results.append(r.as_dict())
                        return day_results

                    results.extend(db.retry_operation(
                        _sync_day, max_retries=3, delay=3.0))
                    cur += _dt.timedelta(days=1)

                ok = not any(r.get("error") for r in results)
                _last_sync_at = time.monotonic()
                self._DAILY_SYNC_BG["result"] = {
                    "ok": ok, "route": _route,
                    "from": _from_s, "to": _to_s, "days": total,
                    "results": results, "lines": _sync_lines(results),
                }
            except Exception as exc:
                self._DAILY_SYNC_BG["result"] = {"ok": False, "error": str(exc)}
            finally:
                self._DAILY_SYNC_BG["running"] = False
                self._DAILY_SYNC_BG["progress"] = ""

        threading.Thread(target=_bg, daemon=True).start()
        return {"ok": True, "started": True, "route": route,
                "from": _from_s, "to": _to_s, "days": (_d_to - _d_from).days + 1}

    # --- Oylik sync background state ---
    _SYNC_BG_STATE: dict = {"running": False, "progress": "", "result": None}

    def _run_sync_monthly(self, payload: dict) -> dict:
        """Oylik sinxronlash — from/to oralig'idagi kunlarni sinxronlaydi."""
        global _last_sync_at
        if self._SYNC_BG_STATE["running"]:
            return {"ok": False, "error": "Sinxronlash davom etmoqda",
                    "progress": self._SYNC_BG_STATE["progress"]}

        with _SYNC_LOCK:
            now = time.monotonic()
            if now - _last_sync_at < _SYNC_MIN_INTERVAL_S:
                return {"ok": False, "error": "Sync juda tez-tez chaqirildi — kutib turing"}

        from_str = str(payload.get("from") or "").strip()
        to_str = str(payload.get("to") or "").strip()
        route = str(payload.get("route") or "").strip()

        from datetime import date as _date
        try:
            d_from = _date.fromisoformat(from_str) if from_str else _date.today().replace(day=1)
            d_to = _date.fromisoformat(to_str) if to_str else _date.today()
        except ValueError:
            return {"ok": False, "error": "Sana formati noto'g'ri (YYYY-MM-DD)"}

        self._SYNC_BG_STATE["running"] = True
        self._SYNC_BG_STATE["progress"] = f"{_fmt_date(d_from.isoformat())} → {_fmt_date(d_to.isoformat())}"
        self._SYNC_BG_STATE["result"] = None

        import datetime as _dt
        _from_s, _to_s, _route = from_str, to_str, route
        _d_from, _d_to = d_from, d_to

        def _bg():
            global _last_sync_at
            try:
                from ..api.client import BMClient
                from ..db.storage import get_storage
                from ..db import sync as db_sync

                storage = get_storage()
                client = BMClient()
                client.login()

                db = storage.db
                results = []
                results.append(db_sync.sync_routes(storage, client).as_dict())
                cur = _d_from
                while cur <= _d_to:
                    ds = cur.isoformat()
                    self._SYNC_BG_STATE["progress"] = _fmt_date(ds)

                    def _sync_day(day=ds):
                        day_results = []
                        if _route:
                            for r in db_sync.sync_route_day(storage, client, _route, day):
                                day_results.append(r.as_dict())
                            day_results.append(db_sync.sync_waybills(
                                storage, client, _route, day, day).as_dict())
                            day_results.append(db_sync.sync_work_logs(
                                storage, day, day, _route).as_dict())
                        else:
                            for r in db_sync.sync_all_profiles(storage, client, day):
                                day_results.append(r.as_dict())
                        return day_results

                    day_results = db.retry_operation(_sync_day, max_retries=3, delay=3.0)
                    results.extend(day_results)
                    cur += _dt.timedelta(days=1)
                ok = not any(r.get("error") for r in results)
                _last_sync_at = time.monotonic()
                self._SYNC_BG_STATE["result"] = {"ok": ok, "from": _from_s, "to": _to_s,
                    "days": (_d_to - _d_from).days + 1, "results": results,
                    "lines": _sync_lines(results)}
            except Exception as exc:
                self._SYNC_BG_STATE["result"] = {"ok": False, "error": str(exc)}
            finally:
                self._SYNC_BG_STATE["running"] = False
                self._SYNC_BG_STATE["progress"] = ""

        threading.Thread(target=_bg, daemon=True).start()
        days = (d_to - d_from).days + 1
        return {"ok": True, "started": True, "from": from_str, "to": to_str, "days": days}

    def _sync_status(self) -> dict:
        """Oylik sync holatini qaytaradi."""
        st = self._SYNC_BG_STATE
        if st["running"]:
            return {"ok": True, "running": True, "progress": st["progress"]}
        if st["result"]:
            r = st["result"]
            return {"ok": True, "running": False, **r}
        return {"ok": True, "running": False}

    # ------------------------------------------------------------ settings

    def _get_settings(self) -> dict:
        """Barcha sozlamalarni qaytaradi (maxfiy qiymatlar yashirilgan)."""
        from ..config.settings import (
            get_config, telegram_settings, openrouter_settings,
            db_settings, backup_enabled, backup_db_settings,
            backup_interval_hours,
        )
        from ..core import bot_settings

        cfg = get_config()
        tg = telegram_settings()
        ai = openrouter_settings()
        db = db_settings()
        b_enabled = backup_enabled()
        b_db = backup_db_settings()
        b_host = ""
        if b_enabled and b_db.get("dsn", ""):
            dsn = b_db["dsn"]
            if "@" in dsn:
                b_host = dsn.split("@")[1].split("/")[0]

        def _mask(val: str) -> str:
            if not val:
                return ""
            if len(val) <= 6:
                return "***"
            return val[:3] + "*" * (len(val) - 6) + val[-3:]

        # Parse DB DSN to extract host/port/dbname/user
        db_info = {"host": "", "port": "", "dbname": "", "user": ""}
        dsn = db.get("dsn", "")
        if dsn:
            try:
                import urllib.parse as _up
                p = _up.urlparse(dsn)
                db_info["host"] = p.hostname or ""
                db_info["port"] = str(p.port or "")
                db_info["dbname"] = p.path.lstrip("/") or ""
                db_info["user"] = p.username or ""
            except Exception:
                pass

        return {
            "ok": True,
            "bm_api": {
                "base_url": cfg.base_url,
                "username": cfg.username,
                "password": _mask(cfg.password),
                "organization": cfg.organization,
            },
            "telegram": {
                "token": _mask(tg.get("token", "")),
                "chat_id": tg.get("chat_id", ""),
                "driver_chat_id": tg.get("driver_chat_id", ""),
                "admin_ids": [x.strip() for x in str(tg.get("admin_ids", "")).split(",") if x.strip()],
                "dispatcher_ids": [x.strip() for x in str(tg.get("dispatcher_ids", "")).split(",") if x.strip()],
                "manager_ids": [x.strip() for x in str(tg.get("manager_ids", "")).split(",") if x.strip()],
                "driver_ids": [x.strip() for x in str(tg.get("driver_ids", "")).split(",") if x.strip()],
                "default_role": tg.get("default_role", "viewer"),
            },
            "ai": {
                "api_key": _mask(ai.get("api_key", "")),
                "model": ai.get("model", ""),
                "referer": ai.get("referer", ""),
                "configured": bool(ai.get("api_key")),
            },
            "database": {
                "host": db_info["host"],
                "port": db_info["port"],
                "dbname": db_info["dbname"],
                "user": db_info["user"],
            },
            "backup": {
                "enabled": b_enabled,
                "host": b_host,
                "interval_hours": backup_interval_hours(),
            },
            "global": {
                "elec_price": bot_settings.elec_price(),
                "elec_kwh_per_km": bot_settings.ELEC_KWH_PER_KM,
                "brutto_skm": bot_settings.brutto_skm(),
                "audit": bot_settings.audit_log(10),
            },
        }

    def _update_settings(self, payload: dict) -> dict:
        """Sozlamalarni yangilaydi (faqat o'zgartrilishi mumkin bo'lganlari)."""
        from ..core import bot_settings

        changed = []
        ep = payload.get("elec_price")
        if ep is not None:
            try:
                val = float(ep)
                if not math.isfinite(val) or val < 0:
                    return {"ok": False, "error": "elec_price noto'g'ri qiymat"}
                bot_settings.set_elec_price(min(val, 100_000_000_000.0))
                changed.append("elec_price")
            except (ValueError, TypeError):
                return {"ok": False, "error": "elec_price noto'g'ri qiymat"}

        skm = payload.get("brutto_skm")
        if skm is not None:
            try:
                val = float(skm)
                if not math.isfinite(val) or val < 0:
                    return {"ok": False, "error": "brutto_skm noto'g'ri qiymat"}
                bot_settings.set_brutto_skm(min(val, 100_000_000_000_000.0))
                changed.append("brutto_skm")
            except (ValueError, TypeError):
                return {"ok": False, "error": "brutto_skm noto'g'ri qiymat"}

        rskm = payload.get("route_skm")
        if isinstance(rskm, dict):
            rid = str(rskm.get("route_id") or "").strip()
            if not rid:
                return {"ok": False, "error": "route_id kerak"}
            raw = rskm.get("value")
            if not isinstance(raw, (int, float)) or \
                    not math.isfinite(float(raw)) or float(raw) < 0:
                return {"ok": False, "error": "route_skm noto'g'ri qiymat"}
            bot_settings.set_route_skm(rid, float(raw))
            changed.append(f"route_skm:{rid}")

        return {"ok": True, "changed": changed}

    def _get_companies(self) -> dict:
        """Kompaniyalar ro'yxati va ularning credential holati."""
        from ..core.profiles import all_profiles

        def _mask(val: str) -> str:
            if not val:
                return ""
            if len(val) <= 4:
                return "***"
            return val[:2] + "*" * (len(val) - 4) + val[-2:]

        companies = []
        for p in all_profiles():
            has_creds = bool((p.get("username") or "").strip()
                             and (p.get("password") or "").strip())
            companies.append({
                "name": p.get("name", ""),
                "profileId": p.get("profileId", ""),
                "routeVariantId": p.get("routeVariantId", ""),
                "routeName": p.get("routeName", ""),
                "has_credentials": has_creds,
                "username": _mask(p.get("username", "")) if has_creds else "",
                "kmRate": p.get("kmRate", 0),
            })
        return {"ok": True, "companies": companies}

    def _update_company_creds(self, payload: dict) -> dict:
        """Kompaniya credential'larini yangilaydi."""
        from ..core.profiles import set_credentials

        name = (payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kiritilmagan"}
        username = (payload.get("username") or "").strip()
        password = (payload.get("password") or "").strip()
        if not username and not password:
            return {"ok": False, "error": "Username yoki password kiritilishi shart"}
        set_credentials(name, username, password)
        return {"ok": True, "name": name, "message": f"{name} credentiallari saqlandi"}

    # ------------------------------------------------- settings CRUD (env)

    _ENV_KEYS = {
        # key: (label, secret)
        "BM_BASE_URL": ("BM API manzil", False),
        "BM_USERNAME": ("BM API login", False),
        "BM_PASSWORD": ("BM API parol", True),
        "TG_BOT_TOKEN": ("Telegram bot token", True),
        "TG_CHAT_ID": ("Telegram chat ID", False),
        "TG_ADMIN_IDS": ("Admin chat ID'lar", False),
        "TG_DISPATCHER_IDS": ("Dispetcher chat ID'lar", False),
        "TG_MANAGER_IDS": ("Manager chat ID'lar", False),
        "TG_DRIVER_IDS": ("Haydovchi chat ID'lar", False),
        "OPENROUTER_API_KEY": ("AI (OpenRouter) kalit", True),
        "OPENROUTER_MODEL": ("AI model", False),
    }

    _ENV_PATH = Path(".env")

    @classmethod
    def _read_env(cls) -> dict[str, str]:
        """`.env`ni key->value xarita sifatida o'qiydi."""
        out: dict[str, str] = {}
        if not cls._ENV_PATH.exists():
            return out
        try:
            for line in cls._ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        except OSError:
            pass
        return out

    @classmethod
    def _write_env(cls, updates: dict[str, str]) -> None:
        """`.env`da faqat berilgan kalitlarni yangilaydi (qolganlari saqlanadi)."""
        lines: list[str] = []
        if cls._ENV_PATH.exists():
            lines = cls._ENV_PATH.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    out.append(f"{key}={updates[key]}")
                    seen.add(key)
                    continue
            out.append(line)
        for key, value in updates.items():
            if key not in seen:
                out.append(f"{key}={value}")
        from ..utils.io import atomic_write
        atomic_write(cls._ENV_PATH, "\n".join(out) + "\n")

    def _get_env_settings(self) -> dict:
        """`.env` asosidagi tahrirlanadigan sozlamalar (maxfiylar maskalanadi)."""
        env = self._read_env()
        fields = []
        for key, (label, secret) in self._ENV_KEYS.items():
            raw = env.get(key, "")
            fields.append({
                "key": key, "label": label, "secret": secret,
                "set": bool(raw),
                "value": "" if secret else raw,
            })
        return {"ok": True, "fields": fields}

    def _update_env_settings(self, payload: dict) -> dict:
        """`.env`ga sozlamalarni yozadi. Bo'sh qiymat = o'zgartirilmagan
        (maxfiy maydonlar uchun) yoki tozalangan (oddiy maydonlar)."""
        updates: dict[str, str] = {}
        values = payload.get("values") or {}
        if not isinstance(values, dict):
            return {"ok": False, "error": "values noto'g'ri"}
        for key, raw in values.items():
            if key not in self._ENV_KEYS:
                continue
            val = str(raw or "").strip()
            _, secret = self._ENV_KEYS[key]
            if not val:
                # Bo'sh qiymat: maxfiy maydon saqlanadi, oddiy maydon tozalanadi
                if secret:
                    continue
                updates[key] = ""
            else:
                updates[key] = val
        if not updates:
            return {"ok": True, "changed": [], "message": "O'zgartirish yo'q"}
        self._write_env(updates)
        return {"ok": True, "changed": list(updates),
                "message": "Saqlandi. O'zgarishlar jarayon qayta ishga tushganda kuchga kiradi."}

    def _company_action(self, payload: dict) -> dict:
        """Kompaniya amallari: add / update / delete / creds."""
        action = (payload.get("action") or "creds").strip()
        name = (payload.get("name") or "").strip()
        if action == "add":
            from ..core.profiles import add_profile
            try:
                p = add_profile(
                    name,
                    route_variant_id=str(payload.get("routeVariantId") or "").strip(),
                    route_name=str(payload.get("routeName") or "").strip(),
                    profile_id=str(payload.get("profileId") or "").strip())
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if (payload.get("username") or "").strip() or (payload.get("password") or "").strip():
                from ..core.profiles import set_credentials
                set_credentials(name,
                                str(payload.get("username") or "").strip(),
                                str(payload.get("password") or "").strip())
            return {"ok": True, "message": f"{p['name']} qo'shildi"}
        if action == "delete":
            from ..core.profiles import delete_profile
            if not name:
                return {"ok": False, "error": "Kompaniya nomi kiritilmagan"}
            if delete_profile(name):
                return {"ok": True, "message": f"{name} o'chirildi"}
            return {"ok": False, "error": f"{name} topilmadi"}
        if action == "update":
            from ..core.profiles import update_profile_fields
            try:
                update_profile_fields(name, {
                    "profileId": payload.get("profileId"),
                    "routeVariantId": payload.get("routeVariantId"),
                    "routeName": payload.get("routeName"),
                    "kmRate": payload.get("kmRate"),
                })
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "message": f"{name} yangilandi"}
        # default: creds
        return self._update_company_creds(payload)

    # ---------------------------------------------------------------- disk

    @staticmethod
    def _dir_size(path: Path, max_files: int = 20000) -> tuple[int, int]:
        """(bayt, fayl soni)."""
        total = 0
        files = 0
        if not path.exists():
            return 0, 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                        files += 1
                    except OSError:
                        continue
                    if files >= max_files:
                        break
        except OSError:
            pass
        return total, files

    @staticmethod
    def _db_disk_info() -> dict:
        """Baza hajmi + kunlik o'sish (baza yoshi bo'yicha) + haqiqiy tarix.

        Har chaqiruvda bugungi snapshot `state/disk_history.json`ga yoziladi
        (kuniga bir marta) — shu tarix 30 kunlik trend sifatida ko'rsatiladi.
        """
        size_bytes = 0
        size_pretty = "?"
        engine = ""
        db_age_days = 0
        try:
            from ..db.storage import get_storage
            st = get_storage()
            if st.enabled:
                engine = "postgres"
                row = st.query(
                    "SELECT pg_database_size(current_database()) b, "
                    "pg_size_pretty(pg_database_size(current_database())) p", limit=1)
                if row:
                    size_bytes = int(row[0].get("b") or 0)
                    size_pretty = str(row[0].get("p") or "?")
                # Baza yoshi: eng erta yozuv sanasi (route_daily).
                r0 = st.query("SELECT MIN(date) d0 FROM route_daily", limit=1)
                if r0 and r0[0].get("d0"):
                    try:
                        d0 = date.fromisoformat(str(r0[0]["d0"])[:10])
                        db_age_days = max((date.today() - d0).days, 1)
                    except ValueError:
                        pass
        except Exception as exc:  # noqa: BLE001
            log.debug("db hajmi olinmadi: %s", exc)

        growth = size_bytes / db_age_days if (size_bytes and db_age_days) else 0.0

        # Kuniga bir marta snapshot (haqiqiy tarix — trend shundan chiziladi).
        history: dict[str, dict] = {}
        hist_path = Path("state") / "disk_history.json"
        try:
            if hist_path.is_file():
                loaded = json.loads(hist_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    history = loaded
        except (OSError, ValueError):
            history = {}
        today = date.today().isoformat()
        return {"engine": engine, "size_bytes": size_bytes,
                "size_pretty": size_pretty, "db_age_days": db_age_days,
                "daily_growth_bytes": int(growth),
                "history": history, "history_path": hist_path, "today": today}

    @classmethod
    def _disk_usage(cls) -> dict:
        """Papka + DB hajmlari va 30 kunlik o'sish tendensiyasi."""
        folders = [
            ("reports/", "Generatsiya chiqishlari (30 kun TTL bilan tozalanadi)"),
            ("data/", "Haydovchi rasmlari va hujjatlari (arxiv, TTL yo'q)"),
            ("logs/", "Loglar (30 kun TTL + rotatsiya)"),
            ("backup/", "Qo'lda olingan SQL dump'lar (git'da yo'q)"),
            ("state/", "Bot holati, tokenlar, sozlamalar"),
        ]
        items = []
        total = 0
        for name, note in folders:
            b, f = cls._dir_size(Path(name))
            items.append({"name": name, "bytes": b, "files": f, "note": note})
            total += b
        db = cls._db_disk_info()
        # Bugungi snapshot'ni yozish (DB mavjud bo'lsa; atomik).
        history: dict = db.pop("history", {})
        hist_path: Path = db.pop("history_path")
        today: str = db.pop("today")
        if db.get("size_bytes"):
            history[today] = {"db_bytes": db.get("size_bytes", 0), "total_bytes": total}
        keep = sorted(history)[-60:]
        trimmed = {k: history[k] for k in keep}
        trend = [{"date": k, "week": k[5:], "bytes": v.get("db_bytes", 0)}
                 for k, v in sorted(trimmed.items())][-30:]
        try:
            from ..utils.io import atomic_write
            atomic_write(hist_path, json.dumps(trimmed, ensure_ascii=False, indent=1))
        except OSError:
            pass
        daily_growth = db.get("daily_growth_bytes") or 0
        return {
            "ok": True,
            "folders": items,
            "total_bytes": total,
            "total_note": f"+ DB {db.get('size_pretty', '?')} alohida (Docker volume)",
            "daily_growth_bytes": daily_growth,
            "db": db,
            "db_trend": trend,
        }

    # ------------------------------------------------------------------ log

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("dash %s | %s", self.address_string(), fmt % args)


def run(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True) -> int:
    """Server'ni ishga tushiradi (bloklanadi)."""
    global _DASHBOARD_TOKEN
    _DASHBOARD_TOKEN = (os.environ.get("DASHBOARD_TOKEN") or "").strip()
    # Eski sessiyalarni tiklash va birinchi ADMIN akkauntini yaratish.
    with _SESSION_LOCK:
        _load_sessions()
    _ensure_bootstrap_admin()
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    httpd.daemon_threads = True
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    if not _DASHBOARD_TOKEN and host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "DASHBOARD_TOKEN sozlanmagan, dashboard %s manzili tashqariga "
            "ochiq — haydovchi rasmlari va hujjatlari ham himoyasiz. "
            ".env faylida DASHBOARD_TOKEN o'rnatang.", url,
        )
    print(f"Dashboard: {url}")
    print("To'xtatish: Ctrl+C")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    # DB sxemasi va dastlabki xulosani FONDA tayyorlash — server zudlik bilan
    # tinglashni boshlaydi, pre-warm tugaguncha birinchi so'rov biroz sekin.
    import threading
    def _prewarm():
        try:
            from ..db.storage import get_storage
            get_storage().enabled
            from . import metrics as m
            m.summary()
            m.Metrics().system()
        except Exception:
            log.exception("dastlabki tayyorgarlik xatosi")
        # Auto-backup ni ishga tushirish (agar yoqilgan bo'lsa)
        try:
            from ..db.backup import start_auto_backup, backup_enabled
            if backup_enabled():
                start_auto_backup()
        except Exception:
            pass
    # GPS kuzatuv servisini ham fonda ishga tushirish (fail-safe: hech qanday
    # xato dashboard'ga xalaqit bermaydi; GPS_AUTOSTART=0 bilan o'chiriladi).
    try:
        from ..gps import autostart as _gps_autostart

        _gps_autostart.start_background()
    except Exception:
        log.exception("GPS autostart hook xatosi (dashboard davom etadi)")
    threading.Thread(target=_prewarm, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]
