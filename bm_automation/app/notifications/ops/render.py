"""Ko'rsatkichlarni Telegram matniga aylantirish (render).

"Jo'nash taxtasi" (departure board) uslubi: emoji-semantik sarlavhalar,
bo'limlar bo'sh qator bilan ajratiladi, ko'p qatorli raqamli ma'lumot
`<pre>` mono-jadvalda, foiz har doim status emoji bilan (✅/⚠️/❌).

Har bir funksiya `(text, reply_markup)` qaytaradi; `reply_markup` inline
navigatsiya (muammo tugmalari / nav) yoki `None`.
"""

from __future__ import annotations

from datetime import date

from ...dashboard.metrics import Metrics, parse_filters
from ...db.models import TripStatus, json_loads
from ...db.storage import get_storage
from ...utils.logger import get_logger
from ...utils.names import short_name
from ...utils.tgformat import badge, badge_plain, esc, fmt, table
from . import context, kb
from .roles import Role, configured_roles, resolve_role, role_label

log = get_logger("bm_automation.bot")

_WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba",
             "juma", "shanba", "yakshanba"]
_MONTHS = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun",
           "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]


def _met() -> Metrics:
    return Metrics()


def _date_label(value: str) -> str:
    """ISO sana -> '10.08.2026 · dushanba'."""
    try:
        d = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return str(value or "")
    return f"{d:%d.%m.%Y} · {_WEEKDAYS[d.weekday()]}"


def _month_label(value: str) -> str:
    """'2026-08' -> 'avgust 2026'."""
    try:
        y, m = str(value).split("-")
        return f"{_MONTHS[int(m)]} {y}"
    except (ValueError, IndexError):
        return str(value or "")


def _combine(*kbs) -> dict | None:
    rows = []
    for k in kbs:
        if k and k.get("inline_keyboard"):
            rows.extend(k["inline_keyboard"])
    return {"inline_keyboard": rows} if rows else None


def _profile_bar(filters: dict | None) -> dict | None:
    """Faol kompaniya bo'lsa 'chiqish' tugmasi qatorini qaytaradi."""
    name = (filters or {}).get("profile")
    return kb.profile_bar(context.short_name(name)) if name else None


# ---------------------------------------------------------------- /today

PROBLEM_LABELS = {
    "gps": "GPS",
    "technical": "texnik",
    "schedule": "jadval",
    "unknown": "noma'lum",
}


def today(filters: dict | None = None) -> tuple[str, dict]:
    f = parse_filters(filters)
    m = _met()
    t = m.today(f)
    p = m.problems(f)
    c = p["counts"]
    title = "📊 <b>BUGUNGI HOLAT</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"

    perf = (t["completed"] / t["planned"] * 100) if t["planned"] else None
    lines = [
        title,
        f"📅 {_date_label(t['date'])}",
        "",
        "<b>🚌 AVTOBUSLAR</b>",
        f"Jami: {t['total_buses']}  |  🔄 Faol: {t['active_buses']}  |  "
        f"❌ Muammoli: {t['not_active_buses']}",
        "",
        "<b>📋 REYSLAR</b>",
        f"Reja: {fmt(t['planned'], 0)}  |  Amalda: {fmt(t['completed'], 0)}  |  "
        f"{badge(perf)}",
    ]
    status_rows = []
    if t["accepted"]:
        status_rows.append(["Qabul", fmt(t["accepted"], 0)])
    if t["not_accepted"]:
        status_rows.append(["Qab. qilnmagan", fmt(t["not_accepted"], 0)])
    if t["rejected"]:
        status_rows.append(["Rejected", fmt(t["rejected"], 0)])
    if t["zero_mileage"]:
        status_rows.append(["Nollik", fmt(t["zero_mileage"], 0)])
    if status_rows:
        lines.append("")
        lines.append(table(["Holat", "Soni"], status_rows))
    lines += [
        "",
        "<b>⚠️ MUAMMOLAR</b>",
        f"GPS: {c['gps']}  |  Texnik: {c['technical']}  |  "
        f"Jadval: {c['schedule']}  |  Noma'lum: {c['unknown']}",
    ]
    return "\n".join(lines), _combine(kb.problems_kb(p), kb.download_kb("today"),
                                      _profile_bar(f), kb.nav_kb("nav:sync"))


# --------------------------------------------------------------- /problems

def _problem_table(items: list[dict], limit: int = 6) -> list[str]:
    if not items:
        return ["  yo'q ✓"]
    rows = [
        [it.get("planned_time") or "--:--",
         it.get("vehicle") or "-",
         short_name(str(it.get("driver") or "-")),
         it.get("status") or "-"]
        for it in items[:limit]
    ]
    lines = [table(["Vaqt", "Avtobus", "Haydovchi", "Holat"], rows)]
    if len(items) > limit:
        lines.append(f"  ... yana {len(items) - limit} ta")
    return lines


