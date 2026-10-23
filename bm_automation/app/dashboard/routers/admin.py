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
def get_settings(request: Request, scope: dict = Depends(Scope())):
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
            "departure_offset_min": bot_settings.departure_offset_min(),
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

    doff = payload.get("departure_offset_min")
    if doff is not None:
        try:
            val = float(doff)
        except (TypeError, ValueError):
            return {"ok": False, "error": "departure_offset_min noto'g'ri qiymat"}
        if not math.isfinite(val):
            return {"ok": False, "error": "departure_offset_min noto'g'ri qiymat"}
        bot_settings.set_departure_offset_min(int(val))
        changed.append("departure_offset_min")

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
def get_env_settings(request: Request, scope: dict = Depends(Scope())):
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
def get_companies(request: Request, scope: dict = Depends(Scope())):
    from ...core.profiles import grouped_profiles, company_group_ids
    out = []
    for c in grouped_profiles():
        c["groupChatIds"] = company_group_ids(c.get("name"))
        out.append(c)
    return {"ok": True, "companies": out}


@router.post("/api/settings/companies")
async def update_companies(request: Request, scope: dict = Depends(Scope())):
    from ...core.profiles import (add_profile, add_route_profile,
                                  update_profile_fields, delete_profile,
                                  get_profile, set_credentials)
    payload = await request.json()
    action = str(payload.get("action") or "creds").strip()

    if action == "add":
        name = str(payload.get("name") or "").strip()
        route = str(payload.get("routeVariantId") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        existing = get_profile(name)
        if existing and not route:
            return {"ok": False, "error": "Yangi yo'nalish uchun routeVariantId kerak"}
        try:
            if existing:
                add_route_profile(
                    name, route_variant_id=route,
                    route_name=payload.get("routeName") or "",
                    profile_id=payload.get("profileId") or "")
            else:
                add_profile(
                    name, route_variant_id=route,
                    route_name=payload.get("routeName") or "",
                    profile_id=payload.get("profileId") or "")
            if payload.get("username") or payload.get("password"):
                set_credentials(name, payload.get("username") or "",
                                payload.get("password") or "")
            if "groupChatIds" in payload or "groups" in payload:
                from ...core.profiles import set_company_group_ids
                set_company_group_ids(
                    name, _onb_group_ids(payload.get("groupChatIds",
                                                    payload.get("groups"))))
            starts = {k: str(payload.get(k) or "").strip()
                      for k in ("start1", "start2")
                      if str(payload.get(k) or "").strip()}
            if starts:
                update_profile_fields(name, starts, route_id=route)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        warnings: list[str] = []
        _onb_autofill_profile(name, [], warnings, route_id=route)
        verb = "yangi yo'nalish qo'shildi" if existing else "qo'shildi"
        return {"ok": True, "message": f"{name}: {verb}",
                "warnings": warnings}

    if action == "groups":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        if not get_profile(name):
            return {"ok": False, "error": f"Kompaniya topilmadi: {name}"}
        from ...core.profiles import set_company_group_ids
        try:
            ids = set_company_group_ids(
                name, _onb_group_ids(payload.get("groupChatIds",
                                                payload.get("groups"))))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True,
                "message": f"{name}: {len(ids)} guruh bog'landi"
                           if ids else f"{name}: guruhlar tozalandi",
                "groupChatIds": ids}

    if action == "delete":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        if not delete_profile(name):
            return {"ok": False, "error": f"Kompaniya topilmadi: {name}"}
        return {"ok": True, "message": f"{name} o'chirildi"}

    if action == "update":
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Kompaniya nomi kerak"}
        match = str(payload.get("matchRouteVariantId")
                    or payload.get("oldRouteVariantId") or "").strip()
        old = get_profile(name, route_id=match) or {}
        old_rid = str(old.get("routeVariantId") or "").strip()
        if match and old_rid.lower() != match.lower():
            return {"ok": False, "error": f"Yo'nalish topilmadi: {match}"}
        new_rid = payload.get("routeVariantId")
        new_rid_s = str(new_rid or "").strip()
        route_changed = (new_rid is not None
                         and new_rid_s != old_rid)
        if new_rid_s and new_rid_s.lower() != old_rid.lower():
            from ...core.profiles import all_profiles
            if any(str(p.get("routeVariantId") or "").strip().lower()
                   == new_rid_s.lower() for p in all_profiles()):
                return {"ok": False,
                        "error": f"Bu yo'nalish allaqachon bog'langan: {new_rid_s}"}
        fields = {
            "profileId": payload.get("profileId"),
            "routeVariantId": new_rid,
            "kmRate": payload.get("kmRate"),
        }
        if "routeName" in payload:
            fields["routeName"] = payload.get("routeName")
        elif route_changed:
            # Eski yo'nalish nomi yangi yo'nalishga qolmasin — BM'dan
            # aniqlash yoki qo'lda kiritish uchun bo'sh qoldiriladi.
            fields["routeName"] = ""
        for key in ("start1", "start2"):
            if key in payload:
                fields[key] = payload.get(key)
            elif route_changed:
                # Eski yo'nalishning konechkasi yangi yo'nalishga qolmasin.
                fields[key] = ""
        try:
            update_profile_fields(name, fields, route_id=match or old_rid)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        warnings: list[str] = []
        _onb_autofill_profile(name, [], warnings,
                              route_id=new_rid_s or old_rid)
        return {"ok": True, "message": f"{name} yangilandi",
                "warnings": warnings}

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
def list_companies_admin(request: Request, scope: dict = Depends(Scope())):
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


