"""Agent Event Bus — central event system for real-time agent status.

Events are stored in memory (ring buffer) and optionally persisted to DB.
Frontend connects via SSE for real-time updates.

Event payload schema (AgentEvent.to_dict()):

    event         str  — hodisa turi (masalan "agent.status")
    timestamp     float — epoch vaqt
    timestamp_iso str  — ISO formatda vaqt (to_dict tomonidan qo'shiladi)
    agent_id      str  — agent identifikatori (agent_type.value)
    status        str  — holat: thinking|planning|working|completed|
                           error|waiting|walking|idle
    action        str  — xabar/amat (ro'yxatda ko'rsatiladi)
    task_id       str  — tegishli task (agar mavjud bo'lsa)
    progress      int  — 0..100 (task umumiy progressi)
    message       str  — to'liq matn (feed/detail uchun)
    from_agent    str  — jo'natuvchi agent (agent.message/walking)
    to_agent      str  — qabul qiluvchi agent
    data          dict — qo'shimcha kontekst (masalan tool nomi)

Hodisa turlari:

    Task darajasida (emit_task):
        task.started / task.completed / task.failed /
        task.cancelled / task.paused / task.resumed
    Agent holatida (emit):
        agent.status                — agent_id + status
        agent.thinking              — LLM chaqiruvidan oldin
        agent.planning              — reja tuzish paytida
        agent.tool_call / agent.tool_result
                                    — tool ishga tushishi/tugashi
                                      (data.tool, data.success)
        agent.walking               — agentlar orasida ma'lumot uzatish
                                      (from_agent->to_agent)
        agent.waiting               — kutish (masalan pauza)
        agent.message               — agent-to-agent xabar
                                      (from_agent, to_agent, message)
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, AsyncGenerator, Generator

from ..utils.logger import get_logger

log = get_logger("myai.events")


@dataclass
class AgentEvent:
    """A single agent event."""
    event: str
    timestamp: float
    agent_id: str = ""
    status: str = ""
    action: str = ""
    task_id: str = ""
    progress: int = 0
    message: str = ""
    from_agent: str = ""
    to_agent: str = ""
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp_iso"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)
        )
        return {k: v for k, v in d.items() if v or k in ("event", "timestamp")}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventBus:
    """In-memory event bus with optional DB persistence."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls) -> EventBus:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_bus()
        return cls._instance

    def _init_bus(self) -> None:
        self._buffer: deque[AgentEvent] = deque(maxlen=500)
        self._subscribers: list[deque[AgentEvent]] = []
        self._sub_lock = threading.Lock()
        self._persist_queue: queue.Queue[AgentEvent] = queue.Queue()
        self._persist_worker_lock = threading.Lock()
        self._persist_worker_started = False

    # ── Persistence worker ──────────────────────────────────────────
    # emit() hech qachon bloklanmasligi uchun DB'ga yozish bitta
    # fon (daemon) ishchi ipi orqali navbatda amalga oshiriladi.
    # Har bir emit uchun yangi ip ochilmaydi — bu og'ir yukda
    # thread oqishi (leak) va bloklanishlardan saqlaydi.
    def _ensure_persist_worker(self) -> None:
        if self._persist_worker_started:
            return
        with self._persist_worker_lock:
            if self._persist_worker_started:
                return
            self._persist_worker_started = True
            threading.Thread(
                target=self._persist_worker, daemon=True, name="myai-events-persist"
            ).start()

    def _persist_worker(self) -> None:
        # Ishchi ip faqat faollik davrida yashaydi: navbat bo'shab,
        # bir muncha vaqt (idle_timeout) ish kelmasa o'zi yakunlanadi.
        # Bu interpreter shutdown vaqtida tirik daemon thread
        # qolishining oldini oladi (shuning uchun "Fatal Python error"
        # kabi shovqinlar chiqmaydi).
        while True:
            try:
                ev = self._persist_queue.get(timeout=0.25)
            except queue.Empty:
                with self._persist_worker_lock:
                    if self._persist_queue.empty():
                        self._persist_worker_started = False
                        break
                    continue
            try:
                self._persist_event(ev)
            except Exception:  # noqa: BLE001
                log.exception("Event persist xatosi: %s", ev.event)

    def emit(self, event: str, *, agent_id: str = "", status: str = "",
             action: str = "", task_id: str = "", progress: int = 0,
             message: str = "", from_agent: str = "", to_agent: str = "",
             data: dict | None = None) -> AgentEvent:
        """Emit an event and notify all subscribers.

        emit() sync va non-blocking — faqat xotiraga qo'shadi hamda
        obunalarda qayta tarqatadi. DB'ga yozish fon ishchi ipida
        amalga oshiriladi (best-effort).
        """
        ev = AgentEvent(
            event=event,
            timestamp=time.time(),
            agent_id=agent_id,
            status=status,
            action=action,
            task_id=task_id,
            progress=progress,
            message=message,
            from_agent=from_agent,
            to_agent=to_agent,
            data=data or {},
        )
        self._buffer.append(ev)

        # Notify SSE subscribers
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.append(ev)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Subscriber notify xatosi: %s", exc)
                    dead.append(q)
            for d in dead:
                self._subscribers.remove(d)

        # Persist to DB via background worker (non-blocking)
        try:
            self._ensure_persist_worker()
            self._persist_queue.put_nowait(ev)
        except Exception as exc:  # noqa: BLE001
            log.debug("Persist navbatiga qo'shilmadi: %s", exc)

        return ev

    def _persist_event(self, ev: AgentEvent) -> None:
        """Persist event to DB (best effort)."""
        try:
            from ..db.storage import get_storage
            storage = get_storage()
            if not storage.enabled:
                return
            storage.db.execute(
                "INSERT INTO myai_events (event, agent_id, status, action, "
                "task_id, progress, message, from_agent, to_agent, data, "
                "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TO_TIMESTAMP(%s))",
                (
                    ev.event, ev.agent_id, ev.status, ev.action,
                    ev.task_id, ev.progress, ev.message,
                    ev.from_agent, ev.to_agent,
                    json.dumps(ev.data, ensure_ascii=False) if ev.data else "{}",
                    ev.timestamp,
                ),
            )
            log.debug("Event saqlandi: %s", ev.event)
        except Exception as exc:  # noqa: BLE001
            log.debug("Event DB'ga yozilmadi: %s — %s", ev.event, exc)

    def subscribe(self) -> deque[AgentEvent]:
        """Subscribe to events. Returns a queue that receives new events."""
        q: deque[AgentEvent] = deque(maxlen=200)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: deque[AgentEvent]) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def recent(self, limit: int = 50) -> list[dict]:
        """Get recent events from the in-memory buffer."""
        items = list(self._buffer)[-limit:]
        return [e.to_dict() for e in items]

    def events_sse(self, timeout: float = 30.0) -> Generator[str, None, None]:
        """SSE generator — yields event data lines for HTTP streaming."""
        q = self.subscribe()
        try:
            # Send recent events first
            for ev in list(self._buffer)[-20:]:
                yield f"data: {ev.to_json()}\n\n"
            # Then stream live
            while True:
                try:
                    ev = q.popleft()
                    yield f"data: {ev.to_json()}\n\n"
                except IndexError:
                    time.sleep(0.5)
                    # Send keepalive comment
                    yield f": keepalive {time.time():.0f}\n\n"
        finally:
            self.unsubscribe(q)


