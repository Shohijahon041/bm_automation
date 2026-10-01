"""Kunlik sync endpoint'lari + avtomatik kunlik sinxronlash testlari.

Qamrov:
  * /api/sync va /api/sync/monthly — validatsiya xatolari JSON dict (500
    emas), scope cheklovi (non-admin faqat o'z yo'nalishlari), running va
    20s interval guard'lari, status endpoint shakllari;
  * fon oqimi — BMClient/db.sync monkeypatch bilan start → tugash, lines;
  * db_sync.sync_days — yo'nalishli va barcha-profillar yo'llari;
  * sync_auto — soat/kun/backoff guard'lari, holat fayli (state/sync_auto.json),
    xatolikda kun belgilanmasligi;
  * bot_settings auto_sync/auto_sync_hour round-trip.
"""

from __future__ import annotations

import json
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from bm_automation.app.core import bot_settings
from bm_automation.app.dashboard.routers import system as system_mod
from bm_automation.app.db import storage as storage_mod
from bm_automation.app.db import sync as db_sync
from bm_automation.app.db.models import SyncResult
from bm_automation.app.notifications.ops import sync_auto
from tests.sqlite_backend import SQLiteDatabase


# ---------------------------------------------------------------------------
# Fixture'lar
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_post_rate(monkeypatch):
    """POST 429 middleware hisoblagichini har testda tozalaydi (1s interval)."""
    from bm_automation.app.dashboard import deps
    monkeypatch.setattr(deps, "_last_post_at", {})


@pytest.fixture
def client():
    """TestClient — lifespan'siz (tarmoq side-effect yo'q)."""
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


@pytest.fixture
def storage(monkeypatch, tmp_path):
    """SQLite Storage'ni singleton sifatida o'rnatadi."""
    db = SQLiteDatabase(str(tmp_path / "bm_sync.db"))
    st = storage_mod.storage_for(db)
    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", st)
    yield st
    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", None)


@pytest.fixture
def sync_bg(monkeypatch):
    """Sync fon holatlari + cooldown — har testda toza holat."""
    monkeypatch.setattr(system_mod, "_last_sync_at", 0.0)
    daily = {"running": False, "progress": "", "result": None}
    monthly = {"running": False, "progress": "", "result": None}
    monkeypatch.setattr(system_mod, "_DAILY_SYNC_BG", daily)
    monkeypatch.setattr(system_mod, "_MONTHLY_SYNC_BG", monthly)
    yield daily, monthly


