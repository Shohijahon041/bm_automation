"""BRUTTO-SHARTNEMA MASTER tizimi — 5 modul yig'indisi.

Yuqori darajali `master_report` — barcha modullarni (1-5) bitta
lug'atga jamlab, dashboard/API uchun tayyor natija qaytaradi.

Modullar:
  1. contract  — huquqiy/shartnomaviy biriktirish;
  2. tariff    — 1 km narx kalkulyatsiyasi (S_KM);
  3. monitoring — operatsion dispetcherlik va monitoring;
  4. payment   — sifat mezonlari va jarimalar (alpha/beta/gamma);
  5. fund      — moliyaviy jamg'arma va subsidiyalash.
"""

from __future__ import annotations

from datetime import date

from .contract import (
    contract_months,
    equipment_complete,
    indexed_price,
    mandatory_equipment,
    needs_review,
)
from .fund import (
    advance_amount,
    advance_due_date,
    fund_balance,
    fund_transfer_business_day,
    subsidy_amount,
)
from .monitoring import effective_kstjb, effective_km, is_excused
from .payment import coefficients, net_payment
from .tariff import driver_staff_norm, driver_wage, skm_total


def master_report(
    bus_age: float = 0.0,
    equipment: list[str] | None = None,
    skm: float = 16176.0,
    cpi_percent: float = 12.0,
    fuel_change: float = 0.0,
    electricity_change: float = 0.0,
    tax_change: float = 0.0,
    hours_per_day: float = 8.0,
    stats_monthly_wage: float = 0.0,
    blocks: dict | None = None,
    driver_count: float = 1.0,
    annual_km: float = 85400.0,
    lr: float = 0.0,
    lf: float = 0.0,
    kamal: int = 0,
    kstjb: int = 0,
    kmaq: int = 0,
    excused_count: int = 0,
    excuse_reason: str = "",
    fund_collected: float = 0.0,
    report_month: date | None = None,
    mode: str = "multiplicative",
) -> dict:
    """Master tizim bo'yicha to'liq hisobot (5 modul).

    `mode`: 'multiplicative' (standard, 32-band) yoki 'additive'
            (master prompt S_neto = S_KM * L_f * (1 - Total_Rate)).

    Qaytaradi: har bir modul natijasi + jami `payment` kaliti.
    """
    month = report_month or date.today()
    eff_kstjb = effective_kstjb(kstjb, excused_count)
    eff_lf = effective_km(lr, lf, excuse_reason)
    coef = coefficients(lr, eff_lf, kamal, eff_kstjb, kmaq)
    payment = net_payment(skm, eff_lf,
                          coef["alpha"], coef["beta"], coef["gamma"], mode)
    contr_months = contract_months(bus_age)

    return {
        "module1_contract": {
            "bus_age": float(bus_age or 0),
            "contract_months": contr_months,
            "equipment_required": list(mandatory_equipment()),
            "equipment_complete": equipment_complete(equipment or []),
            "indexed_skm": indexed_price(skm, cpi_percent),
            "needs_price_review": needs_review(
                fuel_change, electricity_change, tax_change),
        },
        "module2_tariff": {
            "staff_norm": driver_staff_norm(hours_per_day),
            "driver_wage": driver_wage(stats_monthly_wage),
            "skm_calculated": skm_total(blocks, driver_count, annual_km),
        },
        "module3_monitoring": {
            "excuse_reason": excuse_reason,
            "is_excused": is_excused(excuse_reason),
            "effective_kstjb": eff_kstjb,
            "effective_lf": eff_lf,
            "raw_lf": float(lf or 0),
            "raw_kstjb": int(kstjb or 0),
        },
        "module4_payment": {
            "mode": mode,
            **coef,
            "total_rate": coef["alpha"] + coef["beta"] + coef["gamma"],
            "payment": payment,
        },
        "module5_fund": {
            "advance": advance_amount(payment),
            "advance_due": advance_due_date(month),
            "fund_balance": fund_balance(fund_collected, payment),
            "subsidy": subsidy_amount(fund_collected, payment),
            "transfer_business_day": fund_transfer_business_day(month),
        },
        "summary": {
            "skm": float(skm or 0),
            "annual_km": float(annual_km or 0),
            "report_month": month.isoformat(),
            "total_payment": payment,
        },
    }