"""Vault tool va reviewer self-improvement testlari.

HaShqiy fayl papkasida ishlaydi (Obsidian vault) — DB talab qilmaydi.
"""

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _run(coro):
    return asyncio.run(coro)


class TestVaultTool:
    def test_registry_has_vault(self):
        from bm_automation.app.myai.tools import get_tool_registry
        reg = get_tool_registry()
        assert "vault" in reg.list_tools()
        tool = reg.get("vault")
        assert tool.name == "vault"

    def test_read_qoidalar_file(self, monkeypatch, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        (base / "Qoidalar").mkdir(parents=True)
        (base / "Qoidalar" / "Asosiy-invariantlar.md").write_text(
            "# Asosiy invariantlar\n\n## QATNOV ≠ AVTOBUS\n", encoding="utf-8")
        tool = VaultTool(base_dir=base)
        res = _run(tool.execute(action="read",
                                path="Qoidalar/Asosiy-invariantlar.md"))
        assert res["found"] is True
        assert "QATNOV" in res["text"]

    def test_search_finds_wikilink_compatible(self, monkeypatch, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        (base / "Xatolar").mkdir(parents=True)
        (base / "Xatolar" / "async-syntax-bug.md").write_text(
            "# async-syntax-bug\n\nawait faqat async funksiyada\n",
            encoding="utf-8")
        tool = VaultTool(base_dir=base)
        res = _run(tool.execute(action="search", keyword="async"))
        assert res["found"] is True
        assert any("async-syntax-bug" in r["path"] for r in res["results"])

    def test_write_creates_file(self, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        tool = VaultTool(base_dir=base)
        res = _run(tool.execute(action="write",
                                title="test-xato",
                                category="Xatolar",
                                content="Sabab topildi."))
        assert res["ok"] is True
        assert (base / "Xatolar" / "test-xato.md").exists()

    def test_write_unknown_category_rejected(self, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        tool = VaultTool(base_dir=base)
        res = _run(tool.execute(action="write",
                                title="qayd",
                                category="Boshqa",
                                content="..."))
        assert res["ok"] is False
        assert "ruxsat" in res["error"]

    def test_path_traversal_blocked(self, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        tool = VaultTool(base_dir=base)
        with pytest.raises(ValueError):
            _run(tool.execute(action="read", path="../../secret"))

    def test_index_has_main_documents(self, monkeypatch, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        base = tmp_path / "vault"
        (base / "Qoidalar").mkdir(parents=True)
        (base / "Qoidalar" / "Asosiy-invariantlar.md").write_text(
            "# Asosiy invariantlar\n", encoding="utf-8")
        (base / "Qoidalar" / "Tekshirish-qoidalari.md").write_text(
            "# Tekshirish\n", encoding="utf-8")
        (base / "Xatolar").mkdir(parents=True)
        (base / "Xatolar" / "Xato-jurnali.md").write_text(
            "# Xato jurnali\n", encoding="utf-8")
        tool = VaultTool(base_dir=base)
        res = _run(tool.execute(action="index"))
        paths = [e["path"] for e in res["entries"]]
        assert "Xatolar/Xato-jurnali.md" in paths
        assert "Qoidalar/Asosiy-invariantlar.md" in paths


class TestSharedPreamble:
    def test_preamble_in_system_prompts(self):
        from bm_automation.app.myai.prompts.master import MASTER_SYSTEM
        from bm_automation.app.myai.prompts.reviewer import REVIEWER_SYSTEM
        from bm_automation.app.myai.prompts.analytics import ANALYTICS_SYSTEM
        from bm_automation.app.myai.prompts.transport import TRANSPORT_SYSTEM
        from bm_automation.app.myai.prompts.shared import SHARED_PREAMBLE
        for sys_ in (MASTER_SYSTEM, REVIEWER_SYSTEM,
                     ANALYTICS_SYSTEM, TRANSPORT_SYSTEM):
            assert sys_.startswith(SHARED_PREAMBLE)
        assert "TO'G'RILIK" in SHARED_PREAMBLE

    def test_preamble_mentions_vault(self):
        from bm_automation.app.myai.prompts.shared import SHARED_PREAMBLE
        assert "vault" in SHARED_PREAMBLE


class TestReviewerLearns:
    def _reviewer_with_fake_tools(self, tmp_path):
        """Vault tool'ga ruxsat berilgan, soxta tools registry'li reviewer."""
        import json
        from bm_automation.app.myai.tools.vault import VaultTool
        from bm_automation.app.myai.reviewer import ReviewerAgent

        class FakeLLM:
            name = "fake"

            def configured(self):
                return False  # LLM ishlamaydi → local fallback qoidalari

            def complete(self, system, user, **kwargs):
                raise RuntimeError("LLM yo'q")

            def chat(self, messages, **kwargs):
                raise RuntimeError("LLM yo'q")

        class FakeTools:
            def __init__(self, tool):
                self._tool = tool

            def get(self, name):
                if name == "vault":
                    return self._tool
                return None

        base = tmp_path / "vault"
        tool = VaultTool(base_dir=base)
        agent = ReviewerAgent(FakeLLM(), FakeTools(tool))
        agent.allowed_tools = {"vault"}
        return agent, base

    def test_reviewer_writes_to_vault_on_error(self, tmp_path, monkeypatch):
        agent, base = self._reviewer_with_fake_tools(tmp_path)

        from bm_automation.app.myai.models import AgentContext
        ctx = AgentContext(
            task_id="t1",
            user_request="test",
            params={
                "agent_name": "analytics",
                "input_data": {"route": "r1"},
                "output_data": {},  # bo'sh → local qoida: "Natija bo'sh"
            },
        )
        res = _run(agent.run(ctx))
        assert res.success is True
        data = res.data
        assert data["approved"] is False

        md_files = list((base / "Xatolar").glob("*.md"))
        assert md_files, "Vault'ga xato yozilishi kerak"
        assert any("analytics" in f.read_text(encoding="utf-8")
                   for f in md_files)

    def test_reviewer_no_write_when_approved(self, tmp_path, monkeypatch):
        agent, base = self._reviewer_with_fake_tools(tmp_path)

        from bm_automation.app.myai.models import AgentContext
        ctx = AgentContext(
            task_id="t2",
            user_request="test",
            params={
                "agent_name": "analytics",
                "input_data": {},
                "output_data": {
                    "total_trips": 10,
                    "accepted": 10,
                    "not_accepted": 0,
                    "total_km": 100,
                    "completion_rate": 100,
                },
            },
        )
        res = _run(agent.run(ctx))
        assert res.data["approved"] is True
        if (base / "Xatolar").exists():
            assert not list((base / "Xatolar").glob("*.md"))


class TestAllowlist:
    def test_reviewer_allowed_vault(self):
        from bm_automation.app.myai.orchestrator import AGENT_TOOL_ALLOWLIST
        assert "vault" in AGENT_TOOL_ALLOWLIST["reviewer"]
        assert "vault" in AGENT_TOOL_ALLOWLIST["analytics"]

    def test_registry_metadata_reviewer_has_vault(self):
        from bm_automation.app.myai.registry import AGENT_REGISTRY
        assert "vault" in AGENT_REGISTRY["reviewer"].tools


class TestSelfCheck:
    """Deterministik agentlar uchun self_check (invariant tekshiruvi)."""

    def _agent_with_vault(self, tmp_path):
        from bm_automation.app.myai.tools.vault import VaultTool
        from bm_automation.app.myai.reviewer import ReviewerAgent

        class FakeLLM:
            name = "fake"

            def configured(self):
                return False

            def complete(self, system, user, **kwargs):
                raise RuntimeError("LLM yo'q")

            def chat(self, messages, **kwargs):
                raise RuntimeError("LLM yo'q")

        class FakeTools:
            def __init__(self, tool):
                self._tool = tool

            def get(self, name):
                if name == "vault":
                    return self._tool
                return None

        base = tmp_path / "vault"
        tool = VaultTool(base_dir=base)
        agent = ReviewerAgent(FakeLLM(), FakeTools(tool))
        agent.allowed_tools = {"vault"}
        return agent, base

    def test_self_check_ok_no_marker(self, tmp_path):
        agent, _ = self._agent_with_vault(tmp_path)
        good = {
            "total_trips": 10, "accepted": 8, "not_accepted": 2,
            "completion_rate": 80, "total_km": 100,
        }
        out = _run(agent.self_check(good, "analytics_calculate"))
        assert "_self_check" not in out
        assert out["total_trips"] == 10

    def test_self_check_invariant_break_adds_marker(self, tmp_path):
        agent, base = self._agent_with_vault(tmp_path)
        bad = {
            "total_trips": 10, "accepted": 8, "not_accepted": 5,
            "completion_rate": 80, "total_km": 100,
        }
        out = _run(agent.self_check(bad, "analytics_calculate"))
        assert out["_self_check"]["ok"] is False
        assert any("Invariant" in e for e in out["_self_check"]["errors"])
        # vault'ga yozildi
        md_files = list((base / "Xatolar").glob("*.md"))
        assert md_files, "Invariant buzzilishi vault'ga yozilishi kerak"

    def test_self_check_rate_mismatch(self, tmp_path):
        agent, _ = self._agent_with_vault(tmp_path)
        # foiz noto'g'ri: 8/10 = 80% emas 50%
        bad = {
            "total_trips": 10, "accepted": 8, "not_accepted": 2,
            "completion_rate": 50, "total_km": 100,
        }
        out = _run(agent.self_check(bad, "analytics_calculate"))
        assert out["_self_check"]["ok"] is False
        assert any("foizi" in e for e in out["_self_check"]["errors"])

    def test_self_check_no_vault_perm_no_write(self, tmp_path, monkeypatch):
        """Vault ruxsati bo'lmagan agent tekshiradi, lekin yozmaydi."""
        base = tmp_path / "vault"

        from bm_automation.app.myai.tools.vault import VaultTool
        from bm_automation.app.myai.reviewer import ReviewerAgent

        class FakeLLM:
            name = "fake"

            def configured(self):
                return False

            def complete(self, system, user, **kwargs):
                raise RuntimeError("LLM yo'q")

            def chat(self, messages, **kwargs):
                raise RuntimeError("LLM yo'q")

        class FakeTools:
            def get(self, name):
                return None

        tool = VaultTool(base_dir=base)
        agent = ReviewerAgent(FakeLLM(), FakeTools())
        agent.tools = type("T", (), {"get": lambda self_, n: tool})()
        agent.allowed_tools = set()  # vault ruxsati yo'q

        bad = {
            "total_trips": 10, "accepted": 8, "not_accepted": 5,
            "completion_rate": 80, "total_km": 100,
        }
        out = _run(agent.self_check(bad, "analytics_calculate"))
        assert out["_self_check"]["ok"] is False
        if (base / "Xatolar").exists():
            assert not list((base / "Xatolar").glob("*.md"))

    def test_verify_invariants_module_function(self):
        from bm_automation.app.myai.reviewer import verify_invariants
        ok = verify_invariants(
            {"total_trips": 10, "accepted": 8, "not_accepted": 2})
        assert ok.approved is True
        bad = verify_invariants(
            {"total_trips": 10, "accepted": 8, "not_accepted": 5})
        assert bad.approved is False