"""brutto_calculator moduli uchun unit testlar (pytest).

O'zbekiston Respublikasi Vazirlar Mahkamasining 2023-yil 18-martdagi
116-son qarori (1-ilova, 32-33-bandlar) talablarini tekshiradi.

Ishga tushirish:
    python -m pytest test_brutto_calculator.py -v
"""

from __future__ import annotations

from datetime import date

import pytest
from pandas import DataFrame

from brutto_calculator import (
    BruttoCalculator,
    Contract,
    MAX_ALPHA,
    MAX_BETA,
    MAX_GAMMA,
    load_excel,
    payment_due_date,
)

SKM = 16176.0


@pytest.fixture
def calculator() -> BruttoCalculator:
    """Standart shartnoma asosida calculator yaratadi."""
    contract = Contract(
        skm=SKM,
        route_number="B-80",
        carrier="AvtoPark №1",
        valid_from=date(2024, 1, 1),
        valid_to=date(2024, 12, 31),
    )
    return BruttoCalculator(contract)


class TestAlpha:
    """calc_alpha — hajm (lk) mezoni bo'yicha ulush."""

    def test_zero_index(self, calculator: BruttoCalculator) -> None:
        assert calculator.calc_alpha(0, 100) == 0.20

    def test_low_bound(self, calculator: BruttoCalculator) -> None:
        # 69.9% gacha alpha = 20%
        assert calculator.calc_alpha(69.9, 100) == 0.20
        assert calculator.calc_alpha(0, 0) == MAX_ALPHA

    def test_70_percent(self, calculator: BruttoCalculator) -> None:
        # 70% dan alpha = 15%
        assert calculator.calc_alpha(70, 100) == 0.15

    def test_high_bound(self, calculator: BruttoCalculator) -> None:
        # 95-100% alpha = 0%
        assert calculator.calc_alpha(95, 100) == 0.0
        assert calculator.calc_alpha(100, 100) == 0.0

    def test_over_100(self, calculator: BruttoCalculator) -> None:
        assert calculator.calc_alpha(120, 100) == 0.0


class TestBeta:
    """calc_beta — sifat mezoni bo'yicha ulush."""

    def test_zero_quality(self, calculator: BruttoCalculator) -> None:
        # sifat indeksi 0% -> beta = 15%
        assert calculator.calc_beta(100, 100) == 0.15

    def test_full_quality(self, calculator: BruttoCalculator) -> None:
        # sifat indeksi 95% -> beta = 0%
        assert calculator.calc_beta(100, 5) == 0.0

    def test_no_trips(self, calculator: BruttoCalculator) -> None:
        # kamal = 0 -> eng yuqori beta
        assert calculator.calc_beta(0, 0) == MAX_BETA


class TestGamma:
    """calc_gamma — majburiyatlarga amal qilish mezoni."""

    def test_zero_obligation(self, calculator: BruttoCalculator) -> None:
        # majburiyat indeksi 50% -> gamma = 5%
        assert calculator.calc_gamma(100, 50) == 0.05

    def test_full_obligation(self, calculator: BruttoCalculator) -> None:
        # majburiyat indeksi 95% -> gamma = 0%
        assert calculator.calc_gamma(100, 5) == 0.0

    def test_no_trips(self, calculator: BruttoCalculator) -> None:
        assert calculator.calc_gamma(0, 0) == MAX_GAMMA


class TestPayment:
    """calc_payment — asosiy formulani tekshirish."""

    def test_full_payment(self, calculator: BruttoCalculator) -> None:
        # Reja 100%, sifat 100%, majburiyat 100% -> barcha koeffitsiyentlar 0
        # S = 16176 * 100 * (1-0) * (1-0) * (1-0) = 1 617 600
        assert calculator.calc_payment(100, 100, 100, 0, 0) == pytest.approx(1617600)

    def test_partial_payment(self, calculator: BruttoCalculator) -> None:
        # Masofa indeksi 50% (lf=50, lr=100), sifat 80%, majburiyat 90%
        # alpha=0.20 (0-69.9%), beta=0.09 (80-84.9%), gamma=0.01 (90-94.9%)
        alpha = calculator.calc_alpha(50, 100)
        beta = calculator.calc_beta(100, 20)
        gamma = calculator.calc_gamma(100, 10)
        expected = SKM * 50 * (1 - alpha) * (1 - beta) * (1 - gamma)
        assert calculator.calc_payment(50, 100, 100, 20, 10) == pytest.approx(expected)

    def test_zero_division(self, calculator: BruttoCalculator) -> None:
        # lr=0, kamal=0 -> xato bermaslik, jazo koeffitsiyentlari qo'llanishi
        result = calculator.calc_payment(0, 0, 0, 0, 0)
        assert result == pytest.approx(0.0)
        # kamal=0 bo'lganda ham ishlashi kerak; lf=100 bo'lsa jazo
        # koeffitsiyentlari bilan to'lov: 16176*100*0.8*0.85*0.95
        result2 = calculator.calc_payment(100, 0, 0, 0, 0)
        assert result2 == pytest.approx(SKM * 100 * 0.8 * 0.85 * 0.95)