@pytest.fixture
def auto_state(monkeypatch, tmp_path):
    """sync_auto'ni izolyatsiya qiladi: holat fayli tmp, soat 00:00, yoniq."""
    monkeypatch.setattr(sync_auto, "STATE_FILE", tmp_path / "sync_auto.json")
    monkeypatch.setattr(sync_auto, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(sync_auto, "_LAST_FAIL_AT", 0.0)
    monkeypatch.setattr(sync_auto, "_RUNNING", False)
    monkeypatch.setattr(bot_settings, "auto_sync", lambda: True)
    monkeypatch.setattr(bot_settings, "auto_sync_hour", lambda: 0)
    yield


def _fake_client(monkeypatch, login_error: Exception | None = None):
    """BMClient o'rniga soxta klient (tarmoqqa tegilmaydi)."""
    class FakeClient:
        def __init__(self):
            self.access_token = "t"
            self.refresh_token = "r"
            self.session = type("S", (), {"headers": {}})()

        def login(self):
            if login_error is not None:
                raise login_error

    monkeypatch.setattr("bm_automation.app.api.client.BMClient", FakeClient)


def _fake_sync(monkeypatch, day_errors: dict | None = None):
    """db_sync.sync_routes/sync_days'ni soxtalashtiradi va chaqiruvlarni qayd etadi."""
    calls: dict = {"routes": 0, "days": []}

    def fake_routes(storage, client, brutto=True):
        calls["routes"] += 1
        return SyncResult(entity="routes", inserted=3)

    def fake_days(storage, client, days, routes=None, progress_cb=None):
        calls["days"].append({"days": list(days), "routes": routes})
        out = []
        for i, d in enumerate(days, 1):
            if progress_cb:
                progress_cb(d, i, len(days))
            out.append(SyncResult(entity="drivers", inserted=1,
                                  error=(day_errors or {}).get(d, "")).as_dict())
        return out

    monkeypatch.setattr(db_sync, "sync_routes", fake_routes)
    monkeypatch.setattr(db_sync, "sync_days", fake_days)
    return calls


def _wait_bg(bg: dict, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while bg["running"] and time.time() < deadline:
        time.sleep(0.02)
    assert not bg["running"], "fon sync tugamadi"


def _wait_running(timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while sync_auto._RUNNING and time.time() < deadline:
        time.sleep(0.02)
    assert not sync_auto._RUNNING, "sync_auto fon oqimi tugamadi"


# ---------------------------------------------------------------------------
# /api/sync — validatsiya (500 emas, JSON dict)
# ---------------------------------------------------------------------------

def test_sync_invalid_date_json_not_500(client, storage, sync_bg):
    r = client.post("/api/sync", json={"from": "2026-13-01", "to": "2026-13-02"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and "Sana formati" in d["error"]


def test_sync_to_before_from(client, storage, sync_bg):
    r = client.post("/api/sync", json={"from": "2026-10-05", "to": "2026-10-01"})
    d = r.json()
    assert d["ok"] is False and "oldin" in d["error"]


def test_sync_storage_disabled(sync_bg, monkeypatch):
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage",
                        lambda: type("Off", (), {"enabled": False})())
    d = system_mod._run_sync({}, {"is_admin": True})
    assert d["ok"] is False and "o'chirilgan" in d["error"]


# ---------------------------------------------------------------------------
# Scope cheklovi
# ---------------------------------------------------------------------------

def test_sync_target_routes_admin():
    routes, err = system_mod._sync_target_routes({"route": "r1"}, {"is_admin": True})
    assert routes == ["r1"] and err is None
    routes, err = system_mod._sync_target_routes({}, {"is_admin": True})
    assert routes == [] and err is None
    # Vergulli ro'yxat (force_route formati) ham qo'llab-quvvatlanadi
    routes, err = system_mod._sync_target_routes({"route": "r1, r2"}, {"is_admin": True})
    assert routes == ["r1", "r2"] and err is None


def test_sync_target_routes_non_admin():
    scope = {"is_admin": False, "routes": ["r2", "r1"]}
    routes, err = system_mod._sync_target_routes({}, scope)
    assert routes == ["r1", "r2"] and err is None
    routes, err = system_mod._sync_target_routes({"route": "r1"}, scope)
    assert routes == ["r1"] and err is None
    # Sessiyadagi vergulli qiymat (ko'p yo'nalishli kompaniya)
    routes, err = system_mod._sync_target_routes({"route": "r2,r1"}, scope)
    assert routes == ["r2", "r1"] and err is None
    routes, err = system_mod._sync_target_routes({"route": "rX"}, scope)
    assert routes == [] and "topilmadi" in err["error"]
    routes, err = system_mod._sync_target_routes({"route": "r1,rX"}, scope)
    assert routes == [] and "topilmadi" in err["error"]


def test_sync_target_routes_empty_scope():
    routes, err = system_mod._sync_target_routes({}, {"is_admin": False, "routes": []})
    assert routes == [] and err and "aniqlanmadi" in err["error"]


def test_sync_non_admin_limited_to_scope(client, storage, sync_bg, monkeypatch):
    calls = _fake_sync(monkeypatch)
    _fake_client(monkeypatch)
    d = system_mod._run_sync({"route": "rX"}, {"is_admin": False, "routes": ["r1"]})
    assert d["ok"] is False and "topilmadi" in d["error"]
    assert calls["days"] == []


# ---------------------------------------------------------------------------
# Guard'lar
# ---------------------------------------------------------------------------

def test_sync_running_guard(sync_bg, storage):
    daily, _ = sync_bg
    daily["running"] = True
    daily["progress"] = "Login..."
    d = system_mod._run_sync({}, {"is_admin": True})
    assert d["ok"] is False and "davom etmoqda" in d["error"]
    assert d["progress"] == "Login..."


def test_sync_interval_guard(sync_bg, storage, monkeypatch):
    monkeypatch.setattr(system_mod, "_last_sync_at", time.monotonic())
    d = system_mod._run_sync({}, {"is_admin": True})
    assert d["ok"] is False and "tez-tez" in d["error"]


def test_sync_status_shapes(sync_bg):
    daily, _ = sync_bg
    assert system_mod._sync_bg_status(daily) == {"ok": True, "running": False}
    daily["running"] = True
    daily["progress"] = "01.10.2026 (1/2)"
    out = system_mod._sync_bg_status(daily)
    assert out["running"] is True and out["progress"] == "01.10.2026 (1/2)"
    daily["running"] = False
    daily["result"] = {"ok": True, "days": 2}
    out = system_mod._sync_bg_status(daily)
    assert out == {"ok": True, "running": False, "days": 2}


def test_fmt_date():
    assert system_mod._fmt_date("2026-10-01") == "01.10.2026"
    assert system_mod._fmt_date("bad") == "bad"


# ---------------------------------------------------------------------------
# Fon oqimi: start → tugash
# ---------------------------------------------------------------------------

def test_sync_daily_start_and_finish(client, storage, sync_bg, monkeypatch):
    daily, _ = sync_bg
    calls = _fake_sync(monkeypatch)
    _fake_client(monkeypatch)
    r = client.post("/api/sync", json={"from": "2026-09-30", "to": "2026-10-01"})
    d = r.json()
    assert d["ok"] is True and d["started"] is True and d["days"] == 2
    _wait_bg(daily)
    res = daily["result"]
    assert res["ok"] is True
    assert res["from"] == "2026-09-30" and res["to"] == "2026-10-01"
    assert res["days"] == 2 and res["lines"]
    assert calls["routes"] == 1
    assert calls["days"][0]["days"] == ["2026-09-30", "2026-10-01"]
    assert calls["days"][0]["routes"] == []
    st = client.get("/api/sync/status").json()
    assert st["ok"] is True and st["running"] is False and st["from"] == "2026-09-30"


def test_sync_daily_day_with_error_marks_result_not_ok(storage, sync_bg, monkeypatch):
    daily, _ = sync_bg
    _fake_sync(monkeypatch, day_errors={"2026-10-01": "API xatosi"})
    _fake_client(monkeypatch)
    system_mod._run_sync({"date": "2026-10-01"}, {"is_admin": True})
    _wait_bg(daily)
    assert daily["result"]["ok"] is False
    assert any("API xatosi" in line for line in daily["result"]["lines"])


def test_sync_monthly_default_month_start(client, storage, sync_bg, monkeypatch):
    monthly_first = date.today().replace(day=1).isoformat()
    _, monthly = sync_bg
    _fake_sync(monkeypatch)
    _fake_client(monkeypatch)
    r = client.post("/api/sync/monthly", json={})
    d = r.json()
    assert d["ok"] is True and d["started"] is True
    assert d["from"] == monthly_first and d["days"] >= 1
    _wait_bg(monthly)
    assert monthly["result"]["ok"] is True
    st = client.get("/api/sync/monthly/status").json()
    assert st["running"] is False and st["from"] == monthly_first


# ---------------------------------------------------------------------------
# db_sync.sync_days
# ---------------------------------------------------------------------------

def test_sync_days_routes_path(storage, monkeypatch):
    monkeypatch.setattr(db_sync, "sync_route_day",
                        lambda s, c, rid, d: [SyncResult(entity="drivers", inserted=1)])
    monkeypatch.setattr(db_sync, "sync_waybills",
                        lambda s, c, rid, f, t: SyncResult(entity="waybills", updated=2))
    monkeypatch.setattr(db_sync, "sync_work_logs",
                        lambda s, d1, d2, rid="": SyncResult(entity="driver_work_logs", inserted=1))
    out = db_sync.sync_days(storage, None, ["2026-10-01"], routes=["r1", "r2"])
    assert [r["entity"] for r in out] == [
        "drivers", "waybills", "driver_work_logs",
        "drivers", "waybills", "driver_work_logs"]


def test_sync_days_all_profiles_path(storage, monkeypatch):
    monkeypatch.setattr(db_sync, "sync_all_profiles",
                        lambda s, c, d: [SyncResult(entity="profiles/x", inserted=5)])
    out = db_sync.sync_days(storage, None, ["2026-10-01", "2026-10-02"], routes=[])
    assert len(out) == 2 and out[0]["inserted"] == 5


def test_sync_days_progress_cb(storage, monkeypatch):
    monkeypatch.setattr(db_sync, "sync_all_profiles",
                        lambda s, c, d: [SyncResult(entity="routes", inserted=1)])
    seen = []
    db_sync.sync_days(storage, None, ["2026-10-01", "2026-10-02"],
                      progress_cb=lambda d, i, n: seen.append((d, i, n)))
    assert seen == [("2026-10-01", 1, 2), ("2026-10-02", 2, 2)]


# ---------------------------------------------------------------------------
# sync_auto
# ---------------------------------------------------------------------------

def test_target_days():
    assert sync_auto._target_days(date(2026, 9, 30)) == ["2026-09-29", "2026-09-30"]
    assert sync_auto._target_days(date(2026, 10, 5)) == [
        "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]


def test_auto_sync_disabled(auto_state, storage, monkeypatch):
    monkeypatch.setattr(bot_settings, "auto_sync", lambda: False)
    sync_auto.check_and_sync()
    assert not sync_auto.STATE_FILE.exists()


def test_auto_sync_before_hour(auto_state, storage, monkeypatch):
    class FakeTime:
        @staticmethod
        def time():
            return time.time()

        @staticmethod
        def localtime():
            # 2026-10-01 03:00 — soat chiqmaguncha kutish
            return time.struct_time((2026, 10, 1, 3, 0, 0, 3, 274, 0))

    monkeypatch.setattr(sync_auto, "time", FakeTime)
    monkeypatch.setattr(bot_settings, "auto_sync_hour", lambda: 6)
    sync_auto.check_and_sync()
    assert not sync_auto.STATE_FILE.exists()


def test_auto_sync_date_guard(auto_state, storage):
    sync_auto.STATE_FILE.write_text(
        json.dumps({"date": date.today().isoformat()}), encoding="utf-8")
    sync_auto.check_and_sync()
    assert not sync_auto._RUNNING


def test_auto_sync_fail_backoff_guard(auto_state, storage, monkeypatch):
    monkeypatch.setattr(sync_auto, "_LAST_FAIL_AT", time.time())
    sync_auto.check_and_sync()
    assert not sync_auto._RUNNING


def test_auto_sync_runs_and_marks_state(auto_state, storage, monkeypatch):
    steps: dict = {"statuses": 0, "profiles": 0, "routes": 0, "days": None}

    def fake_statuses(s):
        steps["statuses"] += 1
        return SyncResult(entity="statuses", inserted=1)

    def fake_profiles(s, profiles=None):
        steps["profiles"] += 1
        return SyncResult(entity="profiles", inserted=1)

    def fake_routes(s, c, brutto=True):
        steps["routes"] += 1
        return SyncResult(entity="routes", inserted=1)

    monkeypatch.setattr(db_sync, "sync_statuses", fake_statuses)
    monkeypatch.setattr(db_sync, "sync_profiles", fake_profiles)
    monkeypatch.setattr(db_sync, "sync_routes", fake_routes)

    def fake_days(s, c, days, routes=None, progress_cb=None):
        steps["days"] = list(days)
        return [SyncResult(entity="profiles/x", inserted=2).as_dict()]

    monkeypatch.setattr(db_sync, "sync_days", fake_days)
    _fake_client(monkeypatch)

    sync_auto.check_and_sync()
    _wait_running()

    state = json.loads(sync_auto.STATE_FILE.read_text(encoding="utf-8"))
    assert state["date"] == date.today().isoformat()
    assert state["errors"] == 0
    assert steps["days"] == sync_auto._target_days()
    assert steps["statuses"] == 1 and steps["profiles"] == 1 and steps["routes"] == 1


def test_auto_sync_failure_not_marked(auto_state, storage, monkeypatch):
    _fake_client(monkeypatch, login_error=RuntimeError("login timeout"))
    sync_auto.check_and_sync()
    _wait_running()
    assert sync_auto._LAST_FAIL_AT > 0
    if sync_auto.STATE_FILE.exists():
        assert json.loads(sync_auto.STATE_FILE.read_text(
            encoding="utf-8")).get("date") != date.today().isoformat()


# ---------------------------------------------------------------------------
# bot_settings
# ---------------------------------------------------------------------------

def test_auto_sync_settings_roundtrip():
    assert bot_settings.auto_sync() is True       # standart: yoniq
    assert bot_settings.auto_sync_hour() == 6     # standart: 6:00
    assert bot_settings.set_auto_sync(False) is False
    assert bot_settings.auto_sync() is False
    assert bot_settings.set_auto_sync_hour(9) == 9
    assert bot_settings.auto_sync_hour() == 9
    assert bot_settings.set_auto_sync_hour(99) == 23
    assert bot_settings.set_auto_sync_hour("bad") == 6


def test_settings_api_auto_sync_roundtrip(client):
    out = client.get("/api/settings").json()
    assert out["global"]["auto_sync"] is True
    assert out["global"]["auto_sync_hour"] == 6
    res = client.post("/api/settings",
                      json={"auto_sync": False, "auto_sync_hour": 7}).json()
    assert res["ok"] is True
    assert "auto_sync" in res["changed"] and "auto_sync_hour" in res["changed"]
    out2 = client.get("/api/settings").json()
    assert out2["global"]["auto_sync"] is False
    assert out2["global"]["auto_sync_hour"] == 7
