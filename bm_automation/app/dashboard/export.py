"""Dashboard eksporti — CSV, Excel (xlsx) va PDF.

`build_export` umumiy kirish nuqtasi: `scope` bo'yicha ma'lumotni
`metrics.Metrics` dan olib, so'ralgan formatda bytes qaytaradi.

PDF `matplotlib` (Agg) orqali jadval ko'rinishida yaratiladi.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from . import metrics as m


def _kv(f: dict) -> list:
    return [("sana", f.get("date") or f.get("from", "")),
            ("route", f.get("route", "") or "barchasi"),
            ("vehicle", f.get("vehicle", "") or "barchasi"),
            ("driver", f.get("driver", "") or "barchasi"),
            ("status", f.get("status", "") or "barchasi")]


def _data_for(scope: str, met: m.Metrics, f: dict) -> tuple[str, list, list[list]]:
    """scope -> (sarlavha, ustunlar, qatorlar)."""
    if scope == "today":
        t = met.today(f)
        cols = ["ko'rsatkich", "qiymat"]
        rows = [
            ["Sana", t["date"]],
            ["Jami avtobuslar", t["total_buses"]],
            ["Faol avtobuslar", t["active_buses"]],
            ["Faol emas", t["not_active_buses"]],
            ["Jami reyslar", t["total_trips"]],
            ["Bajarilgan", t["completed"]],
            ["Qabul qilingan", t["accepted"]],
            ["Qabul qilinmagan", t["not_accepted"]],
            ["Pending", t["pending"]],
            ["Rejected", t["rejected"]],
            ["Zero mileage", t["zero_mileage"]],
        ]
        return "Bugungi ko'rsatkichlar", cols, rows

    if scope == "routes":
        cols = ["route_id", "nom", "planned", "actual", "accepted",
                "not_accepted", "rejected", "zero_mileage", "performance %"]
        rows = [[r["route_id"], r["name"], r["planned"], r["actual"],
                 r["accepted"], r["not_accepted"], r["rejected"],
                 r["zero_mileage"], r["performance"]] for r in met.routes(f)]
        return "Yo'nalishlar", cols, rows

    if scope == "vehicles":
        cols = ["vehicle_id", "davlat raqami", "garaj", "model", "status",
                "trips", "issues", "last activity"]
        rows = [[v["vehicle_id"], v["plate_number"], v["garage_number"],
                 v["model"], v["status"], v["trips"], v["issues"],
                 v["last_activity"]] for v in met.vehicles(f)]
        return "Avtobuslar", cols, rows

    if scope == "drivers":
        cols = ["driver_id", "ism", "trips", "working days", "issues", "attendance %"]
        rows = [[d["driver_id"], d["name"], d["trips"], d["working_days"],
                 d["issues"], d["attendance"]] for d in met.drivers(f)]
        return "Haydovchilar", cols, rows

    # trips (default)
    cols = ["id", "date", "route_id", "vehicle_id", "driver_id",
            "planned_time", "actual_time", "status", "source"]
    rows = [[t.get("id"), t.get("date"), t.get("route_id"), t.get("vehicle_id"),
             t.get("driver_id"), t.get("planned_time"), t.get("actual_time"),
             t.get("status"), t.get("source")] for t in met.trips(f)]
    return "Reyslar", cols, rows


def export_csv(filters: dict, scope: str = "trips") -> bytes:
    met = m.Metrics()
    f = m.parse_filters(filters)
    title, cols, rows = _data_for(scope, met, f)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title])
    w.writerow([f"{k}: {v}" for k, v in _kv(f)])
    w.writerow([])
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def export_xlsx(filters: dict, scope: str = "trips") -> bytes:
    from openpyxl import Workbook

    met = m.Metrics()
    f = m.parse_filters(filters)
    wb = Workbook()
    ws = wb.active
    ws.title = "Xulosa"
    ws.append(["BM Automation — hisobot"])
    for k, v in _kv(f):
        ws.append([k, v])

    if scope == "all":
        scopes = ["today", "routes", "vehicles", "drivers", "trips"]
    else:
        scopes = [scope]

    for sc in scopes:
        title, cols, rows = _data_for(sc, met, f)
        sheet = wb.create_sheet(title[:31])
        sheet.append(cols)
        for r in rows:
            sheet.append(r)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_pdf(filters: dict, scope: str = "trips") -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    met = m.Metrics()
    f = m.parse_filters(filters)

    if scope == "all":
        scopes = ["today", "routes", "vehicles", "drivers", "trips"]
    else:
        scopes = [scope]

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # Sarlavha sahifasi
        fig, ax = plt.subplots(figsize=(11.7, 8.3))
        ax.axis("off")
        ax.set_title("BM Automation — Dashboard hisoboti", fontsize=18)
        info = [f"{k}: {v}" for k, v in _kv(f)]
        ax.text(0.05, 0.9, "\n".join(info), fontsize=11, va="top")
        pdf.savefig(fig)
        plt.close(fig)

        for sc in scopes:
            title, cols, rows = _data_for(sc, met, f)
            fig, ax = plt.subplots(figsize=(11.7, 8.3))
            ax.axis("off")
            ax.set_title(title, fontsize=15, pad=20)
            if rows:
                table = ax.table(
                    cellText=rows[:50], colLabels=cols, loc="center",
                    cellLoc="left", colLoc="left")
                table.auto_set_font_size(False)
                table.set_fontsize(8)
                table.scale(1, 1.4)
            pdf.savefig(fig)
            plt.close(fig)

    return buf.getvalue()


def build_export(filters: dict, format: str, scope: str = "trips") -> bytes:
    """Umumiy eksport: csv | xlsx | pdf."""
    fmt = (format or "csv").lower()
    if fmt == "csv":
        return export_csv(filters, scope)
    if fmt == "xlsx":
        return export_xlsx(filters, scope)
    if fmt == "pdf":
        return export_pdf(filters, scope)
    raise ValueError(f"format: csv | xlsx | pdf (berildi: {format})")


_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def content_type(fmt: str) -> str:
    return _CONTENT_TYPES.get((fmt or "csv").lower(), "application/octet-stream")


def filename(fmt: str, scope: str, date_str: str) -> str:
    stamp = date_str.replace("-", "") or date.today().strftime("%Y%m%d")
    return f"dashboard_{scope}_{stamp}.{ (fmt or 'csv').lower()}"