def problems(filters: dict | None = None) -> tuple[str, dict]:
    f = parse_filters(filters)
    m = _met()
    p = m.problems(f)
    c = p["counts"]
    title = "⚠️ <b>MUAMMOLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [
        title,
        f"📅 {_date_label(f['date'])} · Jami: {sum(c.values())} ta",
    ]
    for key, label in PROBLEM_LABELS.items():
        items = p["items"][key]
        parts.append("")
        parts.append(f"<b>{label} ({len(items)})</b>")
        parts.extend(_problem_table(items))
    return "\n".join(parts), _combine(kb.problems_kb(p), _profile_bar(f),
                                      kb.nav_kb("nav:sync"))


def problem_category(key: str, filters: dict | None = None) -> tuple[str, dict]:
    """Bitta muammo kategoriyasining batafsil ro'yxati (inline tugma uchun)."""
    f = parse_filters(filters)
    m = _met()
    p = m.problems(f)
    label = PROBLEM_LABELS.get(key, key)
    items = p["items"].get(key, [])
    title = f"⚠️ <b>{label.upper()} MUAMMOLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [
        title,
        f"📅 {_date_label(f['date'])} · Jami: {len(items)} ta",
        "",
    ]
    if not items:
        parts.append("  yo'q ✓")
    for i, it in enumerate(items[:20], 1):
        parts.append(
            f"{i}. {it['planned_time'] or '--:--'} | {esc(it['vehicle'] or '-')}\n"
            f"   Yo'nalish: {esc(it['route'] or '-')}\n"
            f"   Haydovchi: {esc(short_name(str(it['driver'] or '-')))}\n"
            f"   Holat: {esc(it['status'] or '-')}")
        if it.get("actual_time"):
            parts.append(f"   Haqiqiy: {esc(it['actual_time'])}")
    if len(items) > 20:
        parts.append(f"... yana {len(items) - 20} ta")
    return "\n".join(parts), _combine(_profile_bar(f), kb.back_kb("nav:problems"))


# ---------------------------------------------------------------- /routes

