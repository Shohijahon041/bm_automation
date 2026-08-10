"""Transport Operations Bot testlari: rollar, render, dispatch, problems."""

import pytest

from bm_automation.app.db.base import Database
from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus
from bm_automation.app.db.storage import storage_for
from bm_automation.app.dashboard.metrics import Metrics
from bm_automation.app.notifications.ops import context, dispatch, kb, render, roles
from bm_automation.app.notifications.ops.roles import Role, can, resolve_role
from bm_automation.app.notifications.ops.text import DENIED_TEXT


def _tg(**overrides):
    base = {
        "token": "T", "chat_id": "", "driver_chat_id": "",
        "admin_ids": "", "dispatcher_ids": "", "default_role": "viewer",
    }
    base.update(overrides)
    return base


def _seed(storage):
    storage.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    for i, plate in enumerate(("01A001", "01A002", "01A003", "01A004",
                               "01A005", "01A006", "01A007")):
        storage.save_vehicle(external_id=f"v{i + 1}", plate_number=plate,
                             route_id="r1")
    for i, name in enumerate(("Aliyev", "Karimov", "Rasulov", "Olimov",
                              "Yusupov", "Saidov", "Toirov")):
        storage.save_driver(external_id=f"d{i + 1}", full_name=name)

    def trip(vid, drv, time, status, actual=""):
        return TripRecord(date="2026-08-10", route_id="r1", vehicle_id=vid,
                          driver_id=drv, planned_time=time, actual_time=actual,
                          status=status, source=SyncSource.DUTY.value)

    # normal — muammo emas
    storage.save_trip(trip("v1", "d1", "06:00", "ACCEPTED", actual="06:15"))
    # gps (v6) — qabul qilingan, lekin harakat qaydi yo'q
    storage.save_trip(trip("v6", "d6", "07:00", "ACCEPTED"))
    # gps lekin texnik muammosi ham bor (v7) — texnik ustun g'alaba qiladi
    storage.save_trip(trip("v7", "d7", "08:00", "ACCEPTED"))
    storage.save_trip(trip("v7", "d7", "08:30", "REJECTED"))
    # texnik (v1, v3)
    storage.save_trip(trip("v1", "d1", "11:00", "REJECTED"))
    storage.save_trip(trip("v3", "d3", "09:00", "ZERO_MILEAGE"))
    # jadval
    storage.save_trip(trip("v4", "d4", "10:00", "NOT_ACCEPTED"))
    storage.save_trip(trip("v4", "d4", "10:30", "PENDING_ACCESS"))
    # noma'lum status (save_trip normallashtiradi — bevosita yozamiz)
    storage.db.execute(
        "INSERT INTO trips (date, route_id, vehicle_id, driver_id, planned_time,"
        " status, source, last_synced_at, data, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-08-10", "r1", "v5", "d5", "12:00", "BOGUS", "MANUAL",
         "", "{}", "2026-08-10T00:00:00+00:00", "2026-08-10T00:00:00+00:00"))
    return storage


@pytest.fixture()
def storage(tmp_path):
    db = Database(driver="sqlite", path=str(tmp_path / "ops.db"))
    st = storage_for(db)
    return _seed(st)


@pytest.fixture()
def met(storage):
    return Metrics(storage)


# ------------------------------------------------------------------- roles

def test_no_config_everyone_admin(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings", lambda: _tg())
    assert resolve_role(123) is Role.ADMIN
    assert resolve_role(None) is Role.ADMIN


def test_legacy_chat_id_is_admin(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(chat_id="111", driver_chat_id="222"))
    assert resolve_role(111) is Role.ADMIN
    assert resolve_role(222) is Role.ADMIN


def test_configured_roles_and_default(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111, 222",
                                    dispatcher_ids="333", default_role="viewer"))
    assert resolve_role(111) is Role.ADMIN
    assert resolve_role(222) is Role.ADMIN
    assert resolve_role(333) is Role.DISPATCHER
    assert resolve_role(444) is Role.VIEWER


def test_capabilities():
    assert can(Role.ADMIN, "sync")
    assert not can(Role.DISPATCHER, "sync")
    assert not can(Role.VIEWER, "sync")
    assert can(Role.DISPATCHER, "export")
    assert not can(Role.VIEWER, "export")
    assert can(Role.VIEWER, "view")


# ------------------------------------------------------------- problems

