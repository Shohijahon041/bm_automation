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
    monkeypatch.setattr(commands, "_keepalive_companies", lambda: [])
    assert commands.cmd_keepalive(None) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "refresh yangilandi" in out


def test_cmd_keepalive_company_error_notifies(monkeypatch, capsys):
    class FakeClient:
        def keepalive(self):
            return "refreshed"

    sent = []
    monkeypatch.setattr(commands, "BMClient", lambda: FakeClient())
    monkeypatch.setattr(commands, "_keepalive_companies",
                        lambda: ["AND ECO: refresh 401"])
    monkeypatch.setattr(commands, "_notify_keepalive_error",
                        lambda exc: sent.append(str(exc)))

    assert commands.cmd_keepalive(None) == 1
    assert sent and "AND ECO" in sent[0]
    assert "XATO" not in capsys.readouterr().err


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


def test_keepalive_companies_deduplicates_names(monkeypatch, capsys):
    from bm_automation.app.core import companies as companies_mod
    from bm_automation.app.core import profiles as profiles_mod

    called = []
    monkeypatch.setattr(profiles_mod, "profiles_with_credentials", lambda: [
        {"name": "AND ECO", "username": "u", "password": "p"},
        {"name": "and eco", "username": "u", "password": "p"},
        {"name": "ASL", "username": "u", "password": "p"},
    ])

    class FakeClient:
        def __init__(self, profile):
            self.profile = profile

        def keepalive(self):
            called.append(self.profile["name"])
            return "refreshed"

    monkeypatch.setattr(companies_mod, "own_client", lambda p: FakeClient(p))
    assert commands._keepalive_companies() == []
    assert called == ["AND ECO", "ASL"]
    assert "AND ECO" in capsys.readouterr().out


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
