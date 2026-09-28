"""BM sayt ma'lumotlari ↔ DB solishtirish (dashboard /api/check).

Hisob-kitoblar yagona manbasi — brutto-route (`GET /gross/route`): saytdagi
`distanceFact`/`tripFact`/`workingDay` summalari `route_daily` jadvalidagi
qiymatlar bilan taqqoslanadi. Farq ko'rsatiladi.

Har profil o'z tokeni bilan ishlaydi (profileId bo'lsa `login_by_profile`),
xuddi `sync_all_profiles` kabi.
"""

from __future__ import annotations

import datetime as _dt
from datetime import date

from ..api.client import BMClient
from ..core.profiles import all_profiles
from ..db.storage import get_storage
from ..repositories.gross_repo import GrossRepository


def _route_list(route: str) -> list:
    """Tanlangan route; bo'sh bo'lsa profiles.json'dagi barcha firmalar."""
    if route:
        return [route]
    return [str(p.get("routeVariantId") or "").strip()
            for p in all_profiles()
            if str(p.get("routeVariantId") or "").strip()]


def _db_counts(storage, rid: str, date_str: str) -> dict:
    ph = storage.db.ph

    def one(sql: str, params: tuple, default=0):
        rows = storage.query(sql, params, limit=1)
        return rows[0][next(iter(rows[0]))] if rows else default

    rd = "SELECT COALESCE(SUM({c}), 0) AS n FROM route_daily " \
         f"WHERE date = {ph} AND route_id = {ph}"
    return {
        "db_trips": int(one(rd.format(c="trip_fact"), (date_str, rid)) or 0),
        "db_km": round(float(one(rd.format(c="distance_fact"), (date_str, rid)) or 0), 2),
        "db_work_days": int(one(rd.format(c="working_day"), (date_str, rid)) or 0),
        "db_waybills": int(one(
            "SELECT COUNT(*) AS n FROM route_daily "
            f"WHERE date = {ph} AND route_id = {ph}", (date_str, rid)) or 0),
    }


def _check_one_day(client, gross_repo, storage, rid: str, name: str,
                   route_name: str, date_str: str, profiles: dict,
                   main_access: str, main_refresh: str) -> dict:
    """Bitta kun uchun sayt ↔ DB solishtirish."""

    def use_main() -> None:
        client.access_token = main_access
        client.refresh_token = main_refresh
        client.session.headers["Authorization"] = f"Bearer {main_access}"

    pid = str(profiles.get(rid, {}).get("profileId") or "").strip()
    try:
        if pid:
            client.login_for_profile(pid, fallback_to_main=True)
        else:
            use_main()
    except Exception as exc:  # noqa: BLE001
        return {
            "route_id": rid, "name": name, "route_name": route_name,
            "date": date_str,
            "site": {"trips": 0, "waybills": 0, "km": 0.0, "working_days": 0,
                     "error": f"token: {exc}"},
            "db": {}, "diff": {}, "status": "ERROR",
        }

    site = {"trips": 0, "waybills": 0, "km": 0.0, "working_days": 0,
            "error": ""}
    try:
        data = gross_repo.route(rid, date_str, date_str)
        dates = data.get("dates") or {}
        vehicles = [v for info in dates.values()
                    for v in (info.get("vehicles") or [])]
        site["waybills"] = len(vehicles)
        site["trips"] = int(data.get("tripFactSum") or sum(
            int(v.get("tripFact") or 0) for v in vehicles) or 0)
        site["km"] = round(float(data.get("distanceFactSum") or sum(
            float(v.get("distanceFact") or 0) for v in vehicles) or 0), 2)
        site["working_days"] = int(data.get("workingDaySum") or sum(
            int(v.get("workingDay") or 0) for v in vehicles) or 0)
    except Exception as exc:  # noqa: BLE001
        from ..db import sync as db_sync
        if not db_sync._not_found(exc):
            site["error"] = f"gross/route: {exc}"

    dbc = _db_counts(storage, rid, date_str)
    diff = {
        "trips": site["trips"] - dbc["db_trips"],
        "waybills": site["waybills"] - dbc["db_waybills"],
        "km": round(site["km"] - dbc["db_km"], 2),
        "working_days": site["working_days"] - dbc["db_work_days"],
    }
    if site["error"]:
        status = "ERROR"
    elif any(diff.values()):
        status = "DIFF"
    else:
        status = "OK"
    return {
        "route_id": rid,
        "name": name,
        "route_name": route_name,
        "date": date_str,
        "site": site,
        "db": dbc,
        "diff": diff,
        "status": status,
    }


