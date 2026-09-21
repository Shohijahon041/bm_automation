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
    description = ("Database queries — routes, drivers, vehicles, schedules, "
                   "trips, attendance, not-accepted km report, avans, "
                   "fines, waybills, duties, documents, staff, SMS, "
                   "notifications, reports, dispatcher routes")

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
            "get_not_accepted_km": self._get_not_accepted_km,
            "get_vehicle_detail": self._get_vehicle_detail,
            "get_route_overview": self._get_route_overview,
            "get_route_vehicles": self._get_route_vehicles,
            "get_trip_anomalies": self._get_trip_anomalies,
            "get_route_health": self._get_route_health,
            "get_avans": self._get_avans,
            "get_driver_fines": self._get_driver_fines,
            "get_waybills": self._get_waybills,
            "get_duties": self._get_duties,
            "get_dispatcher_routes": self._get_dispatcher_routes,
            "get_documents": self._get_documents,
            "get_staff": self._get_staff,
            "get_sms": self._get_sms,
            "get_notifications": self._get_notifications,
            "get_reports": self._get_reports,
            "get_automation_runs": self._get_automation_runs,
            "get_trip_statuses": self._get_trip_statuses,
            "rows": self._rows,
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

    async def _resolve_route_uid(self, route_id: str = "") -> str:
        """route_id (UUID yoki yo'nalish nomi) → UUID qaytaradi.

        Nom ("B-80") berilsa routes jadvalidan UUID topiladi; topilmasa
        asl qiymat qaytadi (to'g'ridan-to'g'ri so'rovda mos kelmasa bo'sh).
        """
        rid = (route_id or "").strip()
        if not rid:
            return ""
        uid, _ = await self._resolve_route(rid)
        return uid or rid

    async def _route_no_data(self, uid: str, date: str) -> tuple[bool, str]:
        """(no_data, latest_date) — route_daily da berilgan sana uchun yozuv
        yo'qligi va oxirgi mavjud sana haqida ma'lumot qaytaradi.

        no_data=True bo'lsa o'sha sana uchun hech qanday yozuv yo'q
        (ma'lumot hali yuklanmagan bo'lishi mumkin); latest_date — shu
        sanagacha bo'lgan oxirgi yozuv sanasi ("" bo'lsa umuman yo'q).
        """
        if not uid or not date:
            return False, ""
        rows = self._storage().query(
            "SELECT MAX(date) AS m FROM route_daily "
            "WHERE route_id = %s AND date <= %s", (uid, date))
        latest = rows[0]["m"] if rows and rows[0].get("m") else ""
        has_day = bool(latest) and str(latest) == str(date)
        return (not has_day), (str(latest) if latest else "")

    def _normalize_date(self, value: str) -> str:
        """Istalgan sana formatini ISO (YYYY-MM-DD) ga keltiradi.

        "13-sentabr", "13 sentyabr 2026", "13.09.2026", "13.09",
        "2026-09-13" → "2026-09-13". Agar aniqlanmasa asl qiymat qaytadi.
        """
        from datetime import date as _date
        import re as _re
        v = (value or "").strip()
        if not v:
            return ""
        from datetime import timedelta as _td
        if v.lower() in ("kecha", "bugun", "ertaga"):
            base = _date.today()
            if v.lower() == "kecha":
                return (base - _td(days=1)).isoformat()
            if v.lower() == "ertaga":
                return (base + _td(days=1)).isoformat()
            return base.isoformat()
        if _re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            try:
                return _date(*map(int, v.split("-"))).isoformat()
            except (TypeError, ValueError):
                return v
        m = _re.match(r"^(\d{1,2})[.\/](\d{1,2})[.\/](\d{2,4})$", v)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = 2000 + y if y < 100 else y
            try:
                return _date(y, mo, d).isoformat()
            except (TypeError, ValueError):
                return v
        m = _re.match(r"^(\d{1,2})[.\/](\d{1,2})$", v)
        if m:
            try:
                return _date(_date.today().year, int(m.group(2)),
                             int(m.group(1))).isoformat()
            except (TypeError, ValueError):
                return v
        months = {
            "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5,
            "iyun": 6, "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9,
            "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
        }
        low = v.lower()
        year = _date.today().year
        ym = _re.search(r"\b(20\d{2})\b", low)
        if ym:
            year = int(ym.group(1))
        for name, num in sorted(
                months.items(), key=lambda kv: len(kv[0]), reverse=True):
            m = _re.search(
                r"\b(\d{1,2})\s*[-–']?\s*" + name + r"(?:dagi|da|gi)?\b", low)
            if m:
                try:
                    return _date(year, num, int(m.group(1))).isoformat()
                except (TypeError, ValueError):
                    continue
        return v

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
        rid = await self._resolve_route_uid(route_id)
        if rid:
            rows = storage.query(
                "SELECT external_id, plate_number, garage_number, model, route_id "
                "FROM vehicles WHERE route_id = %s ORDER BY plate_number",
                (rid,),
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
            rid = await self._resolve_route_uid(route_id)
            if rid:
                where.append("route_id = %s")
                params.append(rid)
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
        date = self._normalize_date(date)
        if not date:
            date = self._today()
        where = ["date = %s"]
        params: list[Any] = [date]
        if route_id:
            rid = await self._resolve_route_uid(route_id)
            if rid:
                where.append("route_id = %s")
                params.append(rid)
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
        date = self._normalize_date(date)
        if not date:
            date = self._today()
        if route_id:
            rid = await self._resolve_route_uid(route_id)
            if rid:
                rows = storage.query(
                    f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
                    "WHERE route_id = %s AND date = %s ORDER BY vehicle_number",
                    (rid, date),
                )
            else:
                rows = storage.query(
                    f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
                    f"WHERE route_id = %s AND date = %s ORDER BY vehicle_number",
                    (route_id, date),
                )
        else:
            rows = storage.query(
                f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
                "WHERE date = %s ORDER BY route_id, vehicle_number",
                (date,),
            )
        _, route_name = await self._resolve_route(route_id)
        has = bool(rows)
        no_data, latest = (False, "")
        if route_id:
            rid2 = await self._resolve_route_uid(route_id)
            no_data, latest = await self._route_no_data(rid2, date)
        return {"route_id": route_id, "route_name": route_name,
                "date": date, "data": rows, "found": has,
                "no_data": no_data, "latest_date": latest}

    async def _get_drivers(
        self, route_id: str = "", query: str = "", limit: int = 200, **kwargs
    ) -> dict:
        storage = self._storage()
        where = []
        params: list[Any] = []
        if route_id:
            rid = await self._resolve_route_uid(route_id)
            if rid:
                where.append("d.route_id = %s")
                params.append(rid)
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

    async def _get_avans(self, driver: str = "", driver_id: str = "",
                         month: str = "", **kwargs) -> dict:
        """Haydovchi/oy uchun avans to'lovlari (avans jadvali)."""
        storage = self._storage()
        if not driver_id:
            driver_id, _ = await self._resolve_driver(driver)
        if not driver_id:
            return {"driver": driver, "avans": [], "count": 0, "total": 0}
        rows = storage.query(
            "SELECT a.pay_date, a.amount, a.route_name, a.note "
            "FROM avans a WHERE a.driver_id = %s ORDER BY a.pay_date DESC",
            (driver_id,))
        rows = [dict(r) for r in rows]
        if month:
            rows = [r for r in rows
                    if str(r.get("pay_date", "")).startswith(
                        str(month).replace(".", "-")[:7])]
        total = sum(int(r.get("amount", 0) or 0) for r in rows)
        return {"driver": driver, "month": month or "",
                "avans": rows, "count": len(rows), "total": total}

    async def _get_driver_fines(self, driver: str = "", driver_id: str = "",
                                month: str = "", date: str = "",
                                **kwargs) -> dict:
        """Haydovchi jarimalari (driver_fines jadvali)."""
        storage = self._storage()
        if not driver_id:
            driver_id, _ = await self._resolve_driver(driver)
        if not driver_id:
            return {"driver": driver, "fines": [], "count": 0, "total": 0}
        sql = ("SELECT f.date, f.amount, f.reason, f.status FROM driver_fines f "
               "WHERE f.driver_id = %s")
        params: list = [driver_id]
        if month:
            sql += " AND CAST(f.date AS TEXT) LIKE %s"
            params.append(str(month).replace(".", "-")[:7] + "%")
        elif date:
            sql += " AND CAST(f.date AS TEXT) = %s"
            params.append(self._normalize_date(date) or date)
        sql += " ORDER BY f.date DESC"
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        total = sum(int(r.get("amount", 0) or 0) for r in rows)
        return {"driver": driver, "month": month or "", "date": date or "",
                "fines": rows, "count": len(rows), "total": total}

    async def _get_waybills(self, route_id: str = "", date: str = "",
                            limit: int = 50, **kwargs) -> dict:
        """Yo'l varaqalari (waybills jadvali)."""
        storage = self._storage()
        if route_id:
            uid, _ = await self._resolve_route(route_id)
        else:
            uid = ""
        sql = ("SELECT w.date, COALESCE(r.name, w.route_id::text) AS route_name, "
               "w.plate_number, COALESCE(d.full_name, w.driver_id::text) AS driver_name, "
               "w.direction, w.status "
               "FROM waybills w "
               "LEFT JOIN routes r ON r.external_id = w.route_id "
               "LEFT JOIN drivers d ON d.external_id = w.driver_id "
               "WHERE 1=1")
        params: list = []
        if uid:
            sql += " AND w.route_id = %s"
            params.append(uid)
        if date:
            sql += " AND CAST(w.date AS TEXT) = %s"
            params.append(self._normalize_date(date) or date)
        sql += " ORDER BY w.date DESC LIMIT %s"
        params.append(int(limit))
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "date": date or "", "waybills": rows}

    async def _get_duties(self, route_id: str = "", date: str = "",
                          **kwargs) -> dict:
        """Navbatchilik ro'yxati (duties jadvali)."""
        storage = self._storage()
        if route_id:
            uid, _ = await self._resolve_route(route_id)
        else:
            uid = ""
        sql = ("SELECT d.date, COALESCE(r.name, d.route_id::text) AS route_name, "
               "d.shift_id, d.data "
               "FROM duties d "
               "LEFT JOIN routes r ON r.external_id = d.route_id "
               "WHERE 1=1")
        params: list = []
        if uid:
            sql += " AND d.route_id = %s"
            params.append(uid)
        if date:
            sql += " AND CAST(d.date AS TEXT) = %s"
            params.append(self._normalize_date(date) or date)
        sql += " ORDER BY d.date DESC LIMIT 100"
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "date": date or "", "duties": rows}

    async def _get_dispatcher_routes(self, dispatcher_chat_id: str = "",
                                     **kwargs) -> dict:
        """Dispecher biriktirilgan yo'nalishlar (dispatcher_routes jadvali)."""
        storage = self._storage()
        sql = ("SELECT dispatcher_chat_id, route_name, company, phone "
               "FROM dispatcher_routes")
        params: list = []
        if dispatcher_chat_id:
            sql += " WHERE dispatcher_chat_id = %s"
            params.append(dispatcher_chat_id)
        sql += " ORDER BY route_name"
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "routes": rows}

    async def _get_documents(self, query: str = "", driver: str = "",
                             limit: int = 20, **kwargs) -> dict:
        """Sotilgan hujjatlar ro'yxati (documents jadvali)."""
        storage = self._storage()
        sql = ("SELECT d.title, d.category, d.status, d.created_at, "
               "COALESCE(dr.full_name, d.driver_id::text) AS driver_name "
               "FROM documents d "
               "LEFT JOIN drivers dr ON dr.external_id = d.driver_id "
               "WHERE 1=1")
        params: list = []
        if driver:
            did, _ = await self._resolve_driver(driver)
            if did:
                sql += " AND d.driver_id = %s"
                params.append(did)
        if query:
            sql += " AND (LOWER(d.title) LIKE LOWER(%s) "
            sql += " OR LOWER(d.category) LIKE LOWER(%s))"
            params.append(f"%{query}%")
            params.append(f"%{query}%")
        sql += " ORDER BY d.created_at DESC LIMIT %s"
        params.append(int(limit))
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "query": query or "", "documents": rows}

    async def _get_staff(self, query: str = "", limit: int = 30,
                         **kwargs) -> dict:
        """Xodimlar (staff jadvali)."""
        storage = self._storage()
        sql = ("SELECT name, position, company, salary_type, rate, days "
               "FROM staff WHERE 1=1")
        params: list = []
        if query:
            sql += " AND (LOWER(name) LIKE LOWER(%s) OR LOWER(position) LIKE LOWER(%s))"
            params.append(f"%{query}%")
            params.append(f"%{query}%")
        sql += " ORDER BY name LIMIT %s"
        params.append(int(limit))
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "query": query or "", "staff": rows}

    async def _get_sms(self, driver: str = "", route_id: str = "",
                       limit: int = 50, **kwargs) -> dict:
        """Haydovchilarga yuborilgan SMS log (sms_log jadvali)."""
        storage = self._storage()
        sql = ("SELECT s.name, s.phone, COALESCE(r.name, s.route_id::text) "
               "AS route_name, s.schedule_date, s.message, s.status, s.send_at "
               "FROM sms_log s "
               "LEFT JOIN routes r ON r.external_id = s.route_id "
               "WHERE 1=1")
        params: list = []
        if driver:
            sql += " AND LOWER(s.name) LIKE LOWER(%s)"
            params.append(f"%{driver}%")
        if route_id:
            uid, _ = await self._resolve_route(route_id)
            if uid:
                sql += " AND s.route_id = %s"
                params.append(uid)
        sql += " ORDER BY s.created_at DESC LIMIT %s"
        params.append(int(limit))
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "sms": rows}

    async def _get_notifications(self, unread_only: bool = False,
                                 limit: int = 20, **kwargs) -> dict:
        """Bildirishnomalar (notifications jadvali)."""
        storage = self._storage()
        params: list = []
        sql = ("SELECT channel, target, message, status, sent_at "
               "FROM notifications WHERE 1=1")
        if unread_only:
            sql += " AND status = ANY(%s)"
            params.append(["pending", "new", "failed"])
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(int(limit))
        rows = [dict(r) for r in storage.query(sql, tuple(params))]
        return {"count": len(rows), "notifications": rows}

    async def _get_reports(self, limit: int = 10, **kwargs) -> dict:
        """Saqlangan hisobotlar (reports jadvali)."""
        storage = self._storage()
        rows = [dict(r) for r in storage.query(
            "SELECT name, report_type, period_date, file_path, created_at "
            "FROM reports ORDER BY created_at DESC LIMIT %s", (int(limit),))]
        return {"count": len(rows), "reports": rows}

    async def _get_automation_runs(self, limit: int = 10, **kwargs) -> dict:
        """Avtomatlashtirish ishga tushirishlar (automation_runs jadvali)."""
        storage = self._storage()
        rows = [dict(r) for r in storage.query(
            "SELECT trigger, started_at, finished_at, status, summary, "
            "sheet_date, month FROM automation_runs "
            "ORDER BY created_at DESC LIMIT %s", (int(limit),))]
        return {"count": len(rows), "runs": rows}

    async def _get_trip_statuses(self, **kwargs) -> dict:
        """Reys holatlarining izohlari (trip_statuses jadvali)."""
        storage = self._storage()
        rows = [dict(r) for r in storage.query(
            "SELECT code, name, description FROM trip_statuses ORDER BY code")]
        return {"count": len(rows), "statuses": rows}

    async def _rows(self, table: str = "", key: str = "", value: str = "",
                    limit: int = 50, **kwargs) -> dict:
        """Istalgan jadvaldan so'rov (xavfsiz whitelist).

        Faqat ma'lum jadvallarga ruxsat; key/value bo'yicha LIKE qidiruv.
        """
        allowed = {
            "routes", "vehicles", "drivers", "schedules", "avans",
            "driver_fines", "waybills", "duties", "documents", "staff",
            "sms_log", "notifications", "reports", "dispatcher_routes",
            "trip_statuses", "automation_runs", "errors",
        }
        table = (table or "").strip().lower()
        if table not in allowed:
            return {"count": 0, "error": f"Ruxsat berilmagan jadval: {table}",
                    "rows": []}
        storage = self._storage()
        params: list = []
        limit = max(1, min(int(limit), 100))
        try:
            cols = [c["column_name"] for c in storage.query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,))]
        except Exception:  # noqa: BLE001
            cols = []
        if not cols:
            return {"count": 0, "error": "Jadval ustunlari topilmadi", "rows": []}
        sql = f'SELECT * FROM "{table}" WHERE 1=1'
        if key and key in cols and value:
            sql += f' AND CAST("{key}" AS TEXT) LIKE %s'
            params.append(f"%{value}%")
        sql += f" ORDER BY {cols[0]} LIMIT %s"
        params.append(limit)
        try:
            rows = [dict(r) for r in storage.query(sql, tuple(params))]
        except Exception as exc:  # noqa: BLE001
            return {"count": 0, "error": str(exc), "rows": []}
        return {"count": len(rows), "table": table, "rows": rows}

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
        rid = await self._resolve_route_uid(route_id)
        rows = storage.query(
            "SELECT * FROM trips WHERE route_id = %s AND date = %s",
            (rid or route_id, date),
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
        date = self._normalize_date(date)
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
        date = self._normalize_date(date)
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

    async def _build_period_filter(self, month: str = "", date: str = "",
                                   route_id: str = "") -> dict:
        """_period_filter + route nomini UUID'ga resolve qilish.

        Metrics filter'i route UUID kutaradi; "B-80" kabi nom berilsa
        avval UUID'ga o'tkaziladi (aks holda filter nol natija beradi).
        """
        rid = str(route_id or "").strip()
        if rid:
            uid = await self._resolve_route_uid(rid)
            if uid:
                rid = uid
        return self._period_filter(month=month, date=date, route_id=rid)

    async def _get_daily_summary(self, date: str = "", route_id: str = "",
                                 month: str = "", **kwargs) -> dict:
        """Rich kunlik/davr xulosasi — Metrics.monthly() asosida.

        rejalashtirilgan, amalda, qabul qilingan, muammolar, performance,
        accept_rate kabi ko'rsatkichlar bilan (single/day bo'yicha).
        """
        m = self._metrics()
        date = self._normalize_date(date)
        f = await self._build_period_filter(month=month, date=date, route_id=route_id)
        try:
            mon = m.monthly(dict(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_daily_summary xatosi: %s", exc)
            return {"found": False, "error": str(exc)}
        days = mon.get("days") or []
        t = dict(mon.get("totals", {}))
        # Readable route name (if route_id given as name, resolve)
        route_uuid, route_name = await self._resolve_route(route_id)
        # Route uchun kun davomida ishga chiqqan avtobuslar soni (route_daily)
        if route_uuid:
            try:
                st = self._storage()
                rows = st.query(
                    "SELECT count(*) AS c FROM route_daily "
                    "WHERE route_id = %s AND date = %s AND trip_fact > 0",
                    (route_uuid, str(f.get("from", ""))))
                t["vehicles_out"] = int(rows[0]["c"]) if rows else 0
            except Exception as exc:  # noqa: BLE001
                log.warning("vehicles_out xatosi: %s", exc)
        return {
            "found": bool(days),
            "date": f.get("from", ""),
            "month": f.get("month", ""),
            "route_id": route_uuid or route_id or "",
            "route_name": route_name,
            "days": len(days),
            "period": {"from": f.get("from", ""), "to": f.get("to", "")},
            "daily": days[:40],
            "totals": t,
        }

    async def _get_problems(self, date: str = "", route_id: str = "",
                            month: str = "", **kwargs) -> dict:
        """Muammolar tahlili — GPS, texnik, jadval (Metrics.monthly dan)."""
        m = self._metrics()
        date = self._normalize_date(date)
        f = await self._build_period_filter(month=month, date=date, route_id=route_id)
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
        date = self._normalize_date(date)
        f = await self._build_period_filter(month=month, date=date, route_id=route_id)
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

    async def _get_not_accepted_km(self, month: str = "", date: str = "",
                                   route_id: str = "", driver: str = "",
                                   **kwargs) -> dict:
        """Qabul qilinmagan reyslar — haydovchi bo'yicha hisobot.

        Sayt bilan bir xil hisob (route_daily): har bir haydovchi uchun
        rejadagi reyslar, amalda bajarilgan reyslar, qabul qilinmagan
        (reja − amalda), ish kuni, rejadagi km, amalda km va farq.
        """
        m = self._metrics()
        date = self._normalize_date(date)
        f = await self._build_period_filter(month=month, date=date, route_id=route_id)
        drv = str(driver or "").strip()
        if drv:
            try:
                drv_id, _ = await self._resolve_driver(drv)
                f["driver"] = drv_id or drv
            except Exception:  # noqa: BLE001
                f["driver"] = drv
        try:
            rep = m.not_accepted_km_report(dict(f))
        except Exception as exc:  # noqa: BLE001
            log.warning("get_not_accepted_km xatosi: %s", exc)
            return {"found": False, "error": str(exc)}
        route_uuid, route_name = await self._resolve_route(route_id)
        return {
            "found": bool(rep.get("rows")),
            "period": rep.get("period", {}),
            "month": f.get("month", ""),
            "route_id": route_uuid or route_id or "",
            "route_name": route_name or rep.get("route_name", ""),
            "company": rep.get("company", ""),
            "totals": rep.get("totals", {}),
            "drivers": rep.get("rows", []),
            "drivers_count": len(rep.get("rows", [])),
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
        date = self._normalize_date(date)
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

    async def _get_route_vehicles(self, route: str = "", route_id: str = "",
                                  date: str = "", **kwargs) -> dict:
        """Yo'nalish (nom yoki ID) bo'yicha avtobuslar ro'yxati.

        Har bir avtobus uchun kunlik real holat (isha chiqqanmi, reyslar,
        km) ham qo'shiladi — agent "qaysi avtobuslar yo'lda" degan savolga
        javob bera oladi.
        """
        storage = self._storage()
        rid = (route_id or route or "").strip()
        if not rid:
            return {"found": False, "route": "", "vehicles": [], "count": 0}
        uid, name = await self._resolve_route(rid)
        if not uid:
            return {"found": False, "route": route or route_id, "vehicles": [], "count": 0}
        date = self._normalize_date(date)
        if not date:
            date = self._today()
        veh = await self._get_vehicles(route_id=uid)
        rows = veh.get("vehicles", [])
        daily_map: dict[str, list] = {}
        daily = storage.query(
            f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
            "WHERE route_id = %s AND date = %s", (uid, date))
        for r in daily:
            for key in (r.get("vehicle_number"), r.get("vehicle_id")):
                if key:
                    daily_map.setdefault(str(key), []).append(r)
        vehicles = []
        for r in rows:
            day = (
                daily_map.get(str(r.get("plate_number", "") or ""))
                or daily_map.get(str(r.get("garage_number", "") or ""))
                or daily_map.get(str(r.get("external_id", "")))
            )
            rec = day[0] if day else None
            vehicles.append({
                "id": r.get("external_id", ""),
                "plate_number": r.get("plate_number", ""),
                "garage_number": r.get("garage_number", ""),
                "model": r.get("model", ""),
                "working_day": int(rec.get("working_day", 0) or 0) if rec else 0,
                "trip_plan": int(rec.get("trip_plan", 0) or 0) if rec else 0,
                "trip_fact": int(rec.get("trip_fact", 0) or 0) if rec else 0,
                "distance_plan": round(float(rec.get("distance_plan", 0) or 0), 2) if rec else 0,
                "distance_fact": round(float(rec.get("distance_fact", 0) or 0), 2) if rec else 0,
                "shift_name": (rec.get("shift_name", "") if rec else ""),
                "on_route": bool(rec and int(rec.get("trip_fact", 0) or 0) > 0),
            })
        on_route = sum(1 for v in vehicles if v["on_route"])
        no_data, latest = (False, "")
        if not daily:
            no_data, latest = await self._route_no_data(uid, date)
        return {
            "found": True,
            "no_data": no_data,
            "latest_date": latest,
            "route_id": uid,
            "route_name": name,
            "date": date,
            "vehicles_count": len(vehicles),
            "on_route": on_route,
            "off_route": len(vehicles) - on_route,
            "vehicles": vehicles,
        }

    async def _get_trip_anomalies(self, route: str = "", route_id: str = "",
                                  date: str = "", min_diff: int = 1,
                                  **kwargs) -> dict:
        """Rejaga nisbatan reyslar yetmayotgan (anomaliya) avtobuslar.

        working_day=1 bo'lgan avtobuslarda trip_plan > trip_fact bo'lsa
        aniqlanadi. min_diff — minimal farq (kelish shovqinni kamaytirish).
        """
        storage = self._storage()
        date = self._normalize_date(date)
        uid = route_id or ""
        name = ""
        if uid or route:
            uid, name = await self._resolve_route(uid or route)
        if not uid:
            return {"found": False, "route": route or route_id,
                    "date": date, "anomalies": [], "count": 0}
        rows = storage.query(
            f"SELECT {_ROUTE_DAILY_COLS} FROM route_daily "
            "WHERE route_id = %s AND date = %s AND working_day = 1 "
            "ORDER BY vehicle_number", (uid, date))
        anomalies = []
        plan_total = fact_total = 0
        for r in rows:
            plan = int(r.get("trip_plan", 0) or 0)
            fact = int(r.get("trip_fact", 0) or 0)
            diff = plan - fact
            plan_total += plan
            fact_total += fact
            if diff >= max(int(min_diff or 1), 1):
                anomalies.append({
                    "vehicle_number": r.get("vehicle_number", ""),
                    "vehicle_brand": r.get("vehicle_brand", ""),
                    "shift_name": r.get("shift_name", ""),
                    "trip_plan": plan,
                    "trip_fact": fact,
                    "diff": diff,
                    "distance_fact": round(float(r.get("distance_fact", 0) or 0), 2),
                })
        no_data, latest = (False, "")
        found = bool(rows)
        if not rows:
            no_data, latest = await self._route_no_data(uid, date)
            found = not no_data
        return {
            "found": found,
            "no_data": no_data,
            "latest_date": latest,
            "route_id": uid,
            "route_name": name,
            "date": date,
            "anomalies_count": len(anomalies),
            "trip_plan_total": plan_total,
            "trip_fact_total": fact_total,
            "missing_trips_total": plan_total - fact_total,
            "anomalies": anomalies,
        }

    async def _get_route_health(self, route: str = "", route_id: str = "",
                                days: int = 7, **kwargs) -> dict:
        """Yo'nalish salomatligi — oxirgi N kundagi ishchi avtobuslar,
        reja/fakt reyslari va masofa ko'rsatkichlari tendensiyasi."""
        storage = self._storage()
        if not route_id and not route:
            return {"found": False, "route": "", "days": days}
        uid, name = await self._resolve_route(route_id or route)
        if not uid:
            return {"found": False, "route": route or route_id, "days": days}
        days = max(min(int(days or 7), 60), 1)
        rows = storage.query(
            "SELECT date, COUNT(*) FILTER (WHERE working_day = 1) AS buses, "
            "SUM(trip_plan) AS plan, SUM(trip_fact) AS fact, "
            "SUM(distance_plan) AS dplan, SUM(distance_fact) AS dfact "
            "FROM route_daily "
            "WHERE route_id = %s AND date >= TO_CHAR(CURRENT_DATE - %s, 'YYYY-MM-DD') "
            "GROUP BY date ORDER BY date",
            (uid, days - 1))
        history = []
        for r in rows:
            plan = int(r.get("plan", 0) or 0)
            fact = int(r.get("fact", 0) or 0)
            history.append({
                "date": str(r.get("date", "")),
                "buses_worked": int(r.get("buses", 0) or 0),
                "trip_plan": plan,
                "trip_fact": fact,
                "missing_trips": plan - fact,
                "distance_plan": round(float(r.get("dplan", 0) or 0), 2),
                "distance_fact": round(float(r.get("dfact", 0) or 0), 2),
            })
        worked = [h for h in history if h["buses_worked"] > 0]
        avg_pct = 0.0
        if worked:
            avgs = [
                (h["trip_fact"] / h["trip_plan"] * 100)
                for h in worked if h["trip_plan"] > 0
            ]
            avg_pct = round(sum(avgs) / len(avgs), 1) if avgs else 0.0
        return {
            "found": bool(history),
            "route_id": uid,
            "route_name": name,
            "days": days,
            "history": history,
            "avg_execution_pct": avg_pct,
            "last_day": history[-1] if history else None,
        }