def routes(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().routes(f)
    title = "🛣 <b>YO'NALISHLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_date_label(f['date'])}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    else:
        trows = [
            [r["name"], fmt(r["planned"], 0), fmt(r["actual"], 0),
             badge_plain(r["performance"])]
            for r in rows[:limit]
        ]
        parts.append(table(["Yo'nalish", "Reja", "Amalda", "Foiz"], trows))
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta yo'nalish")
    return "\n".join(parts), _combine(kb.download_kb("routes"), _profile_bar(f),
                                      kb.nav_kb("nav:routes"))


# -------------------------------------------------------------- /vehicles

def vehicles(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().vehicles(f)
    title = "🚌 <b>AVTOBUSLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_date_label(f['date'])}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    else:
        trows = [
            [f"{'🔄' if v['status'] == 'faol' else '❌'} {v['plate_number']}",
             fmt(v["trips"], 0), fmt(v["issues"], 0),
             esc(v["last_activity"] or "-")]
            for v in rows[:limit]
        ]
        parts.append(table(["Avtobus", "Reys", "Muammo", "Oxirgi"], trows))
        parts.append("")
        parts.append("Karta ochish: tugmani bosing yoki /vehicle raqam")
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta avtobus")
    return "\n".join(parts), _combine(kb.vehicle_buttons(rows),
                                      kb.download_kb("vehicles"),
                                      _profile_bar(f), kb.nav_kb("nav:vehicles"))


# --------------------------------------------------------------- /drivers

def drivers(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().drivers(f)
    title = "👨‍✈️ <b>HAYDOVCHILAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_date_label(f['date'])}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    else:
        trows = [
            [short_name(str(d["name"] or "")), fmt(d["trips"], 0),
             fmt(d["working_days"], 0),
             badge_plain(d["attendance"]), fmt(d["issues"], 0)]
            for d in rows[:limit]
        ]
        parts.append(table(["Haydovchi", "Reys", "Kun", "Qatnash", "Muammo"],
                           trows))
        parts.append("")
        parts.append("Karta ochish: tugmani bosing yoki /driver ism|ID")
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta haydovchi")
    return "\n".join(parts), _combine(kb.driver_buttons(rows),
                                      kb.download_kb("drivers"),
                                      _profile_bar(f), kb.nav_kb("nav:drivers"))


# ----------------------------------------------------------- driver card

def _default_month(f: dict, orig: dict) -> dict:
    """Filtrda sana ko'rsatilmagan bo'lsa joriy oyni qo'yadi (karta uchun)."""
    if (not str(f.get("month") or "").strip()
            and not str(orig.get("from") or "").strip()
            and not str(orig.get("to") or "").strip()
            and not str(orig.get("date") or "").strip()):
        f["month"] = date.today().strftime("%Y-%m")
    return f


def _period_label(f: dict) -> str:
    month = str(f.get("month") or "").strip()
    if month:
        return _month_label(month)
    frm = str(f.get("from") or f.get("date") or "")
    to = str(f.get("to") or "")
    if frm and to and frm != to:
        return f"{frm[8:10]}.{frm[5:7]} … {to[8:10]}.{to[5:7]}"
    return _date_label(frm)


def resolve_driver(arg: str, filters: dict | None = None) -> str:
    """Haydovchi ismi yoki ID'sini driver_id ga aylantiradi.

    Avval aniq ID, so'ngra ism bo'yicha (katta-kichik harf muhim emas,
    substring moslik) qidiriladi. Topilmasa bo'sh qator.
    """
    arg = (arg or "").strip()
    if not arg:
        return ""
    f = _default_month(parse_filters(filters or {}), filters or {})
    rows = _met().drivers(f)
    for r in rows:
        if str(r["driver_id"]) == arg:
            return r["driver_id"]
    low = arg.lower()
    for r in rows:
        name = (r.get("name") or "").lower()
        if low in name or name in low:
            return r["driver_id"]
    return ""


def driver_card(driver_id: str, filters: dict | None = None) -> tuple[str, dict]:
    """Bitta haydovchining to'liq kartasi (profil + oy statistikasi + tarix)."""
    driver_id = str(driver_id or "").strip()
    f = _default_month(parse_filters(filters or {}), filters or {})
    data = _met().driver_detail(driver_id, f)
    if not data:
        return ("👨‍✈️ <b>HAYDOVCHI</b>\n\n"
                "Haydovchi topilmadi."), _combine(kb.back_kb("nav:drivers"))
    d = data["driver"]
    p = data["profile"]
    title = "👨‍✈️ <b>HAYDOVCHI KARTASI</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, esc(short_name(str(d.get("name") or "-"))),
             f"🆔 ID: <code>{esc(d.get('driver_id'))}</code>"]

    marks = []
    if d.get("blacklisted"):
        marks.append("⛔ Qora ro'yxatda")
    if d.get("notification_enabled"):
        marks.append("🔔 Telegram bildirishnoma yoqilgan")
    if marks:
        parts.append(" · ".join(marks))
    rating = d.get("rating")
    parts.append(f"⭐ Reyting: {fmt(rating, 1) if rating is not None else '-'}  ·  "
                 f"💵 1 km: {fmt(d.get('km_rate') or 0, 0)} so'm")

    doc_lines = []
    if d.get("has_passport"):
        line = f"  Passport: ✅ {esc(p.get('passport_number') or '-')}"
        if p.get("passport_expiry"):
            line += f" · muddat: {esc(p['passport_expiry'])}"
        doc_lines.append(line)
    else:
        doc_lines.append("  Passport: ❌ kiritilmagan")
    if d.get("has_license"):
        line = f"  Guvohnoma: ✅ {esc(p.get('license_number') or '-')}"
        if p.get("license_category"):
            line += f" ({esc(p['license_category'])})"
        if p.get("license_expiry"):
            line += f" · muddat: {esc(p['license_expiry'])}"
        doc_lines.append(line)
    else:
        doc_lines.append("  Guvohnoma: ❌ kiritilmagan")
    parts += ["", "🪪 <b>HUJJATLAR</b>"] + doc_lines

    parts += ["", f"📅 <b>{_period_label(f)}</b>"]
    manual = d.get("manual_trips") or 0
    qatnov = fmt(d.get("trips") or 0, 0) + (f" (+{fmt(manual, 0)} qo'l)" if manual else "")
    auto_km = d.get("automatic_km") or 0
    km = fmt(d.get("km") or 0, 1) + (f" (API {fmt(auto_km, 1)})" if auto_km else "")
    parts.append(table(["Ko'rsatkich", "Qiymat"], [
        ["Ish kunlari", fmt(d.get("working_days") or 0, 0)],
        ["Qatnov", qatnov],
        ["Km", km],
        ["Brutto", fmt(d.get("gross_pay") or 0, 0)],
        ["Jarimalar", fmt(d.get("fines") or 0, 0)],
        ["Netto", fmt(d.get("net_pay") or 0, 0)],
        ["Qatnashish", badge_plain(d.get("attendance"))],
    ]))

    logs = data.get("work_logs") or []
    if logs:
        parts += ["", "📋 <b>ISH QAYDLARI</b>"]
        parts.append(table(["Sana", "Avtobus", "Km", "Qat"], [
            [r.get("date") or "-",
             r.get("vehicle") or r.get("vehicle_id") or "-",
             fmt(r.get("distance_km") or 0, 1),
             fmt(r.get("trip_count") or 0, 0)]
            for r in logs[:8]
        ]))

    fines = data.get("fine_rows") or []
    if fines:
        parts += ["", "⚠️ <b>JARIMALAR</b>"]
        parts.append(table(["Sana", "Summa", "Sabab", "Holat"], [
            [r.get("date") or "-", fmt(r.get("amount") or 0, 0),
             (r.get("reason") or "-")[:16], r.get("status") or "-"]
            for r in fines[:8]
        ]))

    trips = data.get("trips") or []
    if trips:
        icons = {"ACCEPTED": "✅", "APPROVED": "✅", "NOT_ACCEPTED": "❌",
                 "REJECTED": "⛔", "ZERO_MILEAGE": "0️⃣", "PENDING_ACCESS": "⏳"}
        parts += ["", "🕒 <b>SO'NGI REYSLAR</b>"]
        for t in trips[:5]:
            parts.append(
                f"{icons.get(t.get('status') or '', '❓')} "
                f"{esc(t.get('date') or '-')} {esc(t.get('planned_time') or '--:--')} | "
                f"{esc(t.get('route') or '-')} | {esc(t.get('vehicle') or '-')}")
    return "\n".join(parts), _combine(kb.driver_card_kb(driver_id),
                                      _profile_bar(f), kb.nav_kb("nav:drivers"))


# ------------------------------------------------------- vehicle card

def resolve_vehicle(arg: str, filters: dict | None = None) -> str:
    """Avtobus raqami/ID'sini vehicle_id ga aylantiradi."""
    return _met().resolve_vehicle(arg, filters)


def vehicle_card(vehicle_id: str, filters: dict | None = None) -> tuple[str, dict]:
    """Bitta avtobusning to'liq kartasi (ma'lumot + masofa + grafik + reyslar)."""
    vehicle_id = str(vehicle_id or "").strip()
    data = _met().vehicle_detail(vehicle_id, filters)
    if not data:
        return ("🚌 <b>AVTOBUS</b>\n\n"
                "Avtobus topilmadi."), _combine(kb.back_kb("nav:vehicles"))
    v = data["vehicle"]
    rd = data["route_daily"]
    tr = data["trips"]
    f = data["period"]
    title = "🚌 <b>AVTOBUS KARTASI</b>"
    if v.get("company"):
        title += f" — 🏢 {context.short_name(v['company'])}"
    parts = [title, f"🚌 {esc(v['plate_number'])}"]
    meta = []
    if v.get("garage_number"):
        meta.append(f"Garag: {esc(v['garage_number'])}")
    if v.get("model"):
        meta.append(f"Model: {esc(v['model'])}")
    if v.get("route_name"):
        meta.append(f"Yo'nalish: {esc(v['route_name'])}")
    if meta:
        parts.append(" · ".join(meta))
    parts.append(f"🆔 ID: <code>{esc(v['vehicle_id'])}</code>")

    parts += ["", f"📅 <b>{_period_label(f)}</b>"]
    parts.append(table(["Ko'rsatkich", "Qiymat"], [
        ["Ish kunlari", fmt(rd.get("days") or 0, 0)],
        ["Reja km", fmt(rd.get("distance_plan") or 0, 1)],
        ["Fakt km", fmt(rd.get("distance_fact") or 0, 1)],
        ["Qo'shimcha km", fmt(rd.get("distance_fact_extra") or 0, 1)],
        ["Reja reys", fmt(rd.get("trip_plan") or 0, 0)],
        ["Fakt reys", fmt(rd.get("trip_fact") or 0, 0)],
        ["Qabul reys", fmt(rd.get("trip_approved") or 0, 0)],
        ["Muammolar", fmt(tr.get("issues") or 0, 0)],
    ]))

    sched = data.get("schedule_today") or []
    if sched:
        parts += ["", "📅 <b>BUGUNGI GRAFIK</b>"]
        parts.append(table(["Smena", "Vaqt", "Haydovchi", "Reys"], [
            [s.get("shift") or s.get("graph") or "-",
             f"{s.get('start') or '--'}-{s.get('end') or '--'}",
             short_name(str(s.get("driver") or "-")),
             fmt(s.get("trip_count") or 0, 0)]
            for s in sched[:6]
        ]))

    last = data.get("last_trips") or []
    if last:
        icons = {"ACCEPTED": "✅", "APPROVED": "✅", "NOT_ACCEPTED": "❌",
                 "REJECTED": "⛔", "ZERO_MILEAGE": "0️⃣", "PENDING_ACCESS": "⏳"}
        parts += ["", "🕒 <b>SO'NGI REYSLAR</b>"]
        for t in last[:5]:
            parts.append(
                f"{icons.get(t.get('status') or '', '❓')} "
                f"{esc(t.get('date') or '-')} {esc(t.get('planned_time') or '--:--')} | "
                f"{esc(t.get('route') or '-')} | {esc(short_name(str(t.get('driver') or '-')))}")
    return "\n".join(parts), _combine(kb.back_kb("nav:vehicles"),
                                      _profile_bar(f), kb.nav_kb("nav:vehicles"))


# ----------------------------------------------------------- leaderboard

_TOP_METRICS = {
    "km": ("📏 Masofa (km)", "km", 1),
    "trips": ("🔁 Reyslar", "trips", 0),
    "net": ("💵 Netto (so'm)", "net_pay", 0),
    "attendance": ("📋 Davomat %", "attendance", 1),
}


def leaderboard(filters: dict | None = None, metric: str = "km",
                limit: int = 10, chat_id: int | None = None) -> tuple[str, dict]:
    """Haydovchilar reytingi — tanlangan ko'rsatkich bo'yicha top-10."""
    from .roles import Role, resolve_role
    is_driver = chat_id is not None and resolve_role(chat_id) is Role.DRIVER
    metric = (metric or "km").lower()
    if is_driver and metric == "net":
        metric = "km"
    label, key, dec = _TOP_METRICS.get(metric, _TOP_METRICS["km"])
    f = _default_month(parse_filters(filters or {}), filters or {})
    rows = [r for r in _met().drivers(f)
            if r.get(key) is not None and not r.get("blacklisted")]
    rows.sort(key=lambda r: (r.get(key) or 0), reverse=True)
    rows = rows[:limit]

    title = f"🏆 <b>REYTING</b> — {label}"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_period_label(f)}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    else:
        medals = ["🥇", "🥈", "🥉"]
        trows = [
            [f"{medals[i] if i < 3 else i + 1} "
             f"{short_name(str(r.get('name') or r.get('driver_id') or '-'))}",
             fmt(r.get(key) or 0, dec)]
            for i, r in enumerate(rows)
        ]
        parts.append(table(["Haydovchi", "Ko'rsatkich"], trows))
    parts.append("")
    parts.append("Boshqa ko'rsatkich: /top km|trips|net|attendance")

    buttons = [{"text": label, "callback_data": f"top:{m}"}
               for m, (label, _k, _d) in _TOP_METRICS.items()]
    kb_row = {"inline_keyboard": [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)]}
    return "\n".join(parts), _combine(kb_row, _profile_bar(f),
                                      kb.nav_kb("nav:drivers"))


