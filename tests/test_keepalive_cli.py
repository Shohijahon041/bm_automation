"""cmd_keepalive (token keep-alive CLI) testlari.

Tarmoqqa chiqilmaydi: BMClient va Telegram ogohlantirish monkeypatch qilinadi.
"""

from __future__ import annotations

from bm_automation.cli import commands


def test_cmd_keepalive_ok(monkeypatch, capsys):
    class FakeClient:
        def keepalive(self):
            return "refreshed"

    monkeypatch.setattr(commands, "BMClient", lambda: FakeClient())
    assert commands.cmd_keepalive(None) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "refresh yangilandi" in out


def test_cmd_keepalive_error_notifies(monkeypatch, capsys):
    sent = []

    class FakeClient:
        def keepalive(self):
            raise RuntimeError("refresh 401")

    monkeypatch.setattr(commands, "BMClient", lambda: FakeClient())
    monkeypatch.setattr(
        commands, "_notify_keepalive_error", lambda exc: sent.append(str(exc))
    )
    assert commands.cmd_keepalive(None) == 1
    assert sent and "refresh 401" in sent[0]
    assert "XATO" in capsys.readouterr().err


def test_notify_keepalive_error_never_raises(monkeypatch):
    """Ogohlantirish yuborilmasa ham funksiya xato ko'tarmaydi."""
    calls = []

    def boom(text, chat_id=None):
        calls.append(chat_id)
        raise RuntimeError("telegram yo'q")

    import bm_automation.app.notifications.telegram as tg

    monkeypatch.setenv("TG_ADMIN_IDS", "123456")
    monkeypatch.setattr(tg, "send_message", boom)
    commands._notify_keepalive_error(RuntimeError("test"))  # xato ko'tarmasligi kerak
    assert calls == ["123456"]