@router.post("/api/admin/companies/detect")
async def detect_company_fields(request: Request, scope: dict = Depends(Scope())):
    """BM waybill'dan rasmiy yo'nalish nomi va konechkalarni aniqlaydi.

    Hech narsani saqlamaydi — tahrirlash formasini to'ldirish uchun
    qaytaradi, admin tekshirib o'zi saqlaydi. Mavjud kompaniya uchun `name`,
    yangi kompaniya uchun esa `routeVariantId` (va ixtiyoriy BM
    kredensiallari) yuboriladi.
    """
    _require_super_admin(scope)
    from ...core.profiles import get_profile
    from ...services.driver_sheet_service import detect_profile_fields

    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    profile = dict(get_profile(name) or {}) if name else {}
    for key, field in (("routeVariantId", "routeVariantId"),
                       ("username", "bmUsername"),
                       ("password", "bmPassword")):
        value = str(payload.get(field) or "").strip()
        if value:
            profile[key] = value
    if not str(profile.get("routeVariantId") or "").strip():
        return {"ok": False, "error": "routeVariantId kerak"}
    try:
        found = detect_profile_fields(profile)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"BM'dan aniqlanmadi: {exc}"}
    if not found:
        return {"ok": False, "error": "Waybill topilmadi — nomlarni qo'lda kiriting"}
    return {"ok": True, **found}


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
        from ...db.tenancy import slugify
        slug = slugify(name)
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    # slug unikalligi (ph — postgres `%s` / test sqlite `?`)
    ph = st.db.ph
    exist = st.query(f"SELECT 1 FROM companies WHERE slug = {ph}", (slug,), limit=1)
    if exist:
        return {"ok": False, "error": "Bu slug band"}
    from ...db.models import now_utc
    now = now_utc()
    try:
        marks = ",".join([ph] * 18)
        st._exec(
            f"""INSERT INTO companies (name, slug, logo, phone, email, address,
                subscription_plan, subscription_expire_date, status,
                max_users, max_drivers, max_routes, features,
                contact_telegram, contact_phone, notes, created_at, updated_at)
               VALUES ({marks})""",
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
    from ...core import tenant
    tenant.reset()
    return {"ok": True, "message": "Kompaniya yaratildi", "slug": slug}


# ---------------------------------------------------------------------------
# SaaS: kompaniyani to'liq ro'yxatga olish (onboarding)
# ---------------------------------------------------------------------------

# Kompaniya darajasidagi rollar (ADMIN — tizim darajasi, kompaniyaga berilmaydi)
_COMPANY_USER_ROLES = ("DIRECTOR", "MANAGER", "DISPATCHER", "VIEWER")


def _onb_step(steps: list, key: str, ok: bool, message: str = "") -> None:
    """Bajarilgan qadamni natijaga qo'shadi (frontend ro'yxat ko'rinishida chiqaradi)."""
    steps.append({"key": key, "ok": bool(ok), "message": str(message or "")})


def _onb_owner_ids(raw) -> list[int] | None:
    """`ownerChatIds` (matn "1,2" yoki ro'yxat) → int ro'yxati (bo'sh bo'lsa None)."""
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else re.split(r"[,\s;]+", str(raw))
    out: list[int] = []
    for v in items:
        s = str(v).strip()
        if s.lstrip("-").isdigit() and int(s) not in out:
            out.append(int(s))
    return out


def _onb_group_ids(raw) -> list[str]:
    """Firma guruhlari (`groupChatIds`) → normallashtirilgan chat ID ro'yxati."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else re.split(r"[,\s;]+", str(raw))
    out: list[str] = []
    for v in items:
        s = str(v).strip()
        body = s[1:] if s.startswith("-") else s
        if body.isdigit() and s not in out:
            out.append(s)
    return out or None


def _onb_int(value, default: int = 0) -> int:
    """Musbat int yoki default (bo'sh/noto'g'ri qiymat uchun)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _onb_autofill_profile(name: str, steps: list, warnings: list,
                          route_id: str = "") -> dict:
    """Bo'sh yo'nalish nomi va konechkalarni waybill'lardan to'ldiradi.

    Faqat bo'sh maydonlar to'ldiriladi — qo'lda kiritilgan qiymat ustidan
    yozilmaydi. Aniqlanmasa admin kompaniyani tahrirlab qo'lda kiritishi
    kerakligi haqida ogohlantiriladi (boshqa firma nomlari chiqmasligi uchun
    standart qiymatlar umuman ishlatilmaydi).
    """
    from ...core.profiles import get_profile, update_profile_fields
    from ...services.driver_sheet_service import detect_profile_fields

    prof = get_profile(name, route_id=route_id) or {}
    have = {
        key: bool(str(prof.get(key) or "").strip())
        for key in ("routeName", "start1", "start2")
    }
    if all(have.values()):
        return {}
    if not str(prof.get("routeVariantId") or "").strip():
        return {}
    starts_missing = not (have["start1"] and have["start2"])

    def _fail(message: str) -> None:
        if starts_missing:
            _onb_step(steps, "konechka", False, message)
        if not have["routeName"]:
            _onb_step(steps, "route_name", False, message)
        warnings.append("Yo'nalish nomi/konechka aniqlanmadi — kompaniyani "
                        "tahrirlab qo'lda kiriting")

    try:
        found = detect_profile_fields(prof)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Aniqlanmadi: {exc}")
        return {}
    fields = {}
    for key in ("routeName", "start1", "start2"):
        if not have[key] and str(found.get(key) or "").strip():
            fields[key] = str(found[key]).strip()
    if not fields:
        _fail("Waybill topilmadi")
        return {}
    try:
        update_profile_fields(name, fields, route_id=route_id)
    except Exception as exc:  # noqa: BLE001
        _fail(f"Saqlanmadi: {exc}")
        return {}
    after = get_profile(name, route_id=route_id) or {}
    if starts_missing:
        up = str(after.get("start1") or "").strip()
        down = str(after.get("start2") or "").strip()
        _onb_step(steps, "konechka", bool(up and down),
                  f"1-chiqish: {up or '—'} · 2-chiqish: {down or '—'}")
        missing = [label for label, val in (("1-chiqish", up), ("2-chiqish", down))
                   if not val]
        if missing:
            warnings.append(f"{', '.join(missing)} konechkasi aniqlanmadi — "
                            "kompaniyani tahrirlab qo'lda kiriting")
    if not have["routeName"]:
        route_name = str(after.get("routeName") or "").strip()
        _onb_step(steps, "route_name", bool(route_name),
                  route_name or "Aniqlanmadi")
        if not route_name:
            warnings.append("Yo'nalish nomi aniqlanmadi — kompaniyani "
                            "tahrirlab qo'lda kiriting")
    return fields


@router.post("/api/admin/companies/onboard")
async def onboard_company(request: Request, scope: dict = Depends(Scope())):
    """Yangi korxonani to'liq ro'yxatga oladi (faqat Super Admin).

    Bitta so'rovda: `profiles.json` yozuvi + BM kredensiallari + `companies`
    reyestri (tarif/limit/obuna) + `profiles` DB qatori + dashboard
    foydalanuvchisi. Ixtiyoriy: BM kredensiallarini tekshirish va
    kompaniyaning O'Z tokeni bilan kunlik sinxronni ishga tushirish.

    Qadamlar `steps` ro'yxatida qaytadi — qaysi biri bajarilmaganini
    foydalanuvchi darhol ko'radi. Hard xatolarda hech narsa yozilmaydi
    (barcha tekshiruvlar oldindan).
    """
    _require_super_admin(scope)
    from ...core.profiles import (add_profile, all_profiles, get_profile,
                                  set_company_group_ids, set_credentials,
                                  update_profile_fields)
    from ...db.storage import get_storage
    from ...db.tenancy import slugify
    from ..deps import hash_password

    payload = await request.json()
    steps: list[dict] = []
    warnings: list[str] = []

    def fail(msg: str) -> dict:
        return {"ok": False, "error": msg, "steps": steps, "warnings": warnings}

    name = str(payload.get("name") or "").strip()
    rid = str(payload.get("routeVariantId") or "").strip()
    if not name:
        return fail("Kompaniya nomi kerak")
    if not rid:
        return fail("routeVariantId (yo'nalish) kerak")
    if get_profile(name):
        return fail(f"Bu nom bilan kompaniya allaqachon bor: {name}")
    for p in all_profiles():
        if str(p.get("routeVariantId") or "").strip() == rid:
            return fail(f"Bu yo'nalish allaqachon «{p.get('name')}» kompaniyasiga bog'langan")

    bm_user = str(payload.get("bmUsername") or "").strip()
    bm_pass = str(payload.get("bmPassword") or "").strip()
    if bool(bm_user) != bool(bm_pass):
        return fail("BM login va parol — ikkalasi ham kiritilishi kerak")

    user_login = str(payload.get("userUsername") or "").strip().lower()
    user_pass = str(payload.get("userPassword") or "")
    role = str(payload.get("userRole") or "DIRECTOR").strip().upper()
    if bool(user_login) != bool(user_pass):
        return fail("Foydalanuvchi logini va paroli — ikkalasi ham kerak")
    if role not in _COMPANY_USER_ROLES:
        return fail(f"Rol noto'g'ri: {role} (ruxsat: {', '.join(_COMPANY_USER_ROLES)})")
    if user_pass:
        from ..deps import password_error
        perr = password_error(user_pass, user_login)
        if perr:
            return fail(perr)

    expire = str(payload.get("expireDate") or "").strip()
    if expire:
        try:
            date.fromisoformat(expire[:10])
        except ValueError:
            return fail("Obuna muddati formati noto'g'ri (YYYY-MM-DD)")

    st = get_storage()
    if not st.enabled:
        return fail("DB mavjud emas")
    if user_login and st.dashboard_user_get(username=user_login):
        return fail(f"Bu login allaqachon mavjud: {user_login}")

    # 1) profiles.json — kompaniya va yo'nalishi
    try:
        add_profile(name, route_variant_id=rid,
                    route_name=str(payload.get("routeName") or ""),
                    profile_id=str(payload.get("profileId") or ""))
        extra = {k: payload[k] for k in ("routeName", "start1", "start2")
                 if str(payload.get(k) or "").strip()}
        if _onb_int(payload.get("kmRate"), 0) or str(payload.get("kmRate") or "").strip() in ("0", "0.0"):
            try:
                extra["kmRate"] = float(payload.get("kmRate") or 0)
            except (TypeError, ValueError):
                warnings.append("kmRate raqam emas — tashlab ketildi")
        owners = _onb_owner_ids(payload.get("ownerChatIds"))
        if owners:
            extra["ownerChatIds"] = owners
        if extra:
            update_profile_fields(name, extra)
        if "groupChatIds" in payload or "groups" in payload:
            set_company_group_ids(name, _onb_group_ids(
                payload.get("groupChatIds", payload.get("groups"))))
        _onb_step(steps, "profile", True, f"{name} profili qo'shildi")
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "profile", False, str(exc))
        return {"ok": False, "error": f"Profil yozilmadi: {exc}",
                "steps": steps, "warnings": warnings}

    # 2) BM kredensiallari (kompaniyaning o'z akkaunti)
    if bm_user and bm_pass:
        try:
            set_credentials(name, bm_user, bm_pass)
            _onb_step(steps, "credentials", True, "BM kredensiallari saqlandi")
        except Exception as exc:  # noqa: BLE001
            _onb_step(steps, "credentials", False, str(exc))
            warnings.append(f"BM kredensiallari saqlanmadi: {exc}")
    else:
        _onb_step(steps, "credentials", True, "Kredensial yo'q — asosiy token ishlatiladi")

    # 2.5) Konechka nomlari — waybill'lardan avtomatik (yangi kompaniyada
    # boshqa yo'nalishning nomlari chiqmasligi uchun)
    _onb_autofill_profile(name, steps, warnings, route_id=rid)

    # 3) `companies` reyestri — tarif, obuna, limitlar, kontaktlar
    try:
        _onb_step(steps, "registry", True,
                  _onb_registry_update(st, name, payload, expire))
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "registry", False, str(exc))
        warnings.append(f"Reyestr qatori yozilmadi: {exc}")

    # 4) `profiles` DB jadvali — route→kompaniya xaritasi uchun
    try:
        _onb_step(steps, "profiles_db", True, _onb_sync_profile_db(st, name))
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "profiles_db", False, str(exc))
        warnings.append(f"DB profili yozilmadi: {exc}")

    # 5) Dashboard foydalanuvchisi (korxona admini)
    if user_login and user_pass:
        try:
            digest, salt = hash_password(user_pass)
            uid = st.dashboard_user_add(username=user_login, salt=salt,
                                        password_hash=digest, role=role,
                                        company=name, active=1)
            if not uid:
                raise ValueError("foydalanuvchi yaratilmadi")
            _onb_step(steps, "user", True, f"{user_login} ({role}) yaratildi")
        except Exception as exc:  # noqa: BLE001
            _onb_step(steps, "user", False, str(exc))
            warnings.append(f"Dashboard foydalanuvchisi yaratilmadi: {exc}")
    else:
        _onb_step(steps, "user", True, "Foydalanuvchi so'ralmagan")

    # 6-7) BM tekshiruvi va kunlik sinxron (kompaniyaning O'Z tokeni bilan)
    sync_info = _onb_verify_and_sync(st, name, rid, payload, bm_user, bm_pass,
                                     steps, warnings)

    log.info("Kompaniya onboarding: %s -> %s (%s)", scope.get("username"), name,
             {s["key"]: s["ok"] for s in steps})
    return {"ok": True, "message": f"{name} qo'shildi", "company": name,
            "slug": slugify(name), "steps": steps, "warnings": warnings,
            "sync": sync_info}


