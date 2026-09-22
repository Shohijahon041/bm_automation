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

    async def _llm_refine_driver(self, driver: str) -> str:
        """LLM yordamida haydovchi so'rovini takomillashtirish (optional).

        Aniq natija topilmaganda LLM ismni to'g'irlashi mumkin
        (masalan "XALILOV NUR" → "XALILOV NURMUHAMMAD", yoki kiril/lotin
        transliteratsiyasini tuzatish). LLM yo'q/xato bo'lsa — asl qiymat.
        """
        try:
            if self.llm is None or not self.llm.configured():
                return driver
        except Exception:  # noqa: BLE001
            return driver
        system = (
            "Siz transport bazasi uchun ism-normalizatorisiz. "
            "Foydalanuvchi haydovchi ismini noto'g'ri/kiril-lotin aralash "
            "yozgan bo'lishi mumkin. Eng ehtimoliy to'g'ri yozilishini "
            "qaytaring. FAQAT ismni qaytaring, boshqa hech narsa yo'q."
        )
        try:
            response = await self._call_llm(
                system,
                f"Haydovchi so'rovi: {driver}\nTo'g'ri ism:",
                max_tokens=40,
            )
            refined = (response or "").strip().strip('"')
            if refined and len(refined) <= 80 and refined.lower() != driver.lower():
                self.logger.info("LLM haydovchi ismini tuzatdi: %s → %s",
                                 driver, refined)
                return refined
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("LLM refine xatosi: %s", exc)
        return driver

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
                        # Self-check: oylik yakuniy raqamlar invariant bilan
                        # tekshiriladi (jami = bajarilgan + bajarilmagan).
                        # Norm (bot ko'rsatadigan) ustida ham, xom metrics
                        # ustida ham shu qoidalar mavjud bo'lsa tekshiramiz.
                        if isinstance(data, dict) and (
                                data.get("total_trips") is not None
                                or data.get("trips") is not None):
                            await self.self_check(data, "driver_monthly")
                        self.source_note = "PostgreSQL (ma'lumotlar bazasi) — oylik statistika"
                        self._finish(True, "Driver: oylik hisob tugadi")
                        return AgentResult(success=True, data=rich)

                    data = await self._use_tool(
                        "monthly", action="get_drivers_monthly",
                        month=month, route_id=route_id or "",
                        sort=context.params.get("sort", "net_pay"),
                        top=context.params.get("top", 0),
                    )
                    # Self-check: oylik yakuniy raqamlar invariant bilan
                    # tekshiriladi (jami = qabul + qabul qilinmagan va h.k.)
                    if isinstance(data, dict) and isinstance(
                            data.get("totals"), dict):
                        data = await self.self_check(data, "driver_monthly_rank")
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi) — oylik statistika"
                    self._finish(True, "Driver: oylik reyting tugadi")
                    return AgentResult(success=True, data=data)

                # --- Avans to'lovlari va jarimalar (avans/driver_fines) ---
                if action in ("avans", "advance", "fines", "jarima", "jarimalar"):
                    if not driver:
                        raise ValueError(
                            "Avans/jarima so'rovi uchun haydovchi ko'rsatilishi kerak")
                    is_avans = action in ("avans", "advance")
                    data = await self._use_tool(
                        "db", action="get_avans" if is_avans else "get_driver_fines",
                        driver=driver, month=month,
                        date="" if is_avans else date,
                    )
                    self.source_note = "PostgreSQL (ma'lumotlar bazasi)"
                    self._finish(True, f"Driver: {action} tugadi")
                    return AgentResult(
                        success=True,
                        data={"driver": data, "driver_name": driver,
                              "money_type": "avans" if is_avans else "fines"},
                    )

                try:
                    # Ayrim haydovchi so'ralgan bo'lsa — chuqur tahlil
                    if driver:
                        data = await self._use_tool(
                            "db", action="get_drivers", query=driver,
                        )
                        matched = data.get("drivers") or []
                        if not matched:
                            refined = await self._llm_refine_driver(driver)
                            if refined and refined != driver:
                                data = await self._use_tool(
                                    "db", action="get_drivers", query=refined,
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