"""Browser Agent — bm.dtransport.uz bilan ishlash."""

from __future__ import annotations

import json
from typing import Any

from .agents import BaseAgent
from .config import get_myai_config
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.browser_agent")


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
            today = context.date or __import__("datetime").date.today().isoformat()

            db_primary = get_myai_config().db_primary

            # 1. DB — primary manba
            if db_primary:
                data = await self._fetch_from_db(action, route_id, date or today)
                if self._db_data_present(data):
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
            data = await self._fetch_from_db(action, route_id, date or today)
            self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
            self._finish(True, f"DB: {action} tugadi")
            return AgentResult(success=True, data=data)

        except Exception as exc:
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

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

    async def _fetch_from_db(self, action: str, route_id: str, date: str) -> dict:
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
                month = date[:7] if len(date) >= 7 else date
                data = await self._use_tool(
                    "db", action="get_electricity", month=month,
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

            if action in ("get_route_data", "get_trips"):
                # Try route_id, then route name
                data = await self._use_tool(
                    "db", action="get_route_summary",
                    route_id=route_id, date=date,
                )
                return {"method": "db", "action": action, "data": data}

            # Default: get all routes summary
            data = await self._use_tool(
                "db", action="get_all_routes_summary", date=date,
            )
            return {"method": "db", "action": action, "data": data}
        except Exception as db_exc:
            log.error("DB fetch xatosi: %s", db_exc)
            return {"method": "db", "action": action, "data": {"routes": [], "summary": {}}}
