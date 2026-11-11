"""Erta qatnov ogohlantirishi (favqulodda) testlari."""

from datetime import datetime, timedelta

import pytest

from bm_automation.app.core import bot_settings
from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications import sms_notify, telegram
from bm_automation.app.notifications.ops import early_start, group_departures
from bm_automation.app.repositories.duty_repo import DutyRepository
from tests.sqlite_backend import SQLiteDatabase

NOW = datetime(2026, 11, 5, 10, 6, 0)


def _storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "es.db"))
    st = storage_for(db)
    st.save_route("r1", name="B-80")
    st.save_driver("d1", full_name="Ali Aliyev", route_id="r1")
    st.save_driver("d2", full_name="Kamol Karimov", route_id="r1")
    st.save_vehicle("v1", plate_number="01-230 ABC", route_id="r1")
    return st


def _row(**kw):
    base = {
        "date": "2026-11-05",
        "routeVariantId": "r1",
        "shiftGraphId": "sg1",
        "tripNum": 1,
        "startTm": "2026-11-05T10:00:00",
        "endTm": "2026-11-05T10:40:00",
        "direction": "UP",
        "driverId": "d1",
        "driverName": "ALI ALIYEV",
        "plateNum": "01-230 ABC",
        "routeName": "B-80",
        "tripStatus": "ACCEPTED",
    }
    base.update(kw)
    return base


SLOTS = {
    "sg1": {
        "upShiftTimes": [
            {"index": 1, "type": "START", "direction": "UP",
             "arrivalTime": "10:10:00", "departureTime": "10:10:00"},
            {"index": 2, "type": "BETWEEN", "direction": "UP",
             "arrivalTime": "11:00:00", "departureTime": "11:00:00"},
        ],
        "downShiftTimes": [
            # Boshqa yo'nalish boshlamaydi — global indekssiz joy egallovchi.
            {"index": None, "type": None, "direction": "DOWN",
             "arrivalTime": None, "departureTime": None},
        ],
    },
}


def _graph_times(shift_graph_id):
    return SLOTS.get(shift_graph_id, {"upShiftTimes": [], "downShiftTimes": []})


@pytest.fixture
def wired(tmp_path, monkeypatch):
    st = _storage(tmp_path)
    sent_msgs: list[tuple[str, str]] = []
    sms_calls: list[tuple[str, str]] = []
    targets = {"r1": ["-1001"]}

    monkeypatch.setattr(early_start, "STATE_FILE", tmp_path / "es.json")
    monkeypatch.setattr(early_start, "enabled", lambda: True)
    monkeypatch.setattr(early_start, "get_storage", lambda: st)
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [_row()])
    monkeypatch.setattr(
        DutyRepository, "shift_graph_times",
        lambda self, sgid, **_kw: _graph_times(sgid))
    monkeypatch.setattr(group_departures, "_targets_for",
                        lambda route_id: list(targets.get(route_id, [])))
    monkeypatch.setattr(group_departures, "chat_enabled", lambda cid: False)

    def fake_send(text, chat_id=None, **_kw):
        sent_msgs.append((str(chat_id), text))
        return True

    def fake_sms(phone, text):
        sms_calls.append((phone, text))
        return {"ok": True, "status": "SENT", "message_id": "m1",
                "error": "", "phone": phone}

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(sms_notify, "send_sms", fake_sms)
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 5)
    # Natija (follow-up) xabarlari alohida testlarda; eski testlar ta'sirlanmasin.
    monkeypatch.setattr(bot_settings, "early_start_followup_min", lambda: 0)
    return {"st": st, "msgs": sent_msgs, "sms": sms_calls, "targets": targets}


def test_legacy_list_plan_is_refetched(wired):
    """Eski formatdagi (list) reja keshi xato bermaydi — qayta so'raladi."""
    import json

    el = early_start.STATE_FILE
    el.write_text(json.dumps({
        "date": "2026-11-05",
        "plans": {"sg1": [{"index": 1, "departureTime": "10:10:00"}]},
        "sent": {}, "attempts": {}, "followup": {}, "delivered": {},
    }), encoding="utf-8")

    res = early_start._run_once(now=NOW)

    assert res["alerts"] == 1
    state = json.loads(el.read_text(encoding="utf-8"))
    assert isinstance(state["plans"]["sg1"], dict)


