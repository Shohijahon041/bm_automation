"""Ma'muriy bo'lim — oylik ish haqi, avans va har-yo'nalish bo'yicha Excel.

Dashboard'ning Autodastur" admin bo'limi uchun:
  - `salary_summary(year, month)` — oylik daromad jadvali (har yo'nalish bo'yicha),
    avans bilan kamaytirilgan "qo'liga" summasi bilan.
  - `avans_add / avans_remove / avans_data` — avans to'lovlarini boshqarish.
  - `salary_workbook(route_id, year, month)` — bitta yo'nalish uchun alohida
    XLSX (ish haqi + shu yo'nalish avanslari).
  - `salary_all_workbook(year, month)` — barcha yo'nalishlar uchun bir fayl,
    har yo'nalish alohida ism bilan saqlanadi.

Ish haqi `Metrics.drivers()` dan (km × km_narxi — soliq 12% — jarima) hisoblanadi;
"qo'liga" = netto - shu davrdagi avans.
"""

from __future__ import annotations

import io
import re
from datetime import date, timedelta

from .metrics import Metrics
from ..utils.names import short_name

_TAX_LABEL = "soliq (12%)"

# Ma'muriy bo'lim lavozimlari (firma ishchilari).
STAFF_POSITIONS = [
    "Buxgalter",
    "Moyshik",
    "Quvvatlovchi",
    "Dispetcher",
    "Mexanik",
    "BD",
    "Shifokor",
    "Ish boshqaruvchi",
]


