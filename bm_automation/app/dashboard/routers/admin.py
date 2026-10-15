"""Admin router — settings, users, dispatchers, admin endpoints, SMS, documents."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response as FastResponse

from ..deps import Scope, force_route, scope_routes
from ...utils.logger import get_logger

log = get_logger("bm_automation.dashboard.admin")

router = APIRouter(tags=["admin"])


def _parse_qs(request: Request) -> dict:
    return dict(request.query_params)


def _admin_ym(params: dict) -> tuple[int, int]:
    today = date.today()
    try:
        year = int(params.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year
    try:
        month = int(params.get("month") or today.month)
    except (TypeError, ValueError):
        month = today.month
    if not 1 <= month <= 12:
        month = 1
    return year, month


# ---------------------------------------------------------------------------
# .env sozlamalari yordamchilari
# ---------------------------------------------------------------------------

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


def _mask_secret(val: str, keep: int = 6) -> str:
    if not val:
        return ""
    if len(val) <= keep:
        return "***"
    edge = keep // 2
    return val[:edge] + "*" * (len(val) - keep) + val[-edge:]


def _read_env() -> dict[str, str]:
    """`.env`ni key->value xarita sifatida o'qiydi."""
    out: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return out
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    except OSError:
        return {}
    return out


def _write_env(updates: dict[str, str]) -> None:
    """`.env`da faqat berilgan kalitlarni yangilaydi (qolganlari saqlanadi)."""
    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
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
    from ...utils.io import atomic_write
    atomic_write(_ENV_PATH, "\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@router.get("/api/settings")
async def get_settings(request: Request, scope: dict = Depends(Scope())):
    from ...config.settings import (
        get_config, telegram_settings, openrouter_settings,
        db_settings, backup_enabled, backup_db_settings, backup_interval_hours,
    )
    from ...core import bot_settings

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
        except Exception:  # noqa: BLE001
            pass

    def _split_ids(val) -> list[str]:
        return [x.strip() for x in str(val or "").split(",") if x.strip()]

    return {
        "ok": True,
        "bm_api": {
            "base_url": cfg.base_url,
            "username": cfg.username,
            "password": _mask_secret(cfg.password),
            "organization": cfg.organization,
        },
        "telegram": {
            "token": _mask_secret(tg.get("token", "")),
            "chat_id": tg.get("chat_id", ""),
            "driver_chat_id": tg.get("driver_chat_id", ""),
            "admin_ids": _split_ids(tg.get("admin_ids", "")),
            "dispatcher_ids": _split_ids(tg.get("dispatcher_ids", "")),
            "manager_ids": _split_ids(tg.get("manager_ids", "")),
            "driver_ids": _split_ids(tg.get("driver_ids", "")),
            "default_role": tg.get("default_role", "viewer"),
        },
        "ai": {
            "api_key": _mask_secret(ai.get("api_key", "")),
            "model": ai.get("model", ""),
            "referer": ai.get("referer", ""),
            "configured": bool(ai.get("api_key")),
        },
        "database": db_info,
        "backup": {
            "enabled": b_enabled,
            "host": b_host,
            "interval_hours": backup_interval_hours(),
        },
        "global": {
            "elec_price": bot_settings.elec_price(),
            "elec_kwh_per_km": bot_settings.ELEC_KWH_PER_KM,
            "brutto_skm": bot_settings.brutto_skm(),
            "show_driver_salary": bot_settings.show_driver_salary(),
            "auto_sync": bot_settings.auto_sync(),
            "auto_sync_interval_min": bot_settings.auto_sync_interval_min(),
            "audit": bot_settings.audit_log(10),
        },
    }


@router.post("/api/settings")
async def update_settings(request: Request, scope: dict = Depends(Scope())):
    import math
    from ...core import bot_settings

    payload = await request.json()
    changed: list[str] = []

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

    sds = payload.get("show_driver_salary")
    if sds is not None:
        if isinstance(sds, str):
            sds = str(sds).strip().lower() in ("1", "true", "yes", "on", "ha")
        bot_settings.set_show_driver_salary(bool(sds))
        changed.append("show_driver_salary")

    asy = payload.get("auto_sync")
    if asy is not None:
        if isinstance(asy, str):
            asy = str(asy).strip().lower() in ("1", "true", "yes", "on", "ha")
        bot_settings.set_auto_sync(bool(asy))
        changed.append("auto_sync")

    asi = payload.get("auto_sync_interval_min")
    if asi is not None:
        try:
            bot_settings.set_auto_sync_interval_min(int(asi))
            changed.append("auto_sync_interval_min")
        except (TypeError, ValueError):
            return {"ok": False, "error": "auto_sync_interval_min noto'g'ri qiymat"}

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


@router.get("/api/settings/env")
async def get_env_settings(request: Request, scope: dict = Depends(Scope())):
    env = _read_env()
    fields = []
    for key, (label, secret) in _ENV_KEYS.items():
        raw = env.get(key, "")
        fields.append({
            "key": key, "label": label, "secret": secret,
            "set": bool(raw),
            "value": "" if secret else raw,
        })
    return {"ok": True, "fields": fields}


# Kritikal maxfiy kalitlar — faqat SUPER ADMIN o'zgartira oladi
_CRITICAL_ENV_KEYS = frozenset((
    "BM_PASSWORD", "TG_BOT_TOKEN", "TG_CHAT_ID", "TG_ADMIN_IDS",
    "TG_DISPATCHER_IDS", "TG_MANAGER_IDS", "TG_DRIVER_IDS",
    "OPENROUTER_API_KEY", "ANDROID_SMS_GATEWAY_LOGIN",
    "ANDROID_SMS_GATEWAY_PASSWORD", "ANDROID_SMS_GATEWAY_URL",
    "SUPABASE_BACKUP_DSN", "DASHBOARD_TOKEN",
    "DASHBOARD_ADMIN_USER", "DASHBOARD_ADMIN_PASS",
))

def _is_super(scope: dict) -> bool:
    from ..deps import is_super_username
    return scope.get("is_admin") and is_super_username(scope.get("username") or "")


@router.post("/api/settings/env")
async def update_env_settings(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    updates: dict[str, str] = {}
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        return {"ok": False, "error": "values noto'g'ri"}

    is_super_admin = _is_super(scope)
    for key, raw in values.items():
        if key not in _ENV_KEYS:
            continue
        # Kritikal maxfiy kalitlar — faqat SUPER ADMIN
        if key in _CRITICAL_ENV_KEYS and not is_super_admin:
            log.warning("ENV o'zgartirish rad etildi (super admin emas): %s -> %s",
                        scope.get("username"), key)
            return {"ok": False, "error": f"{key} — faqat SUPER ADMIN o'zgartira oladi"}

        val = str(raw or "").strip()
        _, secret = _ENV_KEYS[key]
        if not val:
            # Bo'sh qiymat: maxfiy maydon saqlanadi, oddiy maydon tozalanadi
            if secret:
                continue
            updates[key] = ""
        else:
            updates[key] = val
    if not updates:
        return {"ok": True, "changed": [], "message": "O'zgartirish yo'q"}
    _write_env(updates)
    log.info("ENV yangilandi: %s -> %s", scope.get("username"), list(updates.keys()))
    return {"ok": True, "changed": list(updates),
            "message": "Saqlandi. O'zgarishlar jarayon qayta ishga tushganda kuchga kiradi."}


@router.get("/api/settings/companies")
async def get_companies(request: Request, scope: dict = Depends(Scope())):
    from ...core.profiles import all_profiles
    profiles = all_profiles()
    return {"ok": True, "companies": profiles}


@router.post("/api/settings/companies")
async def update_companies(request: Request, scope: dict = Depends(Scope())):
    from ...core.profiles import all_profiles, update_profile_fields, add_profile, remove_profile
    payload = await request.json()
    action = str(payload.get("action") or "creds").strip()

    if action == "add":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        try:
            add_profile(name, {
                "profileId": payload.get("profileId"),
                "routeVariantId": payload.get("routeVariantId"),
                "routeName": payload.get("routeName"),
                "kmRate": payload.get("kmRate"),
            })
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"{name} qo'shildi"}

    if action == "delete":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        try:
            remove_profile(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"{name} o'chirildi"}

    if action == "update":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
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
    return _update_company_creds(payload)


# ---------------------------------------------------------------------------
# SaaS: Company registry (Super Admin only)
# ---------------------------------------------------------------------------

def _require_super_admin(scope: dict) -> None:
    """Faqat Super Admin (role=ADMIN + super flag) uchun."""
    if not scope.get("is_admin"):
        raise HTTPException(403, "Ruxsat yo'q")
    from ..deps import is_super_username
    if not is_super_username(scope.get("username") or ""):
        raise HTTPException(403, "Faqat Super Admin")


@router.get("/api/admin/companies")
async def list_companies_admin(request: Request, scope: dict = Depends(Scope())):
    """Barcha kompaniyalar ro'yxati (Super Admin uchun)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    st = get_storage()
    companies = []
    if st.enabled:
        try:
            for r in st.query(
                "SELECT id, name, slug, logo, phone, email, address, "
                "subscription_plan, subscription_expire_date, status, "
                "max_users, max_drivers, max_routes, features, "
                "contact_telegram, contact_phone, notes, created_at, updated_at "
                "FROM companies ORDER BY id", limit=1000):
                companies.append(dict(r))
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "companies": companies}


@router.post("/api/admin/companies")
async def create_company(request: Request, scope: dict = Depends(Scope())):
    """Yangi kompaniya yaratish (Super Admin)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    slug = str(payload.get("slug") or "").strip().lower()
    if not name:
        return {"ok": False, "error": "Kompaniya nomi kerak"}
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    # slug unikalligi
    exist = st.query("SELECT 1 FROM companies WHERE slug = %s", (slug,), limit=1)
    if exist:
        return {"ok": False, "error": "Bu slug band"}
    from ...utils.io import now_iso
    now = now_iso()
    try:
        st.query(
            """INSERT INTO companies (name, slug, logo, phone, email, address,
                subscription_plan, subscription_expire_date, status,
                max_users, max_drivers, max_routes, features,
                contact_telegram, contact_phone, notes, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, slug, payload.get("logo") or "", payload.get("phone") or "",
             payload.get("email") or "", payload.get("address") or "",
             payload.get("subscription_plan") or "basic",
             payload.get("subscription_expire_date") or "",
             payload.get("status") or "active",
             int(payload.get("max_users") or 5),
             int(payload.get("max_drivers") or 50),
             int(payload.get("max_routes") or 10),
             json.dumps(payload.get("features") or {}),
             payload.get("contact_telegram") or "",
             payload.get("contact_phone") or "",
             payload.get("notes") or "",
             now, now))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": "Kompaniya yaratildi", "slug": slug}


@router.get("/api/admin/companies/{company_id}")
async def get_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya ma'lumotlari (Super Admin)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    row = st.query("SELECT * FROM companies WHERE id = %s", (company_id,), limit=1)
    if not row:
        raise HTTPException(404, "Kompaniya topilmadi")
    return {"ok": True, "company": dict(row[0])}


@router.put("/api/admin/companies/{company_id}")
async def update_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya ma'lumotlarini yangilash (Super Admin)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...utils.io import now_iso
    payload = await request.json()
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    allowed = {
        "name", "slug", "logo", "phone", "email", "address",
        "subscription_plan", "subscription_expire_date", "status",
        "max_users", "max_drivers", "max_routes", "features",
        "contact_telegram", "contact_phone", "notes"
    }
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        return {"ok": False, "error": "O'zgartirish yo'q"}
    # slug unikalligi
    if "slug" in updates:
        slug = str(updates["slug"]).strip().lower()
        exist = st.query("SELECT 1 FROM companies WHERE slug = %s AND id != %s", (slug, company_id), limit=1)
        if exist:
            return {"ok": False, "error": "Bu slug band"}
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [now_iso(), company_id]
    try:
        st.query(f"UPDATE companies SET {set_clause}, updated_at = %s WHERE id = %s", tuple(values))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": "Yangilandi"}


@router.delete("/api/admin/companies/{company_id}")
async def delete_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya o'chirish (Super Admin) — soft delete: status='deleted'."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...utils.io import now_iso
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    try:
        st.query("UPDATE companies SET status = 'deleted', updated_at = %s WHERE id = %s", (now_iso(), company_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": "O'chirildi (status=deleted)"}


@router.post("/api/admin/companies/{company_id}/activate")
async def activate_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniyani faollashtirish (obuna yo'qolganida ham)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...utils.io import now_iso
    payload = await request.json()
    days = int(payload.get("days") or 365)
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    from datetime import date, timedelta
    expire = (date.today() + timedelta(days=days)).isoformat()
    try:
        st.query(
            "UPDATE companies SET status = 'active', subscription_expire_date = %s, updated_at = %s WHERE id = %s",
            (expire, now_iso(), company_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": f"Faollashtirildi ({days} kun)", "expire_date": expire}


# ---------------------------------------------------------------------------
# SaaS: Company users (per-company admin)
# ---------------------------------------------------------------------------

@router.get("/api/admin/companies/{company_id}/users")
async def list_company_users(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya foydalanuvchilari ro'yxati."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    users = st.query(
        "SELECT id, username, role, company, active, created_at, data FROM dashboard_users WHERE company = (SELECT name FROM companies WHERE id = %s)", (company_id,), limit=200)
    return {"ok": True, "users": [dict(u) for u in users]}


@router.post("/api/admin/companies/{company_id}/users")
async def create_company_user(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniyaga yangi foydalanuvchi qo'shish."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ..deps import hash_password
    payload = await request.json()
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "VIEWER").strip().upper()
    if role not in ("VIEWER", "DISPATCHER", "MANAGER", "ADMIN", "DIRECTOR"):
        role = "VIEWER"
    if not username or not password:
        return {"ok": False, "error": "Login va parol kerak"}
    if len(password) < 4:
        return {"ok": False, "error": "Parol kamida 4 belgi"}
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    # company nomi
    comp = st.query("SELECT name FROM companies WHERE id = %s", (company_id,), limit=1)
    if not comp:
        return {"ok": False, "error": "Kompaniya topilmadi"}
    company_name = str(comp[0].get("name") or "")
    # max_users chek
    comp_info = st.query("SELECT max_users FROM companies WHERE id = %s", (company_id,), limit=1)
    max_u = int(comp_info[0].get("max_users") or 5) if comp_info else 5
    cur_count = st.query("SELECT COUNT(*) as c FROM dashboard_users WHERE company = %s", (company_name,), limit=1)
    if int(cur_count[0].get("c") or 0) >= max_u:
        return {"ok": False, "error": f"Maksimal foydalanuvchi soni ({max_u}) yetib qo'yilgan"}
    if st.dashboard_user_get(username=username):
        return {"ok": False, "error": "Bu login allaqachon mavjud"}
    digest, salt = hash_password(password)
    uid = st.dashboard_user_add(
        username=username, salt=salt, password_hash=digest,
        role=role, company=company_name, active=1)
    return {"ok": True, "id": uid, "message": "Foydalanuvchi qo'shildi"}


def _update_company_creds(payload: dict) -> dict:
    from ...core.profiles import update_profile_fields
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Kompaniya nomi kerak"}
    fields = {}
    for key in ("username", "password", "organization"):
        if key in payload:
            fields[key] = str(payload[key] or "").strip()
    if not fields:
        return {"ok": False, "error": "Hech qanday maydon berilmagan"}
    try:
        update_profile_fields(name, fields)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": f"{name} ma'lumotlari yangilandi"}


# ---------------------------------------------------------------------------
# Bot users / dispatchers
# ---------------------------------------------------------------------------

@router.get("/api/users")
async def get_users(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import get_users as _get_users, user_count
    users = _get_users()
    try:
        from ...db.storage import get_storage
        from ...notifications.ops.roles import resolve_role, role_label
        st = get_storage()
        if st.enabled:
            for u in users:
                try:
                    cid = int(u.get("chat_id"))
                except (TypeError, ValueError):
                    continue
                try:
                    prof = st.find_driver_by_telegram(cid) or {}
                except Exception:
                    prof = {}
                if not prof and str(u.get("phone") or "").strip():
                    try:
                        prof = st.find_driver_by_phone(
                            str(u["phone"]).lstrip("+").removeprefix("998")) or {}
                    except Exception:
                        prof = {}
                if prof:
                    base = st.find("drivers", external_id=str(prof.get("driver_id") or "")) or {}
                    u["driver_name"] = base.get("full_name") or str(prof.get("driver_id") or "")
                    u["driver_phone"] = str(prof.get("phone") or "")
                    u["driver_id"] = str(prof.get("driver_id") or "")
                    u["linked"] = True
                try:
                    u["resolved_role"] = role_label(resolve_role(cid))
                except Exception:
                    pass
    except Exception:
        pass
    return {"ok": True, "count": user_count(), "users": users}


@router.get("/api/users/{uid}")
async def get_user_detail(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import get_user
    user = get_user(uid)
    if user:
        return {"ok": True, "user": user}
    raise HTTPException(404, "Foydalanuvchi topilmadi")


_PHOTO_TTL = 6 * 3600
_PHOTO_NEG_TTL = 600
_PHOTO_CACHE: dict[int, tuple[float, str | None]] = {}


@router.get("/api/users/{uid}/photo")
def get_user_photo(uid: int, request: Request, scope: dict = Depends(Scope())):
    import time
    now = time.monotonic()
    hit = _PHOTO_CACHE.get(uid)
    ttl = _PHOTO_TTL if (hit and hit[1]) else _PHOTO_NEG_TTL
    if hit and (now - hit[0]) < ttl:
        url = hit[1]
    else:
        from ...core.bot_users import get_user
        from ...notifications.telegram import get_profile_photo_url
        user = get_user(uid) or {}
        url = get_profile_photo_url(uid, user.get("photo_file_id", ""))
        _PHOTO_CACHE[uid] = (now, url)
    if url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url)
    raise HTTPException(404, "Rasm topilmadi")


@router.get("/api/users/{uid}/driver-info")
async def get_user_driver_info(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import get_user
    from ...db.storage import get_storage
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
    return {"ok": True, "driver": driver_info}


@router.post("/api/users/{uid}/role")
async def set_user_role(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import set_role
    payload = await request.json()
    role = (payload.get("role") or "").strip()
    if not role:
        return {"ok": False, "error": "Rol ko'rsatilmagan"}
    return {"ok": set_role(uid, role)}


@router.post("/api/users/{uid}/delete")
async def delete_user(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import remove_user
    result = remove_user(uid)
    return {"ok": result, "message": "Foydalanuvchi faol edilmagan (o'chirildi)" if result else "Foydalanuvchi topilmadi"}


@router.post("/api/users/{uid}/link-driver")
async def link_driver(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    payload = await request.json()
    driver_id = str(payload.get("driver_id") or "").strip()
    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}
    if driver_id:
        storage.link_driver_telegram(driver_id, uid)
        return {"ok": True, "message": f"Haydovchi {driver_id} Telegram {uid} ga bog'landi"}
    storage.unlink_driver_telegram(uid)
    return {"ok": True, "message": "Bog'lanish olib tashlandi"}


@router.get("/api/dispatchers")
async def get_dispatchers(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import get_users
    from ...db.storage import get_storage
    from ...notifications.ops.roles import resolve_role, role_label
    storage = get_storage()
    db_enabled = storage.enabled
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
    return {"ok": True, "dispatchers": dispatchers}


@router.post("/api/dispatchers/{uid}/routes")
async def set_dispatcher_routes(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from .. import metrics as m
    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}
    payload = await request.json()
    routes = payload.get("routes") or []
    cleaned = []
    for r in routes:
        if isinstance(r, dict) and (r.get("route_id") or r.get("id")):
            cleaned.append({
                "route_id": str(r.get("route_id") or r.get("id") or ""),
                "route_name": str(r.get("route_name") or r.get("name") or ""),
                "company": str(r.get("company") or ""),
                "phone": str(r.get("phone") or ""),
            })
    opts = {str(o.get("id") or ""): o for o in m.route_options()}
    cleaned = [
        {"route_id": c["route_id"],
         "route_name": c["route_name"] or opts[c["route_id"]].get("route_name", ""),
         "company": c["company"] or opts[c["route_id"]].get("company", ""),
         "phone": c["phone"]}
        for c in cleaned if c["route_id"] in opts
    ]
    storage.set_dispatcher_routes(uid, cleaned)
    return {"ok": True, "routes": storage.dispatcher_routes(uid)}


@router.get("/api/group-stats")
async def get_group_stats(request: Request, scope: dict = Depends(Scope())):
    from ...core.group_stats import group_stats
    return {"ok": True, **group_stats()}


# ---------------------------------------------------------------------------
# Admin: salary, avans, fines, staff
# ---------------------------------------------------------------------------

@router.get("/api/admin/summary")
async def admin_summary(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import salary_summary
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    return {"ok": True, **salary_summary(year, month)}


@router.get("/api/admin/drivers")
async def admin_drivers(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import _drivers
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    return {"ok": True, "drivers": _drivers(year, month)}


@router.get("/api/admin/avans")
async def admin_avans(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import avans_data
    params = _parse_qs(request)
    return {"ok": True, **avans_data(
        get_storage(), month=params.get("month", ""),
        driver_id=params.get("driver_id", ""),
        route_id=params.get("route_id", ""))}


@router.post("/api/admin/avans")
async def admin_avans_add(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import avans_add
    payload = await request.json()
    res = avans_add(
        get_storage(),
        driver_id=str(payload.get("driver_id") or "").strip(),
        name=str(payload.get("name") or "").strip(),
        amount=payload.get("amount"),
        pay_date=str(payload.get("pay_date") or "").strip(),
        route_id=str(payload.get("route_id") or "").strip(),
        route_name=str(payload.get("route_name") or "").strip(),
        note=str(payload.get("note") or "").strip())
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "Xatolik"))
    return res


@router.post("/api/admin/avans/delete")
async def admin_avans_delete(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import avans_remove
    payload = await request.json()
    try:
        row_id = int(payload.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    res = avans_remove(get_storage(), row_id)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "Xatolik"))
    return res


@router.get("/api/admin/fines")
async def admin_fines(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import fines_data
    params = _parse_qs(request)
    return {"ok": True, **fines_data(
        get_storage(), month=params.get("month", ""),
        driver_id=params.get("driver_id", ""),
        route_id=params.get("route_id", ""))}


@router.post("/api/admin/fines/delete")
async def admin_fines_delete(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import fine_remove
    payload = await request.json()
    try:
        row_id = int(payload.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    res = fine_remove(get_storage(), row_id)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "Xatolik"))
    return res


@router.get("/api/admin/staff")
async def admin_staff(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ..admin_service import staff_summary, STAFF_POSITIONS
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    company = params.get("company", "")
    data = staff_summary(get_storage(), year, month)
    if company:
        data["staff"] = [s for s in data["staff"] if s["company"] == company]
    return {"ok": True, "positions": STAFF_POSITIONS, **data}


@router.post("/api/admin/staff")
async def admin_staff_save(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    payload = await request.json()
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

    if row_id is not None and str(row_id) not in ("", "0"):
        try:
            rid = int(row_id)
        except (TypeError, ValueError):
            rid = 0
        ok = st.staff_update(rid, name=name, position=position, company=company,
                             salary_type=salary_type, rate=rate, days=days, note=note)
        if not ok:
            raise HTTPException(400, "Xodim yangilanmadi")
        return {"ok": True, "id": rid}

    if not name:
        raise HTTPException(400, "Ism kiritilmagan")
    if salary_type not in ("oylik", "kunbay"):
        salary_type = "oylik"
    new_id = st.staff_add(name=name, position=position, company=company,
                          salary_type=salary_type, rate=rate, days=days, note=note)
    if not new_id:
        raise HTTPException(400, "Xodim saqlanmadi")
    return {"ok": True, "id": new_id}


@router.post("/api/admin/staff/delete")
async def admin_staff_delete(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    payload = await request.json()
    try:
        row_id = int(payload.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    res = {"ok": get_storage().delete_staff(row_id), "id": row_id}
    if not res["ok"]:
        raise HTTPException(400, "O'chirilmadi")
    return res


@router.get("/api/admin/staff/download")
async def admin_staff_download(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import staff_workbook
    params = _parse_qs(request)
    company = params.get("company", "")
    year, month = _admin_ym(params)
    data = staff_workbook(company, year, month)
    safe = re.sub(r"[^\w\-]+", "_", company) or "firma"
    name = f"xodimlar_{safe}_{year:04d}-{month:02d}.xlsx"
    return FastResponse(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/admin/download")
async def admin_download(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import salary_workbook
    params = _parse_qs(request)
    route_id = params.get("route_id", "")
    year, month = _admin_ym(params)
    if not route_id:
        raise HTTPException(400, "route_id kerak")
    data = salary_workbook(route_id, year, month)
    rname = re.sub(r"[^\w\-]+", "_", route_id)
    name = f"ish_haqi_{rname}_{year:04d}-{month:02d}.xlsx"
    return FastResponse(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/admin/download-all")
async def admin_download_all(request: Request, scope: dict = Depends(Scope())):
    import io
    import zipfile
    from ..admin_service import salary_all_workbook
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    res = salary_all_workbook(year, month)
    files = [f for f in res["files"] if f["data"]]
    if not files:
        raise HTTPException(404, "Ma'lumot topilmadi")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f["filename"], f["data"])
    name = f"oylik_ish_haqi_{year:04d}-{month:02d}.zip"
    return FastResponse(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# SMS
# ---------------------------------------------------------------------------

@router.get("/api/sms-log")
async def sms_log_api(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.sms_notify import sms_log
    params = _parse_qs(request)
    try:
        limit = int(params.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    status = params.get("status", "").strip().upper()
    data = sms_log(limit=min(max(limit, 1), 1000), status=status)
    scopes = scope_routes(scope)
    if scopes is not None:
        data["rows"] = [r for r in data["rows"] if str(r.get("route_id") or "") in scopes]
        data["count"] = len(data["rows"])
        counts: dict[str, int] = {}
        for r in data["rows"]:
            st = (r.get("status") or "UNKNOWN").upper()
            counts[st] = counts.get(st, 0) + 1
        counts["total"] = data["count"]
        data["counts"] = counts
    return {"ok": True, **data}


@router.get("/api/sms/routes")
async def sms_routes(request: Request, scope: dict = Depends(Scope())):
    from ...core.profiles import all_profiles
    from ...notifications.sms_notify import sms_route_list
    profiles = all_profiles()
    seen = {}
    for p in profiles:
        rid = str(p.get("routeVariantId") or "").strip()
        if rid and rid not in seen:
            seen[rid] = str(p.get("routeName") or p.get("name") or "") or rid
    scopes = scope_routes(scope)
    if scopes is not None:
        seen = {rid: name for rid, name in seen.items() if rid in scopes}
    return {"ok": True, "routes": sms_route_list(routes=list(seen), route_names=seen)}


@router.post("/api/sms/retry")
async def sms_retry(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.sms_notify import retry_failed_sms
    payload = await request.json()
    try:
        limit = int(payload.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    result = retry_failed_sms(limit=min(max(limit, 1), 200))
    return {"ok": True, **result}


@router.post("/api/sms/send")
async def sms_send(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.sms_notify import send_and_log
    payload = await request.json()
    phone = str(payload.get("phone") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not phone or not text:
        raise HTTPException(400, "Telefon va matn kiritilishi kerak")
    result = send_and_log(phone=phone, text=text[:500],
                          name=str(payload.get("name") or phone))
    return {"ok": True, **result}


@router.post("/api/sms/routes")
async def sms_route_set(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.sms_notify import sms_route_set as _set
    payload = await request.json()
    route_id = str(payload.get("route_id") or "").strip()
    enabled = payload.get("enabled")
    if not route_id and enabled is None:
        raise HTTPException(400, "route_id yoki enabled kerak")
    scopes = scope_routes(scope)
    if scopes is not None and route_id and route_id not in scopes:
        raise HTTPException(404, "Yo'nalish topilmadi")
    result = _set(route_id=route_id, enabled=enabled if enabled is not None else None)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Documents (hujjatlar bo'limi)
# ---------------------------------------------------------------------------

@router.get("/api/documents")
async def documents_list(request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    params = _parse_qs(request)
    mgr = get_manager()
    docs = mgr.list_documents(
        category=params.get("category", ""),
        status=params.get("status", ""),
        search=params.get("q", ""),
        driver_id=params.get("driver_id", ""))
    scopes = scope_routes(scope)
    if scopes is not None:
        storage = get_storage()
        docs = [d for d in docs if _doc_in_scope(storage, str(d.get("driver_id") or ""), scopes)]
    for d in docs:
        if "created_at" in d:
            d["created_at"] = str(d["created_at"])
        if "updated_at" in d:
            d["updated_at"] = str(d["updated_at"])
    return {"ok": True, "documents": docs}


@router.get("/api/documents/templates")
async def documents_templates(request: Request, scope: dict = Depends(Scope())):
    from ...documents.templates import list_templates, CATEGORIES
    return {"ok": True, "templates": list_templates(), "categories": dict(CATEGORIES)}


@router.get("/api/documents/autofill")
async def documents_autofill(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    params = _parse_qs(request)
    driver_id = params.get("driver_id", "")
    if driver_id:
        scopes = scope_routes(scope)
        if scopes is not None:
            if not _doc_in_scope(get_storage(), driver_id, scopes):
                raise HTTPException(404, "haydovchi topilmadi")
    from ...documents.manager import get_manager
    return get_manager().autofill_data(driver_id)


@router.get("/api/documents/{doc_id}")
async def document_detail(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    doc = get_manager().get_document(doc_id)
    if doc:
        scopes = scope_routes(scope)
        if scopes is not None:
            if not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
                doc = None
    if not doc:
        raise HTTPException(404, "Xujjat topilmadi")
    if "created_at" in doc:
        doc["created_at"] = str(doc["created_at"])
    if "updated_at" in doc:
        doc["updated_at"] = str(doc["updated_at"])
    return {"ok": True, "document": doc}


@router.get("/api/documents/{doc_id}/html")
async def document_html(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        doc = get_manager().get_document(doc_id)
        if not doc or not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
            raise HTTPException(404, "Xujjat topilmadi")
    html_content = get_manager().get_html(doc_id)
    if not html_content:
        raise HTTPException(404, "Xujjat topilmadi")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html_content)


@router.post("/api/documents")
async def document_create(request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    payload = await request.json()
    scopes = scope_routes(scope)
    if scopes is not None and payload.get("driver_id"):
        if not _doc_in_scope(get_storage(), str(payload.get("driver_id") or ""), scopes):
            raise HTTPException(404, "haydovchi topilmadi")
    result = get_manager().create_document(
        template_key=payload.get("template_key", ""),
        fields=payload.get("fields", {}),
        title=payload.get("title", ""),
        status=payload.get("status", "draft"),
        driver_id=payload.get("driver_id", ""),
    )
    return result


@router.post("/api/documents/ai-generate")
async def document_ai_generate(request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    payload = await request.json()
    return get_manager().ai_generate(
        template_key=payload.get("template_key", ""),
        context=payload.get("context", {}),
        custom_prompt=payload.get("prompt", ""),
    )


@router.post("/api/documents/{doc_id}")
async def document_update(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        doc = get_manager().get_document(doc_id)
        if not doc or not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
            raise HTTPException(404, "Xujjat topilmadi")
    payload = await request.json()
    if scopes is not None and payload.get("driver_id"):
        if not _doc_in_scope(get_storage(), str(payload.get("driver_id") or ""), scopes):
            raise HTTPException(404, "haydovchi topilmadi")
    return get_manager().update_document(
        doc_id=doc_id,
        fields=payload.get("fields"),
        title=payload.get("title"),
        status=payload.get("status"),
        driver_id=payload.get("driver_id"),
    )


@router.post("/api/documents/{doc_id}/delete")
async def document_delete(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        doc = get_manager().get_document(doc_id)
        if not doc or not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
            raise HTTPException(404, "Xujjat topilmadi")
    return get_manager().delete_document(doc_id)


@router.post("/api/documents/{doc_id}/attach")
async def document_attach(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        doc = get_manager().get_document(doc_id)
        if not doc or not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
            raise HTTPException(404, "Xujjat topilmadi")
    payload = await request.json()
    return get_manager().attach_file(doc_id, payload.get("file_path", ""))


@router.post("/api/documents/{doc_id}/detach")
async def document_detach(doc_id: int, request: Request, scope: dict = Depends(Scope())):
    from ...documents.manager import get_manager
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        doc = get_manager().get_document(doc_id)
        if not doc or not _doc_in_scope(get_storage(), str(doc.get("driver_id") or ""), scopes):
            raise HTTPException(404, "Xujjat topilmadi")
    payload = await request.json()
    return get_manager().detach_file(doc_id, payload.get("file_path", ""))


def _doc_in_scope(storage, driver_id: str, scopes: set) -> bool:
    if not driver_id:
        return False
    row = storage.find("drivers", external_id=driver_id)
    return bool(row and str(row.get("route_id") or "") in scopes)


# =============================================================================
# Video Kuzatuv — ruxsatli tokenlar, audit, arxiv cheklovlari
# =============================================================================

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

# Token imzosi uchun maxfiy kalit (production'da .env dan olinadi)
_VIDEO_TOKEN_SECRET = os.environ.get("VIDEO_TOKEN_SECRET", "").encode()
if not _VIDEO_TOKEN_SECRET:
    # Dev fallback — productionda shart emas
    _VIDEO_TOKEN_SECRET = secrets.token_bytes(32)

# Ruxsat matritsasi
_VIDEO_ROLE_PERMS = {
    "VIEWER": set(),
    "DISPATCHER": {"live", "archive"},
    "MANAGER": {"live", "archive"},
    "DIRECTOR": {"live", "archive", "export"},
    "ADMIN": {"live", "archive", "export"},
}

# Dispetcher arxivi uchun maksimal kunlar (sozlanadigan)
_VIDEO_ARCHIVE_MAX_DAYS = int(os.environ.get("VIDEO_ARCHIVE_MAX_DAYS", "7"))


def _video_perms_for_role(role: str) -> set:
    return _VIDEO_ROLE_PERMS.get(role.upper(), set())


def _has_video_perm(scope: dict, perm: str) -> bool:
    """Foydalanuvchida video ruxsati bormi?"""
    if scope.get("is_admin"):
        return True
    role = (scope.get("role") or "VIEWER").upper()
    return perm in _video_perms_for_role(role)


def _sign_video_token(camera_id: int, user_id: int, action: str, ttl_s: int = 300) -> str:
    """Video uchun imzoli token yaratadi (1-5 daqiqa)."""
    payload = f"{camera_id}|{user_id}|{action}|{int(time.time()) + ttl_s}"
    sig = hmac.new(_VIDEO_TOKEN_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_video_token(token: str) -> tuple[int, int, str] | None:
    """Token ni tekshiradi va (camera_id, user_id, action) qaytaradi."""
    try:
        parts = token.split("|")
        if len(parts) != 5:
            return None
        camera_id = int(parts[0])
        user_id = int(parts[1])
        action = parts[2]
        exp = int(parts[3])
        sig = parts[4]
        if time.time() > exp:
            return None
        payload = "|".join(parts[:4])
        expected = hmac.new(_VIDEO_TOKEN_SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return camera_id, user_id, action
    except Exception:
        return None


async def _log_video_audit(storage, camera_id: int, scope: dict, action: str, duration_s: int = 0):
    """Video ko'rishni audit jadvaliga yozadi."""
    try:
        storage.db.execute(
            """INSERT INTO video_audit (camera_id, user_id, username, action, ip, duration_s, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())""",
            (camera_id, scope.get("user_id") or 0, scope.get("username") or "",
             action, "", duration_s)
        )
    except Exception:
        pass  # audit xatosi asosiy so'rovni buzmasin


@router.get("/api/video/cameras")
async def video_cameras_list(request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Ruxsatli kameralar ro'yxati (URL parollarisiz)."""
    from ...db.storage import get_storage
    storage = get_storage()
    scopes = scope_routes(scope)

    # Kameralar jadvalini o'qiyamiz (company_id bilan filtrlangan)
    # Dispatcher/MANAGER uchun faqat o'z yo'nalishlaridagi avtobus kameralari
    query = """SELECT id, name, source_type, vehicle_id, enabled, company_id
               FROM video_cameras WHERE enabled = 1"""
    params: tuple = ()

    if scopes is not None:
        # Faqat o'z kompaniyasining kameralari
        company_id = scope.get("company_id") or 0
        query += " AND company_id = %s"
        params = (company_id,)
        # Va faqat yo'nalishlarga tegishli avtobuslar
        if scopes:
            ph = ", ".join(storage.db.ph for _ in scopes)
            query += f" AND vehicle_id IN (SELECT external_id FROM vehicles WHERE route_id IN ({ph}))"
            params = (company_id,) + tuple(scopes)

    query += " ORDER BY id"
    cams = storage.db.query(query, params)

    # URL va boshqa maxfiy maydonlarni chiqarib yuboramiz
    for c in cams:
        c.pop("url", None)
        c.pop("company_id", None)

    return {"ok": True, "cameras": cams}


@router.get("/api/video/cameras/{cam_id}/token")
async def video_camera_token(cam_id: int, request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Kamera uchun imzoli token beradi (live/archive/export)."""
    action = request.query_params.get("action", "live")
    if action not in ("live", "archive", "export"):
        raise HTTPException(400, "Noto'g'ri action")

    if not _has_video_perm(scope, action):
        raise HTTPException(403, "Sizda bu video harakati uchun ruxsat yo'q")

    from ...db.storage import get_storage
    storage = get_storage()
    scopes = scope_routes(scope)

    # Kameraning kompaniya/yo'nalishga tegishliligi tekshiriladi
    cam = storage.db.query(
        "SELECT id, vehicle_id, company_id FROM video_cameras WHERE id = %s",
        (cam_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")
    cam = cam[0]

    if scopes is not None:
        company_id = scope.get("company_id") or 0
        if int(cam.get("company_id") or 0) != company_id:
            raise HTTPException(403, "Kamera sizga tegishli emas")
        vehicle_id = cam.get("vehicle_id")
        if vehicle_id:
            # Avtobus yo'nalishi foydalanuvchi qamrovida yo'q
            route_row = storage.db.query(
                "SELECT route_id FROM vehicles WHERE external_id = %s",
                (vehicle_id,), limit=1)
            if route_row and str(route_row[0].get("route_id") or "") not in scopes:
                raise HTTPException(403, "Kamera sizga tegishli emas")

    # Arxiv uchun dispetcher 7 kun cheklovi
    if action == "archive" and scope.get("role") == "DISPATCHER":
        max_days = _VIDEO_ARCHIVE_MAX_DAYS
        cutoff = (datetime.now() - timedelta(days=max_days)).isoformat()
        # Token payloadiga cutoff qo'shamiz (keyinroq relay/hls da tekshiriladi)
        token = _sign_video_token(cam_id, scope.get("user_id") or 0, f"archive:{cutoff}", 300)
    else:
        token = _sign_video_token(cam_id, scope.get("user_id") or 0, action, 300)

    await _log_video_audit(storage, cam_id, scope, action)
    return {"ok": True, "token": token, "ttl_s": 300}


@router.get("/api/video/archive/range")
async def video_archive_range(request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Arxiv uchun mavjud sana oralig'ini qaytaradi (dispetcher uchun cheklangan)."""
    cam_id = int(request.query_params.get("camera_id") or 0)
    if not cam_id:
        raise HTTPException(400, "camera_id kerak")

    if not _has_video_perm(scope, "archive"):
        raise HTTPException(403, "Arxiv ruxsati yo'q")

    from ...db.storage import get_storage
    storage = get_storage()
    scopes = scope_routes(scope)

    cam = storage.db.query(
        "SELECT id, company_id FROM video_cameras WHERE id = %s",
        (cam_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")
    cam = cam[0]

    if scopes is not None:
        company_id = scope.get("company_id") or 0
        if int(cam.get("company_id") or 0) != company_id:
            raise HTTPException(403, "Kamera sizga tegishli emas")

    # Dispetcher uchun 7 kun cheklovi
    if scope.get("role") == "DISPATCHER":
        max_days = _VIDEO_ARCHIVE_MAX_DAYS
        end = datetime.now()
        start = end - timedelta(days=max_days)
    else:
        # Admin/Director/Manager uchun cheklov yo'q (yoki konfiguratsiyaga qarab)
        end = datetime.now()
        start = end - timedelta(days=30)

    await _log_video_audit(storage, cam_id, scope, "archive_range")
    return {
        "ok": True,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "max_days": (end - start).days
    }


@router.post("/api/video/cameras")
async def video_camera_create(request: Request, scope: dict = Depends(Scope(min_role=4))):
    """Yangi kamera qo'shish (faqat ADMIN)."""
    from ...db.storage import get_storage
    storage = get_storage()
    payload = await request.json()

    name = str(payload.get("name") or "").strip()
    url = str(payload.get("url") or "").strip()
    source_type = str(payload.get("source_type") or "hls").strip()
    vehicle_id = str(payload.get("vehicle_id") or "").strip()

    if not name or not url:
        raise HTTPException(400, "name va url kerak")

    company_id = scope.get("company_id") or 0
    storage.db.execute(
        """INSERT INTO video_cameras (name, source_type, url, vehicle_id, company_id, enabled, created_at)
           VALUES (%s, %s, %s, %s, %s, 1, now())""",
        (name, source_type, url, vehicle_id, company_id)
    )
    rows = storage.db.query("SELECT MAX(id) AS id FROM video_cameras", limit=1)
    cam_id = int(rows[0]["id"]) if rows else 0

    return {"ok": True, "id": cam_id}


@router.patch("/api/video/cameras/{cam_id}")
async def video_camera_update(cam_id: int, request: Request, scope: dict = Depends(Scope(min_role=4))):
    """Kamera tahrirlash (faqat ADMIN)."""
    from ...db.storage import get_storage
    storage = get_storage()
    payload = await request.json()

    cam = storage.db.query("SELECT id FROM video_cameras WHERE id = %s", (cam_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")

    fields, params = [], []
    for key in ("name", "source_type", "url", "vehicle_id", "enabled"):
        if key in payload:
            fields.append(f"{key} = %s")
            params.append(payload[key])
    if fields:
        params.append(cam_id)
        storage.db.execute(f"UPDATE video_cameras SET {', '.join(fields)} WHERE id = %s", tuple(params))

    return {"ok": True}


@router.delete("/api/video/cameras/{cam_id}")
async def video_camera_delete(cam_id: int, request: Request, scope: dict = Depends(Scope(min_role=4))):
    """Kamera o'chirish (faqat ADMIN)."""
    from ...db.storage import get_storage
    storage = get_storage()

    storage.db.execute("DELETE FROM video_cameras WHERE id = %s", (cam_id,))
    return {"ok": True}


# =============================================================================
# Video Setka (Grid) — bitta kanal bo'yicha barcha avtobuslar
# =============================================================================

@router.get("/api/video/channels")
async def video_channels_list(request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Foydalanuvchi uchun mavjud kamera kanallar ro'yxati."""
    if not _has_video_perm(scope, "live"):
        raise HTTPException(403, "Video ko'rish ruxsati yo'q")

    # Standart 8 kanal (JT_CAMERA_CHANNELS), lekin keyinroq kameralar jadvalidan aniqlanishi mumkin
    max_channels = 8
    return {"ok": True, "channels": list(range(1, max_channels + 1))}


@router.get("/api/video/grid")
async def video_grid(
    request: Request,
    channel: int = 1,
    page: int = 1,
    page_size: int = 25,
    scope: dict = Depends(Scope(min_role=1))
):
    """Video setka uchun ma'lumotlar: avtobuslar va har biri uchun oqim tokeni."""
    if not _has_video_perm(scope, "live"):
        raise HTTPException(403, "Video ko'rish ruxsati yo'q")

    from ...db.storage import get_storage
    from ...gps import storage as gps_storage
    storage = get_storage()
    scopes = scope_routes(scope)

    if channel < 1:
        channel = 1

    # Kameralar jadvalidan shu kanalda oqim bor kameralarni topamiz
    # Hozircha video_cameras jadvalidan vehicle_id orqali bog'laymiz
    query = """SELECT c.id, c.name, c.source_type, c.vehicle_id, c.company_id,
                      v.plate_number, v.route_id, r.name as route_name
               FROM video_cameras c
               LEFT JOIN vehicles v ON v.external_id = c.vehicle_id
               LEFT JOIN routes r ON r.external_id = v.route_id
               WHERE c.enabled = 1"""
    params: list = []

    if scopes is not None:
        company_id = scope.get("company_id") or 0
        query += " AND c.company_id = %s"
        params.append(company_id)
        if scopes:
            ph = ", ".join(storage.db.ph for _ in scopes)
            query += f" AND v.route_id IN ({ph})"
            params.extend(scopes)

    query += " ORDER BY v.plate_number, c.id"
    cams = storage.db.query(query, tuple(params))

    # Har bir kamera uchun token yaratamiz
    result = []
    for cam in cams:
        cam_id = cam.get("id")
        if not cam_id:
            continue
        # Imzoli token yaratish (5 daqiqa)
        token_data = _sign_video_token(cam_id, scope.get("user_id") or 0, "grid", 300)
        result.append({
            "camera_id": cam_id,
            "name": cam.get("name"),
            "source_type": cam.get("source_type"),
            "vehicle_id": cam.get("vehicle_id"),
            "plate_number": cam.get("plate_number"),
            "route_id": cam.get("route_id"),
            "route_name": cam.get("route_name"),
            "token": token_data,
            "channel": channel,
            "status": "online",  # keyinroq health check bilan aniqlanadi
        })

    # Sahifalash
    total = len(result)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = result[start:end]

    return {
        "ok": True,
        "channel": channel,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": page_items,
    }


@router.post("/api/video/token/refresh")
async def video_token_refresh(request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Token muddati tugagandan oldin yangilash."""
    if not _has_video_perm(scope, "live"):
        raise HTTPException(403, "Video ko'rish ruxsati yo'q")

    payload = await request.json()
    camera_id = int(payload.get("camera_id") or 0)
    action = payload.get("action", "grid")

    if not camera_id:
        raise HTTPException(400, "camera_id kerak")

    # Kamerani tekshirish (scope da bo'lgani)
    from ...db.storage import get_storage
    storage = get_storage()
    scopes = scope_routes(scope)

    cam = storage.db.query(
        "SELECT id, vehicle_id, company_id FROM video_cameras WHERE id = %s",
        (camera_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")
    cam = cam[0]

    if scopes is not None:
        company_id = scope.get("company_id") or 0
        if int(cam.get("company_id") or 0) != company_id:
            raise HTTPException(403, "Kamera sizga tegishli emas")

    token = _sign_video_token(camera_id, scope.get("user_id") or 0, action, 300)

    from ...gps import storage as gps_storage
    await _log_video_audit(gps_storage, camera_id, scope, f"token_refresh:{action}")

    return {"ok": True, "token": token, "ttl_s": 300}
