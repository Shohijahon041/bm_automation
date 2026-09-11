"""Oylik sayt↔baza tekshirish (`services.verify`) testlari."""

from datetime import date
from types import SimpleNamespace

import pytest

from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import assistant, openrouter, render, roles
from bm_automation.app.services import verify
from tests.sqlite_backend import SQLiteDatabase


def _storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "verify.db"))
    st = storage_for(db)
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    return st


def _seed_route_daily(st, rid, rows):
    for r in rows:
        vid = r.get("vehicle_id", "v1")
        st.db.execute(
            "INSERT INTO route_daily (date, route_id, vehicle_id, vehicle_number,"
            " trip_fact, trip_passed, trip_approved, distance_fact, working_day,"
            " created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["date"], rid, vid, r.get("vehicle_number", vid),
             r.get("trip_fact", 0), r.get("trip_passed", 0),
             r.get("trip_approved", 0), r.get("distance_fact", 0.0),
             r.get("working_day", 0),
             "2026-08-10T00:00:00+00:00", "2026-08-10T00:00:00+00:00"))


class _FakeClient:
    auto_relogin = True

    def __init__(self, fail_profile: str | None = None):
        self.access_token = "main-access"
        self.refresh_token = "main-refresh"
        self.session = SimpleNamespace(headers={})
        self.logins = []
        self._fail = fail_profile

    def login(self, force=False):
        self.logins.append("main")

    def login_by_profile(self, pid):
        self.logins.append(pid)
        if self._fail and pid == self._fail:
            raise RuntimeError(f"token xato: {pid}")
        self.access_token = f"tok-{pid}"
        self.refresh_token = f"ref-{pid}"
        self.session.headers["Authorization"] = f"Bearer tok-{pid}"


class _FakeGross:
    def __init__(self, client):
        self.client = client

    def route(self, rid, frm, to):
        if rid == "r-empty":
            raise RuntimeError("TRIP_NOT_FOUND")
        # Haqiqiy API faqat so'ralgan oraliqdagi kunlarni qaytaradi.
        # 2026-08-01 so'ralgan oraliqdan tashqarida bo'lsa ma'lumot yo'q.
        if not (frm <= "2026-08-01" <= to):
            return {"tripFactSum": 0, "distanceFactSum": 0.0,
                    "workingDaySum": 0, "dates": {}}
        return {
            "tripFactSum": 40,
            "distanceFactSum": 500.0,
            "workingDaySum": 2,
            "dates": {
                "2026-08-01": {"vehicles": [
                    {"tripFact": 20, "distanceFact": 250.0, "workingDay": 1},
                    {"tripFact": 20, "distanceFact": 250.0, "workingDay": 1},
                ]},
            },
        }


@pytest.fixture()
def ver_env(tmp_path, monkeypatch):
    st = _storage(tmp_path)
    profiles = [
        {"name": "ASL SUNDAY MCHJ", "routeVariantId": "r-asl",
         "routeName": "B-80", "profileId": "p1"},
        {"name": "FERGANATEX", "routeVariantId": "r-fer",
         "routeName": "10-yo'nalish", "profileId": ""},
        {"name": "EMPTY FIRMA", "routeVariantId": "r-empty",
         "routeName": "N-0", "profileId": ""},
    ]
    monkeypatch.setattr(verify, "get_storage", lambda: st)
    monkeypatch.setattr(verify, "all_profiles", lambda: profiles)
    monkeypatch.setattr(verify, "BMClient", _FakeClient)
    monkeypatch.setattr(verify, "GrossRepository", _FakeGross)
    return st


# ------------------------------------------------------------- month bounds

def test_month_bounds():
    # O'tgan to'liq oy — oy boshidan oxirigacha
    assert verify._month_bounds("2026-07") == ("2026-07-01", "2026-07-31")
    # Kelajakdagi oy — bugun bilan chegaralanadi (bo'sh)
    today = date.today().isoformat()
    assert verify._month_bounds("2030-12") == (today, today)


def test_chunks_split_over_30_days():
    out = verify._chunks("2026-07-01", "2026-08-16")
    assert len(out) == 2
    assert out[0] == ("2026-07-01", "2026-07-30")
    assert out[1] == ("2026-07-31", "2026-08-16")
    assert verify._chunks("2026-08-01", "2026-08-16") == [("2026-08-01",
                                                           "2026-08-16")]


