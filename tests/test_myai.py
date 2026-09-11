"""MyAI event bus, state, and orchestrator skeleton tests."""

import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _no_persist(monkeypatch):
    """Event yozuvlarini sinov paytida DB'ga yozmaymiz (Neon ifloslanmasin).

    `_persist_event` bu yerda no-op qilinadi — u real ishlashi alohida
    hududda (izolyatsiya qilingan bus) tekshiriladi.
    """
    from bm_automation.app.myai import events as ev_mod

    def _noop(self, ev):
        pass

    monkeypatch.setattr(ev_mod.EventBus, "_persist_event", _noop)


class TestEventBus:
    def test_emit_and_recent(self):
        from bm_automation.app.myai.events import EventBus, emit
        bus = EventBus()
        initial = len(bus.recent(100))
        ev = emit("test_agent", "working", task_id="test_001", action="test")
        assert ev.event == "agent.status"
        assert ev.agent_id == "test_agent"
        recent = bus.recent(100)
        assert len(recent) >= initial + 1

    def test_thinking_event(self):
        from bm_automation.app.myai.events import emit_thinking
        ev = emit_thinking("master", task_id="t1", action="LLM chaqirilmoqda")
        assert ev.event == "agent.thinking"
        assert ev.status == "thinking"

    def test_planning_event(self):
        from bm_automation.app.myai.events import emit_planning
        ev = emit_planning("planner", task_id="t1", action="Reja tuzilmoqda")
        assert ev.event == "agent.planning"
        assert ev.status == "planning"

    def test_tool_call_event(self):
        from bm_automation.app.myai.events import emit_tool_call
        ev = emit_tool_call("browser", "dtransport", action="get_route_data", task_id="t1")
        assert ev.event == "agent.tool_call"
        assert ev.data["tool"] == "dtransport"
        assert ev.data["action"] == "get_route_data"

    def test_tool_result_event_success(self):
        from bm_automation.app.myai.events import emit_tool_result
        ev = emit_tool_result("browser", "dtransport", success=True, task_id="t1")
        assert ev.event == "agent.tool_result"
        assert ev.data["success"] is True
        assert ev.status == "completed"

    def test_tool_result_event_failure(self):
        from bm_automation.app.myai.events import emit_tool_result
        ev = emit_tool_result("browser", "dtransport", success=False, task_id="t1")
        assert ev.event == "agent.tool_result"
        assert ev.data["success"] is False
        assert ev.status == "error"

    def test_walking_event(self):
        from bm_automation.app.myai.events import emit_walking
        ev = emit_walking("browser", to_agent="transport", task_id="t1")
        assert ev.event == "agent.walking"
        assert ev.status == "walking"
        assert ev.to_agent == "transport"

    def test_waiting_event(self):
        from bm_automation.app.myai.events import emit_waiting
        ev = emit_waiting("driver", task_id="t1")
        assert ev.event == "agent.waiting"
        assert ev.status == "waiting"

    def test_subscribe_receives_events(self):
        from bm_automation.app.myai.events import EventBus, emit
        bus = EventBus()
        q = bus.subscribe()
        try:
            emit("sub_test", "working", task_id="sub_001")
            import time
            time.sleep(0.1)
            assert len(q) >= 1
        finally:
            bus.unsubscribe(q)

    def test_sse_generator(self):
        from bm_automation.app.myai.events import EventBus
        bus = EventBus()
        gen = bus.events_sse(timeout=0.1)
        first = next(gen)
        assert first.startswith("data: ") or first.startswith(": keepalive")

    def test_emit_tool_call_no_action(self):
        from bm_automation.app.myai.events import emit_tool_call
        ev = emit_tool_call("browser", "dtransport", task_id="t1")
        assert ev.event == "agent.tool_call"
        assert ev.action == "dtransport"

    def test_event_ring_buffer_cap(self):
        from bm_automation.app.myai.events import EventBus, emit
        bus = EventBus()
        for i in range(600):
            emit(f"agent_{i % 5}", "working", task_id=f"cap_{i}")
        assert len(bus.recent(1000)) == 500