class TestProcessReport:
    """process_report — DataFrame qayta ishlash."""

    def test_report_result(self, calculator: BruttoCalculator) -> None:
        df = DataFrame([
            {"sana": "2024-06-10", "grafik": "1", "davlat_raqami": "01A123BC",
             "fio": "Karimov A.", "lr": 100, "lf": 100, "kamal": 100,
             "kstjb": 0, "kmaq": 0},
            {"sana": "2024-06-11", "grafik": "2", "davlat_raqami": "01A124BC",
             "fio": "Aliyev B.", "lr": 100, "lf": 50, "kamal": 100,
             "kstjb": 20, "kmaq": 10},
        ])
        results = calculator.process_report(df)
        assert len(results) == 2
        first = results[0]
        assert first.tolov == pytest.approx(SKM * 100)  # barcha kattaliklar 0
        assert first.alpha == 0.0
        second = results[1]
        assert second.alpha == 0.20  # lf/lr = 50% (0-69.9 -> 20%)
        assert second.beta == 0.09   # sifat 80%
        assert second.gamma == 0.01  # majburiyat 90%

    def test_empty_report(self, calculator: BruttoCalculator) -> None:
        assert calculator.process_report(DataFrame()) == []


class TestAggregate:
    """aggregate — JAMI qatorini hisoblash."""

    def test_aggregate_sums(self, calculator: BruttoCalculator) -> None:
        df = DataFrame([
            {"sana": "2024-06-10", "grafik": "1", "davlat_raqami": "a",
             "fio": "X", "lr": 100, "lf": 100, "kamal": 100, "kstjb": 0, "kmaq": 0},
            {"sana": "2024-06-11", "grafik": "2", "davlat_raqami": "b",
             "fio": "Y", "lr": 100, "lf": 100, "kamal": 100, "kstjb": 0, "kmaq": 0},
        ])
        results = calculator.process_report(df)
        agg = calculator.aggregate(results)
        assert agg["lr"] == pytest.approx(200)
        assert agg["lf"] == pytest.approx(200)
        assert agg["kamal"] == 200
        assert agg["alpha"] == pytest.approx(0.0)
        # Umumiy to'lov = SKM * Jami_Lf * (1-0)*(1-0)*(1-0)
        assert agg["tolov"] == pytest.approx(SKM * 200)

    def test_aggregate_empty(self, calculator: BruttoCalculator) -> None:
        agg = calculator.aggregate([])
        assert agg["qatorlar"] == 0
        assert agg["tolov"] == 0.0


class TestExcel:
    """Excel import/eksport."""

    def test_load_and_export(self, calculator: BruttoCalculator, tmp_path) -> None:
        # Namuna manba fayl yaratish
        sample = str(tmp_path / "source.xlsx")
        df = DataFrame([
            {"Sana": "2024-06-10", "Grafik": "1", "Davlat raqami": "01A123BC",
             "F.I.O": "Karimov A.", "Lr (km)": 100, "Lf (km)": 100,
             "Kamal": 100, "Kstjb": 0, "Kmaq": 0},
        ])
        df.to_excel(sample, index=False)

        loaded = load_excel(sample)
        assert list(loaded.columns) == [
            "sana", "grafik", "davlat_raqami", "fio",
            "lr", "lf", "kamal", "kstjb", "kmaq",
        ]
        results = calculator.process_report(loaded)
        out = str(tmp_path / "out.xlsx")
        calculator.export_excel(results, out)
        # Fayl yaratilganligini tekshiramiz
        from os.path import getsize
        assert getsize(out) > 0


class TestPaymentDate:
    """33-band — to'lov muddati."""

    def test_due_date(self) -> None:
        assert payment_due_date(date(2024, 6, 10)) == date(2024, 7, 15)
        assert payment_due_date(date(2024, 12, 10)) == date(2025, 1, 15)
        assert payment_due_date(date(2025, 11, 30)) == date(2025, 12, 15)