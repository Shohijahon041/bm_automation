"""MyAI ↔ bilim bazasi (Obsidian) va agent ishonchliligi testlari.

Tekshiriladi:
- vault qaydlari agent kontekstiga tushishi (qidiruv + blok);
- barcha agentlarga `vault` tool ochiqligi, yozish esa cheklanganligi;
- tool timeout/retry va xato hodisasi (`last_tool_error`);
- rol normalizatsiyasi (fail-closed) va security gate;
- planner/reviewer/master promptlarida bilim bazasi bloki.
"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _no_persist(monkeypatch):
    """Event yozuvlarini DB'ga yozmaymiz (izolyatsiya)."""
    from bm_automation.app.myai import events as ev_mod

    monkeypatch.setattr(ev_mod.EventBus, "_persist_event", lambda self, ev: None)


@pytest.fixture(autouse=True)
def _fresh_knowledge_cache():
    """Har testdan oldin bilim bazasi keshini tozalash (vault papkasi o'zgaradi)."""
    from bm_automation.app.myai import knowledge_context

    knowledge_context.clear_cache()
    yield
    knowledge_context.clear_cache()


def _note(title, category, text):
    from bm_automation.app.core import knowledge as K
    return K.write_note(title, category, text)


# ---------------------------------------------------------------------------
# Bilim bazasi konteksti
# ---------------------------------------------------------------------------

def test_relevant_notes_finds_matching_note():
    from bm_automation.app.myai import knowledge_context as kc

    _note("KM hisoblash qoidasi", "Qoidalar",
          "Yo'nalish km narxi brutto bilan hisoblanadi va tarif qo'llanadi.")
    _note("Telegram token yangilash", "Sozlamalar",
          "Bot token muddati tugasa yangi token qo'yiladi.")
    kc.clear_cache()

    notes = kc.relevant_notes("yo'nalish km narxi brutto hisoblash", limit=2)
    assert notes, "qidiruv natija qaytarishi kerak"
    assert any("KM-hisoblash-qoidasi" in n["path"] for n in notes), notes


def test_brief_contains_path_and_excerpt():
    from bm_automation.app.myai import knowledge_context as kc

    _note("Invariantlar", "Xatolar", "accepted + not_accepted == total_trips")
    kc.clear_cache()

    text = kc.brief("accepted not_accepted total_trips tekshirish")
    assert "Bilim bazasi" in text
    assert "Xatolar/Invariantlar" in text


def test_brief_empty_when_disabled(monkeypatch):
    from bm_automation.app.myai import knowledge_context as kc

    _note("Qoida", "Qoidalar", "matn")
    kc.clear_cache()
    monkeypatch.setenv("MYAI_KNOWLEDGE", "off")
    assert kc.brief("qoida") == ""
    assert kc.enabled() is False


def test_brief_falls_back_to_primary_note():
    """Mos qayd topilmasa ham asosiy invariantlar beriladi."""
    from bm_automation.app.myai import knowledge_context as kc

    _note("Asosiy-invariantlar", "Qoidalar", "jami = qabul + qabul qilinmagan")
    kc.clear_cache()

    notes = kc.relevant_notes("zzzz hech qanday mos so'z yo'q")
    assert [n["path"] for n in notes] == ["Qoidalar/Asosiy-invariantlar.md"]


def test_lesson_hint_prefers_error_notes():
    from bm_automation.app.myai import knowledge_context as kc

    _note("dtransport 401 xatosi", "Xatolar",
          "dtransport tool 401 qaytarsa token yangilanishi kerak.")
    kc.clear_cache()

    hint = kc.lesson_hint("dtransport", "get_routes")
    assert "Xatolar/dtransport-401-xatosi" in hint


# ---------------------------------------------------------------------------
# Agent ↔ vault ulanishi
# ---------------------------------------------------------------------------

def test_all_agents_have_vault_read_access():
    """Har bir agent bilim bazasini o'qiy oladi (Obsidian bilan ulangan)."""
    from bm_automation.app.myai.models import AgentType
    from bm_automation.app.myai.orchestrator import AGENT_TOOL_ALLOWLIST

    for agent in AgentType:
        if agent is AgentType.MASTER:
            continue
        assert agent.value in AGENT_TOOL_ALLOWLIST, agent.value
        assert "vault" in AGENT_TOOL_ALLOWLIST[agent.value], agent.value


# ---------------------------------------------------------------------------
# Tool ishonchliligi: timeout, retry, diagnostika
# ---------------------------------------------------------------------------

class _Tool:
    """Navbatli natija/xato qaytaradigan soxta tool."""

    def __init__(self, results, name="demo"):
        self.name = name
        self._results = list(results)
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        item = self._results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _Registry:
    def __init__(self, tool):
        self._tool = tool

    def get(self, name):
        return self._tool if name == self._tool.name else None

    def list_tools(self):
        return [self._tool.name]


