"""Umumiy test konfiguratsiyasi.

Jonli `state/bot_settings.json` (bot orqali o'rnatilgan km narxi, til) test
natijalariga ta'sir qilmasligi uchun bot sozlamalari har bir testda neytral
holatga keltiriladi (km_rate = 0). Sozlamani o'zini tekshiradigan testlar
o'zlari `monkeypatch.setattr(bot_settings, ...)` orqali qiymat beradi.
"""

import pytest

import bm_automation.app.core.bot_settings as bot_settings


@pytest.fixture(autouse=True)
def _isolate_bot_settings(monkeypatch):
    """Jonli bot sozlamalaridan (fayldan o'qiladi) testlarni izolyatsiya qiladi."""
    monkeypatch.setattr(bot_settings, "km_rate", lambda: 0.0)
    yield
