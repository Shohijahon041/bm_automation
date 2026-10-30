"""Discovery admin router — faqat ADMIN (role) uchun avtomatik kashf qilish.

Ikki endpoint:
  * GET  /api/admin/discovery/status — "BM'dan olish" natijasi (background kashf)
  * POST /api/admin/discovery/import — tanlangan korxonalarni ro'yxatga olish

Ruxsat: faqat `role == ADMIN` (super-admin emas, oddiy ADMIN ham kiradi).
Import bitta payload ichida NECHTA korxona va NECHTA yo'nalishni ham
qo'llaydi (bitta OneID profilida bir nechta korxona bo'lishi mumkin).
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, Request

from ..deps import Scope
from ...utils.logger import get_logger

log = get_logger("bm_automation.dashboard.discovery")

router = APIRouter(tags=["admin"])


def _require_admin(scope: dict) -> None:
    """Faqat ADMIN roli (DISPATCHER/MANAGER/VIEWER uchun 403)."""
    if not scope.get("is_admin"):
        from fastapi import HTTPException
        raise HTTPException(403, "Ruxsat faqat ADMIN uchun")


@router.get("/api/admin/discovery/status")
async def discovery_status(scope: dict = Depends(Scope())):
    """"BM'dan olish" (kashf) jarayoni holati — SPA shu yerdan kuzatadi.

    Qaytaradi:
      {"ok": True, "running": bool, "progress": str, "result": {...}}
    `result` — `discovery.build_companies()` natijasi (companies/routes/errors).
    """
    _require_admin(scope)
    from .. import discovery as d
    with d._LOCK:
        running = d._BG["running"]
        progress = d._BG["progress"]
        result = d._BG["result"]
    return {"ok": True, "running": running, "progress": progress,
            "result": result}


@router.post("/api/admin/discovery/import")
async def discovery_import(request: Request, scope: dict = Depends(Scope())):
    """Tanlangan korxonalarni ro'yxatga oladi (profiles.json + companies + users).

    Payload:
      {
        "username": "...", "password": "...",       # BM kredensiallari
        "companies": [
          {"name": "AND ECO", "profileId": "...", "routes": ["rid1", "rid2"]},
          ...
        ],
        "userUsername": "yangi_admin",              # bitta korxona uchun
        "userPassword": "...",
        "verify": true, "sync": true
      }

    Qaytadi: {"ok": bool, "added": int, "skipped": int, "errors": [str]}
    """
    _require_admin(scope)
    payload = await request.json()
    from ...core import discovery as disc

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "").strip()
    if not username or not password:
        return {"ok": False, "error": "BM login/parol bo'sh", "added": 0,
                "skipped": 0, "errors": ["BM login/parol bo'sh"]}

    items = payload.get("companies") or []
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "Korxonalar tanlanmagan", "added": 0,
                "skipped": 0, "errors": ["Korxonalar tanlanmagan"]}

    # Bir korxona tanlanganda foydalanuvchi ham qo'shilsin
    single = len(items) == 1
    user_login = str(payload.get("userUsername") or "").strip()
    user_pass = str(payload.get("userPassword") or "")
    if user_login and not user_pass:
        return {"ok": False, "error": "Foydalanuvchi paroli bo'sh",
                "added": 0, "skipped": 0,
                "errors": ["Foydalanuvchi paroli bo'sh"]}

    added = 0
    skipped = 0
    errors: list[str] = []
    sync_jobs: list[tuple[str, str, list[str]]] = []
    st = disc.get_storage()

    for raw in items:
        name = str(raw.get("name") or "").strip()
        profile_id = str(raw.get("profileId") or "").strip()
        routes = [str(r) for r in (raw.get("routes") or [])]
        if not name:
            errors.append("Korxona nomi bo'sh")
            skipped += 1
            continue
        try:
            res = disc.import_company(
                st, name, profile_id=profile_id, routes=routes,
                username=username, password=password,
                user_username=user_login if single else "",
                user_password=user_pass if single else "",
                verify=bool(payload.get("verify", True)),
                sync=bool(payload.get("sync", True)),
            )
            if res.get("ok"):
                added += 1
                sync_jobs.append((name, profile_id, routes))
            else:
                skipped += 1
                errors.append(f"{name}: {res.get('error')}")
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(f"{name}: {exc}")
            log.warning("Discovery import xato (%s): %s", name, exc)

    # Fon sinxroni (cheklovsiz; bitta korxona ham bo'lsa)
    if sync_jobs:
        threading.Thread(
            target=disc.sync_companies_bg,
            args=sync_jobs,
            daemon=True,
        ).start()

    ok = added > 0
    msg = (f"{added} ta korxona qo'shildi"
           + (f", {skipped} ta o'tkazib ketildi" if skipped else ""))
    log.info("Discovery import: %s (xato: %d)", msg, len(errors))
    return {"ok": ok, "added": added, "skipped": skipped, "errors": errors,
            "message": msg}
