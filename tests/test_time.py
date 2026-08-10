"""Sana va vaqt yordamchilari testlari."""

from datetime import date

from bm_automation.app.utils.time import last_working_day, month_range, today_iso


def test_today_iso():
    assert today_iso() == date.today().isoformat()


def test_last_working_day_weekday_unchanged():
    assert last_working_day(date(2026, 8, 10)) == date(2026, 8, 10)  # Dushanba


def test_last_working_day_saturday_back():
    assert last_working_day(date(2026, 8, 8)) == date(2026, 8, 7)  # Shanba -> Juma


def test_last_working_day_sunday_back():
    assert last_working_day(date(2026, 8, 9)) == date(2026, 8, 7)  # Yakshanba -> Juma


def test_month_range():
    assert month_range(date(2026, 2, 15)) == date(2026, 2, 1)
    assert month_range(date(2026, 2, 15), month_offset=1) == date(2026, 1, 1)
    # yil bo'yicha o'ram (yanvar - 1 = dekabr o'tgan yil)
    assert month_range(date(2026, 1, 10), month_offset=1) == date(2025, 12, 1)