def test_detects_early_and_sends_three_channels(wired):
    st = wired["st"]
    st.save_driver_profile("d1", phone="+998901234567",
                           telegram_chat_id="555",
                           notification_enabled=1)

    res = early_start._run_once(now=NOW)

    assert res["alerts"] == 1
    chats = {c for c, _ in wired["msgs"]}
    assert "-1001" in chats          # korxona guruhi
    assert "555" in chats            # haydovchi profili (DM)
    assert len(wired["sms"]) == 1
    phone, sms_text = wired["sms"][0]
    assert phone == "+998901234567"
    assert "ERTA QATNOV" in sms_text and len(sms_text) <= 160

    logs = st.query("SELECT * FROM sms_log")
    assert len(logs) == 1
    # Favqulodda SMS kunlik 2 ta limitni egallamaydi.
    assert logs[0]["schedule_date"] == ""
    assert sms_notify._day_send_count(
        st, "2026-11-05", "d1", "+998901234567") == 0


def test_plans_prefetched_for_upcoming_trip(wired, monkeypatch):
    """Qatnov boshlanishidan oldin reja yuklanadi — birinchi alert tezroq."""
    called = []
    monkeypatch.setattr(early_start, "_ensure_plans",
                        lambda client, sgids, state, today: called.extend(sgids))
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:20:00")])  # NOW 10:06 — 14 daq oldin

    res = early_start._run_once(now=NOW)
    assert "sg1" in called
    assert res["alerts"] == 0


def test_failed_channel_is_retried_without_repeating_group(wired, monkeypatch):
    """Guruh va DM ketdi, SMS xato — keyingi sikl faqat SMS'ni qayta urinadi."""
    st = wired["st"]
    st.save_driver_profile("d1", phone="+998901234567",
                           telegram_chat_id="555",
                           notification_enabled=1)
    calls = {"sms": 0}

    def flaky_sms(phone, text):
        calls["sms"] += 1
        return {"ok": False, "status": "FAILED", "message_id": "",
                "error": "operator", "phone": phone}

    monkeypatch.setattr(sms_notify, "send_sms", flaky_sms)

    assert early_start._run_once(now=NOW)["alerts"] == 1
    n_msgs = len(wired["msgs"])
    assert n_msgs == 2 and calls["sms"] == 1   # guruh + DM; SMS xato

    res = early_start._run_once(now=NOW + timedelta(seconds=5))
    assert res["alerts"] == 0              # yangi hodisa emas
    assert len(wired["msgs"]) == n_msgs    # guruh/DM takrorlanmaydi
    assert calls["sms"] == 2               # faqat SMS qayta urinildi


def test_group_message_sent_even_when_chat_disabled(wired):
    # chat_enabled=False — guruh sozlamasi o'chirilgan, lekin istisno ishlaydi.
    early_start._run_once(now=NOW)
    assert any(c == "-1001" for c, _ in wired["msgs"])


def test_on_time_and_late_are_silent(wired, monkeypatch):
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today: [_row(startTm="2026-11-05T10:10:30")])
    assert early_start._run_once(now=NOW)["alerts"] == 0

    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today: [_row(startTm="2026-11-05T10:25:00")])
    assert early_start._run_once(now=NOW)["alerts"] == 0


def test_one_minute_threshold_boundary(wired, monkeypatch):
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 1)
    now = datetime(2026, 11, 5, 10, 9, 40)
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:09:30")])
    assert early_start._run_once(now=now)["alerts"] == 0
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:09:00")])
    assert early_start._run_once(now=now)["alerts"] == 1


def test_old_start_outside_window_is_ignored(wired, monkeypatch):
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today: [_row(startTm="2026-11-05T06:00:00")])
    assert early_start._run_once(now=NOW)["alerts"] == 0


def test_second_run_does_not_repeat(wired):
    early_start._run_once(now=NOW)
    n = len(wired["msgs"])
    res = early_start._run_once(now=NOW)
    assert res["alerts"] == 0
    assert len(wired["msgs"]) == n


