"""Umumiy test konfiguratsiyasi.

Jonli `state/bot_settings.json` (bot orqali o'rnatilgan km narxi, til) test
natijalariga ta'sir qilmasligi uchun bot sozlamalari har bir testda neytral
holatga keltiriladi (km_rate = 0). Sozlamani o'zini tekshiradigan testlar
o'zlari `monkeypatch.setattr(bot_settings, ...)` orqali qiymat beradi.
"""

import os

import pytest

import bm_automation.app.core.bot_settings as bot_settings


@pytest.fixture(autouse=True)
def _isolate_bot_settings(monkeypatch, tmp_path):
    """Jonli bot sozlamalaridan (fayldan o'qiladi) testlarni izolyatsiya qiladi."""
    monkeypatch.setattr(bot_settings, "STATE_FILE", tmp_path / "bot_settings_test.json")
    bot_settings._reset()
    # Dashboard API testlari autentifikatsiyasiz ishlaydi (haqiqiy kirish
    # testlari BM_TEST_MODE'ni o'chirib oladi).
    monkeypatch.setenv("BM_TEST_MODE", "1")
    yield
    bot_settings._reset()
