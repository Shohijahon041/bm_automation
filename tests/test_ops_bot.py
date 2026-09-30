"""Transport Operations Bot testlari: rollar, render, dispatch, problems."""

import pytest

from datetime import date

from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus
from bm_automation.app.db.storage import storage_for
from bm_automation.app.dashboard.metrics import Metrics
from bm_automation.app.notifications.ops import (assistant, context, daily_summary,
                                                  dispatch, driver_entry, kb,
                                                  openrouter, planning, render,
                                                  roles, self_review)
from bm_automation.app.notifications.ops.roles import (Role, allowed_ids,
                                                        can, is_allowed,
                                                        resolve_role)
from bm_automation.app.notifications.ops.text import DENIED_TEXT
from bm_automation.app.notifications import telegram as tg
from tests.sqlite_backend import SQLiteDatabase


def _tg(**overrides):
    base = {
        "token": "T", "chat_id": "", "driver_chat_id": "",
        "admin_ids": "", "dispatcher_ids": "", "manager_ids": "",
        "default_role": "viewer",
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
    # texnik (v1, v7) — REJECTED; v3 ZERO_MILEAGE texnik/jadval xatosi emas
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
    db = SQLiteDatabase(str(tmp_path / "ops.db"))
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
    assert can(Role.DISPATCHER, "sync")
    assert not can(Role.VIEWER, "sync")
    assert can(Role.DISPATCHER, "export")
    assert not can(Role.VIEWER, "export")
    assert can(Role.VIEWER, "view")
    # Moliyaviy (salary) faqat ADMIN/MANAGER — DISPATCHER'ga emas.
    assert can(Role.ADMIN, "salary")
    assert can(Role.MANAGER, "salary")
    assert not can(Role.DISPATCHER, "salary")
    assert not can(Role.VIEWER, "salary")


def test_is_allowed_no_config_open(monkeypatch):
    """Hech qanday ID sozlanmagan — ochiq rejim: barcha chat'lar kiradi."""
    monkeypatch.setattr(roles, "telegram_settings", lambda: _tg())
    assert is_allowed(123) is True
    assert is_allowed(None) is False


def test_is_allowed_strict_entered_ids(monkeypatch):
    """Qat'iy rejimda faqat sozlangan ID'lar (admin+dispatcher+chat) kiradi."""
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", dispatcher_ids="333",
                                    chat_id="222", driver_chat_id="444",
                                    strict_access="on"))
    for cid in (111, 222, 333, 444):
        assert is_allowed(cid) is True
    assert is_allowed(555) is False


def test_allowed_ids_strict_union(monkeypatch):
    """TG_ALLOWED_IDS qat'iy rejimni yoqadi — boshqa ID manbalari bilan birlashadi."""
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(allowed_ids="999",
                                    admin_ids="111", chat_id="222"))
    assert allowed_ids() == [999, 111, 222]
    assert is_allowed(999) is True
    assert is_allowed(111) is True
    assert is_allowed(222) is True
    assert is_allowed(555) is False


def test_allowed_ids_union_dedup(monkeypatch):
    """Bir xil ID turli manbalardan kelganda dublikat olib tashlanadi."""
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111, 111", chat_id="111",
                                    strict_access="on"))
    assert allowed_ids() == [111]


# ------------------------------------------------------------- problems

def test_problems_buckets(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    p = met.problems(f)
    c = p["counts"]
    assert c["gps"] == 1        # v6
    assert c["technical"] == 2  # v1, v7 (REJECTED); v3 ZERO_MILEAGE emas
    assert c["schedule"] == 2   # 2 trip
    assert c["unknown"] == 1    # BOGUS
    assert c["gps"] + c["technical"] + c["schedule"] + c["unknown"] == 6

    assert p["items"]["gps"][0]["vehicle"] == "01A006"
    assert p["items"]["unknown"][0]["status"] == "BOGUS"
    # ZERO_MILEAGE muammo kategoriyalariga kirmaydi (garajdan chiqish)
    all_tech = {it["vehicle"] for it in p["items"]["technical"]}
    assert "01A003" not in all_tech


# ---------------------------------------------------------------- render

@pytest.fixture()
def patch_met(monkeypatch, storage):
    monkeypatch.setattr(render, "_met", lambda: Metrics(storage))
    monkeypatch.setattr(render, "get_storage", lambda: storage)


def test_render_today(patch_met):
    text, kb_ = render.today({"date": "2026-08-10"})
    assert "AVTOBUSLAR" in text and "REYSLAR" in text and "MUAMMOLAR" in text
    assert "Jami: 7" in text
    assert "<pre>" in text
    assert any(x in text for x in ("✅", "⚠️", "❌"))
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


def test_render_month(patch_met):
    text, kb_ = render.month({"from": "2026-08-10", "to": "2026-08-10"})
    assert "OY TARIXI" in text
    assert "10.08" in text
    assert "JAMI" in text
    assert any(d.startswith("nav:") for d in
               [b["callback_data"] for row in kb_["inline_keyboard"] for b in row])


def test_render_problems(patch_met):
    text, _ = render.problems({"date": "2026-08-10"})
    assert "GPS (1)" in text and "texnik (2)" in text
    assert "01A006" in text


def test_render_problem_category(patch_met):
    text, kb_ = render.problem_category("technical", {"date": "2026-08-10"})
    assert "TEXNIK MUAMMOLAR" in text
    assert "Jami: 2 ta" in text
    assert "01A001" in text and "01A007" in text
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


def test_render_settings_finance_block_gated_by_salary(monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111"))
    admin = render.settings_text(111)
    viewer = render.settings_text(999)
    assert "ELEKTR ENERGIYA" in admin
    assert "116-SON QAROR" in admin
    assert "ELEKTR ENERGIYA" not in viewer
    assert "116-SON QAROR" not in viewer
    # 1 KM NARXI — tijorat ma'lumoti: faqat salary huquqi bor rollar ko'radi
    # (haydovchi/viewer kabi oddiy foydalanuvchilarga yashirin).
    assert "1 KM NARXI" not in viewer


def test_settings_kb_shows_finance_for_salary(monkeypatch):
    from bm_automation.app.notifications.ops import kb
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", default_role="viewer"))
    admin = kb.settings_kb(111)
    viewer = kb.settings_kb(999)
    adata = str(admin)
    vdata = str(viewer)
    assert "settings:elecprice" in adata
    assert "settings:skm" in adata
    assert "settings:elecprice" not in vdata
    assert "settings:skm" not in vdata


def test_settings_set_elec_price_flow(admin, monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    dispatch.handle_message(111, "/settings")
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    dispatch.handle_callback(111, {"id": "q"}, "settings:elecprice")
    assert bot_settings.pending(111) == "elec_price"
    dispatch.handle_message(111, "900")
    assert bot_settings.elec_price() == 900.0
    assert any("900" in m["text"] for m in admin)


def test_settings_set_skm_flow(admin, monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    dispatch.handle_message(111, "/settings")
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    dispatch.handle_callback(111, {"id": "q"}, "settings:skm")
    assert bot_settings.pending(111) == "brutto_skm"
    dispatch.handle_message(111, "18000")
    assert bot_settings.brutto_skm() == 18000.0
    assert any("18 000" in m["text"] for m in admin)


def test_settings_set_route_skm_flow(admin, monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    monkeypatch.setattr(dispatch.context, "filters_for",
                        lambda cid: {"route": "r1"})
    dispatch.handle_message(111, "/settings")
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    dispatch.handle_callback(111, {"id": "q"}, "settings:rskm")
    assert bot_settings.pending(111) == "route_skm:r1"
    dispatch.handle_message(111, "17000")
    assert bot_settings.route_skm("r1") == 17000.0
    assert any("17 000" in m["text"] for m in admin)


def test_settings_audit_tracks_changes(monkeypatch, tmp_path):
    from bm_automation.app.core import bot_settings
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    bot_settings.set_elec_price(850)
    bot_settings.set_elec_price(850)
    bot_settings.set_route_km("r1", 2000)
    rows = bot_settings.audit_log(5)
    assert rows[0]["key"] == "route_km:r1"
    assert rows[0]["old"] == 0.0 and rows[0]["new"] == 2000.0
    assert rows[1]["key"] == "elec_price"
    assert len(rows) == 2, "bir xil ketma-ket qiymat dublikat qilinmasligi kerak"


def test_settings_audit_view(admin, monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    bot_settings.set_elec_price(850)
    text = render.settings_audit_text(111)
    assert "SETTINGS TARIXI" in text
    assert "elec_price" in text


def test_settings_audit_view_denied_for_viewer(viewer):
    text = render.settings_audit_text(999)
    assert "Huquq yo'q" in text


def test_blacklisted_driver_not_resolved_as_driver(monkeypatch, tmp_path):
    """Qora ro'yxatdagi bog'langan haydovchi DRIVER rolini olmaydi."""
    from bm_automation.app.notifications.ops.roles import (
        _DRIVER_CHAT_CACHE, _resolve_driver_role)
    _DRIVER_CHAT_CACHE.clear()
    st = storage_for(SQLiteDatabase(str(tmp_path / "bl.db")))
    st.save_driver(external_id="db", full_name="Qorayev")
    st.save_driver_profile("db", notification_enabled=True)
    st.link_driver_telegram("db", 777)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    assert _resolve_driver_role(777) is not None
    st.save_driver_profile("db", blacklisted=True)
    assert _resolve_driver_role(777) is None


def test_dashboard_settings_get_includes_finance(monkeypatch, tmp_path):
    from bm_automation.app.core import bot_settings
    from bm_automation.app.dashboard import server
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    bot_settings.set_elec_price(850)
    bot_settings.set_brutto_skm(18000)
    h = server.DashboardHandler.__new__(server.DashboardHandler)
    out = h._get_settings()
    assert out["global"]["elec_price"] == 850.0
    assert out["global"]["brutto_skm"] == 18000.0
    assert out["global"]["elec_kwh_per_km"] > 0
    assert out["global"]["audit"]


def test_dashboard_settings_update_finance(monkeypatch, tmp_path):
    from bm_automation.app.core import bot_settings
    from bm_automation.app.dashboard import server
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    h = server.DashboardHandler.__new__(server.DashboardHandler)
    res = h._update_settings({"elec_price": 900, "brutto_skm": 17000})
    assert res["ok"]
    assert bot_settings.elec_price() == 900.0
    assert bot_settings.brutto_skm() == 17000.0


def test_dashboard_settings_update_bad_values(monkeypatch, tmp_path):
    from bm_automation.app.core import bot_settings
    from bm_automation.app.dashboard import server
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    h = server.DashboardHandler.__new__(server.DashboardHandler)
    res = h._update_settings({"elec_price": -5})
    assert not res["ok"]
    res2 = h._update_settings({"brutto_skm": "abc"})
    assert not res2["ok"]


def test_settings_audit_csv(admin, monkeypatch):
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    bot_settings.set_elec_price(850)
    csv = render.settings_audit_csv(111)
    assert csv.startswith("sep=;")
    assert "Key" in csv
    assert "elec_price" in csv
    assert ";850" in csv


def test_settings_audit_csv_denied_for_viewer(viewer):
    assert render.settings_audit_csv(999) == "⛔ Huquq yo'q."


def test_dashboard_export_settings_scope(tmp_path):
    from bm_automation.app.core import bot_settings
    from bm_automation.app.dashboard import export as ex
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    bot_settings.set_brutto_skm(12345)
    data = ex.build_export({}, "xlsx", "settings")
    assert data
    data2 = ex.build_export({}, "csv", "settings")
    assert b"brutto_skm" in data2 or b"12345" in data2


def test_settings_audit_excel_sends_file(admin, monkeypatch):
    """`/settings audit excel` CSV fayl sifatida yuboriladi."""
    from bm_automation.app.core import bot_settings
    bot_settings._reset()
    bot_settings.set_elec_price(850)
    sent = {}
    monkeypatch.setattr("bm_automation.app.notifications.telegram.send_bytes",
                        lambda name, data, caption="", chat_id=None: (
                            sent.update(name=name, data=data), True)[1])
    dispatch.handle_message(111, "/settings audit excel")
    assert sent.get("name", "").startswith("settings_audit_")
    assert sent["name"].endswith(".csv")
    assert sent["data"].startswith(b"\xef\xbb\xbfsep=;"), "utf-8 BOM bo'lishi kerak"


def test_settings_audit_excel_denied(viewer, monkeypatch):
    monkeypatch.setattr("bm_automation.app.notifications.telegram.send_bytes",
                        lambda *a, **k: None)
    dispatch.handle_message(999, "/settings audit excel")
    assert any("Huquq yo'q" in m["text"] for m in viewer)


def test_bot_settings_clean_amount(tmp_path):
    from bm_automation.app.core import bot_settings
    import math as _m
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    assert bot_settings.set_elec_price(float("nan")) == 0.0
    assert bot_settings.set_elec_price(float("inf")) == 0.0
    assert bot_settings.set_elec_price(float("-inf")) == 0.0
    assert bot_settings.set_brutto_skm(-500) == bot_settings.DEFAULT_SKM
    huge = bot_settings.MAX_AMOUNT * 2
    assert bot_settings.set_route_km("r1", huge) == bot_settings.MAX_AMOUNT
    assert bot_settings.route_km("r1") == bot_settings.MAX_AMOUNT
    assert bot_settings.set_route_skm("r1", float("nan")) == 0.0
    assert bot_settings.route_skm("r1") == 0.0
    assert bot_settings.set_route_tariff("r2", float("nan"), float("inf")) == \
        {"no_vat": 0.0, "vat": 0.0}
    assert _m.isfinite(bot_settings.elec_price())


def test_dashboard_export_tariffs_scope(monkeypatch, tmp_path):
    from bm_automation.app.core import bot_settings
    from bm_automation.app.dashboard import export as ex
    bot_settings.STATE_FILE = tmp_path / "s.json"
    bot_settings._reset()
    bot_settings.set_brutto_skm(17000)
    monkeypatch.setattr("bm_automation.app.dashboard.metrics.Metrics._companies",
                        lambda self: {"r1": {"company": "ASL SUNDAY", "route_name": "B-80"}})
    monkeypatch.setattr("bm_automation.app.dashboard.metrics.Metrics._route_names",
                        lambda self: {"r1": "B-80"})
    title, cols, rows = ex._data_for("tariffs",
                                     ex.m.Metrics(), {})
    assert cols[3] == "skm (so'm/km)"
    assert rows and rows[0][0] == "ASL SUNDAY"
    data = ex.build_export({}, "xlsx", "tariffs")
    assert data


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
        SQLiteDatabase(str(tmp_path / "chat.db"))))
    monkeypatch.setattr(render, "_met", lambda: Metrics(st))
    monkeypatch.setattr(render, "get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
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


def test_driver_link_by_contact_share(monkeypatch, tmp_path):
    """Haydovchi 'Raqamni ulashish' tugmasi (contact) bilan bog'lanadi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "contact.db"))))
    st.save_driver_profile("d1", phone="+998901234567",
                           notification_enabled=False)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    monkeypatch.setattr("bm_automation.app.core.bot_users.record_activity",
                        lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, text, markup=None, **kw: sent.append(text))

    dispatch.handle_contact(777, {"phone_number": "+998 90 123 45 67"})
    assert any("Xush kelibsiz" in t for t in sent)
    profile = st.find("driver_profiles", driver_id="d1")
    assert profile["telegram_chat_id"] == "777"
    assert roles.resolve_role(777) is roles.Role.DRIVER


def test_driver_link_by_contact_unknown_phone(monkeypatch, tmp_path):
    """Noma'lum raqam ulashilsa oqim ochiq qoladi (qo'lda yozish mumkin)."""
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "contact2.db"))))
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    monkeypatch.setattr("bm_automation.app.core.bot_users.record_activity",
                        lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, text, markup=None, **kw: sent.append(text))

    dispatch.handle_contact(778, {"phone_number": "+998900000000"})
    assert any("topilmadi" in t for t in sent)
    # oqim ochiq qoladi — keyin to'g'ri raqam yozsa bog'lanadi
    assert 778 in dispatch._DRIVER_LINK_STATE


def test_find_driver_by_phone_formats(tmp_path):
    """Telefon qidiruvi formatdan qat'i nazar ishlaydi."""
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "phone.db"))))
    st.save_driver_profile("d1", phone="+998 93 733 45 60")
    st.save_driver_profile("d2", phone="998941983003")
    for raw in ("937334560", "+998937334560", "998 93 733 45 60",
                "93-733-45-60"):
        row = st.find_driver_by_phone(raw)
        assert row and row["driver_id"] == "d1", raw
    assert st.find_driver_by_phone("941983003")["driver_id"] == "d2"
    assert st.find_driver_by_phone("900000000") is None


def test_driver_self_link_flow(viewer, monkeypatch, tmp_path):
    """Part 2: haydovchi /start → telefon raqami → tizimga bog'lanish."""
    from bm_automation.app.db import get_storage
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "link.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=False)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    with dispatch._DRIVER_LINK_LOCK:
        dispatch._DRIVER_LINK_STATE.discard(999)

    dispatch.handle_message(999, "/start")
    assert viewer, "Haydovchidan telefon raqami so'ralishi kerak"
    assert "902" in viewer[-1]["text"] or "raqam" in viewer[-1]["text"].lower()

    dispatch.handle_message(999, "901234567")
    assert any("Xush kelibsiz" in m["text"] for m in viewer)

    profile = st.find("driver_profiles", driver_id="d1")
    assert profile["telegram_chat_id"] == "999"
    assert profile["notification_enabled"] == 1
    assert roles.resolve_role(999) is roles.Role.DRIVER


def test_driver_entry_notify_prefers_telegram_chat_id(monkeypatch, tmp_path):
    """Kunlik qayd tasdiqlanganda bog'langan haydovchi o'z chat'iga oladi."""
    from bm_automation.app.notifications import telegram as _tg_mod
    from bm_automation.app.notifications.ops import driver_entry
    sent = []
    monkeypatch.setattr(_tg_mod, "send_message",
                        lambda message, chat_id=None, **kw: sent.append(
                            (message, chat_id)) or True)
    st = storage_for(SQLiteDatabase(str(tmp_path / "notify.db")))
    st.save_driver(external_id="d1", full_name="Aliyev")
    st.save_driver_profile("d1", notification_target="998901234",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 555)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch._notify_driver("d1", "log", {"date": "2026-08-10", "km": 120,
                                          "trips": 5, "vehicle": "01A001"})
    assert sent, "Haydovchiga bildirishnoma yuborilishi kerak"
    assert sent[0][1] == "555"


def test_driver_entry_notify_skips_blacklisted(monkeypatch, tmp_path):
    """Qora ro'yxatdagi haydovchiga bildirishnoma yuborilmaydi."""
    st = storage_for(SQLiteDatabase(str(tmp_path / "notify_bl.db")))
    st.save_driver(external_id="d1", full_name="Aliyev")
    st.save_driver_profile("d1", notification_enabled=True)
    st.link_driver_telegram("d1", 555)
    st.save_driver_profile("d1", blacklisted=True)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    res = dispatch._notify_driver("d1", "log", {"date": "2026-08-10",
                                                "km": 120, "trips": 5,
                                                "vehicle": "01A001"})
    assert "qora ro'yxat" in res


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


# ------------------------------------------------------ AI self-review

def test_dispatch_insights_command(admin, monkeypatch):
    monkeypatch.setattr(self_review, "send_now", lambda: "INSIGHTS_TEXT")
    dispatch.handle_message(111, "/insights")
    assert admin[-1]["text"] == "INSIGHTS_TEXT"


def test_dispatch_insights_menu_uz(admin, monkeypatch):
    monkeypatch.setattr(self_review, "send_now", lambda: "INSIGHTS_TEXT")
    dispatch.handle_message(111, "🧠 O'zini tahlil")
    assert admin[-1]["text"] == "INSIGHTS_TEXT"


def test_dispatch_insights_menu_ru(admin, monkeypatch):
    monkeypatch.setattr(self_review, "send_now", lambda: "INSIGHTS_TEXT")
    dispatch.handle_message(111, "🧠 Самоанализ")
    assert admin[-1]["text"] == "INSIGHTS_TEXT"


def test_dispatch_callback_nav_insights(admin, monkeypatch):
    monkeypatch.setattr(self_review, "build_text", lambda: "INSIGHTS_TEXT")
    dispatch.handle_callback(111, {"id": "q"}, "nav:insights")
    assert admin[-1]["text"] == "INSIGHTS_TEXT"


# ----------------------------------------------------------- driver card

def _seed_driver_extras(storage):
    storage.save_driver_profile(
        "d1", km_rate=4000, rating=4.5,
        passport_number="AA 1234567", passport_expiry="2030-01-01",
        license_number="01AB 98765", license_category="B",
        notification_enabled=True)
    storage.save_driver_work_log(date="2026-08-10", driver_id="d1",
                                 vehicle_id="v1", distance_km=120, trip_count=4)
    storage.add_driver_fine(driver_id="d1", date="2026-08-10",
                            amount=20000, reason="Kechikish")


def test_render_driver_card(patch_met, storage):
    _seed_driver_extras(storage)
    text, kb_ = render.driver_card("d1", {"month": "2026-08"})
    assert "HAYDOVCHI KARTASI" in text
    assert "Aliyev" in text
    assert "Passport: ✅" in text and "Guvohnoma: ✅" in text
    assert "🔔 Telegram bildirishnoma" in text
    assert "Brutto" in text and "Jarimalar" in text and "Netto" in text
    assert "20 000" in text and "480 000" in text
    assert "Kechikish" in text
    # Jarima summasi alohida qatorda + alohida blokda ko'rinadi.
    assert "Jarima summasi" in text
    assert "JAMI JARIMA" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert "dlog:d1" in datas and "dfine:d1" in datas
    assert any(d.startswith("nav:") for d in datas)


def test_render_driver_card_unknown(patch_met):
    text, kb_ = render.driver_card("nobody", {"month": "2026-08"})
    assert "topilmadi" in text.lower()


def test_render_driver_card_hides_salary_for_driver(patch_met, storage,
                                                    monkeypatch):
    """DRIVER uchun kartada Brutto/Jarimalar/Netto ko'rinmaydi."""
    _seed_driver_extras(storage)
    monkeypatch.setattr(render, "resolve_role", lambda cid: Role.DRIVER)
    text, _ = render.driver_card("d1", {"month": "2026-08"}, chat_id=555)
    assert "HAYDOVCHI KARTASI" in text
    assert "Brutto" not in text
    assert "Netto" not in text
    assert "Jarimalar" not in text
    # Jarima summasi esa haydovchi o'z profilida ko'radi (faqat umumiy summa).
    assert "Jarima summasi" in text
    assert "20 000" in text
    assert "JAMI JARIMA" in text


def test_render_driver_card_shows_salary_for_admin(patch_met, storage,
                                                   monkeypatch):
    """ADMIN uchun kartada maosh qatorlari saqlanadi."""
    _seed_driver_extras(storage)
    monkeypatch.setattr(render, "resolve_role", lambda cid: Role.ADMIN)
    text, _ = render.driver_card("d1", {"month": "2026-08"}, chat_id=111)
    assert "Brutto" in text and "Jarimalar" in text and "Netto" in text


def test_render_resolve_driver(patch_met):
    assert render.resolve_driver("d1", {"month": "2026-08"}) == "d1"
    assert render.resolve_driver("Aliyev", {"month": "2026-08"}) == "d1"
    assert render.resolve_driver("aliy", {"month": "2026-08"}) == "d1"
    assert render.resolve_driver("nobody", {"month": "2026-08"}) == ""


def test_render_drivers_buttons(patch_met):
    text, kb_ = render.drivers({"date": "2026-08-10"})
    assert "Karta ochish" in text
    datas = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert any(d.startswith("d:") for d in datas)


def test_dispatch_driver_command(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "driver_card",
                        lambda *a, **k: ("CARD", {"inline_keyboard": []}))
    dispatch.handle_message(111, "/driver Aliyev")
    assert calls and calls[-1][1] == "CARD"


def test_dispatch_driver_command_not_found(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "driver_card",
                        lambda *a, **k: ("CARD", {"inline_keyboard": []}))
    dispatch.handle_message(111, "/driver Nobody")
    assert "TOPILMADI" in calls[-1][1]


def test_dispatch_callback_driver_card(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "driver_card",
                        lambda *a, **k: ("CARD", {"inline_keyboard": []}))
    dispatch.handle_callback(111, {"id": "q"}, "d:d1")
    assert calls[-1][1] == "CARD"


def test_dispatch_callback_driver_entry_viewer_denied(viewer):
    dispatch.handle_callback(999, {"id": "q"}, "dfine:d1")
    assert viewer[-1]["text"] == DENIED_TEXT


@pytest.fixture()
def dentry(tmp_path, monkeypatch):
    monkeypatch.setattr(driver_entry, "STATE_FILE", tmp_path / "entry.json")
    driver_entry._PENDING.clear()
    yield
    driver_entry._PENDING.clear()


def test_driver_entry_fine_flow(admin, dentry):
    dispatch.handle_callback(111, {"id": "q"}, "dfine:d1")
    assert "JARIMA KIRITISH" in admin[-1]["text"]
    assert admin[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "dentry:cancel"
    dispatch.handle_message(111, "2026-08-10")
    assert "so'm" in admin[-1]["text"]
    dispatch.handle_message(111, "15000")
    assert "Sabab" in admin[-1]["text"]
    dispatch.handle_message(111, "Kechikish")
    assert "Jarima saqlandi" in admin[-3]["text"]
    assert "Bildirishnoma" in admin[-2]["text"]
    assert "HAYDOVCHI KARTASI" in admin[-1]["text"]
    st = render.get_storage()
    rows = st.query("SELECT * FROM driver_fines WHERE driver_id='d1'")
    assert rows and rows[0]["amount"] == 15000
    assert rows[0]["reason"] == "Kechikish"
    assert rows[0]["status"] == "ACTIVE"


def test_driver_entry_log_flow(admin, dentry):
    dispatch.handle_callback(111, {"id": "q"}, "dlog:d1")
    assert "KUNLIK KM QAYDI" in admin[-1]["text"]
    dispatch.handle_message(111, "2026-08-10")
    assert "Avtobus" in admin[-1]["text"]
    dispatch.handle_message(111, "v1")
    assert "masofa" in admin[-1]["text"]
    dispatch.handle_message(111, "75")
    assert "Qatnovlar" in admin[-1]["text"]
    dispatch.handle_message(111, "3")
    assert "qayd saqlandi" in admin[-3]["text"]
    assert "Bildirishnoma" in admin[-2]["text"]
    assert "HAYDOVCHI KARTASI" in admin[-1]["text"]
    st = render.get_storage()
    rows = st.query("SELECT * FROM driver_work_logs WHERE driver_id='d1'")
    assert rows and rows[0]["distance_km"] == 75
    assert rows[0]["trip_count"] == 3
    assert rows[0]["vehicle_id"] == "v1"


def test_driver_entry_invalid_date(admin, dentry):
    dispatch.handle_callback(111, {"id": "q"}, "dfine:d1")
    dispatch.handle_message(111, "10.08.2026")
    assert "noto'g'ri" in admin[-1]["text"]
    dispatch.handle_message(111, "2026-08-10")
    assert "so'm" in admin[-1]["text"]


def test_driver_entry_cancel(admin, dentry):
    dispatch.handle_callback(111, {"id": "q"}, "dlog:d1")
    dispatch.handle_callback(111, {"id": "q"}, "dentry:cancel")
    assert driver_entry.current(111) is None
    dispatch.handle_message(111, "2026-08-10")
    assert "JARIMA" not in admin[-1]["text"].upper() and "QAYDI" not in admin[-1]["text"].upper()


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
    monkeypatch.setattr(context, "resolve_role",
                        lambda chat_id: Role.ADMIN)
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
    assert "pr:r-asl" in datas
    assert "pr:r-fer" in datas
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


def test_dispatch_callback_route_select(profiles_env, admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, t, rm=None: calls.append((cid, t, rm)))
    monkeypatch.setattr(render, "today",
                        lambda *a, **k: ("ROUTE TODAY", {"inline_keyboard": []}))
    dispatch.handle_callback(111, {"id": "q"}, "pr:r-asl")
    assert profiles_env.get_active(111) == "ASL SUNDAY MCHJ"
    f = calls[-1][2] if len(calls[-1]) > 2 else calls[-1][1]
    assert calls[-1][1] == "ROUTE TODAY"


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
    def fake_today(filters=None, chat_id=None):
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


def test_auto_agents_disabled_by_default(monkeypatch):
    """AI_AUTO_AGENTS=off (standart) — agentlar poll-tsiklcha chaqirilmaydi."""
    from bm_automation.app.notifications.ops import ops as ops_mod
    called = []

    def fake_tc(method, payload=None, files=None):
        if method == "getUpdates":
            if payload.get("offset"):
                raise SystemExit  # ikkinchi tsiklni to'xtatamiz
            return [{"update_id": 1}]
        called.append(method)
        return {}

    monkeypatch.setattr(ops_mod, "telegram_call", fake_tc)
    monkeypatch.setattr(ops_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ops_mod, "telegram_settings",
        lambda: {"auto_agents": "off", "token": "T"})
    # Har qanday xabar yuborishga urinishni ushlaymiz (agentlar ochib yuborilmasin)
    from bm_automation.app.notifications.ops import (daily_summary, doc_expiry,
                                                     fines_report, grafik_sms,
                                                     monthly_results, problem_alerts,
                                                     self_review)

    def short_name(mod):
        return mod.__name__.split(".")[-1]

    for mod in (problem_alerts, doc_expiry, monthly_results, fines_report,
                daily_summary, self_review, grafik_sms):
        monkeypatch.setattr(
            mod, "check_and_send",
            lambda m=mod: called.append(short_name(m)), raising=False)
        monkeypatch.setattr(
            mod, "check_and_notify",
            lambda m=mod: called.append(short_name(m)), raising=False)
    from bm_automation.app.notifications import sms_notify
    monkeypatch.setattr(
        sms_notify, "retry_stale_pending",
        lambda *a, **k: (called.append("sms_retry"), {})[1], raising=False)
    with pytest.raises(SystemExit):
        ops_mod.poll_forever()
    assert called == [], "agentlar avtomatik chaqirilmasligi kerak"


def test_auto_agents_enabled_runs_agents(monkeypatch):
    """AI_AUTO_AGENTS=on — agentlar odatdagidek chaqiriladi."""
    from bm_automation.app.notifications.ops import ops as ops_mod
    called = []

    def fake_tc(method, payload=None, files=None):
        if method == "getUpdates":
            if payload.get("offset"):
                raise SystemExit  # ikkinchi tsiklni to'xtatamiz
            return [{"update_id": 1}]
        called.append(method)
        return {}

    monkeypatch.setattr(ops_mod, "telegram_call", fake_tc)
    monkeypatch.setattr(ops_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        ops_mod, "telegram_settings",
        lambda: {"auto_agents": "on", "token": "T"})
    from bm_automation.app.notifications.ops import (daily_summary, doc_expiry,
                                                     fines_report, grafik_sms,
                                                     monthly_results, problem_alerts,
                                                     self_review)
    def short_name(mod):
        return mod.__name__.split(".")[-1]

    for mod in (problem_alerts, doc_expiry, monthly_results, fines_report,
                daily_summary, self_review, grafik_sms):
        monkeypatch.setattr(
            mod, "check_and_send",
            lambda m=mod: called.append(short_name(m)), raising=False)
        monkeypatch.setattr(
            mod, "check_and_notify",
            lambda m=mod: called.append(short_name(m)), raising=False)
    from bm_automation.app.notifications import sms_notify
    monkeypatch.setattr(
        sms_notify, "retry_stale_pending",
        lambda *a, **k: (called.append("sms_retry"), {})[1], raising=False)
    with pytest.raises(SystemExit):
        ops_mod.poll_forever()
    names = {"problem_alerts", "doc_expiry", "monthly_results", "fines_report",
             "daily_summary", "self_review", "grafik_sms", "sms_retry"}
    assert names.issubset(set(called)), called


# --------------------------------------------------------------- AI assistant

@pytest.fixture()
def ai_env(monkeypatch, storage):
    monkeypatch.setattr(assistant, "_met", lambda: Metrics(storage))
    monkeypatch.setattr(render, "_met", lambda: Metrics(storage))
    monkeypatch.setattr(render, "get_storage", lambda: storage)
    # .env da kalit bo'lsa ham lokal rejimda ishlaymiz (deterministik testlar)
    monkeypatch.setattr(openrouter, "configured", lambda: False)
    return storage


def test_assistant_unknown_returns_none(ai_env):
    assert assistant.assist("Salom nima gap") is None
    assert assistant.assist("") is None


def test_assistant_today(ai_env):
    text, _ = assistant.assist("2026-08-10 holat qanday?")
    assert text is not None
    assert "KUN HOLATI" in text
    assert "10.08.2026" in text


def test_assistant_yesterday(ai_env):
    text, _ = assistant.assist("Kecha qanday edi?")
    assert "KUN HOLATI" in text


def test_assistant_month(ai_env):
    text, _ = assistant.assist("oy xulosasi avgust")
    assert "OY XULOSASI" in text


def test_assistant_analysis(ai_env):
    text, _ = assistant.assist("Xulosa ber")
    assert "AI TAHLIL" in text
    assert "TAVSIYALAR" in text
    assert "ANIQLANGANLAR" in text


def test_assistant_forecast(ai_env):
    text, _ = assistant.assist("Ertaga qanday bo'ladi?")
    assert "BASHORAT" in text


def test_assistant_vehicle_card(ai_env):
    text, _ = assistant.assist("Avtobus 01A001 holati")
    assert text is not None
    assert "AVTOBUS KARTASI" in text
    assert "01A001" in text


def test_assistant_vehicle_not_found(ai_env):
    text, _ = assistant.assist("Avtobus 99Z99 holati")
    assert "topilmadi" in text.lower()


def test_assistant_driver_card(ai_env):
    text, _ = assistant.assist("Haydovchi Aliyev")
    assert text is not None
    assert "HAYDOVCHI KARTASI" in text
    assert "Aliyev" in text


def test_assistant_distance(ai_env):
    text, _ = assistant.assist("Masofa necha km?")
    assert "MASOFA (KM)" in text


def test_assistant_attendance(ai_env):
    text, _ = assistant.assist("Davomat qanday?")
    assert "DAVOMAT" in text


def test_assistant_schedule(ai_env):
    text, _ = assistant.assist("Jadvalni ko'rsat")
    assert "YO'NALISH JADVALI" in text


def test_assistant_dispatch_fallback(ai_env, admin):
    """Tanib bo'lmagan matn AI orqali javob bersa — HELP_TEXT o'rniga javob."""
    dispatch.handle_message(111, "Xulosa ber")
    assert "AI TAHLIL" in admin[-1]["text"]


def test_assistant_ai_command_no_args(ai_env, admin):
    dispatch.handle_message(111, "/ai")
    assert "AI TAHLIL" in admin[-1]["text"]


def test_assistant_ai_command_with_text(ai_env, admin):
    dispatch.handle_message(111, "/ai davomat qanday?")
    assert "DAVOMAT" in admin[-1]["text"]


def test_assistant_nav_ai(ai_env, admin):
    dispatch.handle_callback(111, {"id": "q"}, "nav:ai")
    assert "AI TAHLIL" in admin[-1]["text"]


# ------------------------------------------------------- OpenRouter (LLM)

def test_openrouter_not_configured_by_default(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert openrouter.configured() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert openrouter.configured() is True


def test_assistant_llm_used_when_configured(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "complete",
        lambda system, user, max_tokens=600: "LLM javob: a'lo holat")
    text, _ = assistant.assist("holat qanday?")
    assert text == "LLM javob: a'lo holat"


def test_assistant_llm_error_falls_back_local(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)

    def boom(system, user, max_tokens=600):
        raise RuntimeError("API down")

    monkeypatch.setattr(openrouter, "complete", boom)
    text, _ = assistant.assist("holat qanday?")
    assert "KUN HOLATI" in text


def test_assistant_general_none_without_key(ai_env):
    assert assistant.assist_general("Nima yangiliklar?") is None


def test_assistant_general_uses_llm(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "complete",
        lambda system, user, max_tokens=600: "LLM: yangilik yo'q")
    text, _ = assistant.assist_general("Nima yangiliklar?")
    assert text == "LLM: yangilik yo'q"


def test_assistant_general_error_returns_none(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)

    def boom(system, user, max_tokens=600):
        raise RuntimeError("down")

    monkeypatch.setattr(openrouter, "complete", boom)
    assert assistant.assist_general("Nima yangiliklar?") is None


def test_assistant_general_keeps_history(ai_env, monkeypatch):
    """chat_id berilsa suhbat tarixi LLM ga uzatiladi va saqlanadi."""
    seen = []

    def fake_chat(messages, max_tokens=600):
        seen.append([m["role"] for m in messages])
        return "LLM javob 1"

    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(openrouter, "chat", fake_chat)

    out1 = assistant.assist_general("Qanday rejalar bor?", chat_id=999)
    assert out1 and "LLM javob 1" in out1[0]
    out2 = assistant.assist_general("Va masofa-chi?", chat_id=999)
    assert out2 and "LLM javob 1" in out2[0]

    assert len(seen) == 2
    assert seen[1][0] == "system"
    # tarix: system + (user, assistant) + yangi user
    assert seen[1][-3:] == ["user", "assistant", "user"]


def test_assistant_history_cleared_after_max(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "chat", lambda messages, max_tokens=600: "Javob")
    # 4 aylanish > _MAX_HISTORY(6) chegarasi
    for _ in range(4):
        assistant.assist_general("Savol", chat_id=1111)
    assert len(assistant._HISTORY.get(1111, [])) <= assistant._MAX_HISTORY


def test_ai_status_local(ai_env):
    assert "lokal" in assistant.ai_status()


def test_ai_status_openrouter(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(openrouter, "model", lambda: "openai/gpt-4o-mini")
    assert "OpenRouter" in assistant.ai_status()
    assert "openai/gpt-4o-mini" in assistant.ai_status()


def test_help_text_has_status(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    assert "AI yordamchi" in assistant.help_text()


def test_settings_shows_ai_status(ai_env, admin, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    text = render.settings_text(111)
    assert "OpenRouter" in text


def test_llm_output_escaped(ai_env, monkeypatch):
    """LLM HTML teglar bersa ham Telegram xatosiz yuboradi (escape qilinadi)."""
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "complete",
        lambda system, user, max_tokens=600: "<b>bold</b> va 3 < 5 & 7")
    text, _ = assistant.assist("holat qanday?")
    assert "<b>bold</b>" not in text
    assert "&lt;b&gt;bold&lt;/b&gt;" in text
    assert "3 &lt; 5 &amp; 7" in text


def test_llm_general_output_escaped(ai_env, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "chat",
        lambda messages, max_tokens=600: "Natija < 5 foiz")
    text, _ = assistant.assist_general("Qanday?")
    assert "Natija &lt; 5 foiz" in text


def test_ai_clear_history_command(ai_env, admin, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "chat", lambda messages, max_tokens=600: "Javob")
    assistant.assist_general("Birinchi savol", chat_id=111)
    assert len(assistant._HISTORY.get(111, [])) > 0
    dispatch.handle_message(111, "/ai toza")
    assert 111 not in assistant._HISTORY
    assert "tozalandi" in admin[-1]["text"]


# ---------------------------------------------------- ertalabki AI-xulosa

@pytest.fixture()
def daily_env(monkeypatch, tmp_path):
    import types
    sent = []

    def fake_send(text, chat_id=None, reply_markup=None):
        sent.append((text, chat_id))

    monkeypatch.setattr(daily_summary, "STATE_FILE", tmp_path / "ds.json")
    monkeypatch.setattr(
        daily_summary, "telegram_settings",
        lambda: {"daily_summary": "on", "daily_summary_hour": "8"})
    monkeypatch.setattr(
        daily_summary, "get_storage",
        lambda: types.SimpleNamespace(enabled=True))
    monkeypatch.setattr(
        daily_summary, "configured_roles", lambda: {111: Role.ADMIN})
    monkeypatch.setattr(daily_summary, "send_message", fake_send)
    monkeypatch.setattr(daily_summary.assistant, "daily_summary",
                        lambda: "🌅 <b>TEST XULOSA</b>")
    monkeypatch.setattr(
        daily_summary.assistant, "_maybe_llm",
        lambda text, local_text, q: local_text)
    return sent


def test_daily_summary_skips_when_db_disabled(daily_env, monkeypatch):
    import types
    monkeypatch.setattr(
        daily_summary, "get_storage", lambda: types.SimpleNamespace(enabled=False))
    daily_summary.check_and_send()
    assert daily_env == []


def test_daily_summary_hour_gate(daily_env, monkeypatch):
    import types
    monkeypatch.setattr(daily_summary, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(
        daily_summary.time, "localtime", lambda: types.SimpleNamespace(tm_hour=6))
    daily_summary.check_and_send()
    assert daily_env == []


def test_daily_summary_sends_once_per_day(daily_env, monkeypatch):
    import types
    monkeypatch.setattr(daily_summary, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(
        daily_summary.time, "localtime", lambda: types.SimpleNamespace(tm_hour=9))
    daily_summary.check_and_send()
    daily_summary.check_and_send()  # ikkinchi chaqiruv — holat fayl to'xtatadi
    assert len(daily_env) == 1
    assert daily_env[0][1] == "111"
    assert "TEST XULOSA" in daily_env[0][0]


def test_daily_summary_disabled_by_setting(daily_env, monkeypatch):
    import types
    monkeypatch.setattr(daily_summary, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(
        daily_summary, "telegram_settings",
        lambda: {"daily_summary": "off", "daily_summary_hour": "8"})
    monkeypatch.setattr(
        daily_summary.time, "localtime", lambda: types.SimpleNamespace(tm_hour=9))
    daily_summary.check_and_send()
    assert daily_env == []


def test_daily_summary_send_now_command(daily_env, admin):
    dispatch.handle_message(111, "/daily")
    assert "TEST XULOSA" in admin[-1]["text"]


def test_alerts_shows_daily_status(daily_env, admin):
    text, _ = render.alerts_text()
    assert "ERTALABKI XULOSA" in text
    assert "MUAMMO ALERTLARI" in text


def test_dispatch_unknown_uses_llm(ai_env, admin, monkeypatch):
    monkeypatch.setattr(openrouter, "configured", lambda: True)
    monkeypatch.setattr(
        openrouter, "complete",
        lambda system, user, max_tokens=600: "LLM javob berdi")
    dispatch.handle_message(111, "Qanday rejalar bor?")
    assert "LLM javob berdi" in admin[-1]["text"]


def test_openrouter_complete_sends_choices(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "Salom"}}]}

    monkeypatch.setattr(openrouter.requests, "post",
                        lambda *a, **k: FakeResp())
    assert openrouter.complete("s", "u") == "Salom"


def test_openrouter_complete_http_error(monkeypatch):
    class FakeResp:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(openrouter.requests, "post",
                        lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError):
        openrouter.complete("s", "u")


def test_openrouter_complete_empty_choices(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"choices": []}

    monkeypatch.setattr(openrouter.requests, "post",
                        lambda *a, **k: FakeResp())
    with pytest.raises(RuntimeError):
        openrouter.complete("s", "u")


def test_dispatch_new_commands(ai_env, admin):
    dispatch.handle_message(111, "/vehicle 01A001")
    assert "AVTOBUS KARTASI" in admin[-1]["text"]
    dispatch.handle_message(111, "/top km")
    assert "REYTING" in admin[-1]["text"]
    dispatch.handle_message(111, "/distance")
    assert "MASOFA (KM)" in admin[-1]["text"]


def test_menu_has_ai_button():
    buttons = [b for row in kb.MAIN_MENU_ROWS for b in row]
    assert "🤖 AI yordamchi" in buttons


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


# ----------------------------------------------------------- tgformat

def test_format_badge_thresholds():
    from bm_automation.app.utils.tgformat import badge, badge_plain
    assert badge(98) == "✅ <b>98%</b>"
    assert badge(80) == "⚠️ <b>80%</b>"
    assert badge(79.9) == "❌ <b>79.9%</b>"
    assert badge(None) == "-"
    assert badge_plain(100) == "✅ 100%"
    assert "<b>" not in badge_plain(100)


def test_format_table_escapes_and_caps_width():
    from bm_automation.app.utils.tgformat import table
    out = table(["A", "B"], [["a<b>&", "123456789012345678901234567890"]])
    assert "&lt;b&gt;" in out and "&amp;" in out
    assert out.startswith("<pre>") and out.endswith("</pre>")
    # jadval kengligi max_width dan oshmaydi (46)
    for line in out.split("\n"):
        assert len(line) <= 46 + 13  # <pre></pre> bilan


def test_format_table_truncation():
    from bm_automation.app.utils.tgformat import table
    out = table(["Sana", "Izoh"],
                [["10.08", "bu juda uzun matn bo'lib telefon ekraniga sig'maydi"]],
                max_width=30)
    assert "…" in out
    for line in out.split("\n"):
        assert len(line) <= 30 + 13


# ---------------------------------------------------------------- planning

@pytest.fixture()
def plan_env(monkeypatch, tmp_path):
    """Rejalashtirish uchun: alohida SQLite, state fayllari tmp_path'da."""
    sent = []

    def fake_reply(chat_id, text, reply_markup=None):
        sent.append({"chat_id": chat_id, "text": text,
                     "reply_markup": reply_markup})

    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "plan.db"))))
    monkeypatch.setattr(planning, "get_storage", lambda: st)
    monkeypatch.setattr(planning, "STATE_FILE", tmp_path / "plan.json")
    monkeypatch.setattr(planning, "FLOW_FILE", tmp_path / "plan_flow.json")
    monkeypatch.setattr(dispatch, "reply", fake_reply)
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    monkeypatch.setattr(render, "_met", lambda: Metrics(st))
    monkeypatch.setattr(render, "get_storage", lambda: st)
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", default_role="viewer"))
    planning._PLAN.clear()
    planning._FLOW.clear()
    return sent


def _plan_seed_today(st, driver_id="d1", graph="P5"):
    st.save_schedule(date.today().isoformat(), "r1", graph,
                     driver_id=driver_id, vehicle_id="v1",
                     shift_name="SHANBA")


def test_plan_add_auto_graph(plan_env):
    st = planning.get_storage()
    _plan_seed_today(st, "d1", "P5")
    msg = planning.add_from_text("Aliyev")
    assert "✅" in msg and "Aliyev" in msg
    e = planning.entries()
    assert len(e) == 1 and e[0]["graph_name"] == "P5"


def test_plan_add_with_graph_number(plan_env):
    msg = planning.add_from_text("Aliyev 7")
    assert "P7" in msg
    e = planning.entries()
    assert e[0]["graph_name"] == "P7"
    assert e[0]["driver_id"] == "d1"


def test_plan_add_natural_phrase(plan_env):
    msg = planning.add_from_text("Aliyevni 2-grafikaga yoz")
    assert "P2" in msg
    assert planning.entries()[0]["graph_name"] == "P2"


def test_plan_add_not_found(plan_env):
    msg = planning.add_from_text("BundayKishi")
    assert "topilmadi" in msg.lower()
    assert planning.entries() == []


def test_plan_add_multiple_and_clear(plan_env):
    planning.add_from_text("Aliyev 2")
    planning.add_from_text("Karimov 3")
    assert len(planning.entries()) == 2
    n = planning.clear()
    assert n == 2
    assert planning.entries() == []


def test_plan_remove_driver(plan_env):
    planning.add_from_text("Aliyev 2")
    assert planning.remove("d1") is True
    assert planning.entries() == []
    assert planning.remove("d1") is False


def test_plan_text(plan_env):
    assert "bo'sh" in planning.plan_text()
    planning.add_from_text("Aliyev 2")
    txt = planning.plan_text()
    assert "ERTANGI REJA" in txt and "Aliyev" in txt and "P2" in txt


def test_plan_flow(plan_env):
    dispatch.handle_callback(111, {"id": "q"}, "plan:add")
    assert "Haydovchi ismini yozing" in plan_env[-1]["text"]
    dispatch.handle_message(111, "Aliyev")
    assert "Grafik raqamini yozing" in plan_env[-1]["text"]
    dispatch.handle_message(111, "3")
    assert "P3" in plan_env[-1]["text"]
    assert len(planning.entries()) == 1


def test_plan_flow_cancel(plan_env):
    dispatch.handle_callback(111, {"id": "q"}, "plan:add")
    dispatch.handle_callback(111, {"id": "q"}, "plan:cancel")
    assert planning.flow_current(111) is None


def test_dispatch_plan_admin(plan_env):
    dispatch.handle_message(111, "/plan")
    assert "ERTANGI REJA" in plan_env[-1]["text"]
    assert plan_env[-1]["reply_markup"]["inline_keyboard"]


def test_dispatch_plan_viewer_denied(plan_env):
    dispatch.handle_message(999, "/plan")
    assert plan_env[-1]["text"] == DENIED_TEXT


def test_dispatch_plan_clear_callback(plan_env):
    planning.add_from_text("Aliyev 2")
    dispatch.handle_callback(111, {"id": "q"}, "plan:clear")
    assert "tozalandi" in plan_env[-1]["text"].lower()
    assert planning.entries() == []


def test_assistant_plan_intent(ai_env, admin, monkeypatch, tmp_path):
    monkeypatch.setattr(planning, "get_storage", lambda: ai_env)
    monkeypatch.setattr(planning, "STATE_FILE", tmp_path / "plan.json")
    monkeypatch.setattr(planning, "FLOW_FILE", tmp_path / "plan_flow.json")
    planning._PLAN.clear()
    planning._FLOW.clear()
    _plan_seed_today(ai_env, "d1", "P5")
    dispatch.handle_message(111, "Aliyevni ertaga rejaga yoz")
    assert "P5" in admin[-1]["text"]


def test_menu_has_plan_button():
    buttons = [b for row in kb.MAIN_MENU_ROWS for b in row]
    assert "🗓 Reja" in buttons


def test_nav_has_plan():
    texts = [t for t, _ in kb.NAV_ITEMS]
    assert "🗓 Reja" in texts


def test_plan_role_capability():
    assert can(Role.ADMIN, "plan")
    assert can(Role.DISPATCHER, "plan")
    assert not can(Role.VIEWER, "plan")


# ----------------------------------------------------------- MANAGER roli

def test_manager_role(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(manager_ids="777", admin_ids="111"))
    assert resolve_role(777) is Role.MANAGER
    assert resolve_role(111) is Role.ADMIN
    assert not can(Role.MANAGER, "sync")
    assert not can(Role.MANAGER, "export")
    assert not can(Role.MANAGER, "plan")
    assert can(Role.MANAGER, "view")
    assert "FIRMA BOSHQARUVCHI" in roles.role_label(Role.MANAGER)


def test_allowed_ids_includes_manager(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(manager_ids="777", admin_ids="111",
                                    strict_access="on"))
    assert is_allowed(777) is True
    assert 777 in allowed_ids()


def test_manager_sees_only_own_company(monkeypatch):
    """MANAGER faqat o'z kompaniyasini (ownerChatIds) ko'radi — majburiy filter."""
    fake = [
        {"name": "ASL SUNDAY MCHJ", "routeVariantId": "r-asl", "profileId": "p1",
         "ownerChatIds": [111]},
        {"name": "FERGANATEX", "routeVariantId": "r-fer", "profileId": "",
         "ownerChatIds": [777]},
    ]
    monkeypatch.setattr(context, "all_profiles", lambda: fake)
    monkeypatch.setattr(context, "get_profile",
                        lambda name: next((p for p in fake
                                           if p["name"] == name), None))
    monkeypatch.setattr(context, "resolve_role",
                        lambda cid: Role.ADMIN if cid == 111 else Role.MANAGER)
    assert context.allowed_names(777) == ["FERGANATEX"]
    assert context.allowed_names(111) == ["ASL SUNDAY MCHJ", "FERGANATEX"]
    assert context.filters_for(777) == {
        "profile": "FERGANATEX", "route": "r-fer"}
    assert context.can_view(777, "ASL SUNDAY MCHJ") is False
    assert context.can_view(777, "FERGANATEX") is True


# ------------------------------------------ settings / til / 1 km narxi

def test_render_settings_km_rate_and_lang(monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    from bm_automation.app.notifications.ops import context as ctx
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111"))
    monkeypatch.setattr(ctx, "filters_for", lambda cid: {"route": "ASL"})
    monkeypatch.setattr(bs, "route_km",
                        lambda rid, default=0.0: 2000.0 if rid == "ASL" else default)
    monkeypatch.setattr(bs, "lang", lambda cid: "uz")
    text = render.settings_text(111)
    assert "Haydovchi 1 km" in text
    assert "2 000 so'm" in text
    assert "O'zbek" in text


def test_render_settings_km_rate_unset(monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    from bm_automation.app.notifications.ops import context as ctx
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111"))
    monkeypatch.setattr(ctx, "filters_for", lambda cid: {"route": "ASL"})
    monkeypatch.setattr(bs, "route_km",
                        lambda rid, default=0.0: 0.0 if rid == "ASL" else default)
    assert "o'rnatilmagan" in render.settings_text(111)


def test_render_settings_lang_russian(monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111"))
    monkeypatch.setattr(bs, "lang", lambda cid: "ru")
    assert "Русский" in render.settings_text(111)


def test_render_attendance_shows_present(patch_met):
    text, _ = render.attendance({"date": "2026-08-10"})
    assert "✅ JOYIDA KELGANLAR" in text
    assert "Aliyev" in text        # d1 qatnashdi
    assert "❌ KELMAGANLAR" in text
    assert "Karimov" in text       # d2 kelmadi


def test_render_month_accept_rate_column(patch_met):
    text, _ = render.month({"from": "2026-08-10", "to": "2026-08-10"})
    assert "Qabul/Reja%" not in text
    assert "Qabul" in text


def test_settings_kb_admin_only_kmrate(monkeypatch):
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", dispatcher_ids="333"))
    for cid, expected in ((111, True), (333, False), (999, False)):
        buttons = [b["callback_data"] for row in kb.settings_kb(cid)["inline_keyboard"]
                   for b in row]
        assert ("settings:kmrate" in buttons) is expected
        assert "settings:lang" in buttons


def test_dispatch_kmrate_dispatcher_denied(monkeypatch, tmp_path):
    """DISPATCHER moliyaviy 1 KM NARXI ni o'zgartira olmaydi."""
    import bm_automation.app.core.bot_settings as bs
    bs._reset()
    sent = []

    def fake_reply(chat_id, text, reply_markup=None):
        sent.append({"chat_id": chat_id, "text": text,
                     "reply_markup": reply_markup})

    monkeypatch.setattr(dispatch, "reply", fake_reply)
    monkeypatch.setattr(dispatch, "answer", lambda cq, t="": None)
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", dispatcher_ids="333"))
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "dis_km.db"))))
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    dispatch.handle_callback(333, {"id": "q"}, "settings:kmrate")
    assert bs.pending(333) is None
    assert sent[-1]["text"] == DENIED_TEXT


def test_main_menu_hides_finance_for_dispatcher(monkeypatch):
    """Dispetcher reply-menuda Brutto/Oylik tugmalari ko'rinmaydi."""
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", dispatcher_ids="333"))
    dispatcher = kb.main_menu_kb(333)
    dflat = [b for row in dispatcher["keyboard"] for b in row]
    assert "🧾 Brutto" not in dflat
    assert "💰 Oylik" not in dflat
    admin = kb.main_menu_kb(111)
    aflat = [b for row in admin["keyboard"] for b in row]
    assert "🧾 Brutto" in aflat
    assert "💰 Oylik" in aflat


def test_lang_kb_marks_current():
    kb_ = kb.lang_kb("ru")
    ru = [b for row in kb_["inline_keyboard"] for b in row
          if b["callback_data"] == "setlang:ru"][0]
    assert ru["text"].startswith("✅")


def test_main_menu_kb_switches_to_russian(monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    monkeypatch.setattr(bs, "lang", lambda cid: "ru" if cid == 123 else "uz")
    kb_ = kb.main_menu_kb(123)
    flat = [b for row in kb_["keyboard"] for b in row]
    assert any("🚌 Автобусы" == t for t in flat)
    kb_uz = kb.main_menu_kb(456)
    flat_uz = [b for row in kb_uz["keyboard"] for b in row]
    assert "🚌 Автобусы" not in flat_uz


def test_dispatch_start_uses_lang_menu(admin, monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    monkeypatch.setattr(bs, "lang", lambda cid: "ru")
    dispatch.handle_message(111, "/start")
    kb_ = admin[-1]["reply_markup"]
    flat = [b for row in kb_["keyboard"] for b in row]
    assert any("🚌 Автобусы" == t for t in flat)


def test_dispatch_set_route_km_flow(admin, monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    bs._reset()
    dispatch.handle_callback(111, {"id": "q"}, "settings:kmrate")
    assert admin[-1]["text"].startswith("💵")
    assert bs.pending(111) == "km_rate"
    dispatch.handle_message(111, "2 500")
    assert bs.pending(111) is None
    assert "2 500 so'm" in admin[-1]["text"]


def test_dispatch_km_rate_invalid_keeps_pending(admin, monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    bs._reset()
    dispatch.handle_callback(111, {"id": "q"}, "settings:kmrate")
    dispatch.handle_message(111, "abc")
    assert bs.pending(111) == "km_rate"
    assert "Son kiriting" in admin[-1]["text"]


def test_dispatch_km_rate_viewer_denied(viewer):
    import bm_automation.app.core.bot_settings as bs
    bs._reset()
    dispatch.handle_callback(999, {"id": "q"}, "settings:kmrate")
    assert viewer[-1]["text"] == DENIED_TEXT


def test_dispatch_set_lang(admin, monkeypatch):
    import bm_automation.app.core.bot_settings as bs
    bs._reset()
    monkeypatch.setattr(bs, "set_lang", lambda cid, v: v)
    monkeypatch.setattr(bs, "lang", lambda cid: "ru")
    dispatch.handle_callback(111, {"id": "q"}, "setlang:ru")
    assert admin[-1]["text"].startswith("⚙️")
    assert "Русский" in admin[-1]["text"]


# ------------------------------------------------------ Telegram bo'laklash

def test_split_text_short_returns_single():
    text = "Qisqa xabar"
    chunks = tg.split_text(text)
    assert chunks == [("Qisqa xabar", "HTML")]


def test_split_text_over_limit():
    para = "A" * 100
    text = "\n\n".join([para] * 10)  # 10 * 100 + 18 = 1018 > 4096? yo'q
    assert len(text) < tg.MAX_TEXT_LEN


def test_split_text_splits_by_paragraph():
    para = "B" * 2000
    text = "\n\n".join([para] * 3)  # 3 * 2000 + 4 > 4096
    chunks = tg.split_text(text)
    assert len(chunks) == 2
    assert all(len(c[0]) <= tg.MAX_TEXT_LEN for c in chunks)
    assert all(mode == "HTML" for _, mode in chunks)


def test_split_oversized_pre_plain():
    # bitta katta <pre> jadval — HTML tashlanib plain qilib bo'laklanadi
    big = "<pre>" + ("0123456789" * 500) + "</pre>"  # 5002 belgi
    chunks = tg.split_text(big)
    assert len(chunks) > 1
    for body, mode in chunks:
        assert len(body) <= tg.MAX_TEXT_LEN
        assert "<pre>" not in body
        if mode is not None:
            assert mode == "HTML"
    # oddiy matn bo'laklari parse_mode siz (None)
    assert any(mode is None for _, mode in chunks)


def test_split_text_empty():
    assert tg.split_text("") == [("", "HTML")]
    assert tg.split_text(None) == [("", "HTML")]


def test_send_chunks_splits_and_keyboard_last(monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "telegram_settings", lambda: {
        "token": "T", "chat_id": "1", "driver_chat_id": ""})
    monkeypatch.setattr(tg, "telegram_call",
                        lambda method, payload=None: sent.append(payload))
    para = "C" * 2000
    text = "\n\n".join([para] * 3)
    tg.send_chunks(123, text, reply_markup={"inline_keyboard": []})
    assert len(sent) == 2
    assert all(len(p["text"]) <= tg.MAX_TEXT_LEN for p in sent)
    # klaviatura faqat oxirgi blokka
    assert sent[0].get("reply_markup") is None
    assert sent[1]["reply_markup"] == {"inline_keyboard": []}


def test_send_message_routes_oversized_to_chunks(monkeypatch):
    monkeypatch.setattr(tg, "telegram_settings", lambda: {
        "token": "T", "chat_id": "1", "driver_chat_id": ""})
    calls = []
    monkeypatch.setattr(tg, "send_chunks",
                        lambda cid, text, rm=None: calls.append((cid, len(text), rm)))
    monkeypatch.setattr(tg, "telegram_call",
                        lambda method, payload=None: calls.append(("direct", 0)))
    ok = tg.send_message("X" * 5000, chat_id="5")
    assert ok is True
    assert calls[0][0] == "5" and calls[0][1] == 5000
    # qisqa xabar to'g'ridan-to'g'ri sendMessage
    tg.send_message("Qisqa", chat_id="5")
    assert any(c == ("direct", 0) for c in calls)


def test_reply_uses_send_chunks(monkeypatch):
    """dispatch.reply uzun xabarni ham bo'laklab yuboradi."""
    seen = {}
    monkeypatch.setattr(tg, "send_chunks",
                        lambda cid, text, rm=None: seen.update(
                            {"cid": cid, "text": text, "rm": rm}))
    dispatch.reply(777, "UZUN" * 5000)
    assert seen["cid"] == 777
    assert len(seen["text"]) == 20000


# ---------------------------------------------------------- schedule limit

def test_render_schedule_truncated(patch_met, storage):
    for i in range(5):
        storage.save_route(external_id=f"r-{i}", name=f"Route {i}",
                           entity_type="ROUTE")
        for j in range(5):
            storage.save_schedule(date="2026-08-10", route_id=f"r-{i}",
                                  graph_name=f"G{j}", driver_id=f"d{j}",
                                  vehicle_id=f"v{i}", shift_name="KUN")
    text, _ = render.schedule({"date": "2026-08-10"},
                              limit_routes=2, limit_rows=2)
    assert "Route 0" in text and "Route 1" in text
    assert "Route 2" not in text
    assert "yana 3 ta yo'nalish" in text
    assert "yana 3 ta smena" in text
    assert len(text) <= tg.MAX_TEXT_LEN


# ------------------------------------------------------------ /verify

def test_dispatch_verify_starts(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "start_verify",
                        lambda cid, month=None: calls.append((cid, month)))
    dispatch.handle_message(111, "/verify")
    dispatch.handle_message(111, "/verify 2026-08")
    assert calls[0] == (111, None)
    assert calls[1] == (111, "2026-08")


def test_dispatch_verify_confirm_callback(admin, monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "start_verify",
                        lambda cid, month=None: calls.append((cid, month)))
    dispatch.handle_callback(111, {"id": "q"}, "verify:2026-08")
    assert calls == [(111, "2026-08")]
    dispatch.handle_callback(111, {"id": "q"}, "verify:bogus")
    assert calls[-1] == (111, None)
    dispatch.handle_callback(111, {"id": "q"}, "verify:cancel")
    assert len(calls) == 2


def test_dispatch_verify_do_reports(admin, monkeypatch):
    """_do_verify natijani foydalanuvchiga yuboradi (AI va DB bilan)."""
    import bm_automation.app.dashboard.metrics as metrics_mod
    import bm_automation.app.services.verify as vmod
    from bm_automation.app.notifications.ops import self_review

    st = render.get_storage()
    monkeypatch.setattr(metrics_mod.Metrics, "system", lambda self: {
        "api": {"ok": True}, "database": {"ok": True},
        "last_sync": "2026-08-10T08:00:00+00:00"})
    monkeypatch.setattr(self_review, "analyze",
                        lambda days=None: {"error_total": 0, "failures": 0,
                                           "top_errors": []})
    monkeypatch.setattr(vmod, "get_storage", lambda: st)
    monkeypatch.setattr(vmod, "all_profiles", lambda: [{
        "name": "FERGANATEX", "routeVariantId": "r1", "profileId": ""}])
    monkeypatch.setattr(vmod, "BMClient", lambda: type("C", (), {
        "auto_relogin": True,
        "access_token": "a", "refresh_token": "r",
        "session": type("S", (), {"headers": {}})(),
        "login": lambda self=None: None,
        "login_by_profile": lambda self, pid: None})())
    monkeypatch.setattr(vmod, "GrossRepository", lambda client: type("G", (), {
        "route": lambda self, rid, f, t: {
            "dates": {"2026-08-01": {"vehicles": [
                {"tripFact": 1, "distanceFact": 10.0, "workingDay": 1}]}}}})())
    monkeypatch.setattr(vmod, "ai_report", lambda dg: None)
    dispatch._do_verify(111, "2026-08")
    text = admin[-1]["text"]
    assert "OYLIK TEKSHIRISH" in text
    assert "FERGANATEX" in text


# ----------------------------------------------------- haydovchi (DRIVER)

def test_driver_nav_kb_limited(monkeypatch, tmp_path):
    """Haydovchi nav_kb faqat Dashboard + Settings ko'rsatadi."""
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "drv_nav.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)

    kb_ = kb.nav_kb(chat_id=999)
    data = [b["callback_data"] for row in kb_["inline_keyboard"] for b in row]
    assert data == ["nav:dashboard", "nav:settings"]


def test_driver_main_menu_limited(monkeypatch, tmp_path):
    """Haydovchi reply-menuda faqat Dashboard/Settings/Yordam ko'radi."""
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "drv_mm.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)

    menu = kb.main_menu_kb(999)
    buttons = [b for row in menu["keyboard"] for b in row]
    assert buttons == ["📊 Dashboard", "📬 Murojaat", "⚙️ Settings", "❓ Yordam"]


def test_driver_appeal_flow_saves(admin, dentry, monkeypatch, tmp_path):
    """Haydovchi /murojaat bilan tugma orqali mavzu tanlab murojaat yuboradi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    from bm_automation.app.notifications import telegram as _tg_mod
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeal.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    monkeypatch.setattr(render, "get_storage", lambda: st)
    monkeypatch.setattr(_tg_mod, "send_message",
                        lambda message, chat_id=None, **kw: True)

    dispatch.handle_message(999, "/murojaat")
    assert "MUROJAAT YUBORISH" in admin[-1]["text"]
    assert "Mavzuni tanlang" in admin[-1]["text"]
    # mavzu tugma orqali tanlanadi
    cdata = [b["callback_data"]
             for row in admin[-1]["reply_markup"]["inline_keyboard"]
             for b in row]
    assert cdata[0].startswith("apptopic:")
    dispatch.handle_callback(999, {"id": "q"}, "apptopic:0")
    assert "Murojaat yoki taklif matni" in admin[-1]["text"]
    dispatch.handle_message(999, "Kechqurun qaytishda yo'l yoritilmagan")
    assert "Murojaat qabul qilindi" in admin[-1]["text"]
    assert "#1" in admin[-1]["text"]
    rows = st.appeals_list(driver_id="d1")
    assert len(rows) == 1
    assert rows[0]["title"] == "🚧 Yo'l muammosi"
    assert rows[0]["text"] == "Kechqurun qaytishda yo'l yoritilmagan"
    assert rows[0]["status"] == "YANGI"


def test_driver_appeal_flow_skips_title(admin, dentry, monkeypatch, tmp_path):
    """Mavzusiz tugma bilan mavzu o'tkaziladi; matn saqlanadi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeal2.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "_notify_admins_appeal", lambda *a, **k: None)

    dispatch.handle_message(999, "/murojaat")
    dispatch.handle_callback(999, {"id": "q"}, "apptopic:none")
    assert "Murojaat yoki taklif matni" in admin[-1]["text"]
    dispatch.handle_message(999, "Umuniy taklif")
    rows = st.appeals_list(driver_id="d1")
    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["text"] == "Umuniy taklif"


def test_driver_appeal_requires_text(admin, dentry, monkeypatch, tmp_path):
    """Bo'sh matn qabul qilinmaydi — murojaat saqlanmaydi, oqim ochiq qoladi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeal3.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch.handle_message(999, "/murojaat")
    dispatch.handle_callback(999, {"id": "q"}, "apptopic:3")
    assert "Murojaat yoki taklif matni" in admin[-1]["text"]
    dispatch.handle_message(999, "   ")
    assert st.appeals_list(driver_id="d1") == []
    assert driver_entry.current(999) is not None


def test_driver_appeal_unlinked_denied(viewer, monkeypatch, tmp_path):
    """Bog'lanmagan foydalanuvchi /murojaat deyishga huquqi yo'q —
    rol DENIED emas, balki bog'lanishga yo'naltiriladi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeal4.db"))))
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    dispatch.handle_message(999, "/murojaat")
    assert "haydovchi sifatida bog'lanmagansiz" in viewer[-1]["text"]
    assert viewer[-1]["text"] != DENIED_TEXT


def test_driver_my_appeals_empty(admin, monkeypatch, tmp_path):
    """Murojaatlar bo'lmasa — bo'sh javob chiqadi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeals_my.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch.handle_message(999, "/murojaatlarim")
    assert "MUROJAATLARINGIZ YO'Q" in admin[-1]["text"]


def test_driver_my_appeals_lists_saved(admin, monkeypatch, tmp_path):
    """Saqllangan murojaatlar holat bilan ko'rinadi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "appeals_l.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    st.appeal_add("d1", title="Yo'l yomon", text="Asfalt buzilgan",
                  status="YANGI")
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch.handle_message(999, "/murojaatlarim")
    out = admin[-1]["text"]
    assert "#1" in out
    assert "Yo'l yomon" in out
    assert "YANGI" in out


def test_driver_company_sections_denied(viewer, monkeypatch, tmp_path):
    """Haydovchiga kompaniya bo'limlari DENIED_TEXT qaytaradi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "drv_deny.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    for cmd in ("/drivers", "/problems", "/vehicles", "/routes", "/trips",
                "/reports", "/profiles", "/top", "/distance"):
        dispatch.handle_message(999, cmd)
        assert viewer[-1]["text"] == DENIED_TEXT, cmd


def test_driver_nav_callback_denied(viewer, monkeypatch, tmp_path):
    """Haydovchi nav callback'da faqat dashboard/settings ishlaydi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "drv_cb.db"))))
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch.handle_callback(999, {"id": "q"}, "nav:problems")
    assert viewer[-1]["text"] == DENIED_TEXT


def test_driver_self_link_9_digit_phone(viewer, monkeypatch, tmp_path):
    """Part 2b: 9 xonali raqam (998 prefiksiz) ham bog'lanadi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "link9.db"))))
    st.save_driver_profile("d1", phone="998901112233",
                           notification_target="",
                           notification_enabled=False)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    with dispatch._DRIVER_LINK_LOCK:
        dispatch._DRIVER_LINK_STATE.discard(999)

    dispatch.handle_message(999, "/start")
    assert viewer, "Haydovchidan telefon raqami so'ralishi kerak"

    dispatch.handle_message(999, "901112233")
    assert any("Xush kelibsiz" in m["text"] for m in viewer)
    profile = st.find("driver_profiles", driver_id="d1")
    assert profile["telegram_chat_id"] == "999"
    assert roles.resolve_role(999) is roles.Role.DRIVER


def test_driver_card_shows_own_metrics(viewer, monkeypatch, tmp_path):
    """Haydovchi o'z kartasida Ish kunlari/Qatnov/Km/Qatnashish ko'radi.

    `__deny__`/`__none__` sentinel filterlari tashlab yuborilishi kerak —
    aks holda Ish kunlari va Km bo'sh chiqadi.
    """
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = render.get_storage()
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    st.save_trip(TripRecord(date=date.today().isoformat(), route_id="r1",
                            vehicle_id="v1", driver_id="d1",
                            planned_time="06:00", actual_time="06:15",
                            status="ACCEPTED", source=SyncSource.DUTY.value))
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)

    text, _mk = render.driver_card("d1",
                                   {"profile": "__none__", "route": "__deny__"},
                                   chat_id=999)
    assert "Ish kunlari" in text
    assert "Qatnov" in text
    assert "Km" in text
    assert "Qatnashish" in text


def test_driver_help_text(viewer, monkeypatch, tmp_path):
    """Haydovchi /help da oddiy (kompaniya) yordam emas, o'zinikini oladi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = render.get_storage()
    st.save_driver_profile("d1", notification_target="901234567",
                           notification_enabled=True)
    st.link_driver_telegram("d1", 999)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)

    dispatch.handle_message(999, "/help")
    assert "Haydovchi uchun yordam" in viewer[-1]["text"]
    assert "/today" in viewer[-1]["text"]
    assert "/settings" in viewer[-1]["text"]
    assert "disfatcher/admin" not in viewer[-1]["text"]


# ------------------------------------------- grafik post + start klaviatura

def test_main_menu_unlinked_shows_share_phone(monkeypatch, tmp_path):
    """Bog'lanmagan foydalanuvchi menyusida 'Raqamni ulashish' tugmasi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", default_role="viewer"))
    kb_unlinked = kb.main_menu_kb(4242)
    items = str(kb_unlinked)
    # Contact tugmasi QO'SHIMCHA qator — asosiy menyu o'zgarmaydi
    assert "request_contact" in items, "bog'lanmagan userga contact tugmasi"
    assert "Dashboard" in items

    # Bog'langan haydovchi oddiy menyuni oladi (contact tugmasisiz)
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "mm.db"))))
    st.save_driver_profile("d1", notification_target="901234567")
    st.link_driver_telegram("d1", 4243)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE \
        as _c2
    _c2[4243] = "d1"
    kb_driver = kb.main_menu_kb(4243)
    assert "request_contact" not in str(kb_driver)
    assert "Dashboard" in str(kb_driver)


def test_stored_viewer_role_yields_to_linked_driver(monkeypatch, tmp_path):
    """Bog'langan haydovchining eski VIEWER yozuvi DRIVER bo'lishi kerak.

    Admin keyinchalik haydovchini bog'lasa, bot_usersda qolgan VIEWER rol
    uni admin panellarga ochib qo'yardi — endi DB bog'lanishi ustun.
    """
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "rl.db"))))
    st.save_driver_profile("d1", notification_target="901234567")
    st.link_driver_telegram("d1", 3131)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    # bot_users'da eski VIEWER yozuvi qolgan (admin bog'lashidan oldin)
    from bm_automation.app.core import bot_users
    bot_users.record_user(3131, role="VIEWER")
    try:
        assert roles.resolve_role(3131) is roles.Role.DRIVER
    finally:
        bot_users._LOCK.acquire()
        try:
            bot_users._CACHE.pop("3131", None)
            bot_users._save()
        finally:
            bot_users._LOCK.release()


def test_stored_admin_role_kept_when_linked(monkeypatch, tmp_path):
    """Baland rollar (ADMIN/DISPATCHER/MANAGER) bog'lanishdan o'zgarmaydi."""
    from bm_automation.app.notifications.ops.roles import _DRIVER_CHAT_CACHE
    _DRIVER_CHAT_CACHE.clear()
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "rl2.db"))))
    st.save_driver_profile("d1", notification_target="901234567")
    st.link_driver_telegram("d1", 3232)
    monkeypatch.setattr("bm_automation.app.db.get_storage", lambda: st)
    monkeypatch.setattr(roles, "telegram_settings",
                        lambda: _tg(admin_ids="111", default_role="viewer"))
    from bm_automation.app.core import bot_users
    bot_users.record_user(3232, role="DISPATCHER")
    try:
        assert roles.resolve_role(3232) is roles.Role.DISPATCHER
    finally:
        bot_users._LOCK.acquire()
        try:
            bot_users._CACHE.pop("3232", None)
            bot_users._save()
        finally:
            bot_users._LOCK.release()


def test_build_rows_collects_driver_ids(monkeypatch, tmp_path):
    """Grafik qatorlarida haydovchi ID'lari yig'iladi (postda ko'rsatish uchun)."""
    from bm_automation.app.services import driver_sheet_service as dss

    class _FakeClient:
        def get(self, *_a, **_k):
            return {}

    duty = {
        "graphs": [{
            "graphName": "P1", "driverName": "ALIYEV ALI",
            "driverId": "drv-1", "hasSecond": True,
            "secondDriverName": "KARIMOV VALI", "secondDriverId": "drv-2",
            "plateNum": "01A001", "startTime": "06:00",
            "endTime": "18:00", "shiftName": "KUN", "shiftGraphId": "sg1",
        }],
    }
    monkeypatch.setattr(dss, "graph_start_direction",
                        lambda *a, **k: "UP")
    monkeypatch.setattr(dss, "konechka_names",
                        lambda *a, **k: {"UP": "A", "DOWN": "B"})
    data = dss.build_rows(_FakeClient(), "route-1", "2026-09-23",
                          duty_data=duty)
    ids = {d["id"] for d in data["drivers"]}
    assert ids == {"drv-1", "drv-2"}


def test_send_photo_album_single_file_id(monkeypatch):
    """bitta file_id rasm — sendPhotoFileId ga yo'naltiriladi."""
    calls = []
    monkeypatch.setattr(tg, "telegram_settings", lambda: {
        "token": "T", "chat_id": "1", "driver_chat_id": ""})
    monkeypatch.setattr(tg, "send_photo_file_id",
                        lambda fid, cap, chat_id=None, **kw:
                        calls.append((fid, cap, chat_id)) or True)
    ok = tg.send_photo_album([{"file_id": "F1"}], caption="cap",
                             chat_id="9")
    assert ok is True
    assert calls == [("F1", "cap", "9")]


def test_send_photo_album_group_builds_media(monkeypatch):
    """bir necha rasm — sendMediaGroup'ga attach:// media quriladi."""
    calls = []
    monkeypatch.setattr(tg, "telegram_settings", lambda: {
        "token": "T", "chat_id": "1", "driver_chat_id": ""})

    def _fake_call(method, payload=None, files=None):
        calls.append((method, payload, files or {}))
        return {"result": []}

    monkeypatch.setattr(tg, "telegram_call", _fake_call)
    p1 = tmp_png = "reports/_t_album_a.png"
    p2 = "reports/_t_album_b.png"
    from pathlib import Path as _P
    for f in (p1, p2):
        _P(f).parent.mkdir(exist_ok=True)
        _P(f).write_bytes(b"\x89PNG fake")
    try:
        ok = tg.send_photo_album(
            [{"path": p1}, {"file_id": "FID2"}], caption="Jadval",
            chat_id="9")
        assert ok is True
        method, payload, files = calls[0]
        assert method == "sendMediaGroup"
        media = __import__("json").loads(payload["media"])
        assert media[0]["media"].startswith("attach://")
        assert media[0]["caption"] == "Jadval"
        assert media[1]["media"] == "FID2"
        assert "photo0" in files
    finally:
        for f in (p1, p2):
            _P(f).unlink(missing_ok=True)


# ------------------------------------------------------------- /notify oqimi

def _notify_flow(monkeypatch):
    sent = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, text, markup=None, **kw:
                        sent.append(text))
    monkeypatch.setattr(dispatch.kb, "main_menu_kb", lambda cid=None: "MM")
    monkeypatch.setattr(dispatch.kb, "back_kb", lambda *a, **k: "BK")
    with dispatch._NOTIFY_LOCK:
        dispatch._NOTIFY_STATE.clear()
    return sent


def test_notify_cancel_clears_state(monkeypatch):
    """Bug 6: /cancel buyrug'i oqim holatini tozalashi kerak."""
    sent = _notify_flow(monkeypatch)
    dispatch._start_notify(777)
    with dispatch._NOTIFY_LOCK:
        assert 777 in dispatch._NOTIFY_STATE
    dispatch.handle_message(777, "/cancel")
    with dispatch._NOTIFY_LOCK:
        assert 777 not in dispatch._NOTIFY_STATE
    assert any("Bekor qilindi" in t for t in sent)


def test_notify_continue_cancels_via_keyword(monkeypatch):
    """Bug 6: oqim qadamida '❌ Bekor qilish' — holat tozalanadi."""
    sent = _notify_flow(monkeypatch)
    dispatch._start_notify(778)
    dispatch._continue_notify(778, "❌ Bekor qilish")
    with dispatch._NOTIFY_LOCK:
        assert 778 not in dispatch._NOTIFY_STATE
    assert any("Bekor qilindi" in t for t in sent)


def test_notify_stale_state_expires(monkeypatch):
    """Bug 6: 15 daqiqadan eski oqim tozalanadi va qayta /notify talab."""
    sent = _notify_flow(monkeypatch)
    dispatch._start_notify(779)
    # vaqtni sun'iy eskirayamiz
    old = dispatch._time_now() - 16 * 60
    with dispatch._NOTIFY_LOCK:
        dispatch._NOTIFY_STATE[779]["ts"] = old
    dispatch._continue_notify(779, "901234567")
    with dispatch._NOTIFY_LOCK:
        assert 779 not in dispatch._NOTIFY_STATE
    assert any("muddati tugadi" in t for t in sent)


def test_notify_stale_state_sends_restart_prompt(monkeypatch):
    """Eski holat — /notify rasmga qayta yuborish so'raladi."""
    sent = _notify_flow(monkeypatch)
    with dispatch._NOTIFY_LOCK:
        dispatch._NOTIFY_STATE[780] = {"step": "message",
                                       "ts": dispatch._time_now() - 60 * 60}
    dispatch._continue_notify(780, "salom")
    assert any("muddati tugadi" in t for t in sent)
    with dispatch._NOTIFY_LOCK:
        assert 780 not in dispatch._NOTIFY_STATE


def test_notify_phone_step_unknown_phone(monkeypatch, tmp_path):
    """Bog'lanmagan raqam — so'raladi, holat ochiq qoladi."""
    sent = _notify_flow(monkeypatch)
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "n1.db"))))
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    dispatch._start_notify(781)
    dispatch._continue_notify(781, "999777111")
    with dispatch._NOTIFY_LOCK:
        assert 781 in dispatch._NOTIFY_STATE
    assert any("topilmadi" in t for t in sent)


def test_notify_phone_step_found_then_message(monkeypatch, tmp_path):
    """Raqam topilsa — 'message' qadamiga o'tadi va xabar yuboriladi."""
    sent = _notify_flow(monkeypatch)
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "n2.db"))))
    st.save_driver_profile("d1", notification_target="901234567")
    st.link_driver_telegram("d1", 4444)
    monkeypatch.setattr(dispatch, "get_storage", lambda: st)
    sent_upd = []
    monkeypatch.setattr(tg, "send_message",
                        lambda text, chat_id=None, **kw:
                        sent_upd.append((chat_id, text)))
    dispatch._start_notify(782)
    dispatch._continue_notify(782, "901234567")
    with dispatch._NOTIFY_LOCK:
        assert dispatch._NOTIFY_STATE[782]["step"] == "message"
    dispatch._continue_notify(782, "ertaga 06:00")
    assert any("yuborildi" in t for t in sent)
    assert sent_upd and sent_upd[0][0] == "4444"


def test_find_driver_by_phone_many_rows(tmp_path):
    """Bug 2: 1000+ yozuvda ham fallback skaner to'g'ri ishlaydi."""
    st = _seed(storage_for(SQLiteDatabase(str(tmp_path / "big.db"))))
    for i in range(1100):
        st.save_driver_profile(f"bx{i}",
                               phone=f"998{i:06d}")
    st.save_driver_profile("needle", phone="9981100000")
    row = st.find_driver_by_phone("1100000")
    assert row and row["driver_id"] == "needle"
    assert st.find_driver_by_phone("999999") is None


# -------------------------------------------- guruhda /chiqish buyruqlari

def _group_upd(text, sender_id=111):
    return {
        "message": {
            "chat": {"id": -100500, "type": "supergroup",
                     "title": "Haydovchilar"},
            "from": {"id": sender_id, "first_name": "Admin",
                     "is_bot": False},
            "text": text,
        }
    }


def test_group_chiqish_commands(monkeypatch, tmp_path):
    from bm_automation.app.notifications.ops import group_departures, ops
    monkeypatch.setattr(group_departures, "STATE_FILE", tmp_path / "gd.json")
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(ops, "_bot_mentioned", lambda upd: False)
    sent = []
    monkeypatch.setattr(ops, "send_message",
                        lambda text, chat_id=None, parse_mode=None:
                        sent.append((chat_id, text)))

    # /chiqish_off@Bot — guruh o'chiriladi (username bilan ham ishlaydi).
    ops._handle_group_update(_group_upd("/chiqish_off@SomeBot"), -100500)
    assert group_departures.chat_enabled(-100500) is False
    assert sent[-1][0] == -100500 and "o'chirildi" in sent[-1][1]

    # /chiqish_on — global off bo'lsa ham guruh qayta yoqiladi.
    monkeypatch.setattr(group_departures, "enabled", lambda: False)
    ops._handle_group_update(_group_upd("/chiqish_on"), -100500)
    assert group_departures.chat_enabled(-100500) is True
    assert sent[-1][0] == -100500 and "yoqildi" in sent[-1][1]

    # /chiqish — holat so'rovi.
    ops._handle_group_update(_group_upd("/chiqish"), -100500)
    assert "YOQILGAN" in sent[-1][1]


def test_group_chiqish_role_gated(monkeypatch, tmp_path):
    from bm_automation.app.notifications.ops import group_departures, ops
    monkeypatch.setattr(group_departures, "STATE_FILE", tmp_path / "gd.json")
    monkeypatch.setattr(group_departures, "enabled", lambda: True)
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.VIEWER)
    monkeypatch.setattr(ops, "_bot_mentioned", lambda upd: False)
    sent = []
    monkeypatch.setattr(ops, "send_message",
                        lambda text, chat_id=None, parse_mode=None:
                        sent.append((chat_id, text)))
    # Oddiy a'zo (/chiqish_off) — javob yo'q, holat o'zgarmaydi.
    ops._handle_group_update(_group_upd("/chiqish_off"), -100500)
    assert sent == []
    assert group_departures.chat_enabled(-100500) is True