def test_routes_filter(ver_env):
    assert verify._routes({"route": "r-asl"}) == ["r-asl"]
    assert verify._routes({"route": "r-asl r-fer"}) == ["r-asl", "r-fer"]
    assert verify._routes({}) == ["r-asl", "r-fer", "r-empty"]


# ---------------------------------------------------------- db monthly

def test_db_monthly_aggregates(ver_env):
    _seed_route_daily(ver_env, "r1", [
        {"date": "2026-08-01", "trip_fact": 20, "distance_fact": 250.0,
         "working_day": 1},
        {"date": "2026-08-02", "trip_fact": 30, "distance_fact": 300.0,
         "working_day": 2},
    ])
    ver_env.save_schedule(date="2026-08-01", route_id="r1", graph_name="P1",
                          driver_id="d1", vehicle_id="v1", trip_count=3)
    ver_env.save_schedule(date="2026-08-02", route_id="r1", graph_name="P2",
                          driver_id="d2", vehicle_id="v1", trip_count=4)
    db = verify._db_monthly(ver_env, "r1", "2026-08-01", "2026-08-31")
    assert db["db_trips"] == 50
    assert db["db_km"] == 550.0
    assert db["db_work_days"] == 3
    assert db["db_waybills"] == 2
    assert db["db_plan"] == 7  # schedules trip_count

    # Boshqa route yoki davrga tegmaslik
    db2 = verify._db_monthly(ver_env, "r1", "2026-09-01", "2026-09-30")
    assert db2["db_trips"] == 0


# --------------------------------------------------------------- main flow

def test_verify_month_ok(ver_env, monkeypatch):
    _seed_route_daily(ver_env, "r-asl", [
        {"date": "2026-08-01", "trip_fact": 20, "distance_fact": 250.0,
         "working_day": 1, "vehicle_id": "v1"},
        {"date": "2026-08-01", "trip_fact": 20, "distance_fact": 250.0,
         "working_day": 1, "vehicle_id": "v2"},
    ])
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    assert data["ok"] is True
    assert len(data["results"]) == 1
    r = data["results"][0]
    assert r["status"] == "OK"
    assert r["site"]["trips"] == 40 and r["db"]["db_trips"] == 40
    assert r["site"]["waybills"] == 2 and r["db"]["db_waybills"] == 2
    assert r["diff"]["trips"] == 0 and r["diff"]["km"] == 0.0
    # Profil tokeni ishlatilishi kerak
    assert r["route_id"] == "r-asl"


def test_verify_month_diff(ver_env):
    _seed_route_daily(ver_env, "r-asl", [
        {"date": "2026-08-01", "trip_fact": 30, "distance_fact": 400.0,
         "working_day": 1},
    ])
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    r = data["results"][0]
    assert r["status"] == "DIFF"
    assert r["diff"]["trips"] == 10
    assert r["diff"]["km"] == 100.0


def test_verify_month_site_error(ver_env):
    data = verify.verify_month(month="2026-08", filters={"route": "r-empty"})
    r = data["results"][0]
    assert r["status"] == "ERROR"
    assert r["diff"] == {}
    assert "TRIP_NOT_FOUND" in r["site"].get("error", "")
    # not-found ma'lumot yo'qligi — xato rejimida ham ok=True (o'tkazib yuboriladi)
    assert data["ok"] is True


def test_verify_month_token_error(ver_env, monkeypatch):
    monkeypatch.setattr(verify, "BMClient",
                        lambda: _FakeClient(fail_profile="p1"))
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    r = data["results"][0]
    assert r["status"] == "ERROR"
    assert "token" in r["site"].get("error", "")


def test_verify_month_db_disabled(ver_env, monkeypatch):
    monkeypatch.setattr(verify, "get_storage",
                        lambda: SimpleNamespace(enabled=False))
    data = verify.verify_month(month="2026-08")
    assert data["ok"] is False
    assert "DB" in data["error"]


def test_verify_month_future(ver_env):
    data = verify.verify_month(month="2030-01")
    assert data["ok"] is False
    assert "tugamagan" in data["error"]


# -------------------------------------------------------------- render

def test_render_table_and_ai(ver_env):
    _seed_route_daily(ver_env, "r-asl", [
        {"date": "2026-08-01", "trip_fact": 40, "distance_fact": 500.0,
         "working_day": 2},
    ])
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    text, mk = verify.render(data, ai_text="Baza saytga to'liq mos.")
    assert "OYLIK TEKSHIRISH" in text
    assert "2026-08" in text
    assert "AI TAHLIL" in text
    assert "to'liq mos" in text
    assert "<pre>" in text
    assert mk is None


