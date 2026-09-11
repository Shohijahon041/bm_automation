"""Report Agent — hisobot tayyorlaydi."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.report_agent")


class ReportAgent(BaseAgent):
    name = AgentType.REPORT
    description = "Hisobot tayyorlaydi (kunlik, oylik)"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            analytics_data = context.previous_results.get("analytics", {})
            summary = analytics_data if (
                "total_trips" in analytics_data or "total_buses" in analytics_data
            ) else {}

            # Analytics natijasi bo'lmasa — DB dan to'g'ridan-to'g'ri
            if not summary:
                try:
                    route_id = context.params.get("route_id", context.route)
                    date = context.params.get("date", context.date)
                    if route_id:
                        raw = await self._use_tool(
                            "db", action="get_route_summary",
                            route_id=route_id, date=date,
                        )
                        s = raw.get("summary", {})
                    else:
                        raw = await self._use_tool(
                            "db", action="get_all_routes_summary", date=date,
                        )
                        s = raw.get("summary", {})
                    total = s.get("total_trips", 0)
                    accepted = s.get("accepted", 0)
                    if total:
                        summary = {
                            "total_trips": total,
                            "accepted": accepted,
                            "not_accepted": s.get("not_accepted", 0),
                            "completion_rate": s.get("completion_rate", 0),
                            "total_km": s.get("total_km", 0),
                            "total_vehicles": s.get("total_vehicles", 0),
                            "units": "trips",
                        }
                        self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                except Exception as db_exc:  # noqa: BLE001
                    log.warning("DB hisobot xatosi: %s", db_exc)

            # Oddiy matnli hisobot
            report = (
                f"Kunlik hisobot\n"
                f"Jami qatnov: {summary.get('total_trips', 0)}\n"
                f"Qabul qilingan: {summary.get('accepted', 0)}\n"
                f"Qabul qilinmagan: {summary.get('not_accepted', 0)}\n"
                f"Bajarilish: {summary.get('completion_rate', 0)}%\n"
                f"Umumiy km: {summary.get('total_km', 0)}"
            )

            self._finish(True)
            return AgentResult(success=True, data={"report": report})
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
