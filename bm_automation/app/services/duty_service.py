"""Kunlik duty (haydovchi/avtobus/grafik) xizmati.

Business logic shu yerda; API so'rovlar `DutyRepository`, Excel yozish
`app.exporters.excel_export` orqali.
"""

from __future__ import annotations

from pathlib import Path

from ..api.client import BMClient
from ..exporters.excel_export import DUTY_HEADERS, duty_to_excel
from ..repositories.duty_repo import DUTY, DutyRepository
from ..utils.io import ensure_dir, save_json

__all__ = ["DUTY", "DUTY_HEADERS", "duty_by_date", "graphs_to_rows", "run", "to_excel"]


def duty_by_date(client: BMClient, route_id: str, date_str: str) -> dict:
    """Bir kunlik duty ma'lumotini qaytaradi (graphs bilan)."""
    return DutyRepository(client).by_date(route_id, date_str)


def graphs_to_rows(duty: dict) -> list[dict]:
    """Duty ma'lumotini jadval qatorlariga aylantiradi (2-haydovchi ham)."""
    rows = []
    date_str = duty.get("date", "")
    for g in duty.get("graphs") or []:
        row = {
            "date": date_str,
            "graphName": g.get("graphName"),
            "shiftName": g.get("shiftName"),
            "driverName": g.get("driverName"),
            "timeTableNumber": g.get("timeTableNumber"),
            "plateNum": g.get("plateNum"),
            "garageNumber": g.get("garageNumber"),
            "vehicleModel": g.get("vehicleModel"),
            "vehicleId": g.get("vehicleId"),
            "startTime": g.get("startTime"),
            "endTime": g.get("endTime"),
            "tripCount": g.get("tripCount"),
            "isReplaced": g.get("isReplaced"),
            "dutyId": g.get("dutyId"),
            "driverId": g.get("driverId"),
        }
        rows.append(row)
        if g.get("hasSecond"):
            rows.append({
                **{k: None for k in row},
                "date": date_str,
                "graphName": g.get("graphName"),
                "shiftName": g.get("shiftName"),
                "driverName": g.get("secondDriverName"),
                "timeTableNumber": g.get("secondTimeTableNumber"),
                "plateNum": g.get("plateNum"),
                "garageNumber": g.get("garageNumber"),
                "vehicleModel": g.get("vehicleModel"),
                "vehicleId": g.get("vehicleId"),
                "startTime": g.get("secondStartTime"),
                "endTime": g.get("secondEndTime"),
                "tripCount": g.get("tripCount"),
                "isReplaced": g.get("isReplaced"),
                "dutyId": g.get("dutyId"),
                "driverId": g.get("secondDriverId"),
            })
    return rows


def to_excel(rows: list[dict], out_file: str) -> str:
    """Duty qatorlarini Excel jadvaliga yozadi (compat)."""
    return duty_to_excel(rows, out_file)


def run(
    client: BMClient,
    route_id: str,
    date_str: str = "",
    out_dir: str = "reports",
    save_json_file: bool = True,
    save_xlsx: bool = True,
) -> dict:
    """Bir kunlik duty ma'lumotini yuklab, JSON + Excel saqlaydi."""
    from datetime import date

    date_str = date_str or date.today().isoformat()
    duty = DutyRepository(client).by_date(route_id, date_str)
    base = Path(out_dir)
    ensure_dir(base)
    stamp = date_str.replace("-", "")
    result = {"date": date_str, "files": []}

    if save_json_file:
        f = base / f"duty_{route_id[:8]}_{stamp}.json"
        save_json(duty, f)
        result["json_file"] = str(f)
        result["files"].append(str(f))

    if save_xlsx:
        rows = graphs_to_rows(duty)
        f = base / f"duty_{route_id[:8]}_{stamp}.xlsx"
        result["excel_file"] = duty_to_excel(rows, str(f))
        result["files"].append(str(f))

    return result
