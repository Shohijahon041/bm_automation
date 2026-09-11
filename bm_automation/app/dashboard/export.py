"""Dashboard eksporti — CSV, Excel (xlsx) va PDF.

`build_export` umumiy kirish nuqtasi: `scope` bo'yicha ma'lumotni
`metrics.Metrics` dan olib, so'ralgan formatda bytes qaytaradi.

PDF `matplotlib` (Agg) orqali jadval ko'rinishida yaratiladi.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any

from . import metrics as m


def _kv(f: dict) -> list:
    return [("sana", f.get("date") or f.get("from", "")),
            ("route", f.get("route", "") or "barchasi"),
            ("vehicle", f.get("vehicle", "") or "barchasi"),
            ("driver", f.get("driver", "") or "barchasi"),
            ("status", f.get("status", "") or "barchasi")]


def _expand_period(met: m.Metrics, f: dict) -> dict:
    """`month`/`year` filterini aniq davrga aylantiradi va f dagi
    `date`/`from`/`to` ni shu davrga to'g'rilaydi.

    `parse_filters` `month` ni saqlaydi, lekin `date`/`from`/`to` ni
    bugungi kunga qo'yadi. Bu oylik eksportda "_kv" sarlavhasining noto'g'ri
    (bir kunlik) ko'rinishiga sabab bo'ladi — shu yerda tuzatiladi va
    boshqa scopes ham to'g'ri davrga ishlaydi.
    """
    f = dict(f)
    if f.get("month") or f.get("year"):
        frm, to = met._month_bounds(f)
        f["from"], f["to"] = frm, to
        f["date"] = ""
    return f


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
        cols = ["driver_id", "ism", "reyslar", "ish kunlari", "km", "km narxi",
                "brutto", "soliq (12%)", "jarima", "netto (qo'liga)", "rating",
                "qora ro'yxat", "davomat %"]
        rows = [[d["driver_id"], d["name"], d["trips"], d["working_days"],
                 d["km"], d["km_rate"], d["gross_pay"], d["tax"], d["fines"],
                 d["net_pay"], d["rating"], "ha" if d["blacklisted"] else "yo'q",
                 d["attendance"]]
                for d in met.drivers(f)]
        return "Haydovchilar", cols, rows

    if scope == "distance":
        d = met.distance(f)
        cols = ["avtobus", "davlat raqami", "garaj", "model", "yo'nalish",
                "ish kunlari", "reja km", "fakt km", "qo'shimcha km",
                "reja reys", "fakt reys", "qabul reys"]
        rows = [[v["vehicle_id"], v["plate_number"], v.get("garage_number") or "",
                 v["model"], v["route_name"], v["days"], v["distance_plan"],
                 v["distance_fact"], v["distance_fact_extra"], v["trip_plan"],
                 v["trip_fact"], v["trip_approved"]]
                for v in d["rows"]]
        t = d["totals"]
        rows.append(["JAMI", "", "", "", "", "", t["distance_plan"],
                     t["distance_fact"], t["distance_fact_extra"], t["trip_plan"],
                     t["trip_fact"], t["trip_approved"]])
        return "Masofa (km)", cols, rows

    if scope == "schedule":
        s = met.schedule(f)
        rows = []
        for rt in s["routes"]:
            for it in rt["items"]:
                rows.append([rt["route_name"], rt.get("company") or "",
                             it["shift"], it["start"], it["driver"],
                             it["vehicle"], it["trip_count"]])
        cols = ["yo'nalish", "firma", "smena", "boshlash", "haydovchi",
                "avtobus", "reys soni"]
        return f"Jadval ({s['date']})", cols, rows

    if scope == "attendance":
        a = met.attendance(f)
        rows = [[d["driver_id"], d["name"],
                 "kelmadi" if d["present"] else "ha"]
                for d in a["roster"]]
        cols = ["driver_id", "ism", "kelmadi"]
        return f"Davomat ({a['date']})", cols, rows

    if scope == "electricity":
        e = met.electricity_report(f)
        cols = ["firma", "yo'nalish", "haydovchi", "km", "kVt/soat",
                "1 kVt narxi", "summa (so'm)"]
        rows = []

        def _firm(firma: str) -> str:
            return firma or "—"

        # Firmalar bo'yicha guruhlab ajratamiz: har bir firma uchun
        # yo'nalishlar va haydovchilar, so'ng firma jami.
        firms: dict[str, dict] = {}
        for rt in e["routes"]:
            key = rt["company"] or "—"
            g = firms.setdefault(key, {"routes": [], "drivers": [], "km": 0.0,
                                       "kwh": 0.0})
            g["routes"].append(rt)
            g["km"] += rt["km"]
            g["kwh"] += rt["kwh"]
        for d in e["drivers"]:
            key = d["company"] or "—"
            g = firms.setdefault(key, {"routes": [], "drivers": [], "km": 0.0,
                                       "kwh": 0.0})
            g["drivers"].append(d)
            g["km"] += d["km"]
            g["kwh"] += d["kwh"]

        for firm, g in firms.items():
            rows.append(["=== " + (firm or "—") + " ===", "", "", "", "", "", ""])
            for rt in g["routes"]:
                rows.append([_firm(firm), rt["name"], "", rt["km"], rt["kwh"],
                             rt["rate"], rt["total"]])
            for d in g["drivers"]:
                rows.append([_firm(firm), d["route_name"], d["name"], d["km"],
                             d["kwh"], d["rate"], d["total"]])
            firm_total = round(sum(r["total"] for r in g["routes"]) +
                               sum(r["total"] for r in g["drivers"]), 2)
            rows.append(["", "FIRMA JAMI", "", round(g["km"], 2),
                         round(g["kwh"], 2), e["rate"], firm_total])
        t = e["totals"]
        rows.append(["", "UMUMIY JAMI", "", t["km"], t["kwh"], e["rate"],
                     t["total"]])
        return f"Elektr energiya ({e['period'].get('from','')} → {e['period'].get('to','')})", cols, rows

    if scope == "rating":
        f2 = {**f, "scope": "month"}
        drivers = met.drivers(f2)
        drivers.sort(key=lambda d: d.get("net_pay") or 0, reverse=True)
        cols = ["driver_id", "ism", "reyslar", "km", "davomat %", "netto (so'm)"]
        rows = [[d["driver_id"], d["name"], d["trips"], d["km"],
                 d["attendance"], d["net_pay"]] for d in drivers]
        return "Haydovchilar reytingi", cols, rows

    # trips (default)
    cols = ["id", "date", "route_id", "vehicle_id", "driver_id",
            "planned_time", "actual_time", "status", "source"]
    rows = [[t.get("id"), t.get("date"), t.get("route_id"), t.get("vehicle_id"),
             t.get("driver_id"), t.get("planned_time"), t.get("actual_time"),
             t.get("status"), t.get("source")] for t in met.trips(f)]
    return "Reyslar", cols, rows


def export_csv(filters: dict, scope: str = "trips") -> bytes:
    met = m.Metrics()
    f = _expand_period(met, m.parse_filters(filters))
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
    f = _expand_period(met, m.parse_filters(filters))
    wb = Workbook()
    ws = wb.active
    ws.title = "Xulosa"
    ws.append(["BM Automation — hisobot"])
    for k, v in _kv(f):
        ws.append([k, v])

    if scope == "all":
        scopes = ["today", "routes", "vehicles", "drivers", "trips",
                  "distance", "schedule", "attendance", "rating",
                  "electricity"]
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


def export_xls(filters: dict, scope: str = "trips") -> bytes:
    """Excel 97-2003 (.xls) — xlwt orqali."""
    import xlwt

    met = m.Metrics()
    f = _expand_period(met, m.parse_filters(filters))
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Xulosa")
    ws.write(0, 0, "BM Automation — hisobot")
    row = 1
    for k, v in _kv(f):
        ws.write(row, 0, k)
        ws.write(row, 1, v)
        row += 1

    if scope == "all":
        scopes = ["today", "routes", "vehicles", "drivers", "trips",
                  "distance", "schedule", "attendance", "rating",
                  "electricity"]
    else:
        scopes = [scope]

    for sc in scopes:
        title, cols, rows = _data_for(sc, met, f)
        sheet = wb.add_sheet(title[:31])
        for ci, c in enumerate(cols):
            sheet.write(0, ci, c)
        for ri, r in enumerate(rows, start=1):
            for ci, v in enumerate(r):
                sheet.write(ri, ci, v)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_pdf(filters: dict, scope: str = "trips") -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    met = m.Metrics()
    f = _expand_period(met, m.parse_filters(filters))

    if scope == "all":
        scopes = ["today", "routes", "vehicles", "drivers", "trips",
                  "distance", "schedule", "attendance", "rating"]
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
            shown = len(rows)
            ax.set_title(title, fontsize=15, pad=20)
            if rows:
                if shown > 50:
                    shown = 50
                    ax.text(0.5, -0.02, f"Ko'rsatilmoqda: {shown} / {len(rows)} qator",
                            ha="center", fontsize=9, color="gray",
                            transform=ax.transAxes)
                table = ax.table(
                    cellText=rows[:shown], colLabels=cols, loc="center",
                    cellLoc="left", colLoc="left")
                table.auto_set_font_size(False)
                table.set_fontsize(8)
                table.scale(1, 1.4)
            pdf.savefig(fig)
            plt.close(fig)

    return buf.getvalue()


def build_export(filters: dict, format: str, scope: str = "trips") -> bytes:
    """Umumiy eksport: csv | xls | xlsx | pdf."""
    fmt = (format or "csv").lower()
    if fmt == "csv":
        return export_csv(filters, scope)
    if fmt == "xls":
        return export_xls(filters, scope)
    if fmt == "xlsx":
        return export_xlsx(filters, scope)
    if fmt == "pdf":
        return export_pdf(filters, scope)
    raise ValueError(f"format: csv | xls | xlsx | pdf (berildi: {format})")


_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def content_type(fmt: str) -> str:
    return _CONTENT_TYPES.get((fmt or "csv").lower(), "application/octet-stream")


def filename(fmt: str, scope: str, date_str: str) -> str:
    stamp = re.sub(r"[^0-9]", "", date_str or "")[:8] or date.today().strftime("%Y%m%d")
    name = f"dashboard_{scope}_{stamp}.{(fmt or 'csv').lower()}"
    return re.sub(r'[^\w.\-]+', "_", name)


def salary_filename(from_date: str, to_date: str) -> str:
    f = re.sub(r"[^0-9]", "", from_date)[:8]
    t = re.sub(r"[^0-9]", "", to_date)[:8]
    return f"oylik_{f}_{t}.xlsx"


def salary_export(from_date: str, to_date: str) -> bytes:
    """Barcha yo'nalishlar bo'yicha haydovchilar oylik hisoboti (XLSX).

    Har bir yo'nalish alohida sahifada — kompaniya nomi + yo'nalish nomi
    sarlavha sifatida, so'ng haydovchilar jadvali.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    met = m.Metrics()
    f = {"from": from_date, "to": to_date, "date": from_date}
    drivers = met.drivers(f)

    # Yo'nalishlar bo'yicha guruhlash
    by_route: dict[str, list[dict]] = {}
    for d in drivers:
        rid = d.get("route_id") or "nomalum"
        by_route.setdefault(rid, []).append(d)

    companies = met._companies()
    rnames = met._route_names()

    wb = Workbook()

    # --- Xulosa sahifasi ---
    ws = wb.active
    ws.title = "Xulosa"
    header_font = Font(bold=True, size=14)
    sub_font = Font(bold=True, size=11, color="666666")
    ws.append(["💰 OYLIK HISOBOT"])
    ws.append([f"📅 Davr: {from_date} — {to_date}"])
    ws.append([f"📊 Jami haydovchilar: {len(drivers)}"])
    total_km = round(sum(d.get("km", 0) for d in drivers), 2)
    total_gross = round(sum(d.get("gross_pay", 0) for d in drivers), 2)
    total_tax = round(sum(d.get("tax", 0) for d in drivers), 2)
    total_fines = round(sum(d.get("fines", 0) for d in drivers), 2)
    total_net = round(sum(d.get("net_pay", 0) for d in drivers), 2)
    ws.append([f"📏 Jami km: {total_km:,.2f}"])
    ws.append([f"💵 Jami brutto: {total_gross:,.0f} so'm"])
    ws.append([f"🏦 Jami soliq: {total_tax:,.0f} so'm"])
    ws.append([f"⚠️ Jami jarimalar: {total_fines:,.0f} so'm"])
    ws.append([f"💰 Jami netto: {total_net:,.0f} so'm"])

    # --- Har bir yo'nalish uchun sahifa ---
    cols = ["driver_id", "Ism", "Reyslar", "Ish kunlari", "Km", "Km narxi",
            "Brutto", "Soliq (12%)", "Jarima", "Netto", "Reyting", "Davomat %"]

    for rid, drv_list in sorted(by_route.items()):
        comp = companies.get(rid, {})
        comp_name = comp.get("company", "") or "Noma'lum"
        route_name = rnames.get(rid, "") or comp.get("route_name", "") or rid
        sheet_name = f"{comp_name} — {route_name}"[:31]
        ws = wb.create_sheet(sheet_name)

        # Sarlavha
        ws.append([f"🏢 {comp_name}"])
        ws.append([f"🛣 {route_name}"])
        ws.append([f"📅 {from_date} — {to_date}"])
        ws.append([])

        # Jadvallar
        ws.append(cols)
        for d in sorted(drv_list, key=lambda x: (-x.get("trips", 0), x.get("name", ""))):
            ws.append([
                d.get("driver_id", ""),
                d.get("name", ""),
                d.get("trips", 0),
                d.get("working_days", 0),
                round(d.get("km", 0), 2),
                round(d.get("km_rate", 0), 2),
                round(d.get("gross_pay", 0), 2),
                round(d.get("tax", 0), 2),
                round(d.get("fines", 0), 2),
                round(d.get("net_pay", 0), 2),
                round(d.get("rating", 0), 1),
                round(d.get("attendance", 0), 1),
            ])

        # Yo'nalish yig'indisi
        ws.append([])
        route_km = round(sum(d.get("km", 0) for d in drv_list), 2)
        route_gross = round(sum(d.get("gross_pay", 0) for d in drv_list), 2)
        route_tax = round(sum(d.get("tax", 0) for d in drv_list), 2)
        route_fines = round(sum(d.get("fines", 0) for d in drv_list), 2)
        route_net = round(sum(d.get("net_pay", 0) for d in drv_list), 2)
        ws.append(["", "JAMI", "", "",
                    route_km, "", route_gross, route_tax, route_fines, route_net,
                    "", ""])

        # Ustun kengliklarini avtomatik sozlash
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_len + 3, 30)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
