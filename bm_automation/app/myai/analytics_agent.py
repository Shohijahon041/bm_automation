"""Analytics Agent — hisob-kitoblarni bajaradi."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.analytics_agent")


class AnalyticsAgent(BaseAgent):
    """Analytics Agent — hisob-kitoblarni DETERMINISTIC Python code orqali bajaradi.

    LLM matematik hisoblashning yagona manbasi EMAS.
    """

    name = AgentType.ANALYTICS
    description = "Hisob-kitoblarni bajaradi (foiz, km, reyslar)"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            action = context.params.get("action", "calculate")
            transport_data = context.previous_results.get("transport", {})

            if action == "calculate":
                result = await self._calculate(transport_data)
            elif action == "compare":
                result = await self._compare(context)
            else:
                result = transport_data.get("summary", {})

            # Rich DB ma'lumotlarini (daily/problemlar/elektr/avtobus) oxirgi
            # natijaga olib o'tamiz — master javobi ularni ko'rsata olsin.
            self._carry_rich_db(context, result)

            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    def _carry_rich_db(self, context: AgentContext, result: dict) -> None:
        """Browser agent DB paylodi'dagi rich ma'lumotni natijaga bog'laydi.

        daily_summary / problems / electricity / vehicle_detail — transport
        normalizatsiyasi ularni ixcham `summary`ga keltiradi, lekin to'liq
        foydali maydonlarini saqlab qolamiz.
        """
        # Transport DBTool rich paylodini o'zi ko'tarib o'tgan bo'lishi mumkin
        br = context.previous_results.get("browser") or {}
        if isinstance(br, dict) and isinstance(br.get("data"), dict):
            db = br["data"]
            if isinstance(db, dict):
                if db.get("daily") or db.get("totals"):
                    result.setdefault("db_daily", db)
                if db.get("problems"):
                    result.setdefault("db_problems", db)
                if db.get("kwh_per_km") is not None or db.get("routes"):
                    result.setdefault("db_electricity", db)
                if db.get("vehicle") or db.get("route_daily"):
                    result.setdefault("db_vehicle", db)

    async def _calculate(self, transport_data: dict) -> dict:
        """Hisob-kitob qilish — deterministic.

        trips jadvalidagi har bir yozuv AVTOBUS EMAS — bitta QATNOV.
        Avtobuslar soni = total_vehicles (distinct), qatnovlar = total_trips.
        """
        summary = transport_data.get("summary", {})
        vehicles = transport_data.get("vehicles", [])
        driver_data = transport_data.get("driver_data")
        attendance_data = transport_data.get("attendance_data")

        units = summary.get("units", "trips")
        is_attendance = bool(attendance_data)

        if is_attendance or units == "buses":
            total = summary.get("total_buses", 0)
            active = summary.get("active_buses", 0)
            inactive = summary.get("inactive_buses", 0)
        else:
            total = summary.get("total_trips", 0)
            active = summary.get("accepted", 0)
            inactive = summary.get("not_accepted", 0)
        total_km = summary.get("total_km", 0)
        total_vehicles = summary.get("total_vehicles", 0)

        # Bajarilish foizi
        completion_rate = round((active / total * 100), 1) if total > 0 else 0

        # O'rtacha km
        avg_km = round(total_km / active, 2) if active > 0 else 0

        # Yo'nalishlar bo'yicha (route_trips/route_accepted/route_vehicles — 
        # transport tomonidan har bir yozuvga joylangan umumlashtirilgan qiymatlar)
        route_stats = {}
        for v in vehicles:
            route = v.get("route", v.get("route_name", "")) or "Noma'lum"
            if route not in route_stats:
                route_stats[route] = {"trips": 0, "km": 0, "accepted": 0, "vehicles": 0}
            route_stats[route]["trips"] = max(
                route_stats[route]["trips"], v.get("route_trips", 0))
            route_stats[route]["accepted"] = max(
                route_stats[route]["accepted"], v.get("route_accepted", 0))
            route_stats[route]["vehicles"] = max(
                route_stats[route]["vehicles"], v.get("route_vehicles", 0))
            route_stats[route]["km"] += v.get("km", 0)

        result = {
            "total_trips": total if not is_attendance else 0,
            "accepted": active if not is_attendance else 0,
            "not_accepted": inactive if not is_attendance else 0,
            "total_vehicles": total_vehicles if not is_attendance else total,
            "total_km": total_km,
            "completion_rate": round(summary.get("completion_rate", completion_rate), 1),
            "avg_km_per_trip": avg_km,
            "units": "buses" if is_attendance else "trips",
            "route_stats": route_stats,
        }
        if is_attendance:
            result["total_buses"] = total
            result["active_buses"] = active
            result["inactive_buses"] = inactive
        if driver_data:
            result["driver_data"] = driver_data
            result["driver_found"] = bool(driver_data.get("found"))
            result["driver_name"] = driver_data.get("full_name", "")
        if attendance_data:
            result["attendance_data"] = attendance_data
        return result

    async def _compare(self, context: AgentContext) -> dict:
        """Sayt va DB ma'lumotlarini solishtirish."""
        browser_data = context.previous_results.get("browser", {})
        db_data = context.previous_results.get("db", {})

        site_summary = browser_data.get("data", {}).get("summary", {})
        db_summary = db_data.get("data", {})

        # Farqlar
        diff_trips = (site_summary.get("total_trips", 0)
                      - db_summary.get("db_trips", 0))
        diff_km = round(
            site_summary.get("total_km", 0) - db_summary.get("db_km", 0), 2
        )

        return {
            "site": site_summary,
            "db": db_summary,
            "diff": {
                "trips": diff_trips,
                "km": diff_km,
            },
            "status": "OK" if diff_trips == 0 and diff_km == 0 else "DIFF",
        }
