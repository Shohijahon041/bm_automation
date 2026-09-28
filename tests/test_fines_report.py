"""Oy oxiri jarima hisoboti testlari (fines_report)."""

import datetime

from bm_automation.app.notifications.ops import fines_report


def test_last_day_of_month():
    d = fines_report._last_day_of_month(datetime.date(2026, 2, 10))
    assert d == 28
    assert fines_report._last_day_of_month(datetime.date(2024, 2, 10)) == 29
    assert fines_report._last_day_of_month(datetime.date(2026, 12, 31)) == 31


def test_status_text_mentions_last_day():
    text = fines_report.status_text()
    assert "OYLIK JARIMA HISOBOTI" in text
    assert "oxirgi kunida" in text
    assert "/finesreport" in text


def _fake_date(day: int):
    """`date.today` ni masxara qiladi, `date(y, m, d)` real ishlaydi."""
    real_date = datetime.date

    class FakeDate(type(real_date)):
        @classmethod
        def today(cls):
            return real_date(2026, 5, day)

    return FakeDate


def _patch_common(monkeypatch, tmp_path, fines_rows=None):
    sent = []
    monkeypatch.setattr(fines_report, "STATE_FILE", tmp_path / "fr.json")
    monkeypatch.setattr(fines_report, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(fines_report, "build_text",
                        lambda month="": "⚠️ HISOBOT" if month or month == "" else None)
    monkeypatch.setattr(fines_report, "_targets", lambda: [111, 222])
    monkeypatch.setattr(fines_report, "send_message",
                        lambda text, **kw: sent.append(text))
    return sent


def test_check_and_send_noop_mid_month(monkeypatch, tmp_path):
    sent = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(fines_report, "date", _fake_date(20))
    fines_report._LAST_CHECK_AT = 0.0
    fines_report.check_and_send()
    assert not sent


def test_check_and_send_on_last_day(monkeypatch, tmp_path):
    sent = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(fines_report, "date", _fake_date(31))
    fines_report._LAST_CHECK_AT = 0.0
    fines_report.check_and_send()
    assert len(sent) == 2, "oxirgi kunda ikkala admin/dispatcher'ga yuboriladi"

    # Bir oyda bir marta — ikkinchi chaqiruv dublikat yubormaydi.
    sent.clear()
    fines_report._LAST_CHECK_AT = 0.0
    fines_report.check_and_send()
    assert not sent


def test_send_now_returns_text(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path)
    out = fines_report.send_now()
    assert out == "⚠️ HISOBOT"


def test_send_now_empty_when_no_fines(monkeypatch, tmp_path):
    monkeypatch.setattr(fines_report, "STATE_FILE", tmp_path / "fr.json")
    monkeypatch.setattr(fines_report, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(fines_report, "build_text", lambda month="": None)
    out = fines_report.send_now()
    assert "tayyorlanmadi" in out


def test_enabled_follows_fines_report_env(monkeypatch):
    """FINES_REPORT=off — kill-switch: agent butunlay o'chadi."""
    def _tg(**kw):
        s = {"fines_report": "on"}
        s.update(kw)
        return s

    monkeypatch.setattr(fines_report, "telegram_settings",
                        lambda: _tg(fines_report="on"))
    assert fines_report.enabled() is True
    monkeypatch.setattr(fines_report, "telegram_settings",
                        lambda: _tg(fines_report="off"))
    assert fines_report.enabled() is False
    monkeypatch.setattr(fines_report, "telegram_settings",
                        lambda: _tg(fines_report="0"))
    assert fines_report.enabled() is False
