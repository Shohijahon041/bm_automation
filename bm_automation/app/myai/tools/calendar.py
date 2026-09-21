"""Calendar Tool — sana va oyni aniqlash/formatlash agentlar uchun.

Agentlarga "kecha", "13-sentabr", "avgust", "08.2026" kabi so'zlarni
ISO formatga keltirish, oy nomini hisoblash va davr chegaralarini
aniqlash imkonini beradi. Hatto LLM bo'lmasa ham agentlar sanani
to'g'ri aniqlay oladi.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime, timedelta
from typing import Any

from . import BaseTool
from ...utils.logger import get_logger

log = get_logger("myai.tools.calendar")

_MONTHS: list[str] = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]

_MONTH_ALIASES = {
    "sentyabr": 9, "oktyabr": 10,
}

_WEEKDAYS = ("dushanba", "seshanba", "chorshanba",
             "payshanba", "juma", "shanba", "yakshanba")


def _month_number(name: str) -> int | None:
    """Uzbek oy nomini raqamga aylantiradi (1-12)."""
    name = (name or "").strip().lower().strip(".,")
    if name in _MONTH_ALIASES:
        return _MONTH_ALIASES[name]
    for i, m in enumerate(_MONTHS, 1):
        if name == m or name.startswith(m):
            return i
    return None


class CalendarTool(BaseTool):
    """Sana kalendar yordamchisi — aniqlash, formatlash, davrlar."""

    name = "calendar"
    description = (
        "Sana/oy kalkulyatori: resolve_date (kundan ISO), "
        "resolve_month (oy nomi -> YYYY-MM), month_name, month_range, "
        "days_in_month, now/today, days_ago/days_ahead, weekday"
    )

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "resolve_date": self._resolve_date,
            "resolve_month": self._resolve_month,
            "today": self._today,
            "now": self._now,
            "month_range": self._month_range,
            "month_name": self._month_name,
            "days_in_month": self._days_in_month,
            "days_ago": self._days_ago,
            "days_ahead": self._days_ahead,
            "weekday": self._weekday,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum calendar action: {action}")
        return await handler(**kwargs)

    @staticmethod
    def _today(**kwargs) -> dict:
        d = _date.today()
        return {
            "date": d.isoformat(), "year": d.year, "month": d.month,
            "day": d.day, "month_name": _MONTHS[d.month - 1],
            "weekday": "yakshanba" if d.weekday() == 6 else _WEEKDAYS[d.weekday()],
        }

    @staticmethod
    def _now(**kwargs) -> dict:
        dt = datetime.now()
        return {
            "datetime": dt.isoformat(timespec="seconds"),
            "date": dt.date().isoformat(),
            "time": dt.strftime("%H:%M:%S"),
        }

    @staticmethod
    def resolve_date(value: str = "") -> str:
        """Istalgan kunni ISO (YYYY-MM-DD) ga keltiradi.

        "13-sentabr", "13 sentyabr 2026", "13.09.2026", "13.09",
        "kecha", "bugun", "ertaga", "2026-09-13" → "2026-09-13".
        """
        v = str(value or "").strip()
        if not v:
            return _date.today().isoformat()
        low = v.lower()
        if low in ("kecha", "bugun", "ertaga"):
            base = _date.today()
            if low == "kecha":
                return (base - timedelta(days=1)).isoformat()
            if low == "ertaga":
                return (base + timedelta(days=1)).isoformat()
            return base.isoformat()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            try:
                return _date(*map(int, v.split("-"))).isoformat()
            except (TypeError, ValueError):
                return v
        m = re.match(r"^(\d{1,2})[.\/](\d{1,2})[.\/](\d{2,4})$", v)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = 2000 + y if y < 100 else y
            try:
                return _date(y, mo, d).isoformat()
            except (TypeError, ValueError):
                return v
        m = re.match(r"^(\d{1,2})[.\/](\d{1,2})$", v)
        if m:
            try:
                return _date(_date.today().year, int(m.group(2)),
                             int(m.group(1))).isoformat()
            except (TypeError, ValueError):
                return v
        year = _date.today().year
        ym = re.search(r"\b(20\d{2})\b", low)
        if ym:
            year = int(ym.group(1))
        for i, name in enumerate(_MONTHS, 1):
            m = re.search(
                r"\b(\d{1,2})\s*[-–']?\s*" + name + r"(?:dagi|da|gi)?\b",
                low, re.IGNORECASE)
            if m:
                try:
                    return _date(year, i, int(m.group(1))).isoformat()
                except (TypeError, ValueError):
                    continue
        return v

    @staticmethod
    def _resolve_date(value: str = "") -> dict:
        out = CalendarTool.resolve_date(value)
        return {"query": str(value or ""), "date": out}

    @staticmethod
    def resolve_month(value: str = "") -> str:
        """Oyni "YYYY-MM" formatiga keltiradi.

        "avgust", "avgust 2026", "08.2026", "2026-08", "oy" → "2026-08".
        Aniqlanmasa joriy oy.
        """
        v = str(value or "").strip()
        if not v:
            return _date.today().strftime("%Y-%m")
        low = v.lower()
        if low == "oy":
            return _date.today().strftime("%Y-%m")
        m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", low)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        m = re.search(r"\b(0[1-9]|1[0-2])[.\-\\/](20\d{2})\b", low)
        if m:
            return f"{m.group(2)}-{m.group(1)}"
        year = _date.today().year
        ym = re.search(r"\b(20\d{2})\b", low)
        if ym:
            year = int(ym.group(1))
        for alias, num in _MONTH_ALIASES.items():
            if alias in low:
                return f"{year}-{num:02d}"
        for i, name in enumerate(_MONTHS, 1):
            if name in low:
                return f"{year}-{i:02d}"
        return _date.today().strftime("%Y-%m")

    async def _resolve_month(self, value: str = "", **kwargs) -> dict:
        out = CalendarTool.resolve_month(value)
        return {"query": str(value or ""), "month": out,
                "month_name": _MONTHS[int(out[5:7]) - 1]}

    async def _month_range(self, month: str = "", **kwargs) -> dict:
        ym = CalendarTool.resolve_month(month)
        y, m = int(ym[:4]), int(ym[5:7])
        frm = _date(y, m, 1)
        nxt = _date(y + 1, 1, 1) if m == 12 else _date(y, m + 1, 1)
        to = nxt - timedelta(days=1)
        return {"month": ym, "from": frm.isoformat(), "to": to.isoformat(),
                "days": (to - frm).days + 1,
                "month_name": _MONTHS[m - 1]}

    async def _month_name(self, month: str = "", **kwargs) -> dict:
        ym = CalendarTool.resolve_month(month)
        y, m = int(ym[:4]), int(ym[5:7])
        return {"month": ym, "month_name": _MONTHS[m - 1],
                "year": y, "uyzbek": f"{_MONTHS[m - 1]} {y}"}

    async def _days_in_month(self, month: str = "", **kwargs) -> dict:
        rng = await self._month_range(month)
        return {"month": rng["month"], "days": rng["days"]}

    async def _days_ago(self, days: str = "", date: str = "", **kwargs) -> dict:
        try:
            n = int(str(days or "1").strip())
        except (TypeError, ValueError):
            n = 1
        base = _date.fromisoformat(CalendarTool.resolve_date(date)) \
            if str(date or "").strip() else _date.today()
        d = base - timedelta(days=n)
        return {"days": n, "date": d.isoformat(),
                "from": d.isoformat(), "to": base.isoformat()}

    async def _days_ahead(self, days: str = "", date: str = "", **kwargs) -> dict:
        try:
            n = int(str(days or "1").strip())
        except (TypeError, ValueError):
            n = 1
        base = _date.fromisoformat(CalendarTool.resolve_date(date)) \
            if str(date or "").strip() else _date.today()
        d = base + timedelta(days=n)
        return {"days": n, "date": d.isoformat()}

    async def _weekday(self, date: str = "", **kwargs) -> dict:
        d = _date.fromisoformat(CalendarTool.resolve_date(date))
        wd = "yakshanba" if d.weekday() == 6 else _WEEKDAYS[d.weekday()]
        return {"date": d.isoformat(), "weekday": wd,
                "is_weekend": d.weekday() >= 5}