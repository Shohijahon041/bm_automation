"""Schedule Agent — jadval ma'lumotlari."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.schedule_agent")


class ScheduleAgent(BaseAgent):
    name = AgentType.SCHEDULE
    description = "Jadval ma'lumotlarini tahlil qiladi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            route_id = context.params.get("route_id", context.route)
            date = context.params.get("date", context.date)
            driver_id = context.params.get("driver_id", "")

            # 1. DB — schedules jadvalidan
            data = await self._use_tool(
                "db", action="get_schedules",
                route_id=route_id, date=date, driver_id=driver_id,
            )
            if data.get("count", 0) > 0:
                # Self-check: count va qaytgan ro'yxat mosligi
                rows = data.get("data") or data.get("schedules") or []
                if isinstance(rows, list) and len(rows) != data.get("count"):
                    data = dict(data)
                    data["_self_check"] = {
                        "ok": False,
                        "source": "schedule_db",
                        "errors": [
                            f"Jadval soni mos emas: count={data.get('count')}, "
                            f"ro'yxat={len(rows)}"
                        ],
                        "corrections": [f"count = {len(rows)}"],
                    }
                self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                self._finish(True, "DB: get_schedules tugadi")
                return AgentResult(success=True, data=data)
            log.warning("DB da jadval topilmadi — dtransport API ga o'tiladi")

            # 2. API fallback — bm.dtransport.uz
            data = await self._use_tool(
                "dtransport", action="get_duties", route_id=route_id, date=date or "",
            )
            if data.get("duties") or data.get("data"):
                self.source_note = "BM API (bm.dtransport.uz)"
            self._finish(True)
            return AgentResult(success=True, data=data)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
