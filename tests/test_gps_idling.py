"""Bekorchi ishlash (idling) nazorati testlari."""

from datetime import datetime, timedelta, timezone

from bm_automation.app.gps import idling
from bm_automation.app.gps import storage as gstorage

_T0 = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)


def _setup(monkeypatch, calls):
    monkeypatch.setattr(idling, "_state", {})
    monkeypatch.setattr(idling.config, "IDLE_MIN_S", 600)
    monkeypatch.setattr(idling.config, "IDLE_MAX_SPEED_KMH", 0)
    monkeypatch.setattr(idling.config, "IDLE_TELEGRAM", False)
    monkeypatch.setattr(gstorage, "insert_alert", lambda **kw: calls.append(kw))
    monkeypatch.setattr(
        "bm_automation.app.gps.geofence._vehicle_for", lambda imei: "")


def test_idling_alert_after_min_duration(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0)
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0 + timedelta(seconds=300))
    assert calls == []  # 5 daqiqa < 10 daqiqa
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0 + timedelta(seconds=601))
    assert len(calls) == 1
    assert calls[0]["kind"] == "idling"
    # epizod ichida takrorlanmaydi
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0 + timedelta(seconds=900))
    assert len(calls) == 1


def test_idling_reset_on_movement(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0)  # boshlanish
    idling.check_position("111", 41.0, 69.0, 30, 1, _T0 + timedelta(seconds=120))
    # harakat → epizod yopiladi; yana 10 daqiqa kerak
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0 + timedelta(seconds=200))
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0 + timedelta(seconds=900))
    assert len(calls) == 1


def test_idling_ignores_engine_off(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    idling.check_position("111", 41.0, 69.0, 0, 0, _T0)
    idling.check_position("111", 41.0, 69.0, 0, 0, _T0 + timedelta(seconds=900))
    assert calls == []


def test_idling_no_state_on_first_idle_point(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    idling.check_position("111", 41.0, 69.0, 0, 1, _T0)
    assert idling._state["111"]["since"] is not None
    assert idling._state["111"]["alerted"] is False
