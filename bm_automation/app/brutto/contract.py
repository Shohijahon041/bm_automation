"""Brutto-shartnoma 1-modul: huquqiy va shartnomaviy biriktirish.

116-son qaror va tender shartlari asosida:
- shartnoma muddati avtobusning o'rtacha yoshiga qarab 7 yilgacha;
- GPS-tracker, ATTO validatorlar, dispetcherlik monitoringi va
  videokuzatuv tashuvchining majburiyatlari;
- to'lov 12 oyda bir marta CPI (iste'mol narxlari indeksi) bo'yicha
  indekslanadi;
- yoqilg'i, elektr energiyasi yoki soliqlar 10% dan ortiq o'zgarsa,
  shartnoma narxi qayta ko'rib chiqiladi.
"""

from __future__ import annotations

from datetime import date

# Avtobus o'rtacha yoshi (yil) -> shartnoma muddati (oy)
# Har bir qator: yosh (chegara) kam bo'lsa shu oy muddat qaytariladi.
BUS_AGE_MONTHS: tuple[tuple[float, int], ...] = (
    (1.0, 84),    #   <1 yosh  -> 7 yil
    (3.0, 72),    # 1-3 yosh   -> 6 yil
    (5.0, 60),    # 3-5 yosh   -> 5 yil
    (7.0, 48),    # 5-7 yosh   -> 4 yil
    (9.0, 36),    # 7-9 yosh   -> 3 yil
    (999.0, 12),  # 9+ yosh    -> 1 yil (minimal)
)

DEFAULT_CPI_PERCENT = 12.0   # yillik CPI (foiz)
INDEX_INTERVAL_MONTHS = 12   # indeksatsiya davri (oy)
REVIEW_THRESHOLD = 0.10      # narxni qayta ko'rib chiqish chegarasi (10%)

MANDATORY_EQUIPMENT: tuple[str, ...] = (
    "GPS-tracker",
    "ATTO validatorlar",
    "Dispetcherlik monitoringi",
    "Videokuzatuv",
)


def contract_months(bus_age: float) -> int:
    """Avtobus o'rtacha yoshiga qarab shartnoma muddati (oy).

    Yosh <= 1 -> 84 oy (7 yil); yosh oshgan sayin muddat qisqaradi;
    10+ yosh -> 12 oy (1 yil, minimal).
    """
    for threshold, months in BUS_AGE_MONTHS:
        if float(bus_age or 0) < threshold:
            return months
    return BUS_AGE_MONTHS[-1][1]


def mandatory_equipment() -> tuple[str, ...]:
    """Tashuvchida bo'lishi shart bo'lgan uskuna/talablar ro'yxati."""
    return MANDATORY_EQUIPMENT


def equipment_complete(equipment: list[str] | tuple[str, ...]) -> bool:
    """Berilgan uskunalar barcha majburiy talablarni qamrab olganmi?"""
    have = {str(e).strip().lower() for e in (equipment or [])}
    return all(any(req.lower() in h for h in have) for req in MANDATORY_EQUIPMENT)


def indexed_price(skm: float, cpi_percent: float = DEFAULT_CPI_PERCENT) -> float:
    """12 oylik CPI bo'yicha indekslangan SKM narxi.

    Index formula: skm * (1 + cpi/100).
    """
    cpi = float(cpi_percent or 0)
    return round(float(skm or 0) * (1 + cpi / 100.0), 2)


def needs_review(fuel_change: float, electricity_change: float,
                 tax_change: float, threshold: float = REVIEW_THRESHOLD) -> bool:
    """Yoqilg'i/elektr/soliq o'zgarishi chegaradan oshsa — narxni qayta ko'rib chiqish.

    Har qanday omil abs(qiymat) >= threshold bo'lsa True.
    """
    for v in (fuel_change, electricity_change, tax_change):
        if abs(float(v or 0)) >= float(threshold or 0):
            return True
    return False


def price_effective_from(contract_start: date) -> date:
    """Narx kuchga kiradigan sana: shartnoma boshlangan oyning 1-sanasi."""
    return date(contract_start.year, contract_start.month, 1)