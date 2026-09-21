"""Master Agent — asosiy orchestrator."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .agents import BaseAgent
from .models import (
    AgentContext, AgentResult, AgentType, AgentStatus,
    LLMMessage, StepStatus, Plan, PlanStep,
)
from .prompts.master import MASTER_SYSTEM, MASTER_CLASSIFY
from ..utils.logger import get_logger

log = get_logger("myai.master")

_MONTH_NAMES = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6,
    "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9,
    "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
}

_MONTH_KEYWORDS = ("oylik", "monthly", "oy xulos", "oy yakun", "bu oy")


class MasterAgent(BaseAgent):
    """Master Agent — asosiy orchestrator.

    Vazifalari:
    1. Foydalanuvchi topshirig'ini qabul qilish
    2. Topshiriq turini aniqlash (intent classification)
    3. Planner Agent'ga yuborish
    4. Agentlar ishini boshqarish
    5. Natijalarni birlashtirish
    6. Reviewer natijasini qabul qilish
    7. Yakuniy javobni foydalanuvchiga berish
    """

    name = AgentType.MASTER
    description = "Asosiy orchestrator — topshiriqni boshqaradi"

    def __init__(self, llm, tools=None):
        super().__init__(llm, tools)
        self._planner = None
        self._reviewer = None
        self._agents: dict[AgentType, BaseAgent] = {}

    def set_planner(self, planner) -> None:
        """Planner Agent ni o'rnatish."""
        self._planner = planner

    def set_reviewer(self, reviewer) -> None:
        """Reviewer Agent ni o'rnatish."""
        self._reviewer = reviewer

    def register_agent(self, agent: BaseAgent) -> None:
        """Domain agent ni ro'yxatga olish."""
        self._agents[agent.name] = agent
        log.info("Agent ro'yxatga olindi: %s", agent.name.value)

    async def run(self, context: AgentContext) -> AgentResult:
        """Asosiy bajarish — topshiriqni to'liq siklida bajarish."""
        self._start()
        try:
            # 1. Intent aniqlash
            intent = await self._classify_intent(context.user_request)
            log.info("Intent aniqlandi: %s", intent.get("intent", "unknown"))

            # 2. Planner dan plan olish
            if self._planner is None:
                return AgentResult(
                    success=False,
                    error="Planner Agent o'rnatilmagan",
                )

            plan = await self._planner.create_plan(
                user_request=context.user_request,
                intent=intent,
                params=context.params,
            )
            log.info("Plan yaratildi: %d qadam", len(plan.steps))

            # 3. Har bir step ni bajarish
            results = {}
            for i, step in enumerate(plan.steps):
                agent = self._get_agent(step.agent)
                if agent is None:
                    log.warning("Agent topilmadi: %s", step.agent)
                    continue

                step_context = AgentContext(
                    task_id=context.task_id,
                    user_request=context.user_request,
                    route=context.route,
                    date=context.date,
                    params={**context.params, **step.params},
                    previous_results=results,
                    step_index=i,
                )

                try:
                    result = await agent.run(step_context)
                    results[step.agent.value] = result.data
                    log.info("Step %d/%d tugadi: %s",
                             i + 1, len(plan.steps), step.agent.value)
                except Exception as exc:  # noqa: BLE001
                    log.error("Step %d/%d xatosi: %s — %s",
                              i + 1, len(plan.steps), step.agent.value, exc)
                    results[step.agent.value] = {"error": str(exc)}

            # 4. Reviewer dan tasdiq
            review_approved = True
            if self._reviewer:
                review = await self._reviewer.verify(
                    agent_name="master",
                    input_data={"steps": [
                        {"agent": s.agent.value, "action": s.action,
                         "params": s.params}
                        for s in plan.steps
                    ]},
                    output_data=results,
                )
                review_approved = review.approved
                if not review_approved:
                    log.warning("Reviewer rad etdi: %s", review.errors)

            # 5. Yakuniy javob
            final_response = await self._build_response(context, results)
            self._finish(True, "Topshiriq bajarildi")
            return AgentResult(
                success=True,
                data={
                    "response": final_response,
                    "steps_results": results,
                    "review_approved": review_approved,
                },
            )

        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def _classify_intent(self, user_request: str) -> dict:
        """Foydalanuvchi so'rovini tahlil qilish."""
        try:
            response = await self._call_llm(
                MASTER_CLASSIFY.format(user_request=user_request),
                user_request,
                max_tokens=200,
            )
            # JSON parse qilish
            response = response.strip()
            if response.startswith("```"):
                response = response.split("\n", 1)[1].rsplit("```", 1)[0]
            try:
                result = json.loads(response)
                if isinstance(result, dict) and result.get("intent"):
                    # Telegram'ga yuborish flag'i LLM natijasidan mustaqil
                    # qo'shiladi — aks holda qadam plan'ga tushmasdi.
                    rp = result.setdefault("params", {})
                    self._apply_send_flag(rp, user_request.lower())
                    # LLM noto'g'ri umumiy intent tanlasa va so'rovda
                    # yo'nalish/avtobus/haydovchi ishorasi bo'lsa — aniq
                    # deterministik intent ustun bo'ladi (model noaniqligi).
                    det = self._classify_deterministic(user_request)
                    strong = ("check_route", "check_vehicle", "check_driver",
                              "attendance", "schedule", "not_accepted_km",
                              "routes_list", "vehicles_list", "avans", "fines",
                              "documents", "staff", "dispatcher_routes",
                              "waybills", "sms")
                    if (det.get("params") or {}).get("route"):
                        strong += ("daily_summary",)
                    if (det.get("intent") in strong
                            and result.get("intent") in (
                                "monthly_report", "monthly_driver",
                                "daily_summary", "monthly", "general",
                                "check_route", "check_vehicle")):
                        det.setdefault("params", {})
                        self._apply_send_flag(det["params"],
                                              user_request.lower())
                        return det
                    return result
            except json.JSONDecodeError:
                log.warning("Intent JSON emas (%s) — deterministik tahlil", response[:80])
        except Exception as exc:  # noqa: BLE001
            log.warning("Intent aniqlanmadi: %s — deterministik tahlil", exc)

        return self._classify_deterministic(user_request)

    @staticmethod
    def _apply_send_flag(params: dict, lower: str) -> None:
        """'yubor/telegram' so'zlari bo'lsa send_telegram flag'ini qo'yadi."""
        if any(w in lower for w in (
                "yubor", "telegramga", "telegram ga", "telegram",
                "send", "jo'nat", "jonat", "elchi", "yetkazi")):
            params["send_telegram"] = True

    @staticmethod
    def _extract_month(lower: str) -> str:
        """So'rovdan oyni aniqlaydi → '2026-08'.

        Qabul qiladi: '2026-08', '08.2026', '08-2026', Uzbek oy nomlari
        ('avgust', 'avgustda', 'sentabr') — yil aniq bo'lmasa joriy yil.
        """
        m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", lower)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        m = re.search(r"\b(0[1-9]|1[0-2])[.\-\\/](20\d{2})\b", lower)
        if m:
            return f"{m.group(2)}-{m.group(1)}"
        year = datetime.now().year
        ym = re.search(r"\b(20\d{2})\b", lower)
        if ym:
            try:
                year = int(ym.group(1))
            except (TypeError, ValueError):
                year = datetime.now().year
        for name, num in _MONTH_NAMES.items():
            if name in lower:
                return f"{year}-{num:02d}"
        return ""

    @staticmethod
    def _parse_daydate(lower: str) -> str:
        """Kun ko'rsatilgan oy nomini sanaga aylantiradi → ISO date.

        "13-sentabr", "13 sentyabr 2026", "13-sentabrda" → "2026-09-13".
        Faqat oy nomi ("avgust") bo'lsa — bo'sh qaytadi (month emas).
        """
        months = {
            "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5,
            "iyun": 6, "iyul": 7, "avgust": 8, "sentabr": 9, "sentyabr": 9,
            "oktabr": 10, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
        }
        year = datetime.now().year
        ym = re.search(r"\b(20\d{2})\b", lower)
        if ym:
            try:
                year = int(ym.group(1))
            except (TypeError, ValueError):
                year = datetime.now().year
        for name, num in sorted(
                months.items(), key=lambda kv: len(kv[0]), reverse=True):
            m = re.search(
                r"\b(\d{1,2})\s*[-–']?\s*" + name + r"(?:dagi|da|gi)?\b",
                lower)
            if m:
                try:
                    return datetime(year, num, int(m.group(1))).date().isoformat()
                except (TypeError, ValueError):
                    continue
        return ""

    @staticmethod
    def _classify_deterministic(user_request: str) -> dict:
        """LLM bo'lmasa — qoidalar bilan intent aniqlash (insonga o'xshash)."""
        text = (user_request or "").strip()
        lower = text.lower()
        params: dict[str, Any] = {}

        route_pattern = re.search(
            r"\b(?:B|V|SHI|SH|В|М|M|A)\s?[-–—]?\s?(\d{1,3})\b", text, re.IGNORECASE)
        if route_pattern:
            route = route_pattern.group(0)
            match = re.search(r"([A-Za-zА-Яа-яЁё]{1,4}\s?[-–—]?\s?\d{1,3})",
                              text, re.IGNORECASE)
            params["route"] = match.group(1) if match else route

        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
        date_match2 = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text)
        date_match3 = re.search(r"\b(\d{2}\.\d{2})\b", text)
        if date_match:
            params["date"] = date_match.group(1)
        elif date_match2:
            d, m, y = date_match2.group(1).split(".")
            params["date"] = f"{y}-{m}-{d}"
        elif date_match3:
            d, m = date_match3.group(1).split(".")
            params["date"] = f"{datetime.now().year}-{m}-{d}"
        elif any(w in lower for w in ("kecha", "bugun", "ertaga")):
            from datetime import timedelta
            base = datetime.now().date()
            if "kecha" in lower:
                params["date"] = (base - timedelta(days=1)).isoformat()
            elif "ertaga" in lower:
                params["date"] = (base + timedelta(days=1)).isoformat()
            else:
                params["date"] = base.isoformat()

        driver_hint = re.search(
            r"([А-ЯЁA-Z][a-zа-яё]+(?:\s+[А-ЯЁA-Z][a-zа-яё]+){1,4}"
            r"(?:\s*[А-ЯЁA-Z]\.?)*)", text)
        if not driver_hint:
            # ALL-CAPS ism (XALILOV NURMUHAMMAD ...)
            driver_hint = re.search(
                r"\b([А-ЯЁA-Z]{2,}(?:\s+[А-ЯЁA-Z]{2,}){1,3})\b", text)
        if driver_hint and any(w in lower for w in (
                "haydovchi", "haydovchisini", "shofyor", "driver",
                "haydovchisi", "kiritdi", "tekshir", "haqida",
                "ma'lumot", "malumot", "informatsiya", "kim bu",
                "kim bu haydovchi")):
            params["driver"] = driver_hint.group(1).strip()

        # Oylik aniqlash — oy nomi/sanasi va 'oylik' so'zlari.
        # Kun ko'rsatilgan oy nomi ("13-sentabr") — sana, oy emas:
        # bunday holatda month o'rniga date param qo'yiladi.
        if not params.get("month"):
            day_dt = MasterAgent._parse_daydate(lower)
            if day_dt and not params.get("date"):
                params["date"] = day_dt
            if not params.get("date"):
                month = MasterAgent._extract_month(lower)
                if month:
                    params["month"] = month

        # Telegram'ga yuborish so'rovi bormi? — flag sifatida saqlanadi.
        MasterAgent._apply_send_flag(params, lower)

        daily_words = ("hisobot", "hisobotini", "xulosa", "umumiy",
                       "barcha yo'nalish", "kunlik", "hozirgi holat",
                       "kundalik", "qancha avtobus chiqqan")

        # Reyting / top / eng yaxshi haydovchi so'rovlari
        _SORT_HINTS = (
            ("reyting bo'yicha", "rating"), ("reyting", "rating"),
            ("baho", "rating"), ("eng yaxshi", "rating"),
            ("eng ko'p km", "km"), ("eng kop km", "km"),
            ("eng ko'p qatnov", "trips"), ("eng kop qatnov", "trips"),
            ("eng ko'p reys", "trips"), ("eng kop reys", "trips"),
        )
        for _phrase, _s in _SORT_HINTS:
            if _phrase in lower:
                params["sort"] = _s
                break
        if re.search(r"\btop\b", lower):
            params.setdefault("sort", "rating")

        # Avval aniq intentlar tekshiriladi (daily umumiy so'zlaridan oldin)
        if any(w in lower for w in ("attendance", "ishga chiqish", "qatnashish",
                                    "davomati", "keldimi", "davomat", "qatnashdi")):
            return {"intent": "attendance", "params": params}
        if any(w in lower for w in ("jadval", "smеna", "grafik", "schedule",
                                    "smеna jadvali", "smеnasi")):
            return {"intent": "schedule", "params": params}
        # Qabul qilinmagan KM hisoboti (oylik blokdan oldin — o'z aniq intenti)
        if ("qabul qilinmagan" in lower or "not accepted" in lower
                or "not_accepted" in lower):
            return {"intent": "not_accepted_km", "params": params}
        # Reyting/top haydovchi → oylik reyting (month o'zi yoziladi)
        if params.get("sort") and ("haydovchi" in lower or "driver" in lower):
            return {"intent": "monthly_report", "params": params}
        # Excel / fayl ko'rinishidagi hisobot so'rovi — oylik/daily bloklaridan
        # oldin tekshiriladi ("oylik hisobotni excel qilib" → report_excel).
        if any(w in lower for w in ("excel", "xlsx", "fayl qilib", "jadval qilib",
                                    "yuklab ol", "fil yukla", ".xlsx")):
            return {"intent": "report_excel", "params": params}
        # Oylik so'rov (maosh/daily so'zlaridan oldin — 'oylik maosh'/'oylik
        # hisobot' kabi iboralar oylik hisobga tushadi).
        if params.get("month") or any(w in lower for w in _MONTH_KEYWORDS):
            if params.get("driver") and not params.get("send_telegram"):
                return {"intent": "monthly_driver", "params": params}
            return {"intent": "monthly_report", "params": params}
        if any(w in lower for w in ("maosh", "salary", "oklad", "ish haqi",
                                    "hisobla")):
            return {"intent": "salary", "params": params}
        # Avans to'lovlari
        if any(w in lower for w in ("avans", "avanslari", "avansim",
                                    "oldindan to'lov")):
            if not params.get("driver"):
                _nm = re.search(
                    r"\b([А-ЯЁA-Z]{3,}(?:\s+[А-ЯЁA-Z]{3,}){0,3})\b", text)
                if _nm:
                    params["driver"] = _nm.group(1).strip()
            return {"intent": "avans", "params": params}
        # Jarimalar
        if any(w in lower for w in ("jarima", "jarimalari", "jarimalar",
                                    "jarimasi", "fines", "fine", "shtraf")):
            if not params.get("driver"):
                _nm = re.search(
                    r"\b([А-ЯЁA-Z]{3,}(?:\s+[А-ЯЁA-Z]{3,}){0,3})\b", text)
                if _nm:
                    params["driver"] = _nm.group(1).strip()
            return {"intent": "fines", "params": params}
        # Hujjatlar
        if any(w in lower for w in ("hujjat", "hujjatlar", "dokument",
                                    "documents", "spravka")):
            return {"intent": "documents", "params": params}
        # Xodimlar
        if any(w in lower for w in ("xodimlar", "xodim", "staff", "hodimlar",
                                    "hodim")):
            return {"intent": "staff", "params": params}
        # Dispecher yo'nalishlari
        if ("dispecher yo'nalish" in lower or "dispatcher routes" in lower
                or "dispecher yoʻnalish" in lower
                or "dispecher yo`nalish" in lower):
            return {"intent": "dispatcher_routes", "params": params}
        # Yo'l varaqalari
        if any(w in lower for w in ("yo'l varaqa", "yo'l varaqasi",
                                    "yoʻl varaqa", "yo`l varaqa",
                                    "waybill", "putevoy list")):
            return {"intent": "waybills", "params": params}
        # SMS tarixi / jo'natmalar
        if any(w in lower for w in ("sms tarixi", "sms log", "smslar",
                                    "jo'natmalar", "yuborilgan sms",
                                    "sms jo'natma")):
            return {"intent": "sms", "params": params}
        if any(w in lower for w in ("muammo", "problems", "nosoz", "kechik")):
            return {"intent": "problems", "params": params}
        if any(w in lower for w in daily_words):
            return {"intent": "daily_summary", "params": params}
        # Ro'yxatlar — oylik/daily bloklaridan keyin (route'ga bog'lanmaganlar)
        if not params.get("route") and any(w in lower for w in (
                "yo'nalishlar ro'yxati", "barcha yo'nalishlar",
                "qancha yo'nalish", "nechta yo'nalish", "yo'nalishlar soni")):
            return {"intent": "routes_list", "params": params}
        if not params.get("route") and any(w in lower for w in (
                "avtobuslar ro'yxati", "barcha avtobuslar", "avtobus parki",
                "qancha avtobus bor", "nechta avtobus bor",
                "avtobuslar soni", "avtobuslari ro'yxati")):
            return {"intent": "vehicles_list", "params": params}
        if driver_hint and ("drivers" in lower or params.get("driver")
                            or "haydovchi" in lower or "shofyor" in lower):
            return {"intent": "check_driver", "params": params}
        if params.get("route"):
            veh_words = ("avtobus" in lower or "mashina" in lower
                         or "transport" in lower)
            list_words = any(w in lower for w in (
                "qancha", "nechta", "ro'yxati", "barcha", "holati",
                "chiqqan", "chiqgan", "yo'lda", "harakatda"))
            if veh_words and list_words:
                return {"intent": "check_route", "params": params}
            if veh_words:
                return {"intent": "check_vehicle", "params": params}
            return {"intent": "check_route", "params": params}
        if "marshrut" in lower or "yo'nalish" in lower:
            return {"intent": "check_route", "params": params}
        if params.get("send_telegram"):
            return {"intent": "telegram", "params": params}
        return {"intent": "general", "params": params}

    def _get_agent(self, agent_type: AgentType) -> BaseAgent | None:
        """Agent ni olish."""
        return self._agents.get(agent_type)

    async def _build_response(self, context: AgentContext, results: dict,
                              sources: dict[str, str] | None = None) -> str:
        """Yakuniy javobni yaratish.

        sources — har bir agent qaysi manbadan ma'lumot olgani.
        Javobda manbalar eslatib o'tiladi (shaffoflik).
        """
        sources = sources or {}
        system = (
            "Siz transport tizimi uchun AI-yordamchisiz. "
            "Natijalarni tushunarli formatda O'zbek tilida qaytaring. "
            "Faqat berilgan ma'lumotlardagi faktlardan foydalaning. "
            "Agar manbalar berilgan bo'lsa, javobda qaysi manbadan "
            "olinganini ko'rsating (masalan 'Manba: PostgreSQL')."
        )
        source_block = ""
        if sources:
            lines = [f"  • {agent}: {src}" for agent, src in sources.items()]
            source_block = "\n\nManbalar:\n" + "\n".join(lines)
        user = (
            f"So'rov: {context.user_request}\n\n"
            f"Natijalar:\n{json.dumps(results, ensure_ascii=False, indent=2)}"
            f"{source_block}"
        )
        try:
            response = await self._call_llm(system, user)
            return response
        except Exception:  # noqa: BLE001
            # LLM ishlamasa — oddiy format
            return self._format_fallback(results, sources)

    def _format_fallback(self, results: dict,
                         sources: dict[str, str] | None = None) -> str:
        """LLM bo'lmaganida ham insoniy, strukturli javob yaratish."""
        sources = sources or {}
        lines: list[str] = []

        ana = results.get("analytics") or {}
        driver_data = ana.get("driver_data") if isinstance(ana, dict) else None
        attendance_data = ana.get("attendance_data") if isinstance(ana, dict) else None

        # 0. Oylik haydovchi ma'lumoti (driver agent — oylik register)
        drv = results.get("driver") or {}
        if isinstance(drv, dict) and drv.get("period"):
            period = drv.get("period", "")
            if "drivers" in drv:
                totals = drv.get("totals") or {}
                lines.append(
                    f"📅 Oylik haydovchilar ({period}): "
                    f"{totals.get('active_drivers', 0)}/{totals.get('drivers', 0)} "
                    "faol, "
                    f"jami qatnovlar: {totals.get('total_trips', 0)}, "
                    f"masofa: {totals.get('total_km', 0)} km, "
                    f"net maosh: {totals.get('total_net', 0)} so'm.")
                for r in (drv.get("drivers") or [])[:5]:
                    lines.append(
                        f"  • {r.get('name', '-')}: {r.get('km', 0)} km, "
                        f"{r.get('trips', 0)} qatnov, "
                        f"gross {r.get('gross_pay', 0)} / "
                        f"net {r.get('net_pay', 0)} so'm, "
                        f"jarima {r.get('fines', 0)}")
            elif isinstance(drv.get("driver"), dict) and drv["driver"].get("found"):
                d = drv["driver"]
                lines.append(
                    f"👤 Oylik hisob ({period}): {d.get('name', '-')} — "
                    f"{d.get('km', 0)} km, {d.get('trips', 0)} qatnov, "
                    f"{d.get('working_days', 0)} ish kuni, "
                    f"gross {d.get('gross_pay', 0)} / net {d.get('net_pay', 0)} "
                    f"so'm, jarima {d.get('fines', 0)}, baho {d.get('rating', 0)}")
            elif isinstance(drv.get("driver"), dict):
                d = drv["driver"]
                lines.append(
                    f"Haydovchi topilmadi yoki bu davrda ma'lumot yo'q: "
                    f"{d.get('query', drv.get('query', '-'))}")

        # 0.4. Avans / jarimalar (driver agent — avans/driver_fines)
        if isinstance(drv, dict):
            _mtype = drv.get("money_type")
            _minner = drv.get("driver")
            if _mtype and isinstance(_minner, dict):
                _mname = drv.get("driver_name") or _minner.get("driver", "")
                if _mtype == "avans":
                    lines.append(
                        f"💰 Avans ({_mname}): jami {_minner.get('total', 0)} "
                        f"so'm — {_minner.get('count', 0)} ta to'lov.")
                    for a0 in (_minner.get("avans") or [])[:10]:
                        lines.append(
                            f"  • {str(a0.get('pay_date', ''))[:10]}: "
                            f"{a0.get('amount', 0)} so'm — "
                            f"{a0.get('note', '')} ({a0.get('route_name', '')})")
                else:
                    lines.append(
                        f"⚠️ Jarimalar ({_mname}): jami {_minner.get('total', 0)} "
                        f"so'm — {_minner.get('count', 0)} ta.")
                    for f0 in (_minner.get("fines") or [])[:10]:
                        lines.append(
                            f"  • {str(f0.get('date', ''))[:10]}: "
                            f"{f0.get('amount', 0)} so'm — "
                            f"{f0.get('reason', '')} [{f0.get('status', '')}]")

        # 0.5. Qabul qilinmagan KM hisoboti (browser get_not_accepted_km)
        br = results.get("browser")
        br_inner = br.get("data") if isinstance(br, dict) else None
        if isinstance(br_inner, dict) and br_inner.get("month") \
                and br_inner.get("drivers"):
            rej = br_inner
            rej_totals = rej.get("totals") or {}
            lines.append(
                f"📉 Qabul qilinmagan KM ({rej.get('month')}): "
                f"{rej.get('drivers_count', len(rej.get('drivers') or []))} "
                f"ta haydovchi — reja {rej_totals.get('plan_reys', 0)} / "
                f"amalda {rej_totals.get('fact_reys', 0)} reys, "
                f"qabul qilinmagan {rej_totals.get('qabul_qilinmagan', 0)} reys, "
                f"masofa farqi {rej_totals.get('diff', 0)} km.")
            for r in (rej.get("drivers") or [])[:8]:
                nc = int(r.get("qabul_qilinmagan", 0) or 0)
                if nc:
                    lines.append(
                        f"  • {r.get('name', '-')}: "
                        f"qabul {nc} reys, "
                        f"masofa farq {r.get('diff', 0)} km, "
                        f"ish kuni {r.get('days', 0)}")
            rejects_shown = True
        else:
            rejects_shown = False

        # 0.6. Yo'nalishlar ro'yxati (browser get_all_routes_summary)
        routes_shown = False
        if isinstance(br_inner, dict) and br_inner.get("routes") \
                and isinstance(br_inner.get("summary"), dict) \
                and "daily" not in br_inner:
            routes_shown = True
            rts = br_inner.get("routes") or []
            rt_t = br_inner.get("summary") or {}
            lines.append(
                f"🗺️ Yo'nalishlar ({br_inner.get('date', '')}): "
                f"jami {len(rts)} ta yo'nalish, "
                f"{rt_t.get('total_trips', 0)} reys, "
                f"qabul {rt_t.get('accepted', 0)}, "
                f"jami km {rt_t.get('total_km', 0)}.")
            for r0 in rts[:15]:
                lines.append(
                    f"  • {r0.get('route_name', '-')}: "
                    f"{r0.get('total_trips', 0)} reys "
                    f"({r0.get('completion_rate', 0)}%), "
                    f"{r0.get('total_km', 0)} km, "
                    f"{r0.get('total_vehicles', 0)} avtobus")

        # 0.7. Avtobuslar ro'yxati (db get_vehicles)
        if isinstance(br_inner, dict) and br_inner.get("vehicles") \
                and "count" in br_inner and "summary" not in br_inner:
            vlist = br_inner.get("vehicles") or []
            lines.append(
                f"🚌 Avtobuslar: jami {br_inner.get('count', len(vlist))} ta")
            for v0 in vlist[:20]:
                lines.append(
                    f"  • {v0.get('plate_number', '-')} — "
                    f"{v0.get('model', '')}, "
                    f"garaj №{v0.get('garage_number', '')}")

        # 0.8. Hujjatlar ro'yxati (db get_documents)
        if isinstance(br_inner, dict) and br_inner.get("documents"):
            dlist = br_inner.get("documents") or []
            lines.append(
                f"📄 Hujjatlar: {br_inner.get('count', len(dlist))} ta")
            for d0 in dlist[:15]:
                lines.append(
                    f"  • {d0.get('title', '-')} ({d0.get('category', '')}) — "
                    f"{d0.get('status', '')}, {d0.get('driver_name', '-')}")

        # 0.9. Xodimlar (db get_staff)
        if isinstance(br_inner, dict) and br_inner.get("staff"):
            slist = br_inner.get("staff") or []
            lines.append(
                f"👥 Xodimlar: {br_inner.get('count', len(slist))} ta")
            for s0 in slist[:15]:
                lines.append(
                    f"  • {s0.get('name', '-')} — {s0.get('position', '')} "
                    f"({s0.get('company', '')}), "
                    f"{s0.get('rate', 0)} so'm/{s0.get('salary_type', '')}")

        # 0.10. Dispecher yo'nalishlari (db get_dispatcher_routes)
        if isinstance(br_inner, dict) and br_inner.get("routes") \
                and "summary" not in br_inner and "vehicles" not in br_inner \
                and "daily" not in br_inner:
            drlist = br_inner.get("routes") or []
            lines.append(
                f"📡 Dispecher yo'nalishlari: "
                f"{br_inner.get('count', len(drlist))} ta")
            for dr0 in drlist[:10]:
                lines.append(
                    f"  • {dr0.get('route_name', '-')} — "
                    f"{dr0.get('company', '')} ({dr0.get('phone', '')})")

        # 0.11. Yo'l varaqalari (db get_waybills)
        if isinstance(br_inner, dict) and br_inner.get("waybills"):
            wlist = br_inner.get("waybills") or []
            lines.append(
                f"🧾 Yo'l varaqalari: {br_inner.get('count', len(wlist))} ta")
            for w0 in wlist[:15]:
                lines.append(
                    f"  • {str(w0.get('date', ''))[:10]} "
                    f"{w0.get('plate_number', '-')} — "
                    f"{w0.get('driver_name', '-')}, {w0.get('route_name', '')}, "
                    f"{w0.get('direction', '')} {w0.get('status', '')}")

        # 0.12. SMS tarixi (db get_sms)
        if isinstance(br_inner, dict) and br_inner.get("sms"):
            mlist = br_inner.get("sms") or []
            lines.append(
                f"📱 SMS tarixi: {br_inner.get('count', len(mlist))} ta")
            for m0 in mlist[:10]:
                lines.append(
                    f"  • {str(m0.get('send_at', ''))[:16]} → "
                    f"{m0.get('name', '-')} ({m0.get('phone', '')}): "
                    f"{m0.get('status', '')}")

        # 1. Haydovchi ma'lumoti
        if driver_data:
            d = driver_data.get("driver") or {}
            found = bool(driver_data.get("found") or d)
            if not found:
                lines.append(
                    f"Haydovchi topilmadi: {driver_data.get('full_name', '') or ''} "
                    "bazada yo'q.")
            else:
                name = (d.get("full_name") if isinstance(d, dict)
                        else driver_data.get("full_name", ""))
                lines.append(f"👤 Haydovchi: {name}")
                if isinstance(d, dict):
                    if d.get("phone"):
                        lines.append(f"Telefon: {d['phone']}")
                    lines.append(
                        "Reyting: {} | Blacklist: {}".format(
                            d.get("rating", 0),
                            "ha" if d.get("blacklisted") else "yo'q"))
                trips_count = driver_data.get("trips_count", 0)
                lines.append(
                    f"Qatnovlar: {ana.get('total_trips', trips_count)} — "
                    f"qabul qilingan: {ana.get('accepted', 0)}, "
                    f"qabul qilinmagan: {ana.get('not_accepted', 0)}")
                if ana.get("total_km"):
                    lines.append(f"Umumiy masofa: {ana.get('total_km')} km")
                if ana.get("completion_rate"):
                    lines.append(
                        f"Bajarilish: {ana.get('completion_rate')}%")
                if driver_data.get("work_log_days"):
                    lines.append(
                        f"Ish jurnali: so'nggi {driver_data['work_log_days']} kun yozuvi")
                if driver_data.get("schedule_count"):
                    lines.append(
                        f"Bugungi jadval: {driver_data['schedule_count']} yozuv")
            if ana.get("route_stats"):
                lines.append("Yo'nalishlar bo'yicha:")
                for r, st in ana["route_stats"].items():
                    lines.append(
                        f"  • {r}: {st['trips']} qatnov, "
                        f"{st.get('vehicles', 0)} avtobus, {st['km']:.1f} km")

        # 2. Yo'nalish / marshrut — asosiy birlik: QATNOV, avtobus emas
        elif ana.get("total_trips") and not attendance_data and not routes_shown:
            vehicles = ana.get("total_vehicles", 0)
            lines.append(
                f"🚏 Qatnovlar: jami {ana.get('total_trips')} — "
                f"qabul qilingan: {ana.get('accepted')}, "
                f"qabul qilinmagan: {ana.get('not_accepted')}")
            if vehicles:
                lines.append(f"🚌 Avtobuslar: {vehicles} ta")
            lines.append(
                f"Bajarilish: {ana.get('completion_rate')}% | "
                f"Umumiy masofa: {ana.get('total_km')} km")
            if ana.get("route_stats"):
                lines.append("Yo'nalishlar bo'yicha:")
                for r, st in ana["route_stats"].items():
                    lines.append(
                        f"  • {r}: {st['trips']} qatnov, "
                        f"{st.get('vehicles', 0)} avtobus, {st['km']:.1f} km")

        # 3. Davomat
        if attendance_data:
            lines.append(
                f"✅ Davomat: {attendance_data.get('present', 0)}/"
                f"{attendance_data.get('scheduled_count', attendance_data.get('present', 0))} "
                f"({attendance_data.get('attendance_rate', 0)}%)")
            absent = attendance_data.get("absent_list") or []
            if absent:
                lines.append(
                    "Kelmaganlar: " + ", ".join(
                        a.get("vehicle_number", "") for a in absent[:10]))

        # 3.5. Yo'nalish DB natijalari (RouteAgent — get_route_daily + boyitma)
        route_step = results.get("route")
        if isinstance(route_step, dict):
            rname = route_step.get("route_name") or "Yo'nalish"
            rdate = route_step.get("date") or ""
            daily = route_step.get("data") or []
            if route_step.get("no_data"):
                lines.append(
                    f"🚏 {rname}: {rdate} uchun ma'lumot yo'q — "
                    f"oxirgi yozuv: {route_step.get('latest_date') or 'topilmadi'}.")
            elif daily:
                plan = sum(int(r.get("trip_plan", 0) or 0) for r in daily)
                fact = sum(int(r.get("trip_fact", 0) or 0) for r in daily)
                working = sum(1 for r in daily
                              if int(r.get("working_day", 0) or 0))
                on = sum(1 for r in daily
                         if int(r.get("trip_fact", 0) or 0) > 0)
                lines.append(
                    f"🚏 {rname}: {rdate} — {len(daily)} ta avtobus "
                    f"(ishchi {working}, ishda {on}), "
                    f"reja {plan} / amalda {fact} reys, "
                    f"yetmagan {plan - fact}.")
            else:
                lines.append(f"🚏 {rname}: {rdate} — Kunlik yozuv yo'q.")
            anom = route_step.get("trip_anomalies")
            if isinstance(anom, dict) and anom.get("found"):
                lines.append(
                    f"⚠️ Reys anomaliyalari: {anom.get('anomalies_count', 0)} ta "
                    f"avtobusda jami {anom.get('missing_trips_total', 0)} reys "
                    f"yetmagan (reja {anom.get('trip_plan_total', 0)} / "
                    f"amalda {anom.get('trip_fact_total', 0)}).")
            veh = route_step.get("route_vehicles")
            if isinstance(veh, dict) and veh.get("found"):
                lines.append(
                    f"🚌 Avtobuslar: jami {veh.get('vehicles_count', 0)}, "
                    f"yo'lda {veh.get('on_route', 0)} "
                    f"(tashqarida {veh.get('off_route', 0)}).")

        # 4. Qidiruv natijalari (search tool)
        sr = results.get("search")
        if isinstance(sr, dict) and (sr.get("found") or sr.get("count")):
            lines.append(
                f"🔎 “{sr.get('query', '')}” bo'yicha qidiruv: "
                f"{sr.get('count', 0)} ta topildi.")
            s_routes = sr.get("routes") or []
            if s_routes:
                lines.append("  🗺️ Yo'nalishlar: " + ", ".join(
                    str(x.get("name", "")) for x in s_routes[:10]))
            s_drivers = sr.get("drivers") or []
            if s_drivers:
                lines.append("  👤 Haydovchilar: " + ", ".join(
                    str(x.get("full_name", "")) for x in s_drivers[:10]))
            s_vehicles = sr.get("vehicles") or []
            if s_vehicles:
                lines.append("  🚌 Avtobuslar: " + ", ".join(
                    str(x.get("plate_number", "")) for x in s_vehicles[:10]))

        # 4.5. Report agent matni (kunlik xulosa ko'rsatilmagan bo'lsa)
        rep = results.get("report")
        if isinstance(rep, dict) and rep.get("report"):
            has_daily_summary = (isinstance(ana, dict)
                                 and isinstance(ana.get("db_daily"), dict))
            if not has_daily_summary:
                lines.append(str(rep["report"]))

        # 5. Umumiy / boshqa
        if not lines:
            for agent_name, data in results.items():
                if isinstance(data, dict) and not data.get("error"):
                    keys = list(data.keys())[:4]
                    lines.append(f"• {agent_name}: " + ", ".join(keys))
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"• {agent_name}: xato — {data['error']}")

        # 5. Rich DB ma'lumotlari — analytics tomonidan olib o'tilgan
        #    (rejects hisoboti ko'rsatilgan bo'lsa, bo'sh "Kunlik xulosa" o'tkazib
        #    yuboriladi — chalkashtirmaslik uchun).
        if isinstance(ana, dict) and not rejects_shown:
            db_daily = ana.get("db_daily")
            if isinstance(db_daily, dict):
                tot = db_daily.get("totals", {})
                perf = tot.get("performance", 0)
                acc = tot.get("accept_rate", 0)
                lines.append(
                    f"📊 Kunlik xulosa ({db_daily.get('date', '')}): "
                    f"reja {tot.get('planned', 0)}, amalda {tot.get('actual', 0)}, "
                    f"qabul qilingan {tot.get('accepted', 0)}, "
                    f"qabul qilinmagan {tot.get('not_accepted', 0)}, "
                    f"muammolar {tot.get('problems_total', 0)}. "
                    f"Perf {perf}% | Accept {acc}%.")
                if tot.get("vehicles_out"):
                    lines.append(
                        f"🚌 Ishda: {tot['vehicles_out']} ta avtobus.")
            db_problems = ana.get("db_problems")
            if isinstance(db_problems, dict):
                p = db_problems.get("problems", {})
                lines.append(
                    f"⚠️ Muammolar: GPS {p.get('gps', 0)}, "
                    f"texnik {p.get('technical', 0)}, "
                    f"jadval {p.get('schedule', 0)} — "
                    f"jami {p.get('total', 0)}.")
            db_elec = ana.get("db_electricity")
            if isinstance(db_elec, dict) and not routes_shown:
                t = db_elec.get("totals", {})
                lines.append(
                    f"⚡ Elektr xisoboti: {t.get('km', 0)} km → "
                    f"{t.get('kwh', 0)} kVt·soat "
                    f"({db_elec.get('kwh_per_km', 0)} kVt/km), "
                    f"narx {db_elec.get('rate', 0)} so'm/kVt, "
                    f"jami {t.get('total', 0)} so'm.")
            db_veh = ana.get("db_vehicle")
            if isinstance(db_veh, dict):
                v = db_veh.get("vehicle", {})
                lines.append(
                    f"🚌 Avtobus {v.get('plate_number', '')}: "
                    f"model {v.get('model', '')}, garaj №{v.get('garage_number', '')}, "
                    f"kunlik yozuv {len(db_veh.get('route_daily', []))}, "
                    f"reyslar {db_veh.get('trips_count', 0)}.")

        if not lines:
            lines.append("Ma'lumotlar bazasida topilmadi yoki sanada ma'lumot yo'q.")

        if sources:
            lines.append("\nManbalar:")
            for agent, src in sources.items():
                lines.append(f"  • {agent}: {src}")

        return "\n".join(lines)
