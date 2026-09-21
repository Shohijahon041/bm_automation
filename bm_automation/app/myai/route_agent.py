"""Route Agent — yo'nalish ma'lumotlari."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.route_agent")


class RouteAgent(BaseAgent):
    name = AgentType.ROUTE
    description = "Yo'nalish ma'lumotlarini tahlil qiladi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            route_id = context.params.get("route_id", context.route)
            date = context.params.get("date", context.date)
            data = await self._use_tool(
                "db", action="get_route_daily", route_id=route_id, date=date,
            )
            enriched = dict(data or {})
            # Real ma'lumotni boyitish: reys anomaliyalari va avtobuslar holati
            try:
                anomalies = await self._use_tool(
                    "db", action="get_trip_anomalies", route=route_id, date=date,
                )
                if anomalies and anomalies.get("found"):
                    enriched["trip_anomalies"] = anomalies
            except Exception:  # noqa: BLE001
                pass
            try:
                vehicles = await self._use_tool(
                    "db", action="get_route_vehicles", route=route_id, date=date,
                )
                if vehicles and vehicles.get("found"):
                    enriched["route_vehicles"] = vehicles
            except Exception:  # noqa: BLE001
                pass
            self._finish(True)
            return AgentResult(success=True, data=enriched)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
