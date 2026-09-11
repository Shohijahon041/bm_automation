"""Excel kataklariga xavfsiz yozish (MergedCell — read-only) yordamchisi.

Birlashtirilgan (merged) kataklarning markazdagi hujayralari o'qish-yozish
uchun yopiq (`MergedCell.value` — read-only). Shu sababli qiymat birlashmaning
yuqori-chap (anchor) katagiga yoziladi. Bu yerda bitta umumiy funksiya bilan
har ikkala Excel yozuvchisi (grafik va jadval to'ldirish) bir xil, xatosiz
ishlaydi.
"""

from __future__ import annotations

from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.worksheet import Worksheet

__all__ = ["resolve_cell", "set_cell"]


def resolve_cell(ws: Worksheet, row: int, col: int):
    """Berilgan koordinata uchun yozish mumkin bo'lgan haqiqiy katak.

    Koordinata birlashtirilgan diapazonda bo'lsa — birlashmaning yuqori-chap
    katagi (anchor) qaytariladi. Ichma-ich birlashmalarda ham ishlaydi.
    """
    cell = ws.cell(row, col)
    while isinstance(cell, MergedCell):
        anchor = None
        for mr in ws.merged_cells.ranges:
            if (mr.min_row <= row <= mr.max_row
                    and mr.min_col <= col <= mr.max_col):
                anchor = ws.cell(mr.min_row, mr.min_col)
                break
        if anchor is None or anchor is cell:
            break
        cell = anchor
    return cell


def set_cell(ws: Worksheet, row: int, col: int, value,
             font=None, alignment=None) -> None:
    """Qiymat (va ixtiyoriy uslub)ni merged-xavfsiz katakka yozadi."""
    cell = resolve_cell(ws, row, col)
    cell.value = value
    if font is not None:
        cell.font = font
    if alignment is not None:
        cell.alignment = alignment
