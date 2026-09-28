"""Brutto-shartnoma 5-modul: moliyaviy jamg'arma va subsidiyalash.

116-son qaror va master prompt asosida:
- yo'lkira tushumlari maxsus jamg'armaga o'tkaziladi — 1 ish kunida;
- har oyning 5-sanasigacha yoqilg'i xarajatlari uchun 35% avans beriladi;
- jamg'arma tushumi va shartnoma bo'yicha to'lov orasidagi manfiy farq
  mahalliy budjetdan subsidiyalash hisobiga qoplanadi.
"""

from __future__ import annotations

from datetime import date, timedelta

FUND_TRANSFER_DAYS = 1          # jamg'armaga o'tkazma muddati (ish kuni)
ADVANCE_RATE = 0.35             # yoqilg'i avansi ulushi (35%)
ADVANCE_DAY = 5                 # avans to'lanadigan oy kuni


def advance_amount(contract_payment: float) -> float:
    """Yoqilg'i avansi = shartnoma to'lovining 35%."""
    return round(float(contract_payment or 0) * ADVANCE_RATE, 2)


def advance_due_date(report_month: date) -> date:
    """Avans to'lanadigan sana: hisobot oyining 5-sanasi."""
    return date(report_month.year, report_month.month, ADVANCE_DAY)


def fund_transfer_due(day: date) -> date:
    """Yo'lkira tushumini jamg'armaga o'tkazishning oxirgi muddati.

    Real ish kunlarini hisoblash uchun soddalashtirilgan — qo'shimcha
    `transfer_due_business_day` dan foydalanish mumkin.
    """
    return day + timedelta(days=FUND_TRANSFER_DAYS)


def subsidy_amount(fund_collected: float, contract_payment: float) -> float:
    """Mahalliy budjetdan subsidiyalanishi lozim bo'lgan summa.

    Jamg'arma tushumi shartnoma to'lovidan kam bo'lsa, farq subsidiyalanadi.
    """
    diff = float(contract_payment or 0) - float(fund_collected or 0)
    return round(max(diff, 0.0), 2)


def fund_balance(fund_collected: float, contract_payment: float) -> float:
    """Jamg'arma natijasi: musbat — qoplanadi, manfiy — subsidiya kerak."""
    return round(float(fund_collected or 0) - float(contract_payment or 0), 2)


def next_workday(day: date) -> date:
    """Keyingi ish kuni (shanba/yakshanbani o'tkazib yuradi)."""
    d = day + timedelta(days=1)
    while d.weekday() >= 5:  # 5=shanba, 6=yakshanba
        d += timedelta(days=1)
    return d


def fund_transfer_business_day(day: date) -> date:
    """Jamg'armaga 1 ish kunida o'tkazish — keyingi ish kunini hisoblash."""
    return next_workday(day)