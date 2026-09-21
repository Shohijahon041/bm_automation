"""Oylik natijalar bildirishnomasi testlari (monthly_results)."""

import datetime

from bm_automation.app.notifications.ops import monthly_results


def test_prev_month_january_wraps():
    assert monthly_results._prev_month(datetime.date(2026, 1, 5)) == "2025-12"
    assert monthly_results._prev_month(datetime.date(2026, 5, 1)) == "2026-04"


def test_status_text_mentions_first_day():
    assert "1-kunida" in monthly_results.status_text()


def _fake_date(day: int):
    class FakeDate:
        @staticmethod
        def today():
            return datetime.date(2026, 5, day)
    return FakeDate


def test_check_and_send_noop_when_not_first_day(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(monthly_results, "STATE_FILE", tmp_path / "m.json")
    monkeypatch.setattr(monthly_results, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(monthly_results, "_recipients",
                        lambda: [{"driver_id": "d1", "telegram_chat_id": "777"}])
    monkeypatch.setattr(monthly_results, "_send_to",
                        lambda cid, text: sent.append((cid, text)))
    monkeypatch.setattr(monthly_results, "date", _fake_date(20))
    monthly_results._LAST_CHECK_AT = 0.0
    monthly_results.check_and_send()
    assert not sent


def test_check_and_send_sends_on_first_day(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(monthly_results, "STATE_FILE", tmp_path / "m.json")
    monkeypatch.setattr(monthly_results, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(monthly_results, "_recipients",
                        lambda: [{"driver_id": "d1", "telegram_chat_id": "777"}])
    monkeypatch.setattr(monthly_results, "_send_to",
                        lambda cid, text: sent.append((cid, text)))
    monkeypatch.setattr(monthly_results, "driver_inbox",
                        lambda did, f, chat_id=None: ("📊 Natijalar", {}))
    monkeypatch.setattr(monthly_results, "date", _fake_date(1))
    monthly_results._LAST_CHECK_AT = 0.0
    monthly_results.check_and_send()
    assert sent, "1-kunida haydovchiga natijalar yuborilishi kerak"
    assert sent[0][0] == "777"

    # Bir oyda bir marta — ikkinchi chaqiruv dublikat yubormaydi.
    sent.clear()
    monthly_results.check_and_send()
    assert not sent