def _onb_registry_update(st, name: str, payload: dict, expire: str = "") -> str:
    """`companies` qatorini tarif/limit/kontakt maydonlari bilan yangilaydi.

    Qator yo'q bo'lsa avval `seed_companies` orqali yaratiladi. Faqat
    yuborilgan (bo'sh bo'lmagan) maydonlar yoziladi — qolganlari saqlanadi.
    Qaytadi: qadam izohi.
    """
    from ...db.models import now_utc
    from ...db.tenancy import seed_companies, slugify

    seed_companies(st)
    row = st.find("companies", slug=slugify(name))
    if not row:
        raise ValueError("companies qatori yaratilmadi")
    reg: dict = {}
    for key, val in (
            ("phone", payload.get("phone")),
            ("email", payload.get("email")),
            ("address", payload.get("address")),
            ("subscription_plan", payload.get("plan")),
            ("contact_telegram", payload.get("contactTelegram")),
            # Aloqa telefoni — kompaniya telefonidan alohida maydon; eski
            # xato uni har tahrirda kompaniya telefoniga qayta yozardi.
            ("contact_phone", payload.get("contactPhone")),
            ("notes", payload.get("notes"))):
        sval = str(val or "").strip()
        if sval:
            reg[key] = sval
    if expire:
        reg["subscription_expire_date"] = expire
    for key, src in (("max_users", "maxUsers"),
                     ("max_drivers", "maxDrivers"),
                     ("max_routes", "maxRoutes")):
        num = _onb_int(payload.get(src), 0)
        if num:
            reg[key] = num
    if reg:
        ph = st.db.ph
        set_clause = ", ".join(f"{k} = {ph}" for k in reg)
        # Yozuvlar `query` (SELECT-only) emas, `_exec` orqali — Postgres'da
        # UPDATE dan keyin fetch qilinishi "didn't produce records" xatosi beradi.
        st._exec(f"UPDATE companies SET {set_clause}, updated_at = {ph} "
                 f"WHERE id = {ph}", (*reg.values(), now_utc(), int(row["id"])))
        # Obuna muddati/limit o'zgargan bo'lishi mumkin — tenant keshini
        # darhol yangilaymiz, aks holda 30 soniya eski holat qoladi.
        from ...core import tenant as tenant_mod
        tenant_mod.reset()
    return f"Reyestr: {slugify(name)} (id={row['id']})"


