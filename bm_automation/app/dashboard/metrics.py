"""Dashboard metrikalari — ma'lumotlar bazasi (Storage) asosida.

Barcha ko'rsatkichlar DB'dan hisoblanadi:

- **today** — kunlik KPI (avtobuslar, reyslar, statuslar);
- **routes** — har bir yo'nalish: planned/actual/accepted/not_accepted/perf%;
- **vehicles** — har bir avtobus: status, trips, issues, last activity;
- **drivers** — har bir haydovchi: trips, working days, issues, attendance;
- **system** — BM API, Database, Telegram, Scheduler, last sync, last error.

DB o'chirilgan bo'lsa `ok=False` va izoh bilan qaytadi — server yiqilmaydi.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

from ..config.settings import km_rate_for, telegram_settings
from ..db.models import TripStatus, now_utc
from ..db.storage import get_storage
from ..utils.km import to_float as _number
from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard")

# status -> o'zbekcha izoh
STATUS_LABELS = {
    "ACCEPTED": "Qabul qilingan",
    "NOT_ACCEPTED": "Qabul qilinmagan",
    "PENDING_ACCESS": "Pending",
    "APPROVED": "Tasdiqlangan",
    "REJECTED": "Rejected",
    "ZERO_MILEAGE": "Zero mileage",
}

# "bajarilgan" deb hisoblanadigan statuslar (actual_time ham hisobga olinadi)
_DONE_STATUSES = ("ACCEPTED", "APPROVED")
_ISSUE_STATUSES = ("REJECTED", "NOT_ACCEPTED")

# Haydovchi oyligidan ushlab qolinadigan daromad solig'i stavkasi (12%)
TAX_RATE = 0.12


def _last_active_date() -> str:
    """Eng oxirgi reys bo'lgan sana — bugun bo'sh bo'lsa avtomatik tushadi."""
    try:
        from ..db.storage import get_storage
        s = get_storage()
        rows = s.query("SELECT MAX(date) AS d FROM trips", limit=1)
        if rows and rows[0].get("d"):
            return str(rows[0]["d"])
    except Exception:
        pass
    return date.today().isoformat()


def parse_filters(params: dict | None) -> dict:
    """So'rov parametrlaridan filterlar dict'ini yig'adi."""
    params = params or {}
    p = {k: (v if isinstance(v, str) else (v[0] if v else "")) for k, v in params.items()}
    filters = {
        "date": (p.get("date") or "").strip(),
        "from": (p.get("from") or "").strip(),
        "to": (p.get("to") or "").strip(),
        "route": (p.get("route") or "").strip(),
        "vehicle": (p.get("vehicle") or "").strip(),
        "driver": (p.get("driver") or "").strip(),
        "status": (p.get("status") or "").strip().upper(),
    }
    if not filters["date"] and not filters["from"]:
        filters["date"] = _last_active_date()
    if not filters["from"] and filters["date"]:
        filters["from"] = filters["date"]
    if not filters["to"]:
        filters["to"] = filters["from"] or filters["date"] or _last_active_date()
    for _k, _v in p.items():
        if _k not in filters and _v:
            filters[_k] = _v
    return filters


