"""Excel o'qish (rejadagi jadval) va export to'ldirish testlari."""

from datetime import date

import openpyxl
import pytest

from bm_automation.app.exporters.export_fill import fill_export_workbook
from bm_automation.app.services.excel_fill_service import (
    _to_date,
    _to_time,
    parse_plan_excel,
)


def _make_plan_xlsx(tmp_path, date_cell="01.08.2026"):
    path = tmp_path / "plan.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = f"10-сонли автобус йўналиши  {date_cell}"
    ws["A2"] = "Sana"
    ws["B2"] = "Grafik"
    ws["C2"] = "Smena"
    ws["D2"] = "Haydovchi"
    ws["E2"] = "Jadval raqami"
    ws["F2"] = "Avtobus"
    ws["G2"] = "Boshlanish"
    ws["H2"] = "Tugash"
    ws["A3"] = date_cell
    ws["B3"] = "P1"
    ws["C3"] = "DU"
    ws["D3"] = "Aliyev Aliy"
    ws["E3"] = "101"
    ws["F3"] = "01A123AA"
    ws["G3"] = "06:00"
    ws["H3"] = "14:00"
    ws["B4"] = "P2"
    ws["D4"] = "Karimov Karim"
    wb.save(path)
    return path


def test_parse_plan_excel(tmp_path):
    parsed = parse_plan_excel(str(_make_plan_xlsx(tmp_path)))
    assert parsed["date"] == "2026-08-01"
    assert len(parsed["rows"]) == 2
    first = parsed["rows"][0]
    assert first["graph"] == "P1"
    assert first["driver"] == "Aliyev Aliy"  # normalize_uz (katta-kichik saqlanadi)
    assert first["start"] == "06:00"
    assert first["end"] == "14:00"
    assert first["plate"] == "01A123AA"


def test_parse_plan_excel_missing_headers(tmp_path):
    path = tmp_path / "bad.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Hech qanday ustun yo'q"
    wb.save(path)
    with pytest.raises(ValueError):
        parse_plan_excel(str(path))


def test_to_date_parses_formats():
    assert _to_date("01.08.2026") == date(2026, 8, 1)
    assert _to_date("2026-08-01") == date(2026, 8, 1)
    assert _to_date("") is None


def test_to_time_parses():
    assert _to_time("06:00") == "06:00"
    assert _to_time("6.00") == "06:00"
    assert _to_time("") == ""


def _make_export_template(tmp_path):
    path = tmp_path / "template.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "10-сонли автобус йўналиши"
    ws["A3"] = "Иш куни"
    ws["B3"] = "Haydovchi"
    ws["F3"] = "Avtobus"
    ws["A13"] = "Иш куни"
    wb.save(path)
    return path


def test_fill_export_workbook(tmp_path):
    template = _make_export_template(tmp_path)
    by_graph = {
        "P1": {"driver": "Aliyev Aliy", "plate": "01A123AA"},
        "P2": {"driver": "Karimov Karim", "plate": "01B456BB"},
    }
    out = tmp_path / "filled.xlsx"
    result = fill_export_workbook(template.read_bytes(), "01.08.2026", by_graph, out)

    wb = openpyxl.load_workbook(out)
    ws = wb.active
    assert ws["A2"].value == "01.08.2026"
    assert ws["B3"].value == "Aliyev Aliy"
    assert ws["F3"].value == "01A123AA"
    assert ws["B13"].value == "Karimov Karim"
    assert ws["F13"].value == "01B456BB"
    assert result == out
