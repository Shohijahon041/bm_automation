"""MyAI korxona (tenant) scope testlari.

Scope DB so'rovlarini korxona yo'nalishlari bilan cheklaydi, begona
resursga aniq murojaatni rad etadi va bot/panel kirish nuqtalarida
scope'ni task parametrlariga uzatadi.
"""

import asyncio
import pytest

from bm_automation.app.db.storage import storage_for
from bm_automation.app.myai import scope as scope_mod
from tests.sqlite_backend import SQLiteDatabase


class _SqliteCompat(SQLiteDatabase):
    """Tool'lar `%s` placeholder/ILIKE ishlatadi; SQLite `?`/LIKE kutadi."""

    def query(self, sql, params=None, limit=None):
        sql = sql.replace("%s", "?").replace("ILIKE", "LIKE")
        return super().query(sql, params, limit)

    def execute(self, sql, params=None):
        return super().execute(sql.replace("%s", "?"), params)


@pytest.fixture()
def storage(tmp_path):
    return storage_for(_SqliteCompat(str(tmp_path / "myai_scope.db")))


@pytest.fixture(autouse=True)
def _scope_clean():
    token = scope_mod.set_scope(None)
    yield
    scope_mod.reset_scope(token)


def _seed(storage):
    storage.save_route("r1", name="A-1", entity_type="ROUTE")
    storage.save_route("r2", name="B-2", entity_type="ROUTE")
    storage.save_vehicle("v1", plate_number="01 A 001", route_id="r1")
    storage.save_vehicle("v2", plate_number="01 B 002", route_id="r2")
    storage.save_driver("d1", full_name="Ali Valiyev", route_id="r1")
    storage.save_driver("d2", full_name="Vali Aliyev", route_id="r2")


# --------------------------------------------------------------- slug'lar

def test_normalize_admin_global():
    assert scope_mod.normalize_scope(None) is None
    assert scope_mod.normalize_scope({"is_admin": True}) == {"is_admin": True}


def test_normalize_fail_closed_without_routes():
    scope = scope_mod.normalize_scope({"company": ""})
    assert scope["is_admin"] is False
    assert scope["deny"] is True


def test_normalize_keeps_company_and_routes():
    scope = scope_mod.normalize_scope(
        {"company": "A", "companies": ["A", "B"], "routes": ["r1", "r1"]})
    assert scope["company"] == "A"
    assert scope["routes"] == ["r1"]
    assert scope["deny"] is False


def test_allowed_routes_admin_and_scoped():
    assert scope_mod.allowed_routes() is None
    scope_mod.set_scope({"routes": ["r1", "r1", "r2"]})
    assert scope_mod.allowed_routes() == ["r1", "r2"]
    scope_mod.set_scope({"company": "A"})
    assert scope_mod.allowed_routes() == []


def test_allowed_routes_company_id_fallback(storage):
    _seed(storage)
    storage.db.execute("UPDATE routes SET company_id = ? WHERE external_id = ?",
                       (7, "r2"))
    scope_mod.set_scope({"company": "B", "company_id": 7})
    assert scope_mod.allowed_routes(storage) == ["r2"]


def test_scope_company_label():
    assert scope_mod.scope_company_label({"is_admin": True}) == ""
    assert scope_mod.scope_company_label(
        {"company": "A", "companies": ["A"]}) == "A"
    assert scope_mod.scope_company_label(
        {"company": "A", "companies": ["A", "B"]}) == "A, B"


def test_scope_for_session_global_admin():
    assert scope_mod.scope_for_session(
        {"is_admin": True, "company": "", "routes": None}) == {
            "is_admin": True}


def test_scope_for_session_company_admin_is_restricted(monkeypatch):
    from bm_automation.app.core import tenant
    monkeypatch.setattr(tenant, "company_routes", lambda name: ["r1"])
    monkeypatch.setattr(tenant, "company_id_for_name", lambda name: 4)
    scope = scope_mod.scope_for_session(
        {"is_admin": True, "company": "A", "routes": None, "company_id": 0})
    assert scope["is_admin"] is False
    assert scope["company"] == "A"
    assert scope["routes"] == ["r1"]
    assert scope["company_id"] == 4
    assert scope["deny"] is False


def test_scope_for_chat_uses_company_and_assignments(monkeypatch):
    from bm_automation.app.core import tenant
    from bm_automation.app.notifications.ops import context as bot_context
    from bm_automation.app.notifications.ops import roles

    monkeypatch.setattr(roles, "resolve_role", lambda cid: roles.Role.MANAGER)
    monkeypatch.setattr(bot_context, "allowed_names", lambda cid: ["A"])
    monkeypatch.setattr(bot_context, "assigned_route_ids", lambda cid: ["r9"])
    monkeypatch.setattr(tenant, "company_routes", lambda name: ["r1"])

    scope = scope_mod.scope_for_chat(555)
    assert scope["is_admin"] is False
    assert scope["company"] == "A"
    assert scope["routes"] == ["r1", "r9"]
    assert scope["deny"] is False


