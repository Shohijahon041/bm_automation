"""Sana/vaqt bilan ishlash yordamchilari."""

from __future__ import annotations

from datetime import date, timedelta

__all__ = ["today_iso", "last_working_day", "month_range"]


def today_iso() -> str:
    """Bugungi sanani YYYY-MM-DD ko'rinishida qaytaradi."""
    return date.today().isoformat()


def last_working_day(d: date) -> date:
    """Agar d dam olish kuniga to'g'ri kelsa, oldingi ish kuni qaytaradi."""
    while d.weekday() >= 5:  # 5=Shanba, 6=Yakshanba
        d -= timedelta(days=1)
    return d


def month_range(d: date, month_offset: int = 0) -> date:
    """`month_offset` oy orqaga ketgan oyning birinchi kuni.

    month_offset=0 -> joriy oyning 1-kuni; 1 -> o'tgan oyning 1-kuni.
    """
    m = d.month - month_offset
    y = d.year
    if m < 1:
        m += 12
        y -= 1
    return date(y, m, 1)