class _Agent:
    """BaseAgent'ning minimal merosxo'ri (run shart emas)."""

    @staticmethod
    def build(name, tool, results=None, retries=1, timeout=5.0):
        from bm_automation.app.myai.agents import BaseAgent
        from bm_automation.app.myai.models import AgentType

        class _A(BaseAgent):
            async def run(self, context):
                return None

        agent = _A.__new__(_A)
        agent.name = AgentType(name)
        agent.tools = _Registry(tool)
        agent.allowed_tools = {tool.name}
        agent.config = SimpleNamespace(tool_timeout=timeout, tool_retries=retries)
        agent.source_note = ""
        agent._last_tool = None
        agent._last_tool_error = None
        from bm_automation.app.utils.logger import get_logger
        agent.logger = get_logger(f"myai.test.{name}")
        return agent


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    """Testlarda kutish bo'lmasin (jitter/backoff 0)."""
    from bm_automation.app.myai import agents as agents_mod

    monkeypatch.setattr(agents_mod.BaseAgent, "_backoff",
                        staticmethod(lambda attempt: 0.0), raising=False)


def test_tool_retries_transient_error_then_succeeds():
    tool = _Tool([TimeoutError("tarmoq"), {"ok": True}])
    agent = _Agent.build("transport", tool, retries=2)

    result = asyncio.run(agent._use_tool("demo", action="read"))
    assert result == {"ok": True}
    assert tool.calls == 2
    assert agent.last_tool_error is None


def test_tool_does_not_retry_non_idempotent_action():
    tool = _Tool([TimeoutError("tarmoq"), {"ok": True}])
    agent = _Agent.build("transport", tool, retries=3)

    with pytest.raises(TimeoutError):
        asyncio.run(agent._use_tool("demo", action="write"))
    assert tool.calls == 1, "yozish qayta urinilmaydi (ikki marta bajarilmasin)"


def test_tool_logical_error_is_not_retried():
    tool = _Tool([ValueError("noto'g'ri argument"), {"ok": True}])
    agent = _Agent.build("transport", tool, retries=3)

    with pytest.raises(ValueError):
        asyncio.run(agent._use_tool("demo", action="read"))
    assert tool.calls == 1
    assert agent.last_tool_error["error"].startswith("noto'g'ri")


def test_tool_timeout_is_bounded_and_reported(monkeypatch):
    from bm_automation.app.myai import agents as agents_mod

    class _Slow(_Tool):
        async def execute(self, **kwargs):
            self.calls += 1
            await asyncio.sleep(0.5)
            return {"ok": True}

    monkeypatch.setattr(agents_mod.BaseAgent, "_tool_timeout",
                        lambda self: 0.05, raising=False)
    tool = _Slow([])
    agent = _Agent.build("transport", tool, retries=0, timeout=0.05)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(agent._use_tool("demo", action="read"))
    assert agent.last_tool_error["tool"] == "demo"
    assert agent.last_tool_error["attempts"] == 1


def test_tool_failure_emits_error_event():
    from bm_automation.app.myai.events import EventBus

    tool = _Tool([ValueError("buzilgan ma'lumot")])
    agent = _Agent.build("transport", tool, retries=0)
    with pytest.raises(ValueError):
        asyncio.run(agent._use_tool("demo", action="read"))

    results = [e for e in EventBus().recent(50)
               if e.get("event") == "agent.tool_result"
               and e.get("agent_id") == "transport"]
    assert results, "xato hodisasi ham yuborilishi kerak"
    assert results[-1]["data"]["success"] is False


def test_vault_write_gate():
    """Bilim bazasiga yozish faqat ruxsat etilgan agentlarga."""
    tool = _Tool([{"ok": True}], name="vault")
    transport = _Agent.build("transport", tool)
    with pytest.raises(PermissionError):
        asyncio.run(transport._use_tool("vault", action="write"))
    assert tool.calls == 0

    reviewer = _Agent.build("reviewer", tool)
    assert asyncio.run(reviewer._use_tool("vault", action="write")) == {"ok": True}


# ---------------------------------------------------------------------------
# Rol va security gate (fail-closed)
# ---------------------------------------------------------------------------

class _State:
    def update_task(self, *a, **k):
        pass

    def add_log(self, *a, **k):
        pass


def _quiet(monkeypatch):
    from bm_automation.app.myai import orchestrator as O

    monkeypatch.setattr(O, "emit_task", lambda *a, **k: None)
    monkeypatch.setattr(O, "emit", lambda *a, **k: None)


def test_normalize_role_mapping():
    from bm_automation.app.myai.orchestrator import _normalize_role

    assert _normalize_role("ADMIN") == "admin"
    assert _normalize_role("DIRECTOR") == "manager"   # huquq oshirilmaydi
    assert _normalize_role("manager") == "manager"
    assert _normalize_role("dispatcher") == "dispatcher"
    assert _normalize_role("nomalum") == "viewer"
    assert _normalize_role("") == "viewer"


