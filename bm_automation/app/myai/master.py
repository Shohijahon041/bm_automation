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
        if not params.get("month"):
            month = MasterAgent._extract_month(lower)
            if month:
                params["month"] = month

        # Telegram'ga yuborish so'rovi bormi? — flag sifatida saqlanadi.
        MasterAgent._apply_send_flag(params, lower)

        daily_words = ("hisobot", "hisobotini", "xulosa", "umumiy",
                       "barcha yo'nalish", "kunlik", "hozirgi holat",
                       "kundalik", "qancha avtobus chiqqan")

        # Avval aniq intentlar tekshiriladi (daily umumiy so'zlaridan oldin)
        if any(w in lower for w in ("attendance", "ishga chiqish", "qatnashish",
                                    "davomati", "keldimi", "davomat", "qatnashdi")):
            return {"intent": "attendance", "params": params}
        if any(w in lower for w in ("jadval", "smеna", "grafik", "schedule",
                                    "smеna jadvali", "smеnasi")):
            return {"intent": "schedule", "params": params}
        # Oylik so'rov (maosh/daily so'zlaridan oldin — 'oylik maosh'/'oylik
        # hisobot' kabi iboralar oylik hisobga tushadi).
        if params.get("month") or any(w in lower for w in _MONTH_KEYWORDS):
            if params.get("driver") and not params.get("send_telegram"):
                return {"intent": "monthly_driver", "params": params}
            return {"intent": "monthly_report", "params": params}
        if any(w in lower for w in ("maosh", "salary", "oklad", "ish haqi",
                                    "hisobla")):
            return {"intent": "salary", "params": params}
        if any(w in lower for w in ("muammo", "problems", "nosoz", "kechik")):
            return {"intent": "problems", "params": params}
        # Excel / fayl ko'rinishidagi hisobot so'rovi
        if any(w in lower for w in ("excel", "xlsx", "fayl qilib", "jadval qilib",
                                    "yuklab ol", "fil yukla", ".xlsx")):
            return {"intent": "report_excel", "params": params}
        if any(w in lower for w in daily_words):
            return {"intent": "daily_summary", "params": params}
        if driver_hint and ("drivers" in lower or params.get("driver")
                            or "haydovchi" in lower or "shofyor" in lower):
            return {"intent": "check_driver", "params": params}
        if params.get("route"):
            if "avtobus" in lower or "mashina" in lower or "transport" in lower:
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
        elif ana.get("total_trips") and not attendance_data:
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

        # 4. Umumiy / boshqa
        if not lines:
            for agent_name, data in results.items():
                if isinstance(data, dict) and not data.get("error"):
                    keys = list(data.keys())[:4]
                    lines.append(f"• {agent_name}: " + ", ".join(keys))
                elif isinstance(data, dict) and data.get("error"):
                    lines.append(f"• {agent_name}: xato — {data['error']}")

        # 5. Rich DB ma'lumotlari — analytics tomonidan olib o'tilgan
        if isinstance(ana, dict):
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
            db_problems = ana.get("db_problems")
            if isinstance(db_problems, dict):
                p = db_problems.get("problems", {})
                lines.append(
                    f"⚠️ Muammolar: GPS {p.get('gps', 0)}, "
                    f"texnik {p.get('technical', 0)}, "
                    f"jadval {p.get('schedule', 0)} — "
                    f"jami {p.get('total', 0)}.")
            db_elec = ana.get("db_electricity")
            if isinstance(db_elec, dict):
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
