"""Planner Agent — topshiriqni qadamlarga bo'ladi."""

from __future__ import annotations

import json
from typing import Any

from .agents import BaseAgent
from .models import (
    AgentContext, AgentResult, AgentType,
    Plan, PlanStep,
)
from .prompts.planner import PLANNER_SYSTEM, PLANNER_CREATE
from ..utils.logger import get_logger

log = get_logger("myai.planner")


class PlannerAgent(BaseAgent):
    """Planner Agent — katta topshiriqni kichik tasklarga bo'ladi.

    Masalan:
    "B-80 marshrutini tekshir" →
    [
        {"agent": "browser", "action": "get_route_data"},
        {"agent": "transport", "action": "normalize"},
        {"agent": "analytics", "action": "calculate"},
        {"agent": "reviewer", "action": "verify"}
    ]
    """

    name = AgentType.PLANNER
    description = "Topshiriqni qadamlarga bo'ladi"

    # Default plan templates — LLM ishlamaganida
    TEMPLATES: dict[str, list[dict]] = {
        "check_route": [
            {"agent": "route", "action": ""},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "check_driver": [
            {"agent": "driver", "action": "full"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "check_vehicle": [
            {"agent": "browser", "action": "get_vehicle_detail"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "attendance": [
            {"agent": "attendance", "action": "get_attendance"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "schedule": [
            {"agent": "schedule", "action": "get_schedules"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "daily_summary": [
            {"agent": "browser", "action": "get_daily_summary"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
            {"agent": "report", "action": "daily_summary"},
        ],
        "monthly_report": [
            {"agent": "driver", "action": "full"},
        ],
        "monthly_driver": [
            {"agent": "driver", "action": "full"},
        ],
        "salary": [
            {"agent": "driver", "action": "full"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "problems": [
            {"agent": "browser", "action": "get_problems"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "verify": [
            {"agent": "browser", "action": "get_all_routes_summary"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
        "telegram": [
            {"agent": "browser", "action": "get_daily_summary"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
            {"agent": "report", "action": "daily_summary"},
            {"agent": "telegram", "action": "send"},
        ],
        "report_excel": [
            {"agent": "browser", "action": "get_daily_summary"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
            {"agent": "excel", "action": "create_report"},
            {"agent": "report", "action": "daily_summary"},
        ],
        "general": [
            {"agent": "browser", "action": "get_daily_summary"},
            {"agent": "transport", "action": "normalize"},
            {"agent": "analytics", "action": "calculate"},
        ],
    }

    INTROSPECT = {
        "check_route": "Yo'nalish bo'yicha ma'lumot",
        "check_driver": "Haydovchi bo'yicha ma'lumot",
        "check_vehicle": "Avtobus bo'yicha ma'lumot",
        "attendance": "Ishga chiqish",
        "schedule": "Jadval",
        "daily_summary": "Kunlik hisobot",
        "monthly_report": "Oylik hisobot",
        "monthly_driver": "Oylik haydovchi hisoboti",
        "salary": "Maosh / ish haqi",
        "problems": "Muammolar tahlili",
        "verify": "Sayt-baza tekshirish",
        "telegram": "Telegram'ga xabar yuborish",
        "report_excel": "Excel hisobot yaratish",
        "general": "Umumiy ma'lumot",
    }

    async def run(self, context: AgentContext) -> AgentResult:
        """Plan yaratish."""
        self._start()
        try:
            plan = await self.create_plan(
                user_request=context.user_request,
                intent=context.params.get("intent", {}),
                params=context.params,
            )
            self._finish(True, f"{len(plan.steps)} qadam yaratildi")
            return AgentResult(
                success=True,
                data={"task_type": plan.task_type, "steps": [
                    {"agent": s.agent.value, "action": s.action,
                     "params": s.params}
                    for s in plan.steps
                ]},
            )
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def create_plan(self, user_request: str, intent: dict | None = None,
                          params: dict | None = None) -> Plan:
        """Execution plan yaratish."""
        intent_type = (intent or {}).get("intent", "general")
        extra_params = (intent or {}).get("params", {})
        merged = {**(params or {}), **extra_params}

        # Maosh/oylik so'rovlarida oy ko'rsatilmagan bo'lsa — joriy oy.
        # Driver agent oylik yo'lini faqat `month` mavjud bo'lganda ishga
        # tushiradi, shuning uchun bu yerda default beramiz.
        if intent_type in ("salary", "monthly_report", "monthly_driver"):
            if not str(merged.get("month") or "").strip():
                from datetime import date as _date
                merged["month"] = _date.today().strftime("%Y-%m")

        # Avval LLM bilan urinish
        try:
            plan = await self._plan_with_llm(user_request, intent_type, merged)
            if plan and plan.steps:
                self._apply_params_to_steps(plan, merged)
                self._ensure_intent_steps(plan, intent_type, merged)
                self._ensure_delivery(plan, intent_type, merged)
                return plan
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM plan yarata olmadi: %s — template ishlatiladi", exc)

        # Fallback: template
        plan = self._plan_from_template(intent_type, merged)
        self._ensure_intent_steps(plan, intent_type, merged)
        self._ensure_delivery(plan, intent_type, merged)
        return plan

    def _apply_params_to_steps(self, plan: Plan, params: dict) -> None:
        """Har bir step'ga route/driver parametrlarini qo'shish."""
        for step in plan.steps:
            sp = dict(step.params or {})
            if params.get("route"):
                sp["route_id"] = params["route"]
            if params.get("route_id"):
                sp["route_id"] = params["route_id"]
            if params.get("driver"):
                sp["driver_id"] = params["driver"]
                sp["driver"] = params["driver"]
                sp["query"] = params["driver"]
            step.params = sp

    def _ensure_intent_steps(self, plan: Plan, intent_type: str,
                             params: dict) -> None:
        """Intent'ga mos agent majburiy bo'lsa qo'shadi (insonga o'xshash fikrlash).

        Masalan "XALILOV haydovchini tekshir" deyilsa, LLM plan boshqa
        agentlar bilan to'lgan bo'lsa ham driver agent birinchi step bo'ladi.
        """
        used = {s.agent for s in plan.steps}
        driver_name = params.get("driver") or params.get("route_id") or ""
        intent_agents: dict[str, AgentType] = {
            "check_driver": AgentType.DRIVER,
            "monthly_driver": AgentType.DRIVER,
            "monthly_report": AgentType.DRIVER,
            "attendance": AgentType.ATTENDANCE,
            "schedule": AgentType.SCHEDULE,
            "check_route": AgentType.ROUTE,
            "check_vehicle": AgentType.BROWSER,
        }
        required = intent_agents.get(intent_type)
        if required and required not in used:
            extra: dict[str, Any] = {}
            if required == AgentType.DRIVER:
                extra = {"driver": driver_name or params.get("query", ""),
                         "query": driver_name or params.get("query", "")}
            elif required == AgentType.BROWSER:
                extra = {"route_id": params.get("route") or params.get("route_id", "")}
            log.info("Intent %s uchun %s step qo'shildi",
                     intent_type, required.value)
            plan.steps.insert(0, PlanStep(agent=required, action="", params=extra))

    def _ensure_delivery(self, plan: Plan, intent_type: str,
                         params: dict) -> None:
        """Send-so'rov bo'lsa, plan oxiriga Telegram step qo'shadi.

        Foydalanuvchi "yubor"/"telegramga yubor" desa, tayyor natija
        (report / excel / analytics) Telegram agent orqali chat'ga
        yetkaziladi. chat_id params orqali (bot) yoki default chat.
        """
        if not params.get("send_telegram"):
            return
        used = {s.agent for s in plan.steps}
        if AgentType.TELEGRAM in used:
            return
        has_content = any(a in used for a in (
            AgentType.ANALYTICS, AgentType.REPORT, AgentType.EXCEL,
            AgentType.DRIVER,
        ))
        if not has_content:
            log.info("send_telegram=True, lekin yuboriladigan kontent yo'q")
            return
        chat_id = str(params.get("chat_id") or "").strip()
        log.info("Telegram yetkazish step qo'shildi (chat_id=%s)",
                 chat_id or "default")
        plan.steps.append(PlanStep(
            agent=AgentType.TELEGRAM, action="send",
            params={"chat_id": chat_id, "send_telegram": True},
        ))

    async def _plan_with_llm(self, user_request: str, intent_type: str,
                             params: dict) -> Plan | None:
        """LLM yordamida plan yaratish."""
        response = await self._call_llm(
            PLANNER_CREATE.format(
                user_request=user_request,
                intent=intent_type,
                params=json.dumps(params, ensure_ascii=False),
            ),
            user_request,
            max_tokens=1000,
        )

        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]

        data = json.loads(response)
        steps = []
        for s in data.get("steps", []):
            try:
                agent_type = AgentType(s["agent"])
            except (ValueError, KeyError):
                continue
            steps.append(PlanStep(
                agent=agent_type,
                action=s.get("action", ""),
                params=s.get("params", {}),
            ))

        return Plan(
            task_type=data.get("task_type", intent_type),
            params=data.get("params", params),
            steps=steps,
        )

    def _plan_from_template(self, intent_type: str, params: dict) -> Plan:
        """Template asosida plan yaratish (LLM fallback)."""
        template = self.TEMPLATES.get(intent_type, self.TEMPLATES["check_route"])
        steps = []
        for t in template:
            try:
                agent_type = AgentType(t["agent"])
            except ValueError:
                continue
            step_params = dict(params)
            if params.get("route"):
                step_params["route_id"] = params["route"]
            if params.get("route_id"):
                step_params["route_id"] = params["route_id"]
            if params.get("driver"):
                step_params["driver_id"] = params["driver"]
                step_params["driver"] = params["driver"]
                step_params["query"] = params["driver"]
            steps.append(PlanStep(
                agent=agent_type,
                action=t.get("action", ""),
                params=step_params,
            ))

        return Plan(
            task_type=intent_type,
            params=params,
            steps=steps,
        )
