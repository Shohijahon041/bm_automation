"""Excel Agent — Excel hisobot yaratadi.

Oylik so'rov bo'lsa — haydovchi reytingi (km, ish kuni, gross/net maosh,
jarimalar); aks holda kunlik ko'rsatkichlar (reja/amalda/qabul, muammolar,
performance) va avtobuslar ro'yxati varaqlari bilan. Fayl yo'li `result.path`
orqali Telegram agent faylni yuborishi mumkin.
"""

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
            month = context.params.get("month", "")
            route_id = context.params.get("route_id", context.route)

            if month:
                result = await self._monthly_report(context, month, route_id)
            else:
                result = await self._daily_report(context)

            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def _monthly_report(self, context: AgentContext,
                              month: str, route_id: str) -> dict:
        """Oylik haydovchi hisoboti — monthly tool asosida."""
        sort = context.params.get("sort", "net_pay")
        drv = await self._use_tool(
            "monthly", action="get_drivers_monthly",
            month=month, route_id=route_id, sort=sort,
        )
        rows = []
        for r in drv.get("drivers") or []:
            rows.append({
                "Haydovchi": r.get("name", ""),
                "Yo'nalish": r.get("route_name", ""),
                "Reys": r.get("trips", 0) + r.get("manual_trips", 0),
                "Ish kuni": r.get("working_days", 0),
                "Davomat%": r.get("attendance", 0),
                "KM": r.get("km", 0),
                "Gross": r.get("gross_pay", 0),
                "Soliq": r.get("tax", 0),
                "Jarima": r.get("fines", 0),
                "Net": r.get("net_pay", 0),
                "Baho": r.get("rating", 0),
            })
        totals = drv.get("totals") or {}
        summary_rows = [
            {"Ko'rsatkich": "Haydovchilar (faol/jami)",
             "Qiymat": f"{totals.get('active_drivers', 0)}/{totals.get('drivers', 0)}"},
            {"Ko'rsatkich": "Jami qatnovlar", "Qiymat": totals.get("total_trips", 0)},
            {"Ko'rsatkich": "Jami km", "Qiymat": totals.get("total_km", 0)},
            {"Ko'rsatkich": "Gross", "Qiymat": totals.get("total_gross", 0)},
            {"Ko'rsatkich": "Soliq", "Qiymat": totals.get("total_tax", 0)},
            {"Ko'rsatkich": "Jarima", "Qiymat": totals.get("total_fines", 0)},
            {"Ko'rsatkich": "Net", "Qiymat": totals.get("total_net", 0)},
        ]
        period = drv.get("period") or month
        title = f"Oylik haydovchi hisoboti {period}"
        return await self._use_tool(
            "excel", action="create_report",
            title=title, sheet_name="Haydovchilar",
            data=rows,
            extra_sheets=[{"name": "Xulosa", "data": summary_rows}],
        )

    async def _daily_report(self, context: AgentContext) -> dict:
        """Kunlik hisobot — db_daily ko'rsatkichlari + avtobuslar."""
        ana = context.previous_results.get("analytics") or {}
        transport = context.previous_results.get("transport") or {}
        extra_sheets: list[dict] = []

        # Asosiy varaq: kunlik ko'rsatkichlar (Metrics db_daily)
        daily_rows = []
        db_daily = ana.get("db_daily") if isinstance(ana, dict) else None
        if isinstance(db_daily, dict):
            t = db_daily.get("totals") or {}
            daily_rows = [{
                "Ko'rsatkich": "Kun / davr",
                "Qiymat": db_daily.get("date") or db_daily.get("month", ""),
            }] + [
                {"Ko'rsatkich": "Reja", "Qiymat": t.get("planned", 0)},
                {"Ko'rsatkich": "Amalda", "Qiymat": t.get("actual", 0)},
                {"Ko'rsatkich": "Qabul qilingan", "Qiymat": t.get("accepted", 0)},
                {"Ko'rsatkich": "Qabul qilinmagan", "Qiymat": t.get("not_accepted", 0)},
                {"Ko'rsatkich": "Muammolar", "Qiymat": t.get("problems_total", 0)},
                {"Ko'rsatkich": "Performance (bajarilish)%",
                 "Qiymat": t.get("performance", 0)},
                {"Ko'rsatkich": "Accept rate%", "Qiymat": t.get("accept_rate", 0)},
            ]
            per_day = db_daily.get("daily") or []
            if per_day:
                multi = []
                for d in per_day[:60]:
                    p = d.get("problems") or {}
                    multi.append({
                        "Sana": d.get("date", ""),
                        "Reja": d.get("planned", 0),
                        "Amalda": d.get("actual", 0),
                        "Qabul": d.get("accepted", 0),
                        "Qabul qilinmagan": d.get("not_accepted", 0),
                        "Muammolar": d.get("problems_total", 0),
                        "Perf%": d.get("performance", 0),
                    })
                extra_sheets.append({
                    "name": "Kunlar", "headers": [
                        "Sana", "Reja", "Amalda", "Qabul",
                        "Qabul qilinmagan", "Muammolar", "Perf%",
                    ], "data": multi,
                })

        # Avtobuslar varaqasi
        vehicles = transport.get("vehicles") or []
        veh_rows = []
        for v in vehicles:
            veh_rows.append({
                "Avtobus": v.get("bus_number", ""),
                "Haydovchi": v.get("driver", ""),
                "Yo'nalish": v.get("route", ""),
                "Reys": v.get("trips", 0),
                "Km": v.get("km", 0),
                "Holat": v.get("status", ""),
            })
        if veh_rows:
            extra_sheets.insert(0, {
                "name": "Avtobuslar",
                "headers": ["Avtobus", "Haydovchi", "Yo'nalish",
                            "Reys", "Km", "Holat"],
                "data": veh_rows,
            })

        if not daily_rows and not veh_rows:
            daily_rows = [{"Ko'rsatkich": "Ma'lumot", "Qiymat": "Topilmadi"}]
        title = "Kunlik hisobot"
        return await self._use_tool(
            "excel", action="create_report",
            title=title, sheet_name="Xulosa",
            data=daily_rows,
            extra_sheets=extra_sheets,
        )