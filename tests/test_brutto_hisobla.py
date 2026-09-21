"""116-son qarori formulasi uchun modul-darajasidagi funksiyalar testlari."""

from bm_automation.app.dashboard.brutto_calculator import (
    alpha_hisobla,
    beta_hisobla,
    gamma_hisobla,
    tolov_hisobla,
)


class TestAlpha:
    def test_alpha_70_dan_past(self):
        assert alpha_hisobla(60, 100) == 0.20

    def test_alpha_70_79(self):
        assert alpha_hisobla(75, 100) == 0.15

    def test_alpha_80_84(self):
        assert alpha_hisobla(82, 100) == 0.10

    def test_alpha_85_89(self):
        assert alpha_hisobla(87, 100) == 0.05

    def test_alpha_90_94(self):
        assert alpha_hisobla(92, 100) == 0.025

    def test_alpha_95_plus(self):
        assert alpha_hisobla(96, 100) == 0.00
        assert alpha_hisobla(100, 100) == 0.00

    def test_alpha_lr_zero(self):
        assert alpha_hisobla(100, 0) == 0.20


class TestBeta:
    def test_beta_barcha_oraliglar(self):
        assert beta_hisobla(100, 50) == 0.15   # 50%
        assert beta_hisobla(100, 25) == 0.12   # 75%
        assert beta_hisobla(100, 18) == 0.09   # 82%
        assert beta_hisobla(100, 12) == 0.06   # 88%
        assert beta_hisobla(100, 8)  == 0.03   # 92%
        assert beta_hisobla(100, 2)  == 0.00   # 98%

    def test_beta_kamal_zero(self):
        assert beta_hisobla(0, 0) == 0.15


class TestGamma:
    def test_gamma_barcha_oraliglar(self):
        assert gamma_hisobla(100, 50) == 0.05
        assert gamma_hisobla(100, 25) == 0.04
        assert gamma_hisobla(100, 18) == 0.03
        assert gamma_hisobla(100, 12) == 0.02
        assert gamma_hisobla(100, 8)  == 0.01
        assert gamma_hisobla(100, 2)  == 0.00

    def test_gamma_kamal_zero(self):
        assert gamma_hisobla(0, 0) == 0.05


class TestTolov:
    def test_tolov_ideal_holat(self):
        """Barcha ko'rsatkichlar 100% — ushlab qolinmaydi."""
        h = tolov_hisobla(16176, 100, 100, 100, 0, 0)
        assert h["alpha"] == 0.0
        assert h["beta"] == 0.0
        assert h["gamma"] == 0.0
        assert h["tolov"] == 16176 * 100  # 1,617,600

    def test_tolov_barcha_jarima(self):
        """Barcha ko'rsatkichlar past — barcha ushlab qolinadi."""
        h = tolov_hisobla(16176, 50, 100, 100, 50, 50)
        assert h["alpha"] == 0.20
        assert h["beta"] == 0.15
        assert h["gamma"] == 0.05
        kutilgan = 16176 * 50 * 0.8 * 0.85 * 0.95
        assert abs(h["tolov"] - kutilgan) < 0.01

    def test_tolov_qarordagi_misol(self):
        """01.07.2026 P1 qatori uchun qaror bo'yicha hisob."""
        h = tolov_hisobla(16176, 218.65, 232.4, 17, 1, 0)
        assert h["alpha"] == 0.025
        assert h["beta"] == 0.03
        assert h["gamma"] == 0.00
        kutilgan = 16176 * 218.65 * 0.975 * 0.97 * 1.0
        assert abs(h["tolov"] - kutilgan) < 0.01


_ROW_DICT = {
    "sana": "2026-07-01", "grafik": "P1", "davlat_raqami": "60870GCA",
    "fio": "Karimov A.",
    "lr": 232.4, "lf": 218.65, "kamal": 17, "kstjb": 1, "kmaq": 0,
}


class TestMoliyaviyKo:
    """Moliya bo'limi talab qilgan qo'shimcha ko'rsatkichlar."""

    def _natija(self):
        from datetime import date
        from bm_automation.app.dashboard.brutto_calculator import (
            BruttoCalculator, Contract,
        )
        calc = BruttoCalculator(Contract(
            skm=16176.0, route_number="B-80", carrier="ASL SUNDAY MCHJ",
            valid_from=date(2026, 7, 1), valid_to=date(2026, 7, 31)))
        return calc.process_row(dict(_ROW_DICT))

    def test_brutto_100(self):
        """Brutto_100% = Lr x SKM = 232.4 x 16176."""
        n = self._natija()
        assert n.brutto_100 == 232.4 * 16176  # 3,759,302.40

    def test_jarima(self):
        """Jarima = (Lr - Lf) x SKM = 13.75 x 16176."""
        n = self._natija()
        assert n.jarima == (232.4 - 218.65) * 16176  # 222,420.00

    def test_haydovchi_ish_haqi(self):
        """Haydovchi = Lf x 2363.8 so'm/km."""
        n = self._natija()
        assert abs(n.haydovchi_ish_haqi - 516844.87) < 0.01  # 218.65 x 2363.8

    def test_haydovchi_soliq(self):
        """Soliq 12% = ish haqi x 0.12."""
        n = self._natija()
        assert abs(n.haydovchi_soliq - 516844.87 * 0.12) < 0.01

    def test_haydovchi_qolga(self):
        """Qo'lga tegishi = ish haqi - soliq."""
        n = self._natija()
        assert abs(n.haydovchi_qolga
                   - (n.haydovchi_ish_haqi - n.haydovchi_soliq)) < 0.01

    def test_elektr(self):
        """Elektr: kWt = Lf x 0.955, summa = kWt x 1100."""
        n = self._natija()
        assert abs(n.elektr_kwt - 218.65 * 0.955) < 0.01      # ~208.81 kWt
        assert abs(n.elektr_summ - 218.65 * 0.955 * 1100) < 0.01  # ~229,691

    def test_tolov_va_qolgan_maydonlar(self):
        """Tolov eski formulkacha saqlanadi, yangi maydonlar bor."""
        n = self._natija()
        assert n.alpha == 0.025
        assert n.beta == 0.03
        assert n.gamma == 0.00