def run_check(filters: dict) -> dict:
    """Brutto-route (site) ↔ route_daily (DB) solishtirish natijasi.

    Bitta kun yoki kunlar oralig'i (from/to) uchun tekshiruv.
    Agar from/to berilgan bo'lsa, har bir kun uchun alohida tekshiruv
    bajariladi va umumiy natija qaytariladi.
    """
    from_str = str(filters.get("from") or "").strip()
    to_str = str(filters.get("to") or "").strip()
    date_str = str(filters.get("date") or date.today().isoformat())
    route = str(filters.get("route") or "").strip()
    rids = _route_list(route)

    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}

    client = BMClient()
    client.login()
    gross_repo = GrossRepository(client)

    main_access = client.access_token
    main_refresh = client.refresh_token

    profiles = {str(p.get("routeVariantId") or "").strip(): p
                for p in all_profiles()
                if str(p.get("routeVariantId") or "").strip()}

    # Agar from/to berilgan bo'lsa — kunlar oralig'ini tekshirish
    if from_str or to_str:
        try:
            d_from = _dt.date.fromisoformat(from_str) if from_str else _dt.date.today()
            d_to = _dt.date.fromisoformat(to_str) if to_str else _dt.date.today()
        except ValueError:
            return {"ok": False, "error": "Sana formati noto'g'ri (YYYY-MM-DD)"}

        all_results = []
        summary = {"total_trips_diff": 0, "total_km_diff": 0.0,
                   "total_waybills_diff": 0, "days_checked": 0, "diff_days": 0}
        cur = d_from
        while cur <= d_to:
            ds = cur.isoformat()
            for rid in rids:
                prof = profiles.get(rid, {})
                name = str(prof.get("name") or rid)
                route_name = str(prof.get("routeName") or "")
                result = _check_one_day(client, gross_repo, storage, rid, name,
                                        route_name, ds, profiles,
                                        main_access, main_refresh)
                all_results.append(result)
                if result["status"] != "ERROR":
                    summary["total_trips_diff"] += result["diff"].get("trips", 0)
                    summary["total_km_diff"] += result["diff"].get("km", 0)
                    summary["total_waybills_diff"] += result["diff"].get("waybills", 0)
                    summary["days_checked"] += 1
                    if result["status"] == "DIFF":
                        summary["diff_days"] += 1
            cur += _dt.timedelta(days=1)

        summary["total_km_diff"] = round(summary["total_km_diff"], 2)
        ok = not any(r["status"] == "ERROR" for r in all_results) and \
             summary["total_trips_diff"] == 0 and summary["total_km_diff"] == 0
        return {"ok": True, "from": from_str or d_from.isoformat(),
                "to": to_str or d_to.isoformat(),
                "route": route, "results": all_results, "summary": summary}

    # Bitta kun — avvalgi mantiq
    return _check_one_day_result(client, gross_repo, storage, rids, profiles,
                                 date_str, route, main_access, main_refresh)


def _check_one_day_result(client, gross_repo, storage, rids, profiles,
                          date_str, route, main_access, main_refresh) -> dict:
    """Bitta kun uchun natija (avvalgi run_check mantiqi)."""
    results = []
    for rid in rids:
        prof = profiles.get(rid, {})
        name = str(prof.get("name") or rid)
        route_name = str(prof.get("routeName") or "")
        result = _check_one_day(client, gross_repo, storage, rid, name,
                                route_name, date_str, profiles,
                                main_access, main_refresh)
        results.append(result)
    return {"ok": True, "date": date_str, "route": route, "results": results}