def test_scope_for_chat_admin_is_global(monkeypatch):
    from bm_automation.app.notifications.ops import roles
    monkeypatch.setattr(roles, "resolve_role", lambda cid: roles.Role.ADMIN)
    assert scope_mod.scope_for_chat(1) == {"is_admin": True}


# ------------------------------------------------------------- DB tool

def test_db_routes_filtered_by_scope(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    out = asyncio.run(db_tool.DBTool().execute("get_routes"))
    assert [r["external_id"] for r in out["routes"]] == ["r1"]


def test_db_vehicles_filtered_by_scope(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    out = asyncio.run(db_tool.DBTool().execute("get_vehicles"))
    assert [v["external_id"] for v in out["vehicles"]] == ["v1"]


def test_db_drivers_filtered_by_scope(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    out = asyncio.run(db_tool.DBTool().execute("get_drivers"))
    assert [d["external_id"] for d in out["drivers"]] == ["d1"]


def test_db_foreign_route_denied(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})

    tool = db_tool.DBTool()
    denied = asyncio.run(tool.execute("get_vehicles", route_id="r2"))
    assert denied["scope_denied"] is True
    assert denied["count"] == 0

    foreign_driver = asyncio.run(tool.execute("get_work_logs",
                                              driver_id="d2"))
    assert foreign_driver["scope_denied"] is True


def test_db_raw_sql_and_system_actions_denied():
    from bm_automation.app.myai.tools import db as db_tool
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    tool = db_tool.DBTool()
    for action in ("query", "get_errors", "get_reports"):
        out = asyncio.run(tool.execute(action))
        assert out["scope_denied"] is True


def test_db_period_filter_injects_scope_routes():
    from bm_automation.app.myai.tools import db as db_tool
    scope_mod.set_scope({"company": "A", "routes": ["r1", "r2"]})
    tool = db_tool.DBTool()
    f = asyncio.run(tool._build_period_filter(date="2026-10-05"))
    assert f["route"] == "r1 r2"
    f_own = asyncio.run(tool._build_period_filter(
        date="2026-10-05", route_id="r2"))
    assert f_own["route"] == "r2"
    f_foreign = asyncio.run(tool._build_period_filter(
        date="2026-10-05", route_id="r9"))
    assert f_foreign["route"] == "__scope_deny__"


def test_db_admin_has_no_filters(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    out = asyncio.run(db_tool.DBTool().execute("get_routes"))
    assert {r["external_id"] for r in out["routes"]} == {"r1", "r2"}


def test_db_empty_scope_is_fail_closed(storage, monkeypatch):
    from bm_automation.app.myai.tools import db as db_tool
    _seed(storage)
    monkeypatch.setattr(db_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A"})  # routes resolve bo'lmadi
    out = asyncio.run(db_tool.DBTool().execute("get_routes"))
    assert out["count"] == 0


# ------------------------------------------------------------ search tool

def test_search_routes_scoped(storage, monkeypatch):
    from bm_automation.app.myai.tools import search as search_tool
    _seed(storage)
    monkeypatch.setattr(search_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    out = asyncio.run(search_tool.SearchTool().execute("find", query="A"))
    assert {r["external_id"] for r in out["routes"]} == {"r1"}
    all_out = asyncio.run(search_tool.SearchTool().execute("drivers"))
    assert {d["external_id"] for d in all_out["drivers"]} == {"d1"}


def test_search_foreign_route_denied(storage, monkeypatch):
    from bm_automation.app.myai.tools import search as search_tool
    _seed(storage)
    monkeypatch.setattr(search_tool, "get_storage", lambda: storage)
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    out = asyncio.run(search_tool.SearchTool().execute(
        "vehicles", route_id="r2"))
    assert out["scope_denied"] is True
    assert out["count"] == 0


# ----------------------------------------------------------- monthly tool

def test_monthly_apply_scope(monkeypatch):
    from bm_automation.app.myai.tools.monthly import MonthlyTool
    tool = MonthlyTool()
    monkeypatch.setattr(tool, "_scope_routes", lambda: ["r1", "r2"])
    assert tool._apply_scope({"month": "2026-10"})["route"] == "r1 r2"
    assert tool._apply_scope({"month": "2026-10"}, route_id="r2")["route"] == "r2"
    assert tool._apply_scope({}, route_id="r9") is None
    monkeypatch.setattr(tool, "_scope_routes", lambda: [])
    assert tool._apply_scope({})["route"] == "__scope_deny__"
    monkeypatch.setattr(tool, "_scope_routes", lambda: None)
    assert tool._apply_scope({"month": "2026-10"}) == {"month": "2026-10"}


# --------------------------------------------------------- dtransport tool

def test_dtransport_guard():
    from bm_automation.app.myai.tools.dtransport import DTransportTool
    scope_mod.set_scope({"company": "A", "routes": ["r1"]})
    assert DTransportTool._scope_denial({"route_id": "r2"}) is not None
    assert DTransportTool._scope_denial({"route_id": "r1"}) is None
    assert DTransportTool._scope_denial({}) is not None
    scope_mod.set_scope({"is_admin": True})
    assert DTransportTool._scope_denial({"route_id": "r2"}) is None


# ------------------------------------------------------------- event bus

def test_event_bus_allow_filter():
    from bm_automation.app.myai.events import EventBus

    bus = object.__new__(EventBus)
    bus._init_bus()
    bus.emit("task.started", task_id="t1")
    bus.emit("task.started", task_id="t2")
    allowed = bus.recent(50, allow=lambda ev: ev.task_id == "t1")
    assert [e["task_id"] for e in allowed] == ["t1"]


# ---------------------------------------------------------- state: company

class _FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        return 1


class _FakeStorage:
    enabled = True

    def __init__(self):
        self.db = _FakeDB()
        self.queries = []

    def query(self, sql, params=(), limit=None):
        self.queries.append((sql, tuple(params)))
        return []


def test_create_task_stores_company(monkeypatch):
    from bm_automation.app.myai import state
    fake = _FakeStorage()
    monkeypatch.setattr(state, "get_storage", lambda: fake)
    task = state.create_task("hisobot", params={"scope": {"company": "A"}},
                             company="A")
    sql, params = fake.db.calls[0]
    assert "company" in sql
    assert "A" in params
    assert task["company"] == "A"


def test_list_and_count_tasks_filter_company(monkeypatch):
    from bm_automation.app.myai import state
    fake = _FakeStorage()
    monkeypatch.setattr(state, "get_storage", lambda: fake)
    state.list_tasks(company="A")
    state.count_tasks(company="A")
    for sql, params in fake.queries:
        assert "company = %s" in sql
        assert "A" in params


# ------------------------------------------------------- dashboard wiring

def test_dashboard_task_ownership_helpers():
    from bm_automation.app.dashboard.routers import system
    assert system._myai_task_owned({"company": "A"}, "")
    assert system._myai_task_owned({"company": "A"}, "A")
    assert not system._myai_task_owned({"company": "B"}, "A")
    assert not system._myai_task_owned({}, "A")
    assert not system._myai_task_owned(None, "A")


def test_dashboard_foreign_task_404(monkeypatch):
    from fastapi import HTTPException
    from bm_automation.app.dashboard.routers import system
    from bm_automation.app.myai import state

    monkeypatch.setattr(state, "get_task",
                        lambda tid: {"task_id": tid, "company": "B"})
    with pytest.raises(HTTPException) as exc:
        system._myai_require_owned("t1", "A")
    assert exc.value.status_code == 404
    assert system._myai_require_owned("t1", "B")["company"] == "B"


def test_bot_bridge_passes_chat_scope(monkeypatch):
    from bm_automation.app.notifications.ops import myai_bridge
    from bm_automation.app.myai import scope as scope_mod2
    from bm_automation.app.myai import state

    captured = {}

    def _close(coro):
        coro.close()

    def fake_create(user_request, route="", date="", params=None,
                    company=""):
        captured.update(params=params, company=company)
        return {"task_id": "t1"}

    class _Future:
        def result(self, timeout=None):
            return {"ok": True, "result": {"response": "ok"}}

    monkeypatch.setattr(state, "create_task", fake_create)
    monkeypatch.setattr(myai_bridge, "_get_loop", lambda: object())
    monkeypatch.setattr(
        myai_bridge.asyncio, "run_coroutine_threadsafe",
        lambda coro, loop: (_close(coro), _Future())[1])
    monkeypatch.setattr(scope_mod2, "scope_for_chat",
                        lambda cid: {"is_admin": False, "company": "A",
                                     "companies": ["A"], "routes": ["r1"]})

    resp = myai_bridge.ask_myai("hisobot", chat_id=555)
    assert resp["ok"] is True
    assert captured["company"] == "A"
    assert captured["params"]["scope"]["routes"] == ["r1"]
    assert captured["params"]["chat_id"] == "555"
