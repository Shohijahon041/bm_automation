"""BRUTTO-SHARTNEMA MASTER tizim (app/brutto) testlari.

5 modul funksiyalari va `master_report` agregatsiyasini tekshiradi.
"""

from datetime import date

import pytest

from bm_automation.app.brutto import master_report
from bm_automation.app.brutto import contract
from bm_automation.app.brutto import fund
from bm_automation.app.brutto import monitoring
from bm_automation.app.brutto import payment
from bm_automation.app.brutto import tariff


class TestModule1Contract:
    def test_contract_months_by_age(self):
        assert contract.contract_months(0) == 84
        assert contract.contract_months(1.5) == 72
        assert contract.contract_months(3) == 60
        assert contract.contract_months(5) == 48
        assert contract.contract_months(7) == 36
        assert contract.contract_months(20) == 12

    def test_mandatory_equipment(self):
        eq = contract.mandatory_equipment()
        assert len(eq) == 4
        assert contract.equipment_complete(list(eq))
        assert contract.equipment_complete(["GPS-tracker", "ATTO"]) is False

    def test_indexed_price(self):
        assert contract.indexed_price(16176, 12.0) == pytest.approx(18117.12)
        assert contract.indexed_price(0) == 0.0

    def test_needs_review(self):
        assert contract.needs_review(0.12, 0, 0)
        assert contract.needs_review(0, -0.05, 0) is False
        assert contract.needs_review(0.10, 0, 0)  # threshold ga teng -> ha


class TestModule2Tariff:
    def test_staff_norm_rounding(self):
        assert tariff.driver_staff_norm(8) == 1.0
        assert tariff.driver_staff_norm(16) == 2.0
        assert tariff.driver_staff_norm(1) == 0.0     # 1/8=0.125 <0.25
        assert tariff.driver_staff_norm(4) == 0.5     # 4/8=0.5
        assert tariff.driver_staff_norm(7) == 1.0     # 7/8=0.875 >0.74
        assert tariff.driver_staff_norm(12) == 1.5    # 12/8=1.5 -> .5

    def test_driver_wage(self):
        assert tariff.driver_wage(1000) == 1200.0
        assert tariff.driver_wage(0) == 0.0

    def test_cost_blocks_with_social_tax(self):
        blk = tariff.cost_blocks({"labor_per_km": 100.0})
        assert blk["social_tax_per_km"] == pytest.approx(12.0)

    def test_skm_full(self):
        # tannarx 100 + davr 20 + kredit 30 = 150
        # foyda -> 150*1.10=165 ; QQS -> 165*1.12=184.8
        assert tariff.skm_full(100, 20, 30) == pytest.approx(184.8)


class TestModule3Monitoring:
    def test_is_excused(self):
        assert monitoring.is_excused("tirbandlik")
        assert monitoring.is_excused("YTH sodir bo'ldi")
        assert monitoring.is_excused("GPS texnik nosozlik")
        assert monitoring.is_excused("shaxsiy sabab") is False
        assert monitoring.is_excused("") is False

    def test_effective_kstjb(self):
        assert monitoring.effective_kstjb(5, 2) == 3
        assert monitoring.effective_kstjb(5, 10) == 0

    def test_effective_km(self):
        # Tirbandlikda reja masofa hisobga olinadi
        assert monitoring.effective_km(100, 60, "tirbandlik") == 100
        assert monitoring.effective_km(100, 90, "boshqa") == 90


class TestModule4Payment:
    def test_quality_index(self):
        assert payment.quality_index(100, 5) == pytest.approx(95.0)
        assert payment.quality_index(0, 0) == 0.0

    def test_multiplicative(self):
        # S = 16176 * 100 * (1-a)(1-b)(1-g)
        assert payment.net_payment(16176, 100, 0, 0, 0) == pytest.approx(1617600)
        assert payment.net_payment(
            16176, 100, 0.20, 0.15, 0.05) == pytest.approx(16176*100*0.8*0.85*0.95)

    def test_additive(self):
        # S_neto = SKM * Lf * (1 - (a+b+g))
        v = payment.net_payment(16176, 100, 0.20, 0.15, 0.05, mode="additive")
        assert v == pytest.approx(16176*100*(1 - 0.40))
        assert v < payment.net_payment(16176, 100, 0.20, 0.15, 0.05)

    def test_coefficients(self):
        c = payment.coefficients(100, 50, 100, 20, 0)  # hajm 50% -> a=0.20
        assert c["alpha"] == 0.20
        assert c["quality_index"] == pytest.approx(80.0)  # beta=0.09
        assert c["beta"] == 0.09
        c0 = payment.coefficients(0, 0, 0, 0, 0)
        assert c0["alpha"] == 0.20 and c0["beta"] == 0.15 and c0["gamma"] == 0.05