# -------------------------------------------------------------- distance

def distance(filters: dict | None = None, limit: int = 15) -> tuple[str, dict]:
    """Avtobuslar bo'yicha masofa (km) hisoboti — route_daily asosida."""
    data = _met().distance(filters)
    f = data["period"]
    title = "📏 <b>MASOFA (KM)</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_period_label(f)}", ""]
    if not data["rows"]:
        parts.append("Ma'lumot yo'q.")
    else:
        trows = []
        for r in data["rows"][:limit]:
            rname = r.get("route_name") or "-"
            if len(rname) > 18:
                rname = rname[:15] + "..."
            trows.append([esc(r["plate_number"]),
                          fmt(r["distance_fact"], 1),
                          fmt(r["distance_plan"], 1),
                          fmt(r["trip_approved"], 0),
                          esc(rname)])
        t = data["totals"]
        trows.append(["JAMI", fmt(t["distance_fact"], 1),
                      fmt(t["distance_plan"], 1), fmt(t["trip_approved"], 0), ""])
        parts.append(table(["Avtobus", "Fakt km", "Reja km", "Qabul", "Yo'nalish"],
                           trows))
    return "\n".join(parts), _combine(kb.download_kb("all"), _profile_bar(f),
                                      kb.nav_kb("nav:routes"))


