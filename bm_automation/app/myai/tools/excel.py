"""Excel Tool — Excel fayllar bilan ishlash."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from . import BaseTool
from ...utils.logger import get_logger

log = get_logger("myai.tools.excel")


class ExcelTool(BaseTool):
    """Excel fayl yaratish va to'ldirish."""

    name = "excel"
    description = "Excel fayl yaratish, to'ldirish, formatlash"

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "create_report": self._create_report,
            "create_from_data": self._create_from_data,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum excel action: {action}")
        return await handler(**kwargs)

    async def _create_report(self, title: str = "", data: list[dict] = None, **kwargs) -> dict:
        """Oddiy hisobot Excel fayli yaratish."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill
        except ImportError:
            raise RuntimeError("openpyxl o'rnatilmagan: pip install openpyxl")

        wb = Workbook()
        ws = wb.active
        ws.title = title[:31] or "Hisobot"

        # Sarlavha
        header_font = Font(bold=True, size=12)
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font_white = Font(bold=True, size=12, color="FFFFFF")

        if data:
            headers = list(data[0].keys())
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=header)
                cell.font = header_font_white
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center")

            for row_idx, row_data in enumerate(data, 2):
                for col_idx, key in enumerate(headers, 1):
                    ws.cell(row=row_idx, column=col_idx, value=row_data.get(key))

            # Avtomatik kenglik
            for col in range(1, len(headers) + 1):
                max_len = max(
                    len(str(ws.cell(row=r, column=col).value or ""))
                    for r in range(1, len(data) + 2)
                )
                ws.column_dimensions[chr(64 + col)].width = min(max_len + 2, 50)

        path = os.path.join(
            tempfile.gettempdir(),
            f"myai_report_{title.replace(' ', '_')}.xlsx"
        )
        wb.save(path)
        log.info("Excel hisobot yaratildi: %s", path)
        return {"path": path, "rows": len(data or [])}

    async def _create_from_data(self, headers: list[str] = None, rows: list[list] = None, **kwargs) -> dict:
        """Formatlangan Excel fayl yaratish."""
        data = []
        if headers and rows:
            for row in rows:
                data.append(dict(zip(headers, row)))
        return await self._create_report(title="Hisobot", data=data)
