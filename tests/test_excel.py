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
        "P1": {"driver": "Aliyev Aliy", "plate": "01A123AA", "graph": "P1"},
        "P2": {"driver": "Karimov Karim", "plate": "01B456BB", "graph": "P2"},
    }
    out = tmp_path / "filled.xlsx"
    result = fill_export_workbook(template.read_bytes(), "01.08.2026", by_graph, out)

    wb = openpyxl.load_workbook(out)
    ws = wb.active
    assert ws["A2"].value == "01.08.2026"
    assert ws["B3"].value == "Aliyev Aliy"
    assert ws["E3"].value == "P1"
    assert ws["F3"].value == "01A123AA"
    assert ws["B13"].value == "Karimov Karim"
    assert ws["E13"].value == "P2"
    assert ws["F13"].value == "01B456BB"
    assert result == out


def _make_merged_export_template(tmp_path):
    """Sanasi birlashtirilgan (MergedCell) katak ichiga tushadigan shablon."""
    path = tmp_path / "merged_template.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "10-сонли автобус йўналиши"
    ws.merge_cells("A2:A3")          # A2 anchor, A3 -> MergedCell
    ws["A4"] = "Иш куни"
    ws["B4"] = "Haydovchi"
    ws["F4"] = "Avtobus"
    ws.merge_cells("A5:A6")          # A5 anchor, A6 -> MergedCell
    ws["A7"] = "Иш куни"
    wb.save(path)
    return path


def test_fill_export_workbook_merged_cells(tmp_path):
    """MergedCell ichiga yozish xato bermasligi, anchor katakka yozilishi."""
    template = _make_merged_export_template(tmp_path)
    by_graph = {"P1": {"driver": "Aliyev Aliy", "plate": "01A123AA", "graph": "P1"}}
    out = tmp_path / "filled_merged.xlsx"
    fill_export_workbook(template.read_bytes(), "02.08.2026", by_graph, out)

    wb = openpyxl.load_workbook(out)
    ws = wb.active
    assert ws["A2"].value == "02.08.2026"   # A3 (MergedCell) -> anchor A2
    assert ws["B4"].value == "Aliyev Aliy"
    assert ws["E4"].value == "P1"
    assert ws["F4"].value == "01A123AA"


def test_set_cell_merged_anchor(tmp_path):
    from bm_automation.app.exporters.excel_cells import set_cell

    path = tmp_path / "m.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.merge_cells("B2:D4")
    set_cell(ws, 3, 3, "X")
    assert ws["B2"].value == "X"
    assert ws["C3"].value is None


def test_fill_grafik_merged_cells(tmp_path):
    from datetime import date as _date

    from daily_grafik import fill_grafik

    tpl = tmp_path / "graf.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["E2"] = "P1"
    ws["E4"] = "P2"
    ws.merge_cells("A1:C1")           # sarlavha birlashtirilgan
    ws["A1"] = "old"
    ws.merge_cells("F2:F3")           # avtobus raqami hujayrasi merged
    ws["F2"] = "old-plate"
    ws.merge_cells("A4:B4")
    wb.save(tpl)

    out = tmp_path / "out.xlsx"
    by_graph = {"P1": {"driver": "Aliyev Aliy", "plate": "01A123AA"}}
    title, nblocks, filled, _ = fill_grafik(
        str(tpl), str(out), "B-80", "ISH KUNI", _date(2026, 8, 17), by_graph)

    wb2 = openpyxl.load_workbook(out)
    ws2 = wb2.active
    assert nblocks == 2
    assert filled == 1
    assert ws2["A1"].value == title        # merged sarlavhaga yozildi
    assert ws2["A2"].value == "Aliyev Aliy"
    assert ws2["F2"].value == "01A123AA"   # F3 MergedCell -> anchor F2