def test_security_gate_denies_viewer_salary(monkeypatch):
    from bm_automation.app.myai.orchestrator import Orchestrator

    _quiet(monkeypatch)
    orch = Orchestrator()
    denied = asyncio.run(orch._security_gate(
        "t1", _State(), lambda *a, **k: None,
        {"intent": "salary"}, {"role": "viewer"}))
    assert denied is not None and denied["denied"] is True


def test_security_gate_allows_manager_salary(monkeypatch):
    from bm_automation.app.myai.orchestrator import Orchestrator

    _quiet(monkeypatch)
    orch = Orchestrator()
    assert asyncio.run(orch._security_gate(
        "t1", _State(), lambda *a, **k: None,
        {"intent": "salary"}, {"role": "manager"})) is None


def test_security_gate_is_fail_closed_on_role_error(monkeypatch):
    """Rolni aniqlab bo'lmasa huquq berilmaydi (avval 'admin' qaytarardi)."""
    from bm_automation.app.notifications.ops import roles
    from bm_automation.app.myai.orchestrator import Orchestrator

    _quiet(monkeypatch)

    def _boom(chat_id):
        raise RuntimeError("rol xizmati ishlamadi")

    monkeypatch.setattr(roles, "resolve_role", _boom)
    orch = Orchestrator()
    denied = asyncio.run(orch._security_gate(
        "t1", _State(), lambda *a, **k: None,
        {"intent": "settings"}, {"chat_id": "555"}))
    assert denied is not None and denied["denied"] is True


# ---------------------------------------------------------------------------
# Bilim bazasi bloki promptlarda
# ---------------------------------------------------------------------------

class _PromptLLM:
    """System promptni yozib oladigan soxta LLM."""

    name = "fake"

    def __init__(self, reply):
        from bm_automation.app.myai.models import LLMResponse
        self._reply = reply
        self.systems = []

    def complete(self, system, user, **kwargs):
        from bm_automation.app.myai.models import LLMResponse

        self.systems.append(system)
        return LLMResponse(content=self._reply, model="fake")

    def chat(self, messages, **kwargs):
        return self.complete("", messages[-1].content if messages else "")

    def configured(self):
        return True


def test_planner_prompt_includes_knowledge():
    """LLM plan yo'lida bilim bazasi bloki promptga qo'shiladi.

    `check_route` kabi intentlar deterministik template bilan ishlaydi
    (LLM chaqirilmaydi), shuning uchun template'ga tushmaydigan intent
    olinadi — aks holda test LLM yo'lini umuman tekshirmaydi.
    """
    from bm_automation.app.myai.planner import PlannerAgent

    llm = _PromptLLM('{"task_type": "analysis", "steps": '
                     '[{"agent": "analytics", "action": "calculate"}]}')
    planner = PlannerAgent(llm)
    plan = asyncio.run(planner.create_plan(
        "Korxona bo'yicha tahlil qil",
        intent={"intent": "analysis", "params": {}}, params={},
        knowledge="BILIM-BLOKI"))
    assert llm.systems, "LLM chaqirilmadi (deterministik template ishladi)"
    assert any("BILIM-BLOKI" in s for s in llm.systems)
    assert [s.agent.value for s in plan.steps] == ["analytics"]


def test_reviewer_prompt_includes_knowledge():
    from bm_automation.app.myai.reviewer import ReviewerAgent

    llm = _PromptLLM('{"approved": true, "errors": [], '
                     '"corrections": [], "confidence": 1.0}')
    reviewer = ReviewerAgent(llm)
    asyncio.run(reviewer.verify("master", {"a": 1}, {"analytics": {"x": 1}},
                                knowledge="BILIM-BLOKI"))
    assert any("BILIM-BLOKI" in s for s in llm.systems)


def test_master_response_includes_knowledge():
    from bm_automation.app.myai.master import MasterAgent
    from bm_automation.app.myai.models import AgentContext

    llm = _PromptLLM("B-80 yo'nalishi bo'yicha ma'lumot tayyor.")
    master = MasterAgent(llm)
    context = AgentContext(user_request="B-80 ni tekshir",
                           knowledge="BILIM-BLOKI")
    asyncio.run(master._build_response(context, {"analytics": {"x": 1}}))
    assert any("BILIM-BLOKI" in s for s in llm.systems)


def test_orchestrator_knowledge_helpers():
    from bm_automation.app.myai.orchestrator import Orchestrator

    _note("KM qoidasi", "Qoidalar", "brutto skm tarif hisobi")
    from bm_automation.app.myai import knowledge_context as kc
    kc.clear_cache()

    notes = Orchestrator._knowledge_notes("brutto skm tarif hisobi")
    assert [n["path"] for n in notes] == ["Qoidalar/KM-qoidasi.md"]
    assert "Qoidalar/KM-qoidasi" in Orchestrator._knowledge_brief(
        "brutto skm tarif hisobi")
