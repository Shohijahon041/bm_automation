"""Brutto-shartnoma 4-modul: sifat mezonlari va jarimalar.

Asosiy ko'rsatkichlar (32-band):
- hajm indeksi (alpha): Lf / Lr * 100;
- sifat indeksi S (beta): (K_amal - K_stjb) / K_amal * 100;
- majburiyat indeksi (gamma): (K_amal - K_maq) / K_amal * 100.

To'lov hisobi ikki usulda qo'llaniladi:
- multiplikativ (loyiha standarti, 32-band):
    S = SKM * Lf * (1 - alpha) * (1 - beta) * (1 - gamma);
- additiv (master prompt 4-modul, S_neto):
    Total_Rate = alpha + beta + gamma
    S_neto = S_KM * L_f * (1 - Total_Rate).

Koefitsiyentlar `dashboard.brutto_calculator` dan qayta foydalaniladi.
"""

from __future__ import annotations

from ..dashboard.brutto_calculator import (
    ALPHA_TABLE,
    BETA_TABLE,
    GAMMA_TABLE,
    MAX_ALPHA,
    MAX_BETA,
    MAX_GAMMA,
    _index_100,
    _lookup,
)


def quality_index(kamal: int, kstjb: int) -> float:
    """Sifat indeksi S = (K_amal - K_stjb) / K_amal * 100."""
    return _index_100(kamal - kstjb, kamal)


def total_rate(alpha: float, beta: float, gamma: float) -> float:
    """Jamga ushlab qolinadigan umumiy ulush (additiv formula)."""
    return float(alpha or 0) + float(beta or 0) + float(gamma or 0)


def net_payment_additive(skm: float, lf: float,
                         alpha: float, beta: float, gamma: float) -> float:
    """S_neto = S_KM * L_f * (1 - Total_Rate) (master prompt 4-modul)."""
    rate = min(total_rate(alpha, beta, gamma), 1.0)
    return max(float(skm or 0) * float(lf or 0) * (1 - rate), 0.0)


def net_payment_multiplicative(skm: float, lf: float,
                               alpha: float, beta: float, gamma: float) -> float:
    """S = SKM * Lf * (1 - alpha) * (1 - beta) * (1 - gamma) (32-band)."""
    a = max(min(float(alpha or 0), 1.0), 0.0)
    b = max(min(float(beta or 0), 1.0), 0.0)
    g = max(min(float(gamma or 0), 1.0), 0.0)
    return max(float(skm or 0) * float(lf or 0)
               * (1 - a) * (1 - b) * (1 - g), 0.0)


def net_payment(skm: float, lf: float,
                alpha: float, beta: float, gamma: float,
                mode: str = "multiplicative") -> float:
    """To'lov — mode: 'multiplicative' (standart) yoki 'additive'."""
    if mode == "additive":
        return net_payment_additive(skm, lf, alpha, beta, gamma)
    return net_payment_multiplicative(skm, lf, alpha, beta, gamma)


# -- Koefitsiyent hisoblash (dashboard yadrosi orqali) ------------------

def calc_alpha(lf: float, lr: float) -> float:
    """Hajm ulushi alpha jadvali (Lf / Lr * 100)."""
    if not lr:
        return MAX_ALPHA
    return _lookup(_index_100(lf, lr), ALPHA_TABLE)


def calc_beta(kamal: int, kstjb: int) -> float:
    """Sifat ulushi beta jadvali (K_amal - K_stjb)/K_amal * 100."""
    if not kamal:
        return MAX_BETA
    return _lookup(_index_100(kamal - kstjb, kamal), BETA_TABLE)


def calc_gamma(kamal: int, kmaq: int) -> float:
    """Majburiyat ulushi gamma jadvali (K_amal - K_maq)/K_amal * 100."""
    if not kamal:
        return MAX_GAMMA
    return _lookup(_index_100(kamal - kmaq, kamal), GAMMA_TABLE)


def coefficients(lr: float, lf: float,
                 kamal: int, kstjb: int, kmaq: int) -> dict:
    """Alpha/beta/gamma + sifat indeksini bitta lug'atda qaytaradi."""
    if not lr or not kamal:
        return {
            "alpha": MAX_ALPHA, "beta": MAX_BETA, "gamma": MAX_GAMMA,
            "quality_index": 0.0,
        }
    return {
        "alpha": calc_alpha(lf, lr),
        "beta": calc_beta(kamal, kstjb),
        "gamma": calc_gamma(kamal, kmaq),
        "quality_index": quality_index(kamal, kstjb),
    }