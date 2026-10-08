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


@router.post("/api/settings/env")
async def update_env_settings(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    updates: dict[str, str] = {}
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        return {"ok": False, "error": "values noto'g'ri"}
    for key, raw in values.items():
        if key not in _ENV_KEYS:
            continue
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
    return {"ok": remove_user(uid)}


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
