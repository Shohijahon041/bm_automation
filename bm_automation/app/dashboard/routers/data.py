"""Data router — summary, routes, month, attendance, brutto, rejects, electricity, export, check."""

from __future__ import annotations

import math
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response as FastResponse, StreamingResponse

from ..deps import Scope, force_route, scope_routes

router = APIRouter(tags=["data"])


def _parse_qs(request: Request) -> dict:
    return dict(request.query_params)


def _report_month(params: dict) -> date | None:
    month = (params.get("month") or "").strip()
    if month:
        try:
            return datetime.strptime(month[:7], "%Y-%m").date()
        except ValueError:
            return None
    for key in ("from", "to"):
        val = (params.get(key) or "").strip()
        if val:
            try:
                return date.fromisoformat(val[:10])
            except ValueError:
                return None
    return None


_FIN_FIELDS = ("skm", "km_rate", "tariff_no_vat", "tariff_vat")


def _strip_finance(items: list | None, scope: dict) -> list | None:
    if not items:
        return items
    if not scope or scope.get("is_admin"):
        return items
    from ..deps import _ROLE_RANK
    rank = _ROLE_RANK.get(scope.get("role") or "VIEWER", 0)
    if rank >= 2:
        return items
    return [{k: v for k, v in item.items() if k not in _FIN_FIELDS}
            if isinstance(item, dict) else item for item in items]


