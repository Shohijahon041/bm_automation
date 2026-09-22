"""Attendance Agent — ishga chiqish."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.attendance_agent")


class AttendanceAgent(BaseAgent):
    name = AgentType.ATTENDANCE
    description = "Ishga chiqish ma'lumotlarini tahlil qiladi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            route_id = context.params.get("route_id", context.route)
            date = context.params.get("date", context.date)

            # 1. DB — route_daily (working_day vs trip_fact) dan to'g'ridan-to'g'ri
            try:
                data = await self._use_tool(
                    "db", action="get_attendance", route_id=route_id, date=date or "",
                )
                if data.get("total_drivers", 0) > 0:
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                    result = {
                        "total_drivers": data["total_drivers"],
                        "present": data["present"],
                        "absent": data["absent"],
                        "attendance_rate": data["attendance_rate"],
                        "scheduled": data.get("scheduled", []),
                        "absent_list": data.get("absent_list", []),
                    }
                    # Self-check: davomat invariantlari (present+absent, foiz)
                    await self.self_check(result, "attendance_db")
                    self._finish(True, "DB: get_attendance tugadi")
                    return AgentResult(success=True, data=result)
                log.warning("DB da attendance topilmadi — browser natijasiga o'tiladi")
            except Exception as db_exc:  # noqa: BLE001
                log.warning("DB attendance xatosi: %s", db_exc)

            # 2. Browser (avvalgi agent) natijasidan
            browser_data = context.previous_results.get("browser", {})
            summary = browser_data.get("data", {}).get("summary", {})

            total = summary.get("total_buses", 0)
            active = summary.get("active_buses", 0)
            rate = round((active / total * 100), 1) if total > 0 else 0

            result = {
                "total_drivers": total,
                "present": active,
                "absent": total - active,
                "attendance_rate": rate,
                "scheduled": [],
                "absent_list": [],
            }
            # Self-check: davomat invariantlari (present+absent, foiz)
            await self.self_check(result, "attendance_browser")
            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))
