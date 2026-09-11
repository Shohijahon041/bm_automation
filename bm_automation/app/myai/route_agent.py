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
            self._finish(True)
            return AgentResult(success=True, data=data)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
