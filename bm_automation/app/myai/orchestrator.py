"""Orchestrator — agent pipeline ni boshqaradi."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .config import get_myai_config
from .models import (
    AgentContext, AgentResult, AgentType, TaskStatus,
)
from .providers import LLMProvider
from .providers.factory import get_provider
from .tools import get_tool_registry
from .agents import BaseAgent
from . import state
from ..utils.logger import get_logger

log = get_logger("myai.orchestrator")

# Tool nomi → inson o'qiydigan manba tavsifi (shaffoflik uchun).
TOOL_SOURCE_LABELS = {
    "db": "PostgreSQL (ma'lumotlar bazasi)",
    "dtransport": "BM API (bm.dtransport.uz)",
    "browser": "Playwright brauzer",
    "telegram": "Telegram messenjer",
    "excel": "Excel fayl",
    "report": "Hisobot generatori",
}


def _source_label_for(agent: BaseAgent) -> str:
    """Agent oxirgi chaqirgan tool'idan manba tavsifini chiqaradi."""
    note = getattr(agent, "source_note", "")
    if note:
        return note
    last = getattr(agent, "last_tool", None)
    if last and last[0] in TOOL_SOURCE_LABELS:
        if last[0] == "db" and last[1]:
            return f"PostgreSQL (ma'lumotlar bazasi) — {last[1]}"
        return TOOL_SOURCE_LABELS[last[0]]
    return "Tizim ichki manbasi"