# -------------------------------------------------------------- schedule

def schedule(filters: dict | None = None, limit_routes: int = 8,
             limit_rows: int = 12) -> tuple[str, dict]:
    """Tanlangan sana uchun yo'nalish bo'yicha jadval.

    Telegram 4096 belgi limitiga sig'ishi uchun yo'nalishlar va har bir
    yo'nalishdagi qatorlar soni cheklanadi; qolgani "... yana N ta" bilan
    ko'rsatiladi.
    """
    f = parse_filters(filters or {})
    data = _met().schedule(f)
    title = "📅 <b>YO'NALISH JADVALI</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [
        title,
        f"📅 {_date_label(data['date'])} · Avtobuslar: {data['total_buses']}"
        f" · Rejalashtirilgan reys: {data['total_trips']}",
        "",
    ]
    if not data["routes"]:
        parts.append("Jadval yo'q. Sana noto'g'ri bo'lishi mumkin "
                     "(masalan: /schedule 2026-08-15).")
    total_routes = len(data["routes"])
    total_items = sum(len(rt["items"]) for rt in data["routes"])
    for rt in data["routes"][:limit_routes]:
        head = f"🛣 <b>{esc(rt['route_name'])}</b>"
        if rt.get("company"):
            head += f" — {esc(rt['company'])}"
        parts.append(head)
        items = rt["items"]
        parts.append(table(["Smena", "Boshlash", "Haydovchi", "Avtobus", "Reys"], [
            [r.get("shift") or r.get("graph") or "-",
             r.get("start") or "--:--",
             short_name(str(r.get("driver") or "-")),
             r.get("vehicle") or "-",
             fmt(r.get("trip_count") or 0, 0)]
            for r in items[:limit_rows]
        ]))
        if len(items) > limit_rows:
            parts.append(f"... yana {len(items) - limit_rows} ta smena")
    if total_routes > limit_routes:
        parts.append(f"... yana {total_routes - limit_routes} ta yo'nalish")
    if total_items > limit_routes * limit_rows:
        parts.append(f"To'liq jadval: /export schedule yoki "
                     f"/schedule {data['date']} batafsil")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:trips"))


# -------------------------------------------------------------- attendance

def attendance(filters: dict | None = None, limit: int = 20) -> tuple[str, dict]:
    """Kunlik davomat: qatnashgan / kelmagan haydovchilar."""
    f = parse_filters(filters or {})
    data = _met().attendance(f)
    title = "📋 <b>DAVOMAT</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [
        title,
        f"📅 {_date_label(data['date'])}",
        "",
        f"Jami: {data['total']} · ✅ Qatnashdi: {data['present_count']} · "
        f"❌ Kelmadi: {data['absent_count']}",
        f"Qatnashish: {badge(data['rate'])}",
    ]
    if data["present"]:
        parts += ["", "<b>✅ JOYIDA KELGANLAR</b>"]
        for p in data["present"][:limit]:
            parts.append(f"• {esc(p['name'])}")
        if len(data["present"]) > limit:
            parts.append(f"... yana {len(data['present']) - limit} ta")
    if data["absent"]:
        parts += ["", "<b>❌ KELMAGANLAR</b>"]
        for a in data["absent"][:limit]:
            parts.append(f"• {esc(a['name'])}")
        if len(data["absent"]) > limit:
            parts.append(f"... yana {len(data['absent']) - limit} ta")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:drivers"))


# --------------------------------------------------------------- /alerts

def alerts_text() -> tuple[str, dict]:
    """Muammo alertlari + ertalabki xulosa + AI o'z-o'zini rivojlantirish holati."""
    from . import daily_summary, problem_alerts, self_review
    text = "\n\n".join([
        problem_alerts.status_text(),
        daily_summary.status_text(),
        self_review.status_text(),
    ])
    return text, _combine(kb.nav_kb("nav:sync"))


# ----------------------------------------------------------------- /trips

def _trip_line(t: dict) -> str:
    status = t.get("status") or ""
    icon = {"ACCEPTED": "✅", "APPROVED": "✅", "NOT_ACCEPTED": "❌",
            "REJECTED": "⛔", "ZERO_MILEAGE": "0️⃣", "PENDING_ACCESS": "⏳"}
    data = json_loads(t.get("data") or "")
    route = data.get("routeName") or t.get("route_id") or "-"
    plate = (data.get("plateNum") or data.get("plateNumber")
             or t.get("vehicle_id") or "-")
    driver = data.get("driverName") or t.get("driver_id") or "-"
    quality = ", ".join(data.get("poorQualities") or []) or "-"
    try:
        missed = int(data.get("totalStationCount") or 0) - int(
            data.get("totalPassedStationCount") or 0)
    except (TypeError, ValueError):
        missed = "-"
    return (f"{icon.get(status, '❓')} {t.get('planned_time') or '--:--'} | "
            f"{esc(route)} | {esc(plate)} | {esc(status)}\n"
            f"   🧑 {esc(driver)} · sifatsizlik: {esc(quality)} · "
            f"o'tkazib yuborilgan bekat: {missed}")


def trips(filters: dict | None = None, limit: int = 15) -> tuple[str, dict]:
    f = parse_filters(filters)
    m = _met()
    rows = m.trips(f, limit=limit)
    total = m.today(f)["total_trips"]
    title = "📋 <b>REYSLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [title, f"📅 {_date_label(f['date'])} · Jami: {total} ta"]
    for t in rows:
        parts.append(_trip_line(t))
    if total > limit:
        parts.append(f"... jami {total} ta (ko'rish uchun /export yoki /problems)")
    return "\n".join(parts), _combine(kb.download_kb("trips"), _profile_bar(f),
                                      kb.nav_kb("nav:trips"))


# ---------------------------------------------------------------- /month

def _day_label(value: str) -> str:
    return f"{value[8:10]}.{value[5:7]}"


def month(filters: dict | None = None) -> tuple[str, dict]:
    f = parse_filters(filters or {})
    orig = filters or {}
    if (not str(f.get("month") or "").strip()
            and not str(orig.get("from") or "").strip()
            and not str(orig.get("to") or "").strip()
            and not str(orig.get("date") or "").strip()):
        f["month"] = date.today().strftime("%Y-%m")
    data = _met().monthly(f)
    title = "📅 <b>OY TARIXI</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [
        title,
        f"{_month_label(data['month'])} · {_day_label(data['from'])}"
        f" … {_day_label(data['to'])}",
        "",
    ]
    if not data["days"]:
        parts.append("Ma'lumot yo'q.")
    else:
        trows = [
            [_day_label(d["date"]), fmt(d["planned"], 0), fmt(d["accepted"], 0),
             badge_plain(d["accept_rate"]), badge_plain(d["performance"])]
            for d in data["days"]
        ]
        t = data["totals"]
        trows.append(["JAMI", fmt(t["planned"], 0), fmt(t["accepted"], 0),
                      badge_plain(t["accept_rate"]), badge_plain(t["performance"])])
        parts.append(table(["Sana", "Reja", "Qabul", "Qabul/Reja%", "Foiz"],
                           trows))
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:month"))


# --------------------------------------------------------------- /reports

def reports(filters: dict | None = None, limit: int = 15) -> tuple[str, dict]:
    st = get_storage()
    rows = st.list_reports(limit=limit)
    parts = ["📈 <b>HISOBOTLAR</b>", ""]
    if not rows:
        parts.append("Hisobotlar yo'q. /sync yoki CLI orqali yarating.")
    for r in rows:
        parts.append(
            f"• <b>{esc(r.get('name') or '-')}</b> "
            f"({esc(r.get('report_type') or '-')})\n"
            f"  davr: {esc(r.get('period_date') or '-')} · "
            f"{esc(r.get('updated_at') or '')}\n"
            f"  fayl: {esc(r.get('file_path') or '-')}")
    runs = st.list_runs(limit=3)
    if runs:
        parts.append("")
        parts.append("So'nggi avto-ishlar:")
        for run in runs:
            parts.append(f"  {run.get('started_at')} → {run.get('status')} "
                         f"[{run.get('trigger')}]")
    return "\n".join(parts), _combine(kb.nav_kb("nav:reports"))


# --------------------------------------------------------------- /profiles

def profiles(filters: dict | None = None, chat_id: int | None = None) -> tuple[str, dict]:
    """Kompaniyalar ro'yxati (tanlash ekrani).

    Ruxsat: ADMIN hammasini, kompaniya egasi faqat o'zini ko'radi.
    """
    f = parse_filters(filters or {})
    active = f.get("profile", "")
    role = resolve_role(chat_id) if chat_id is not None else Role.ADMIN
    is_admin = role is Role.ADMIN
    plist = context.profiles_with_routes(chat_id)
    parts = [
        "🏢 <b>FIRMALAR</b>",
        "",
        "Kompaniyani tanlang — uning ma'lumotlari alohida ko'rsatiladi:",
        "",
    ]
    for p in plist:
        mark = " ✅" if p["name"] == active else ""
        route_txt = esc(p.get("route_name") or "yo'nalishi yo'q")
        parts.append(f"• {esc(p['label'])}{mark} — {route_txt}")
    if not plist:
        parts.append("Kompaniyalar yo'q. Profil qo'shish kerak (profiles.json).")
    if active and is_admin:
        parts.append("")
        parts.append("Chiqish uchun quyidagi 'Barchasi / Chiqish' tugmasini bosing.")
    return "\n".join(parts), kb.profiles_kb(plist, active, allow_all=is_admin)


# ---------------------------------------------------------------- /errors