def test_unknown_route_does_not_leak_to_other_group(wired, monkeypatch):
    monkeypatch.setattr(group_departures, "_targets_for", lambda rid: [])
    early_start._run_once(now=NOW)
    assert all(c != "-1001" for c, _ in wired["msgs"])


def test_tripnum_uses_merged_plan_index(wired, monkeypatch):
    # Ikkinchi slot (11:00) — tripNum=2, haqiqiy 10:50 → 10 daq erta.
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(tripNum=2, startTm="2026-11-05T10:50:00")])
    now = datetime(2026, 11, 5, 10, 52, 0)
    assert early_start._run_once(now=now)["alerts"] == 1


def test_tripnum_out_of_range_is_ignored(wired, monkeypatch):
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(tripNum=99, startTm="2026-11-05T10:00:00")])
    assert early_start._run_once(now=NOW)["alerts"] == 0


def test_bm_flag_alerts_even_when_plan_says_late(wired, monkeypatch):
    """BM bayrog'i asosiy manba: reja bo'yicha kech bo'lsa ham alert ketadi.

    Matn yolg'on "erta" daqiqa ko'rsatmaydi — kechikish oshkor yoziladi.
    """
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:12:00", tripStatus="NOT_ACCEPTED",
             brokenQualities=["SUITABLE_PLAN_TIME_QUALITY"])])
    res = early_start._run_once(now=datetime(2026, 11, 5, 10, 13))
    assert res["alerts"] == 1
    html = wired["msgs"][0][1]
    assert "REJA VAQTIDAN CHETLASHGAN QATNOV" in html
    assert "BM bayrog'i: 2 daq kech" in html
    assert "ERTA QATNOV BOSHLANDI" not in html


def test_bm_flag_ignored_when_accepted(wired, monkeypatch):
    """BM sahifasidagi filtr kabi: faqat qabul qilinmagan reys hisoblanadi."""
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:12:00", tripStatus="ACCEPTED",
             brokenQualities=["SUITABLE_PLAN_TIME_QUALITY"])])
    assert early_start._run_once(
        now=datetime(2026, 11, 5, 10, 13))["alerts"] == 0


def test_bm_flag_without_plan_still_alerts(wired, monkeypatch):
    """Reja olinmasa ham BM bayrog'i yetarli."""
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {})
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:05:30", tripStatus="NOT_ACCEPTED",
             brokenQualities=["PASSENGER_OVERFLOW",
                              "SUITABLE_PLAN_TIME_QUALITY"])])
    assert early_start._run_once(now=NOW)["alerts"] == 1


def test_plan_interval_start_is_ontime(wired, monkeypatch):
    """Reja oralig'i (kelish–jo'nash): oraliq ichida boshlash erta emas."""
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {
        "1": {"arr": 10 * 3600 + 10 * 60, "dep": 10 * 3600 + 20 * 60,
              "type": "START"}})
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:15:00")])
    assert early_start._run_once(
        now=datetime(2026, 11, 5, 10, 16))["alerts"] == 0

    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(startTm="2026-11-05T10:03:00")])
    assert early_start._run_once(
        now=datetime(2026, 11, 5, 10, 4))["alerts"] == 1


def test_delta_handles_midnight_wrap():
    assert early_start._delta_sec(10 * 60, 23 * 3600 + 50 * 60) == 20 * 60
    assert early_start._delta_sec(23 * 3600 + 50 * 60, 10 * 60) == -20 * 60


def test_fetch_uses_short_timeout(monkeypatch):
    from bm_automation.app.core import profiles as profiles_mod
    from bm_automation.app.repositories.waybill_repo import WaybillRepository

    calls = {}

    def fake_report(self, rid, from_date, to_date, timeout=None):
        calls["timeout"] = timeout
        return {"data": []}

    monkeypatch.setattr(early_start, "_profile_client",
                        lambda profile: object())
    monkeypatch.setattr(profiles_mod, "all_profiles",
                        lambda: [{"name": "X", "routeVariantId": "r1"}])
    monkeypatch.setattr(WaybillRepository, "report", fake_report)

    early_start._fetch_rows("2026-11-05")
    assert calls["timeout"] == early_start._FETCH_TIMEOUT_S


