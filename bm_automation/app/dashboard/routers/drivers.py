"""Drivers router — haydovchilar, rasmlar, hujjatlar, avtobuslar."""

from __future__ import annotations

import base64
import binascii
import math
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response as FastResponse

from ..deps import Scope, force_route, scope_routes

router = APIRouter(tags=["drivers"])

DOCUMENT_DIR = Path("data") / "driver_documents"
PHOTO_DIR = Path("data") / "driver_photos"
DOCUMENT_FIELDS = {
    "passport_front": "passport_front_path",
    "passport_back": "passport_back_path",
    "license_front": "license_front_path",
    "license_back": "license_back_path",
}
PHOTO_FIELD = "photo_path"
DOCUMENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_DRIVER_MONEY_KEYS = ("gross_pay", "tax", "fines", "net_pay")


def _parse_qs(request: Request) -> dict:
    return dict(request.query_params)


def _driver_in_scope(storage, driver_id: str, scopes: set) -> bool:
    if not driver_id:
        return False
    row = storage.find("drivers", external_id=driver_id)
    return bool(row and str(row.get("route_id") or "") in scopes)


def _image_ext(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _driver_finance_visible() -> bool:
    from ...core import bot_settings
    return bot_settings.show_driver_salary()


def _mask_driver_money(row: dict | None) -> None:
    if row is None:
        return
    for key in _DRIVER_MONEY_KEYS:
        if key in row:
            row[key] = None


@router.get("/api/drivers")
async def api_drivers(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/drivers", params)
    rep = m.Metrics().driver_directory(params)
    show = _driver_finance_visible()
    if not show:
        for dr in rep.get("drivers") or []:
            _mask_driver_money(dr)
        totals = rep.get("totals") or {}
        for key in _DRIVER_MONEY_KEYS:
            if key in totals:
                totals[key] = None
        rep["show_driver_salary"] = False
    return {"ok": True, **rep}


@router.get("/api/drivers/list")
async def api_drivers_list(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}
    rows = storage.query(
        "SELECT d.external_id, d.full_name, d.route_id, "
        "dp.telegram_chat_id FROM drivers d "
        "LEFT JOIN driver_profiles dp ON dp.driver_id = d.external_id "
        "ORDER BY d.full_name")
    drivers = [{"driver_id": r.get("external_id", ""),
                "name": r.get("full_name", ""),
                "route_id": r.get("route_id", ""),
                "telegram_chat_id": r.get("telegram_chat_id", "")} for r in rows]
    scopes = scope_routes(scope)
    if scopes is not None:
        drivers = [d for d in drivers if str(d.get("route_id") or "") in scopes]
    return {"ok": True, "drivers": drivers}


@router.get("/api/drivers/{driver_id}")
async def api_driver_detail(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None:
        if not _driver_in_scope(get_storage(), driver_id, scopes):
            raise HTTPException(404, "haydovchi topilmadi")
    params = _parse_qs(request)
    detail = m.Metrics().driver_detail(driver_id, params)
    if detail is None:
        return {"ok": False, "error": "Haydovchi topilmadi"}
    show = _driver_finance_visible()
    if not show:
        _mask_driver_money(detail.get("driver"))
        detail["fines_total"] = None
    return {"ok": True, "show_driver_salary": show, **detail}


@router.post("/api/drivers")
async def api_driver_create(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    scopes = scope_routes(scope)
    if scopes is not None:
        route = str(payload.get("route_id") or "").strip()
        if route not in scopes:
            raise HTTPException(404, "haydovchi topilmadi")
    return _save_driver(payload, "")


@router.post("/api/drivers/{driver_id}/profile")
async def api_driver_profile(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    payload = await request.json()
    scopes = scope_routes(scope)
    if scopes is not None:
        storage = get_storage()
        if not _driver_in_scope(storage, driver_id, scopes):
            raise HTTPException(404, "haydovchi topilmadi")
        new_route = str(payload.get("route_id") or "").strip()
        if new_route and new_route not in scopes:
            raise HTTPException(404, "haydovchi topilmadi")
    return _save_driver(payload, driver_id)


@router.post("/api/drivers/{driver_id}/photo")
async def api_driver_photo(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    payload = await request.json()
    return _save_photo(driver_id, payload)


@router.get("/api/drivers/{driver_id}/photo")
async def api_driver_photo_get(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    storage = get_storage()
    profile = storage.find("driver_profiles", driver_id=driver_id) or {}
    photo_path = str(profile.get(PHOTO_FIELD) or "")
    if not photo_path:
        raise HTTPException(404, "Rasm topilmadi")
    p = Path(photo_path)
    if not p.exists():
        raise HTTPException(404, "Rasm topilmadi")
    return FileResponse(p)


@router.post("/api/drivers/{driver_id}/document")
async def api_driver_document_save(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    payload = await request.json()
    return _save_document(driver_id, payload)


@router.get("/api/drivers/{driver_id}/document/{kind}")
async def api_driver_document_get(driver_id: str, kind: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    if kind not in DOCUMENT_FIELDS:
        raise HTTPException(404, "Hujjat turi topilmadi")
    storage = get_storage()
    profile = storage.find("driver_profiles", driver_id=driver_id) or {}
    doc_path = str(profile.get(DOCUMENT_FIELDS[kind]) or "")
    if not doc_path:
        raise HTTPException(404, "Hujjat topilmadi")
    p = Path(doc_path)
    if not p.exists():
        raise HTTPException(404, "Hujjat topilmadi")
    return FileResponse(p)


@router.post("/api/drivers/{driver_id}/work-log")
async def api_driver_work_log(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    payload = await request.json()
    return _save_work_log(driver_id, payload)


@router.post("/api/drivers/{driver_id}/work-log-delete")
async def api_driver_work_log_delete(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    payload = await request.json()
    return _delete_work_log(driver_id, payload)


@router.post("/api/drivers/{driver_id}/fine")
async def api_driver_fine(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    payload = await request.json()
    return _save_fine(driver_id, payload)


@router.post("/api/drivers/{driver_id}/notification-test")
async def api_driver_notification_test(driver_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    scopes = scope_routes(scope)
    if scopes is not None and not _driver_in_scope(get_storage(), driver_id, scopes):
        raise HTTPException(404, "haydovchi topilmadi")
    return _test_notification(driver_id)


@router.get("/api/vehicles/{vehicle_id}")
async def api_vehicle_detail(vehicle_id: str, request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    from ...db.storage import get_storage
    params = _parse_qs(request)
    vid = m.Metrics().resolve_vehicle(vehicle_id, params) or vehicle_id
    scopes = scope_routes(scope)
    if scopes is not None:
        row = get_storage().find("vehicles", external_id=vid)
        if not row or str(row.get("route_id") or "") not in scopes:
            raise HTTPException(404, "Avtobus topilmadi")
    detail = m.Metrics().vehicle_detail(vid, params)
    if detail:
        return {"ok": True, **detail}
    raise HTTPException(404, "Avtobus topilmadi")


# ---------------------------------------------------------------------------
# Ichki yordamchilar (eski server.py'dagi logika)
# ---------------------------------------------------------------------------

def _save_driver(payload: dict, driver_id: str = "") -> dict:
    from ...db.models import json_loads
    from ...db.storage import get_storage
    from .. import metrics as m

    did = str(driver_id or payload.get("driver_id") or "").strip()
    if not did or len(did) > 128:
        raise HTTPException(400, "Haydovchi ID kiritilishi kerak")
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")

    existing = storage.find("drivers", external_id=did) or {}
    name = str(payload.get("full_name") or "").strip()[:160] or existing.get("full_name") or did
    storage.save_driver(
        external_id=did, full_name=name,
        tin=str(payload.get("tin") or "").strip()[:32] or existing.get("tin", ""),
        route_id=str(payload.get("route_id") or "").strip()[:128] or existing.get("route_id", ""),
        data=json_loads(existing.get("data")),
    )
    fields = {}
    for key in ("phone", "passport_number", "passport_issued_by", "passport_expiry",
                "license_number", "license_category", "license_expiry",
                "blacklist_reason", "notification_target", "notes"):
        if key in payload:
            fields[key] = str(payload[key] or "").strip()[:500]
    for key in ("rating", "km_rate"):
        if key in payload:
            raw = payload[key]
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                fields[key] = 0.0 if key == "km_rate" else 5.0
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{key} son bo'lishi kerak")
            if not math.isfinite(val):
                raise HTTPException(400, f"{key} qiymati yaroqsiz")
            fields[key] = min(max(val, 0.0), 5.0) if key == "rating" else min(max(val, 0.0), 10_000_000_000.0)
    for key in ("blacklisted", "notification_enabled"):
        if key in payload:
            fields[key] = bool(payload[key])
    storage.save_driver_profile(did, **fields)

    detail = m.Metrics().driver_detail(did, {})
    if detail is None:
        return {"ok": False, "error": "Haydovchi topilmadi"}
    show = _driver_finance_visible()
    if not show:
        _mask_driver_money(detail.get("driver"))
        detail["fines_total"] = None
    return {"ok": True, "show_driver_salary": show, **detail}


def _save_photo(driver_id: str, payload: dict) -> dict:
    from ...db.storage import get_storage
    did = str(driver_id or "").strip()
    if not did or len(did) > 128:
        raise HTTPException(400, "Haydovchi ID kiritilishi kerak")
    data_url = str(payload.get("data_url") or "")
    match = re.fullmatch(r"data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)", data_url)
    if not match or match.group(1).lower() not in DOCUMENT_TYPES:
        raise HTTPException(400, "Faqat JPG, PNG yoki WEBP rasm yuklang")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Rasm ma'lumoti buzilgan") from exc
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Rasm 5 MB dan kichik bo'lishi kerak")
    ext = _image_ext(raw)
    if not ext:
        raise HTTPException(400, "Fayl rasm emas (JPG/PNG/WEBP bo'lishi kerak)")
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", did)[:80] or "driver"
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    target = PHOTO_DIR / f"{safe_id}_photo{ext}"
    target.write_bytes(raw)
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    storage.save_driver_profile(did, **{PHOTO_FIELD: target.as_posix()})
    return {"ok": True, "url": f"/api/drivers/{did}/photo"}


def _save_document(driver_id: str, payload: dict) -> dict:
    from ...db.storage import get_storage
    did = str(driver_id or "").strip()
    if not did or len(did) > 128:
        raise HTTPException(400, "Haydovchi ID kiritilishi kerak")
    kind = str(payload.get("kind") or "").strip()
    if kind not in DOCUMENT_FIELDS:
        raise HTTPException(400, "Hujjat turi noto'g'ri")
    data_url = str(payload.get("data_url") or "")
    match = re.fullmatch(r"data:([\w/+.-]+);base64,([A-Za-z0-9+/=\s]+)", data_url)
    if not match or match.group(1).lower() not in DOCUMENT_TYPES:
        raise HTTPException(400, "Faqat JPG, PNG yoki WEBP rasm yuklang")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Rasm ma'lumoti buzilgan") from exc
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Rasm 5 MB dan kichik bo'lishi kerak")
    ext = _image_ext(raw)
    if not ext:
        raise HTTPException(400, "Fayl rasm emas (JPG/PNG/WEBP bo'lishi kerak)")
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", did)[:80] or "driver"
    DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    target = DOCUMENT_DIR / f"{safe_id}_{kind}{ext}"
    target.write_bytes(raw)
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    storage.save_driver_profile(did, **{DOCUMENT_FIELDS[kind]: target.as_posix()})
    return {"ok": True, "document": kind, "url": f"/api/drivers/{did}/document/{kind}"}


def _save_work_log(driver_id: str, payload: dict) -> dict:
    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    log_date = str(payload.get("date") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(log_date)
    except ValueError:
        raise HTTPException(400, "Sana YYYY-MM-DD formatida bo'lishi kerak")
    try:
        distance = float(payload.get("distance_km") or 0)
        trips = int(payload.get("trip_count") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Km yoki reys soni noto'g'ri")
    if not math.isfinite(distance) or distance < 0 or distance > 1_000_000:
        raise HTTPException(400, "Km qiymati yaroqsiz")
    if trips < 0 or trips > 100_000:
        raise HTTPException(400, "Reys soni yaroqsiz")
    vehicle_id = str(payload.get("vehicle_id") or "").strip()[:128]
    note = str(payload.get("note") or "").strip()[:500]
    # Natural kalit (date, driver_id, vehicle_id) — upsert: qayta kiritilsa
    # yangilanadi (eski server.py bilan bir xil xatti-harakat).
    storage.save_driver_work_log(
        log_date, driver_id, vehicle_id, distance, trips, note)
    return {"ok": True}


def _delete_work_log(driver_id: str, payload: dict) -> dict:
    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    log_date = str(payload.get("date") or "").strip()
    if not log_date:
        raise HTTPException(400, "Sana kerak")
    try:
        date.fromisoformat(log_date)
    except ValueError:
        raise HTTPException(400, "Sana YYYY-MM-DD formatida bo'lishi kerak")
    vehicle_id = str(payload.get("vehicle_id") or "").strip()[:128]
    if not storage.delete_driver_work_log(log_date, driver_id, vehicle_id):
        raise HTTPException(400, "Bunday km qaydi topilmadi")
    return {"ok": True}


def _save_fine(driver_id: str, payload: dict) -> dict:
    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    fine_date = str(payload.get("date") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(fine_date)
        amount = float(payload.get("amount") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Sana yoki jarima summasi noto'g'ri")
    if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000_000_000:
        raise HTTPException(400, "Jarima summasi yaroqsiz")
    reason = str(payload.get("reason") or "").strip()[:500]
    return {"ok": bool(storage.add_driver_fine(driver_id, fine_date, amount,
                                               reason))}


def _test_notification(driver_id: str) -> dict:
    from ...db.storage import get_storage
    from ...notifications.telegram import send_message
    storage = get_storage()
    profile = storage.find("driver_profiles", driver_id=driver_id) or {}
    chat_id = profile.get("telegram_chat_id")
    if not chat_id:
        return {"ok": False, "error": "Haydovchi Telegram'ga ulanmagan"}
    base = storage.find("drivers", external_id=driver_id) or {}
    name = base.get("full_name") or driver_id
    text = f"🔔 Test bildirishnoma: {name} uchun tizim ishlayapti."
    try:
        send_message(str(chat_id), text)
        return {"ok": True, "message": "Test xabar yuborildi"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