def test_render_rule_report(ver_env):
    _seed_route_daily(ver_env, "r-asl", [
        {"date": "2026-08-01", "trip_fact": 30, "distance_fact": 400.0,
         "working_day": 1},
    ])
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    text, _ = verify.render(data, ai_text=None)  # AI yo'q — lokal xulosa
    assert "farqli" in text or "mos" in text
    assert "reys +10" in text


def test_render_error(ver_env):
    text, mk = verify.render({"ok": False, "error": "DB rejimi o'chirilgan"})
    assert "DB rejimi o'chirilgan" in text
    assert mk is None


# --------------------------------------------------------------- digest/AI

def test_digest_lines(ver_env, monkeypatch):
    import bm_automation.app.dashboard.metrics as metrics_mod
    from bm_automation.app.notifications.ops import self_review

    class _FakeMetrics:
        def __init__(self, storage=None):
            pass

        def system(self):
            return {"api": {"ok": True}, "database": {"ok": True},
                    "last_sync": "2026-08-10T08:00:00+00:00"}

    monkeypatch.setattr(metrics_mod, "Metrics", _FakeMetrics)
    monkeypatch.setattr(self_review, "analyze",
                        lambda days=None: {"error_total": 3, "failures": 1,
                                           "top_errors": [
                                               {"source": "bot", "norm": "HTTP 500",
                                                "n": 2}]})
    _seed_route_daily(ver_env, "r-asl", [
        {"date": "2026-08-01", "trip_fact": 40, "distance_fact": 500.0,
         "working_day": 2},
    ])
    data = verify.verify_month(month="2026-08", filters={"route": "r-asl"})
    dg = verify.digest(data)
    assert "ASL SUNDAY" in dg
    assert "sayt 40 reys" in dg
    assert "Tizim" in dg
    assert "Xatolar (14 kun): 3" in dg


def test_ai_report_none_without_key(ver_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: False)
    assert verify.ai_report("digest") is None


def test_ai_report_uses_llm(ver_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(openrouter, "complete",
                        lambda system, user, max_tokens=600: "  Baza saytga mos.  ")
    assert verify.ai_report("digest") == "Baza saytga mos."


def test_ai_report_error_none(ver_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)

    def boom(system, user, max_tokens=600):
        raise RuntimeError("API down")

    monkeypatch.setattr(openrouter, "complete", boom)
    assert verify.ai_report("digest") is None


# ------------------------------------------------------ assistant intents

@pytest.fixture()
def ai_env(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    monkeypatch.setattr(assistant, "_met", lambda: _MetProxy(st))
    monkeypatch.setattr(render, "_met", lambda: _MetProxy(st))
    monkeypatch.setattr(render, "get_storage", lambda: st)
    monkeypatch.setattr(roles, "telegram_settings", lambda: {
        "token": "T", "chat_id": "", "driver_chat_id": "",
        "admin_ids": "111", "dispatcher_ids": "", "manager_ids": "",
        "default_role": "viewer"})
    monkeypatch.setattr(openrouter, "configured", lambda: False)
    return st


class _MetProxy:
    def __init__(self, storage):
        from bm_automation.app.dashboard.metrics import Metrics
        self._m = Metrics(storage=storage)

    def __getattr__(self, item):
        return getattr(self._m, item)


def test_assistant_verify_intent(ai_env, monkeypatch):
    out = assistant.assist("Avgust oyi sayt bilan bazani tekshir")
    assert out is not None
    text, kb_ = out
    assert "OYLIK TEKSHIRISH" in text
    assert "2026-08" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert "verify:2026-08" in datas
    assert "verify:cancel" in datas


def test_assistant_run_sync_admin(ai_env, monkeypatch):
    out = assistant.assist("Sinxronlashni boshlang", chat_id=111)
    assert out is not None
    text, kb_ = out
    assert "Sinxronlashni boshlaymi" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert "sync:confirm" in datas


def test_assistant_run_sync_denied(ai_env, monkeypatch):
    out = assistant.assist("Sinxronlashni boshlang", chat_id=999)
    assert out is not None
    assert "ruxsat" in out[0]


def test_assistant_status_intent(ai_env, monkeypatch):
    monkeypatch.setattr(render, "status", lambda *a, **k: ("TIZIM HOLATI TEST",
                                                           {"inline_keyboard": []}))
    out = assistant.assist("Tizim holati qanday?")
    assert out is not None
    assert "TIZIM HOLATI" in out[0]
