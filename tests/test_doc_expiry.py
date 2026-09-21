"""Hujjat muddati bildirishnomasi testlari (doc_expiry)."""

import datetime

from bm_automation.app.notifications.ops import doc_expiry


def test_no_expiry_or_healthy_returns_none():
    assert doc_expiry._build_text({"passport_expiry": "", "license_expiry": ""}) is None
    assert doc_expiry._build_text({"passport_expiry": "2035-01-01",
                                   "license_expiry": "2035-01-01"}) is None


def test_soon_expiry_included():
    soon = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    text = doc_expiry._build_text({"passport_expiry": soon, "license_expiry": ""})
    assert text is not None
    assert "Passport" in text
    assert "⚠️" in text


def test_expired_included():
    text = doc_expiry._build_text({"passport_expiry": "2020-01-01",
                                   "license_expiry": "2035-01-01"})
    assert text is not None
    assert "o'tgan" in text


def test_status_text_mentions_daily():
    assert "kuniga bir marta" in doc_expiry.status_text()


def test_check_and_send_sends_to_linked(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(doc_expiry, "STATE_FILE", tmp_path / "doc_expiry.json")
    monkeypatch.setattr(doc_expiry, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(doc_expiry, "_recipients",
                        lambda: [{"telegram_chat_id": "555",
                                  "passport_expiry": "2026-10-01",
                                  "license_expiry": ""}])
    monkeypatch.setattr(doc_expiry, "_send_to",
                        lambda cid, text: sent.append((cid, text)))
    doc_expiry._LAST_CHECK_AT = 0.0
    doc_expiry.check_and_send()
    assert sent, "bog'langan haydovchiga eslatma yuborilishi kerak"
    assert sent[0][0] == "555"

    # Kuniga bir marta — ikkinchi chaqiruv dublikat yubormaydi.
    sent.clear()
    doc_expiry.check_and_send()
    assert not sent