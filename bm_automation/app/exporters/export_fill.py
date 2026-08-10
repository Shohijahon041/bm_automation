"""Yo'nalish Jadvali Export xlsx'sini to'ldirish (faqat Excel ishi).

Saytdan olingan shift grafik export shabloni berilgan ma'lumotlar bilan
to'ldiriladi:
    - sarlavhada ("10-сонли автобус йўналиши") sana yoziladi
    - "Иш куни" katagiga haydovchi ism-familyasi yoziladi
    - grafik raqami (F) o'rniga avtobus raqami yoziladi

Ma'lumot yig'ish (duty/shift API) `app/services/export_service` da.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

__all__ = ["fill_export_workbook"]


def fill_export_workbook(template_bytes: bytes, date_label: str,
                         by_graph: dict, out_path: str | Path) -> Path:
    """Export shablonini to'ldirib, `out_path`ga saqlaydi.

    by_graph: {"P1": {"driver": str, "plate": str}, ...}
    """
    tmp_dir = Path(os.environ.get("TEMP", tempfile.gettempdir()))
    tmp = tmp_dir / f"graph_export_{os.getpid()}.xlsx"
    tmp.write_bytes(template_bytes)

    wb = openpyxl.load_workbook(tmp)
    ws = wb.active
    bold = Font(bold=True, size=12)
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    def set_cell(row, col, value, font=None, align=None):
        cell = ws.cell(row, col)
        if isinstance(cell, openpyxl.cell.cell.MergedCell):
            for mr in ws.merged_cells.ranges:
                if cell.coordinate in mr:
                    cell = ws.cell(mr.min_row, mr.min_col)
                    break
        cell.value = value
        if font:
            cell.font = font
        if align:
            cell.alignment = align

    block_rows = [r_idx for r_idx, row in enumerate(ws.iter_rows(), start=1)
                  if row[0].value == "Иш куни"]

    for idx, r in enumerate(block_rows, start=1):
        info = by_graph.get(f"P{idx}") or {}
        set_cell(r - 1, 1, date_label, bold, center)
        set_cell(r, 2, info.get("driver") or "", bold, left)
        set_cell(r, 6, info.get("plate") or "", bold, center)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return out_path
