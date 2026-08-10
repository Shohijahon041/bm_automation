"""Matn ustida ishlovchi yordamchilar testlari."""

from bm_automation.app.utils.text import collapse_ws, normalize_uz, title_case, upper_norm


def test_normalize_uz_preserves_case_and_digits():
    # normalize_uz faqat apostrof/qo'shtirnoq va probellarni tozalaydi
    assert normalize_uz("O'zbek 1-daraja") == "O'zbek 1-daraja"


def test_normalize_uz_unicode_apostrophe():
    assert normalize_uz("Oʻzbekiston") == "O'zbekiston"


def test_normalize_uz_collapses_ws():
    assert normalize_uz("  Toshkent   shahar  ") == "Toshkent shahar"


def test_upper_norm():
    assert upper_norm("O'zbekiston Respublikasi") == "O'ZBEKISTON RESPUBLIKASI"
    assert upper_norm("1-Qatnov") == "1-QATNOV"


def test_title_case():
    assert title_case("o'ZBEKISTON") == "O'zbekiston"


def test_collapse_ws():
    assert collapse_ws("  a \t b \n c ") == "a b c"
    assert collapse_ws("") == ""