def emit(agent_id: str, status: str, **kwargs: Any) -> AgentEvent:
    """Convenience function to emit an agent status event."""
    return EventBus().emit(
        "agent.status", agent_id=agent_id, status=status, **kwargs
    )


def emit_message(from_agent: str, to_agent: str, **kwargs: Any) -> AgentEvent:
    """Emit an agent-to-agent message event."""
    return EventBus().emit(
        "agent.message", from_agent=from_agent, to_agent=to_agent, **kwargs
    )


def emit_task(event: str, **kwargs: Any) -> AgentEvent:
    """Emit a task-level event."""
    return EventBus().emit(event, **kwargs)


def emit_thinking(agent_id: str, task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent LLM chaqirishdan oldin 'thinking' holatiga o'tadi."""
    return EventBus().emit(
        "agent.thinking", agent_id=agent_id, status="thinking",
        task_id=task_id, **kwargs,
    )


def emit_planning(agent_id: str, task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent reja tuzayotganini bildiradi."""
    return EventBus().emit(
        "agent.planning", agent_id=agent_id, status="planning",
        task_id=task_id, **kwargs,
    )


def emit_tool_call(agent_id: str, tool: str, action: str = "",
                    task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent tool chaqirayotganini bildiradi."""
    return EventBus().emit(
        "agent.tool_call", agent_id=agent_id, status="working",
        action=f"{tool}.{action}" if action else tool,
        task_id=task_id, data={"tool": tool, "action": action}, **kwargs,
    )


def emit_tool_result(agent_id: str, tool: str, success: bool,
                      task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent tool natijasini qaytardi."""
    return EventBus().emit(
        "agent.tool_result", agent_id=agent_id,
        status="completed" if success else "error",
        task_id=task_id, data={"tool": tool, "success": success}, **kwargs,
    )


def emit_walking(agent_id: str, to_agent: str = "",
                  task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent boshqa agentga ma'lumot olib ketmoqda."""
    return EventBus().emit(
        "agent.walking", agent_id=agent_id, status="walking",
        to_agent=to_agent, task_id=task_id, **kwargs,
    )


def emit_waiting(agent_id: str, task_id: str = "", **kwargs: Any) -> AgentEvent:
    """Agent kutish holatida."""
    return EventBus().emit(
        "agent.waiting", agent_id=agent_id, status="waiting",
        task_id=task_id, **kwargs,
    )


def get_event_bus() -> EventBus:
    return EventBus()
