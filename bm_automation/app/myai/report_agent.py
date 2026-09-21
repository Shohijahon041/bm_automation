"""Report Agent — hisobot tayyorlaydi (kunlik, oylik, muammolar)."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.report_agent")


class ReportAgent(BaseAgent):
    name = AgentType.REPORT
    description = "Hisobot tayyorlaydi (kunlik, oylik, muammolar)"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            analytics_data = context.previous_results.get("analytics", {})

            # 1) Oylik haydovchi hisoboti (driver agent payload'idan)
            drv = context.previous_results.get("driver") or {}
            if isinstance(drv, dict) and drv.get("period"):
                report = self._monthly_report(drv)
                self.source_note = "PostgreSQL (ma'lumotlar bazasi) — oylik statistika"
                self._finish(True, "Oylik hisobot tayyor")
                return AgentResult(success=True, data={"report": report})

            # 2) Kunlik/davr hisoboti (analytics yoki DB dan)
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

            report = self._daily_report(analytics_data, summary)
            self._finish(True)
            return AgentResult(success=True, data={"report": report})
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    def _monthly_report(self, drv: dict) -> str:
        """Oylik haydovchi payload'idan matnli hisobot."""
        period = drv.get("period", "")
        lines: list[str] = [f"Oylik hisobot ({period})"]
        if "drivers" in drv:
            totals = drv.get("totals") or {}
            lines.append(
                f"Haydovchilar: {totals.get('active_drivers', 0)}/"
                f"{totals.get('drivers', 0)} faol · "
                f"jami qatnovlar: {totals.get('total_trips', 0)} · "
                f"masofa: {totals.get('total_km', 0)} km")
            lines.append(
                f"Soliq: {totals.get('total_tax', 0)} so'm · "
                f"jarimalar: {totals.get('total_fines', 0)} so'm · "
                f"net maosh: {totals.get('total_net', 0)} so'm")
            for r in (drv.get("drivers") or [])[:10]:
                lines.append(
                    f"  • {r.get('name', '-')}: {r.get('km', 0)} km, "
                    f"{r.get('trips', 0)} qatnov, "
                    f"gross {r.get('gross_pay', 0)} / "
                    f"net {r.get('net_pay', 0)} so'm, "
                    f"jarima {r.get('fines', 0)}")
        elif isinstance(drv.get("driver"), dict):
            d = drv["driver"]
            lines.append(
                f"Haydovchi: {d.get('name', drv.get('query', '-'))} · "
                f"{d.get('working_days', 0)} ish kuni")
            lines.append(
                f"Masofa: {d.get('km', 0)} km · "
                f"qatnovlar: {d.get('trips', 0) + d.get('manual_trips', 0)}")
            lines.append(
                f"Maosh: gross {d.get('gross_pay', 0)} / "
                f"net {d.get('net_pay', 0)} so'm · "
                f"soliq {d.get('tax', 0)} · jarima {d.get('fines', 0)}")
            lines.append(f"Baho: {d.get('rating', 0)}")
        else:
            lines.append(f"Ma'lumot yo'q: {drv.get('query', '')}")
        return "\n".join(lines)

    def _daily_report(self, analytics_data: dict, summary: dict) -> str:
        """Kunlik/davr hisoboti — qatnovlar + davomat + muammolar."""
        lines: list[str] = ["Kunlik hisobot"]

        attendance = analytics_data.get("attendance_data") if isinstance(
            analytics_data, dict) else None
        if attendance:
            lines.append(
                f"Davomat: {attendance.get('present', 0)}/"
                f"{attendance.get('scheduled_count', attendance.get('present', 0))} "
                f"({attendance.get('attendance_rate', 0)}%)")
            absent = attendance.get("absent_list") or []
            if absent:
                lines.append("Kelmaganlar: " + ", ".join(
                    str(a.get("vehicle_number", "")) for a in absent[:8]))

        if summary:
            lines.append(
                f"Jami qatnov: {summary.get('total_trips', 0)} · "
                f"qabul qilingan: {summary.get('accepted', 0)} · "
                f"qabul qilinmagan: {summary.get('not_accepted', 0)}")
            lines.append(
                f"Bajarilish: {summary.get('completion_rate', 0)}% · "
                f"umumiy km: {summary.get('total_km', 0)} · "
                f"avtobuslar: {summary.get('total_vehicles', 0)} ta")
        else:
            lines.append("Ma'lumotlar bazasida hisobot uchun ma'lumot topilmadi.")

        # Muammolar (db_problems analytics orqali o'tkazilgan)
        db_problems = analytics_data.get("db_problems") if isinstance(
            analytics_data, dict) else None
        if isinstance(db_problems, dict) and db_problems.get("problems"):
            p = db_problems.get("problems", {})
            lines.append(
                f"Muammolar: GPS {p.get('gps', 0)}, texnik {p.get('technical', 0)}, "
                f"jadval {p.get('schedule', 0)} — jami {p.get('total', 0)}")
        return "\n".join(lines)