def test_plan_uses_no_retry_and_short_timeout(monkeypatch):
    calls = {}

    def fake_times(self, sgid, timeout=None, retries=None):
        calls.update(timeout=timeout, retries=retries)
        return {
            "upShiftTimes": [
                {"index": 1, "type": "START",
                 "arrivalTime": "10:00:00", "departureTime": "10:05:00"}],
            "downShiftTimes": [],
        }

    monkeypatch.setattr(DutyRepository, "shift_graph_times", fake_times)
    assert early_start._graph_plan(object(), "sg1") == {
        "1": {"arr": 10 * 3600, "dep": 10 * 3600 + 300, "type": "START"}}
    assert calls == {"timeout": early_start._PLAN_TIMEOUT_S, "retries": 0}


def test_start_worker_starts_once(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None, **_kw):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(early_start.threading, "Thread", FakeThread)
    monkeypatch.setattr(early_start, "_WORKER_STARTED", False)
    early_start.start_worker()
    early_start.start_worker()
    assert started == ["early-start"]


def test_settings_clamped():
    assert bot_settings.set_early_start_threshold_min(0) == 1
    assert bot_settings.set_early_start_threshold_min(999) == 60
    assert bot_settings.set_early_start_interval_s(1) == 5
    assert bot_settings.set_early_start_interval_s(9999) == 600
    assert bot_settings.set_early_start_threshold_min(7) == 7
    assert bot_settings.early_start_threshold_min() == 7
    # Natija eslatmasi kechikishi: 0..120 daqiqa (0 — o'chirilgan).
    assert bot_settings.set_early_start_followup_min(-5) == 0
    assert bot_settings.set_early_start_followup_min(999) == 120
    assert bot_settings.set_early_start_followup_min(15) == 15
    assert bot_settings.early_start_followup_min() == 15


# ---------------------------------------------------------- reys natijasi

def _profile_with_phone(st):
    st.save_driver_profile("d1", phone="+998901234567",
                           telegram_chat_id="555",
                           notification_enabled=1)


def test_followup_accepted_after_status(wired, monkeypatch):
    """Reys BM'da qabul qilinsa — guruh+DM "REYS QABUL QILINDI", SMS yo'q."""
    _profile_with_phone(wired["st"])
    monkeypatch.setattr(bot_settings, "early_start_followup_min", lambda: 10)

    assert early_start._run_once(now=NOW)["alerts"] == 1
    n_msgs, n_sms = len(wired["msgs"]), len(wired["sms"])

    # Keyingi sikl: tripStatus=ACCEPTED → darhol yopiladi (kechikish kutilmaydi).
    res = early_start._run_once(now=NOW + timedelta(minutes=1))
    assert res["alerts"] == 0 and res["followups"] == 1
    fresh = [t for _, t in wired["msgs"][n_msgs:]]
    assert any("REYS QABUL QILINDI" in t for t in fresh)
    assert len(wired["sms"]) == n_sms          # qabul qilindi — SMS ketmaydi

    # Bir marta yuborilgach takrorlanmaydi.
    again = early_start._run_once(now=NOW + timedelta(minutes=2))
    assert again["followups"] == 0
    assert len(wired["msgs"]) == n_msgs + len(fresh)


def test_followup_missed_after_delay_names_driver(wired, monkeypatch):
    """Qatnov tugab, kechikish o'tgach ham qabul qilinmasa — haydovchi aybi."""
    _profile_with_phone(wired["st"])
    monkeypatch.setattr(bot_settings, "early_start_followup_min", lambda: 10)
    monkeypatch.setattr(early_start, "_fetch_rows", lambda today: [
        _row(tripStatus="PENDING_ACCESS")])

    assert early_start._run_once(now=NOW)["alerts"] == 1
    n_msgs, n_sms = len(wired["msgs"]), len(wired["sms"])

    # endTm 10:40 + 10 daq = 10:50. 10:45 da hali jim.
    assert early_start._run_once(
        now=datetime(2026, 11, 5, 10, 45))["followups"] == 0
    assert len(wired["msgs"]) == n_msgs and len(wired["sms"]) == n_sms

    res = early_start._run_once(now=datetime(2026, 11, 5, 10, 51))
    assert res["followups"] == 1
    fresh = [t for _, t in wired["msgs"][n_msgs:]]
    assert any("REYS QABUL QILINMADI" in t and "aybi" in t for t in fresh)
    assert len(wired["sms"]) == n_sms + 1
    assert "haydovchi aybi" in wired["sms"][-1][1].lower()

    # Eslatma takrorlanmaydi.
    assert early_start._run_once(
        now=datetime(2026, 11, 5, 10, 52))["followups"] == 0


