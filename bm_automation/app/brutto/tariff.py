"""Brutto-shartnoma 2-modul: 1 km narx kalkulyatsiyasi (S_KM).

116-son qaror 13-14-ilova va 4a-ilova asosida:
- haydovchilar soni me'yori = kunlik ish vaqti / 8 soat, fraktsiya
  yaxlitlash: <0.25 -> 0.0; 0.25..0.74 -> 0.5; >0.74 -> 1.0;
- haydovchi oylik ish haqi = statistika oylik ish haqi * 1.2 koeffitsiyent;
- S_KM = tannarx bloklari + davr xarajatlari + kredit foizi
        + 10% sof foyda + 12% QQS.

Tannarx bloklari (1-km): yoqilg'i/elektr, shina/akumulyator, ta'mirlash,
haydovchi ish haqi, 12% ijtimoiy soliq, 20% amortizatsiya, sug'urta,
park (garaj) ijarasi.
"""

from __future__ import annotations

from typing import Mapping

STAFF_HOURS = 8.0          # kunlik ish vaqti me'yori (soat)
WAGE_COEFF = 1.2           # statistika ish haqiga koeffitsiyent
SOCIAL_TAX = 0.12          # ijtimoiy soliq (ish haqidan 12%)
AMORT_RATE = 0.20          # amortizatsiya (20%)
NET_PROFIT = 0.10          # sof foyda (10%)
VAT_RATE = 0.12            # QQS (12%)
COST_BLOCK_KEYS: tuple[str, ...] = (
    "fuel_per_km",      # yoqilg'i / elektr
    "tires_per_km",     # shina / akumulyator
    "repair_per_km",    # ta'mirlash
    "labor_per_km",     # haydovchi ish haqi
    "social_tax_per_km",  # ijtimoiy soliq (odatda labor_per_km * 12%)
    "amort_per_km",     # amortizatsiya (20%)
    "insurance_per_km", # sug'urta
    "park_rent_per_km", # park (garaj) ijarasi
)
# Masshtablash mumkin bo'lgan bloklar: labor uchun ijtimoiy soliq avto-hisob
SOCIAL_BASE_KEYS: tuple[str, ...] = ("labor_per_km",)


def driver_staff_norm(hours_per_day: float) -> float:
    """Haydovchi shtat me'yori (4a-ilova): ish vaqti / 8 soat.

    Natija fraktsion smenada: <0.25 -> 0.0; 0.25..0.74 -> 0.5; >0.74 -> 1.0.
    """
    h = float(hours_per_day or 0)
    norm = h / STAFF_HOURS
    frac = norm - int(norm)
    if frac < 0.25:
        return float(int(norm))
    if frac < 0.75:
        return float(int(norm) + 0.5)
    return float(int(norm) + 1.0)


def driver_wage(stats_monthly_wage: float) -> float:
    """Haydovchi oylik ish haqi = statistika oylik * 1.2."""
    return float(stats_monthly_wage or 0) * WAGE_COEFF


def cost_blocks(blocks: Mapping[str, float] | None = None) -> dict[str, float]:
    """1-km tannarx bloklari, ijtimoiy soliq bilan to'ldirilgan holatda.

    `social_tax_per_km` berilmasa labor_per_km * SOCIAL_TAX hisoblanadi.
    """
    src = dict(blocks or {})
    out: dict[str, float] = {}
    for key in COST_BLOCK_KEYS:
        out[key] = float(src.get(key) or 0)
    if "social_tax_per_km" not in src:
        out["social_tax_per_km"] = out["labor_per_km"] * SOCIAL_TAX
    return out


def skm_total(blocks: Mapping[str, float] | None = None,
              driver_count: float = 1.0,
              annual_km: float = 1.0) -> float:
    """1 km to'liq narxi (S_KM) — master prompt 2-modul.

    Formula:
      tannarx_1km = sum(cost_blocks)
      davr_xarajat = tannarx_1km * (sof foyda 10% uchun shtat tuzilmasi)
      S_KM = (tannarx_1km + kredit_foiz) * (1 + NET_PROFIT) * (1 + VAT_RATE)

    Bu yerda kredit foizi ma'lumotlarni oddiy qilish uchun shtat me'yori
    (driver_count) va yillik masofa (annual_km) orqali kengaytiriladi.
    """
    blocks = cost_blocks(blocks)
    cost = sum(blocks.values())
    staff = max(float(driver_count or 1), 0.5)
    km = max(float(annual_km or 1), 1.0)
    # Shtatning oylik ish haqi masshtabi: me'yor son yillik masofaga bo'linadi
    period_cost = (cost * staff) / km
    base = cost + period_cost
    with_profit = base * (1 + NET_PROFIT)
    return round(with_profit * (1 + VAT_RATE), 6)


def skm_full(cost: float, period_cost: float = 0.0,
             credit_interest: float = 0.0) -> float:
    """S_KM = (tannarx + davr + kredit) -> 10% foyda -> 12% QQS."""
    base = float(cost or 0) + float(period_cost or 0) + float(credit_interest or 0)
    with_profit = base * (1 + NET_PROFIT)
    return round(with_profit * (1 + VAT_RATE), 6)