class TestEventBusPersist:
    """P1.1/P1.2 — emit() non-blocking va worker hayot sikli."""

    @staticmethod
    def _isolated_bus(ev_mod):
        """Singletondan mustaqil, toza bus — worker holati shaflashmasligi uchun."""
        bus = object.__new__(ev_mod.EventBus)
        bus._init_bus()
        return bus

    def test_persist_placeholder_is_postgres_style(self):
        """DB PostgreSQL — '?' o'rniga '%s' placeholder bo'lishi shart."""
        import inspect
        from bm_automation.app.myai import events as ev_mod
        src = inspect.getsource(ev_mod)
        assert "VALUES (%s,%s" in src
        assert "TO_TIMESTAMP(%s)" in src
        assert "?," not in src  # eski sqlite uslubi qolmasligi

    def test_emit_non_blocking_and_worker_drains(self, monkeypatch):
        """emit() DB yozuvini fon ishchi ipiga topshiradi va tez qaytadi."""
        import time
        from bm_automation.app.myai import events as ev_mod
        bus = self._isolated_bus(ev_mod)

        persisted = []

        def slow_persist(ev):
            time.sleep(0.4)  # emit() buni kutmasligi kerak
            persisted.append(ev)

        monkeypatch.setattr(bus, "_persist_event", slow_persist)

        t0 = time.time()
        bus.emit("worker_test", agent_id="x", status="working",
                 action="non-blocking check")
        elapsed = time.time() - t0
        assert elapsed < 0.3, f"emit() bloklandi: {elapsed:.2f}s"

        deadline = time.time() + 3
        while time.time() < deadline and not persisted:
            time.sleep(0.05)
        assert len(persisted) == 1

    def test_persist_worker_exits_after_idle(self, monkeypatch):
        """Navbat bo'shab qolgach ishchi ip o'zi yakunlanadi."""
        import time
        from bm_automation.app.myai import events as ev_mod
        bus = self._isolated_bus(ev_mod)

        monkeypatch.setattr(bus, "_persist_event",
                            lambda ev: None)  # DB urinishisiz

        bus.emit("idle_worker_test", agent_id="x", status="working")

        deadline = time.time() + 3
        while time.time() < deadline and bus._persist_worker_started:
            time.sleep(0.05)
        assert bus._persist_worker_started is False


