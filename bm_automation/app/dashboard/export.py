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


def _n(v) -> float:
    """Raqamni 2 kasrga yaxlitlaydi (None/noto'g'ri uchun 0)."""
    try:
        return round(float(v or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _pc(v) -> float:
    """Ulushni (0..1) foizga aylantiradi va yaxlitlaydi."""
    try:
        return round(float(v or 0.0) * 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


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
    if scope == "settings":
        from ..core.bot_settings import audit_log
        rows = []
        for r in audit_log(500):
            rows.append([r.get("when", ""), r.get("key", ""),
                         r.get("old", ""), r.get("new", ""),
                         r.get("by") or "dashboard"])
        cols = ["vaqt", "kalit", "eski qiymat", "yangi qiymat", "o'zgartiruvchi"]
        return "Sozlamalar tarixi (audit)", cols, rows

    if scope == "tariffs":
        routes = m.route_options()
        cols = ["firma", "yo'nalish", "route_id", "skm (so'm/km)",
                "1 km narxi", "tarif QQSsiz", "tarif QQS bilan"]
        rows = [[r.get("company") or "", r.get("name") or "",
                 r.get("id") or "", r.get("skm") or 0,
                 r.get("km_rate") or 0, r.get("tariff_no_vat") or 0,
                 r.get("tariff_vat") or 0] for r in routes]
        return "Firma tariflari", cols, rows

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

    if scope == "tabel":
        from datetime import date as _d
        d_date = f.get("date") or ""
        mth = f.get("month") or (f.get("from") or "")[:7] or _d.today().strftime("%Y-%m")
        if d_date and len(str(d_date)) >= 10:
            return _tabel_day(str(d_date)[:10], route=f.get("route", ""))
        return _tabel_data(mth, route=f.get("route", ""))

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

    if scope == "rejects":
        rep = met.not_accepted_km_report(f)
        cols = ["№", "Haydovchi (F.I.Sh.)", "Ish kuni", "Jami reys",
                "Qabul qilinmagan reys", "Amalda bajarilgan reyslar",
                "Rejadagi km", "Amalda km", "Farq (reja - amalda)",
                "Qabul qilinmagan so'm (QQSsiz)", "Qabul qilinmagan so'm (QQS bilan)"]
        rows = [[i, r["name"], r["days"], r["plan_reys"],
                 r["qabul_qilinmagan"], r["fact_reys"], r["plan_km"],
                 r["fact_km"], r["diff"], r["sum_no_vat"], r["sum_vat"]]
                for i, r in enumerate(rep["rows"], 1)]
        t = rep["totals"]
        rows.append(["JAMI", "", t["days"], t["plan_reys"],
                     t["qabul_qilinmagan"], t["fact_reys"], t["plan_km"],
                     t["fact_km"], t["diff"], t["sum_no_vat"], t["sum_vat"]])
        return "Qabul qilinmagan KM reys", cols, rows

    if scope == "brutto":
        br = m.brutto(f)
        pcare = br.get("period_label") or ""
        cols = ["№", "Haydovchi (F.I.Sh.)", "ID", "Grafik",
                "Lr (reja km)", "Lf (amalda km)", "SKM (so'm/km)",
                "Kamal (reys)", "Kstjb (muammoli)", "Kmaq",
                "Alfa a (%)", "Beta b (%)", "Gamma g (%)", "Sifat indeksi (%)",
                "Brutto 100 (so'm)", "To'lov (so'm)", "Jarima (so'm)",
                "Haydovchi ish haqi", "Soliq (12%)", "Qo'lga (so'm)",
                "Elektr kVt", "Elektr summasi (so'm)"]
        rows = [[i + 1, r.get("fio") or "", r.get("driver_id") or "",
                 r.get("grafik") or "",
                 _n(r.get("lr")), _n(r.get("lf")),
                 _n(r.get("skm") or br.get("skm")),
                 r.get("kamal") or 0, r.get("kstjb") or 0, r.get("kmaq") or 0,
                 _pc(r.get("alpha")), _pc(r.get("beta")),
                 _pc(r.get("gamma")), _pc(r.get("sifat_index")),
                 _n(r.get("brutto_100")), _n(r.get("tolov")),
                 _n(r.get("jarima")), _n(r.get("haydovchi_ish_haqi")),
                 _n(r.get("haydovchi_soliq")), _n(r.get("haydovchi_qolga")),
                 _n(r.get("elektr_kwt")), _n(r.get("elektr_summ"))]
                for i, r in enumerate(br.get("rows") or [])]
        a = br.get("agg") or {}
        if rows:
            rows.append(["JAMI", f"{len(rows)} haydovchi", "", "",
                         _n(a.get("lr")), _n(a.get("lf")),
                         _n(br.get("skm")),
                         a.get("kamal") or 0, a.get("kstjb") or 0,
                         a.get("kmaq") or 0,
                         _pc(a.get("alpha")), _pc(a.get("beta")),
                         _pc(a.get("gamma")), "",
                         _n(a.get("brutto_100")), _n(a.get("tolov")),
                         _n(a.get("jarima")), _n(a.get("haydovchi_ish_haqi")),
                         _n(a.get("haydovchi_soliq")),
                         _n(a.get("haydovchi_qolga")),
                         _n(a.get("elektr_kwt")), _n(a.get("elektr_summ"))])
        label = f" ({pcare})" if pcare else ""
        return f"Brutto-shartnoma to'lovi{label}", cols, rows

    # trips (default)
    cols = ["id", "date", "route_id", "vehicle_id", "driver_id",
            "planned_time", "actual_time", "status", "source"]
    rows = [[t.get("id"), t.get("date"), t.get("route_id"), t.get("vehicle_id"),
             t.get("driver_id"), t.get("planned_time"), t.get("actual_time"),
             t.get("status"), t.get("source")] for t in met.trips(f)]
    return "Reyslar", cols, rows


def _tabel_data(month: str, route: str = "") -> tuple[str, list, list[list]]:
    """Oylik ishga chiqish tabeli (kalendar) — haydovchi × kun.

    Sahifadagi Performance jadvali bilan bir xil: har bir haydovchi
    uchun oy kunlari bo'yicha reyslar soni (ACCEPTED/APPROVED).
    """
    from datetime import date as _d
    from calendar import monthrange

    from ..db.storage import get_storage

    storage = get_storage()
    if not storage or not storage.enabled:
        return "Tabel", ["Haydovchi"], [["DB o'chirilgan"]]

    month = (month or _d.today().strftime("%Y-%m"))
    y, mo = int(month[:4]), int(month[5:7])
    last = monthrange(y, mo)[1]
    from_date = month + "-01"
    to_date = f"{y + 1}-01-01" if mo == 12 else f"{y}-{mo + 1:02d}-01"

    rnames: dict[str, str] = {}
    try:
        for r in storage.db.query("SELECT external_id, name FROM routes") or []:
            rnames[str(r["external_id"])] = str(r["name"] or "")
    except Exception:  # noqa: BLE001
        pass

    routes_set = [t.strip() for t in str(route or "").replace(",", " ").split()
                  if t.strip()]
    ph = storage.db.ph
    query = (
        "SELECT d.external_id AS driver_id, d.full_name, d.route_id, "
        "  t.date, COUNT(*) AS trip_count "
        "FROM trips t JOIN drivers d ON d.external_id = t.driver_id "
        f"WHERE t.date >= {ph} AND t.date < {ph} "
        "  AND t.status IN ('ACCEPTED','APPROVED')")
    args: list = [from_date, to_date]
    if routes_set:
        marks = ", ".join([ph] * len(routes_set))
        query += f" AND d.route_id IN ({marks})"
        args += routes_set
    query += " GROUP BY d.external_id, d.full_name, d.route_id, t.date"
    try:
        rows = storage.db.query(query, args) or []
    except Exception:  # noqa: BLE001
        return f"Tabel ({month})", ["Haydovchi"], [["Query xatosi"]]

    drivers_map: dict[str, dict] = {}
    for r in rows:
        did = str(r["driver_id"])
        if did not in drivers_map:
            drivers_map[did] = {
                "full_name": str(r["full_name"] or did),
                "route_id": str(r["route_id"] or ""),
                "days": {},
            }
        drivers_map[did]["days"][str(r["date"])] = int(r["trip_count"] or 0)

    # Qo'lda kirilgan ish kunlari (driver_work_logs, note != 'AVTO') —
    # Performance sahifasi bilan bir xil bo'lishi uchun kunlar qatoriga
    # kiritiladi; trips bor kunda trips miqdori ustun turadi. Qo'lda qayd
    # kiritilgan (driver, sana) juftlari eslab qolinadi — AVTO ularni
    # ustiga yozmaydi.
    manual_keys: set = set()
    try:
        wl_query = (
            "SELECT d.external_id AS driver_id, d.full_name, d.route_id,"
            "  w.date, COALESCE(w.trip_count, 0) AS trip_count"
            " FROM driver_work_logs w"
            " JOIN drivers d ON d.external_id = w.driver_id"
            f" WHERE w.note <> {ph} AND w.driver_id <> ''"
            f" AND w.date >= {ph} AND w.date < {ph} ")
        wl_args: list = ["AVTO", from_date, to_date]
        if routes_set:
            marks = ", ".join([ph] * len(routes_set))
            wl_query += f" AND d.route_id IN ({marks})"
            wl_args += routes_set
        for r in storage.db.query(wl_query, wl_args) or []:
            did = str(r["driver_id"])
            if did not in drivers_map:
                drivers_map[did] = {
                    "full_name": str(r["full_name"] or did),
                    "route_id": str(r["route_id"] or ""),
                    "days": {},
                }
            date_str = str(r["date"])
            manual_keys.add((did, date_str))
            if date_str not in drivers_map[did]["days"]:
                drivers_map[did]["days"][date_str] = int(r["trip_count"] or 0)
    except Exception:  # noqa: BLE001
        pass

    # AVTO (brutto-route / route_daily) ish kunlari — saytdagi rasmiy
    # hisob: faqat ishlangan (working_day) kunlar tabelga kiritiladi.
    # Trip bilan takrorlangan kunda sayt qiymati ustun turadi; qo'lda
    # (dlog) qayd kiritilgan kun o'zgarmaydi.
    try:
        avto_query = (
            "SELECT d.external_id AS driver_id, d.full_name, d.route_id,"
            "  w.date, COALESCE(w.trip_count, 0) AS trip_count"
            " FROM driver_work_logs w"
            " JOIN drivers d ON d.external_id = w.driver_id"
            f" WHERE w.note = {ph} AND w.driver_id <> ''"
            f" AND w.date >= {ph} AND w.date < {ph}"
            f" AND COALESCE(w.working_day, 0) > 0 ")
        avto_args: list = ["AVTO", from_date, to_date]
        if routes_set:
            marks = ", ".join([ph] * len(routes_set))
            avto_query += f" AND d.route_id IN ({marks})"
            avto_args += routes_set
        for r in storage.db.query(avto_query, avto_args) or []:
            did = str(r["driver_id"])
            if did not in drivers_map:
                drivers_map[did] = {
                    "full_name": str(r["full_name"] or did),
                    "route_id": str(r["route_id"] or ""),
                    "days": {},
                }
            date_str = str(r["date"])
            if (did, date_str) not in manual_keys:
                drivers_map[did]["days"][date_str] = int(r["trip_count"] or 0)
    except Exception:  # noqa: BLE001
        pass

    drivers = sorted(drivers_map.values(), key=lambda d: d["full_name"])
    cols = ["№", "Haydovchi", "Yo'nalish"] + [str(dd) for dd in range(1, last + 1)] + ["Ish kuni"]
    out: list[list] = []
    for i, drv in enumerate(drivers, 1):
        rname = rnames.get(drv["route_id"], "")
        r = [i, drv["full_name"], rname]
        total = 0
        for dd in range(1, last + 1):
            date_str = f"{month}-{dd:02d}"
            worked = date_str in drv["days"]
            trips = drv["days"].get(date_str, 0)
            r.append(trips if worked else "")
            total += 1 if worked else 0
        r.append(total)
        out.append(r)

    # JAMI qatori
    jami = ["", "JAMI", ""]
    col_sums = [0] * last
    for drv in drivers:
        for dd in range(1, last + 1):
            date_str = f"{month}-{dd:02d}"
            if date_str in drv["days"]:
                col_sums[dd - 1] += 1
    jami += [s if s else "" for s in col_sums]
    jami.append(len(drivers))
    out.append(jami)

    route_label = ""
    if len(routes_set) == 1:
        route_label = f" — {rnames.get(routes_set[0], routes_set[0])}"
    return f"Tabel ({month}){route_label}", cols, out


def _tabel_day(date_str: str, route: str = "") -> tuple[str, list, list[list]]:
    """Bir kunlik tabel — yo'nalishdagi haydovchilar ro'yhati.

    Ustunlar: haydovchi, yo'nalish, shu kungi reyslar soni, davomat
    (keldi / kelmadi). Haydovchi ro'yxati — yo'nalishga biriktirilganlar
    (reis qilmaganlar ham kiritiladi → davomat aniq ko'rinadi).
    """
    from ..db.storage import get_storage

    storage = get_storage()
    if not storage or not storage.enabled:
        return "Tabel", ["Haydovchi"], [["DB o'chirilgan"]]

    routes_set = [t.strip() for t in str(route or "").replace(",", " ").split()
                  if t.strip()]

    rnames: dict[str, str] = {}
    try:
        for r in storage.db.query("SELECT external_id, name FROM routes") or []:
            rnames[str(r["external_id"])] = str(r["name"] or "")
    except Exception:  # noqa: BLE001
        pass

    # Shu kuni reys qilgan haydovchilar (reja emas — amalda reyslar)
    present: dict[str, int] = {}
    query = (
        "SELECT d.external_id AS driver_id, d.full_name, d.route_id, "
        "  COUNT(*) AS trip_count "
        "FROM trips t JOIN drivers d ON d.external_id = t.driver_id "
        "WHERE t.date = %s AND t.status IN ('ACCEPTED','APPROVED')")
    args: list = [date_str]
    if routes_set:
        marks = ", ".join(["%s"] * len(routes_set))
        query += f" AND d.route_id IN ({marks})"
        args += routes_set
    query += " GROUP BY d.external_id, d.full_name, d.route_id"
    try:
        for r in storage.db.query(query, args) or []:
            present[str(r["driver_id"])] = int(r["trip_count"] or 0)
    except Exception:  # noqa: BLE001
        return f"Tabel ({date_str})", ["Haydovchi"], [["Query xatosi"]]

    # Yo'nalishga biriktirilgan barcha haydovchilar (roster)
    roster: dict[str, dict] = {}
    dquery = "SELECT external_id, full_name, route_id FROM drivers"
    dargs: list = []
    if routes_set:
        marks = ", ".join(["%s"] * len(routes_set))
        dquery += f" WHERE route_id IN ({marks})"
        dargs = list(routes_set)
    try:
        for r in storage.db.query(dquery, dargs) or []:
            roster[str(r["external_id"])] = {
                "full_name": str(r["full_name"] or r["external_id"]),
                "route_id": str(r["route_id"] or ""),
            }
    except Exception:  # noqa: BLE001
        pass
    # Reys qilgan, lekin ro'yxatda yo'q haydovchilar ham qo'shiladi
    for did in present:
        roster.setdefault(did, {
            "full_name": did,
            "route_id": "",
        })
    present_ids = set(present)

    cols = ["№", "Haydovchi", "Yo'nalish", "Reyslar", "Davomat"]
    out: list[list] = []
    total_trips = 0
    keldi = 0
    for i, (did, info) in enumerate(
            sorted(roster.items(), key=lambda kv: kv[1]["full_name"]), 1):
        trips = present.get(did, 0)
        total_trips += trips
        state = "Keldi" if trips else "Kelmadi"
        if trips:
            keldi += 1
        out.append([i, info["full_name"],
                    rnames.get(info["route_id"], ""),
                    trips if trips else "", state])
    out.append(["", "JAMI", "", total_trips if total_trips else "",
                f"{keldi}/{len(roster)}"])

    route_label = ""
    if len(routes_set) == 1:
        route_label = f" — {rnames.get(routes_set[0], routes_set[0])}"
    return f"Tabel ({date_str}){route_label}", cols, out


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
                  "electricity", "rejects", "tabel"]
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
                  "electricity", "rejects", "tabel"]
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


def salary_export(from_date: str, to_date: str, route: str = "") -> bytes:
    """Haydovchilar oylik hisoboti (XLSX).

    Har bir yo'nalish alohida sahifada — kompaniya nomi + yo'nalish nomi
    sarlavha sifatida, so'ng haydovchilar jadvali. `route` berilsa (bo'sh
    joy yoki vergul bilan ajratilgan id'lar) hisobot faqat shu yo'nalishlar
    bo'yicha tuziladi.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    met = m.Metrics()
    f = {"from": from_date, "to": to_date, "date": from_date}
    if route:
        f["route"] = route
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