class Orchestrator:
    """MyAI agent pipeline orchestrator.

    Barcha agentlarni yaratadi, boglaydi va boshqaradi.
    Task state ni PostgreSQL da saqlaydi.
    """

    def __init__(self, llm: LLMProvider | None = None):
        self._config = get_myai_config()
        self._llm = llm
        self._tools = None
        self._agents: dict[AgentType, BaseAgent] = {}
        self._master = None
        self._planner = None
        self._reviewer = None
        self._initialized = False
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._pause_events: dict[str, asyncio.Event] = {}

    def _ensure_init(self) -> None:
        """Lazy initialization — faqat birinchi marta ishga tushirilganda."""
        if self._initialized:
            return
        self._initialized = True
        if self._llm is None:
            self._llm = get_provider()
        self._tools = get_tool_registry()
        self._init_agents()

    def _init_agents(self) -> None:
        """Barcha agentlarni yaratish va bog'lash."""
        from .master import MasterAgent
        from .planner import PlannerAgent
        from .reviewer import ReviewerAgent

        master = MasterAgent(self._llm, self._tools)
        planner = PlannerAgent(self._llm, self._tools)
        reviewer = ReviewerAgent(self._llm, self._tools)

        master.set_planner(planner)
        master.set_reviewer(reviewer)

        self._register(master)
        self._register(planner)
        self._register(reviewer)

        self._planner = planner
        self._reviewer = reviewer

        # Domain agents
        from .browser_agent import BrowserAgent
        from .transport_agent import TransportAgent
        from .analytics_agent import AnalyticsAgent
        from .driver_agent import DriverAgent
        from .route_agent import RouteAgent
        from .schedule_agent import ScheduleAgent
        from .attendance_agent import AttendanceAgent
        from .excel_agent import ExcelAgent
        from .report_agent import ReportAgent
        from .telegram_agent import TelegramAgent
        from .security import SecurityAgent

        for cls in [BrowserAgent, TransportAgent, AnalyticsAgent,
                    DriverAgent, RouteAgent, ScheduleAgent,
                    AttendanceAgent, ExcelAgent, ReportAgent,
                    TelegramAgent, SecurityAgent]:
            agent = cls(self._llm, self._tools)
            self._register(agent)
            master.register_agent(agent)

        self._master = master
        log.info("MyAI orchestrator tayyor — %d agent", len(self._agents))

    def _register(self, agent: BaseAgent) -> None:
        self._agents[agent.name] = agent

    def _get_agent(self, agent_type: AgentType) -> BaseAgent | None:
        """Agent ni olish."""
        return self._agents.get(agent_type)

    async def _build_response(self, context: AgentContext, results: dict,
                              sources: dict[str, str] | None = None) -> str:
        """Yakuniy javobni yaratish — masterni delegate qiladi."""
        return await self._master._build_response(context, results,
                                                  sources=sources)

    @staticmethod
    def _permission_for_intent(intent: str) -> str | None:
        """Intent uchun talab qilinadigan ruxsat (yo'q bo'lsa None = ochiq)."""
        perms = {
            "salary": "read_salary",
            "monthly_report": "read_salary",
            "monthly_driver": "read_salary",
            "settings": "read_settings",
            "addcompany": "write_settings",
            "resend": "sync_data",
            "sync": "sync_data",
            "telegram": "send_telegram",
            "export": "generate_report",
            "report": "generate_report",
            "report_excel": "generate_report",
            "schedule": "read_attendance",
            "attendance": "read_attendance",
        }
        return perms.get(intent)

    async def _security_gate(self, task_id, state, emit, intent,
                             params) -> dict | None:
        """Foydalanuvchi roli bo'yicha ruxsatni tekshiradi.

        Ruxsat bo'lsa None, yo'q bo'lsa rad javobini qaytaradi.
        Rol `params["chat_id"]` orqali Telegram bot roli modulidan
        aniqlanadi (admin/dispatcher/manager/viewer). Agar chat_id
        bo'lmasa — ochiq (tashqi dashboard) hisoblanadi.
        """
        intent_type = (intent or {}).get("intent", "general")
        perm = self._permission_for_intent(intent_type)
        if not perm:
            return None

        role = "viewer"
        try:
            chat_id = str(params.get("chat_id") or "").strip()
            if params.get("role"):
                role = str(params["role"]).lower()
            elif chat_id.isdigit():
                from ..notifications.ops.roles import resolve_role as _resolve_role
                role = _resolve_role(int(chat_id)).value.lower()
        except Exception as exc:  # noqa: BLE001
            log.warning("Security rollni aniqlab bo'lmadi: %s", exc)
            role = "viewer"

        from .security import ROLE_PERMISSIONS, Permission as _Perm
        allowed = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["viewer"])
        req = _Perm(perm)
        ok = req in allowed or _Perm.ADMIN in allowed

        if not ok:
            msg = (f"⛔ <b>Ruxsat yo'q.</b> Sizning rolingiz "
                   f"<b>{role.upper()}</b> — bu amal (<code>{perm}</code>) "
                   f"uchun huquq yetarli emas. Batafsil: /myrole")
            state.update_task(task_id, status="failed", error=msg)
            state.add_log(task_id, "security", f"Ruxsat rad etildi: {perm} "
                                               f"(rol={role})", level="warn")
            emit_task("task.failed", task_id=task_id, message=msg)
            emit("security", "error", task_id=task_id, message=msg)
            log.warning("Task %s security rad etdi (rol=%s, perm=%s)",
                        task_id, role, perm)
            return {"ok": False, "task_id": task_id, "denied": True,
                    "result": {"response": msg, "denied": True}}

        emit("security", "completed", task_id=task_id,
             message=f"Ruxsat tasdiqlandi ({role})")
        return None

    async def run_task(self, task_id: str) -> dict:
        """Mavjud task ni bajarish — task_id bo'yicha.

        Server allaqachon task yaratgan va task_id ni uzatgan.
        Orchestrator faqat bajaradi.
        """
        self._ensure_init()
        from .events import emit, emit_task, emit_message, emit_walking, emit_waiting

        # Mavjud task ni olish
        task_row = state.get_task(task_id)
        if not task_row:
            return {"ok": False, "error": f"Task topilmadi: {task_id}"}

        user_request = task_row.get("user_request", "")
        route = ""
        date = ""
        params = {}
        try:
            result_data = task_row.get("result")
            if isinstance(result_data, str):
                import json
                result_data = json.loads(result_data)
            if isinstance(result_data, dict):
                route = result_data.get("route", "")
                date = result_data.get("date", "")
                params = {k: v for k, v in result_data.items() if k not in ("route", "date")}
        except Exception:
            pass

        try:
            state.update_task(task_id, status="running")
            state.add_log(task_id, "master", f"Topshiriq boshlandi: {user_request}")
            emit_task("task.started", task_id=task_id,
                      message=user_request[:100])
            emit("master", "working", task_id=task_id,
                 action="Vazifani tushunmoqda", progress=5)

            context = AgentContext(
                task_id=task_id,
                user_request=user_request,
                route=route,
                date=date,
                params=params,
            )

            # 1. Intent aniqlash
            emit("master", "working", task_id=task_id,
                 action="Intent aniqlanmoqda...", progress=10)
            intent = await self._master._classify_intent(user_request)
            log.info("Intent aniqlandi: %s", intent.get("intent", "unknown"))
            emit("master", "working", task_id=task_id,
                 action=f"Intent: {intent.get('intent', 'general')}", progress=15)

            # 1b. Security pre-check — foydalanuvchi roli bo'yicha ruxsat
            denied = await self._security_gate(task_id, state, emit, intent, params)
            if denied is not None:
                return denied

            # 2. Planner dan plan olish
            if self._planner is None:
                return {"ok": False, "error": "Planner Agent o'rnatilmagan"}

            emit("planner", "working", task_id=task_id,
                 action="Reja tuzilmoqda...", progress=20)
            plan = await self._planner.create_plan(
                user_request=user_request,
                intent=intent,
                params=params,
            )
            step_count = len(plan.steps)
            log.info("Plan yaratildi: %d qadam", step_count)
            emit("planner", "completed", task_id=task_id,
                 action=f"{step_count} qadam yaratildi", progress=25)
            state.add_log(task_id, "planner",
                          f"Reja: {step_count} qadam — " +
                          ", ".join(s.agent.value for s in plan.steps))

            # 3. Har bir step ni bajarish
            results = {}
            sources: dict[str, str] = {}
            self.create_cancellation_token(task_id)
            for i, step in enumerate(plan.steps):
                if self.is_cancelled(task_id):
                    state.update_task(task_id, status="cancelled")
                    emit_task("task.cancelled", task_id=task_id,
                              message="Task bekor qilindi")
                    self._cleanup_token(task_id)
                    return {"ok": False, "task_id": task_id,
                            "error": "cancelled"}

                # Pause kutish — pause bosilgan bo'lsa, davom etguncha kutadi
                await self._wait_if_paused(task_id, emit, state)

                agent = self._get_agent(step.agent)
                if agent is None:
                    log.warning("Agent topilmadi: %s", step.agent)
                    continue

                # Emit waiting for agents that will run later
                if i == 0:
                    for later_step in plan.steps[1:]:
                        emit_waiting(later_step.agent.value, task_id=task_id,
                                     action="Kutmoqda...")

                # Emit walking from previous agent (data transfer animation)
                if i > 0:
                    prev_agent_id = plan.steps[i - 1].agent.value
                    emit_walking(step.agent.value, to_agent=prev_agent_id,
                                 task_id=task_id,
                                 action=f"{prev_agent_id} dan ma'lumot olinmoqda")
                    emit_message(
                        from_agent=prev_agent_id,
                        to_agent=step.agent.value,
                        task_id=task_id,
                        message=f"Data: {list(results.get(prev_agent_id, {}).keys())[:3]}"
                                if isinstance(results.get(prev_agent_id), dict) else "Data",
                    )
                    await asyncio.sleep(0.15)

                step_pct = 25 + int(70 * (i / max(step_count, 1)))
                emit(step.agent.value, "working", task_id=task_id,
                     action=step.action or f"Step {i+1}/{step_count}",
                     progress=step_pct)
                state.update_task(task_id, current_agent=step.agent.value,
                                  progress=step_pct)

                # Persist step to DB
                step_id = state.add_task_step(
                    task_id, step.agent.value, step.action,
                )

                # Reja qadamining action'ini agent params ga uzatish
                step_opts = {**params, **step.params}
                if step.action:
                    step_opts["action"] = step.action
                    step_opts["step_action"] = step.action

                step_context = AgentContext(
                    task_id=task_id,
                    user_request=user_request,
                    route=route,
                    date=date,
                    params=step_opts,
                    previous_results=results,
                    step_index=i,
                )

                try:
                    result = await agent.run(step_context)
                    results[step.agent.value] = result.data

                    # Manba shaffofligi: agent qaysi manbadan olganini yozamiz
                    source = result.source or _source_label_for(agent)
                    sources[step.agent.value] = source
                    state.add_log(
                        task_id, step.agent.value,
                        f"Manba: {source}",
                        metadata={"source": source},
                    )

                    # Emit step result detail
                    detail = ""
                    if isinstance(result.data, dict):
                        keys = list(result.data.keys())[:3]
                        detail = ", ".join(keys) if keys else ""
                    elif isinstance(result.data, str):
                        detail = result.data[:120]

                    emit(step.agent.value, "completed", task_id=task_id,
                         action=f"Step {i+1} tugadi: {step.action}",
                         progress=step_pct + int(70 / max(step_count, 1)),
                         message=detail)
                    state.update_task_step(
                        step_id, status="completed",
                        output_data=result.data if isinstance(result.data, dict) else {"raw": str(result.data)[:500]},
                    )
                    state.add_log(task_id, step.agent.value,
                                  f"Step {i+1}/{step_count} tugadi: {step.action}",
                                  metadata={"detail": detail} if detail else None)
                    log.info("Step %d/%d tugadi: %s",
                             i + 1, step_count, step.agent.value)
                except Exception as exc:  # noqa: BLE001
                    log.error("Step %d/%d xatosi: %s — %s",
                              i + 1, step_count, step.agent.value, exc)
                    results[step.agent.value] = {"error": str(exc)}
                    emit(step.agent.value, "error", task_id=task_id,
                         action=f"Step {i+1} xatosi: {exc}",
                         progress=step_pct)
                    state.update_task_step(
                        step_id, status="failed", error=str(exc),
                    )

            self._cleanup_token(task_id)

            # 4. Reviewer dan tasdiq
            emit("reviewer", "working", task_id=task_id,
                 action="Natijalar tekshirilmoqda...", progress=90)
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
                    emit("reviewer", "error", task_id=task_id,
                         action="Rad etildi: " + str(review.errors))
                else:
                    emit("reviewer", "completed", task_id=task_id,
                         action="Tasdiqlandi", progress=95)

            # 5. Yakuniy javob
            emit("master", "working", task_id=task_id,
                 action="Yakuniy javob tayyorlanmoqda...", progress=95)
            final_response = await self._build_response(context, results,
                                                        sources=sources)
            state.update_task(task_id, status="completed",
                              progress=100, result={
                                  "response": final_response,
                                  "steps_results": results,
                                  "review_approved": review_approved,
                                  "sources": sources,
                              })
            state.add_log(task_id, "master",
                          "Topshiriq muvaffaqiyatli tugadi")
            emit_task("task.completed", task_id=task_id,
                      message="Topshiriq tugadi")
            emit("master", "completed", task_id=task_id,
                 progress=100, message="Topshiriq tugadi")
            log.info("Topshiriq tugadi: %s", task_id)
            return {"ok": True, "task_id": task_id,
                    "result": {"response": final_response,
                               "steps_results": results,
                               "review_approved": review_approved,
                               "sources": sources}}

        except Exception as exc:  # noqa: BLE001
            self._cleanup_token(task_id)
            state.update_task(task_id, status="failed", error=str(exc))
            state.add_log(task_id, "master",
                          f"Kutilmagan xato: {exc}", level="error")
            emit_task("task.failed", task_id=task_id, message=str(exc))
            emit("master", "error", task_id=task_id, message=str(exc))
            log.exception("Topshiriq xatosi: %s", task_id)
            return {"ok": False, "task_id": task_id, "error": str(exc)}

    def get_status(self) -> dict:
        self._ensure_init()
        try:
            stats = state.get_stats()
        except Exception:
            stats = {"total": 0, "active": 0, "completed": 0, "failed": 0}
        agent_statuses = []
        for agent_type, agent in self._agents.items():
            s = agent.status
            agent_statuses.append({
                "agent": s.agent.value,
                "status": s.status.value,
                "message": s.message,
            })
        return {
            "ok": True,
            "configured": self._llm.configured() if self._llm else False,
            "provider": self._llm.name if self._llm else "none",
            "model": self._config.llm_model,
            "agents": agent_statuses,
            "active_tasks": stats["active"],
            "total_tasks": stats["total"],
            "completed_tasks": stats["completed"],
            "failed_tasks": stats["failed"],
        }

    def create_cancellation_token(self, task_id: str) -> asyncio.Event:
        """Task uchun bekor qilish tokeni yaratish."""
        event = asyncio.Event()
        self._cancel_events[task_id] = event
        return event

    def cancel_task(self, task_id: str) -> bool:
        """Task ni bekor qilish."""
        event = self._cancel_events.get(task_id)
        if event:
            event.set()
            return True
        return False

    def is_cancelled(self, task_id: str) -> bool:
        """Task bekor qilinganmi?"""
        event = self._cancel_events.get(task_id)
        return event is not None and event.is_set()

    def _cleanup_token(self, task_id: str) -> None:
        """Bekor qilish tokenini tozalash."""
        self._cancel_events.pop(task_id, None)
        self._pause_events.pop(task_id, None)

    def pause_task(self, task_id: str) -> bool:
        """Task ni pauza qilish.

        Faqat bajarilayotgan task'larni pauza qilish mumkin (cancel
        token mavjud bo'lsa). Pauza — _pause_events dict'da flag;
        resume uni o'chiradi. asyncio.Event ishlatilmaydi, chunki
        pauza boshqa thread'dan (server handler) bosiladi va
        cross-thread Event ishonchsiz.
        """
        if task_id not in self._cancel_events:
            return False  # task bajarilmayapti
        if task_id in self._pause_events:
            return False  # allaqachon pauzada
        self._pause_events[task_id] = True
        return True

    def resume_task(self, task_id: str) -> bool:
        """Task ni davom ettirish."""
        if task_id not in self._pause_events:
            return False
        del self._pause_events[task_id]
        return True

    def is_paused(self, task_id: str) -> bool:
        """Task pauzadami?"""
        return task_id in self._pause_events

    async def _wait_if_paused(self, task_id: str, emit, state) -> None:
        """Task pauzadagi bo'lsa, davom etguncha kutadi.

        asyncio.Event o'rniga polling — pauza boshqa thread'dan
        bosiladi va cross-thread'da Event.wait() ishonchli emas.
        Har 0.25 s da is_paused() ni tekshirib turamiz (thread-safe).
        """
        if not self.is_paused(task_id):
            return
        emit("master", "waiting", task_id=task_id,
             action="Pauza — davom etish kutilmoqda")
        state.add_log(task_id, "master", "Task pauza qilindi")
        while self.is_paused(task_id):
            await asyncio.sleep(0.25)
        emit("master", "working", task_id=task_id,
             action="Task davom etmoqda")
        state.add_log(task_id, "master", "Task davom etdi")


# Singleton
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator
