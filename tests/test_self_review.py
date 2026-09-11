"""AI o'z-o'zini rivojlantirish (`self_review`) moduli testlari.

Quyidagilarni tekshiradi: analiz (takroriy xatolar guruhi, hafta kuni,
muvaffaqiyatsiz avto-ishlar, takroriy muammolar), tavsiyalar, hisobot matni,
`check_and_send` jadvali (soat/holat fayl), `send_now` va holat ekrani.
"""

import json
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from bm_automation.app.db.models import SyncSource, TripRecord
from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import self_review
from bm_automation.app.notifications.ops.roles import Role
from tests.sqlite_backend import SQLiteDatabase


def _tg(**overrides):
    base = {
        "token": "T", "chat_id": "", "driver_chat_id": "",
        "admin_ids": "111", "dispatcher_ids": "222", "manager_ids": "",
        "default_role": "viewer",
    }
    base.update(overrides)
    return base


def _add_error(st, source, message, occurred_at):
    st.db.execute(
        "INSERT INTO errors (source, message, traceback, context, occurred_at,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source, message, "", "{}", occurred_at,
         occurred_at, occurred_at))


def _trip(vid, drv, planned, status, day, actual=""):
    return TripRecord(date=day, route_id="r1", vehicle_id=vid, driver_id=drv,
                      planned_time=planned, actual_time=actual, status=status,
                      source=SyncSource.DUTY.value)


@pytest.fixture()
def sr_storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "sr.db"))
    st = storage_for(db)
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    for i, plate in enumerate(("01A001", "01A002", "01A003")):
        st.save_vehicle(external_id=f"v{i + 1}", plate_number=plate,
                        route_id="r1")
    for i, name in enumerate(("Aliyev", "Karimov", "Rasulov")):
        st.save_driver(external_id=f"d{i + 1}", full_name=name)
    return st


class _FakeTime:
    """`self_review.time` o'rnini bosadi: nazorat qilinadigan soat."""

    def __init__(self, hour=5):
        self.now = [1000.0]
        self.hour = hour

    def time(self):
        return self.now[0]

    def localtime(self):
        return time.struct_time((2026, 8, 15, self.hour, 0, 0, 5, 227, -1))


@pytest.fixture()
def sr_mod(sr_storage, tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(self_review, "get_storage", lambda: sr_storage)
    monkeypatch.setattr(self_review, "telegram_settings", lambda: _tg())
    monkeypatch.setattr(self_review, "configured_roles", lambda: {
        111: Role.ADMIN, 222: Role.DISPATCHER, 333: Role.VIEWER})
    monkeypatch.setattr(self_review, "openrouter", SimpleNamespace(
        configured=lambda: False, complete=lambda *a, **k: None))
    monkeypatch.setattr(self_review, "STATE_FILE", tmp_path / "sr_state.json")
    monkeypatch.setattr(self_review, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(self_review, "time", _FakeTime())
    monkeypatch.setattr(self_review, "send_message",
                        lambda text, chat_id="", reply_markup=None:
                        sent.append({"chat_id": chat_id, "text": text,
                                     "reply_markup": reply_markup}))
    return sr_storage, sent


# ------------------------------------------------------------- rejim/sozlar

def test_enabled_toggle(sr_mod, monkeypatch):
    sr_storage, _ = sr_mod
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview="on"))
    assert self_review.enabled() is True
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview="off"))
    assert self_review.enabled() is False


def test_hour_days_parsing_and_fallback(sr_mod, monkeypatch):
    _, _ = sr_mod
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview_hour="23", selfreview_days="90"))
    assert self_review._hour() == 23
    assert self_review._days() == 90
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview_hour="abc", selfreview_days="abc"))
    assert self_review._hour() == 9
    assert self_review._days() == 14
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview_days="-5"))
    assert self_review._days() == 1  # chegaraga qisqartiriladi


def test_norm_message_masks_digits():
    assert self_review._norm_message("HTTP 500 xato 42") == "HTTP # xato #"
    assert self_review._norm_message("") == "-"


# --------------------------------------------------------------- analiz

def test_analyze_error_grouping_and_weekday(sr_mod):
    st, _ = sr_mod
    base = (date.today() - timedelta(days=1)).isoformat()
    for msg in ("HTTP 500", "HTTP 501", "HTTP 502", "HTTP 503"):
        _add_error(st, "sync", msg, base + "T09:00:00+00:00")
    _add_error(st, "db", "connection refused", base + "T10:00:00+00:00")

    a = self_review.analyze(days=14)
    assert a["error_total"] == 5
    assert a["error_days"] == {base: 5}
    # "HTTP 5xx" bir xil normallashtirilgan matn orqali bitta guruhga yig'iladi
    top = a["top_errors"]
    assert top[0]["source"] == "sync"
    assert top[0]["norm"] == "HTTP #"
    assert top[0]["n"] == 4
    sources = {t["source"] for t in top}
    assert "db" in sources
    from bm_automation.app.notifications.ops.self_review import _WEEKDAYS
    assert a["weekday"] == _WEEKDAYS[(date.fromisoformat(base).weekday())]


def test_analyze_failures(sr_mod):
    st, _ = sr_mod
    started = date.today().isoformat() + "T08:00:00+00:00"
    st.record_automation_run(run_id="a1", trigger="scheduled",
                             started_at=started, status="STARTED")
    st.finish_automation_run("a1", status="ERROR")
    st.record_automation_run(run_id="a2", trigger="manual",
                             started_at=started, status="STARTED")
    st.finish_automation_run("a2", status="OK")

    a = self_review.analyze(days=14)
    assert a["failures"] == 1
    assert a["failure_triggers"] == {"scheduled": 1}


def test_analyze_recurring_gps_only_not_schedule(sr_mod):
    st, _ = sr_mod
    today = date.today()
    for i in range(4):  # oxirgi 7 kunning 4 tasi
        day = (today - timedelta(days=i)).isoformat()
        # v1 (01A001) — GPS muammosi (ACCEPTED, harakat qaydi yo'q)
        st.save_trip(_trip("v1", "d1", "06:00", "ACCEPTED", day))
        # v2 (01A002) — jadval muammosi (schedule) — hisobga olinmasligi kerak
        st.save_trip(_trip("v2", "d2", "07:00", "NOT_ACCEPTED", day))

    a = self_review.analyze(days=14)
    assert [r["vehicle"] for r in a["recurring"]] == ["01A001"]
    r = a["recurring"][0]
    assert r["category"] == "gps"
    assert r["days"] == 4
    assert r["window"] == 7


def test_analyze_recurring_cap_and_sort(sr_mod):
    st, _ = sr_mod
    today = date.today()
    # 6 ta avtobusning har biri kamida 3 kunda GPS muammosiga ega
    for i in range(3):
        day = (today - timedelta(days=i)).isoformat()
        for n in range(1, 7):
            st.save_trip(_trip(f"v{n}", f"d{n % 3 + 1}", f"0{n}:00",
                               "ACCEPTED", day))

    a = self_review.analyze(days=14)
    assert len(a["recurring"]) == 5  # ko'pi bilan 5 ta ko'rsatiladi
    assert all(r["days"] == 3 for r in a["recurring"])


def test_analyze_includes_log_tail(sr_mod):
    st, _ = sr_mod
    _add_error(st, "sync", "xato", date.today().isoformat() + "T09:00:00+00:00")
    a = self_review.analyze(days=14)
    assert isinstance(a["log_tail"], str)


# ------------------------------------------------------------ tavsiyalar

def test_rule_recs_populated(sr_mod):
    st, _ = sr_mod
    base = (date.today() - timedelta(days=1)).isoformat()
    _add_error(st, "sync", "HTTP 500", base + "T09:00:00+00:00")
    _add_error(st, "sync", "HTTP 500", base + "T10:00:00+00:00")
    started = date.today().isoformat() + "T08:00:00+00:00"
    st.record_automation_run(run_id="a1", trigger="scheduled",
                             started_at=started, status="STARTED")
    st.finish_automation_run("a1", status="ERROR")

    recs = self_review._rule_recs(self_review.analyze(days=14))
    joined = "\n".join(recs)
    assert "[sync]" in joined and "HTTP #" in joined
    assert "muvaffaqiyatsiz" in joined
    assert "scheduled" in joined
    assert len(recs) <= 10


def test_rule_recs_quiet(sr_mod):
    recs = self_review._rule_recs(self_review.analyze(days=14))
    assert "barqaror" in recs[0]


# --------------------------------------------------------------- matn

def test_build_text_plain_no_html(sr_mod):
    st, _ = sr_mod
    _add_error(st, "sync", "HTTP 500",
               date.today().isoformat() + "T09:00:00+00:00")
    text = self_review.build_text(html=False)
    assert "<" not in text
    assert "AI O'Z-O'ZINI RIVOJLANTIRISH" in text
    assert "XATOLAR" in text and "TAVSIYALAR" in text
    assert "HTTP #" in text


def test_build_text_html(sr_mod):
    text = self_review.build_text(html=True)
    assert "<b>" in text
    assert "TAKRORIY MUAMMOLAR" in text


def test_build_text_db_disabled(sr_mod, monkeypatch):
    _, _ = sr_mod
    monkeypatch.setattr(self_review, "get_storage",
                        lambda: SimpleNamespace(enabled=False))
    assert self_review.build_text() == "⚠️ Tahlil uchun DB yoqilmagan."


# ------------------------------------------------------- check_and_send

def test_check_and_send_targets_once_per_day(sr_mod, monkeypatch):
    st, sent = sr_mod
    monkeypatch.setattr(self_review, "_hour", lambda: 0)
    self_review.check_and_send()
    assert len(sent) == 2
    ids = sorted(int(s["chat_id"]) for s in sent)
    assert ids == [111, 222]  # VIEWER (333) olmaydi
    for s in sent:
        kb = s["reply_markup"]["inline_keyboard"]
        datas = [b["callback_data"] for row in kb for b in row]
        assert datas  # navigatsiya klaviaturasi bor
        assert "nav:insights" not in datas  # kunlik hisobot uni o'zi ko'rsatmaydi
    state = json.loads(self_review.STATE_FILE.read_text(encoding="utf-8"))
    assert state["date"] == date.today().isoformat()

    # Kunning ikkinchi chaqiruvi — holat fayl tufayli qayta yuborilmaydi
    self_review.time.now[0] += 1000  # interval chegarasidan o'tamiz
    self_review.check_and_send()
    assert len(sent) == 2


def test_check_and_send_disabled(sr_mod, monkeypatch):
    _, sent = sr_mod
    monkeypatch.setattr(self_review, "telegram_settings",
                        lambda: _tg(selfreview="off"))
    self_review.check_and_send()
    assert sent == []


def test_check_and_send_hour_gate(sr_mod, monkeypatch):
    _, sent = sr_mod
    monkeypatch.setattr(self_review, "_hour", lambda: 23)
    self_review.check_and_send()
    assert sent == []
    assert not self_review.STATE_FILE.exists()


def test_check_and_send_no_targets(sr_mod, monkeypatch):
    _, sent = sr_mod
    monkeypatch.setattr(self_review, "configured_roles",
                        lambda: {333: Role.VIEWER})
    monkeypatch.setattr(self_review, "_hour", lambda: 0)
    self_review.check_and_send()
    assert sent == []


def test_send_now_returns_and_saves(sr_mod):
    st, _ = sr_mod
    text = self_review.send_now()
    assert "AI O'Z-O'ZINI RIVOJLANTIRISH" in text
    state = json.loads(self_review.STATE_FILE.read_text(encoding="utf-8"))
    assert state["date"] == date.today().isoformat()


def test_status_text(sr_mod):
    st, _ = sr_mod
    status = self_review.status_text()
    assert "AI O'Z-O'ZINI RIVOJLANTIRISH" in status
    assert "bugun hali yuborilmagan" in status
    self_review.send_now()
    assert "✓ bugun yuborilgan" in self_review.status_text()