def errors(limit: int = 10) -> tuple[str, dict]:
    st = get_storage()
    rows = st.list_errors(limit=limit)
    parts = ["⛔ <b>XATOLAR</b>", ""]
    if not rows:
        parts.append("Xatolar yo'q ✓")
    for e in rows:
        parts.append(
            f"• {e.get('occurred_at')} [{e.get('source')}]\n"
            f"  {esc(str(e.get('message') or ''))[:200]}")
    return "\n".join(parts), _combine(kb.nav_kb("nav:sync"))


# ---------------------------------------------------------------- /status

def status(system: dict | None = None) -> tuple[str, dict]:
    m = _met()
    sysd = system or m.system()
    st = get_storage()
    counts = st.counts()

    api = sysd.get("api", {})
    api_txt = "✅ OK" if api.get("ok") else "❌ XATO"
    api_line = (f"{api_txt} (HTTP {api.get('status')}, {api.get('ms')}ms)"
                if api.get("ok") and api.get("status")
                else f"{api_txt}: {api.get('error', '')}")

    db = sysd.get("database", {})
    db_state = "yoqilgan" if db.get("ok") else "o'chiq"
    tg = sysd.get("telegram", {})
    tg_state = "konfiguratsiya qilingan" if tg.get("configured") else "yo'q"
    sched = sysd.get("scheduler", {})

    parts = [
        "⚙️ <b>TIZIM HOLATI</b>",
        "",
        f"🖥 BM API: {api_line}",
        f"🗄 Database: {db.get('driver') or '-'} ({db_state})",
        f"📨 Telegram: {tg_state}",
        f"⏰ Scheduler: {sched.get('status') or '-'} "
        f"(oxirgi: {sched.get('last_run') or '-'})",
        "",
        f"💾 <b>MA'LUMOTLAR</b>",
        f"  Profillar: {counts.get('profiles', 0)}",
        f"  Yo'nalishlar: {counts.get('routes', 0)}",
        f"  Avtobuslar: {counts.get('vehicles', 0)}",
        f"  Haydovchilar: {counts.get('drivers', 0)}",
        f"  Reyslar: {counts.get('trips', 0)}",
        f"  Xatolar: {counts.get('errors', 0)}",
        "",
        f"So'nggi sinxronlash: {sysd.get('last_sync') or '-'}",
    ]
    le = sysd.get("last_error")
    if le:
        parts.append(f"Oxirgi xato: {le.get('at')} [{le.get('source')}] "
                     f"{esc(str(le.get('message') or ''))[:100]}")
    return "\n".join(parts), _combine(kb.nav_kb("nav:sync"))


# -------------------------------------------------------------- /settings

def settings_text(chat_id: int | None) -> str:
    role = resolve_role(chat_id)
    roles = configured_roles()
    admins = sum(1 for r in roles.values() if r is Role.ADMIN)
    dispatchers = sum(1 for r in roles.values() if r is Role.DISPATCHER)
    managers = sum(1 for r in roles.values() if r is Role.MANAGER)
    open_mode = not roles
    owned = context.allowed_names(chat_id) if chat_id is not None else []
    owned_txt = ", ".join(context.short_name(n) for n in owned) or "yo'q"

    try:
        from ...core.bot_settings import km_rate, lang
        rate = km_rate()
        lang_txt = "🇷🇺 Русский" if lang(chat_id) == "ru" else "🇺🇿 O'zbek"
    except Exception:  # noqa: BLE001 - sozlama bo'lmasa standart qiymat
        rate, lang_txt = 0.0, "🇺🇿 O'zbek"
    rate_txt = (f"{rate:,.0f}".replace(",", " ") + " so'm"
                if rate > 0 else "o'rnatilmagan (env / profil bo'yicha)")

    parts = [
        "⚙️ <b>SETTINGS</b>",
        "",
        f"Sizning rolingiz: {role_label(role)}",
        "",
        "Rollar (chat_id bo'yicha):",
        f"  ADMIN: {admins} ta",
        f"  DISPATCHER: {dispatchers} ta",
        f"  FIRMA BOSHQARUVCHI: {managers} ta",
        f"  Boshqalar: VIEWER (standart)" if not open_mode else "  Ochiq rejim (hamma ADMIN)",
        "",
        "Sizga ochiq kompaniyalar:",
        f"  {owned_txt}",
        "",
        "💵 <b>1 KM NARXI</b>",
        f"  {rate_txt}",
        "",
        "🌐 <b>TIL</b>",
        f"  {lang_txt}",
    ]
    try:
        from .assistant import ai_status
        parts += ["", ai_status()]
    except Exception:  # noqa: BLE001 - kichik bezak, xato bo'lsa o'tkazib yuboramiz
        pass
    return "\n".join(parts)