class TestModule5Fund:
    def test_advance(self):
        assert fund.advance_amount(1000000) == 350000.0
        assert fund.advance_due_date(date(2024, 6, 10)) == date(2024, 6, 5)

    def test_subsidy(self):
        # Jamg'arma tushumi to'lovdan kam -> farq subsidiyalanadi
        assert fund.subsidy_amount(500000, 1000000) == 500000.0
        assert fund.subsidy_amount(1500000, 1000000) == 0.0
        assert fund.fund_balance(1500000, 1000000) == 500000.0
        assert fund.fund_balance(500000, 1000000) == -500000.0

    def test_business_day(self):
        # 2024-06-07 juma -> keyingi ish kuni 2024-06-10 (dushanba)
        assert fund.fund_transfer_business_day(date(2024, 6, 7)) == date(2024, 6, 10)
        assert fund.fund_transfer_business_day(date(2024, 6, 10)) == date(2024, 6, 11)


class TestMasterReport:
    def test_full_report(self):
        r = master_report(
            bus_age=2, equipment=list(contract.mandatory_equipment()),
            skm=16176, hours_per_day=8, stats_monthly_wage=2000000,
            lr=100, lf=90, kamal=100, kstjb=5, kmaq=0,
            fund_collected=900000, report_month=date(2024, 6, 1),
        )
        assert r["module1_contract"]["contract_months"] == 72
        assert r["module1_contract"]["equipment_complete"] is True
        assert r["module2_tariff"]["staff_norm"] == 1.0
        assert r["module2_tariff"]["driver_wage"] == 2400000.0
        assert r["module3_monitoring"]["raw_lf"] == 90.0
        assert r["module4_payment"]["mode"] == "multiplicative"
        # 90/100=90% -> alpha 0.025; sifat 95% -> beta 0.0
        assert r["module4_payment"]["alpha"] == 0.025
        assert r["module4_payment"]["payment"] == pytest.approx(16176*90*0.975)
        assert r["module5_fund"]["advance"] == pytest.approx(r["summary"]["total_payment"]*0.35)
        assert r["summary"]["report_month"] == "2024-06-01"

    def test_report_additive(self):
        r = master_report(skm=16176, lr=100, lf=100, kamal=100,
                          kstjb=5, kmaq=0, mode="additive")
        # sifat 95% . beta=0, hajm 100% alpha=0, gamma=0
        assert r["module4_payment"]["mode"] == "additive"
        assert r["module4_payment"]["payment"] == pytest.approx(16176*100)

    def test_report_excuse(self):
        # YTH asosli sabab: Kstjb chiqariladi, Lf rejaga ko'tariladi
        r = master_report(skm=16176, lr=100, lf=60, kamal=100,
                          kstjb=10, kmaq=0, excused_count=10,
                          excuse_reason="YTH sodir bo'ldi")
        m3 = r["module3_monitoring"]
        assert m3["is_excused"] is True
        assert m3["effective_kstjb"] == 0
        # asosli sabab: Lf = max(100, 60) = 100, alpha=0, beta=0
        assert m3["effective_lf"] == 100.0
        assert r["module4_payment"]["alpha"] == 0.0
        assert r["module4_payment"]["beta"] == 0.0

    def test_report_edge(self):
        r = master_report()  # hamma nol — jazo koeffitsiyentlari
        m4 = r["module4_payment"]
        assert m4["alpha"] == 0.20
        assert m4["beta"] == 0.15
        assert m4["gamma"] == 0.05
        assert r["summary"]["total_payment"] == 0.0