"""Texnik xizmat jurnali — avtobuslar servis tarixi va eslatmalar."""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import Scope, audit_event, rate_limit_post, scope_routes
from ...core.vehicle_service import (
    DEFAULT_DAYS_AHEAD, DEFAULT_KM_AHEAD, SERVICE_TYPES, collect_due)
from ...utils.logger import get_logger

router = APIRouter(tags=["service"])
log = get_logger("bm_automation.dashboard.service")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_ODOMETER = 5_000_000
_MAX_COST = 10_000_000_000.0


def _today() -> date:
    return date.today()


def _parse_day(raw: object, field: str, *, optional: bool = False) -> str:
    text = str(raw or "").strip()
    if not text:
        if optional:
            return ""
        raise HTTPException(400, f"{field} kiritilishi kerak")
    if not _DATE_RE.match(text):
        raise HTTPException(400, f"{field} YYYY-MM-DD shaklida bo'lishi kerak")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"{field} sana noto'g'ri") from exc
    return text


def _int_value(raw: object, field: str, *, default: int = 0) -> int:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{field} son bo'lishi kerak") from exc
    if value < 0 or value > _MAX_ODOMETER:
        raise HTTPException(400, f"{field} 0 dan {_MAX_ODOMETER} gacha bo'lishi kerak")
    return value


def _float_value(raw: object, field: str, *, default: float = 0.0) -> float:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{field} son bo'lishi kerak") from exc
    if not math.isfinite(value) or value < 0 or value > _MAX_COST:
        raise HTTPException(400, f"{field} qiymati yaroqsiz")
    return value