def test_followup_disabled_is_silent(wired):
    """0 daqiqa — natija xabarlari o'chirilgan (faqat dastlabki alert)."""
    _profile_with_phone(wired["st"])
    assert early_start._run_once(now=NOW)["alerts"] == 1
    n = len(wired["msgs"])
    assert early_start._run_once(
        now=NOW + timedelta(minutes=15))["followups"] == 0
    assert len(wired["msgs"]) == n


def test_followup_texts_are_explicit():
    item = {"row": {"driverName": "ALI ALIYEV", "plateNum": "01-230 ABC",
                    "routeName": "B-80"},
            "planned_sec": 10 * 3600 + 10 * 60,
            "actual_sec": 10 * 3600,
            "early_min": 10}
    ok_html, ok_sms = early_start._followup_texts(item, accepted=True)
    assert "REYS QABUL QILINDI" in ok_html and ok_sms == ""
    bad_html, bad_sms = early_start._followup_texts(item, accepted=False)
    assert "REYS QABUL QILINMADI" in bad_html and "haydovchining aybi" in bad_html
    assert "haydovchi aybi" in bad_sms.lower() and len(bad_sms) <= 160


# --------------------------------------------------- aniq kunlik hisobot

def test_graph_plan_keeps_lunch_and_skips_end(monkeypatch):
    """LUNCH (nonushta/tushlik) ham reys — global indeksda qoladi; END tashlanadi."""
    slots = {
        "upShiftTimes": [
            {"index": 1, "type": "START", "arrivalTime": "06:00:00",
             "departureTime": "06:05:00"},
            {"index": 2, "type": "BETWEEN", "arrivalTime": "07:30:00",
             "departureTime": "07:35:00"},
            {"index": 3, "type": "LUNCH", "arrivalTime": "09:00:00",
             "departureTime": "09:40:00"},
            {"index": None, "type": "END", "arrivalTime": "20:00:00",
             "departureTime": "20:00:00"},
        ],
        "downShiftTimes": [
            {"index": 4, "type": "START", "arrivalTime": "",
             "departureTime": "21:00:00"},
        ],
    }

    monkeypatch.setattr(DutyRepository, "shift_graph_times",
                        lambda self, sgid, **_kw: slots)
    plan = early_start._graph_plan(object(), "sg1")
    assert sorted(plan) == ["1", "2", "3", "4"]
    assert plan["3"] == {"arr": 9 * 3600, "dep": 9 * 3600 + 40 * 60,
                         "type": "LUNCH"}
    # arrivalTime bo'sh bo'lsa oraliq departureTime bilan to'ldiriladi.
    assert plan["4"] == {"arr": 21 * 3600, "dep": 21 * 3600, "type": "START"}


def test_graph_plan_returns_empty_on_bad_shape(monkeypatch):
    """Xato javob indeksni siljitmasligi uchun bo'sh reja qaytadi."""
    monkeypatch.setattr(DutyRepository, "shift_graph_times",
                        lambda self, sgid, **_kw: {"detail": "error"})
    assert early_start._graph_plan(object(), "sg1") == {}