def _onb_sync_profile_db(st, name: str) -> str:
    """`profiles` jadvalini yangilaydi (route→kompaniya xaritasi).

    Kesh AVVAL tozalanadi: `upsert` company_id'ni keshdagi `companies`
    qatoridan to'ldiradi — aks holda yangi qator ko'rinmay qoladi.
    """
    from ...core import tenant as tenant_mod
    from ...core.profiles import all_profiles
    from ...db.sync import sync_profiles

    tenant_mod.reset()
    rows = [p for p in all_profiles()
            if str(p.get("name") or "").strip().lower() == name.strip().lower()]
    res = sync_profiles(st, rows or [{"name": name}])
    if res.error:
        raise ValueError(res.error)
    tenant_mod.reset()
    return "DB profili sinxronlandi"


def _onb_verify_and_sync(st, name: str, rid: str, payload: dict,
                         bm_user: str, bm_pass: str,
                         steps: list, warnings: list) -> dict:
    """BM tekshiruvi (ixtiyoriy) + kunlik sinxronni fon rejimida boshlaydi."""
    from ...core.companies import own_client, route_accessible
    from ...core.profiles import get_profile
    from . import system as system_router

    client = None
    if bm_user and bm_pass and payload.get("verify", True):
        try:
            client = own_client(get_profile(name) or {"name": name})
            _onb_step(steps, "verify", True, "BM kredensiallari ishlaydi")
            if route_accessible(client, rid):
                _onb_step(steps, "route_access", True,
                          "Yo'nalish shu akkauntga ochiq")
            else:
                _onb_step(steps, "route_access", False,
                          "Token bu yo'nalishga kira olmadi")
                warnings.append("Yo'nalish (routeVariantId) shu BM akkauntga tegishli "
                                "emas — sinxron xato berishi mumkin")
        except Exception as exc:  # noqa: BLE001
            client = None
            _onb_step(steps, "verify", False, f"Tekshirilmadi: {exc}")
            warnings.append(f"BM kredensiallari tekshirilmadi: {exc}")

    sync_info: dict = {"ok": False, "started": False}
    if not payload.get("sync", True):
        _onb_step(steps, "sync", True, "Sinxron so'ralmagan")
        return sync_info
    if client is None and (bm_user and bm_pass):
        # Tekshiruv o'chirilgan bo'lsa ham sinxron uchun token kerak
        try:
            client = own_client(get_profile(name) or {"name": name})
        except Exception as exc:  # noqa: BLE001
            client = None
            warnings.append(f"Sinxron uchun token olinmadi: {exc}")
    if client is None and (bm_user and bm_pass):
        _onb_step(steps, "sync", False, "Token yo'q — sinxron boshlanmadi")
        return sync_info
    try:
        sync_info = system_router.start_company_sync(client, st, [rid])
        _onb_step(steps, "sync", bool(sync_info.get("ok")),
                  sync_info.get("error")
                  or "Sinxron fon rejimida boshlandi (/api/sync/status)")
    except Exception as exc:  # noqa: BLE001
        sync_info = {"ok": False, "started": False}
        _onb_step(steps, "sync", False, str(exc))
        warnings.append(f"Sinxron boshlanmadi: {exc}")
    return sync_info


