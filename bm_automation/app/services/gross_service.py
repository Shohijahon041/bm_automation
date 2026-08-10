"""Brutto (toshuvchi) hisobot xizmati: trip, route.

API so'rovlar `GrossRepository`/`RouteRepository`, JSON saqlash
`app.utils.io.save_json` orqali.
"""

from __future__ import annotations

from pathlib import Path

from ..api.client import BMClient
from ..repositories.gross_repo import MONTH_ENUM, GrossRepository
from ..repositories.route_repo import RouteRepository, find_route_ids
from ..utils.io import ensure_dir, save_json

__all__ = [
    "MONTH_ENUM",
    "find_route_ids",
    "route_tree",
    "run_route",
    "run_trip",
]


def route_tree(client: BMClient, brutto: bool = True) -> list:
    """Yo'nalish daraxti (region -> tashkilot -> park -> yo'nalish)."""
    return RouteRepository(client).tree(brutto=brutto)


def run_trip(
    client: BMClient,
    route_id: str,
    date_str: str = "",
    out_dir: str = "reports",
    save_json_file: bool = True,
    save_xlsx: bool = True,
) -> dict:
    """Trip hisobotini yaratadi va saqlaydi. Natija: {json_file, excel_file, files}."""
    from datetime import date

    date_str = date_str or date.today().isoformat()
    repo = GrossRepository(client)
    base = Path(out_dir)
    ensure_dir(base)
    stamp = date_str.replace("-", "")
    result = {"date": date_str, "files": []}

    if save_json_file:
        data = repo.trip(route_id, date_str)
        f = base / f"gross-trip_{route_id[:8]}_{stamp}.json"
        result["json_file"] = save_json(data, f)
        result["files"].append(result["json_file"])

    if save_xlsx:
        f = base / f"gross-trip_{route_id[:8]}_{stamp}.xlsx"
        result["excel_file"] = repo.trip_download(route_id, date_str, str(f))
        result["files"].append(result["excel_file"])

    return result


def run_route(
    client: BMClient,
    route_id: str,
    from_date: str = "",
    to_date: str = "",
    out_dir: str = "reports",
    save_json_file: bool = True,
    save_xlsx: bool = True,
) -> dict:
    """Route hisobotini yaratadi va saqlaydi (max 30 kun)."""
    from datetime import date

    to_date = to_date or date.today().isoformat()
    from_date = from_date or to_date
    repo = GrossRepository(client)
    base = Path(out_dir)
    ensure_dir(base)
    stamp = f"{from_date.replace('-', '')}_{to_date.replace('-', '')}"
    result = {"from": from_date, "to": to_date, "files": []}

    if save_json_file:
        data = repo.route(route_id, from_date, to_date)
        f = base / f"gross-route_{route_id[:8]}_{stamp}.json"
        result["json_file"] = save_json(data, f)
        result["files"].append(result["json_file"])

    if save_xlsx:
        f = base / f"gross-route_{route_id[:8]}_{stamp}.xlsx"
        result["excel_file"] = repo.route_download(route_id, from_date, to_date, str(f))
        result["files"].append(result["excel_file"])
        fo = base / f"gross-route-overall_{route_id[:8]}_{stamp}.xlsx"
        result["excel_overall_file"] = repo.route_overall_download(route_id, from_date, to_date, str(fo))
        result["files"].append(result["excel_overall_file"])

    return result
