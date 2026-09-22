"""Browser Agent — bm.dtransport.uz bilan ishlash."""

from __future__ import annotations

import json
from typing import Any

from .agents import BaseAgent
from .config import get_myai_config
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.browser_agent")

# DBTool 'ning to'liq qamrovi — browser orqali passthrough qilinadigan action'lar.
# Mana bu ro'yxat _fetch_from_db'da maxsus branch'larga ega bo'lmagan lekin
# PostgreSQL jadvallari bilan ishlaydigan qo'shimcha so'rovlarni qamrab oladi.
_DB_PASSTHROUGH_ACTIONS = frozenset({
    "get_avans", "get_driver_fines", "get_waybills", "get_duties",
    "get_dispatcher_routes", "get_documents", "get_staff", "get_sms",
    "get_notifications", "get_reports", "get_automation_runs",
    "get_trip_statuses", "rows", "get_errors", "get_routes",
    "get_drivers", "get_work_logs", "get_route_daily",
    "get_route_vehicles", "get_route_health", "get_trip_anomalies",
    "get_vehicle_detail", "get_driver_profile", "get_route_summary",
    "get_route_trips_detail", "get_route_overview", "get_schedules",
    "get_attendance", "get_driver_schedule", "get_driver_work",
})


class BrowserAgent(BaseAgent):
    """Browser Agent — bm.dtransport.uz dan ma'lumot olish.

    Fallback zanjiri: DB (primary) → API → Browser
    """

    name = AgentType.BROWSER
    description = "Ma'lumot olish (DB primary, API/browser fallback)"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            action = context.params.get("action", "get_route_data")
            route_id = context.params.get("route_id", context.route)
            date = context.params.get("date", context.date)
            month = context.params.get("month", "")
            today = context.date or __import__("datetime").date.today().isoformat()

            db_primary = get_myai_config().db_primary

            # 0. Qidiruv (search tool) — global qidiruv so'rovi
            if action in ("search", "find"):
                query = (context.params.get("query")
                         or context.params.get("q") or "").strip()
                data = await self._use_tool(
                    "search", action="find", query=query,
                )
                self.source_note = "PostgreSQL (ma'lumotlar bazasi) — qidiruv"
                self._finish(True, f"DB: {action} tugadi")
                return AgentResult(success=True, data=data)

            # 1. DB — primary manba
            if db_primary:
                data = await self._fetch_from_db(
                    action, route_id, date or today, month=month,
                    query=context.params.get("query", ""),
                    driver=context.params.get("driver", ""),
                    dispatcher_chat_id=context.params.get(
                        "dispatcher_chat_id", ""))
                if self._db_data_present(data):
                    data = await self._self_check_db(data, "browser_db")
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                    self._finish(True, f"DB: {action} tugadi")
                    return AgentResult(success=True, data=data)
                log.warning("DB bo'sh — API/browser ga o'tiladi")

            # 2. API orqali
            try:
                data = await self._use_tool(
                    "dtransport",
                    action=action,
                    route_id=route_id,
                    date=date,
                )
                if data and (data.get("data") or data.get("trips") or data.get("waybills")):
                    self.source_note = "BM API (bm.dtransport.uz)"
                    self._finish(True, f"API: {action} tugadi")
                    return AgentResult(success=True, data=data)
                log.warning("API bo'sh qaytardi — browserga o'tiladi")
            except Exception as api_exc:
                log.warning("API xatosi: %s — browserga o'tiladi", api_exc)

            # 3. Browser fallback
            try:
                data = await self._fetch_via_browser(action, route_id, date or today)
                if data and data.get("method") != "browser":
                    self._finish(True, f"Browser: {action} tugadi")
                    return AgentResult(success=True, data=data)
                log.warning("Browser ham ishlamadi — DB ga o'tiladi")
            except Exception as br_exc:
                log.warning("Browser xatosi: %s — DB ga o'tiladi", br_exc)