class TestOrchestratorInit:
    def test_has_planner_and_reviewer(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        assert orch._planner is None
        assert orch._reviewer is None
        assert orch._cancel_events == {}

    def test_cancellation_token_lifecycle(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        token = orch.create_cancellation_token("cancel_test")
        assert not orch.is_cancelled("cancel_test")
        orch.cancel_task("cancel_test")
        assert orch.is_cancelled("cancel_test")
        orch._cleanup_token("cancel_test")
        assert not orch.is_cancelled("cancel_test")

    def test_cancel_nonexistent_task(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        assert not orch.cancel_task("nonexistent")
        assert not orch.is_cancelled("nonexistent")


class TestStateCRUD:
    def test_add_task_step_backward_compat(self):
        from bm_automation.app.myai.state import add_task_step, add_step
        assert add_task_step is add_step

    def test_update_task_step_backward_compat(self):
        from bm_automation.app.myai.state import update_task_step, update_step
        assert update_task_step is update_step

    def test_get_task_steps_backward_compat(self):
        from bm_automation.app.myai.state import get_task_steps, _get_steps
        assert get_task_steps is _get_steps


class TestAgentEvents:
    def test_base_agent_has_events_import(self):
        import inspect
        from bm_automation.app.myai.agents import BaseAgent
        source = inspect.getsource(BaseAgent._call_llm)
        assert "emit_thinking" in source

    def test_base_agent_use_tool_has_events(self):
        import inspect
        from bm_automation.app.myai.agents import BaseAgent
        source = inspect.getsource(BaseAgent._use_tool)
        assert "emit_tool_call" in source
        assert "emit_tool_result" in source


class TestPauseResume:
    def test_pause_requires_running_task(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        # bajarilmayotgan task pauza qilib bo'lmaydi
        assert orch.pause_task("nope") is False
        assert orch.resume_task("nope") is False

    def test_pause_and_resume(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.create_cancellation_token("p1")
        assert orch.pause_task("p1") is True
        assert orch.is_paused("p1") is True
        assert orch.pause_task("p1") is False  # already paused
        assert orch.resume_task("p1") is True
        assert orch.is_paused("p1") is False
        assert orch.resume_task("p1") is False  # not paused

    def test_pause_clears_on_cleanup(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.create_cancellation_token("p2")
        orch.pause_task("p2")
        assert orch.is_paused("p2")
        orch._cleanup_token("p2")
        assert not orch.is_paused("p2")

    def test_cancel_and_pause_independent(self):
        from bm_automation.app.myai.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.create_cancellation_token("c1")
        orch.pause_task("c1")
        assert not orch.is_cancelled("c1")
        assert orch.is_paused("c1")
        orch.cancel_task("c1")
        assert orch.is_cancelled("c1")
        assert orch.is_paused("c1")  # pause still active until cleanup
        orch._cleanup_token("c1")
        assert not orch.is_paused("c1")

    def test_orchestrator_has_wait_if_paused(self):
        import inspect
        from bm_automation.app.myai.orchestrator import Orchestrator
        source = inspect.getsource(Orchestrator)
        assert "_wait_if_paused" in source
        assert "pause_task" in source
        assert "resume_task" in source


# ── Fake LLM / tool / state — integratsiya testlari uchun (P9) ─────────

class FakeLLM:
    """Script'li (navbatli) javob beradigan soxta LLM provider."""

    name = "fake"

    def __init__(self, script=None, default=None):
        from bm_automation.app.myai.models import LLMResponse
        self.LLMResponse = LLMResponse
        self.script = list(script or [])
        self.default = default or ('{"approved": true, "errors": [], '
                                   '"corrections": [], "confidence": 1.0}')
        self.calls = []

    def complete(self, system, user, **kwargs):
        self.calls.append(user)
        content = self.script.pop(0) if self.script else self.default
        return self.LLMResponse(content=content, model="fake",
                                tokens_used=1, latency_ms=1)

    def chat(self, messages, **kwargs):
        user = messages[-1].content if messages else ""
        return self.complete("", user, **kwargs)

    def configured(self):
        return True


class FakeTool:
    def __init__(self, on_execute):
        self._on = on_execute

    async def execute(self, **kwargs):
        return self._on(**kwargs)


class FakeRegistry:
    def __init__(self, on_execute):
        self.on_execute = on_execute

    def get(self, name):
        return FakeTool(self.on_execute)


class FakeState:
    """Xotiradagi soxta state — DB'siz orchestrator integratsiyasi."""

    def __init__(self):
        self.tasks = {}
        self.logs = []
        self.steps = []
        self._next = 1

    def get_task(self, task_id):
        if task_id not in self.tasks:
            return None
        return dict(self.tasks[task_id])

    def update_task(self, task_id, **kw):
        import json
        t = self.tasks.setdefault(task_id, {})
        for k, v in kw.items():
            if k == "result" and isinstance(v, dict):
                v = json.dumps(v)
            t[k] = v
        return True

    def add_log(self, task_id, agent, message, level="info", metadata=None):
        self.logs.append(dict(task_id=task_id, agent=agent,
                              message=message, level=level))

    def add_task_step(self, task_id, agent, action=""):
        sid = self._next
        self._next += 1
        self.steps.append(dict(id=sid, task_id=task_id, agent=agent,
                               action=action, status="pending"))
        return sid

    def update_task_step(self, sid, **kw):
        for s in self.steps:
            if s["id"] == sid:
                s.update(kw)
                return True
        return False


def _install_fakes(monkeypatch, store, script=None, on_execute=None):
    """state va tool'larni soxta obyektlar bilan almashtiradi."""
    from bm_automation.app.myai import orchestrator as orch_mod
    from bm_automation.app.myai import state as state_mod

    monkeypatch.setattr(state_mod, "get_task", store.get_task)
    monkeypatch.setattr(state_mod, "update_task", store.update_task)
    monkeypatch.setattr(state_mod, "add_log", store.add_log)
    monkeypatch.setattr(state_mod, "add_task_step", store.add_task_step)
    monkeypatch.setattr(state_mod, "update_task_step", store.update_task_step)
    monkeypatch.setattr(
        orch_mod, "get_tool_registry",
        lambda: FakeRegistry(on_execute or (lambda **kw: {"rows": [1]})),
    )
    return FakeLLM(script=script)


class TestIntegration:
    def test_run_task_end_to_end(self, monkeypatch):
        import asyncio
        from bm_automation.app.myai.orchestrator import Orchestrator

        store = FakeState()
        store.tasks["t_end"] = {
            "user_request": "B-80 marshrutini tekshir",
            "result": {"route": "r1", "date": "2026-08-10"},
        }
        script = [
            '{"intent": "check_route", "params": {}}',
            '{"task_type": "check_route", "params": {}, "steps": ['
            ' {"agent": "browser", "action": "get_route_data"},'
            ' {"agent": "transport", "action": "normalize"}]}',
        ]
        fake = _install_fakes(monkeypatch, store, script)
        orch = Orchestrator(llm=fake)
        res = asyncio.run(orch.run_task("t_end"))

        assert res["ok"] is True
        assert res["result"]["response"]
        assert store.tasks["t_end"]["status"] == "completed"
        assert "browser" in res["result"]["steps_results"]
        assert "sources" in res["result"]
        assert store.tasks["t_end"]["progress"] == 100

    def test_run_task_cancelled_mid_way(self, monkeypatch):
        import asyncio
        import threading
        import time
        from bm_automation.app.myai.orchestrator import Orchestrator

        store = FakeState()
        store.tasks["t_cancel"] = {
            "user_request": "marshrutni tekshir", "result": {},
        }
        started = threading.Event()
        release = threading.Event()

        def slow_tool(**kw):
            started.set()
            release.wait(timeout=5)
            return {"rows": [1]}

        script = [
            '{"intent": "general", "params": {}}',
            '{"task_type": "general", "params": {}, "steps": ['
            ' {"agent": "browser", "action": "get_route_data"},'
            ' {"agent": "browser", "action": "get_route_data"}]}',
        ]
        fake = _install_fakes(monkeypatch, store, script, on_execute=slow_tool)
        orch = Orchestrator(llm=fake)
        box = {}

        def runner():
            box["r"] = asyncio.run(orch.run_task("t_cancel"))

        th = threading.Thread(target=runner, daemon=True)
        th.start()
        assert started.wait(3)
        assert orch.cancel_task("t_cancel") is True
        release.set()
        th.join(10)

        res = box.get("r", {})
        assert res.get("ok") is False
        assert res.get("error") == "cancelled"
        assert store.tasks["t_cancel"]["status"] == "cancelled"

    def test_run_task_paused_and_resumed(self, monkeypatch):
        import asyncio
        import threading
        import time
        from bm_automation.app.myai.orchestrator import Orchestrator

        store = FakeState()
        store.tasks["t_p"] = {"user_request": "xulosani tayyorla", "result": {}}
        started = threading.Event()
        gate = threading.Event()

        def gated_tool(**kw):
            started.set()
            gate.wait(timeout=5)
            return {"rows": [1]}

        script = [
            '{"intent": "general", "params": {}}',
            '{"task_type": "general", "params": {}, "steps": ['
            ' {"agent": "browser", "action": "get_route_data"},'
            ' {"agent": "transport", "action": "normalize"}]}',
        ]
        fake = _install_fakes(monkeypatch, store, script, on_execute=gated_tool)
        orch = Orchestrator(llm=fake)
        box = {}

        def runner():
            box["r"] = asyncio.run(orch.run_task("t_p"))

        th = threading.Thread(target=runner, daemon=True)
        th.start()
        assert started.wait(3)
        assert orch.pause_task("t_p") is True
        gate.set()
        time.sleep(0.3)
        assert orch.is_paused("t_p")
        assert orch.resume_task("t_p") is True
        th.join(10)

        res = box.get("r", {})
        assert res.get("ok") is True
        assert store.tasks["t_p"]["status"] == "completed"


class TestLLMTimeout:
    def test_llm_call_times_out(self, monkeypatch):
        import asyncio
        import time
        from bm_automation.app.myai import config as cfg_mod
        from bm_automation.app.myai.agents import BaseAgent
        from bm_automation.app.myai.models import (AgentContext,
                                                   AgentResult, AgentType)

        monkeypatch.setenv("MYAI_LLM_TIMEOUT", "0.2")
        monkeypatch.setattr(cfg_mod, "_config", None)

        class SlowLLM:
            name = "slow"

            def configured(self):
                return True

            def complete(self, system, user, **kwargs):
                time.sleep(0.5)
                return None

            def chat(self, messages, **kwargs):
                return None

        class DummyAgent(BaseAgent):
            name = AgentType.MASTER

            async def run(self, context):
                return AgentResult(success=True)

        agent = DummyAgent(SlowLLM())
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(agent._call_llm("s", "u"))

    def test_source_label_from_tool(self):
        from bm_automation.app.myai.orchestrator import _source_label_for
        from bm_automation.app.myai.agents import BaseAgent
        from bm_automation.app.myai.models import AgentType, AgentResult

        class A(BaseAgent):
            name = AgentType.ROUTE

            async def run(self, context):
                return AgentResult(success=True)

        a = A(FakeLLM())
        a._last_tool = ("db", "get_route_daily")
        assert "PostgreSQL" in _source_label_for(a)
        a._last_tool = ("browser", "open_page")
        assert "Playwright" in _source_label_for(a)
