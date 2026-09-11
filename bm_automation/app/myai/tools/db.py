"""DB Tool — database queries for agents."""

from __future__ import annotations

import json
from typing import Any

from . import BaseTool
from ...db.storage import get_storage
from ...utils.logger import get_logger

log = get_logger("myai.tools.db")

# get_route_daily uchun route_id bo'sh bo'lganda barcha yo'nalishlar
_ROUTE_DAILY_COLS = (
    "date, route_id, vehicle_id, vehicle_number, vehicle_brand, shift_name, "
    "working_day, trip_plan, trip_fact, trip_passed, trip_approved, "
    "distance_plan, distance_fact, distance_fact_extra"
)


class DBTool(BaseTool):
    """PostgreSQL database queries."""

    name = "db"
    description = "Database queries — routes, drivers, vehicles, schedules, trips, attendance"

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "query": self._query,
            "get_routes": self._get_routes,
            "get_vehicles": self._get_vehicles,
            "get_route_by_name": self._get_route_by_name,
            "get_schedules": self._get_schedules,
            "get_attendance": self._get_attendance,
            "get_route_daily": self._get_route_daily,
            "get_drivers": self._get_drivers,
            "get_driver_profile": self._get_driver_profile,
            "get_driver_trips": self._get_driver_trips,
            "get_driver_schedule": self._get_driver_schedule,
            "get_driver_work": self._get_driver_work,
            "get_trips": self._get_trips,
            "get_work_logs": self._get_work_logs,
            "get_errors": self._get_errors,
            "get_route_summary": self._get_route_summary,
            "get_all_routes_summary": self._get_all_routes_summary,
            "get_route_trips_detail": self._get_route_trips_detail,
            "get_daily_summary": self._get_daily_summary,
            "get_problems": self._get_problems,
            "get_electricity": self._get_electricity,
            "get_vehicle_detail": self._get_vehicle_detail,
            "get_route_overview": self._get_route_overview,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum db action: {action}")
        return await handler(**kwargs)

    def _storage(self):
        return get_storage()

    def _today(self) -> str:
        from datetime import date as _d
        return _d.today().isoformat()

    async def _resolve_route(self, route_id: str) -> tuple[str, str]:
        """Yo'nalishni ID (UUID) yoki nomdan topib (route_id, route_name) qaytaradi.

        "B-80" → (fe319a0a-..., "B-80"); bo'sh/"Noma'lum" → ("", "").
        """
        storage = self._storage()
        route_id = (route_id or "").strip()
        if not route_id:
            return "", ""
        # UUID kabi ko'rinsa to'g'ridan-to'g'ri
        if "-" in route_id and len(route_id) == 36:
            row = storage.query(
                "SELECT name FROM routes WHERE external_id = %s LIMIT 1", (route_id,))
            name = row[0]["name"] if row else route_id
            return route_id, name
        # Nomi bo'yicha qidirish (B-80, b-80 → ROUTE yozuvi)
        row = storage.query(
            "SELECT external_id, name FROM routes "
            "WHERE LOWER(name) = LOWER(%s) LIMIT 1", (route_id,))
        if not row:
            row = storage.query(
                "SELECT external_id, name FROM routes "
                "WHERE LOWER(name) LIKE LOWER(%s) AND entity_type = 'ROUTE' LIMIT 1",
                (f"%{route_id}%",))
        if row:
            return row[0]["external_id"], row[0]["name"]
        return route_id, route_id

    async def _query(self, sql: str = "", params: tuple = (), **kwargs) -> dict:
        rows = self._storage().query(sql, params)
        return {"rows": rows, "count": len(rows)}

    async def _get_routes(self, query: str = "", **kwargs) -> dict:
        storage = self._storage()
        if query:
            rows = storage.query(
                "SELECT external_id, name, entity_type, is_brutto, parent_id "
                "FROM routes "
                "WHERE name ILIKE %s OR external_id = %s "
                "ORDER BY name",
                (f"%{query}%", query),
            )
        else:
            rows = storage.query(
                "SELECT external_id, name, entity_type, is_brutto, parent_id "
                "FROM routes ORDER BY name",
            )
        return {"routes": rows, "count": len(rows)}

    async def _get_vehicles(self, route_id: str = "", **kwargs) -> dict:
        storage = self._storage()
        if route_id:
            rows = storage.query(
                "SELECT external_id, plate_number, garage_number, model, route_id "
                "FROM vehicles WHERE route_id = %s ORDER BY plate_number",
                (route_id,),
            )
        else:
            rows = storage.query(
                "SELECT external_id, plate_number, garage_number, model, route_id "
                "FROM vehicles ORDER BY plate_number",
            )
        return {"vehicles": rows, "count": len(rows)}

    async def _get_schedules(
        self,
        date: str = "",
        route_id: str = "",
        driver_id: str = "",
        vehicle_id: str = "",
        **kwargs,
    ) -> dict:
        storage = self._storage()
        if not date:
            date = self._today()
        where = ["date = %s"]
        params: list[Any] = [date]
        if route_id:
            where.append("route_id = %s")
            params.append(route_id)
        if driver_id:
            where.append("driver_id = %s")
            params.append(driver_id)
        if vehicle_id:
            where.append("vehicle_id = %s")
            params.append(vehicle_id)
        rows = storage.query(
            "SELECT date, route_id, graph_name, driver_id, vehicle_id, "
            "shift_name, start_time, end_time, trip_count "
            f"FROM schedules WHERE {' AND '.join(where)} "
            "ORDER BY route_id, start_time",
            tuple(params),
        )
        return {"date": date, "route_id": route_id, "schedules": rows, "count": len(rows)}

    async def _get_attendance(self, date: str = "", route_id: str = "", **kwargs) -> dict:
        """Kunlik ishga chiqish tahlili — route_daily (plan vs fact).

        scheduled = working_day=1 bo'lgan avtobuslar,
        present = trip_fact > 0 bo'lganlar.
        """
        storage = self._storage()
        if not date:
            date = self._today()
        where = ["date = %s"]
        params: list[Any] = [date]
        if route_id:
            where.append("route_id = %s")
            params.append(route_id)
        rows = storage.query(
            f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
            f"WHERE {' AND '.join(where)} ORDER BY route_id, vehicle_number",
            tuple(params),
        )
        scheduled = [r for r in rows if int(r.get("working_day", 0) or 0) > 0]
        present = [r for r in scheduled if int(r.get("trip_fact", 0) or 0) > 0]
        absent = [r for r in scheduled if int(r.get("trip_fact", 0) or 0) == 0]
        attended = len(present)
        total = len(scheduled)
        if total == 0 and rows:
            # working_day belgilanmagan bo'lsa — rejasiz bo'lsa ham fakt bo'yicha
            present = [r for r in rows if int(r.get("trip_fact", 0) or 0) > 0]
            scheduled = rows
            total = len(scheduled)
            attended = len(present)
        return {
            "date": date,
            "route_id": route_id,
            "total_drivers": total,
            "present": attended,
            "absent": total - attended,
            "attendance_rate": round(attended / total * 100, 1) if total else 0,
            "scheduled": [
                {
                    "vehicle_number": r.get("vehicle_number", ""),
                    "vehicle_brand": r.get("vehicle_brand", ""),
                    "shift_name": r.get("shift_name", ""),
                    "trip_plan": int(r.get("trip_plan", 0) or 0),
                    "trip_fact": int(r.get("trip_fact", 0) or 0),
                    "distance_fact": round(float(r.get("distance_fact", 0) or 0), 2),
                }
                for r in scheduled
            ],
            "absent_list": [
                {
                    "vehicle_number": r.get("vehicle_number", ""),
                    "shift_name": r.get("shift_name", ""),
                    "trip_plan": int(r.get("trip_plan", 0) or 0),
                }
                for r in absent
            ],
        }

    async def _get_route_daily(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        storage = self._storage()
        if not date:
            date = self._today()
        if route_id:
            rows = storage.query(
                f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
                "WHERE route_id = %s AND date = %s ORDER BY vehicle_number",
                (route_id, date),
            )
        else:
            rows = storage.query(
                f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
                "WHERE date = %s ORDER BY route_id, vehicle_number",
                (date,),
            )
        return {"route_id": route_id, "date": date, "data": rows}

    async def _get_drivers(
        self, route_id: str = "", query: str = "", limit: int = 200, **kwargs
    ) -> dict:
        storage = self._storage()
        where = []
        params: list[Any] = []
        if route_id:
            where.append("d.route_id = %s")
            params.append(route_id)
        if query:
            where.append("(d.full_name ILIKE %s OR d.tin LIKE %s OR d.external_id = %s)")
            params.extend((f"%{query}%", query, query))
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        rows = storage.query(
            "SELECT d.external_id, d.full_name, d.tin, d.route_id, "
            "COALESCE(p.phone, '') AS phone, "
            "COALESCE(p.rating, 5) AS rating, "
            "COALESCE(p.blacklisted, 0) AS blacklisted, "
            "COALESCE(p.blacklist_reason, '') AS blacklist_reason, "
            "COALESCE(p.km_rate, 0) AS km_rate, "
            "COALESCE(p.notification_enabled, 0) AS notification_enabled "
            "FROM drivers d "
            "LEFT JOIN driver_profiles p ON p.driver_id = d.external_id"
            f"{where_sql} ORDER BY d.full_name LIMIT {int(limit)}",
            tuple(params),
        )
        return {"drivers": rows, "count": len(rows)}

    async def _get_route_by_name(self, query: str = "", **kwargs) -> dict:
        """Yo'nalishni nomi yoki ID bo'yicha topib, ma'lumot keltiradi."""
        route_id, route_name = await self._resolve_route(query)
        if not route_id:
            return {"route": None, "found": False, "query": query}
        vehicles = await self._get_vehicles(route_id=route_id)
        scheduled = await self._get_schedules(route_id=route_id)
        daily = await self._get_route_daily(route_id=route_id)
        return {
            "route_id": route_id,
            "route_name": route_name,
            "vehicles_count": vehicles.get("count", 0),
            "scheduled_count": scheduled.get("count", 0),
            "daily_rows": len(daily.get("data", [])),
            "vehicles": vehicles.get("vehicles", [])[:50],
            "daily": daily.get("data", [])[:50],
            "found": True,
        }

    async def _resolve_driver(self, driver: str = "") -> tuple[str, str]:
        """Haydovchini ism/TIN/external_id bo'yicha topib (driver_id, full_name)."""
        storage = self._storage()
        driver = (driver or "").strip()
        if not driver:
            return "", ""
        row = storage.query(
            "SELECT external_id, full_name FROM drivers "
            "WHERE LOWER(full_name) = LOWER(%s) OR external_id = %s LIMIT 1",
            (driver, driver))
        if not row:
            row = storage.query(
                "SELECT external_id, full_name FROM drivers "
                "WHERE LOWER(full_name) LIKE LOWER(%s) LIMIT 1",
                (f"%{driver}%",))
        if not row:
            return driver, driver
        return row[0]["external_id"], row[0]["full_name"]

    async def _get_driver_trips(self, driver: str = "", driver_id: str = "",
                                date: str = "", **kwargs) -> dict:
        """Haydovchining kunlik reyslari (trips jadvalidan)."""
        storage = self._storage()
        if not date:
            date = self._today()
        if not driver_id:
            driver_id, _ = await self._resolve_driver(driver)
        if not driver_id:
            return {"driver": driver, "date": date, "trips": [], "count": 0,
                    "summary": {}}
        rows = storage.query(
            "SELECT t.date, t.route_id, t.vehicle_id, t.driver_id, t.planned_time, "
            "t.actual_time, t.status, t.distance_km, "
            "(t.data::json->>'plateNum') AS plate, "
            "COALESCE((t.data::json->>'routeName'), r.name, '') AS route_name, "
            "(t.data::json->>'shiftGraphName') AS shift "
            "FROM trips t "
            "LEFT JOIN routes r ON r.external_id = t.route_id "
            "WHERE t.driver_id = %s AND t.date = %s "
            "ORDER BY t.planned_time",
            (driver_id, date))
        trips = []
        for r in rows:
            trips.append({
                "id": r.get("id"),
                "route_id": r.get("route_id", ""),
                "route_name": r.get("route_name") or r.get("route_id", ""),
                "plate": r.get("plate", ""),
                "shift": r.get("shift", ""),
                "planned_time": r.get("planned_time", ""),
                "actual_time": r.get("actual_time", ""),
                "status": r.get("status", ""),
                "distance_km": float(r.get("distance_km", 0) or 0),
            })
        accepted = sum(1 for t in trips if t["status"] == "ACCEPTED")
        return {
            "driver": driver,
            "date": date,
            "trips": trips,
            "count": len(trips),
            "summary": {
                "total_trips": len(trips),
                "accepted": accepted,
                "not_accepted": len(trips) - accepted,
                "total_km": round(sum(t["distance_km"] for t in trips), 2),
                "completion_rate": round(accepted / len(trips) * 100, 1)
                if trips else 0,
            },
        }

    async def _get_driver_schedule(self, driver: str = "", driver_id: str = "",
                                   date: str = "", **kwargs) -> dict:
        """Haydovchining kunlik jadvali (schedules jadvalidan)."""
        storage = self._storage()
        if not date:
            date = self._today()
        if not driver_id:
            driver_id, full_name = await self._resolve_driver(driver)
            if not driver_id:
                return {"driver": driver, "date": date, "schedules": [], "count": 0}
        else:
            full_name = driver
        rows = storage.query(
            "SELECT s.date, s.route_id, s.graph_name, s.driver_id, s.vehicle_id, "
            "s.shift_name, s.start_time, s.end_time, s.trip_count, "
            "COALESCE(r.name, '') AS route_name "
            "FROM schedules s "
            "LEFT JOIN routes r ON r.external_id = s.route_id "
            "WHERE s.driver_id = %s AND s.date = %s "
            "ORDER BY s.start_time",
            (driver_id, date))
        return {
            "driver": full_name or driver,
            "date": date,
            "schedules": rows,
            "count": len(rows),
        }

    async def _get_driver_work(self, driver: str = "", driver_id: str = "",
                               date: str = "", days: int = 30, **kwargs) -> dict:
        """Haydovchi ish jurnali + profili (driver_work_logs + driver_profiles)."""
        storage = self._storage()
        if not driver_id:
            driver_id, full_name = await self._resolve_driver(driver)
            if not driver_id:
                return {"driver": driver, "found": False, "work_logs": []}
        else:
            full_name = driver
        logs = storage.query(
            "SELECT date, vehicle_id, distance_km, trip_count, note "
            "FROM driver_work_logs WHERE driver_id = %s "
            "ORDER BY date DESC LIMIT %s",
            (driver_id, int(days)))
        profile = storage.query(
            "SELECT phone, rating, blacklisted, blacklist_reason, km_rate, "
            "notification_enabled, notes "
            "FROM driver_profiles WHERE driver_id = %s LIMIT 1",
            (driver_id,))
        return {
            "driver": full_name or driver,
            "driver_id": driver_id,
            "found": True,
            "profile": profile[0] if profile else None,
            "work_logs": logs,
            "work_logs_count": len(logs),
        }

    async def _get_driver_profile(self, driver_id: str = "", **kwargs) -> dict:
        storage = self._storage()
        if not driver_id:
            return {"driver": None, "found": False}
        row = storage.query(
            "SELECT * FROM driver_profiles WHERE driver_id = %s LIMIT 1",
            (driver_id,),
        )
        return {"driver": row[0] if row else None, "found": bool(row)}

    async def _get_trips(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        storage = self._storage()
        rows = storage.query(
            "SELECT * FROM trips WHERE route_id = %s AND date = %s",
            (route_id, date),
        )
        return {"trips": rows, "count": len(rows)}

    async def _get_work_logs(self, driver_id: str = "", date: str = "", **kwargs) -> dict:
        storage = self._storage()
        if driver_id and date:
            rows = storage.query(
                "SELECT * FROM driver_work_logs WHERE driver_id = %s AND date = %s",
                (driver_id, date),
            )
        elif driver_id:
            rows = storage.query(
                "SELECT * FROM driver_work_logs WHERE driver_id = %s ORDER BY date DESC LIMIT 30",
                (driver_id,),
            )
        else:
            rows = storage.query(
                "SELECT * FROM driver_work_logs WHERE date = %s",
                (date,),
            )
        return {"work_logs": rows, "count": len(rows)}

    async def _get_errors(self, days: int = 7, **kwargs) -> dict:
        storage = self._storage()
        rows = storage.query(
            "SELECT * FROM errors WHERE created_at >= NOW() - make_interval(days => %s) ORDER BY created_at DESC",
            (days,),
        )
        return {"errors": rows, "count": len(rows)}

    async def _get_route_summary(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        """Bitta yo'nalish bo'yicha kunlik xulosa — DB dan."""
        storage = self._storage()
        if not date:
            date = self._today()
        route_uuid, route_name = await self._resolve_route(route_id)
        lookup_id = route_uuid or route_id
        # Try route_id as UUID first, then as name
        rows = storage.query(
            "SELECT * FROM trips WHERE route_id = %s AND date = %s",
            (lookup_id, date),
        )
        if not rows and route_name and route_name != route_id:
            rows = storage.query(
                "SELECT * FROM trips WHERE route_id = %s AND date = %s",
                (route_uuid, date),
            )
        vehicles = []
        for r in rows:
            d = {}
            if r.get("data"):
                try:
                    d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                except Exception:
                    pass
            vehicles.append({
                "bus_number": d.get("plateNum", ""),
                "route": route_name,
                "route_name": route_name,
                "route_total": 1,
                "driver": d.get("driverName", ""),
                "status": r.get("status", ""),
                "km": r.get("distance_km", 0) or 0,
                "trip_num": d.get("tripNum", 0),
                "trips": 1 if r.get("status") == "ACCEPTED" else 0,
                "shift": d.get("shiftGraphName", ""),
                "direction": d.get("direction", ""),
            })
        total = len(vehicles)
        accepted = sum(1 for v in vehicles if v["status"] == "ACCEPTED")
        total_km = sum(v["km"] for v in vehicles)
        vehicle_count = len({r.get("vehicle_id") for r in rows if r.get("vehicle_id")})
        rate = round(accepted / total * 100, 1) if total else 0
        return {
            "route_id": route_uuid or route_id,
            "route_name": route_name,
            "date": date,
            "vehicles": vehicles,
            "trip_count": total,
            "vehicle_count": vehicle_count,
            "summary": {
                "total_trips": total,
                "accepted": accepted,
                "not_accepted": total - accepted,
                "total_km": round(total_km, 2),
                "completion_rate": rate,
                "total_vehicles": vehicle_count,
                "total_buses": vehicle_count,
                "units": "trips",
            },
        }

    async def _get_all_routes_summary(self, date: str = "", **kwargs) -> dict:
        """Barcha yo'nalishlar bo'yicha kunlik xulosa."""
        storage = self._storage()
        if not date:
            from datetime import date as _d
            date = _d.today().isoformat()
        rows = storage.query(
            """SELECT (data::json->>'routeName') AS route_name,
                      COUNT(*) AS total,
                      COUNT(DISTINCT vehicle_id) AS vehicles,
                      SUM(CASE WHEN status='ACCEPTED' THEN 1 ELSE 0 END) AS accepted,
                      COALESCE(SUM(distance_km), 0) AS total_km
               FROM trips WHERE date = %s
               GROUP BY (data::json->>'routeName')
               ORDER BY total DESC""",
            (date,),
        )
        routes = []
        for r in rows:
            name = r["route_name"] or "Noma'lum"
            total = r["total"]
            accepted = int(r["accepted"] or 0)
            vehicle_count = int(r["vehicles"] or 0)
            routes.append({
                "route_name": name,
                "total_trips": total,
                "accepted": accepted,
                "not_accepted": total - accepted,
                "total_km": round(float(r["total_km"] or 0), 2),
                "completion_rate": round(accepted / total * 100, 1) if total else 0,
                "total_vehicles": vehicle_count,
            })
        total_all = sum(r["total_trips"] for r in routes)
        accepted_all = sum(r["accepted"] for r in routes)
        km_all = sum(r["total_km"] for r in routes)
        return {
            "date": date,
            "routes": routes,
            "summary": {
                "total_trips": total_all,
                "accepted": accepted_all,
                "not_accepted": total_all - accepted_all,
                "total_km": round(km_all, 2),
                "completion_rate": round(accepted_all / total_all * 100, 1) if total_all else 0,
                "units": "trips",
            },
        }

    async def _get_route_trips_detail(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        """Yo'nalish bo'yicha batafsil reyslar."""
        storage = self._storage()
        if not date:
            date = self._today()
        route_uuid, route_name = await self._resolve_route(route_id)
        where = "t.date = %s"
        params: list[Any] = [date]
        if route_uuid or route_id:
            where += " AND t.route_id = %s"
            params.append(route_uuid or route_id)
        rows = storage.query(
            f"""SELECT t.status, t.distance_km,
                      (t.data::json->>'driverName') AS driver_name,
                      (t.data::json->>'plateNum') AS plate,
                      (t.data::json->>'tripNum') AS trip_num,
                      (t.data::json->>'routeName') AS route_name,
                      (t.data::json->>'shiftGraphName') AS shift,
                      t.planned_time, t.actual_time
               FROM trips t
               WHERE {where}
               ORDER BY t.route_id, t.planned_time""",
            tuple(params),
        )
        return {"date": date, "route_id": route_uuid or route_id,
                "route_name": route_name, "trips": rows, "count": len(rows)}

    def _metrics(self):
        """Rich dashboard Metrics — kunlik/oylik fire data manbai."""
        from ...dashboard.metrics import Metrics
        return Metrics()

    @staticmethod
    def _period_filter(month: str = "", date: str = "", route_id: str = "") -> dict:
        """DB agent'lar uchun Metrics filter'ini tuzadi.

        month ("2026-08") ustuvor; aks holda date — bu holda *shu kunning
        o'zi* (from=to=date) olinadi, butun oy emas. route UUID/nom qo'shiladi.
        """
        from datetime import date as _date
        f: dict[str, Any] = {}
        if str(month or "").strip():
            f["month"] = str(month).strip()
        elif str(date or "").strip():
            f["from"] = str(date).strip()
            f["to"] = str(date).strip()
        else:
            today = _date.today().isoformat()
            f["from"] = today
            f["to"] = today
        rid = str(route_id or "").strip()
        if rid:
            f["route"] = rid
        return f

    async def _get_daily_summary(self, date: str = "", route_id: str = "",
                                 month: str = "", **kwargs) -> dict:
        """Rich kunlik/davr xulosasi — Metrics.monthly() asosida.

        rejalashtirilgan, amalda, qabul qilingan, muammolar, performance,
        accept_rate kabi ko'rsatkichlar bilan (single/day bo'yicha).
        """
        m = self._metrics()
        f = self._period_filter(month=month, date=date, route_id=route_id)
        try:
            mon = m.monthly(dict(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_daily_summary xatosi: %s", exc)
            return {"found": False, "error": str(exc)}
        days = mon.get("days") or []
        t = mon.get("totals", {})
        # Readable route name (if route_id given as name, resolve)
        route_uuid, route_name = await self._resolve_route(route_id)
        return {
            "found": bool(days),
            "date": f.get("from", ""),
            "month": f.get("month", ""),
            "route_id": route_uuid or route_id or "",
            "route_name": route_name,
            "days": len(days),
            "period": {"from": f.get("from", ""), "to": f.get("to", "")},
            "daily": days[:40],
            "totals": {
                "planned": t.get("planned", 0),
                "actual": t.get("actual", 0),
                "completed": t.get("completed", 0),
                "accepted": t.get("accepted", 0),
                "not_accepted": t.get("not_accepted", 0),
                "pending": t.get("pending", 0),
                "rejected": t.get("rejected", 0),
                "zero_mileage": t.get("zero_mileage", 0),
                "problems_total": t.get("problems_total", 0),
                "performance": t.get("performance", 0.0),
                "accept_rate": t.get("accept_rate", 0.0),
            },
        }

    async def _get_problems(self, date: str = "", route_id: str = "",
                            month: str = "", **kwargs) -> dict:
        """Muammolar tahlili — GPS, texnik, jadval (Metrics.monthly dan)."""
        m = self._metrics()
        f = self._period_filter(month=month, date=date, route_id=route_id)
        try:
            mon = m.monthly(dict(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_problems xatosi: %s", exc)
            return {"found": False, "error": str(exc)}
        days = mon.get("days") or []
        problems = mon.get("totals", {}).get("problems", {})
        # Detail bo'yicha kunlar
        problem_days = [
            {"date": d.get("date"), "problems_total": d.get("problems_total", 0),
             "gps": d["problems"].get("gps", 0),
             "technical": d["problems"].get("technical", 0),
             "schedule": d["problems"].get("schedule", 0)}
            for d in days if d.get("problems_total")
        ]
        return {
            "found": bool(problems),
            "date": f.get("from", ""),
            "month": f.get("month", ""),
            "route_id": route_id or "",
            "problems": {
                "gps": int(problems.get("gps", 0)),
                "technical": int(problems.get("technical", 0)),
                "schedule": int(problems.get("schedule", 0)),
                "total": int(problems.get("gps", 0))
                + int(problems.get("technical", 0))
                + int(problems.get("schedule", 0)),
            },
            "problem_days": problem_days,
        }

    async def _get_electricity(self, month: str = "", date: str = "",
                               route_id: str = "", **kwargs) -> dict:
        """Elektr energiya xisoboti — km * ELEC_KWH_PER_KM * narx.

        Haydovchi / yo'nalish / kompaniya bo'yicha kVt·soat va so'm.
        """
        m = self._metrics()
        f = self._period_filter(month=month, date=date, route_id=route_id)
        # electricity_report kunlik blur olishi uchun month/from+to kerak
        try:
            rep = m.electricity_report(dict(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_electricity xatosi: %s", exc)
            return {"found": False, "error": str(exc)}
        return {
            "found": bool(rep.get("drivers")),
            "period": rep.get("period", {}),
            "kwh_per_km": rep.get("kwh_per_km", 0),
            "rate": rep.get("rate", 0),
            "totals": rep.get("totals", {}),
            "drivers": rep.get("drivers", [])[:200],
            "routes": rep.get("routes", []),
        }

    async def _resolve_vehicle(self, plate: str = "") -> tuple[str, str]:
        """Avtobusni raqam/garaj ID bo'yicha topib (vehicle_id, plate)."""
        storage = self._storage()
        plate = (plate or "").strip()
        if not plate:
            return "", ""
        row = storage.query(
            "SELECT external_id, plate_number FROM vehicles "
            "WHERE LOWER(plate_number) = LOWER(%s) "
            "OR LOWER(garage_number) = LOWER(%s) "
            "OR external_id = %s LIMIT 1",
            (plate, plate, plate))
        if not row:
            row = storage.query(
                "SELECT external_id, plate_number FROM vehicles "
                "WHERE LOWER(plate_number) LIKE LOWER(%s) LIMIT 1",
                (f"%{plate}%",))
        if not row:
            return plate, plate
        return str(row[0]["external_id"]), str(row[0]["plate_number"])

    async def _get_vehicle_detail(self, vehicle: str = "", plate: str = "",
                                  date: str = "", **kwargs) -> dict:
        """Avtobus profili: ma'lumot + so'nggi reyslar va kunlik yozuvlar."""
        storage = self._storage()
        vid, plate_num = await self._resolve_vehicle(vehicle or plate)
        rows = storage.query(
            "SELECT external_id, plate_number, garage_number, model, route_id "
            "FROM vehicles WHERE external_id = %s LIMIT 1", (vid,))
        if not rows:
            return {"found": False, "query": vehicle or plate or ""}
        veh = rows[0]
        if not date:
            date = self._today()
        daily = storage.query(
            f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
            "WHERE vehicle_id = %s AND date = %s "
            "ORDER BY shift_name", (vid, date))
        trips = storage.query(
            "SELECT date, route_id, driver_id, planned_time, actual_time, status, "
            "distance_km, (data::json->>'routeName') AS route_name "
            "FROM trips WHERE vehicle_id = %s AND date = %s "
            "ORDER BY planned_time", (vid, date))
        return {
            "found": True,
            "vehicle": {
                "id": veh.get("external_id", ""),
                "plate_number": veh.get("plate_number", ""),
                "garage_number": veh.get("garage_number", ""),
                "model": veh.get("model", ""),
                "route_id": veh.get("route_id", ""),
            },
            "date": date,
            "route_daily": [
                {
                    "vehicle_number": r.get("vehicle_number", ""),
                    "shift_name": r.get("shift_name", ""),
                    "working_day": int(r.get("working_day", 0) or 0),
                    "trip_plan": int(r.get("trip_plan", 0) or 0),
                    "trip_fact": int(r.get("trip_fact", 0) or 0),
                    "distance_fact": round(float(r.get("distance_fact", 0) or 0), 2),
                }
                for r in daily
            ],
            "trips": trips,
            "trips_count": len(trips),
        }

    async def _get_route_overview(self, route_id: str = "", date: str = "",
                                  month: str = "", **kwargs) -> dict:
        """Yo'nalish bo'yicha boy xulosa — profilli + kunlik + muammolar."""
        if not date and not month:
            date = self._today()
        route_uuid, route_name = await self._resolve_route(route_id)
        lookup = route_uuid or route_id
        prof = await self._get_route_by_name(query=lookup or route_name)
        daily = await self._get_daily_summary(
            date=date, route_id=lookup, month=month)
        problems = await self._get_problems(
            date=date, route_id=lookup, month=month)
        return {
            "found": prof.get("found", False),
            "route_id": route_uuid or route_id,
            "route_name": route_name,
            "profile": prof,
            "daily": daily,
            "problems": problems,
        }