def test_problems_buckets(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    p = met.problems(f)
    c = p["counts"]
    assert c["gps"] == 1        # v6
    assert c["technical"] == 3  # v1, v3, v7
    assert c["schedule"] == 2   # 2 trip
    assert c["unknown"] == 1    # BOGUS
    assert c["gps"] + c["technical"] + c["schedule"] + c["unknown"] == 7

    assert p["items"]["gps"][0]["vehicle"] == "01A006"
    assert p["items"]["unknown"][0]["status"] == "BOGUS"


# ---------------------------------------------------------------- render

@pytest.fixture()
def patch_met(monkeypatch, storage):
    monkeypatch.setattr(render, "_met", lambda: Metrics(storage))
    monkeypatch.setattr(render, "get_storage", lambda: storage)


def test_render_today(patch_met):
    text, kb_ = render.today({"date": "2026-08-10"})
    assert "AVTOBUSLAR" in text and "REYSLAR" in text and "MUAMMOLAR" in text
    assert "7 jami" in text
    labels = [b["text"] for row in kb_["inline_keyboard"] for b in row]
    assert any(l.startswith("⚠️ GPS") for l in labels)
    assert any(l.startswith("⚠️ texnik") for l in labels)
    assert any(d.startswith("nav:") for d in
               [b["callback_data"] for row in kb_["inline_keyboard"] for b in row])


def test_render_routes_vehicles_drivers(patch_met):
    assert "10-yo'nalish" in render.routes({"date": "2026-08-10"})[0]
    assert "01A001" in render.vehicles({"date": "2026-08-10"})[0]
    assert "Aliyev" in render.drivers({"date": "2026-08-10"})[0]


def test_render_trips(patch_met):
    text, _ = render.trips({"date": "2026-08-10"})
    assert "Jami: 9 ta" in text


def test_render_problems(patch_met):
    text, _ = render.problems({"date": "2026-08-10"})
    assert "GPS (1)" in text and "texnik (3)" in text


def test_render_problem_category(patch_met):
    text, kb_ = render.problem_category("technical", {"date": "2026-08-10"})
    assert "TEXNIK MUAMMOLAR" in text
    assert "Jami: 3 ta" in text
    assert "01A001" in text and "01A003" in text
    rows = [b for row in kb_["inline_keyboard"] for b in row]
    assert rows[-1]["callback_data"] == "nav:problems"
    assert render.problem_category("gps", {"date": "2026-08-10"})[0].find("01A006") > -1


def test_render_reports_errors(patch_met, storage):
    storage.record_error(source="x", message="HTTP 500")
    assert "XATOLAR" in render.errors()[0]
    assert "HISOBOTLAR" in render.reports()[0]


def test_render_status_injected(patch_met):
    sysd = {
        "api": {"ok": True, "status": 200, "ms": 42},
        "database": {"ok": True, "driver": "sqlite"},
        "telegram": {"configured": True},
        "scheduler": {"status": "OK", "last_run": "2026-08-10T10:00:00+00:00"},
        "last_sync": "2026-08-10T10:00:00+00:00",
        "last_error": None,
    }
    text, _ = render.status(sysd)
    assert "TIZIM HOLATI" in text
    assert "sqlite" in text


def test_render_settings(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", dispatcher_ids="222"))
    assert "🛡 ADMIN" in render.settings_text(111)
    assert "👀 VIEWER" in render.settings_text(999)


# ------------------------------------------------- legacy lazy imports

def test_legacy_lazy_imports_resolve():
    """legacy.py funksiyalari ichidagi relative importlar chuqurligi to'g'ri."""
    import importlib

    targets = (
        "bm_automation.app.services.daily_service",
        "bm_automation.app.core.profiles",
        "bm_automation.app.services.export_service",
        "bm_automation.app.api.client",
        "bm_automation.app.config.settings",
        "bm_automation.app.auth.browser_login",
        "bm_automation.app.services.excel_fill_service",
        "bm_automation.app.notifications.telegram",
    )
    for t in targets:
        importlib.import_module(t)


# --------------------------------------------------------------- dispatch

@pytest.fixture()
def chat(monkeypatch, tmp_path):
    sent = []

    def fake_reply(chat_id, text, reply_markup=None):
        sent.append({"chat_id": chat_id, "text": text,
                     "reply_markup": reply_markup})

    monkeypatch.setattr(dispatch, "reply", fake_reply)
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    st = _seed(storage_for(
        Database(driver="sqlite", path=str(tmp_path / "chat.db"))))
    monkeypatch.setattr(render, "_met", lambda: Metrics(st))
    monkeypatch.setattr(render, "get_storage", lambda: st)
    return sent


@pytest.fixture()
def admin(chat, monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111"))
    return chat


@pytest.fixture()
def viewer(chat, monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", default_role="viewer"))
    return chat


def test_dispatch_viewer_sync_denied(viewer):
    dispatch.handle_message(999, "/sync")
    assert viewer[-1]["text"] == DENIED_TEXT


def test_dispatch_admin_sync_confirm(admin):
    dispatch.handle_message(111, "/sync")
    rows = admin[-1]["reply_markup"]["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "sync:confirm"


def test_dispatch_export_viewer_denied(viewer):
    dispatch.handle_message(999, "/export csv routes")
    assert viewer[-1]["text"] == DENIED_TEXT


def test_dispatch_export_admin_starts(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "start_export",
                        lambda cid, fmt, scope: calls.append((cid, fmt, scope)))
    dispatch.handle_message(111, "/export csv routes")
    assert calls == [(111, "csv", "routes")]


def test_dispatch_today(admin, monkeypatch):
    patch = {"text": "", "kb": None}
    monkeypatch.setattr(render, "today",
                        lambda *a, **k: ("TODAY_TEXT", {"inline_keyboard": []}))
    dispatch.handle_message(111, "/today")
    assert admin[-1]["text"] == "TODAY_TEXT"


def test_dispatch_menu_button(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, reply_markup=None: calls.append((cid, t, reply_markup)))
    dispatch.handle_message(111, "🚌 Avtobuslar")
    assert "AVTOBUSLAR" in calls[-1][1]


def test_dispatch_callback_nav(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, reply_markup=None: calls.append((cid, t, reply_markup)))
    dispatch.handle_callback(111, {"id": "q"}, "nav:vehicles")
    assert "AVTOBUSLAR" in calls[-1][1]


def test_dispatch_callback_problem_detail(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    dispatch.handle_callback(111, {"id": "q"}, "prob:technical")
    assert "TEXNIK MUAMMOLAR" in calls[-1][1]


def test_dispatch_callback_problem_unknown_key(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    dispatch.handle_callback(111, {"id": "q"}, "prob:bogus")
    assert len(calls) == 0


def test_dispatch_callback_sync_confirm(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "start_sync",
                        lambda cid: calls.append(cid))
    dispatch.handle_callback(111, {"id": "q"}, "sync:confirm")
    assert calls == [111]


def test_dispatch_callback_sync_denied_viewer(viewer):
    dispatch.handle_callback(999, {"id": "q"}, "sync:confirm")
    assert viewer[-1]["text"] == DENIED_TEXT


def test_parse_export_args():
    assert dispatch._parse_export_args(["/export", "csv", "routes"]) == ("csv", "routes")
    assert dispatch._parse_export_args(["/export"]) == ("xlsx", "all")


def test_parse_trip_filters():
    f = dispatch._trip_filters(["/trips", "2026-08-10", "REJECTED"])
    assert f == {"date": "2026-08-10", "status": "REJECTED"}


# ---------------------------------------------------------------- profiles

@pytest.fixture()
def profiles_env(monkeypatch, tmp_path):
    fake = [
        {"name": "ASL SUNDAY MCHJ", "routeVariantId": "r-asl",
         "routeName": "B-80", "profileId": "p1"},
        {"name": "FERGANATEX", "routeVariantId": "r-fer",
         "routeName": "10-yo'nalish", "profileId": ""},
    ]

    def fake_get(name):
        name = (name or "").lower()
        for p in fake:
            if p["name"].lower() == name:
                return p
        return None

    monkeypatch.setattr(context, "all_profiles", lambda: fake)
    monkeypatch.setattr(context, "get_profile", fake_get)
    monkeypatch.setattr(context, "STATE_FILE", tmp_path / "company.json")
    context._ACTIVE.clear()
    return context


def test_context_profile_routes(profiles_env):
    assert profiles_env.profile_routes("ASL SUNDAY MCHJ") == ["r-asl"]
    assert profiles_env.profile_routes("FERGANATEX") == ["r-fer"]
    assert profiles_env.profile_routes("YOK") == []


def test_context_filters_for(profiles_env):
    assert profiles_env.filters_for(1) == {}
    profiles_env.set_active(1, "ASL SUNDAY MCHJ")
    assert profiles_env.filters_for(1) == {
        "profile": "ASL SUNDAY MCHJ", "route": "r-asl"}
    profiles_env.clear(1)
    assert profiles_env.filters_for(1) == {}


def test_context_set_active_unknown(profiles_env):
    import pytest as _pt
    with _pt.raises(ValueError):
        profiles_env.set_active(1, "BOSHQA FIRMA")


def test_context_persists(profiles_env):
    profiles_env.set_active(7, "FERGANATEX")
    state = profiles_env._load()
    assert state["chats"]["7"] == "FERGANATEX"


def test_short_name():
    assert context.short_name("ASL SUNDAY MCHJ") == "ASL SUNDAY"
    assert context.short_name("FERGANATEX") == "FERGANATEX"


def test_parse_filters_keeps_profile():
    from bm_automation.app.dashboard.metrics import parse_filters
    f = parse_filters({"route": "r-asl", "profile": "ASL SUNDAY MCHJ"})
    assert f["profile"] == "ASL SUNDAY MCHJ"
    assert f["route"] == "r-asl"


def test_trip_where_multiroute(met):
    where, params = met._trip_where({"route": "r1 r2"})
    assert "route_id IN" in where
    assert params == ["r1", "r2"]
    where, params = met._trip_where({"route": "r1, r2"})
    assert params == ["r1", "r2"]


def test_vehicles_route_filtered(met):
    rows = met.vehicles({"date": "2026-08-10", "route": "r1"})
    assert rows and all(v["route_id"] == "r1" for v in rows)


def test_render_profiles_kb(profiles_env):
    text, kb_ = render.profiles({})
    assert "FIRMALAR" in text and "ASL SUNDAY" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert "prof:ASL SUNDAY MCHJ" in datas
    assert "prof:FERGANATEX" in datas
    assert "prof:__all__" not in datas  # faol yo'q — chiqish tugmasi ko'rinmaydi


def test_render_today_profile_bar(profiles_env, patch_met):
    f = {"date": "2026-08-10", "profile": "ASL SUNDAY MCHJ", "route": "r1"}
    text, kb_ = render.today(f)
    assert "🏢 ASL SUNDAY" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert "prof:__all__" in datas


def test_dispatch_callback_prof_select(profiles_env, admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "today",
                        lambda *a, **k: ("ASL TODAY", {"inline_keyboard": []}))
    dispatch.handle_callback(111, {"id": "q"}, "prof:ASL SUNDAY MCHJ")
    assert profiles_env.get_active(111) == "ASL SUNDAY MCHJ"
    assert calls[-1][1] == "ASL TODAY"


def test_dispatch_callback_prof_exit(profiles_env, admin, monkeypatch):
    profiles_env.set_active(111, "FERGANATEX")
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "profiles",
                        lambda *a, **k: ("FIRMALAR", {"inline_keyboard": []}))
    dispatch.handle_callback(111, {"id": "q"}, "prof:__all__")
    assert profiles_env.get_active(111) == ""
    assert "FIRMALAR" in calls[-1][1]


def test_dispatch_callback_nav_profiles(profiles_env, admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "profiles",
                        lambda *a, **k: ("FIRMALAR", {"inline_keyboard": []}))
    dispatch.handle_callback(111, {"id": "q"}, "nav:profiles")
    assert "FIRMALAR" in calls[-1][1]


def test_dispatch_today_uses_profile_filter(profiles_env, admin, monkeypatch):
    seen = {}
    def fake_today(filters=None):
        seen["f"] = filters
        return ("T", {"inline_keyboard": []})
    monkeypatch.setattr(render, "today", fake_today)
    profiles_env.set_active(111, "FERGANATEX")
    dispatch.handle_message(111, "/today")
    assert seen["f"] == {"profile": "FERGANATEX", "route": "r-fer"}


def test_dispatch_menu_firmalar(profiles_env, admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "profiles",
                        lambda *a, **k: ("FIRMALAR", {"inline_keyboard": []}))
    dispatch.handle_message(111, "🏢 Firmalar")
    assert "FIRMALAR" in calls[-1][1]


def test_getupdates_allows_all_update_types(monkeypatch):
    """Tugmalar (callback_query) yetib kelishi uchun allowed_updates bo'sh."""
    from bm_automation.app.notifications.ops import ops as ops_mod
    seen = {}

    def fake_tc(method, payload=None, files=None):
        seen["payload"] = payload
        raise SystemExit("stop")

    monkeypatch.setattr(ops_mod, "telegram_call", fake_tc)
    monkeypatch.setattr(ops_mod.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit):
        ops_mod.poll_forever()
    assert seen["payload"]["allowed_updates"] == []


# -------------------------------------------------------------- keyboards

def test_nav_kb_labels():
    kb_ = kb.nav_kb()
    texts = [b["text"] for row in kb_["inline_keyboard"] for b in row]
    for label in ("📊 Dashboard", "🚌 Avtobuslar", "👨‍✈️ Haydovchilar",
                  "🛣 Yo'nalishlar", "📋 Reyslar", "⚠️ Muammolar",
                  "📈 Hisobot", "🔄 Sync", "🏢 Firmalar", "⚙️ Settings"):
        assert label in texts


def test_problems_kb():
    kb_ = kb.problems_kb({"counts": {"gps": 7, "technical": 5,
                                     "schedule": 8, "unknown": 13}})
    texts = [b["text"] for row in kb_["inline_keyboard"] for b in row]
    assert "⚠️ GPS (7)" in texts
    assert "⚠️ texnik (5)" in texts
    assert "⚠️ jadval (8)" in texts
    assert "⚠️ noma'lum (13)" in texts
