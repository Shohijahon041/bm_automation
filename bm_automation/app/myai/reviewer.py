"""Reviewer Agent — natijalarni tekshiradi."""

from __future__ import annotations

import json
from typing import Any

from .agents import BaseAgent
from .models import (
    AgentContext, AgentResult, AgentType,
    ReviewResult,
)
from .prompts.reviewer import REVIEWER_SYSTEM, REVIEWER_VERIFY
from ..utils.logger import get_logger

log = get_logger("myai.reviewer")


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

            review = await self.verify(agent_name, input_data, output_data)
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
                     output_data: dict) -> ReviewResult:
        """Natijani tekshirish."""
        # Avval LLM bilan tekshirish
        try:
            review = await self._verify_with_llm(agent_name, input_data, output_data)
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
                               output_data: dict) -> ReviewResult | None:
        """LLM yordamida tekshirish."""
        response = await self._call_llm(
            REVIEWER_VERIFY.format(
                agent_name=agent_name,
                input_data=json.dumps(input_data, ensure_ascii=False, indent=2)[:2000],
                output_data=json.dumps(output_data, ensure_ascii=False, indent=2)[:2000],
            ),
            "Natijani tekshiring",
            max_tokens=500,
        )

        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(response)
        return ReviewResult(
            approved=data.get("approved", True),
            errors=data.get("errors", []),
            corrections=data.get("corrections", []),
            confidence=data.get("confidence", 1.0),
        )

    def _verify_local(self, output_data: dict) -> ReviewResult:
        """Local qoidalar bo'yicha tekshirish."""
        return verify_invariants(output_data)


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