@router.post("/api/admin/companies/edit")
async def edit_company_profile(request: Request, scope: dict = Depends(Scope())):
    """Mavjud korxonani tahrirlash (faqat Super Admin).

    Faqat yuborilgan maydonlar o'zgaradi: yo'nalish, BM kredensiallari,
    reyestr (tarif/limit/kontakt) va dashboard foydalanuvchisi. Kompaniya
    NOMI — kalit (`profiles.json`, `dashboard_users.company`), shu sababli
    o'zgarmaydi.
    """
    _require_super_admin(scope)
    from ...core.profiles import (all_profiles, get_profile,
                                  set_company_group_ids, set_credentials,
                                  update_profile_fields)
    from ...db.storage import get_storage
    from ..deps import hash_password

    payload = await request.json()
    steps: list[dict] = []
    warnings: list[str] = []

    def fail(msg: str) -> dict:
        return {"ok": False, "error": msg, "steps": steps, "warnings": warnings}

    name = str(payload.get("name") or "").strip()
    if not name:
        return fail("Kompaniya nomi kerak")
    match_rid = str(payload.get("matchRouteVariantId")
                    or payload.get("oldRouteVariantId") or "").strip()
    cur_profile = get_profile(name, route_id=match_rid)
    if not cur_profile:
        return fail(f"Kompaniya topilmadi: {name}")
    cur_rid = str(cur_profile.get("routeVariantId") or "").strip()
    if match_rid and cur_rid.lower() != match_rid.lower():
        return fail(f"Yo'nalish topilmadi: {match_rid}")
    match_rid = cur_rid
    st = get_storage()
    if not st.enabled:
        return fail("DB mavjud emas")

    rid = (str(payload.get("routeVariantId") or "").strip()
           if "routeVariantId" in payload
           else cur_rid)
    if not rid:
        return fail("routeVariantId (yo'nalish) kerak")
    for p in all_profiles():
        p_rid = str(p.get("routeVariantId") or "").strip()
        if p_rid.lower() != rid.lower():
            continue
        if (str(p.get("name") or "").strip().lower() == name.lower()
                and p_rid.lower() == match_rid.lower()):
            continue
        return fail(f"Bu yo'nalish «{p.get('name')}» kompaniyasiga bog'langan")

    # Tahrirlashda kredensial qisman yangilanishi mumkin: faqat login yoki
    # faqat parol (bo'sh qolgani mavjud qiymatni saqlaydi).
    bm_user = str(payload.get("bmUsername") or "").strip() \
        if "bmUsername" in payload else ""
    bm_pass = str(payload.get("bmPassword") or "").strip() \
        if "bmPassword" in payload else ""

    user_login = str(payload.get("userUsername") or "").strip().lower()
    user_pass = str(payload.get("userPassword") or "")
    role = str(payload.get("userRole") or "").strip().upper()
    if role and role not in _COMPANY_USER_ROLES:
        return fail(f"Rol noto'g'ri: {role} (ruxsat: {', '.join(_COMPANY_USER_ROLES)})")
    if user_pass:
        from ..deps import password_error
        perr = password_error(user_pass, user_login)
        if perr:
            return fail(perr)
    if user_login and not user_pass and not st.dashboard_user_get(username=user_login):
        return fail(f"Yangi foydalanuvchi uchun parol kerak: {user_login}")

    expire = str(payload.get("expireDate") or "").strip()
    if expire:
        try:
            date.fromisoformat(expire[:10])
        except ValueError:
            return fail("Obuna muddati formati noto'g'ri (YYYY-MM-DD)")

    # 1) profiles.json — faqat yuborilgan maydonlar o'zgaradi
    try:
        fields: dict = {"routeVariantId": rid}
        for key in ("routeName", "start1", "start2", "profileId"):
            if key in payload:
                fields[key] = str(payload.get(key) or "").strip()
        if rid != cur_rid:
            # Yo'nalish almashdi — eski nom va konechkalar qolmasin
            # (keyin BM'dan avtomatik aniqlanadi yoki qo'lda kiritiladi).
            for key in ("routeName", "start1", "start2"):
                if key not in payload:
                    fields[key] = ""
        if "kmRate" in payload:
            try:
                fields["kmRate"] = float(payload.get("kmRate") or 0)
            except (TypeError, ValueError):
                warnings.append("kmRate raqam emas — o'zgartirilmadi")
        if "ownerChatIds" in payload:
            fields["ownerChatIds"] = _onb_owner_ids(payload.get("ownerChatIds")) or []
        update_profile_fields(name, fields, route_id=match_rid)
        if "groupChatIds" in payload or "groups" in payload:
            set_company_group_ids(
                name, _onb_group_ids(payload.get("groupChatIds",
                                                payload.get("groups"))))
        _onb_step(steps, "profile", True, "Profil yangilandi")
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "profile", False, str(exc))
        return {"ok": False, "error": f"Profil yangilanmadi: {exc}",
                "steps": steps, "warnings": warnings}

    # 2) BM kredensiallari (yuborilgan qismi)
    if bm_user or bm_pass:
        try:
            set_credentials(name, bm_user, bm_pass)
            _onb_step(steps, "credentials", True,
                      "BM logini yangilandi" if (bm_user and not bm_pass)
                      else "BM paroli yangilandi" if (bm_pass and not bm_user)
                      else "BM kredensiallari yangilandi")
        except Exception as exc:  # noqa: BLE001
            _onb_step(steps, "credentials", False, str(exc))
            warnings.append(f"BM kredensiallari yangilanmadi: {exc}")
    else:
        _onb_step(steps, "credentials", True, "Kredensial o'zgartirilmadi")

    # 2.5) Bo'sh qolgan konechka nomlari — waybill'lardan avtomatik
    _onb_autofill_profile(name, steps, warnings, route_id=rid)

    # 3) `companies` reyestri
    try:
        _onb_step(steps, "registry", True,
                  _onb_registry_update(st, name, payload, expire))
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "registry", False, str(exc))
        warnings.append(f"Reyestr yangilanmadi: {exc}")

    # 4) `profiles` DB qatori
    try:
        _onb_step(steps, "profiles_db", True, _onb_sync_profile_db(st, name))
    except Exception as exc:  # noqa: BLE001
        _onb_step(steps, "profiles_db", False, str(exc))
        warnings.append(f"DB profili yangilanmadi: {exc}")

    # 5) Dashboard foydalanuvchisi (yangi yoki mavjudni yangilash)
    if user_login:
        try:
            existing = st.dashboard_user_get(username=user_login)
            digest = salt = ""
            if user_pass:
                digest, salt = hash_password(user_pass)
            if existing:
                kwargs: dict = {"role": role or None, "company": name, "active": 1}
                if user_pass:
                    kwargs.update(salt=salt, password_hash=digest)
                st.dashboard_user_update(int(existing["id"]), **kwargs)
                if user_pass:
                    from ..deps import drop_user_sessions
                    drop_user_sessions(user_login)
                _onb_step(steps, "user", True, f"{user_login} yangilandi")
            else:
                uid = st.dashboard_user_add(username=user_login, salt=salt,
                                            password_hash=digest,
                                            role=role or "DIRECTOR",
                                            company=name, active=1)
                if not uid:
                    raise ValueError("foydalanuvchi yaratilmadi")
                _onb_step(steps, "user", True, f"{user_login} yaratildi")
        except Exception as exc:  # noqa: BLE001
            _onb_step(steps, "user", False, str(exc))
            warnings.append(f"Dashboard foydalanuvchisi yangilanmadi: {exc}")
    else:
        _onb_step(steps, "user", True, "Foydalanuvchi o'zgartirilmadi")

    # 6-7) BM tekshiruvi va sinxron
    sync_info = _onb_verify_and_sync(st, name, rid, payload, bm_user, bm_pass,
                                     steps, warnings)

    log.info("Kompaniya tahrirlandi: %s -> %s (%s)", scope.get("username"), name,
             {s["key"]: s["ok"] for s in steps})
    return {"ok": True, "message": f"{name} yangilandi", "company": name,
            "steps": steps, "warnings": warnings, "sync": sync_info}


@router.get("/api/admin/companies/{company_id}")
def get_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya ma'lumotlari (Super Admin)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    row = st.query(f"SELECT * FROM companies WHERE id = {st.db.ph}",
                   (company_id,), limit=1)
    if not row:
        raise HTTPException(404, "Kompaniya topilmadi")
    return {"ok": True, "company": dict(row[0])}


