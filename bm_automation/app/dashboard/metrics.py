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

import time
from datetime import date, datetime, timedelta
from typing import Any

from ..config.settings import telegram_settings
from ..db.models import TripStatus, now_utc
from ..db.storage import get_storage
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
        filters["date"] = date.today().isoformat()
    if not filters["from"] and filters["date"]:
        filters["from"] = filters["date"]
    if not filters["to"]:
        filters["to"] = filters["from"] or filters["date"] or date.today().isoformat()
    for _k, _v in p.items():
        if _k not in filters and _v:
            filters[_k] = _v
    return filters


class Metrics:
    """DB asosidagi dashboard metrikalari."""

    def __init__(self, storage=None):
        self.storage = storage if storage is not None else get_storage()
        self.ph = self.storage.db.ph

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

        total_trips = self._count("trips", where, tuple(params))

        completed = self._count(
            "trips",
            where + (f" AND (actual_time != '' OR status IN ({ph},{ph}))" if where else
                     f"actual_time != '' OR status IN ({ph},{ph})"),
            tuple(params) + tuple(_DONE_STATUSES),
        )

        statuses = {}
        for st in TripStatus.values():
            st_where = f"{where} AND status = {ph}" if where else f"status = {ph}"
            statuses[st] = self._count("trips", st_where,
                                       tuple(params) + (st,))

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

        return {
            "date": f.get("date") or f.get("from", ""),
            "total_buses": total_buses,
            "active_buses": active_vehicles,
            "not_active_buses": max(total_buses - active_vehicles, 0),
            "total_trips": total_trips,
            "completed": completed,
            "accepted": statuses.get("ACCEPTED", 0),
            "not_accepted": statuses.get("NOT_ACCEPTED", 0),
            "pending": statuses.get("PENDING_ACCESS", 0),
            "approved": statuses.get("APPROVED", 0),
            "rejected": statuses.get("REJECTED", 0),
            "zero_mileage": statuses.get("ZERO_MILEAGE", 0),
            "statuses": statuses,
        }

    def _route_names(self) -> dict:
        """Yo'nalish nomlari: DB routes jadvali + profiles.json fallback."""
        names = {r["external_id"]: (r["name"] or r["external_id"])
                 for r in self.storage.query(
                     "SELECT external_id, name FROM routes WHERE entity_type='ROUTE'")}
        from ..core.profiles import all_profiles
        for p in all_profiles():
            rid = str(p.get("routeVariantId") or "").strip()
            rn = str(p.get("routeName") or "").strip()
            if rid and rn:
                names.setdefault(rid, rn)
        return names

    # ---------------------------------------------------------------- routes

    def routes(self, f: dict) -> list[dict]:
        where, params = self._trip_where(f)
        w = f" AND {where}" if where else ""
        ph = self.ph
        sql = (
            f"SELECT route_id,"
            f" COUNT(*) AS planned,"
            f" SUM(CASE WHEN actual_time != '' THEN 1 ELSE 0 END) AS actual,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS not_accepted,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS rejected,"
            f" SUM(CASE WHEN status = {ph} THEN 1 ELSE 0 END) AS zero_mileage"
            f" FROM trips"
            f" WHERE route_id != ''{w}"
            f" GROUP BY route_id ORDER BY route_id"
        )
        rows = self.storage.query(
            sql,
            (TripStatus.ACCEPTED.value, TripStatus.NOT_ACCEPTED.value,
             TripStatus.REJECTED.value, TripStatus.ZERO_MILEAGE.value)
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

        names = self._route_names()

        out = []
        for r in rows:
            planned = plans.get(r["route_id"]) or int(r["planned"] or 0)
            actual = int(r["actual"] or 0)
            perf = min(round(actual / planned * 100, 1), 100.0) if planned else 0.0
            out.append({
                "route_id": r["route_id"],
                "name": names.get(r["route_id"], r["route_id"]),
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

    # --------------------------------------------------------------- drivers

    def drivers(self, f: dict) -> list[dict]:
        where, params = self._trip_where(f)
        ph = self.ph
        sql = (
            f"SELECT driver_id, COUNT(*) AS trips, COUNT(DISTINCT date) AS days,"
            f" SUM(CASE WHEN status IN ({ph},{ph}) THEN 1 ELSE 0 END) AS issues"
            f" FROM trips WHERE driver_id != ''"
            f"{(' AND ' + where) if where else ''}"
            f" GROUP BY driver_id ORDER BY trips DESC"
        )
        rows = self.storage.query(sql, tuple(_ISSUE_STATUSES) + tuple(params))

        total_days = 1
        try:
            frm = date.fromisoformat(f.get("from") or f.get("date", ""))
            to = date.fromisoformat(f.get("to") or f.get("from", ""))
            total_days = max((to - frm).days + 1, 1)
        except ValueError:
            total_days = 1

        names = {r["external_id"]: r["full_name"] for r in self.storage.query(
            "SELECT external_id, full_name FROM drivers")}

        out = []
        for r in rows:
            days = int(r["days"] or 0)
            out.append({
                "driver_id": r["driver_id"],
                "name": names.get(r["driver_id"], r["driver_id"]),
                "trips": int(r["trips"] or 0),
                "working_days": days,
                "issues": int(r["issues"] or 0),
                "attendance": round(days / total_days * 100, 1),
                "total_days": total_days,
            })
        return out

    # ----------------------------------------------------------------- trips

    def trips(self, f: dict, limit: int = 200) -> list[dict]:
        where, params = self._trip_where(f)
        sql = "SELECT * FROM trips"
        if where:
            sql += f" WHERE {where}"
        sql += " ORDER BY date DESC, planned_time ASC"
        return self.storage.query(sql, tuple(params), limit=limit)

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
        - **technical** — `ZERO_MILEAGE` / `REJECTED` statusli avtobuslar;
        - **schedule** — `NOT_ACCEPTED` / `PENDING_ACCESS` reyslar (jadval
          qabul qilinmagan yoki tasdiqlanmagan);
        - **unknown** — noma'lum status yoki yo'nalish/avtobus bog'lanmagan.

        `counts` + har bir kategoriya uchun `items` (batafsil) qaytaradi.
        """
        where, params = self._trip_where(f)
        sql = "SELECT * FROM trips"
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
            if st in ("ZERO_MILEAGE", "REJECTED"):
                tech_veh.setdefault(vid, _item(r))
            elif st in ("NOT_ACCEPTED", "PENDING_ACCESS"):
                schedule_items.append(_item(r))
            elif st in ("ACCEPTED", "APPROVED") and not r.get("actual_time"):
                gps_veh.setdefault(vid, _item(r))

        # GPS — texnik muammosi bo'lmagan avtobuslar (bitta avtobus bitta muammo)
        for vid in list(gps_veh):
            if vid in tech_veh:
                del gps_veh[vid]

        # ZERO_MILEAGE waybill qatnovlari trips'ga kirmaydi (haqiqiy qatnov
        # emas) — shu sababli texnik muammolar ichiga waybills'dan qo'shamiz.
        w_where, w_params = self._trip_where(f)
        for wb in self.storage.query(
            "SELECT date, route_id, vehicle_id, driver_id, plate_number, status"
            " FROM waybills" + (f" WHERE {w_where}" if w_where else ""),
            tuple(w_params), limit=1000):
            if wb.get("status") not in ("ZERO_MILEAGE", "REJECTED"):
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

    def system(self) -> dict:
        st = self.storage
        bm = self._bm_status()
        db = {"ok": st.enabled,
              "driver": st.db.driver if st.enabled else "-"}
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
        return {
            "api": bm,
            "database": db,
            "telegram": tg,
            "scheduler": sched,
            "last_sync": last_sync,
            "last_error": last_error,
            "checked_at": now_utc(),
        }

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
        runs = self.storage.list_runs(limit=1)
        if not runs:
            return {"ok": False, "last_run": None,
                    "error": "avtomatik vazifa yozilmagan"}
        run = runs[0]
        try:
            started = datetime.fromisoformat(run.get("started_at", ""))
            fresh = (datetime.now(started.tzinfo) - started).total_seconds() < 86400
        except Exception:  # noqa: BLE001
            fresh = False
        return {"ok": run.get("status") == "RUNNING" or fresh,
                "last_run": run.get("started_at", ""),
                "status": run.get("status", ""),
                "run_id": run.get("run_id", "")}

    def _last_sync(self) -> str:
        for table in ("trips", "duties", "waybills"):
            rows = self.storage.query(
                f"SELECT MAX(updated_at) AS t FROM {table}", limit=1)
            if rows and rows[0].get("t"):
                return rows[0]["t"]
        return ""


def summary(filters: dict | None = None) -> dict:
    """To'liq dashboard xulosasi (JSON uchun)."""
    m = Metrics()
    if not m.storage.enabled:
        return {
            "ok": False,
            "error": "DB rejimi o'chirilgan yoki bo'sh. "
                     "'python -m bm_automation db init' va 'db sync' ishga tushiring.",
            "system": m.system(),
        }
    f = parse_filters(filters)
    return {
        "ok": True,
        "generated_at": now_utc(),
        "filters": f,
        "today": m.today(f),
        "routes": m.routes(f),
        "vehicles": m.vehicles(f),
        "drivers": m.drivers(f),
        "trips": m.trips(f),
        "system": m.system(),
    }


def route_options() -> list[dict]:
    """Filter dropdown uchun yo'nalishlar ro'yxati."""
    m = Metrics()
    rows = m.storage.query(
        "SELECT external_id, name FROM routes ORDER BY name")
    return [{"id": r["external_id"], "name": r["name"] or r["external_id"]}
            for r in rows]
