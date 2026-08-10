"""Oylik/yillik duty jadvalini shakllantirish xizmati.

Kunlik duty JSON fayllarini (yoki API'dan jonli ma'lumotni) yig'ib,
oylik Excel jadvalini tuzadi (Excel ishi `app.exporters.excel_export` da).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from ..api.client import BMApiError, BMClient
from ..exporters.excel_export import monthly_excel
from ..repositories.duty_repo import DUTY, DutyRepository
from ..utils.io import ensure_dir

__all__ = [
    "collect_days",
    "driver_summary",
    "monthly_rows",
    "run",
]

DUTY_FILE_RE = re.compile(r"duty_([0-9a-f]{8})_(\d{8})\.json")


def _load_duty_file(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _find_saved_files(out_dir: str, route_id: str) -> dict[str, dict]:
    """Sana -> duty dict. Papkadagi duty_*.json fayllarini yig'adi."""
    out = {}
    base = Path(out_dir)
    if not base.exists():
        return out
    prefix = f"duty_{route_id[:8]}_"
    for p in base.glob("duty_*.json"):
        if not p.name.startswith(prefix):
            continue
        data = _load_duty_file(p)
        if not data or not data.get("date"):
            continue
        out[data["date"]] = data
    return out


def collect_days(
    client: BMClient | None,
    route_id: str,
    from_date: date,
    to_date: date,
    out_dir: str = "reports",
) -> list[dict]:
    """from..to oralig'idagi har kun uchun duty dict'larini yig'adi.

    Avval saqlangan fayllarni tekshiradi, yetishmaydigan kunlarni
    API'dan yuklab oladi (client berilgan bo'lsa).
    """
    saved = _find_saved_files(out_dir, route_id)
    repo = DutyRepository(client) if client is not None else None
    days = []
    for i in range((to_date - from_date).days + 1):
        d = from_date + timedelta(days=i)
        ds = d.isoformat()
        data = saved.get(ds)
        if data is None and repo is not None:
            try:
                data = repo.by_date(route_id, ds)
            except BMApiError as exc:
                if exc.status != 404:
                    print(f"  {ds}: yuklab bo'lmadi: {exc}")
                data = None
        days.append({"date": ds, "duty": data})
    return days


def _graph_columns(days: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for day in days:
        duty = day["duty"]
        if not duty:
            continue
        for g in duty.get("graphs") or []:
            name = g.get("graphName") or "?"
            if name not in seen:
                seen[name] = None
    return sorted(seen, key=lambda s: (len(s), s))


def monthly_rows(days: list[dict]) -> tuple[list[dict], list[str]]:
    """Har kun uchun: grafiklar, haydovchi, avtobus.

    Qaytaradi: (qatorlar, grafik ro'yxati).
    Qator: {date, weekday, grapich: {driver, bus}}
    """
    graphs = _graph_columns(days)
    rows = []
    for day in days:
        ds = day["date"]
        d = date.fromisoformat(ds)
        duty = day["duty"]
        cell = {g: {"driver": "", "bus": "", "shift": ""} for g in graphs}
        if duty:
            for g in duty.get("graphs") or []:
                name = g.get("graphName") or "?"
                if name not in cell:
                    cell[name] = {"driver": "", "bus": "", "shift": ""}
                driver = g.get("driverName") or ""
                if g.get("hasSecond") and g.get("secondDriverName"):
                    driver = f"{driver} / {g.get('secondDriverName')}"
                cell[name] = {
                    "driver": driver,
                    "bus": g.get("plateNum") or "",
                    "shift": g.get("shiftName") or "",
                }
                graphs = sorted(set(graphs + [name]), key=lambda s: (len(s), s))
        rows.append({"date": ds, "weekday": d.strftime("%A"), "graphs": cell})
    return rows, graphs


def driver_summary(days: list[dict]) -> list[dict]:
    """Haydovchi bo'yicha: ishlagan kunlar, smena soatlari, avtobuslar."""
    stats = defaultdict(lambda: {"days": 0, "hours": 0.0, "buses": set(), "graphics": set()})
    for day in days:
        duty = day["duty"]
        if not duty:
            continue
        for g in duty.get("graphs") or []:
            for key in ("driver", "secondDriver"):
                name = g.get(f"{key}Name") if key == "driver" else g.get("secondDriverName")
                if not name:
                    continue
                st = g.get("startTime" if key == "driver" else "secondStartTime")
                et = g.get("endTime" if key == "driver" else "secondEndTime")
                hours = 0.0
                try:
                    if st and et:
                        t1 = datetime.strptime(st, "%H:%M:%S")
                        t2 = datetime.strptime(et, "%H:%M:%S")
                        hours = round((t2 - t1).total_seconds() / 3600, 1)
                except ValueError:
                    pass
                s = stats[name]
                s["days"] += 1
                s["hours"] += hours
                s["buses"].add(g.get("plateNum") or "-")
                s["graphics"].add(g.get("graphName") or "?")
    rows = [
        {
            "driver": name,
            "days": st["days"],
            "hours": st["hours"],
            "buses": ", ".join(sorted(st["buses"])),
            "graphics": ", ".join(sorted(st["graphics"], key=lambda s: (len(s), s))),
        }
        for name, st in sorted(stats.items(), key=lambda kv: kv[1]["days"], reverse=True)
    ]
    return rows


def run(
    client: BMClient,
    route_id: str,
    from_date: str,
    to_date: str,
    out_dir: str = "reports",
    out_file: str = "",
    download_missing: bool = True,
) -> dict:
    fd = date.fromisoformat(from_date)
    td = date.fromisoformat(to_date)
    if td < fd:
        raise ValueError("to_date < from_date")
    if (td - fd).days > 366:
        raise ValueError("Oraliq 1 yildan oshmasin")

    days = collect_days(client if download_missing else None, route_id, fd, td, out_dir)
    rows, graphs = monthly_rows(days)
    driver_rows = driver_summary(days)

    if not out_file:
        stamp = f"{fd:%Y%m%d}-{td:%Y%m%d}"
        out_file = str(Path(out_dir) / f"summary_{route_id[:8]}_{stamp}.xlsx")
    Path(out_file).parent.mkdir(parents=True, exist_ok=True)
    monthly_excel(rows, graphs, driver_rows, out_file)

    loaded = sum(1 for d in days if d["duty"])
    return {
        "from": from_date,
        "to": to_date,
        "days_total": len(days),
        "days_with_duty": loaded,
        "graphs": graphs,
        "drivers": len(driver_rows),
        "excel_file": out_file,
    }