def _row_out(row: dict) -> dict:
    try:
        row_id = int(row.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    return {
        "id": row_id,
        "vehicle_id": str(row.get("vehicle_id") or ""),
        "route_id": str(row.get("route_id") or ""),
        "service_date": str(row.get("service_date") or ""),
        "service_type": str(row.get("service_type") or ""),
        "odometer": int(row.get("odometer") or 0),
        "cost": float(row.get("cost") or 0),
        "vendor": str(row.get("vendor") or ""),
        "note": str(row.get("note") or ""),
        "next_service_date": str(row.get("next_service_date") or ""),
        "next_service_km": int(row.get("next_service_km") or 0),
        "created_by": str(row.get("created_by") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def _vehicle_or_404(vehicle_id: str, scope: dict) -> dict:
    from ...db.storage import get_storage
    storage = get_storage()
    raw = str(vehicle_id or "").strip()[:128]
    if not raw:
        raise HTTPException(404, "Avtobus topilmadi")
    row = storage.find("vehicles", external_id=raw)
    if not row:
        # Ekranda platenoma/garaj raqami bilan ochilgan bo'lishi mumkin.
        from .. import metrics as m
        resolved = m.Metrics().resolve_vehicle(raw)
        if resolved:
            row = storage.find("vehicles", external_id=resolved)
    if not row:
        raise HTTPException(404, "Avtobus topilmadi")
    scopes = scope_routes(scope)
    if scopes is not None and str(row.get("route_id") or "") not in scopes:
        raise HTTPException(404, "Avtobus topilmadi")
    return row


@router.get("/api/service/upcoming")
def api_service_upcoming(request: Request, scope: dict = Depends(Scope(min_role=2))):
    """Yaqinlashgan yoki o'tib ketgan xizmatlar (barcha avtobuslar)."""
    params = request.query_params
    try:
        days_ahead = max(0, min(int(params.get("days") or DEFAULT_DAYS_AHEAD),
                                365))
    except (TypeError, ValueError):
        days_ahead = DEFAULT_DAYS_AHEAD
    try:
        km_ahead = max(0, min(int(params.get("km") or DEFAULT_KM_AHEAD),
                              100_000))
    except (TypeError, ValueError):
        km_ahead = DEFAULT_KM_AHEAD
    scopes = scope_routes(scope)
    items = collect_due(days_ahead, km_ahead,
                        None if scopes is None else sorted(scopes))
    return {"ok": True, "items": items[:200],
            "days": days_ahead, "km": km_ahead}


@router.get("/api/vehicles/{vehicle_id}/service")
def api_vehicle_service_list(vehicle_id: str, request: Request,
                             scope: dict = Depends(Scope(min_role=2))):
    from ...db.storage import get_storage
    vehicle = _vehicle_or_404(vehicle_id, scope)
    vid = str(vehicle.get("external_id") or "")
    rows = get_storage().vehicle_service_logs(vid)
    logs = [_row_out(r) for r in rows]
    odometer = max([x["odometer"] for x in logs] or [0])
    return {
        "ok": True,
        "vehicle_id": vid,
        "plate_number": str(vehicle.get("plate_number") or vid),
        "odometer": odometer,
        "types": list(SERVICE_TYPES),
        "logs": logs,
    }


@router.post("/api/vehicles/{vehicle_id}/service")
async def api_vehicle_service_add(vehicle_id: str, request: Request,
                                  scope: dict = Depends(Scope(min_role=2))):
    from ...db.storage import get_storage
    if not rate_limit_post("vehicle-service"):
        raise HTTPException(429, "Juda tez — bir soniyadan keyin qayta urinib ko'ring")
    vehicle = _vehicle_or_404(vehicle_id, scope)
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Noto'g'ri so'rov") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "Noto'g'ri so'rov")

    service_date = _parse_day(
        payload.get("service_date") or _today().isoformat(), "Xizmat sanasi")
    next_date = _parse_day(payload.get("next_service_date"), "Keyingi sana",
                           optional=True)
    if next_date and next_date < service_date:
        raise HTTPException(
            400, "Keyingi xizmat sanasi xizmat sanasidan oldin bo'lmasligi kerak")
    odometer = _int_value(payload.get("odometer"), "Odometr")
    next_km = _int_value(payload.get("next_service_km"), "Keyingi km")
    if next_km and odometer and next_km < odometer:
        raise HTTPException(
            400, "Keyingi xizmat km odometrdan kichik bo'lmasligi kerak")

    values = {
        "vehicle_id": str(vehicle.get("external_id") or ""),
        "route_id": str(vehicle.get("route_id") or ""),
        "service_date": service_date,
        "service_type": str(payload.get("service_type") or "").strip()[:80],
        "odometer": odometer,
        "cost": _float_value(payload.get("cost"), "Narx"),
        "vendor": str(payload.get("vendor") or "").strip()[:160],
        "note": str(payload.get("note") or "").strip()[:1000],
        "next_service_date": next_date,
        "next_service_km": next_km,
    }
    storage = get_storage()
    if not storage.enabled:
        raise HTTPException(400, "DB rejimi o'chirilgan")
    actor = str(scope.get("username") or "")
    values["created_by"] = actor
    values["updated_by"] = actor
    if not storage.add_vehicle_service_log(values):
        raise HTTPException(500, "Yozuv saqlanmadi")
    audit_event("vehicle_service.add", request, user=actor,
                target=values["vehicle_id"],
                detail=f"{service_date} {values['service_type']}".strip())
    return {"ok": True, "vehicle_id": values["vehicle_id"],
            "service_date": service_date}


@router.delete("/api/vehicles/{vehicle_id}/service/{row_id}")
def api_vehicle_service_delete(vehicle_id: str, row_id: int, request: Request,
                               scope: dict = Depends(Scope(min_role=2))):
    from ...db.storage import get_storage
    if not rate_limit_post("vehicle-service"):
        raise HTTPException(429, "Juda tez — bir soniyadan keyin qayta urinib ko'ring")
    vehicle = _vehicle_or_404(vehicle_id, scope)
    vid = str(vehicle.get("external_id") or "")
    storage = get_storage()
    row = storage.find("vehicle_service_logs", id=row_id, vehicle_id=vid)
    if not row:
        raise HTTPException(404, "Yozuv topilmadi")
    if not storage.delete_vehicle_service_log(row_id, vid):
        raise HTTPException(500, "Yozuv o'chirilmadi")
    audit_event("vehicle_service.delete", request,
                user=str(scope.get("username") or ""), target=vid,
                detail=f"id={row_id}")
    return {"ok": True}