class Metrics:
    """DB asosidagi dashboard metrikalari."""

    def __init__(self, storage=None):
        self.storage = storage if storage is not None else get_storage()
        self.ph = self.storage.db.ph
        self._route_names_cache: dict | None = None
        self._companies_cache: dict | None = None

    # --------------------------------------------------------------- generic

    def _count(self, table: str, where: str = "", params: tuple = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {table}"
        if where:
            sql += f" WHERE {where}"
        rows = self.storage.query(sql, params, limit=1)
        return rows[0]["n"] if rows else 0

    def _route_filter(self, value: str) -> tuple[str, list]:
        """route filter: bitta yoki bir nechta id (IN (...)) -> (sql, params)."""
        ids = [t.strip() for t in str(value or "").replace(",", " ").split()
               if t.strip()]
        if not ids:
            return "", []
        ph = self.ph
        return f"route_id IN ({','.join([ph] * len(ids))})", ids

    def _trip_where(self, f: dict, prefix: str = "") -> tuple[str, list]:
        clauses, params = [], []
        if f.get("from"):
            clauses.append(f"{prefix}date >= {self.ph}")
            params.append(f["from"])
        if f.get("to"):
            clauses.append(f"{prefix}date <= {self.ph}")
            params.append(f["to"])
        if f.get("route"):
            rw, rp = self._route_filter(f["route"])
            if rw:
                clauses.append(f"{prefix}{rw}")
                params.extend(rp)
        if f.get("vehicle"):
            clauses.append(f"{prefix}vehicle_id = {self.ph}")
            params.append(f["vehicle"])
        if f.get("driver"):
            clauses.append(f"{prefix}driver_id = {self.ph}")
            params.append(f["driver"])
        if f.get("status"):
            clauses.append(f"{prefix}status = {self.ph}")
            params.append(f["status"])
        return (" AND ".join(clauses), params)

    # ----------------------------------------------------------------- today

    def today(self, f: dict) -> dict:
        where, params = self._trip_where(f)
        ph = self.ph

        # Barcha statuslar BIR so'rovda (ilgari har biri alohida COUNT edi —
        # Supabase session pooler'da har so'rov ~165 ms turadi).
        sql = (
            f"SELECT status, COUNT(*) AS n,"
            f" SUM(CASE WHEN actual_time != '' OR status IN ({ph},{ph})"
            f" THEN 1 ELSE 0 END) AS done"
            f" FROM trips"
            f"{(' WHERE ' + where) if where else ''}"
            f" GROUP BY status"
        )
        rows = self.storage.query(sql, tuple(_DONE_STATUSES) + tuple(params))

        statuses = {st: 0 for st in TripStatus.values()}
        completed = 0
        total_trips = 0
        for r in rows:
            total_trips += int(r["n"] or 0)
            st = r["status"]
            if st in statuses:
                statuses[st] += int(r["n"] or 0)
            if st != TripStatus.ZERO_MILEAGE.value:
                completed += int(r["done"] or 0)

        # Avtobuslar: DB'dagi hammasi (route filter bilan)
        v_where, v_params = self._route_filter(f.get("route"))
        total_buses = self._count("vehicles", v_where, tuple(v_params))
        active_vehicles = 0
        if total_buses or where:
            sql = (
                f"SELECT COUNT(DISTINCT vehicle_id) AS n FROM trips"
                f" WHERE vehicle_id != '' {('AND ' + where) if where else ''}"
            )
            rows = self.storage.query(sql, tuple(params), limit=1)
            active_vehicles = rows[0]["n"] if rows else 0

        # Reja — schedules trip_count yig'indisi (saytdagi "Rejadagi qatnovlar")
        planned = 0
        sf = dict(f)
        sf.pop("status", None)
        s_where, s_params = self._trip_where(sf)
        if s_where:
            prow = self.storage.query(
                "SELECT COALESCE(SUM(trip_count), 0) AS n FROM schedules"
                f" WHERE {s_where} AND trip_count > 0",
                tuple(s_params), limit=1)
            planned = int(prow[0]["n"]) if prow else 0

        accepted = statuses.get("ACCEPTED", 0) + statuses.get("APPROVED", 0)
        not_accepted = statuses.get("NOT_ACCEPTED", 0)
        return {
            "date": f.get("date") or f.get("from", ""),
            "planned": planned,
            "total_buses": total_buses,
            "active_buses": active_vehicles,
            "not_active_buses": max(total_buses - active_vehicles, 0),
            "total_trips": total_trips,
            "completed": completed,
            "accepted": accepted,
            "not_accepted": not_accepted,
            "under_review": accepted + not_accepted,
            "pending": statuses.get("PENDING_ACCESS", 0),
            "approved": statuses.get("APPROVED", 0),
            "rejected": statuses.get("REJECTED", 0),
            "zero_mileage": statuses.get("ZERO_MILEAGE", 0),
            "statuses": statuses,
        }

    def _route_names(self) -> dict:
        """Yo'nalish nomlari: profiles.json routeName ustuvor, DB fallback."""
        if self._route_names_cache is not None:
            return self._route_names_cache
        names: dict[str, str] = {}
        from ..core.profiles import all_profiles
        for p in all_profiles():
            rid = str(p.get("routeVariantId") or "").strip()
            rn = str(p.get("routeName") or "").strip()
            if rid and rn:
                names[rid] = rn
        for r in self.storage.query(
                "SELECT external_id, name FROM routes WHERE entity_type='ROUTE'"):
            eid = str(r["external_id"] or "")
            if eid and eid not in names:
                names[eid] = str(r["name"] or eid)
        self._route_names_cache = names
        return names

    def _companies(self) -> dict:
        """routeVariantId -> firma ma'lumoti (profiles.json)."""
        if self._companies_cache is not None:
            return self._companies_cache
        from ..core.profiles import all_profiles
        out = {}
        for p in all_profiles():
            rid = str(p.get("routeVariantId") or "").strip()
            if rid:
                out[rid] = {
                    "company": str(p.get("name") or ""),
                    "route_name": str(p.get("routeName") or ""),
                }
        self._companies_cache = out
        return out

    # ---------------------------------------------------------------- routes

    def routes(self, f: dict) -> list[dict]:
        where, params = self._trip_where(f)
        w = f" AND {where}" if where else ""
        ph = self.ph
        sql = (
            f"SELECT route_id,"
            f" SUM(CASE WHEN status != {ph} THEN 1 ELSE 0 END) AS planned,"
            f" SUM(CASE WHEN actual_time != '' AND status != {ph}"
            f" THEN 1 ELSE 0 END) AS actual,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS not_accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS rejected,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS zero_mileage"
            f" FROM trips"
            f" WHERE route_id != ''{w}"
            f" GROUP BY route_id ORDER BY route_id"
        )
        rows = self.storage.query(
            sql,
            (TripStatus.ZERO_MILEAGE.value, TripStatus.ZERO_MILEAGE.value)
            + tuple(_DONE_STATUSES)
            + (TripStatus.NOT_ACCEPTED.value, TripStatus.REJECTED.value,
               TripStatus.ZERO_MILEAGE.value)
            + tuple(params),
        )

        # Rejadagi qatnovlar — duty jadvalidagi trip_count yig'indisi
        # (trips'ga qaraganda saytdagi "rejadagi qatnovlar"ga mos keladi).
        plan_where, plan_params = [], []
        if f.get("from"):
            plan_where.append(f"date >= {ph}")
            plan_params.append(f["from"])
        if f.get("to"):
            plan_where.append(f"date <= {ph}")
            plan_params.append(f["to"])
        plans = {}
        if plan_where:
            srows = self.storage.query(
                "SELECT route_id, COALESCE(SUM(trip_count), 0) AS n"
                " FROM schedules"
                f" WHERE {' AND '.join(plan_where)} AND trip_count > 0"
                " GROUP BY route_id",
                tuple(plan_params),
            )
            plans = {r["route_id"]: int(r["n"] or 0) for r in srows}

        # Amalda (bajarilgan qatnovlar) — saytdagi "amalda" raqami
        # trips'dagi actual_time dan emas, BM gross/route hisobotidagi
        # trip_fact dan olindi (route_daily; sayt bilan bir xil tahlil).
        rd_rows: dict[str, dict] = {}
        if plan_where:
            rrows = self.storage.query(
                "SELECT route_id, COALESCE(SUM(trip_plan), 0) AS plan,"
                " COALESCE(SUM(trip_fact), 0) AS fact"
                f" FROM route_daily WHERE {' AND '.join(plan_where)}"
                " GROUP BY route_id",
                tuple(plan_params),
            )
            rd_rows = {r["route_id"]: r for r in rrows}

        names = self._route_names()
        companies = self._companies()

        out = []
        for r in rows:
            rid = r["route_id"]
            rd = rd_rows.get(rid)
            planned = int(rd["plan"] or 0) if rd is not None \
                else (plans.get(rid) or int(r["planned"] or 0))
            actual = int(rd["fact"] or 0) if rd is not None \
                else int(r["actual"] or 0)
            perf = min(round(actual / planned * 100, 1), 100.0) if planned else 0.0
            comp = companies.get(rid, {})
            out.append({
                "route_id": rid,
                "name": names.get(rid, rid),
                "company": comp.get("company", ""),
                "route_name": comp.get("route_name", ""),
                "planned": planned,
                "actual": actual,
                "accepted": int(r["accepted"] or 0),
                "not_accepted": int(r["not_accepted"] or 0),
                "rejected": int(r["rejected"] or 0),
                "zero_mileage": int(r["zero_mileage"] or 0),
                "performance": perf,
            })
        return out

    # -------------------------------------------------------------- vehicles

    def vehicles(self, f: dict) -> list[dict]:
        where, params = self._trip_where(f)
        ph = self.ph

        v_where, v_params = self._route_filter(f.get("route"))
        vehicles = self.storage.query(
            "SELECT external_id, plate_number, garage_number, model, route_id"
            " FROM vehicles"
            f"{(' WHERE ' + v_where) if v_where else ''}"
            " ORDER BY plate_number", tuple(v_params))
        trips_sql = (
            f"SELECT vehicle_id, COUNT(*) AS trips,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS issues,"
            f" MAX(actual_time) AS last_actual, MAX(last_synced_at) AS last_sync"
            f" FROM trips WHERE vehicle_id != ''"
            f"{(' AND ' + where) if where else ''}"
            f" GROUP BY vehicle_id"
        )
        trip_rows = {r["vehicle_id"]: r for r in self.storage.query(
            trips_sql, tuple(_ISSUE_STATUSES) + tuple(params))}

        if not vehicles and not trip_rows:
            # Avtobuslar jadvali bo'sh — trips'dan aniqlaymiz
            for vid, r in sorted(trip_rows.items()):
                vehicles.append({"external_id": vid, "plate_number": vid,
                                 "garage_number": "", "model": "", "route_id": ""})

        out = []
        for v in vehicles:
            vid = v["external_id"]
            t = trip_rows.get(vid, {})
            trips = int(t.get("trips") or 0)
            last = t.get("last_actual") or t.get("last_sync") or ""
            active = trips > 0
            out.append({
                "vehicle_id": vid,
                "plate_number": v.get("plate_number") or vid,
                "garage_number": v.get("garage_number") or "",
                "model": v.get("model") or "",
                "route_id": v.get("route_id") or "",
                "status": "faol" if active else "faol emas",
                "trips": trips,
                "issues": int(t.get("issues") or 0),
                "last_activity": last or "-",
            })
        return out

    # ------------------------------------------------------ vehicle detail

    def _vehicle_period(self, f: dict | None) -> dict:
        """Davr: month/from/to/date berilmagan bo'lsa joriy oy."""
        f = dict(f or {})
        if not any(str(f.get(k) or "").strip()
                   for k in ("month", "from", "to", "date")):
            f["month"] = date.today().strftime("%Y-%m")
        frm, to = self._month_bounds(f)
        f.update({"from": frm, "to": to, "date": ""})
        return f

    def resolve_vehicle(self, arg: str, filters: dict | None = None) -> str:
        """Avtobus raqami/ID'sini vehicle_id ga aylantiradi.

        Avval aniq moslik (external_id, plate_number, garage_number),
        so'ngra plate bo'yicha substring. Topilmasa bo'sh qator.
        """
        arg = (arg or "").strip()
        if not arg:
            return ""
        rows = self.storage.query(
            "SELECT external_id, plate_number, garage_number FROM vehicles")
        for r in rows:
            for key in ("external_id", "plate_number", "garage_number"):
                if str(r.get(key) or "") == arg:
                    return str(r["external_id"])
        low = arg.lower()
        for r in rows:
            if low in str(r.get("plate_number") or "").lower():
                return str(r["external_id"])
        return ""

    def vehicle_detail(self, vehicle_id: str, f: dict | None = None) -> dict | None:
        """Bitta avtobus kartasi: ma'lumot + davr statistikasi + grafik + reyslar.

        Masofa (km) `route_daily` (rasmiy gross hisob-kitob) dan olinadi —
        trips odo km'idan ustun.
        """
        vehicle_id = str(vehicle_id or "").strip()
        if not vehicle_id:
            return None
        f = self._vehicle_period(f)
        base = self.storage.find("vehicles", external_id=vehicle_id)
        if not base:
            return None
        plate = str(base.get("plate_number") or "")
        ph = self.ph

        # route_daily — bitta avtobus bo'yicha yig'indi (plate yoki ID orqali)
        rd_where = f"date >= {ph} AND date <= {ph} AND (vehicle_id = {ph}"
        rd_params = [f["from"], f["to"], vehicle_id]
        if plate:
            rd_where += f" OR vehicle_number = {ph}"
            rd_params.append(plate)
        rd_where += ")"
        rd_rows = self.storage.query(
            "SELECT COUNT(*) AS days,"
            " COALESCE(SUM(trip_plan),0) AS trip_plan,"
            " COALESCE(SUM(trip_fact),0) AS trip_fact,"
            " COALESCE(SUM(trip_passed),0) AS trip_passed,"
            " COALESCE(SUM(trip_approved),0) AS trip_approved,"
            " COALESCE(SUM(distance_plan),0) AS distance_plan,"
            " COALESCE(SUM(distance_fact),0) AS distance_fact,"
            " COALESCE(SUM(distance_fact_extra),0) AS distance_fact_extra"
            f" FROM route_daily WHERE {rd_where}",
            tuple(rd_params), limit=1)
        rd = rd_rows[0] if rd_rows else {}

        # trips — davr bo'yicha reyslar / muammolar
        tf = {k: f.get(k) for k in ("route", "status", "profile") if f.get(k)}
        tf.update({"from": f["from"], "to": f["to"], "vehicle": vehicle_id})
        where, params = self._trip_where(tf)
        tr_rows = self.storage.query(
            "SELECT COUNT(*) AS trips,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS issues,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS done"
            f" FROM trips WHERE {where}",
            tuple(_ISSUE_STATUSES) + tuple(_DONE_STATUSES) + tuple(params), limit=1)
        tr = tr_rows[0] if tr_rows else {}

        names = self._names()
        rnames, dnames = names["routes"], names["drivers"]

        last = self.storage.query(
            "SELECT date, route_id, driver_id, planned_time, actual_time, status"
            f" FROM trips WHERE vehicle_id = {ph}"
            " ORDER BY date DESC, planned_time DESC", (vehicle_id,), limit=8)
        for row in last:
            row["route"] = rnames.get(row.get("route_id"), row.get("route_id") or "-")
            row["driver"] = dnames.get(row.get("driver_id"), row.get("driver_id") or "-")

        sched = self.storage.query(
            "SELECT route_id, graph_name, driver_id, shift_name, start_time,"
            " end_time, trip_count"
            f" FROM schedules WHERE date = {ph} AND vehicle_id = {ph}"
            " ORDER BY start_time", (date.today().isoformat(), vehicle_id), limit=10)
        for s in sched:
            s["route"] = rnames.get(s.get("route_id"), s.get("route_id") or "-")
            s["driver"] = dnames.get(s.get("driver_id"), s.get("driver_id") or "-")

        rid = str(base.get("route_id") or "")
        comp = self._companies().get(rid, {})

        # Oxirgi haydovchilar (avtobusda ishlagan unikal haydovchilar)
        recent_drivers = []
        if last:
            seen = set()
            for t in last:
                did = str(t.get("driver_id") or "")
                if did and did not in seen:
                    seen.add(did)
                    recent_drivers.append({
                        "driver_id": did,
                        "name": dnames.get(did, did),
                    })

        return {
            "vehicle": {
                "vehicle_id": vehicle_id,
                "plate_number": str(base.get("plate_number") or vehicle_id),
                "garage_number": str(base.get("garage_number") or ""),
                "model": str(base.get("model") or ""),
                "route_id": rid,
                "route_name": rnames.get(rid, "") or comp.get("route_name", ""),
                "company": comp.get("company", ""),
            },
            "period": f,
            "route_daily": rd,
            "trips": tr,
            "last_trips": last,
            "schedule_today": sched,
            "recent_drivers": recent_drivers,
        }

    # -------------------------------------------------------------- distance

    def distance(self, f: dict | None = None) -> dict:
        """Avtobuslar bo'yicha masofa (km) hisoboti — route_daily asosida.

        Har bir avtobus uchun: ish kunlari, reja/fakt km, reja/fakt/qabul
        reyslar. Oxirida jami.

        Tanlangan firma (route) bo'yicha qat'iy filtrlanadi — boshqa
        firmalarning avtobuslari ko'rinmaydi.
        """
        f = self._vehicle_period(f)
        ph = self.ph
        rd_where = f"date >= {ph} AND date <= {ph}"
        rd_params: list = [f["from"], f["to"]]
        route_where, route_params = self._route_filter(f.get("route"))
        if route_where:
            rd_where += f" AND {route_where}"
            rd_params.extend(route_params)
        rows = self.storage.query(
            "SELECT route_id, vehicle_id, vehicle_number, vehicle_brand,"
            " COUNT(*) AS days, COALESCE(SUM(trip_plan),0) AS trip_plan,"
            " COALESCE(SUM(trip_fact),0) AS trip_fact,"
            " COALESCE(SUM(trip_passed),0) AS trip_passed,"
            " COALESCE(SUM(trip_approved),0) AS trip_approved,"
            " COALESCE(SUM(distance_plan),0) AS distance_plan,"
            " COALESCE(SUM(distance_fact),0) AS distance_fact,"
            " COALESCE(SUM(distance_fact_extra),0) AS distance_fact_extra"
            f" FROM route_daily WHERE {rd_where}"
            " GROUP BY route_id, vehicle_id, vehicle_number, vehicle_brand"
            " ORDER BY distance_fact DESC",
            tuple(rd_params))
        vehicles = {v["external_id"]: v for v in self.storage.query(
            "SELECT external_id, plate_number, model FROM vehicles")}
        rnames = self._route_names()
        companies = self._companies()

        out = []
        for r in rows:
            vid = r.get("vehicle_id") or ""
            plate = r.get("vehicle_number") or \
                vehicles.get(vid, {}).get("plate_number") or vid
            rid = r.get("route_id") or ""
            out.append({
                "plate_number": str(plate),
                "model": (vehicles.get(vid, {}).get("model") or "")
                         or str(r.get("vehicle_brand") or ""),
                "vehicle_id": vid,
                "route_id": rid,
                "route_name": rnames.get(rid, rid),
                "company": companies.get(rid, {}).get("company", ""),
                "days": int(r.get("days") or 0),
                "trip_plan": int(r.get("trip_plan") or 0),
                "trip_fact": int(r.get("trip_fact") or 0),
                "trip_passed": int(r.get("trip_passed") or 0),
                "trip_approved": int(r.get("trip_approved") or 0),
                "distance_plan": round(float(r.get("distance_plan") or 0), 1),
                "distance_fact": round(float(r.get("distance_fact") or 0), 1),
                "distance_fact_extra": round(
                    float(r.get("distance_fact_extra") or 0), 1),
            })
        return {
            "period": f,
            "rows": out,
            "totals": {
                "vehicles": len(out),
                "days": sum(x["days"] for x in out),
                "trip_plan": sum(x["trip_plan"] for x in out),
                "trip_fact": sum(x["trip_fact"] for x in out),
                "trip_approved": sum(x["trip_approved"] for x in out),
                "distance_plan": round(sum(x["distance_plan"] for x in out), 1),
                "distance_fact": round(sum(x["distance_fact"] for x in out), 1),
                "distance_fact_extra": round(
                    sum(x["distance_fact_extra"] for x in out), 1),
            },
        }

    # --------------------------------------------------------------- schedule

    def schedule(self, f: dict | None = None) -> dict:
        """Tanlangan sana uchun yo'nalish bo'yicha jadval (schedules)."""
        f = parse_filters(f or {})
        ds = f.get("date") or date.today().isoformat()
        rows = self.storage.query(
            "SELECT route_id, graph_name, driver_id, vehicle_id, shift_name,"
            " start_time, end_time, trip_count"
            f" FROM schedules WHERE date = {self.ph}"
            " ORDER BY route_id, start_time", (ds,))
        names = self._names()
        rnames, dnames, vnames = names["routes"], names["drivers"], names["vehicles"]
        companies = self._companies()

        groups: dict[str, list[dict]] = {}
        for r in rows:
            rid = r.get("route_id") or "-"
            groups.setdefault(rid, []).append({
                "graph": str(r.get("graph_name") or ""),
                "shift": str(r.get("shift_name") or ""),
                "start": str(r.get("start_time") or ""),
                "end": str(r.get("end_time") or ""),
                "driver": dnames.get(r.get("driver_id"),
                                     r.get("driver_id") or "-"),
                "vehicle": vnames.get(r.get("vehicle_id"),
                                      r.get("vehicle_id") or "-"),
                "trip_count": int(r.get("trip_count") or 0),
            })
        routes = []
        for rid, items in groups.items():
            routes.append({
                "route_id": rid,
                "route_name": rnames.get(rid, rid),
                "company": companies.get(rid, {}).get("company", ""),
                "items": items,
            })
        routes.sort(key=lambda x: x["route_name"])
        return {
            "date": ds,
            "routes": routes,
            "total_buses": sum(len(x["items"]) for x in routes),
            "total_trips": sum(it["trip_count"] for x in routes
                               for it in x["items"]),
        }

    # -------------------------------------------------------------- attendance

    def attendance(self, f: dict | None = None) -> dict:
        """Kunlik davomat: qatnashgan / kelmagan haydovchilar.

        Qatnashgan — shu sanada kamida bitta reysi bo'lgan haydovchi.
        Roster — drivers jadvalidagi (route filterli) haydovchilar.
        """
        f = parse_filters(f or {})
        ds = f.get("date") or date.today().isoformat()
        af = dict(f)
        af.update({"from": ds, "to": ds, "date": ""})
        where, params = self._trip_where(af)

        present_ids = {str(r["driver_id"]) for r in self.storage.query(
            "SELECT DISTINCT driver_id FROM trips WHERE driver_id != ''"
            f"{(' AND ' + where) if where else ''}", tuple(params))}

        drows = self.storage.query(
            "SELECT external_id, full_name, route_id FROM drivers")
        names = {str(r["external_id"]): (r["full_name"] or r["external_id"])
                 for r in drows}
        route_ids = {t.strip() for t in
                     str(f.get("route") or "").replace(",", " ").split() if t.strip()}

        roster = {str(r["external_id"]): names[str(r["external_id"])]
                  for r in drows
                  if not route_ids or str(r.get("route_id") or "") in route_ids}
        for did in present_ids:
            roster.setdefault(did, names.get(did, did))

        present = [{"driver_id": did, "name": name}
                   for did, name in roster.items() if did in present_ids]
        absent = [{"driver_id": did, "name": name}
                  for did, name in roster.items() if did not in present_ids]
        total = len(roster)
        return {
            "date": ds,
            "present": present,
            "absent": absent,
            "present_count": len(present),
            "absent_count": len(absent),
            "total": total,
            "rate": round(len(present) / total * 100, 1) if total else 0.0,
            "roster": [{"driver_id": did, "name": name, "present": did in present_ids}
                       for did, name in roster.items()],
        }

    # --------------------------------------------------------------- drivers

    def _driver_period(self, filters: dict | None) -> dict:
        """``month`` filterini aniq sana oralig'iga aylantiradi."""
        f = dict(filters or {})
        if f.get("month"):
            frm, to = self._month_bounds(f)
            f.update({"from": frm, "to": to, "date": ""})
        return f

    def _driver_profiles(self) -> dict[str, dict]:
        rows = self.storage.query("SELECT * FROM driver_profiles")
        return {str(r.get("driver_id") or ""): r for r in rows if r.get("driver_id")}

    @staticmethod
    def _photo_url(driver_id: str, profile: dict) -> str:
        """Profilga rasm yuklangan bo'lsa dashboard API manzilini qaytaradi."""
        if profile.get("photo_path"):
            return f"/api/drivers/{quote(driver_id)}/photo"
        return ""

    def _driver_logs(self, f: dict) -> dict[str, dict]:
        clauses, params = [], []
        if f.get("from"):
            clauses.append(f"date >= {self.ph}")
            params.append(f["from"])
        if f.get("to"):
            clauses.append(f"date <= {self.ph}")
            params.append(f["to"])
        if f.get("driver"):
            clauses.append(f"driver_id = {self.ph}")
            params.append(f["driver"])
        if f.get("vehicle"):
            clauses.append(f"vehicle_id = {self.ph}")
            params.append(f["vehicle"])
        if f.get("route"):
            route_where, route_params = self._route_filter(f["route"])
            if route_where:
                # Qaydlar avtobusga bog'langan, yo'nalish filtri shu bog'lanish
                # orqali ishlaydi. Avtobussiz eski qaydlar umumiy hisobda qoladi.
                clauses.append("vehicle_id IN (SELECT external_id FROM vehicles WHERE "
                               + route_where + ")")
                params.extend(route_params)
        # AVTO qaydlari brutto-route (gross/route) km'sini ko'rsatadi; u
        # trips km'ni almashtiradi (`drivers()` merge). Bu erda faqat qo'lda
        # (dlog) qo'shilgan qaydlar hisoblanadi — AVTO ikki marta qo'shilmaydi.
        clauses.append(f"note != {self.ph}")
        params.append("AVTO")
        where = " AND ".join(clauses)
        sql = (
            "SELECT driver_id, COALESCE(SUM(distance_km), 0) AS km, "
            "COALESCE(SUM(trip_count), 0) AS trips, MAX(date) AS last_date "
            "FROM driver_work_logs WHERE driver_id != ''"
            f"{(' AND ' + where) if where else ''} GROUP BY driver_id"
        )
        return {str(r["driver_id"]): r for r in self.storage.query(sql, tuple(params))}

    def _driver_avto_keys(self, f: dict) -> dict[tuple, dict]:
        """AVTO (brutto-route) qaydlar — (date, driver, vehicle) kalitida.

        Haydovchi oyligi km`si brutto-route (gross/route `distanceFact`)
        dan olinadi va shu kalit bo'yicha saqlanadi. Dashboard trips
        km'ini AVTO qaydlar bilan almashtiradi (fallback: trips).
        """
        clauses, params = [], []
        ph = self.ph
        if f.get("from"):
            clauses.append(f"date >= {ph}")
            params.append(f["from"])
        if f.get("to"):
            clauses.append(f"date <= {ph}")
            params.append(f["to"])
        if f.get("driver"):
            clauses.append(f"driver_id = {ph}")
            params.append(f["driver"])
        if f.get("vehicle"):
            clauses.append(f"vehicle_id = {ph}")
            params.append(f["vehicle"])
        if f.get("route"):
            route_where, route_params = self._route_filter(f["route"])
            if route_where:
                clauses.append("vehicle_id IN (SELECT external_id FROM vehicles WHERE "
                               + route_where + ")")
                params.extend(route_params)
        clauses.append(f"note = {ph}")
        params.append("AVTO")
        where = " AND ".join(clauses)
        rows = self.storage.query(
            "SELECT date, driver_id, vehicle_id, distance_km, trip_count "
            "FROM driver_work_logs WHERE driver_id != ''"
            f"{(' AND ' + where) if where else ''}", tuple(params), limit=50000)
        return {(str(r.get("date") or ""), str(r.get("driver_id") or ""),
                 str(r.get("vehicle_id") or "")): r for r in rows}

    def _driver_fines(self, f: dict) -> dict[str, float]:
        clauses, params = [], []
        if f.get("from"):
            clauses.append(f"date >= {self.ph}")
            params.append(f["from"])
        if f.get("to"):
            clauses.append(f"date <= {self.ph}")
            params.append(f["to"])
        if f.get("driver"):
            clauses.append(f"driver_id = {self.ph}")
            params.append(f["driver"])
        if f.get("route"):
            route_where, route_params = self._route_filter(f["route"])
            if route_where:
                clauses.append("driver_id IN (SELECT external_id FROM drivers WHERE "
                               + route_where + ")")
                params.extend(route_params)
        where = " AND ".join(clauses)
        # Faqat faol jarima ish-haqi hisobida ushlab qolinadi. Bekor qilingan
        # va to'langan yozuvlar tarixda qoladi, lekin net ish haqini kamaytirmaydi.
        sql = (
            "SELECT driver_id, COALESCE(SUM(amount), 0) AS amount FROM driver_fines "
            "WHERE driver_id != '' AND status = 'ACTIVE'"
            f"{(' AND ' + where) if where else ''} GROUP BY driver_id"
        )
        return {str(r["driver_id"]): float(r.get("amount") or 0) for r in
                self.storage.query(sql, tuple(params))}

    def drivers(self, f: dict) -> list[dict]:
        f = self._driver_period(f)
        where, params = self._trip_where(f)
        ph = self.ph
        # Har bir (sana, haydovchi, avtobus, yo'nalish) uchun trips
        # statistika.  Yo'nalish bo'yicha ajratilgan km har bir route uchun
        # alohida km_rate bilan hisoblanadi (haydovchi turli yo'nalishlarga
        # chiqsa, har birining narxi o'zicha).
        key_sql = (
            f"SELECT date, driver_id, vehicle_id, route_id,"
            f" COUNT(*) AS trips,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS issues,"
            f" COALESCE(SUM(CASE WHEN status IN ({ph},{ph})"
            f" THEN distance_km ELSE 0 END), 0) AS km"
            f" FROM trips WHERE driver_id != ''"
            f"{(' AND ' + where) if where else ''}"
            f" GROUP BY date, driver_id, vehicle_id, route_id"
        )
        key_rows = self.storage.query(
            key_sql, tuple(_ISSUE_STATUSES) + tuple(_DONE_STATUSES) + tuple(params),
            limit=100000)

        total_days = 1
        try:
            frm = date.fromisoformat(f.get("from") or f.get("date", ""))
            to = date.fromisoformat(f.get("to") or f.get("from", ""))
            total_days = max((to - frm).days + 1, 1)
        except ValueError:
            total_days = 1

        driver_rows = self.storage.query(
            "SELECT external_id, full_name, route_id FROM drivers")
        names = {str(r["external_id"]): (r["full_name"] or r["external_id"])
                 for r in driver_rows}
        driver_routes = {str(r["external_id"]): str(r.get("route_id") or "")
                         for r in driver_rows}
        profiles = self._driver_profiles()
        logs = self._driver_logs(f)
        fines = self._driver_fines(f)

        avto = self._driver_avto_keys(f) if not f.get("status") else {}

        # --- Per-route km aggregation ---
        # route_km[did] = {route_id: km_sum} — trips AVTO bilan
        # almashtirilgandan keyin ham route ajralib turadi.
        route_km: dict[str, dict[str, float]] = {}
        per: dict[str, dict] = {}
        avto_replaced: set = set()
        for r in key_rows:
            did = str(r.get("driver_id") or "")
            rid = str(r.get("route_id") or "")
            if not did:
                continue
            d = per.setdefault(did, {"km": 0.0, "trips": 0, "days": set(),
                                     "issues": 0})
            a = avto.get((str(r.get("date") or ""), did,
                          str(r.get("vehicle_id") or "")))
            if a is not None:
                avto_km = float(a.get("distance_km") or 0)
                d["km"] += avto_km
                d["trips"] += int(a.get("trip_count") or 0)
                if rid:
                    avto_replaced.add((str(r.get("date") or ""), did,
                                       str(r.get("vehicle_id") or "")))
                    route_km.setdefault(did, {})
                    route_km[did][rid] = route_km[did].get(rid, 0.0) + avto_km
            else:
                trip_km = float(r.get("km") or 0)
                d["km"] += trip_km
                d["trips"] += int(r.get("trips") or 0)
                if rid:
                    route_km.setdefault(did, {})
                    route_km[did][rid] = route_km[did].get(rid, 0.0) + trip_km
            if r.get("date"):
                d["days"].add(str(r["date"]))
            d["issues"] += int(r.get("issues") or 0)

        # Haydovchi profili oldindan kiritilgan, biroq uning joriy davrda
        # reysi bo'lmasa ham reestrda ko'rinishi kerak.
        route_ids = {t.strip() for t in
                     str(f.get("route") or "").replace(",", " ").split() if t.strip()}
        if route_ids:
            active = set(per) | set(logs) | set(fines)
            keep = lambda did: driver_routes.get(did, "") in route_ids or did in active
            names = {k: v for k, v in names.items() if keep(k)}
            profiles = {k: v for k, v in profiles.items() if keep(k)}
        ids = set(names) | set(profiles) | set(logs) | set(fines) | set(per)
        automatic_km: dict[str, float] = {
            did: round(d["km"], 2) for did, d in per.items()
        }

        companies = self._companies()
        rnames = self._route_names()

        out = []
        for driver_id in ids:
            p = per.get(driver_id, {})
            profile = profiles.get(driver_id, {})
            drv_log = logs.get(driver_id, {})
            days = len(p.get("days") or ())
            manual_km = float(drv_log.get("km") or 0)
            total_km = round(p.get("km", 0.0) + manual_km, 2)

            # --- Per-route salary: qaysi yo'nalishda necha km × route km_rate ---
            drv_route_km = dict(route_km.get(driver_id, {}))
            # Manual qayd (work_log) km'ni eng ko'p ishlangan yo'nalishga qo'sh
            if manual_km > 0 and drv_route_km:
                best_route = max(drv_route_km, key=drv_route_km.get)
                drv_route_km[best_route] = drv_route_km.get(best_route, 0) + manual_km
            gross = 0.0
            if drv_route_km:
                for rid, rkm in drv_route_km.items():
                    rate = km_rate_for(
                        rid, float(profile.get("km_rate") or 0))
                    gross += rkm * rate
            else:
                rid = driver_routes.get(driver_id, "")
                rate = km_rate_for(
                    rid, float(profile.get("km_rate") or 0))
                gross = total_km * rate

            gross = round(gross, 2)
            # Asosiy yo'nalish (display uchun — eng ko'p km)
            if drv_route_km:
                rid = max(drv_route_km, key=drv_route_km.get)
            else:
                rid = driver_routes.get(driver_id, "")
            rate = km_rate_for(rid, float(profile.get("km_rate") or 0))
            comp = companies.get(rid, {})
            fine = round(fines.get(driver_id, 0.0), 2)
            tax = round(gross * TAX_RATE, 2)
            net = round(max(gross - tax - fine, 0), 2)
            trips = int(p.get("trips") or 0)
            out.append({
                "driver_id": driver_id,
                "name": names.get(driver_id, driver_id),
                "company": comp.get("company", ""),
                "route_id": rid,
                "route_name": rnames.get(rid, "") or comp.get("route_name", ""),
                "trips": trips,
                "manual_trips": int(drv_log.get("trips") or 0),
                "working_days": days,
                "issues": int(p.get("issues") or 0),
                "attendance": round(days / total_days * 100, 1),
                "total_days": total_days,
                "km": total_km,
                "automatic_km": automatic_km.get(driver_id, 0.0),
                "km_rate": rate,
                "gross_pay": gross,
                "tax": tax,
                "fines": fine,
                "net_pay": net,
                "rating": float(profile.get("rating") or 5),
                "blacklisted": bool(profile.get("blacklisted")),
                "notification_enabled": bool(profile.get("notification_enabled")),
                "has_passport": bool(profile.get("passport_number")),
                "has_license": bool(profile.get("license_number")),
                "photo_url": self._photo_url(driver_id, profile),
            })
        return sorted(out, key=lambda x: (x["company"], x["blacklisted"],
                                           -x["trips"], x["name"].lower()))

    def electricity_report(self, filters: dict | None = None) -> dict:
        """Oylik elektr energiya xisoboti.

        Har bir haydovchi uchun amalda bosib o'tilgan km (AVTO route_daily
        ``distance_fact``) bazasida hisoblanadi:
            elektrolit (kVt/soat) = km * ELEC_KWH_PER_KM
            summa (so'm)        = kant * 1 kVt narxi

        Har bir yo'nalish bo'yicha ham jami, ham umumiy jami qaytariladi.
        """
        from ..core.bot_settings import ELEC_KWH_PER_KM, elec_price
        f = self._driver_period(dict(filters or {}))
        price = elec_price()
        where, params = self._trip_where(f)
        avto = self._driver_avto_keys(f)

        # (date, driver, vehicle) -> (km), route_id ni trips orqali birlashtiramiz
        ph = self.ph
        trip_rows = self.storage.query(
            "SELECT DISTINCT date, driver_id, vehicle_id, route_id FROM trips"
            f" WHERE driver_id != ''{(' AND ' + where) if where else ''}",
            tuple(params), limit=50000)
        dps = {(str(r.get("date") or ""), str(r.get("driver_id") or ""),
                str(r.get("vehicle_id") or "")): str(r.get("route_id") or "")
               for r in trip_rows}

        driver_rows = self.storage.query(
            "SELECT external_id, full_name, route_id FROM drivers")
        names = {str(r["external_id"]): (r["full_name"] or r["external_id"])
                 for r in driver_rows}
        driver_routes = {str(r["external_id"]): str(r.get("route_id") or "")
                         for r in driver_rows}
        rnames = self._route_names()
        companies = self._companies()

        per_driver: dict[str, dict] = {}
        per_route: dict[str, dict] = {}
        for key, rec in avto.items():
            km = float(rec.get("distance_km") or 0)
            did = key[1]
            rid = dps.get(key) or driver_routes.get(did, "")
            d = per_driver.setdefault(did, {"km": 0.0})
            d["km"] += km
            rt = per_route.setdefault(rid, {"km": 0.0})
            rt["km"] += km

        kwh_km = ELEC_KWH_PER_KM
        def _finish(d: dict) -> dict:
            km = round(float(d.get("km") or 0), 2)
            return {"km": km,
                    "kwh": round(km * kwh_km, 2),
                    "rate": price,
                    "total": round(km * kwh_km * price, 2)}

        drivers = []
        for did, d in per_driver.items():
            rid = driver_routes.get(did, "")
            comp = companies.get(rid, {})
            drivers.append({
                "driver_id": did,
                "name": names.get(did, did),
                "company": comp.get("company", ""),
                "route_name": rnames.get(rid, "") or comp.get("route_name", "SR"),
                **_finish(d),
            })
        drivers = sorted(drivers, key=lambda x: -x["km"])

        routes = []
        for rid, d in per_route.items():
            comp = companies.get(rid, {})
            routes.append({
                "route_id": rid,
                "name": rnames.get(rid, rid),
                "company": comp.get("company", ""),
                **_finish(d),
            })
        routes = sorted(routes, key=lambda x: -x["km"])

        tot_km = round(sum(d["km"] for d in drivers), 2)
        tot_kwh = round(tot_km * kwh_km, 2)
        tot_total = round(tot_kwh * price, 2)
        return {
            "period": {"from": f.get("from", ""), "to": f.get("to", "")},
            "kwh_per_km": kwh_km,
            "rate": price,
            "totals": {"km": tot_km, "kwh": tot_kwh, "total": tot_total},
            "drivers": drivers,
            "routes": routes,
        }

    def _single_driver_metrics(self, driver_id: str, f: dict) -> dict | None:
        """Bitta haydovchi uchun metrics — drivers() dan tezroq."""
        where, params = self._trip_where(f)
        ph = self.ph
        sql = (
            f"SELECT date, vehicle_id, route_id, COUNT(*) AS trips,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS issues,"
            f" COALESCE(SUM(CASE WHEN status IN ({ph},{ph})"
            f" THEN distance_km ELSE 0 END), 0) AS km"
            f" FROM trips WHERE driver_id = {ph}"
            f"{(' AND ' + where) if where else ''}"
            f" GROUP BY date, vehicle_id, route_id"
        )
        rows = self.storage.query(sql, tuple(_ISSUE_STATUSES) + tuple(_DONE_STATUSES)
                                  + (driver_id,) + tuple(params), limit=50000)
        if not rows:
            logs = self._driver_logs({**f, "driver": driver_id})
            drv_log = logs.get(driver_id, {})
            if not drv_log:
                return None
        avto = self._driver_avto_keys(
            {**f, "driver": driver_id}) if not f.get("status") else {}
        per_km = 0.0
        per_trips = 0
        per_issues = 0
        days = set()
        route_km: dict[str, float] = {}
        for r in rows:
            a = avto.get((str(r.get("date") or ""), driver_id,
                          str(r.get("vehicle_id") or "")))
            if a is not None:
                row_km = float(a.get("distance_km") or 0)
                row_trips = int(a.get("trip_count") or 0)
            else:
                row_km = float(r.get("km") or 0)
                row_trips = int(r.get("trips") or 0)
            per_km += row_km
            per_trips += row_trips
            per_issues += int(r.get("issues") or 0)
            if r.get("date"):
                days.add(str(r["date"]))
            rid = str(r.get("route_id") or "")
            if rid:
                route_km[rid] = route_km.get(rid, 0.0) + row_km
        logs = self._driver_logs({**f, "driver": driver_id})
        drv_log = logs.get(driver_id, {})
        manual_km = float(drv_log.get("km") or 0)
        total_km = round(per_km + manual_km, 2)
        if manual_km > 0 and route_km:
            best = max(route_km, key=route_km.get)
            route_km[best] = route_km.get(best, 0.0) + manual_km
        profile = dict(self.storage.find("driver_profiles", driver_id=driver_id) or {})
        gross = 0.0
        if route_km:
            for rid, rkm in route_km.items():
                rate = km_rate_for(rid, float(profile.get("km_rate") or 0))
                gross += rkm * rate
        else:
            rid = (self.storage.find("drivers", external_id=driver_id) or {}).get("route_id", "")
            rate = km_rate_for(str(rid or ""), float(profile.get("km_rate") or 0))
            gross = total_km * rate
        gross = round(gross, 2)
        if route_km:
            rid = max(route_km, key=route_km.get)
        else:
            rid = str((self.storage.find("drivers", external_id=driver_id) or {}).get("route_id") or "")
        rate = km_rate_for(rid, float(profile.get("km_rate") or 0))
        companies = self._companies()
        comp = companies.get(rid, {})
        fines = self._driver_fines({**f, "driver": driver_id})
        fine = round(fines.get(driver_id, 0.0), 2)
        tax = round(gross * TAX_RATE, 2)
        net = round(max(gross - tax - fine, 0), 2)
        total_days = 1
        try:
            frm = date.fromisoformat(f.get("from") or f.get("date", ""))
            to = date.fromisoformat(f.get("to") or f.get("from", ""))
            total_days = max((to - frm).days + 1, 1)
        except ValueError:
            total_days = 1
        return {
            "driver_id": driver_id,
            "name": (self.storage.find("drivers", external_id=driver_id) or {}).get("full_name") or driver_id,
            "company": comp.get("company", ""),
            "route_id": rid,
            "route_name": self._route_names().get(rid, "") or comp.get("route_name", ""),
            "trips": per_trips,
            "manual_trips": int(drv_log.get("trips") or 0),
            "working_days": len(days),
            "issues": per_issues,
            "attendance": round(len(days) / total_days * 100, 1),
            "total_days": total_days,
            "km": total_km,
            "automatic_km": round(per_km, 2),
            "km_rate": rate,
            "gross_pay": gross,
            "tax": tax,
            "fines": fine,
            "net_pay": net,
            "rating": float(profile.get("rating") or 5),
            "blacklisted": bool(profile.get("blacklisted")),
            "notification_enabled": bool(profile.get("notification_enabled")),
            "has_passport": bool(profile.get("passport_number")),
            "has_license": bool(profile.get("license_number")),
            "photo_url": self._photo_url(driver_id, profile),
        }

    def driver_detail(self, driver_id: str, f: dict | None = None) -> dict | None:
        """Bitta haydovchi kartasi uchun profil, statistika va tarix."""
        driver_id = str(driver_id or "").strip()
        if not driver_id:
            return None
        f = self._driver_period(f)

        # Bitta haydovchi uchun to'g'ridan-to'g'ri query — barcha haydovchilarni
        # hisoblash shart emas.
        driver = self._single_driver_metrics(driver_id, f)
        base = self.storage.find("drivers", external_id=driver_id)
        profile = dict(self.storage.find("driver_profiles", driver_id=driver_id) or {})
        if base:
            profile.setdefault("tin", base.get("tin") or "")
        if not driver and not base and not profile:
            return None
        if not driver:
            driver = {"driver_id": driver_id, "name": (base or {}).get("full_name") or driver_id,
                      "trips": 0, "manual_trips": 0, "working_days": 0, "issues": 0,
                      "attendance": 0.0, "total_days": 0, "km": 0.0, "automatic_km": 0.0,
                      "km_rate": km_rate_for(str((base or {}).get("route_id") or ""),
                                             float(profile.get("km_rate") or 0)),
                      "gross_pay": 0.0,
                      "tax": 0.0,
                      "fines": 0.0, "net_pay": 0.0, "rating": float(profile.get("rating") or 5),
                      "blacklisted": bool(profile.get("blacklisted")),
                      "notification_enabled": bool(profile.get("notification_enabled")),
                      "has_passport": bool(profile.get("passport_number")),
                      "has_license": bool(profile.get("license_number")),
                      "photo_url": self._photo_url(driver_id, profile)}

        where, params = self._trip_where({**f, "driver": driver_id})
        names = self._names()
        trips = self.storage.query(
            "SELECT date, vehicle_id, route_id, planned_time, actual_time, status, source "
            "FROM trips" + (f" WHERE {where}" if where else "") +
            " ORDER BY date DESC, planned_time DESC", tuple(params), limit=500)
        for row in trips:
            row["vehicle"] = names["vehicles"].get(row.get("vehicle_id"), row.get("vehicle_id") or "-")
            row["route"] = names["routes"].get(row.get("route_id"), row.get("route_id") or "-")

        log_where = "driver_id = " + self.ph
        log_params: list = [driver_id]
        if f.get("from"):
            log_where += f" AND date >= {self.ph}"
            log_params.append(f["from"])
        if f.get("to"):
            log_where += f" AND date <= {self.ph}"
            log_params.append(f["to"])
        logs = self.storage.query(
            "SELECT date, vehicle_id, distance_km, trip_count, note FROM driver_work_logs "
            f"WHERE {log_where} ORDER BY date DESC", tuple(log_params), limit=500)
        for row in logs:
            row["vehicle"] = names["vehicles"].get(row.get("vehicle_id"), row.get("vehicle_id") or "-")

        fine_where = "driver_id = " + self.ph
        fine_params: list = [driver_id]
        if f.get("from"):
            fine_where += f" AND date >= {self.ph}"
            fine_params.append(f["from"])
        if f.get("to"):
            fine_where += f" AND date <= {self.ph}"
            fine_params.append(f["to"])
        fines = self.storage.query(
            "SELECT id, date, amount, reason, status FROM driver_fines "
            f"WHERE {fine_where} ORDER BY date DESC, id DESC", tuple(fine_params), limit=500)
        # Bog'langan Telegram foydalanuvchi
        telegram_user = None
        tg_chat = str(profile.get("telegram_chat_id") or "").strip()
        if tg_chat:
            try:
                from ...core.bot_users import get_user as _get_tg_user
                tg_user = _get_tg_user(int(tg_chat))
                if tg_user:
                    telegram_user = {
                        "chat_id": tg_user.get("chat_id", int(tg_chat)),
                        "first_name": tg_user.get("first_name", ""),
                        "username": tg_user.get("username", ""),
                        "role": tg_user.get("role", ""),
                        "message_count": tg_user.get("message_count", 0),
                        "last_seen": tg_user.get("last_seen", ""),
                        "photo_url": f"/api/users/{tg_chat}/photo",
                    }
            except Exception:
                telegram_user = {"chat_id": int(tg_chat), "first_name": tg_chat}

        return {"driver": driver, "profile": profile, "trips": trips,
                "work_logs": logs, "fine_rows": fines, "filters": f,
                "telegram_user": telegram_user}

    def driver_directory(self, f: dict | None = None) -> dict:
        f = self._driver_period(f)
        rows = self.drivers(f)
        return {
            "filters": f,
            "drivers": rows,
            "totals": {
                "drivers": len(rows),
                "active": sum(1 for r in rows if r["working_days"] > 0 and not r["blacklisted"]),
                "blacklisted": sum(1 for r in rows if r["blacklisted"]),
                "km": round(sum(r["km"] for r in rows), 2),
                "gross_pay": round(sum(r["gross_pay"] for r in rows), 2),
                "tax": round(sum(r["tax"] for r in rows), 2),
                "net_pay": round(sum(r["net_pay"] for r in rows), 2),
                "fines": round(sum(r["fines"] for r in rows), 2),
            },
        }

    # ----------------------------------------------------------------- trips

    def trips(self, f: dict, limit: int = 200) -> list[dict]:
        where, params = self._trip_where(f)
        sql = "SELECT * FROM trips"
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY date DESC, planned_time ASC"
        return self.storage.query(sql, tuple(params), limit=limit)

    # ------------------------------------------------------------ monthly

    def _month_bounds(self, f: dict) -> tuple[str, str]:
        """Diapazon: `month` (YYYY-MM) → oy boshidan oy oxirigacha;
        `year` (YYYY) → yil boshi va bugun (yil hali tamomlanmagan bo'lsa);
        aks holda `from`/`to`; aks holda joriy oy."""
        month = str(f.get("month") or "").strip()
        year = str(f.get("year") or "").strip()
        frm = str(f.get("from") or "").strip()
        to = str(f.get("to") or "").strip()
        today = date.today()
        try:
            # Yil (YYYY) — yil boshidan bugungacha (yil hali davom etayotgan bo'lsa)
            if len(year) == 4 and year.isdigit():
                first = date(int(year), 1, 1)
                last = date(int(year), 12, 31)
                if last > today:
                    last = today
                return first.isoformat(), last.isoformat()
            if len(month) == 7:
                y, mo = month.split("-")
                first = date(int(y), int(mo), 1)
            elif frm and to:
                first = date.fromisoformat(frm)
                last = date.fromisoformat(to)
                return first.isoformat(), last.isoformat()
            else:
                first = today.replace(day=1)
                last = today
                return first.isoformat(), last.isoformat()
        except ValueError:
            return today.replace(day=1).isoformat(), today.isoformat()
        # oy oxiri (keyingi oy 1-kuni − 1 kun), bugundan oshirmaymiz
        if first.month == 12:
            nxt = date(first.year + 1, 1, 1)
        else:
            nxt = date(first.year, first.month + 1, 1)
        last = nxt - timedelta(days=1)
        if last > today:
            last = today
        return first.isoformat(), last.isoformat()

    def monthly(self, f: dict | None = None) -> dict:
        """Oy/davr bo'yicha kunlik statistika.

        Har bir kun uchun: rejalashtirilgan (schedules trip_count),
        reyslar, amalda bajarilgan, statuslar, muammolar va perf%.
        Oxirida butun davr uchun jami qator.
        """
        f = f or {}
        frm, to = self._month_bounds(f)
        ph = self.ph

        wf = dict(f)
        for k in ("date", "from", "to", "month"):
            wf.pop(k, None)
        where, params = self._trip_where(wf)
        w = f" AND {where}" if where else ""

        # trips — kun bo'yicha status hisoblari
        trips_sql = (
            f"SELECT date,"
            f" COUNT(*) AS total,"
            f" SUM(CASE WHEN actual_time != '' AND status != {ph}"
            f" THEN 1 ELSE 0 END) AS actual,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS completed,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS not_accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS pending,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS rejected,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS zero_mileage,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) AND actual_time = ''"
            f" THEN 1 ELSE 0 END) AS gps,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS technical"
            f" FROM trips"
            f" WHERE date >= {ph} AND date <= {ph}{w}"
            f" GROUP BY date"
        )
        tparams = ((TripStatus.ZERO_MILEAGE.value,)
                   + tuple(_DONE_STATUSES)
                   + tuple(_DONE_STATUSES)
                   + (TripStatus.NOT_ACCEPTED.value, TripStatus.PENDING_ACCESS.value,
                      TripStatus.REJECTED.value, TripStatus.ZERO_MILEAGE.value)
                   + tuple(_DONE_STATUSES)
                   + (TripStatus.REJECTED.value,)
                   + (frm, to) + tuple(params))
        day_rows = {r["date"]: r for r in self.storage.query(trips_sql, tparams)}

        # schedules — rejalashtirilgan qatnovlar (trip_count yig'indisi)
        sparam = (frm, to) + tuple(params)
        ssql = (
            f"SELECT date, COALESCE(SUM(trip_count), 0) AS n FROM schedules"
            f" WHERE date >= {ph} AND date <= {ph} AND trip_count > 0{w}"
            f" GROUP BY date"
        )
        plan_rows = {r["date"]: int(r["n"] or 0)
                     for r in self.storage.query(ssql, sparam)}

        days = []
        cur = date.fromisoformat(frm)
        last = date.fromisoformat(to)
        while cur <= last:
            ds = cur.isoformat()
            tr = day_rows.get(ds, {})
            planned = plan_rows.get(ds, 0)
            actual = int(tr.get("actual") or 0)
            technical = int(tr.get("technical") or 0)
            schedule = int(tr.get("not_accepted") or 0) + int(tr.get("pending") or 0)
            accepted = int(tr.get("accepted") or 0)
            not_accepted = int(tr.get("not_accepted") or 0)
            zero_mileage = int(tr.get("zero_mileage") or 0)
            days.append({
                "date": ds,
                "planned": planned,
                "total": accepted + not_accepted + zero_mileage,
                "actual": actual,
                "completed": int(tr.get("completed") or 0),
                "accepted": accepted,
                "not_accepted": not_accepted,
                "pending": int(tr.get("pending") or 0),
                "rejected": int(tr.get("rejected") or 0),
                "zero_mileage": zero_mileage,
                "under_review": accepted + not_accepted,
                "problems": {
                    "gps": int(tr.get("gps") or 0),
                    "technical": technical,
                    "schedule": schedule,
                },
                "problems_total": (int(tr.get("gps") or 0)
                                   + technical + schedule),
                "performance": (min(round(actual / planned * 100, 1), 100.0)
                                if planned else 0.0),
                "accept_rate": (min(round(accepted / planned * 100, 1), 100.0)
                                if planned else 0.0),
            })
            cur += timedelta(days=1)

        def _sum(key):
            return sum(int(d[key] or 0) for d in days)

        planned_sum = _sum("planned")
        actual_sum = _sum("actual")
        accepted_sum = _sum("accepted")
        totals = {
            "planned": planned_sum,
            "total": _sum("total"),
            "actual": actual_sum,
            "completed": _sum("completed"),
            "accepted": accepted_sum,
            "not_accepted": _sum("not_accepted"),
            "pending": _sum("pending"),
            "rejected": _sum("rejected"),
            "zero_mileage": _sum("zero_mileage"),
            "under_review": _sum("under_review"),
            "problems": {
                "gps": sum(d["problems"]["gps"] for d in days),
                "technical": sum(d["problems"]["technical"] for d in days),
                "schedule": sum(d["problems"]["schedule"] for d in days),
            },
            "problems_total": sum(d["problems_total"] for d in days),
            "performance": (min(round(actual_sum / planned_sum * 100, 1), 100.0)
                            if planned_sum else 0.0),
            "accept_rate": (min(round(accepted_sum / planned_sum * 100, 1), 100.0)
                            if planned_sum else 0.0),
        }
        return {"month": frm[:7], "from": frm, "to": to, "days": days,
                "totals": totals}

    # -------------------------------------------------------------- problems

    # Muammo kategoriyalari (heuristic — BM API'da alohida muammo so'rovi yo'q)
    PROBLEM_KEYS = ("gps", "technical", "schedule", "unknown")

    def _names(self) -> dict:
        routes = self._route_names()
        vehicles = {v["external_id"]: (v["plate_number"] or v["external_id"])
                    for v in self.storage.query(
                        "SELECT external_id, plate_number FROM vehicles")}
        drivers = {d["external_id"]: (d["full_name"] or d["external_id"])
                   for d in self.storage.query(
                       "SELECT external_id, full_name FROM drivers")}
        return {"routes": routes, "vehicles": vehicles, "drivers": drivers}

    def problems(self, f: dict) -> dict:
        """Muammolarni kategoriyalarga ajratadi.

        Kategoriyalar (ma'lumotlar mavjud bo'lgan ko'rsatkichlar asosida):

        - **gps** — qabul qilingan reys, lekin harakat (actual_time) qaydi yo'q
          (bitta avtobus bitta muammo deb hisoblanadi);
        - **technical** — `REJECTED` statusli avtobuslar (`ZERO_MILEAGE`
          texnik/jadval xatosi EMAS — u shunchaki garajdan yo'nalishga chiqish);
        - **schedule** — `NOT_ACCEPTED` / `PENDING_ACCESS` reyslar (jadval
          qabul qilinmagan yoki tasdiqlanmagan);
        - **unknown** — noma'lum status yoki yo'nalish/avtobus bog'lanmagan.

        `counts` + har bir kategoriya uchun `items` (batafsil) qaytaradi.
        """
        where, params = self._trip_where(f)
        sql = ("SELECT date, route_id, vehicle_id, driver_id, planned_time, "
               "actual_time, status FROM trips")
        if where:
            sql += f" WHERE {where}"
        rows = self.storage.query(sql, tuple(params), limit=1000)

        names = self._names()
        gps_veh, tech_veh = {}, {}
        schedule_items, unknown_items = [], []

        def _item(r):
            plate = names["vehicles"].get(r.get("vehicle_id"), r.get("vehicle_id") or "-")
            return {
                "date": r.get("date", ""),
                "route": names["routes"].get(r.get("route_id"), r.get("route_id") or "-"),
                "vehicle": plate,
                "driver": names["drivers"].get(r.get("driver_id"), r.get("driver_id") or "-"),
                "planned_time": r.get("planned_time", ""),
                "actual_time": r.get("actual_time", ""),
                "status": r.get("status", ""),
            }

        for r in rows:
            st_raw = r.get("status")
            if not TripStatus.is_valid(st_raw) or not (
                    r.get("route_id") or r.get("vehicle_id") or r.get("driver_id")):
                unknown_items.append(_item(r))
                continue
            st = TripStatus.normalize(st_raw)
            vid = r.get("vehicle_id") or ""
            if st == "REJECTED":
                tech_veh.setdefault(vid, _item(r))
            elif st in ("NOT_ACCEPTED", "PENDING_ACCESS"):
                schedule_items.append(_item(r))
            elif st in ("ACCEPTED", "APPROVED") and not r.get("actual_time"):
                gps_veh.setdefault(vid, _item(r))

        # GPS — texnik muammosi bo'lmagan avtobuslar (bitta avtobus bitta muammo)
        for vid in list(gps_veh):
            if vid in tech_veh:
                del gps_veh[vid]

        # REJECTED waybill qatnovlari trips'ga kirmaydi (haqiqiy qatnov
        # emas) — shu sababli texnik muammolar ichiga waybills'dan qo'shamiz.
        # ZERO_MILEAGE qo'shilmaydi — u texnik xato emas.
        w_where, w_params = self._trip_where(f)
        for wb in self.storage.query(
            "SELECT date, route_id, vehicle_id, driver_id, plate_number, status"
            " FROM waybills" + (f" WHERE {w_where}" if w_where else ""),
            tuple(w_params), limit=1000):
            if wb.get("status") != "REJECTED":
                continue
            wid = wb.get("vehicle_id") or wb.get("plate_number") or ""
            tech_veh.setdefault(wid, {
                "date": wb.get("date", ""),
                "route": names["routes"].get(wb.get("route_id"),
                                             wb.get("route_id") or "-"),
                "vehicle": names["vehicles"].get(wid, wid or "-"),
                "driver": names["drivers"].get(wb.get("driver_id"),
                                               wb.get("driver_id") or "-"),
                "planned_time": "",
                "actual_time": "",
                "status": wb.get("status", ""),
            })

        buckets = {
            "gps": list(gps_veh.values()),
            "technical": list(tech_veh.values()),
            "schedule": schedule_items,
            "unknown": unknown_items,
        }
        return {
            "counts": {k: len(v) for k, v in buckets.items()},
            "items": buckets,
        }

    # ---------------------------------------------------------------- system

    # Tashqi servislar holati har summary'da tekshirilmaydi (BM/Telegram ping
    # 5s gacha kutadi) — natija qisqa muddatga keshlanadi.
    _HEALTH_TTL = 30.0
    _health_cache: dict = {"at": 0.0, "data": None}

    def system(self) -> dict:
        cached = self._health_cache
        if cached["data"] is not None and time.monotonic() - cached["at"] < self._HEALTH_TTL:
            return cached["data"]
        st = self.storage
        bm = self._bm_status()
        db = {"ok": st.enabled,
              "driver": st.db.driver if st.enabled else "-"}
        if st.enabled:
            db.update(self._db_status())
        tg = self._telegram_status()
        sched = self._scheduler_status()
        last_sync = self._last_sync()
        last_error = None
        errs = st.list_errors(limit=1)
        if errs:
            e = errs[0]
            last_error = {"at": e.get("occurred_at", ""),
                          "source": e.get("source", ""),
                          "message": e.get("message", "")}
        result = {
            "api": bm,
            "database": db,
            "telegram": tg,
            "scheduler": sched,
            "last_sync": last_sync,
            "last_error": last_error,
            "checked_at": now_utc(),
        }
        cached["data"] = result
        cached["at"] = time.monotonic()
        return result

    @staticmethod
    def _parse_dsn(dsn: str) -> dict:
        """DSN'dan host/port/dbname ajratib oladi (URI yoki conninfo qator).

        psycopg turli versiyalarda ``conninfo_to_dict`` turli modullarda
        joylashadi, shuning uchun avval uni urinib ko'ramiz, topilmasa
        ``urllib`` orqali URI ni mustaqil tahlil qilamiz.
        """
        out = {"host": "", "port": "", "dbname": ""}
        if not dsn:
            return out
        try:
            for modname in ("psycopg.conninfo", "psycopg._conninfo_utils"):
                try:
                    mod = __import__(modname, fromlist=["conninfo_to_dict"])
                    fn = getattr(mod, "conninfo_to_dict", None)
                    if fn:
                        info = fn(dsn)
                        out.update({k: info.get(k, "") or ""
                                    for k in ("host", "port", "dbname")})
                        return out
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        try:
            if "://" in dsn:
                from urllib.parse import urlparse
                u = urlparse(dsn)
                out["host"] = u.hostname or ""
                out["port"] = str(u.port or "")
                out["dbname"] = u.path.lstrip("/").split("?", 1)[0]
        except Exception:  # noqa: BLE001
            pass
        return out

    def _db_status(self) -> dict:
        """Postgres ulanish holati: host, port, baza nomi va kechikish (ms).

        ``SELECT 1`` orqali jonli tekshiruv — ``storage.enabled`` faqat sxema
        tayyorligini bildiradi, bu esa haqiqiy ulanish tezligi/ishlashini
        ko'rsatadi. Natija ``system()`` keshida 30s turadi.
        """
        out = {"connected": False, "latency_ms": None, "error": "",
               "host": "", "port": "", "dbname": ""}
        out.update(self._parse_dsn(getattr(self.storage.db, "dsn", "") or ""))
        start = time.monotonic()
        try:
            rows = self.storage.db.query("SELECT 1 AS ok")
            ok = bool(rows and rows[0].get("ok"))
            out["latency_ms"] = int((time.monotonic() - start) * 1000)
            out["connected"] = ok
            if not ok:
                out["error"] = "SELECT 1 javob qaytarmadi"
        except Exception as exc:  # noqa: BLE001 - faqat holatni bildiramiz
            out["connected"] = False
            out["latency_ms"] = 0
            out["error"] = str(exc)[:200]
        return out

    def _bm_status(self) -> dict:
        try:
            from ..api.client import BMClient
            c = BMClient()
            start = time.monotonic()
            resp = c.session.get(c.config.base_url, timeout=5)
            ms = int((time.monotonic() - start) * 1000)
            ok = resp.status_code in (200, 401, 403)
            return {"ok": ok, "status": resp.status_code, "ms": ms,
                    "base_url": c.config.base_url}
        except Exception as exc:  # noqa: BLE001 - faqat holatni bildiramiz
            return {"ok": False, "error": str(exc)[:200], "ms": 0}

    def _telegram_status(self) -> dict:
        s = telegram_settings()
        if not s.get("token"):
            return {"ok": False, "configured": False,
                    "error": "TG_BOT_TOKEN yo'q"}
        try:
            import requests
            resp = requests.get(
                f"https://api.telegram.org/bot{s['token']}/getMe", timeout=5)
            ok = resp.status_code == 200 and resp.json().get("ok")
            return {"ok": bool(ok), "configured": True,
                    "username": resp.json().get("result", {}).get("username", "") if ok else ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "configured": True, "error": str(exc)[:200]}

    def _scheduler_status(self) -> dict:
        import os
        from pathlib import Path

        # 1. Check automation_runs DB table
        runs = self.storage.list_runs(limit=1)
        db_fresh = False
        db_info = {}
        if runs:
            run = runs[0]
            try:
                started = datetime.fromisoformat(run.get("started_at", ""))
                db_fresh = (datetime.now(started.tzinfo) - started).total_seconds() < 86400
            except Exception:  # noqa: BLE001
                pass
            db_info = {"last_run": run.get("started_at", ""),
                       "status": run.get("status", ""),
                       "run_id": run.get("run_id", "")}

        # 2. Check daily_grafik log file freshness
        import tempfile
        grafik_log = Path(tempfile.gettempdir()) / "opencode" / "daily_grafik.log"
        grafik_fresh = False
        grafik_mtime = ""
        try:
            if grafik_log.exists():
                mtime = os.path.getmtime(grafik_log)
                grafik_fresh = (time.time() - mtime) < 86400
                grafik_mtime = datetime.fromtimestamp(mtime).isoformat()
        except Exception:  # noqa: BLE001
            pass

        # 3. Check bot process — PID faylidan tekshirish (oyna ochmaydi)
        bot_running = False
        try:
            from pathlib import Path
            import ctypes
            pid_file = Path('state/bot.pid')
            if pid_file.exists():
                raw = pid_file.read_text(encoding='utf-8', errors='replace').strip()
                pid = int(raw.split('|')[0]) if raw else 0
                if pid > 0:
                    # Windows: PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                        bot_running = True
        except Exception:  # noqa: BLE001
            pass

        ok = db_fresh or grafik_fresh or bot_running
        sources = []
        if db_fresh:
            sources.append("automation_runs")
        if grafik_fresh:
            sources.append(f"daily_grafik ({grafik_mtime[:16]})")
        if bot_running:
            sources.append("bot jarayoni")

        return {
            "ok": ok,
            "last_run": db_info.get("last_run", ""),
            "status": db_info.get("status", ""),
            "run_id": db_info.get("run_id", ""),
            "grafik_log": grafik_mtime,
            "bot_running": bot_running,
            "sources": sources,
            "error": "" if ok else "hech qanday scheduler topilmadi",
        }

    def _last_sync(self) -> str:
        for table in ("trips", "duties", "waybills", "route_daily"):
            rows = self.storage.query(
                f"SELECT MAX(updated_at) AS t FROM {table}", limit=1)
            if rows and rows[0].get("t"):
                return rows[0]["t"]
        return ""

    def _last_active_date(self) -> str:
        """Ma'lumotlar bor bo'lgan so'nggi sana (dashboard bo'sh ko'rinmasligi uchun)."""
        rows = self.storage.query(
            "SELECT MAX(date) AS d FROM trips", limit=1)
        return str(rows[0]["d"]) if rows and rows[0].get("d") else ""


# summary natijasi filtr bo'yicha qisqa muddatga keshlanadi
_SUMMARY_TTL = 10.0
_summary_cache: dict = {"at": 0.0, "key": "", "data": None}


def summary(filters: dict | None = None) -> dict:
    """To'liq dashboard xulosasi (JSON uchun).

    Supabase session pooler'da har so'rov ~165 ms turadi — to'liq xulosa
    20+ so'rov bajaradi. Avto-yangilash (5-30s) natijani qayta hisoblamasligi
    uchun filtr bo'yicha 10 soniyaga keshlanadi.
    """
    m = Metrics()
    if not m.storage.enabled:
        return {
            "ok": False,
            "error": "DB rejimi o'chirilgan yoki bo'sh. "
                     "'python -m bm_automation db init' va 'db sync' ishga tushiring.",
            "system": m.system(),
        }
    f = parse_filters(filters)
    if f.get("month") or f.get("year"):
        _m0 = Metrics()
        _frm, _to = _m0._month_bounds(f)
        f["from"], f["to"] = _frm, _to
        f["date"] = ""
    key = json.dumps(f, sort_keys=True, ensure_ascii=False)
    cached = _summary_cache
    if (cached["data"] is not None and cached["key"] == key
            and time.monotonic() - cached["at"] < _SUMMARY_TTL):
        return cached["data"]
    result = {
        "ok": True,
        "generated_at": now_utc(),
        "filters": f,
        "last_active_date": m._last_active_date(),
        "today": m.today(f),
        "routes": m.routes(f),
        "vehicles": m.vehicles(f),
        "drivers": m.drivers(f),
        "trips": m.trips(f, limit=100),
        "problems": m.problems(f),
        "system": m.system(),
    }
    cached.update(at=time.monotonic(), key=key, data=result)
    return result


def route_options() -> list[dict]:
    """Firma (pill) ro'yxati — faqat profiles.json'dagi ishlaydigan firmalar.

    DB routes jadvalidagi org/park/region tugunlari yoki test yo'nalishlari
    pill bo'lib chiqmaydi. Har kompaniya alohida pill; `route_name` yo'q
    bo'lsa DB routes jadvalidan nom fallback qilinadi.
    """
    m = Metrics()
    companies = m._companies()
    rnames = m._route_names()
    out = []
    for rid, info in companies.items():
        route_name = info["route_name"] or rnames.get(rid, "")
        name = info["company"] or route_name or rid
        out.append({
            "id": rid,
            "name": name,
            "company": info["company"],
            "route_name": route_name,
        })
    return out
