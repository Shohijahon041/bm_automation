"""Reviewer Agent — natijalarni tekshiradi."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .agents import BaseAgent
from .models import (
    AgentContext, AgentResult, AgentType,
    ReviewResult,
)
from .json_utils import extract_json_object
from .prompts.reviewer import REVIEWER_SYSTEM, REVIEWER_VERIFY
from ..utils.logger import get_logger

log = get_logger("myai.reviewer")

# Reviewer LLM tekshiruvi uchun byudjet. Bridge 120s kutadi; tekshiruv
# osilib qolsa local invariantlarga o'tamiz (topshiriq "running" bo'lib
# qolmasligi uchun).
REVIEW_TIMEOUT = 25.0


class ReviewerAgent(BaseAgent):
    """Reviewer Agent — boshqa agentlarning natijasini tekshiradi.

    Tekshirish turlari:
    1. Matematik hisob-to'g'ri
    2. Ma'lumotlar to'liqligi
    3. Format to'g'ri
    4. Mantiqiy xatoliklar

    Agar natija noto'g'ri bo'lsa, tegishli agent qayta ishlasin.
    Maksimum retry: config.max_retries (default 3)

    O'zini o'zi rivojlantiradi: tasdiqlangan xato topilsa (taxmin emas,
    aniq ildiz sabab bilan) — ombor `vault` tool'iga yozib qo'yiladi,
    keyingi safar xuddi shu turdagi vazifa yana xatoga yo'l qo'ymaydi.
    """

    name = AgentType.REVIEWER
    description = "Natijalarni tekshiradi va tasdiqlaydi"

    async def run(self, context: AgentContext) -> AgentResult:
        """Agent natijasini tekshirish."""
        self._start()
        try:
            input_data = context.params.get("input_data", {})
            output_data = context.params.get("output_data", {})
            agent_name = context.params.get("agent_name", "unknown")

            review = await self.verify(agent_name, input_data, output_data,
                                       knowledge=self.knowledge(context))
            self._finish(
                review.approved,
                f"{'Tasdiqlandi' if review.approved else 'Rad etildi'}: "
                f"{len(review.errors)} xato",
            )
            return AgentResult(
                success=True,
                data={
                    "approved": review.approved,
                    "errors": review.errors,
                    "corrections": review.corrections,
                    "confidence": review.confidence,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def verify(self, agent_name: str, input_data: dict,
                     output_data: dict, knowledge: str = "") -> ReviewResult:
        """Natijani tekshirish (bilim bazasidagi qoidalarga tayanib)."""
        # Strukturali natijalar uchun LLM shart emas — local tekshiruv yetarli
        # (model sekin javob beradi va ba'zan noto'g'ri rad etadi).
        if _is_structured_output(output_data):
            return self._verify_local(output_data)

        # Avval LLM bilan tekshirish (cheklangan vaqt — osilib qolmasin)
        try:
            review = await asyncio.wait_for(
                self._verify_with_llm(agent_name, input_data, output_data,
                                      knowledge=knowledge),
                timeout=REVIEW_TIMEOUT,
            )
            if review:
                return review
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM tekshiruvi ishlamadi: %s — local tekshiruv", exc)

        # Fallback: local qoidalar
        review = self._verify_local(output_data)
        await self._learn_from(review, agent_name, output_data)
        return review

    async def _learn_from(self, review: ReviewResult, agent_name: str,
                          output_data: dict) -> None:
        """Tasdiqlangan xatoni vault'ga yozib qo'yish (o'zini o'zi oshiradi).

        Faqat: rad etilgan + aniq errors bor + vault tool'ga ruxsat bor
        (vault boshqa agentlar tomonidan o'qiladi). Taxmin emas — aniq
        ildiz belgilari (`agent_name`, `errors`).
        """
        if review.approved or not review.errors:
            return
        try:
            self._check_tool_allowed("vault")
        except PermissionError:
            return
        if self.tools is None:
            return
        try:
            tool = self.tools.get("vault")
            if tool is None:
                return
            title = f"{agent_name} — aniqlangan xato"
            content = (
                f"## Aniqlangan xato ({agent_name})\n\n"
                f"Tasdiqlangan ildiz sabab quyidagi xatoliklar bilan "
                f"topildi (taxmin emas):\n\n"
                + "\n".join(f"- {e}" for e in review.errors[:5])
                + "\n\nTuzatishlar:\n"
                + "\n".join(f"- {c}" for c in review.corrections[:5] or ["—"])
                + "\n\n> Avtomatik yozib qo'yildi — keyingi safar shu "
                  "turdagi vazifada `vault` qidirib, qayta xato qilmang."
            )
            result = await tool.execute(action="write",
                                        title=title, category="Xatolar",
                                        content=content)
            if result.get("ok"):
                log.info("Reviewer vault'ga yozdi: %s", result.get("path"))
        except Exception as exc:  # noqa: BLE001
            log.debug("Vault'ga yozish amalga oshmadi: %s", exc)

    async def _verify_with_llm(self, agent_name: str, input_data: dict,
                               output_data: dict,
                               knowledge: str = "") -> ReviewResult | None:
        """LLM yordamida tekshirish (bilim bazasi qoidalari bilan)."""
        system = REVIEWER_VERIFY.format(
            agent_name=agent_name,
            input_data=json.dumps(input_data, ensure_ascii=False, indent=2)[:2000],
            output_data=json.dumps(output_data, ensure_ascii=False, indent=2)[:2000],
        )
        if knowledge:
            system = f"{knowledge}\n\n{system}"
        response = await self._call_llm(
            system,
            "Natijani tekshiring",
            max_tokens=700,
        )

        data = extract_json_object(response)
        if not isinstance(data, dict):
            return None

        approved = data.get("approved", True)
        if isinstance(approved, str):
            approved = approved.strip().lower() in ("true", "1", "ha", "yes")
        else:
            approved = bool(approved)
        try:
            confidence = float(data.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8

        return ReviewResult(
            approved=approved,
            errors=_as_text_list(data.get("errors")),
            corrections=_as_text_list(data.get("corrections")),
            confidence=confidence,
        )

    def _verify_local(self, output_data: dict) -> ReviewResult:
        """Local qoidalar bo'yicha tekshirish."""
        return verify_invariants(output_data)


def _is_simple_list_output(output_data: dict) -> bool:
    """Natija faqat ro'yxat ma'lumoti bo'lsa True (LLM tekshiruvi ortiqcha)."""
    if not isinstance(output_data, dict) or not output_data:
        return False
    # transport/analytics — ro'yxatni normalizatsiya qilgan qadamlar.
    if set(output_data) - {"browser", "transport", "analytics"}:
        return False
    br = output_data.get("browser")
    inner = br.get("data") if isinstance(br, dict) else None
    if not isinstance(inner, dict):
        return False
    list_keys = ("routes", "vehicles", "documents", "staff",
                 "waybills", "sms")
    if not any(k in inner for k in list_keys):
        return False
    return not (inner.get("summary") or inner.get("daily")
                or inner.get("totals"))


def _is_structured_output(output_data: dict) -> bool:
    """Natijada aniq struktura bor — LLM tekshiruvisiz local yetarli."""
    if not isinstance(output_data, dict) or not output_data:
        return False
    # Yuborish/Excel qadamlarida LLM tekshiruvi qoladi.
    if set(output_data) - {"browser", "route", "transport",
                           "analytics", "report", "driver", "attendance"}:
        return False
    if _is_simple_list_output(output_data):
        return True
    route_step = output_data.get("route")
    if isinstance(route_step, dict) and (
            "data" in route_step or route_step.get("no_data")):
        return True
    ana = output_data.get("analytics")
    if isinstance(ana, dict) and isinstance(ana.get("db_daily"), dict):
        return True
    # Davomat/haydovchi hisoboti — tayyor struktura, LLM tekshiruvi shart emas.
    for key in ("attendance", "driver"):
        step = output_data.get(key)
        if isinstance(step, dict) and (step.get("data") or step.get("no_data")):
            return True
    if isinstance(ana, dict) and (
            isinstance(ana.get("attendance_data"), dict)
            or isinstance(ana.get("driver_data"), dict)):
        return True
    return False


def _as_text_list(value: Any) -> list[str]:
    """LLM `errors`/`corrections` maydonini matn ro'yxatiga keltirish.

    Model ba'zan obyekt ro'yxati qaytaradi; Pydantic `list[str]` bunday
    qiymatni rad etib, tekshiruvni butunlay yiqitardi.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        value = [value]
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = ", ".join(
                f"{k}: {v}" for k, v in item.items()
                if v not in (None, "", [], {})
            ) or str(item)
        else:
            text = str(item)
        if text.strip():
            out.append(text.strip())
    return out


def verify_invariants(output_data: dict) -> ReviewResult:
    """Local qoidalar bo'yicha tekshirish (modul darajasi).

    Barcha deterministik agentlar tool natijasini shu funksiya bilan
    tekshiradi — ''inson kabi tekshirish'' ning kod qismi. ReviewerAgent
    ham, DriverAgent/ReportAgent/AnalyticsAgent ham aynan shu tekshiruvdan
    o'tadi (yagona manba).
    """
    errors = []
    corrections = []

    # 1. Bo'sh natija tekshirish
    if not output_data:
        errors.append("Natija bo'sh")
        return ReviewResult(
            approved=False,
            errors=errors,
            corrections=["Ma'lumotlar to'ldirilishi kerak"],
            confidence=0.5,
        )

    # 2. Xatolik mavjudligini tekshirish
    if isinstance(output_data, dict):
        for key, val in output_data.items():
            if isinstance(val, dict) and val.get("error"):
                errors.append(f"{key}: {val['error']}")

    # 3. Matematik tekshirish (agar data mavjud bo'lsa)
    if isinstance(output_data, dict):
        total = output_data.get("total_trips", 0)
        active = output_data.get("accepted", 0)
        rate = output_data.get("completion_rate", 0)

        if total > 0 and active > 0:
            expected_rate = round((active / total) * 100, 1)
            if rate and abs(rate - expected_rate) > 1:
                errors.append(
                    f"Noto'g'ri bajarilish foizi: {rate}% (to'g'ri: {expected_rate}%)"
                )
                corrections.append(f"completion_rate = {expected_rate}%")

        # 4. Invariant: QATNOV ≠ AVTOBUS, jami = qabul + qabul qilinmagan
        not_accepted = output_data.get("not_accepted")
        if not_accepted is not None and total > 0:
            if active + not_accepted != total:
                errors.append(
                    f"Invariant buzildi: accepted ({active}) + "
                    f"not_accepted ({not_accepted}) != total_trips ({total})"
                )
                corrections.append(
                    f"not_accepted = {total - active}")

        # 5. Attendance invariant: present + absent == total_drivers
        total_drivers = output_data.get("total_drivers")
        present = output_data.get("present")
        absent = output_data.get("absent")
        att_rate = output_data.get("attendance_rate")
        if total_drivers:
            if present is not None and absent is not None:
                if present + absent != total_drivers:
                    errors.append(
                        f"Invariant buzildi: present ({present}) + "
                        f"absent ({absent}) != total_drivers ({total_drivers})"
                    )
                    corrections.append(f"absent = {total_drivers - present}")
                expected_att = round((present / total_drivers) * 100, 1)
                if att_rate is not None and abs(att_rate - expected_att) > 1:
                    errors.append(
                        f"Noto'g'ri davomat foizi: {att_rate}% "
                        f"(to'g'ri: {expected_att}%)"
                    )
                    corrections.append(f"attendance_rate = {expected_att}%")

    approved = len(errors) == 0
    return ReviewResult(
        approved=approved,
        errors=errors,
        corrections=corrections,
        confidence=0.8 if approved else 0.3,
    )