@router.get("/api/summary")
def api_summary(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/summary", params)
    rep = m.summary(params)
    if isinstance(rep, dict) and rep.get("routes"):
        rep["routes"] = _strip_finance(rep["routes"], scope)
    return rep


@router.get("/api/routes")
def api_routes(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    routes = m.route_options()
    if not scope.get("is_admin"):
        allowed = set(scope.get("routes") or [])
        routes = [r for r in routes if str(r.get("id") or "") in allowed]
    routes = _strip_finance(routes, scope)
    return {"ok": True, "routes": routes}


@router.get("/api/month")
def api_month(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/month", params)
    return {"ok": True, **m.Metrics().monthly(params)}


@router.get("/api/attendance")
def api_attendance(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/attendance", params)
    return m.attendance_calendar(params)


@router.get("/api/brutto")
def api_brutto(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/brutto", params)
    return m.brutto(params)


@router.get("/api/brutto/master")
def api_brutto_master(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    from ...brutto.master import master_report
    params = _parse_qs(request)
    params = force_route(scope, "/api/brutto", params)
    br = m.brutto(params)
    agg = br.get("agg") or {}
    mode = params.get("mode", "multiplicative")
    if mode not in ("additive", "multiplicative"):
        mode = "multiplicative"
    rep = master_report(
        skm=float(br.get("skm") or br.get("base_skm") or 0),
        lr=float(agg.get("lr") or 0),
        lf=float(agg.get("lf") or 0),
        kamal=int(agg.get("kamal") or 0),
        kstjb=int(agg.get("kstjb") or 0),
        kmaq=int(agg.get("kmaq") or 0),
        report_month=_report_month(params),
        mode=mode,
    )
    rep["ok"] = True
    return rep


@router.get("/api/rejects")
def api_rejects(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/rejects", params)
    rep = m.Metrics().not_accepted_km_report(m.parse_filters(params))
    rep["ok"] = True
    return rep


@router.get("/api/rejects/tariff")
def api_rejects_tariff(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import route_tariff
    params = _parse_qs(request)
    params = force_route(scope, "/api/rejects/tariff", params)
    rid = (params.get("route") or "").strip()
    return {"ok": True, "route_id": rid, **route_tariff(rid)}


@router.post("/api/rejects/tariff")
async def api_rejects_tariff_set(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import set_route_tariff
    payload = await request.json()
    rid = str(payload.get("route_id") or "").strip()
    if not rid:
        return {"ok": False, "error": "route_id kerak"}
    scopes = scope_routes(scope)
    if scopes is not None and rid not in scopes:
        raise HTTPException(404, "yo'nalish topilmadi")
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


@router.get("/api/electricity")
def api_electricity(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    params = _parse_qs(request)
    params = force_route(scope, "/api/electricity", params)
    return {"ok": True, **m.Metrics().electricity_report(params)}


@router.get("/api/electricity/price")
def api_electricity_price(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import elec_price, ELEC_KWH_PER_KM
    return {"ok": True, "rate": elec_price(), "kwh_per_km": ELEC_KWH_PER_KM}


@router.post("/api/electricity/price")
async def api_electricity_price_set(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import set_elec_price, ELEC_KWH_PER_KM
    payload = await request.json()
    try:
        rate = float(payload.get("rate"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Narx son bo'lishi kerak"}
    if not math.isfinite(rate) or rate < 0:
        return {"ok": False, "error": "Narx manfiy yoki yaroqsiz"}
    rate = min(rate, 100_000_000_000.0)
    set_elec_price(rate)
    return {"ok": True, "rate": rate, "kwh_per_km": ELEC_KWH_PER_KM}


@router.get("/api/check")
def api_check(request: Request, scope: dict = Depends(Scope())):
    # SYNC endpoint: run_check BM API + Playwright (sync) ishlatadi —
    # async loop ichida ishlasa "Playwright Sync API inside the asyncio loop"
    # xatosi beriladi va 500 qaytadi (frontend JSON kutadi).
    from .. import check as ck
    params = _parse_qs(request)
    params = force_route(scope, "/api/check", params)
    try:
        return ck.run_check(params)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Tekshiruv bajarilmadi: {exc}"}


@router.get("/api/export")
def api_export(request: Request, scope: dict = Depends(Scope())):
    # SYNC endpoint: run_export BM API (requests) ni bloklovchi chaqiradi.
    from .. import export as ex
    from ..deps import _ROLE_RANK
    params = _parse_qs(request)
    params = force_route(scope, "/api/export", params)
    is_admin = bool(scope.get("is_admin"))
    rank = _ROLE_RANK.get(scope.get("role") or "VIEWER", 0)
    allow_financial = is_admin or rank >= 2
    if not is_admin:
        escope = (params.get("scope") or "trips").lower()
        if escope in ("settings", "tariffs"):
            raise HTTPException(403, "Sizda bu bo'limga ruxsat yo'q")
    try:
        result = ex.run_export(params, allow_financial=allow_financial)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"ok": False, "error": f"Eksport bajarilmadi: {exc}"},
            status_code=500)
    if isinstance(result, dict) and result.get("data"):
        return FastResponse(
            content=result["data"],
            media_type=result.get("content_type", "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{result.get("filename", "export.xlsx")}"'},
        )
    if isinstance(result, dict) and result.get("error"):
        return JSONResponse(
            {"ok": False, "error": result["error"]},
            status_code=int(result.get("status") or 400))
    return result


@router.get("/api/salary")
def api_salary(request: Request, scope: dict = Depends(Scope())):
    from ..export import salary_export, salary_filename, content_type
    params = _parse_qs(request)
    params = force_route(scope, "/api/salary", params)
    from_date = params.get("from", "")
    to_date = params.get("to", "")
    if not from_date or not to_date:
        raise HTTPException(400, "from va to parametrlari kerak")
    scopes = scope_routes(scope)
    data = salary_export(from_date, to_date,
                         route=" ".join(sorted(scopes)) if scopes is not None else "")
    name = salary_filename(from_date, to_date)
    return FastResponse(
        content=data,
        media_type=content_type("xlsx"),
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/api/route-skm")
def api_route_skm(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import route_skm
    params = _parse_qs(request)
    params = force_route(scope, "/api/route-skm", params)
    rid = (params.get("route") or "").strip()
    return {"ok": True, "route_id": rid, "skm": route_skm(rid)}


@router.post("/api/route-skm")
async def api_route_skm_set(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import set_route_skm
    payload = await request.json()
    rid = str(payload.get("route_id") or "").strip()
    if not rid:
        return {"ok": False, "error": "route_id kerak"}
    scopes = scope_routes(scope)
    if scopes is not None and rid not in scopes:
        raise HTTPException(404, "yo'nalish topilmadi")
    try:
        val = float(payload.get("skm"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "SKM son bo'lishi kerak"}
    if not math.isfinite(val) or val < 0:
        return {"ok": False, "error": "SKM manfiy yoki yaroqsiz"}
    val = min(val, 100_000_000_000_000.0)
    saved = set_route_skm(rid, val)
    return {"ok": True, "route_id": rid, "skm": saved}


@router.get("/api/kmrate")
def api_kmrate(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import route_km
    params = _parse_qs(request)
    params = force_route(scope, "/api/kmrate", params)
    rid = (params.get("route") or "").strip()
    return {"ok": True, "route_id": rid, "km_rate": route_km(rid)}


@router.post("/api/kmrate")
async def api_kmrate_set(request: Request, scope: dict = Depends(Scope())):
    from ...core.bot_settings import set_route_km
    payload = await request.json()
    route_id = str(payload.get("route_id") or "").strip()
    if not route_id:
        return {"ok": False, "error": "route_id kerak"}
    scopes = scope_routes(scope)
    if scopes is not None and route_id not in scopes:
        raise HTTPException(404, "yo'nalish topilmadi")
    try:
        km_rate = float(payload.get("km_rate"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Noto'g'ri km narxi"}
    if not math.isfinite(km_rate) or km_rate < 0:
        return {"ok": False, "error": "km narxi manfiy yoki yaroqsiz"}
    km_rate = min(km_rate, 10_000_000_000.0)
    set_route_km(route_id, km_rate)
    return {"ok": True, "updated": 0, "route_id": route_id, "km_rate": km_rate}


@router.get("/api/appeals")
def api_appeals(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    from ...db.storage import get_storage
    params = _parse_qs(request)
    params = force_route(scope, "/api/appeals", params)
    report = m.Metrics().appeals_report(params)
    scopes = scope_routes(scope)
    if scopes is not None:
        storage = get_storage()
        report["appeals"] = [
            a for a in report["appeals"]
            if _driver_in_scope(storage, str(a.get("driver_id") or ""), scopes)
        ]
    return {"ok": True, **report}


@router.post("/api/appeals")
async def api_appeals_post(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    payload = await request.json()
    storage = get_storage()
    if not storage.enabled:
        return JSONResponse({"ok": False, "error": "DB rejimi o'chirilgan"}, status_code=400)
    action = str(payload.get("action") or "add").strip()
    try:
        aid = int(payload.get("id") or 0)
    except (TypeError, ValueError):
        aid = 0
    scopes = scope_routes(scope)

    if scopes is not None and action in ("update", "reply", "delete"):
        if not aid:
            return JSONResponse({"ok": False, "error": "Murojaat ID xato"}, status_code=400)
        rows = storage.query(
            "SELECT driver_id FROM driver_appeals WHERE id = " + storage.db.ph,
            (str(aid),), limit=1)
        driver_id = str(rows[0].get("driver_id") or "") if rows else ""
        if not _driver_in_scope(storage, driver_id, scopes):
            return JSONResponse({"ok": False, "error": "Murojaat topilmadi"}, status_code=404)

    if action in ("update", "reply"):
        if not aid:
            return JSONResponse({"ok": False, "error": "Murojaat ID xato"}, status_code=400)
        if action == "reply":
            result = storage.appeal_update(
                aid, reply=str(payload.get("reply") or "").strip(),
                status=str(payload.get("status") or "JAVOB_YOZILDI").upper())
        else:
            result = storage.appeal_update(
                aid, title=payload.get("title"), text=payload.get("text"),
                status=payload.get("status"), reply=payload.get("reply"))
        return {"ok": bool(result), "id": aid}
    elif action == "delete":
        if not aid:
            return JSONResponse({"ok": False, "error": "Murojaat ID xato"}, status_code=400)
        return {"ok": storage.appeal_delete(aid), "id": aid}
    else:
        did = str(payload.get("driver_id") or "").strip()
        if not did:
            return JSONResponse({"ok": False, "error": "Haydovchi ko'rsatilmagan"}, status_code=400)
        if scopes is not None and not _driver_in_scope(storage, did, scopes):
            return JSONResponse({"ok": False, "error": "Haydovchi topilmadi"}, status_code=404)
        new_id = storage.appeal_add(
            did, title=str(payload.get("title") or "").strip(),
            text=str(payload.get("text") or "").strip(),
            status=str(payload.get("status") or "YANGI").upper())
        return {"ok": bool(new_id), "id": new_id}


def _driver_in_scope(storage, driver_id: str, scopes: set) -> bool:
    if not driver_id:
        return False
    row = storage.find("drivers", external_id=driver_id)
    return bool(row and str(row.get("route_id") or "") in scopes)
