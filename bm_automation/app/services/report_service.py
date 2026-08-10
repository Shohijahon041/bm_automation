"""Operativ hisobotlarni yaratish xizmati.

REPORTS reyestri `app/reports/registry` da. API so'rovlar `BMClient`
orqali, JSON/Excel saqlash `app.utils.io.save_json` orqali bajariladi.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from ..api.client import BMClient
from ..reports.registry import REPORTS, VALID_TYPES
from ..utils.io import save_json

__all__ = ["generate_report", "generate_daily_reports", "export_week_range"]


def _resolve_params(spec: dict, extra: dict, date_str: str, report_type: str) -> dict:
    params = {"type": report_type}
    if "date" in spec["params"]:
        params["date"] = date_str
    params.update({k: v for k, v in extra.items() if v is not None})
    return params


def generate_report(
    client: BMClient,
    report_name: str,
    report_type: str = "DAILY",
    date_str: str = "",
    out_dir: str = "reports",
    extra_params: dict | None = None,
    save_json: bool = True,
    save_excel: bool = True,
) -> dict:
    """Bitta hisobotni yaratadi. JSON + Excel (agar mavjud bo'lsa) saqlaydi."""
    if report_name not in REPORTS:
        raise KeyError(
            f"'{report_name}' mavjud emas. Tanlang: {', '.join(sorted(REPORTS))}"
        )
    if report_type not in VALID_TYPES:
        raise ValueError(f"type qiymati: {', '.join(VALID_TYPES)}")

    date_str = date_str or date.today().isoformat()
    spec = REPORTS[report_name]
    params = _resolve_params(spec, extra_params or {}, date_str, report_type)

    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    safe_name = report_name.replace("/", "_")
    stamp = date_str.replace("-", "")

    result = {"name": report_name, "type": report_type, "date": date_str, "files": []}

    try:
        if save_json:
            data = client.get(spec["url"], params=params)
            json_file = base / f"{safe_name}_{stamp}.json"
            save_json(data, json_file)
            result["files"].append(str(json_file))
            result["json_file"] = str(json_file)

        if save_excel and spec.get("excel"):
            excel_file = base / f"{safe_name}_{stamp}.xlsx"
            client.download(spec["excel"], str(excel_file), params=params)
            result["files"].append(str(excel_file))
            result["excel_file"] = str(excel_file)

        _db_record(report_name, report_type, date_str, params, result)
    except Exception as exc:
        _db_error(report_name, exc)
        raise
    return result


def _db_record(name: str, report_type: str, date_str: str,
               params: dict, result: dict) -> None:
    """Yaratilgan hisobotni DB'ga qayd qiladi (best-effort)."""
    try:
        from ..db.storage import get_storage
        from ..db.sync import AutomationLogger

        storage = get_storage()
        if not storage.enabled:
            return
        out_file = result.get("excel_file") or result.get("json_file") or ""
        storage.save_report(
            name=name, report_type=report_type, period_date=date_str,
            params=params, file_path=out_file,
            data={"files": result.get("files", [])},
        )
        AutomationLogger(storage).report_run(
            report_name=name, status="OK", output_file=out_file)
    except Exception:  # noqa: BLE001 - DB xatosi hisobotni buzmaydi
        pass


def _db_error(name: str, exc: BaseException) -> None:
    try:
        from ..db.storage import get_storage
        from ..db.sync import AutomationLogger

        storage = get_storage()
        if not storage.enabled:
            return
        AutomationLogger(storage).report_run(
            report_name=name, status="ERROR", error=str(exc))
    except Exception:  # noqa: BLE001
        pass


def generate_daily_reports(
    client: BMClient,
    date_str: str = "",
    out_dir: str = "reports",
    names: list | None = None,
    report_type: str = "DAILY",
) -> list:
    """Bir nechta hisobotlarni ketma-ket yaratadi. Xatolarni o'tkazib yuboradi."""
    date_str = date_str or date.today().isoformat()
    names = names or [k for k, s in REPORTS.items() if s.get("excel")]
    results = []
    for name in names:
        try:
            results.append(
                generate_report(client, name, report_type=report_type, date_str=date_str, out_dir=out_dir)
            )
            print(f"OK  {name} ({date_str})")
        except Exception as exc:
            print(f"XATO {name}: {exc}")
    return results


def export_week_range(
    client: BMClient,
    start_date: date,
    report_name: str = "bus-region",
    out_dir: str = "reports",
) -> list:
    """Hafta davomidagi har kun uchun hisobot yuklab oladi."""
    results = []
    for i in range(7):
        day = start_date + timedelta(days=i)
        results.append(
            generate_report(client, report_name, report_type="DAILY", date_str=day.isoformat(), out_dir=out_dir)
        )
    return results