@router.put("/api/admin/companies/{company_id}")
async def update_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya ma'lumotlarini yangilash (Super Admin)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...db.models import now_utc
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
    ph = st.db.ph
    cur = st.query(f"SELECT name FROM companies WHERE id = {ph}",
                   (company_id,), limit=1)
    if not cur:
        raise HTTPException(404, "Kompaniya topilmadi")
    old_name = str(cur[0].get("name") or "")
    new_name = str(updates.get("name") or "").strip()
    rename = bool(new_name and new_name != old_name)
    if rename and "slug" not in updates:
        # Nom bo'yicha slug ham yangilanadi: onboarding keyingi tahrirda
        # slug orqali qatorni topadi (aks holda dublikat reyestr qatori).
        from ...db.tenancy import slugify
        updates["slug"] = slugify(new_name)
    # slug unikalligi
    if "slug" in updates:
        slug = str(updates["slug"]).strip().lower()
        exist = st.query(f"SELECT 1 FROM companies WHERE slug = {ph} "
                         f"AND id != {ph}", (slug, company_id), limit=1)
        if exist:
            return {"ok": False, "error": "Bu slug band"}
    set_clause = ", ".join(f"{k} = {ph}" for k in updates)
    values = list(updates.values()) + [now_utc(), company_id]
    if not st._exec(f"UPDATE companies SET {set_clause}, updated_at = {ph} "
                    f"WHERE id = {ph}", tuple(values)):
        return {"ok": False, "error": "Kompaniya topilmadi"}
    # Status/obuna o'zgarishi darhol kuchga kirsin: tenant keshida eski
    # qator 30 soniyagacha qolib, bloklangan korxona ishlab turardi.
    from ...core import tenant
    tenant.reset()
    if rename:
        # Nom — tenant kaliti: profiles.json, dashboard_users va sessiyalar
        # ham yangilanadi. Aks holda eski nomli akkauntlar yo'nalishsiz
        # qolib, kompaniya ma'lumotlarini ko'ra olmaydi.
        from ...core.profiles import rename_profile
        from ...core import tenant
        from ..deps import drop_user_sessions, invalidate_user_access
        try:
            rename_profile(old_name, new_name)
            users = st.query(
                f"SELECT username FROM dashboard_users WHERE company = {ph}",
                (old_name,), limit=1000)
            st._exec(
                f"UPDATE dashboard_users SET company = {ph} WHERE company = {ph}",
                (new_name, old_name))
            for u in users:
                uname = str(u.get("username") or "")
                drop_user_sessions(uname)
                invalidate_user_access(uname)
            invalidate_user_access()
            tenant.reset()
        except Exception as exc:  # noqa: BLE001
            log.warning("Kompaniya nomi sinxronlanmadi (%s -> %s): %s",
                        old_name, new_name, exc)
    return {"ok": True, "message": "Yangilandi"}


@router.delete("/api/admin/companies/{company_id}")
async def delete_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya o'chirish (Super Admin) — soft delete: status='deleted'."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...db.models import now_utc
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    ph = st.db.ph
    cur = st.query(f"SELECT name FROM companies WHERE id = {ph}",
                   (company_id,), limit=1)
    if not cur:
        return {"ok": False, "error": "Kompaniya topilmadi"}
    company_name = str(cur[0].get("name") or "").strip()
    if not st._exec(f"UPDATE companies SET status = 'deleted', "
                    f"updated_at = {ph} WHERE id = {ph}",
                    (now_utc(), company_id)):
        return {"ok": False, "error": "Kompaniya topilmadi"}
    # Kesh 30 soniya yashaydi — reset bo'lmasa o'chirilgan korxona
    # foydalanuvchilari shu vaqt ichida ishlashda davom etardi.
    from ...core import tenant
    tenant.reset()
    # Mavjud sessiyalar ham darhol yopiladi — soft-delete'dan keyin
    # foydalanuvchi cookie bilan ishlashda davom etmasin.
    if company_name:
        try:
            users = st.query(
                f"SELECT username FROM dashboard_users WHERE company = {ph}",
                (company_name,), limit=1000)
        except Exception:  # noqa: BLE001
            users = []
        from ..deps import drop_user_sessions, invalidate_user_access
        for u in users:
            uname = str(u.get("username") or "").strip().lower()
            if not uname:
                continue
            drop_user_sessions(uname)
            invalidate_user_access(uname)
    return {"ok": True, "message": "O'chirildi (status=deleted)"}


