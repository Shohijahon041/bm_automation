"""Aloqa uzilishi (offline) nazorati testlari."""

from datetime import datetime, timedelta, timezone

from bm_automation.app.gps import offline
from bm_automation.app.gps import storage as gstorage


def _dev(imei: str, minutes_ago: int, enabled: int = 1) -> dict:
    return {
        "imei": imei,
        "enabled": enabled,
        "vehicle_id": "",
        "name": "",
        "plate_number": "",
        "last_seen": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    }


def _setup(monkeypatch, calls):
    monkeypatch.setattr(offline, "_offline", set())
    monkeypatch.setattr(offline, "_seeded", True)
    monkeypatch.setattr(offline.config, "OFFLINE_AFTER_S", 1800)
    monkeypatch.setattr(offline.config, "OFFLINE_MAX_AGE_S", 86400)
    monkeypatch.setattr(offline.config, "OFFLINE_TELEGRAM", False)
    monkeypatch.setattr(gstorage, "insert_alert", lambda **kw: calls.append(kw))


def test_offline_alert_once_per_outage(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    devs = [_dev("111", 40), _dev("222", 5)]  # 111 uzoq vaqt yubormagan
    new = offline.check_devices(devs)
    assert [d["imei"] for d in new] == ["111"]
    assert len(calls) == 1
    assert calls[0]["kind"] == "offline"
    # qayta tekshiruv — takroriy alert yo'q
    offline.check_devices(devs)
    assert len(calls) == 1
    # qurilma qaytdi → holat online
    offline.check_devices([_dev("111", 1), _dev("222", 5)])
    assert "111" not in offline._offline


def test_offline_skips_never_seen(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    devs = [{"imei": "111", "enabled": 1, "last_seen": None}]
    assert offline.check_devices(devs) == []
    assert calls == []


def test_offline_skips_disabled(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    assert offline.check_devices([_dev("111", 60, enabled=0)]) == []
    assert calls == []


def test_offline_recovery_then_again(monkeypatch):
    calls = []
    _setup(monkeypatch, calls)
    offline.check_devices([_dev("111", 40)])
    assert len(calls) == 1
    offline.check_devices([_dev("111", 1)])   # qaytdi
    offline.check_devices([_dev("111", 50)])  # yana uzildi
    assert len(calls) == 2


def test_offline_warmup_silent_first_check(monkeypatch):
    """Ishga tushgandan keyingi birinchi tekshiruv alert bermaydi."""
    calls = []
    _setup(monkeypatch, calls)
    monkeypatch.setattr(offline, "_seeded", False)
    assert offline.check_devices([_dev("111", 40)]) == []
    assert calls == []
    assert "111" in offline._offline
    # qaytgach yana uzilsa — endi alert bo'ladi
    offline.check_devices([_dev("111", 1)])
    offline.check_devices([_dev("111", 40)])
    assert len(calls) == 1


def test_offline_skips_too_old(monkeypatch):
    """Juda uzoq vaqt o'chiq turgan qurilma ogohlantirilmaydi."""
    calls = []
    _setup(monkeypatch, calls)
    assert offline.check_devices([_dev("111", 60 * 24 * 2)]) == []
    assert calls == []
    assert "111" not in offline._offline
