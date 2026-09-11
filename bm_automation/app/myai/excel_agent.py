"""Excel Agent — Excel hisobot yaratadi."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.excel_agent")


class ExcelAgent(BaseAgent):
    name = AgentType.EXCEL
    description = "Excel hisobot yaratadi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            analytics_data = context.previous_results.get("analytics", {})
            vehicles = context.previous_results.get("transport", {}).get("vehicles", [])

            headers = ["Avtobus", "Haydovchi", "Yo'nalish", "Reys", "Km", "Holat"]
            rows = []
            for v in vehicles:
                rows.append([
                    v.get("bus_number", ""),
                    v.get("driver", ""),
                    v.get("route", ""),
                    v.get("trips", 0),
                    v.get("km", 0),
                    v.get("status", ""),
                ])

            data = [dict(zip(headers, row)) for row in rows]
            result = await self._use_tool(
                "excel",
                action="create_report",
                title="Hisobot",
                data=data,
            )
            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