def _month_range(year: int, month: int) -> tuple[str, str]:
    """YYYY-MM oyi uchun (from_date, to_date) qaytaradi."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _month_str(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _workdays(year: int, month: int) -> int:
    """Oy ichidagi ish kunlari soni (dushanba-juma)."""
    d = date(year, month, 1)
    days = 0
    while d.month == month:
        if d.weekday() < 5:
            days += 1
        d += timedelta(days=1)
    return days


def staff_summary(storage, year: int, month: int) -> dict:
    """Ma'muriy bo'lim xodimlari oylik hisobi.

    - ``oylik``  → oyga belgilangan ``rate``;
    - ``kunbay`` → ``rate * days`` (days kiritilmagan bo'lsa oyning ish kunlari).
    """
    mo = _month_str(year, month)
    wd = _workdays(year, month)
    rows = storage.staff_list()
    out = []
    total = 0.0
    for s in rows:
        stype = str(s.get("salary_type") or "oylik")
        rate = float(s.get("rate") or 0)
        days = int(s.get("days") or 0)
        if stype == "kunbay":
            days = days or wd
            amount = rate * days
        else:
            days = 0
            amount = rate
        amount = round(amount, 2)
        total += amount
        out.append({
            "id": s.get("id"),
            "name": str(s.get("name") or ""),
            "position": str(s.get("position") or ""),
            "company": str(s.get("company") or ""),
            "salary_type": stype,
            "rate": round(rate, 2),
            "days": days,
            "amount": amount,
            "note": str(s.get("note") or ""),
        })
    out.sort(key=lambda x: (x["company"], x["position"], x["name"]))
    return {"month": mo, "workdays": wd, "staff": out,
            "total": round(total, 2)}


def _drivers(year: int, month: int, route_id: str = "") -> list[dict]:
    frm, to = _month_range(year, month)
    f = {"from": frm, "to": to, "date": frm}
    all_drivers = Metrics().drivers(f)
    if not route_id:
        return all_drivers
    return [d for d in all_drivers
            if str(d.get("route_id") or "") == str(route_id)]


def salary_summary(year: int, month: int) -> dict:
    """Oylik daromad jamlanmasi — har yo'nalish alohida saqlanadi."""
    from ..db.storage import get_storage

    storage = get_storage()
    mo = _month_str(year, month)
    frm, to = _month_range(year, month)

    drivers = _drivers(year, month)
    avans_map = storage.avans_by_driver(mo)

    by_route: dict[str, dict] = {}
    for d in drivers:
        rid = d.get("route_id") or "nomalum"
        rgrp = by_route.setdefault(rid, {
            "route_id": rid,
            "route_name": d.get("route_name") or d.get("company") or rid,
            "drivers": [],
            "gross": 0.0, "tax": 0.0, "fines": 0.0,
            "net": 0.0, "avans": 0.0, "payable": 0.0,
            "km": 0.0, "count": 0,
        })
        did = str(d.get("driver_id") or "")
        avans = avans_map.get(did, 0.0)
        usable = {
            **d,
            "avans": round(avans, 2),
            "payable": round(max((d.get("net_pay") or 0) - avans, 0), 2),
        }
        rgrp["drivers"].append(usable)
        rgrp["gross"] += usable.get("gross_pay") or 0
        rgrp["tax"] += usable.get("tax") or 0
        rgrp["fines"] += usable.get("fines") or 0
        rgrp["net"] += usable.get("net_pay") or 0
        rgrp["avans"] += avail if (avail := avans) else 0
        rgrp["payable"] += usable["payable"]
        rgrp["km"] += usable.get("km") or 0
        rgrp["count"] += 1

    tot_gross = sum(r["gross"] for r in by_route.values())
    tot_tax = sum(r["tax"] for r in by_route.values())
    tot_fines = sum(r["fines"] for r in by_route.values())
    tot_net = sum(r["net"] for r in by_route.values())
    tot_avans = sum(r["avans"] for r in by_route.values())
    tot_payable = sum(r["payable"] for r in by_route.values())

    staff = staff_summary(storage, year, month)
    tot_staff = staff["total"]
    grand = tot_payable + tot_staff

    return {
        "month": mo,
        "from": frm,
        "to": to,
        "workdays": staff["workdays"],
        "routes": sorted(by_route.values(),
                         key=lambda r: (r["route_name"] or "").lower()),
        "staff": staff["staff"],
        "total": {
            "gross": round(tot_gross, 2),
            "tax": round(tot_tax, 2),
            "fines": round(tot_fines, 2),
            "net": round(tot_net, 2),
            "avans": round(tot_avans, 2),
            "payable": round(tot_payable, 2),
            "staff": round(tot_staff, 2),
            "grand": round(grand, 2),
        },
    }


def _resolve_driver_name(storage, driver_id: str) -> str:
    """driver_id bo'yicha haydovchi full_name'ini qisqa shaklda topadi."""
    if not driver_id:
        return ""
    try:
        d = storage.find("drivers", external_id=str(driver_id)) or {}
        return short_name(str(d.get("full_name") or ""))
    except Exception:  # noqa: BLE001 - nom ixtiyoriy
        return ""


def _resolve_route_name(storage, route_id: str) -> str:
    """route_id bo'yicha yo'nalish nomini topadi."""
    if not route_id:
        return ""
    for src in (lambda: (storage.find("routes", external_id=str(route_id)) or {}).get("name", ""),
                lambda: Metrics()._route_names().get(str(route_id), "")):
        try:
            val = str(src() or "").strip()
        except Exception:  # noqa: BLE001 - nom ixtiyoriy
            val = ""
        if val:
            return val
    return ""


def avans_add(storage, driver_id: str, name: str, amount, pay_date: str,
              route_id: str = "", route_name: str = "", note: str = "") -> dict:
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Avans summasi noto'g'ri"}
    if amt <= 0:
        return {"ok": False, "error": "Avans summasi noldan katta bo'lishi kerak"}
    if not driver_id:
        return {"ok": False, "error": "Haydovchi tanlanmagan"}
    if not pay_date:
        from ..db.models import now_utc
        pay_date = str(now_utc())[:10]
    if not str(name or "").strip():
        name = _resolve_driver_name(storage, driver_id)
    if not str(route_name or "").strip():
        route_name = _resolve_route_name(storage, route_id)
    avans_id = storage.record_avans(
        driver_id=driver_id, name=name, route_id=route_id,
        route_name=route_name, amount=amt, pay_date=pay_date, note=note)
    if not avans_id:
        return {"ok": False, "error": "Avans saqlanmadi (DB o'chiq bo'lishi mumkin)"}
    return {"ok": True, "id": avans_id, "amount": amt, "pay_date": pay_date}


def avans_remove(storage, row_id: int) -> dict:
    if storage.delete_avans(row_id):
        return {"ok": True, "id": row_id}
    return {"ok": False, "error": "Avans o'chirilmadi"}


def avans_data(storage, month: str = "", driver_id: str = "",
               route_id: str = "") -> dict:
    rows = storage.avans_list(driver_id=driver_id, route_id=route_id,
                              month=month)
    for r in rows:
        nm = str(r.get("name") or "").strip()
        rn = str(r.get("route_name") or "").strip()
        if not nm:
            nm = _resolve_driver_name(storage, r.get("driver_id"))
            if nm:
                r["name"] = nm
        if not rn:
            rn = _resolve_route_name(storage, r.get("route_id"))
            if rn:
                r["route_name"] = rn
    total = storage.avans_total(driver_id=driver_id, route_id=route_id,
                                month=month)
    return {"rows": rows, "count": len(rows), "total": round(total, 2)}


def salary_workbook(route_id: str, year: int, month: int) -> bytes:
    """Bitta yo'nalish uchun alohida oylik ish haqi XLSX."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    storage = work_storage()
    mo = _month_str(year, month)
    frm, to = _month_range(year, month)
    drivers = _drivers(year, month, route_id)
    avans_map = storage.avans_by_driver(mo)

    wb = Workbook()
    ws = wb.active
    ws.title = "Ish haqi"
    head = Font(bold=True, size=14)
    sub = Font(bold=True, size=11, color="666666")
    fill = PatternFill("solid", fgColor="E8F0FE")

    route_name = drivers[0]["route_name"] if drivers else route_id
    ws.append([f"💰 ISH HAQI — {route_name}"])
    ws.append([f"🛣 Yo'nalish: {route_name or route_id}"])
    ws.append([f"📅 Davr: {frm} — {to}"])
    ws.append([f"👤 Haydovchilar: {len(drivers)}"])

    cols = ["driver_id", "Ism", "Reyslar", "Km", "Km narxi", "Brutto",
            _TAX_LABEL, "Jarima", "Netto", "Avans", "Qo'liga (to'lanadi)"]
    ws.append([])
    header_row = ws.max_row + 1
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=header_row, column=c)
        cell.font = Font(bold=True)
        cell.fill = fill

    for d in sorted(drivers, key=lambda x: (-x.get("trips", 0),
                                            x.get("name", ""))):
        did = str(d.get("driver_id") or "")
        avans = avans_map.get(did, 0.0)
        net = d.get("net_pay") or 0
        payable = max(net - avans, 0)
        ws.append([
            did, d.get("name", ""), d.get("trips", 0),
            round(d.get("km", 0), 2), round(d.get("km_rate", 0), 2),
            round(d.get("gross_pay", 0), 2), round(d.get("tax", 0), 2),
            round(d.get("fines", 0), 2), round(net, 2),
            round(avans, 2), round(payable, 2),
        ])

    ws.append([])
    tg = sum(d.get("gross_pay") or 0 for d in drivers)
    tt = sum(d.get("tax") or 0 for d in drivers)
    tf = sum(d.get("fines") or 0 for d in drivers)
    tn = sum(d.get("net_pay") or 0 for d in drivers)
    ta = sum(avans_map.get(str(d.get("driver_id") or ""), 0) for d in drivers)
    tp = max(tn - ta, 0)
    ws.append(["", "JAMI", "", "", "", round(tg, 2), round(tt, 2),
               round(tf, 2), round(tn, 2), round(ta, 2), round(tp, 2)])

    for col in ws.columns:
        m = 0
        letter = col[0].column_letter
        for cell in col:
            if cell.value is not None:
                m = max(m, len(str(cell.value)))
        ws.column_dimensions[letter].width = min(m + 3, 32)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def staff_workbook(company: str, year: int, month: int) -> bytes:
    """Bitta firma uchun ma'muriy bo'lim xodimlari oylik XLSX."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    storage = work_storage()
    mo = _month_str(year, month)
    frm, to = _month_range(year, month)
    data = staff_summary(storage, year, month)
    rows = [s for s in data["staff"]
            if not company or s["company"] == company]
    if not rows:
        rows = data["staff"] if not company and data["staff"] else rows

    wb = Workbook()
    ws = wb.active
    ws.title = "Xodimlar"
    fill = PatternFill("solid", fgColor="E8F0FE")

    title = f"Ma'muriy bo'lim" + (f" — {company}" if company else "")
    ws.append([f"🏢 {title}"])
    ws.append([f"📅 Davr: {frm} — {to}"])
    ws.append([f"👥 Xodimlar: {len(rows)}"])

    cols = ["№", "Ismi", "Lavozim", "Firma", "To'lov turi", "Stavka",
            "Kunlar", "Oy summasi", "Izoh"]
    ws.append([])
    header_row = ws.max_row + 1
    ws.append(cols)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=header_row, column=c)
        cell.font = Font(bold=True)
        cell.fill = fill

    for i, s in enumerate(rows, 1):
        ws.append([
            i, s["name"], s["position"], s["company"],
            "Oylik" if s["salary_type"] == "oylik" else "Kunbay",
            round(s["rate"], 2),
            s["days"] if s["salary_type"] == "kunbay" else "",
            round(s["amount"], 2), s["note"],
        ])

    ws.append([])
    ws.append(["", "", "", "", "", "", "", round(data["total"], 2), ""])

    for col in ws.columns:
        m = 0
        letter = col[0].column_letter
        for cell in col:
            if cell.value is not None:
                m = max(m, len(str(cell.value)))
        ws.column_dimensions[letter].width = min(m + 3, 32)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def salary_all_workbook(year: int, month: int) -> dict:
    """Barcha yo'nalishlar + firmalar xodimlari uchun alohida XLSX'lar.

    Qaytaradi: {"files": [{"route_id", "route_name", "filename", "data"}]}
    """
    storage = work_storage()
    drivers = _drivers(year, month)
    route_ids = sorted({str(d.get("route_id") or "nomalum")
                        for d in drivers})
    mo = _month_str(year, month)
    files = []
    for rid in route_ids:
        if rid == "nomalum":
            continue
        data = salary_workbook(rid, year, month)
        rn = next((d.get("route_name") for d in drivers
                   if str(d.get("route_id")) == rid), rid)
        safe = re.sub(r"[^\w\-]+", "_", str(rn)) or "yo'nalish"
        filename = f"ish_haqi_{safe}_{mo}.xlsx"
        files.append({"route_id": rid, "route_name": rn,
                      "filename": filename, "data": data})
    staff_data = staff_summary(storage, year, month)
    companies = sorted({s["company"] for s in staff_data["staff"] if s["company"]})
    for cc in companies:
        data = staff_workbook(cc, year, month)
        safe = re.sub(r"[^\w\-]+", "_", str(cc)) or "firma"
        filename = f"xodimlar_{safe}_{mo}.xlsx"
        files.append({"route_id": "", "route_name": cc,
                      "filename": filename, "data": data})
    return {"month": mo, "files": files}


def work_storage():
    from ..db.storage import get_storage
    return get_storage()