def test_report_counts_early_and_not_accepted(monkeypatch):
    early_start._REPORT_CACHE.clear()
    early_start._PLAN_CACHE.clear()
    rows = [
        _row(tripNum=1, startTm="2026-11-05T10:00:00", tripStatus="ACCEPTED"),
        _row(tripNum=2, startTm="2026-11-05T11:00:00", tripStatus="ACCEPTED"),
        _row(tripNum=3, startTm="2026-11-05T11:50:00",
             tripStatus="NOT_ACCEPTED", driverId="d2",
             driverName="KAMOL KARIMOV"),
    ]
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today, route_ids=None, errors=None, clients_out=None:
                        list(rows))
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {
        "1": {"arr": 10 * 3600 + 10 * 60, "dep": 10 * 3600 + 12 * 60,
              "type": "START"},
        "2": {"arr": 11 * 3600, "dep": 11 * 3600 + 2 * 60, "type": "BETWEEN"},
        "3": {"arr": 12 * 3600, "dep": 12 * 3600 + 2 * 60, "type": "LUNCH"},
    })
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 1)

    rep = early_start.early_start_report("2026-11-05")
    t = rep["totals"]
    assert t["total"] == 3
    assert t["measured"] == 3
    assert t["early"] == 2 and t["ontime"] == 1 and t["late"] == 0
    assert t["accepted"] == 2 and t["not_accepted"] == 1
    assert t["early_accepted"] == 1 and t["early_not_accepted"] == 1
    assert len(rep["rows"]) == 2 and len(rep["not_accepted_rows"]) == 1
    assert rep["rows"][0]["planned"] == "10:10"
    assert rep["rows"][0]["actual"] == "10:00"
    by_id = {d["id"]: d for d in rep["drivers"]}
    assert by_id["d1"]["early"] == 1
    assert by_id["d2"]["early"] == 1 and by_id["d2"]["not_accepted"] == 1


def test_report_marks_not_started_and_missing_plan(monkeypatch):
    early_start._REPORT_CACHE.clear()
    early_start._PLAN_CACHE.clear()
    rows = [
        _row(tripNum=1, startTm="", tripStatus="NOT_ACCEPTED"),
        _row(tripNum=9, startTm="2026-11-05T10:00:00",
             tripStatus="ACCEPTED"),
    ]
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today, route_ids=None, errors=None, clients_out=None:
                        list(rows))
    monkeypatch.setattr(early_start, "_graph_plan",
                        lambda client, sgid: {
                            "1": {"arr": 10 * 3600 + 10 * 60,
                                  "dep": 10 * 3600 + 10 * 60,
                                  "type": "START"}})
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 1)

    t = early_start.early_start_report("2026-11-05")["totals"]
    assert t["total"] == 2
    assert t["not_started"] == 1 and t["no_plan"] == 1
    assert t["not_accepted"] == 1 and t["accepted"] == 1


def test_report_marks_bm_flag_as_early(monkeypatch):
    """BM bayrog'i reja bo'yicha kech bo'lsa ham "erta" toifasiga tushadi."""
    early_start._REPORT_CACHE.clear()
    early_start._PLAN_CACHE.clear()
    rows = [
        _row(tripNum=1, startTm="2026-11-05T10:20:00",
             tripStatus="NOT_ACCEPTED",
             brokenQualities=["SUITABLE_PLAN_TIME_QUALITY"]),
        _row(tripNum=2, startTm="2026-11-05T11:00:00", tripStatus="ACCEPTED"),
    ]
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today, route_ids=None, errors=None, clients_out=None:
                        list(rows))
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {
        "1": {"arr": 10 * 3600 + 10 * 60, "dep": 10 * 3600 + 15 * 60,
              "type": "START"},
        "2": {"arr": 11 * 3600, "dep": 11 * 3600 + 5 * 60,
              "type": "BETWEEN"}})
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 5)

    rep = early_start.early_start_report("2026-11-05")
    assert rep["totals"]["bm_early"] == 1
    assert rep["totals"]["early"] == 1
    assert rep["rows"][0]["source"] == "bm"
    assert rep["rows"][0]["planned"] == "10:10"
    assert rep["rows"][0]["planned_end"] == "10:15"


def test_delta_does_not_wrap_evening_start_to_morning_plan():
    """Kechki reys ertalabki rejaga nisbatan "erta" bo'lib ko'rinmasligi kerak."""
    actual = 19 * 3600 + 33 * 60
    planned = 6 * 3600 + 41 * 60
    assert early_start._delta_sec(actual, planned) == actual - planned
    assert early_start._window_delta(actual, {"arr": planned, "dep": planned}) > 0