# 4. Oxirgi imkoniyat — DB (primary bo'lmagan holatda ham)
            data = await self._fetch_from_db(action, route_id, date or today,
                                              month=month,
                                              query=context.params.get("query", ""),
                                              driver=context.params.get("driver", ""),
                                              dispatcher_chat_id=context.params.get(
                                                  "dispatcher_chat_id", ""))
            data = await self._self_check_db(data, "browser_db_fallback")
            self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
            self._finish(True, f"DB: {action} tugadi")
            return AgentResult(success=True, data=data)

        except Exception as exc:
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def _self_check_db(self, data: dict, source: str) -> dict:
        """DB natijasidagi statistika invariantlar bilan tekshiriladi.

        BrowserAgent natijalari `{"method", "action", "data"}` konvertida —
        statistika `data.summary` / `data.totals` da. Shu dict'lardagi
        raqamlar (total_trips/accepted/not_accepted, attendance) shu yerda
        verify_invariants bilan tekshiriladi.
        """
        if not isinstance(data, dict):
            return data
        try:
            inner = data.get("data") or {}
            if not isinstance(inner, dict):
                return data
            check = inner.get("summary")
            if not isinstance(check, dict):
                check = inner.get("totals")
            if isinstance(check, dict) and (
                    check.get("total_trips") is not None
                    or check.get("total_drivers") is not None):
                await self.self_check(check, source)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Browser self-check xatosi: %s", exc)
        return data

    @staticmethod
    def _db_data_present(data: dict) -> bool:
        inner = data.get("data", {}) if isinstance(data, dict) else {}
        if not isinstance(inner, dict):
            return bool(inner)
        inner_summary = inner.get("summary", {}) or {}
        for key in ("routes", "vehicles", "data", "drivers", "schedules",
                    "daily", "problems", "totals", "vehicle", "trips"):
            if inner.get(key):
                return True
        if inner.get("found") or inner.get("count"):
            return True
        if inner_summary.get("total_buses") or inner_summary.get("total_trips") \
                or inner_summary.get("accepted") or inner_summary.get("count"):
            return True
        if inner.get("count"):
            return True
        return False

    async def _fetch_via_browser(self, action: str, route_id: str, date: str) -> dict:
        """Playwright orqali ma'lumot olish."""
        await self._use_tool(
            "browser",
            action="open_page",
            url="https://bm.dtransport.uz",
        )
        return {"method": "browser", "action": action, "note": "Browser fallback"}

    async def _fetch_from_db(self, action: str, route_id: str, date: str,
                             month: str = "", query: str = "",
                             driver: str = "",
                             dispatcher_chat_id: str = "") -> dict:
        """DB dan ma'lumot olish — primary manba (API/browser fallback oldi)."""
        log.info("DB fetch: action=%s route=%s date=%s", action, route_id, date)
        try:
            date = date or __import__("datetime").date.today().isoformat()

            # Rich analytics-dan foydalanadigan action'lar (Metrics asosida)
            if action in ("get_daily_summary", "daily_summary"):
                data = await self._use_tool(
                    "db", action="get_daily_summary",
                    date=date, route_id=route_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_problems", "problems"):
                data = await self._use_tool(
                    "db", action="get_problems",
                    date=date, route_id=route_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_electricity", "electricity"):
                month = month or (date[:7] if len(date) >= 7 else date)
                data = await self._use_tool(
                    "db", action="get_electricity", month=month,
                    route_id=route_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_not_accepted_km", "not_accepted_km",
                          "qabul_qilinmagan_km"):
                month = month or (date[:7] if len(date) >= 7 else date)
                data = await self._use_tool(
                    "db", action="get_not_accepted_km", month=month,
                    route_id=route_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_route_overview", "route_overview"):
                data = await self._use_tool(
                    "db", action="get_route_overview",
                    route_id=route_id, date=date,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_vehicle_detail", "vehicle_detail", "check_vehicle"):
                data = await self._use_tool(
                    "db", action="get_vehicle_detail",
                    vehicle=route_id, date=date,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_all_routes", "get_all_routes_summary"):
                data = await self._use_tool(
                    "db", action="get_all_routes_summary", date=date,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_vehicles", "vehicles", "vehicles_list"):
                data = await self._use_tool(
                    "db", action="get_vehicles", route_id=route_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_documents", "documents"):
                data = await self._use_tool(
                    "db", action="get_documents",
                    query=query,
                    driver=driver,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_staff", "staff"):
                data = await self._use_tool(
                    "db", action="get_staff",
                    query=query,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_dispatcher_routes", "dispatcher_routes"):
                data = await self._use_tool(
                    "db", action="get_dispatcher_routes",
                    dispatcher_chat_id=dispatcher_chat_id,
                )
                return {"method": "db", "action": action, "data": data}

            if action in ("get_route_data", "get_trips"):
                # Try route_id, then route name
                data = await self._use_tool(
                    "db", action="get_route_summary",
                    route_id=route_id, date=date,
                )
                return {"method": "db", "action": action, "data": data}

            # Umumiy passthrough: DBTool 'ning qolgan action'lari to'g'ridan-
            # to'g'ri chaqiriladi (agentlar barcha DB jadvallari bilan ishlaydi).
            if action in _DB_PASSTHROUGH_ACTIONS:
                kw: dict = {}
                if route_id:
                    kw["route_id"] = route_id
                if date:
                    kw["date"] = date
                if month:
                    kw["month"] = month
                data = await self._use_tool("db", action=action, **kw)
                return {"method": "db", "action": action, "data": data}

            # Default: get all routes summary
            data = await self._use_tool(
                "db", action="get_all_routes_summary", date=date,
            )
            return {"method": "db", "action": action, "data": data}
        except Exception as db_exc:
            log.error("DB fetch xatosi: %s", db_exc)
            return {"method": "db", "action": action, "data": {"routes": [], "summary": {}}}