@router.post("/api/admin/companies/{company_id}/activate")
async def activate_company(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniyani faollashtirish (obuna yo'qolganida ham)."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ...db.models import now_utc
    payload = await request.json()
    days = int(payload.get("days") or 365)
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    from datetime import date, timedelta
    expire = (date.today() + timedelta(days=days)).isoformat()
    ph = st.db.ph
    if not st._exec(
            f"UPDATE companies SET status = 'active', "
            f"subscription_expire_date = {ph}, updated_at = {ph} WHERE id = {ph}",
            (expire, now_utc(), company_id)):
        return {"ok": False, "error": "Kompaniya topilmadi"}
    from ...core import tenant
    tenant.reset()
    return {"ok": True, "message": f"Faollashtirildi ({days} kun)", "expire_date": expire}


# ---------------------------------------------------------------------------
# SaaS: Company users (per-company admin)
# ---------------------------------------------------------------------------

@router.get("/api/admin/companies/{company_id}/users")
def list_company_users(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniya foydalanuvchilari ro'yxati."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    users = st.query(
        f"SELECT id, username, role, company, active, created_at, data "
        f"FROM dashboard_users WHERE company = "
        f"(SELECT name FROM companies WHERE id = {st.db.ph})", (company_id,), limit=200)
    return {"ok": True, "users": [dict(u) for u in users]}


@router.post("/api/admin/companies/{company_id}/users")
async def create_company_user(company_id: int, request: Request, scope: dict = Depends(Scope())):
    """Kompaniyaga yangi foydalanuvchi qo'shish."""
    _require_super_admin(scope)
    from ...db.storage import get_storage
    from ..deps import hash_password, invalidate_user_access, password_error
    payload = await request.json()
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "VIEWER").strip().upper()
    # Kompaniya akkaunti ADMIN bo'la olmaydi — ADMIN butun tizim darajasi
    # (barcha korxonalar). Aks holda korxona foydalanuvchisi global
    # administratorga aylanib, boshqa tenantlarga kira olardi.
    if role not in _COMPANY_USER_ROLES:
        role = "VIEWER"
    if not username or not password:
        return {"ok": False, "error": "Login va parol kerak"}
    perr = password_error(password, username)
    if perr:
        return {"ok": False, "error": perr}
    st = get_storage()
    if not st.enabled:
        return {"ok": False, "error": "DB mavjud emas"}
    # company nomi
    ph = st.db.ph
    comp = st.query(f"SELECT name FROM companies WHERE id = {ph}",
                    (company_id,), limit=1)
    if not comp:
        return {"ok": False, "error": "Kompaniya topilmadi"}
    company_name = str(comp[0].get("name") or "")
    # max_users chek
    comp_info = st.query(f"SELECT max_users FROM companies WHERE id = {ph}",
                         (company_id,), limit=1)
    max_u = int(comp_info[0].get("max_users") or 5) if comp_info else 5
    cur_count = st.query(f"SELECT COUNT(*) as c FROM dashboard_users "
                         f"WHERE company = {ph}", (company_name,), limit=1)
    if int(cur_count[0].get("c") or 0) >= max_u:
        return {"ok": False, "error": f"Maksimal foydalanuvchi soni ({max_u}) yetib qo'yilgan"}
    if st.dashboard_user_get(username=username):
        return {"ok": False, "error": "Bu login allaqachon mavjud"}
    digest, salt = hash_password(password)
    uid = st.dashboard_user_add(
        username=username, salt=salt, password_hash=digest,
        role=role, company=company_name, active=1)
    if uid:
        invalidate_user_access(username)
    return {"ok": True, "id": uid, "message": "Foydalanuvchi qo'shildi"}


def _update_company_creds(payload: dict) -> dict:
    from ...core.profiles import get_profile, set_credentials
    name = str(payload.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Kompaniya nomi kerak"}
    if not get_profile(name):
        return {"ok": False, "error": f"Kompaniya topilmadi: {name}"}
    try:
        set_credentials(name, payload.get("username") or "",
                        payload.get("password") or "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": f"{name} kredensiallari yangilandi"}


# ---------------------------------------------------------------------------
# Bot users / dispatchers
# ---------------------------------------------------------------------------

def _phone_key(value) -> str:
    """Telefonni solishtirish kaliti (998 olib tashlanadi, faqat raqamlar)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("998") and len(digits) > 9:
        digits = digits[3:]
    return digits if len(digits) >= 7 else ""


def _bot_user_drivers(st, users: list) -> dict:
    """chat_id -> haydovchi profili. Ikki so'rovda (N+1 o'rniga) yechiladi."""
    by_cid: dict[str, dict] = {}
    for u in users:
        try:
            by_cid[str(int(u.get("chat_id")))] = u
        except (TypeError, ValueError):
            continue
    if not by_cid:
        return {}
    ph = st.db.ph
    cols = ("driver_id, phone, notification_target, telegram_chat_id, "
            "notification_enabled, blacklisted")
    found: dict[str, dict] = {}
    ids = list(by_cid)
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        rows = st.query(
            f"SELECT {cols} FROM driver_profiles WHERE telegram_chat_id IN ("
            + ",".join([ph] * len(chunk)) + ")", tuple(chunk))
        for r in rows or []:
            found[str(r.get("telegram_chat_id") or "")] = r
    missing = [u for cid, u in by_cid.items() if cid not in found]
    if missing:
        rows = st.query(
            "SELECT driver_id, phone, notification_target, telegram_chat_id, "
            "notification_enabled, blacklisted FROM driver_profiles "
            "WHERE phone <> '' OR notification_target <> ''", limit=100000)
        index: dict[str, dict] = {}
        for r in rows or []:
            for field in ("phone", "notification_target"):
                key = _phone_key(r.get(field))
                if key and key not in index:
                    index[key] = r
        for u in missing:
            key = _phone_key(u.get("phone"))
            if key and key in index:
                try:
                    found[str(int(u.get("chat_id")))] = index[key]
                except (TypeError, ValueError):
                    pass
    return found


def _driver_names(st, driver_ids: list) -> dict:
    """external_id -> full_name (guruhlangan IN so'rovlar)."""
    names: dict[str, str] = {}
    if not driver_ids:
        return names
    ph = st.db.ph
    for i in range(0, len(driver_ids), 200):
        chunk = driver_ids[i:i + 200]
        rows = st.query(
            "SELECT external_id, full_name FROM drivers WHERE external_id IN ("
            + ",".join([ph] * len(chunk)) + ")", tuple(chunk))
        for r in rows or []:
            names[str(r.get("external_id") or "")] = str(r.get("full_name") or "")
    return names


@router.get("/api/users")
def get_users(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import (get_pages as _get_pages,
                                   get_users as _get_users, user_count)
    users = _get_users()
    try:
        from ...db.storage import get_storage
        from ...notifications.ops.roles import resolve_role, role_label
        st = get_storage()
        if st.enabled:
            drivers = _bot_user_drivers(st, users)
            names = _driver_names(st, sorted({
                str(p.get("driver_id") or "") for p in drivers.values()} - {""}))
            for u in users:
                try:
                    cid = int(u.get("chat_id"))
                except (TypeError, ValueError):
                    cid = None
                prof = drivers.get(str(cid) if cid is not None else "") or {}
                if prof:
                    did = str(prof.get("driver_id") or "")
                    u["driver_name"] = names.get(did) or did
                    u["driver_phone"] = str(prof.get("phone") or "")
                    u["driver_id"] = did
                    u["linked"] = True
                if cid is None:
                    continue
                try:
                    u["resolved_role"] = role_label(resolve_role(cid))
                except Exception:
                    pass
                # Bo'limlar: None = rol bo'yicha cheklovsiz; ro'yxat = faqat
                # shu bo'limlar ishlaydi (panel va bot uchun bir xil).
                try:
                    u["pages"] = _get_pages(cid)
                except Exception:
                    pass
    except Exception:
        pass
    return {"ok": True, "count": user_count(), "users": users}


def _dashboard_link(chat_id: int) -> tuple[str, list | None]:
    """Telegram chat_id qaysi panel foydalanuvchisiga bog'langan → (login, bo'limlar)."""
    from ...db.storage import get_storage
    st = get_storage()
    if not st.enabled:
        return "", None
    want = str(int(chat_id))
    for row in st.dashboard_users_list():
        try:
            data = json.loads(str(row.get("data") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("telegram_chat_id") or "").strip() != want:
            continue
        if not int(row.get("active") or 0):
            continue
        pages = data.get("pages")
        return (str(row.get("username") or ""),
                pages if isinstance(pages, list) else None)
    return "", None


@router.get("/api/users/{uid}")
def get_user_detail(uid: int, request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_users import get_user
    user = get_user(uid)
    if not user:
        raise HTTPException(404, "Foydalanuvchi topilmadi")
    out = {"ok": True, "user": user}
    # Panel akkaunti bog'langan bo'lsa — uning bo'limlari botda ham amal
    # qiladi; panelda ko'rsatish uchun login va bo'limlar qaytariladi.
    try:
        name, pages = _dashboard_link(uid)
        if name:
            out["dashboard_user"] = {"username": name, "pages": pages}
    except Exception:  # noqa: BLE001
        pass
    return out


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
def get_user_driver_info(uid: int, request: Request, scope: dict = Depends(Scope())):
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


@router.post("/api/users/{uid}/pages")
async def set_user_pages(uid: int, request: Request, scope: dict = Depends(Scope())):
    """Telegram foydalanuvchisiga ishlaydigan bo'limlarni belgilaydi.

    `pages: null` — cheklov olib tashlanadi (faqat rol bo'yicha);
    `pages: [...]` — faqat shu bo'limlar (`core/sections.py` kalitlari).
    """
    from ...core.bot_users import set_pages
    from ...core.sections import clean_sections
    payload = await request.json()
    if "pages" not in payload:
        return {"ok": False, "error": "pages maydoni kerak"}
    pages, err = clean_sections(payload.get("pages"))
    if err:
        return {"ok": False, "error": err}
    try:
        ok = set_pages(uid, pages)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not ok:
        return {"ok": False, "error": "Foydalanuvchi topilmadi"}
    return {"ok": True, "pages": pages}


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
def get_dispatchers(request: Request, scope: dict = Depends(Scope())):
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
def get_group_stats(request: Request, scope: dict = Depends(Scope())):
    from ...core.group_stats import group_stats
    return {"ok": True, **group_stats()}


# ---------------------------------------------------------------------------
# Admin: salary, avans, fines, staff
# ---------------------------------------------------------------------------

@router.get("/api/admin/summary")
def admin_summary(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import salary_summary
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    return {"ok": True, **salary_summary(year, month)}


@router.get("/api/admin/drivers")
def admin_drivers(request: Request, scope: dict = Depends(Scope())):
    from ..admin_service import _drivers
    params = _parse_qs(request)
    year, month = _admin_ym(params)
    return {"ok": True, "drivers": _drivers(year, month)}


@router.get("/api/admin/avans")
def admin_avans(request: Request, scope: dict = Depends(Scope())):
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
def admin_fines(request: Request, scope: dict = Depends(Scope())):
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
def admin_staff(request: Request, scope: dict = Depends(Scope())):
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
def sms_log_api(request: Request, scope: dict = Depends(Scope())):
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
def sms_routes(request: Request, scope: dict = Depends(Scope())):
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
    scopes = scope_routes(scope)
    if scopes is not None and not scopes:
        raise HTTPException(403, "Yo'nalishlar topilmadi")
    result = retry_failed_sms(limit=min(max(limit, 1), 200), routes=scopes)
    return {"ok": True, **result}


@router.post("/api/sms/send")
async def sms_send(request: Request, scope: dict = Depends(Scope())):
    """Qo'lda SMS — yo'nalishga bog'lanadi (non-admin faqat o'z scope'ida).

    Non-admin uchun `route_id` majburiy (bitta yo'nalish bo'lsa avtomatik
    tanlanadi) va SMS jurnaliga `route_id`/`route_name` bilan yoziladi —
    aks holda yozuv scope filtri ostida ko'rinmay qolardi.
    """
    from ...core.profiles import all_profiles
    from ...notifications.sms_notify import send_and_log
    payload = await request.json()
    phone = str(payload.get("phone") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not phone or not text:
        raise HTTPException(400, "Telefon va matn kiritilishi kerak")
    names: dict[str, str] = {}
    for p in all_profiles():
        rid = str(p.get("routeVariantId") or "").strip()
        if rid and rid not in names:
            names[rid] = str(p.get("routeName") or p.get("name") or "") or rid
    route_id = str(payload.get("route_id") or "").strip()
    scopes = scope_routes(scope)
    if scopes is not None:
        if not scopes:
            raise HTTPException(403, "Yo'nalishlar topilmadi")
        if not route_id:
            if len(scopes) != 1:
                raise HTTPException(400, "Yo'nalish tanlanishi shart")
            route_id = next(iter(scopes))
        if route_id not in scopes:
            raise HTTPException(404, "Yo'nalish topilmadi")
    elif route_id and route_id not in names:
        raise HTTPException(404, "Yo'nalish topilmadi")
    result = send_and_log(phone=phone, text=text[:500],
                          name=str(payload.get("name") or phone),
                          route_id=route_id,
                          route_name=names.get(route_id, ""))
    return {"ok": True, "route_id": route_id, **result}


@router.post("/api/sms/routes")
async def sms_route_set(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.sms_notify import sms_route_set as _set
    payload = await request.json()
    route_id = str(payload.get("route_id") or "").strip()
    enabled = payload.get("enabled")
    if not route_id and enabled is None:
        raise HTTPException(400, "route_id yoki enabled kerak")
    scopes = scope_routes(scope)
    if scopes is not None:
        if not route_id:
            raise HTTPException(403, "Yo'nalish tanlanishi shart")
        if route_id not in scopes:
            raise HTTPException(404, "Yo'nalish topilmadi")
    result = _set(route_id=route_id, enabled=enabled if enabled is not None else None)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Documents (hujjatlar bo'limi)
# ---------------------------------------------------------------------------

@router.get("/api/documents")
def documents_list(request: Request, scope: dict = Depends(Scope())):
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
def documents_templates(request: Request, scope: dict = Depends(Scope())):
    from ...documents.templates import list_templates, CATEGORIES
    return {"ok": True, "templates": list_templates(), "categories": dict(CATEGORIES)}


@router.get("/api/documents/autofill")
def documents_autofill(request: Request, scope: dict = Depends(Scope())):
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
def document_detail(doc_id: int, request: Request, scope: dict = Depends(Scope())):
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


def _camera_scope_check(cam: dict, scope: dict) -> None:
    """Kompaniya akkaunti faqat O'Z kamerasini o'zgartiradi.

    Global admin (`company_id=0`) barcha kameralarga egalik qiladi;
    korxonaga bog'langan akkaunt esa boshqa tenant kamerasiga tegolmaydi.
    """
    cid = int(scope.get("company_id") or 0)
    if cid and int(cam.get("company_id") or 0) != cid:
        raise HTTPException(403, "Kamera sizga tegishli emas")


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
def video_cameras_list(request: Request, scope: dict = Depends(Scope(min_role=1))):
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

    cam = storage.db.query(
        "SELECT id, company_id FROM video_cameras WHERE id = %s",
        (cam_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")
    _camera_scope_check(cam[0], scope)

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

    cam = storage.db.query(
        "SELECT id, company_id FROM video_cameras WHERE id = %s",
        (cam_id,), limit=1)
    if not cam:
        raise HTTPException(404, "Kamera topilmadi")
    _camera_scope_check(cam[0], scope)

    storage.db.execute("DELETE FROM video_cameras WHERE id = %s", (cam_id,))
    return {"ok": True}


# =============================================================================
# Video Setka (Grid) — bitta kanal bo'yicha barcha avtobuslar
# =============================================================================

@router.get("/api/video/channels")
def video_channels_list(request: Request, scope: dict = Depends(Scope(min_role=1))):
    """Foydalanuvchi uchun mavjud kamera kanallar ro'yxati."""
    if not _has_video_perm(scope, "live"):
        raise HTTPException(403, "Video ko'rish ruxsati yo'q")

    # Standart 8 kanal (JT_CAMERA_CHANNELS), lekin keyinroq kameralar jadvalidan aniqlanishi mumkin
    max_channels = 8
    return {"ok": True, "channels": list(range(1, max_channels + 1))}


@router.get("/api/video/grid")
def video_grid(
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
