"""Brutto-shartnoma master tizimi (116-son qaror + master prompt).

5 modul:
  1. contract  — huquqiy/shartnomaviy biriktirish;
  2. tariff    — 1 km narx kalkulyatsiyasi (S_KM);
  3. monitoring — operatsion dispetcherlik va monitoring;
  4. payment   — sifat mezonlari va jarimalar (alpha/beta/gamma);
  5. fund      — moliyaviy jamg'arma va subsidiyalash.

Asosiy kirish nuqta: `master_report` (master.py).
"""

from . import contract, fund, master, monitoring, payment, tariff  # noqa: F401
from .master import master_report  # noqa: F401

__all__ = [
    "contract",
    "tariff",
    "monitoring",
    "payment",
    "fund",
    "master",
    "master_report",
]