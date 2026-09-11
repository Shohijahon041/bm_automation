"""Driver Agent — haydovchi ma'lumotlari (kunlik va oylik)."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.driver_agent")

_MONTHLY_WORDS = ("oylik", "monthly", "oy xulos", "oy yakun", "bu oy",
                  "oyi", "oy uchun")


def _is_monthly(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in _MONTHLY_WORDS)


class DriverAgent(BaseAgent):
    name = AgentType.DRIVER
    description = "Haydovchi ma'lumotlarini tahlil qiladi (kunlik, oylik)"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            route_id = context.params.get("route_id", context.route)
            driver = context.params.get(
                "driver", context.params.get("query", context.params.get("driver_name", ""))
            )
            date = context.params.get("date", context.date) or ""
            month = context.params.get("month", "")
            action = context.params.get("action", "")

            is_monthly = bool(month) or _is_monthly(context.user_request)
            if is_monthly and not month:
                month = context.params.get(
                    "month", context.params.get("date", ""))
            if not month and is_monthly:
                from datetime import date as _date
                month = _date.today().strftime("%Y-%m")

            try:
                # --- Oylik so'rov: haydovchi oyligi (km, maosh, jarimalar) ---
                if is_monthly:
                    if driver:
                        data = await self._use_tool(
                            "monthly", action="get_driver_monthly",
                            driver=driver, driver_id=context.params.get("driver_id", ""),
                            month=month,
                        )
                        rich = {
                            "period": data.get("period"),
                            "query": driver,
                            "driver": data,
                        }
                        self.source_note = "PostgreSQL (ma'lumotlar bazasi) — oylik statistika"
                        self._finish(True, "Driver: oylik hisob tugadi")
                        return AgentResult(success=True, data=rich)

                    data = await self._use_tool(
                        "monthly", action="get_drivers_monthly",
                        month=month, route_id=route_id or "",
                        sort=context.params.get("sort", "net_pay"),
                        top=context.params.get("top", 0),
                    )
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi) — oylik statistika"
                    self._finish(True, "Driver: oylik reyting tugadi")
                    return AgentResult(success=True, data=data)

                try:
                    # Ayrim haydovchi so'ralgan bo'lsa — chuqur tahlil
                    if driver:
                        data = await self._use_tool(
                            "db", action="get_drivers", query=driver,
                        )
                        matched = data.get("drivers") or []
                        if matched:
                            driver_id = matched[0]["external_id"]
                            rich = {
                                "query": driver,
                                "matches": matched,
                                "driver": matched[0],
                            }
                            # Jadval + reyslar + ish jurnali
                            if action in ("", "trips", "schedule", "report", "full"):
                                try:
                                    trips = await self._use_tool(
                                        "db", action="get_driver_trips",
                                        driver=driver, date=date,
                                    )
                                    rich["trips"] = trips
                                except Exception as exc:  # noqa: BLE001
                                    log.debug("driver trips xatosi: %s", exc)
                                try:
                                    sched = await self._use_tool(
                                        "db", action="get_driver_schedule",
                                        driver=driver, date=date,
                                    )
                                    rich["schedule"] = sched
                                except Exception as exc:  # noqa: BLE001
                                    log.debug("driver schedule xatosi: %s", exc)
                            if action in ("", "profile", "work", "full"):
                                try:
                                    work = await self._use_tool(
                                        "db", action="get_driver_work",
                                        driver=driver, date=date, days=30,
                                    )
                                    rich["work"] = work
                                except Exception as exc:  # noqa: BLE001
                                    log.debug("driver work xatosi: %s", exc)
                            self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                            self._finish(True, "Driver: DB tahlil tugadi")
                            return AgentResult(success=True, data=rich)

                    # Ro'yxat / filtrlash
                    data = await self._use_tool(
                        "db", action="get_drivers",
                        route_id=route_id, query=driver or "",
                    )
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                    self._finish(True)
                    return AgentResult(success=True, data=data)
                except Exception as exc:  # noqa: BLE001
                    self._finish(False, str(exc))
                    return AgentResult(success=False, error=str(exc))
            except Exception as exc:  # noqa: BLE001
                self._finish(False, str(exc))
                return AgentResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))