def test_report_ignores_far_evening_start(monkeypatch):
    early_start._REPORT_CACHE.clear()
    early_start._PLAN_CACHE.clear()
    rows = [_row(tripNum=1, startTm="2026-11-05T19:33:00",
                 tripStatus="NOT_ACCEPTED")]
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today, route_ids=None, errors=None, clients_out=None:
                        list(rows))
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {
        "1": {"arr": 6 * 3600 + 41 * 60, "dep": 6 * 3600 + 41 * 60,
              "type": "START"}})
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 1)

    t = early_start.early_start_report("2026-11-05")["totals"]
    assert t["early"] == 0 and t["late"] == 1


def test_report_avg_uses_only_measured_minutes(monkeypatch):
    """BM bayrog'i bor, lekin daqiqa hisoblanmagan reys o'rtachani buzmaydi."""
    early_start._REPORT_CACHE.clear()
    early_start._PLAN_CACHE.clear()
    rows = [
        _row(tripNum=1, startTm="2026-11-05T10:20:00",
             tripStatus="NOT_ACCEPTED",
             brokenQualities=["SUITABLE_PLAN_TIME_QUALITY"]),
        _row(tripNum=2, startTm="2026-11-05T09:58:00",
             tripStatus="NOT_ACCEPTED",
             brokenQualities=["SUITABLE_PLAN_TIME_QUALITY"]),
    ]
    monkeypatch.setattr(early_start, "_fresh_client", lambda: object())
    monkeypatch.setattr(early_start, "_fetch_rows",
                        lambda today, route_ids=None, errors=None, clients_out=None:
                        list(rows))
    monkeypatch.setattr(early_start, "_graph_plan", lambda client, sgid: {
        "1": {"arr": 10 * 3600 + 10 * 60, "dep": 10 * 3600 + 15 * 60,
              "type": "START"},
        "2": {"arr": 10 * 3600 + 10 * 60, "dep": 10 * 3600 + 15 * 60,
              "type": "BETWEEN"}})
    monkeypatch.setattr(bot_settings, "early_start_threshold_min", lambda: 5)

    rep = early_start.early_start_report("2026-11-05")
    t = rep["totals"]
    assert t["bm_early"] == 2 and t["early"] == 2
    # BM bayrog'ida daqiqa reja ketish vaqti (10:15) dan hisoblanadi.
    assert t["avg_early_min"] == 17.0 and t["max_early_min"] == 17.0
    by_trip = {r["trip_num"]: r for r in rep["rows"]}
    assert by_trip[1]["early_min"] == 0.0 and by_trip[1]["bm"] is True
    assert by_trip[2]["early_min"] == 17.0
    drv = {d["id"]: d for d in rep["drivers"]}["d1"]
    assert drv["early"] == 2 and drv["avg_early_min"] == 17.0


def test_profile_client_backoff_on_auth_error(monkeypatch):
    """401 dan keyin har 5 s login urinilmaydi — cooldown ishlaydi."""
    from bm_automation.app.core import companies

    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("kompaniya tokeni olinmadi: API xatosi (HTTP 401)")

    monkeypatch.setattr(companies, "client_for_profile", _boom)
    early_start._CLIENT_FAIL_UNTIL.clear()
    profile = {"name": "TEST BACKOFF"}
    try:
        assert early_start._profile_client(profile) is None
        assert early_start._profile_client(profile) is None
        assert calls["n"] == 1
    finally:
        early_start._CLIENT_FAIL_UNTIL.clear()


def test_profile_client_retries_after_cooldown(monkeypatch):
    """Muddat o'tgach qayta uriniladi; muvaffaqiyat cooldown'ni tozalaydi."""
    from bm_automation.app.core import companies

    calls = {"n": 0}
    sentinel = object()

    def _ok(*_a, **_k):
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(companies, "client_for_profile", _ok)
    early_start._CLIENT_FAIL_UNTIL.clear()
    profile = {"name": "TEST RETRY"}
    early_start._CLIENT_FAIL_UNTIL["TEST RETRY"] = 0.0
    try:
        assert early_start._profile_client(profile) is sentinel
        assert calls["n"] == 1
        assert "TEST RETRY" not in early_start._CLIENT_FAIL_UNTIL
    finally:
        early_start._CLIENT_FAIL_UNTIL.clear()
