"""Waybill (yo'l varaqalari) hisobot xizmati.

API so'rovlar `WaybillRepository`, JSON saqlash `app.utils.io.save_json`
orqali.
"""

from __future__ import annotations

from pathlib import Path

from ..api.client import BMClient
from ..repositories.waybill_repo import (  # noqa: F401
    DIRECTIONS,
    MAX_RANGE_DAYS,
    STATUSES,
    WAYBILL,
    WaybillRepository,
)
from ..utils.io import ensure_dir, save_json

__all__ = [
    "DIRECTIONS",
    "MAX_RANGE_DAYS",
    "STATUSES",
    "WAYBILL",
    "run",
    "save_json",
    "waybill_download",
    "waybill_report",
    "waybill_stat_plan",
]


def waybill_report(client: BMClient, route_id: str, from_date: str, to_date: str) -> list:
    """Waybill hisobotini JSON ko'rinishida qaytaradi."""
    return WaybillRepository(client).report(route_id, from_date, to_date)


def waybill_download(
    client: BMClient,
    route_id: str,
    from_date: str,
    to_date: str,
    out_file: str,
    plate_num: str | None = None,
    direction: str | None = None,
    status: str | None = None,
) -> str:
    """Waybill hisobotini Excel qilib yuklab oladi."""
    return WaybillRepository(client).download(
        route_id, from_date, to_date, out_file,
        plate_num=plate_num, direction=direction, status=status,
    )


def waybill_stat_plan(client: BMClient, route_id: str, from_date: str, to_date: str):
    """Reja statistikasi: totalTrip, forwardTrip, backwardTrip, vehicles."""
    return WaybillRepository(client).stat_plan(route_id, from_date, to_date)


def run(
    client: BMClient,
    route_id: str,
    from_date: str = "",
    to_date: str = "",
    out_dir: str = "reports",
    save_json_file: bool = True,
    save_xlsx: bool = True,
    **filters,
) -> dict:
    """Waybill hisobotini yaratadi va saqlaydi."""
    from datetime import date

    to_date = to_date or date.today().isoformat()
    from_date = from_date or to_date
    repo = WaybillRepository(client)
    repo.validate_range(from_date, to_date)
    base = Path(out_dir)
    ensure_dir(base)
    stamp = f"{from_date.replace('-', '')}_{to_date.replace('-', '')}"
    result = {"from": from_date, "to": to_date, "files": []}

    if save_json_file:
        data = repo.report(route_id, from_date, to_date)
        f = base / f"waybill_{route_id[:8]}_{stamp}.json"
        result["json_file"] = save_json(data, f)
        result["files"].append(result["json_file"])

    if save_xlsx:
        f = base / f"waybill_{route_id[:8]}_{stamp}.xlsx"
        result["excel_file"] = repo.download(
            route_id, from_date, to_date, str(f), **filters
        )
        result["files"].append(result["excel_file"])

    return result
