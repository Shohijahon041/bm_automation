"""Ko'rsatkichlarni Telegram matniga aylantirish (render).

"Jo'nash taxtasi" (departure board) uslubi: emoji-semantik sarlavhalar,
bo'limlar bo'sh qator bilan ajratiladi, ko'p qatorli raqamli ma'lumot
`<pre>` mono-jadvalda, foiz har doim status emoji bilan (✅/⚠️/❌).

Har bir funksiya `(text, reply_markup)` qaytaradi; `reply_markup` inline
navigatsiya (muammo tugmalari / nav) yoki `None`.
"""

from __future__ import annotations

from datetime import date, datetime

from pandas import DataFrame

from ...dashboard.metrics import Metrics, parse_filters
from ...dashboard.hisob import brutto as _brutto, hisob_text as _hisob_text
from ...dashboard.brutto_calculator import BruttoCalculator as _BruttoCalculator
from ...dashboard.brutto_calculator import Contract as _Contract
from ...db.models import TripStatus, json_loads
from ...db.storage import get_storage
from ...core.bot_settings import brutto_skm
from ...utils.logger import get_logger
from ...utils.names import short_name
from ...utils.tgformat import badge, badge_plain, esc, fmt, pct, table
from . import context, kb
from .roles import Role, can, configured_roles, resolve_role, role_label

log = get_logger("bm_automation.bot")

# Ish haqi (salary) faqat ADMIN/DISPATCHER/MANAGER ga ko'rinadi.
def _can_view_salary(chat_id: int | None) -> bool:
    if chat_id is None:
        return True  # ichki/skeduler chaqiruvlar uchun boshqaruv rejimi
    return can(resolve_role(chat_id), "salary")

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
    if not name or name in ("__none__", "__deny__"):
        return None
    return kb.profile_bar(context.short_name(name)) if name else None


# ---------------------------------------------------------------- /today

PROBLEM_LABELS = {
    "gps": "GPS",
    "technical": "texnik",
    "schedule": "jadval",
    "unknown": "noma'lum",
}

_STATUS_SHORT = {
    "ACCEPTED": "Qabul",
    "APPROVED": "Tasdiq",
    "NOT_ACCEPTED": "Qab.yoq",
    "REJECTED": "Rad",
    "PENDING_ACCESS": "Kutilmoqda",
    "ZERO_MILEAGE": "Nollik",
}


def _status_label(status: str) -> str:
    """Statusni qisqa o'zbekcha belgiga aylantiradi (mobil o'qish uchun)."""
    s = str(status or "").strip().upper()
    return _STATUS_SHORT.get(s, esc(s) or "-")


def today(filters: dict | None = None, chat_id: int | None = None) -> tuple[str, dict]:
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
        f"🅿️ Rezerv: {t.get('reserve_buses', t['not_active_buses'])}",
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
    did = (f.get("driver_id") or f.get("driver") or "").strip()
    if did:
        b = _brutto(f, did)
    else:
        rows = m.drivers(f) or []
        b = {
            "gross_full": sum(float(r.get("gross_pay") or 0) for r in rows),
            "tax": sum(float(r.get("tax") or 0) for r in rows),
            "fines": sum(float(r.get("fines") or 0) for r in rows),
            "net_pay": sum(float(r.get("net_pay") or 0) for r in rows),
        }
    if b.get("gross_full") and _can_view_salary(chat_id):
        lines += [
            "",
            "<b>💰 ISH HAQI</b>",
            f"Brutto: {fmt(b['gross_full'], 0)}  |  Soliq: {fmt(b['tax'], 0)}  |  "
            f"Jarima: {fmt(b['fines'], 0)}  |  <b>Netto: {fmt(b['net_pay'], 0)}</b>",
        ]
    lines += [
        "",
        "<b>⚠️ MUAMMOLAR</b>",
        f"GPS: {c['gps']}  |  Texnik: {c['technical']}  |  "
        f"Jadval: {c['schedule']}  |  Noma'lum: {c['unknown']}",
    ]
    return "\n".join(lines), _combine(kb.problems_kb(p), kb.download_kb("today"),
                                      _profile_bar(f), kb.nav_kb("nav:sync"))


def today_short(filters: dict | None = None, chat_id: int | None = None) -> str:
    """Bugungi holatning qisqa ixcham varianti (guruh chat'lar uchun).

    To'liq karta va tugmalarsiz — bir nechta satrda asosiy raqamlar.
    Guruhga spam tashlamaslik uchun `in_group` rejimida ishlatiladi.
    """
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
        f"🚌 Jami: {t['total_buses']} · Faol: {t['active_buses']}",
        f"📋 Reja: {fmt(t['planned'], 0)} · Amalda: {fmt(t['completed'], 0)} · "
        f"{badge(perf)}",
    ]
    if t["accepted"] or t["not_accepted"]:
        parts = []
        if t["accepted"]:
            parts.append(f"✅ Qabul: {fmt(t['accepted'], 0)}")
        if t["not_accepted"]:
            parts.append(f"◇ Qab.yoq: {fmt(t['not_accepted'], 0)}")
        lines.append("   ".join(parts))
    lines.append(f"⚠️ Muammolar: {sum(c.values())} ta "
                 f"(GPS {c['gps']} · Tex {c['technical']} · "
                 f"Jad {c['schedule']} · Sim {c['unknown']})")
    return "\n".join(lines)


# --------------------------------------------------------------- /problems

def _problem_table(items: list[dict], limit: int = 6) -> list[str]:
    if not items:
        return ["  yo'q ✓"]
    # Mobil Telegram: keng `<pre>` jadval o'rniga ixcham inline qatorlar —
    # har bir reys bitta qatorda, ekranda osongina o'qiladi.
    lines = []
    for it in items[:limit]:
        vt = it.get("planned_time") or "--:--"
        veh = esc(it.get("vehicle") or "-")
        drv = esc(short_name(str(it.get("driver") or "-")))
        stt = it.get("status") or "-"
        lines.append(f"  {vt}  {veh}  {drv}  {_status_label(stt)}")
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


_EXPIRY_SOON_DAYS = 30


def _expiry_state(value: str) -> tuple[str, str] | None:
    """Muddat holatini aniqlaydi: (status_badge, detail).

    Yetmagan → None; muddati o'tgan → '❌'; 30 kundan kam qolgan → '⚠️';
    aks holda '✅'. `value` ISO sana (YYYY-MM-DD) deb qabul qilinadi.
    """
    try:
        d = date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None
    from datetime import date as _d2
    today = _d2.today()
    delta = (d - today).days
    if delta < 0:
        return "❌ o'tgan", str(value)
    if delta <= _EXPIRY_SOON_DAYS:
        return "⚠️ yaqin", str(value)
    return "✅", str(value)


def driver_shortcomings(data: dict) -> list[str]:
    """Haydovchi profilidagi jiddiy kamchiliklar ro'yxati (profil to'ldirish).

    Belgilangan maydonlar kiritilmagan yoki muddati o'tayotgan hujjatlar
    haqida ogohlantirish qatorlarini qaytaradi. Kamchilik bo'lmasa — bo'sh.
    """
    d = data.get("driver") or {}
    p = data.get("profile") or {}
    out: list[str] = []

    if d.get("blacklisted"):
        out.append("⛔ Qora ro'yxatda — admin bilan bog'laning")

    if not str(p.get("phone") or "").strip():
        out.append("📱 Telefon raqam kiritilmagan")
    if not str(p.get("passport_number") or "").strip():
        out.append("🪪 Passport raqami kiritilmagan")
    elif (st := _expiry_state(p.get("passport_expiry"))) and st[0] != "✅":
        out.append(f"🪪 Passport muddati: {st[0]} ({esc(st[1])})")
    if not str(p.get("license_number") or "").strip():
        out.append("🚘 Guvohnoma raqami kiritilmagan")
    elif (st := _expiry_state(p.get("license_expiry"))) and st[0] != "✅":
        out.append(f"🚘 Guvohnoma muddati: {st[0]} ({esc(st[1])})")
    if not p.get("notification_enabled"):
        out.append("🔕 Telegram bildirishnoma yoqilmagan")
    return out


def driver_inbox(driver_id: str, filters: dict | None = None,
                 chat_id: int | None = None) -> tuple[str, dict]:
    """Haydovchi o'zi uchun: profil kartasi + kamchiliklar (inbox uslubi).

    `/start` da haydovchi o'z profilini ko'radi; Brutto/Netto satrlari
    `chat_id` gating orqali yashiriladi (umumiy jarima summasi esa
    haydovchiga ham ko'rinadi). Kamchiliklar bo'lmasa, profil kartasi
    toza chiqadi.
    """
    did = str(driver_id or "").strip()
    text, markup = driver_card(did, filters or None, chat_id=chat_id)

    # Kamchiliklar uchun profil data'sini qayta so'raymiz (rendering uchun).
    from ...dashboard.metrics import Metrics as _MM, parse_filters as _pf
    _f = _default_month(_pf(filters or {}), filters or {})
    data = _MM().driver_detail(did, _f)
    gaps = driver_shortcomings(data) if data else []
    gaps = [g for g in gaps if "Telegram bildirishnoma" not in g]
    if gaps:
        block = ["", "⚠️ <b>KAMCHILIKLAR</b>"] + [f"  {g}" for g in gaps]
        text = f"{text}\n\n" + "\n".join(block)
    return text, markup


def driver_card(driver_id: str, filters: dict | None = None,
                chat_id: int | None = None) -> tuple[str, dict]:
    """Bitta haydovchining to'liq kartasi (profil + oy statistikasi + tarix).

    `chat_id` berilsa maosh (Brutto/Jarimalar/Netto) faqat ruxsat etilgan
    (ADMIN/DISPATCHER/MANAGER) foydalanuvchilarga ko'rsatiladi.
    """
    driver_id = str(driver_id or "").strip()
    f = _default_month(parse_filters(filters or {}), filters or {})
    # Haydovchi o'z kartasini ko'rganida `__deny__`/`__none__` sentinel
    # qiymatlari (ruxsatsiz ma'lumot belgisi) barcha satrlarni yashirib
    # qo'yadi — ularni tashlab, o'z statistikasi to'liq ko'rinadi.
    if chat_id is not None and resolve_role(chat_id) is Role.DRIVER:
        for _key in ("route", "profile"):
            if f.get(_key) in ("__deny__", "__none__"):
                f.pop(_key, None)
    data = _met().driver_detail(driver_id, f)
    if not data:
        return ("👨‍✈️ <b>HAYDOVCHI</b>\n\n"
                "Haydovchi topilmadi."), _combine(kb.back_kb("nav:drivers"))
    d = data["driver"]
    p = data["profile"]
    title = "👨‍✈️ <b>HAYDOVCHI KARTASI</b>"
    is_driver_self = chat_id is not None and resolve_role(chat_id) is Role.DRIVER
    if f.get("profile") and not is_driver_self:
        title += f" — 🏢 {context.short_name(f['profile'])}"
    elif is_driver_self and str(d.get("company") or "").strip() and \
            str(d.get("company") or "").strip() not in ("none", "None"):
        title += f" — 🏢 {esc(d['company'])}"
    parts = [title, esc(short_name(str(d.get("name") or "-"))),
             f"🆔 ID: <code>{esc(d.get('driver_id'))}</code>"]

    marks = []
    if d.get("blacklisted"):
        marks.append("⛔ QORA RO'YXATDA")
    if d.get("notification_enabled"):
        marks.append("🔔 Telegram bildirishnoma yoqilgan")
    if marks:
        parts.append(" · ".join(marks))
    # Qora ro'yxat sababi — alohida blokda prominently ko'rsatiladi.
    if d.get("blacklisted") and str(p.get("blacklist_reason") or "").strip():
        parts += [f"🚫 Sabab: {esc(str(p['blacklist_reason']).strip())}"]
    # Reyting o'rni — yo'nalish bo'yicha nechanchi o'rinda (KM bo'yicha).
    rank = None
    try:
        rank = _met().driver_rank(driver_id, f, "km")
    except Exception:  # noqa: BLE001 - rank aniqlanmasa kartani buzmaydi
        rank = None
    rating = d.get("rating")
    rating_txt = f"⭐ Reyting: {fmt(rating, 1) if rating is not None else '-'}"
    if rank and rank.get("position"):
        rating_txt += (f"  ·  🏆 {rank['position']}-o'rin"
                       f" (yo'nalish, {rank['total']} dan)")
    if _can_view_salary(chat_id):
        rating_txt += f"  ·  💵 1 km: {fmt(d.get('km_rate') or 0, 0)} so'm"
    parts.append(rating_txt)

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
    rows = [
        ["Ish kunlari", fmt(d.get("working_days") or 0, 0)],
        ["Qatnov", qatnov],
        ["Km", km],
    ]
    # Jarima summasi — haydovchi o'z profilida ham ko'radi (faqat umumiy
    # summa; tafsilotlar va ish haqi qatorlari role-gating orqali).
    # Avval jadvalda, keyin alohida "💸 JAMI JARIMA" blokida ko'rsatiladi.
    fines_sum = data.get("fines_total")
    if fines_sum is None:
        try:  # eski Metrics.da maydon bo'lmasa — DB dan hisoblaymiz
            fines_sum = sum(
                float(r.get("amount") or 0)
                for r in get_storage().fines_list(driver_id, f.get("month") or "")
                if str(r.get("status") or "ACTIVE").upper() == "ACTIVE")
        except Exception:  # noqa: BLE001 - jarima bo'lmasa kartani buzmaydi
            fines_sum = 0.0
    rows.append(["💸 Jarima summasi", fmt(float(fines_sum or 0), 0) + " so'm"])
    if _can_view_salary(chat_id):
        rows += [
            ["Brutto", fmt(d.get("gross_pay") or 0, 0)],
            ["Jarimalar", fmt(d.get("fines") or 0, 0)],
            ["Netto", fmt(d.get("net_pay") or 0, 0)],
        ]
    rows.append(["Qatnashish", badge_plain(d.get("attendance"))])
    parts.append(table(["Ko'rsatkich", "Qiymat"], rows))

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

    # Umumiy jarima summasi — kartada ko'zga ko'ringan alohida blok.
    if (fines_sum or 0) > 0:
        parts += ["", f"💸 <b>JAMI JARIMA:</b> {fmt(float(fines_sum), 0)} so'm"]

    fines = data.get("fine_rows") or []
    if fines:
        parts += ["", "⚠️ <b>JARIMALAR</b>"]
        parts.append(table(["Sana", "Summa", "Sabab", "Holat"], [
            [r.get("date") or "-", fmt(r.get("amount") or 0, 0),
             (r.get("reason") or "-")[:16], r.get("status") or "-"]
            for r in fines[:8]
        ]))

    # Qabul qilinmagan reyslar + KM (reja/amalda) — davr uchun hisobot.
    # Haydovchi o'zi ko'rsa ham muhim: qaysi kunlarda reja bajarilmagani.
    try:
        na = _met().not_accepted_km_report({**f, "driver": driver_id})
        na_rows = [r for r in (na.get("rows") or [])
                   if str(r.get("name") or "") not in
                   ("— Atribut qilinmagan —", "")]
        if na_rows:
            nr = na_rows[0]
            parts += ["", "🚫 <b>QABUL QILINMAGAN REYSLAR</b>"]
            parts.append(table(["Ko'rsatkich", "Qiymat"], [
                ["Qabul qilinmagan reys", fmt(nr.get("qabul_qilinmagan") or 0, 0)],
                ["Rejadagi reys", fmt(nr.get("plan_reys") or 0, 0)],
                ["Amalda reys", fmt(nr.get("fact_reys") or 0, 0)],
                ["Rejadagi km", fmt(nr.get("plan_km") or 0, 1)],
                ["Amalda km", fmt(nr.get("fact_km") or 0, 1)],
                ["Yetib bormagan km", fmt(nr.get("diff") or 0, 1)],
            ]))
    except Exception:  # noqa: BLE001 - hisobot bo'lmasa kartani buzmaydi
        pass

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
    is_driver = chat_id is not None and resolve_role(chat_id) is Role.DRIVER
    kbs: list[dict | None] = []
    if is_driver:
        # Haydovchi o'z kartasida "Grafikim" tugmalarini ko'radi —
        # kechagi/bugungi/ertangi grafik rasmini shu yerdan oladi.
        kbs.append(kb.driver_schedule_kb(driver_id))
    else:
        kbs.append(kb.driver_card_kb(driver_id))
    kbs.append(_profile_bar(f))
    kbs.append(kb.nav_kb("nav:drivers", chat_id=chat_id))
    return "\n".join(parts), _combine(*kbs)


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
            f"{esc(route)} · {esc(plate)} · {_status_label(status)}\n"
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


def _pct_plain(value) -> str:
    """Emojisiz foiz (mono-jadval ichi uchun): `98%` / `-`."""
    p = pct(value)
    if p is None:
        return "-"
    return f"{p:g}%"


def _icon_by_index(value) -> str:
    """Sifat indeksi bo'yicha status emoji (95+/80+/past)."""
    p = pct(value)
    if p is None:
        return "⬜"
    if p >= 95:
        return "✅"
    if p >= 80:
        return "⚠️"
    return "❌"


def month(filters: dict | None = None, chat_id: int | None = None) -> tuple[str, dict]:
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
             _pct_plain(d["performance"])]
            for d in data["days"]
        ]
        t = data["totals"]
        trows.append(["JAMI", fmt(t["planned"], 0), fmt(t["accepted"], 0),
                  _pct_plain(t["performance"])])
        parts.append(table(["Sana", "Reja", "Qabul", "Foiz"], trows))
    m = _met()
    did = (f.get("driver_id") or f.get("driver") or "").strip()
    if did:
        b = _brutto(f, did)
    else:
        rows = m.drivers(f) or []
        b = {
            "gross_full": sum(float(r.get("gross_pay") or 0) for r in rows),
            "tax": sum(float(r.get("tax") or 0) for r in rows),
            "fines": sum(float(r.get("fines") or 0) for r in rows),
            "net_pay": sum(float(r.get("net_pay") or 0) for r in rows),
        }
    if b.get("gross_full") and _can_view_salary(chat_id):
        parts += [
            "",
            "<b>💰 ISH HAQI</b>",
            f"Brutto: {fmt(b['gross_full'], 0)}  |  Soliq: {fmt(b['tax'], 0)}  |  "
            f"Jarima: {fmt(b['fines'], 0)}  |  <b>Netto: {fmt(b['net_pay'], 0)}</b>",
        ]
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:month"))


# --------------------------------------------------------------- /brutto

def brutto(filters: dict | None = None, chat_id: int | None = None) -> tuple[str, dict]:
    """116-son qaror (32-band) bo'yicha tashuvchiga to'lov (brutto).

    Ma'lumot `metrics.drivers()` dan olinadi; km/reys saytdagi
    brutto-route (`route_daily` → AVTO qaydlari) asosida:
      Lf = amalda km (distance_fact), Lr = reja km (distance_plan;
      reja bo'lmasa Lf ga ishlatiladi), Kamal = reyslar soni,
      Kstjb = muammoli reyslar, Kmaq = 0.
    SKM `brutto_skm` sozlamasidan (default 16176 so'm/km).
    """
    f = parse_filters(filters or {})
    orig = filters or {}
    if (not str(f.get("month") or "").strip()
            and not str(orig.get("from") or "").strip()
            and not str(orig.get("to") or "").strip()
            and not str(orig.get("date") or "").strip()):
        f["month"] = date.today().strftime("%Y-%m")
    m = _met()
    rows = m.drivers(f) or []
    if not rows:
        return ("🧾 <b>BRUTTO-SHARTNOMA</b>\n"
                + f"Davr: {_period_label(f)}\n\nMa'lumot yo'q."), {}
    skm = brutto_skm()
    from ...core.bot_settings import route_skm

    # Per-route SKM: har bir haydovchi o'z yo'nalishi (firma) narxi bilan
    route_map = dict(
        (str(r.get("route_id") or ""),
         route_skm(str(r.get("route_id") or ""), skm) or skm)
        for r in rows if (r.get("km") or 0) > 0
    )
    contract = _Contract(skm=skm, route_number="JAMI",
                         carrier="Barcha haydovchilar",
                         valid_from=date(2020, 1, 1), valid_to=date(2099, 12, 31))
    calc = _BruttoCalculator(contract)
    source = DataFrame([{
        "sana": str(r.get("date") or f.get("month") or ""),
        "grafik": str(r.get("route_name") or r.get("route_id") or "-"),
        "davlat_raqami": str(r.get("driver_id") or "-"),
        "fio": str(r.get("name") or r.get("driver_id") or "-"),
        "lr": float(r.get("plan_km") or 0) or float(r.get("km") or 0) or 1.0,
        "lf": float(r.get("km") or 0),
        "kamal": int(r.get("trips") or 0),
        "kstjb": int(r.get("issues") or 0),
        "kmaq": 0,
        "skm": route_map.get(str(r.get("route_id") or ""), skm),
    } for r in rows if (r.get("km") or 0) > 0])
    results = calc.process_report(source)
    if not results:
        return ("🧾 <b>BRUTTO-SHARTNOMA</b>\n"
                + f"Davr: {_period_label(f)}\n\nMa'lumot yo'q."), {}
    agg = calc.aggregate(results)
    # Ixcham ro'yxat: eng katta to'lov tartibida, mobil ekranga mos
    ordered = sorted(results, key=lambda r: r.tolov, reverse=True)
    dlines = []
    for r in ordered[:24]:
        dlines.append(
            f"{_icon_by_index(r.sifat_index)} <b>{esc(short_name(str(r.fio)))}</b>"
            f" — {fmt(r.lf, 1)} km · {fmt(r.tolov, 0)} so'm"
        )
    if len(results) > 24:
        dlines.insert(0, f"… jami {len(results)} ta haydovchi:")
    parts = [
        "🧾 <b>BRUTTO-SHARTNOMA</b> (116-son, 32-band)",
        f"Davr: {_period_label(f)}",
        "",
        *dlines[:24],
        "",
        "<b>JAMI</b>",
        f"Haydovchilar: {agg['qatorlar']}  |  Lf: {fmt(agg['lf'], 1)} km"
        f"  |  Kamal: {fmt(agg['kamal'], 0)}",
        f"O'rtacha a: {fmt(agg['alpha'] * 100, 1)}%"
        f"  |  b: {fmt(agg['beta'] * 100, 1)}%"
        f"  |  g: {fmt(agg['gamma'] * 100, 1)}%",
        "",
        f"<b>TO'LOV: {fmt(agg['tolov'], 0)} so'm</b>",
        f"SKM: {fmt(skm, 0)} so'm/km",
    ]
    return "\n".join(parts), kb.nav_kb("nav:month")


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

def _driver_profile_settings(did: str, chat_id: int) -> str:
    """Haydovchi Settings: o'z profil ma'lumotlari (Dashboard'dan farqli).

    Bu yerda oylik statistika/ish qaydlari ko'rsatilmaydi — ular Dashboard
    bo'limida. Settings faqat shaxsiy profil (firma, hujjatlar, bildirishnoma,
    til) va kamchiliklar ro'yxatini beradi.
    """
    from .roles import driver_id_for_chat
    filters = context.filters_for(chat_id)
    f = _default_month(parse_filters(filters or {}), filters or {})
    # Haydovchi uchun `__deny__`/`__none__` sentinel'lari filtr sifatida
    # ishlatilmaydi — ma'lumot to'liq ko'rsatiladi.
    for _key in ("route", "profile"):
        if f.get(_key) in ("__deny__", "__none__"):
            f.pop(_key, None)
    data = _met().driver_detail(did, f)
    if not data:
        return "⚙️ <b>SETTINGS</b>\n\nHaydovchi profili topilmadi."
    d = data["driver"]
    p = data["profile"]
    is_driver = resolve_role(chat_id) is Role.DRIVER
    company = str(d.get("company") or "").strip()
    if company in ("none", "None"):
        company = ""
    rating = d.get("rating")
    parts = ["⚙️ <b>SETTINGS — MENING PROFILIM</b>",
             esc(short_name(str(d.get("name") or "-"))),
             f"🆔 ID: <code>{esc(d.get('driver_id'))}</code>"]
    if company:
        parts.append(f"🏢 Firma: {esc(company)}")
    parts.append(f"⭐ Reyting: {fmt(rating, 1) if rating is not None else '-'}")
    if p.get("phone"):
        parts.append(f"📱 Telefon: <code>{esc(p['phone'])}</code>")
    else:
        parts.append("📱 Telefon: ❌ kiritilmagan")

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

    notif = "🔔 Yoqilgan" if p.get("notification_enabled") else "🔕 O'chiq"
    parts += ["", f"🔔 Bildirishnoma: {notif}"]

    if is_driver:
        gaps = driver_shortcomings(data)
        gaps = [g for g in gaps if "Telegram bildirishnoma" not in g]
        if gaps:
            parts += ["", "⚠️ <b>KAMCHILIKLAR</b>"] + [f"  {g}" for g in gaps]

    try:
        from ...core.bot_settings import lang as _lang
        lang_txt = "🇷🇺 Русский" if _lang(chat_id) == "ru" else "🇺🇿 O'zbek"
        parts += ["", f"🌐 Til: {lang_txt}"]
    except Exception:  # noqa: BLE001 - til sozlamasi bo'lmasa e'tiborsiz
        pass
    return "\n".join(parts)


def settings_text(chat_id: int | None) -> str:
    role = resolve_role(chat_id)
    # Haydovchi: faqat o'z profili ko'rsatiladi (Dashboard statistikasi
    # emas) — `settings_kb` bilan birga yuboriladi.
    if role is Role.DRIVER and chat_id is not None:
        from .roles import driver_id_for_chat
        did = driver_id_for_chat(chat_id)
        if did:
            try:
                return _driver_profile_settings(str(did), chat_id)
            except Exception:  # noqa: BLE001 - profil chiqmasa umumiy matn
                log.warning("Haydovchi profili ko'rsatilmadi: did=%s", did)
    roles = configured_roles()
    admins = sum(1 for r in roles.values() if r is Role.ADMIN)
    dispatchers = sum(1 for r in roles.values() if r is Role.DISPATCHER)
    managers = sum(1 for r in roles.values() if r is Role.MANAGER)
    open_mode = not roles
    owned = context.allowed_names(chat_id) if chat_id is not None else []
    owned_txt = ", ".join(context.short_name(n) for n in owned) or "yo'q"
    show_finance = chat_id is not None and can(role, "salary")

    try:
        from ...core.bot_settings import (ELEC_KWH_PER_KM, brutto_skm,
                                           elec_price, lang)
        el = elec_price()
        skm = brutto_skm()
        lang_txt = "🇷🇺 Русский" if lang(chat_id) == "ru" else "🇺🇿 O'zbek"
    except Exception:  # noqa: BLE001 - sozlama bo'lmasa standart qiymat
        el, skm, lang_txt = 0.0, 16176.0, "🇺🇿 O'zbek"
        ELEC_KWH_PER_KM = 0.955
    el_txt = (f"{el:,.0f}".replace(",", " ") + " so'm"
              if el > 0 else "o'rnatilmagan")
    skm_txt = (f"{skm:,.0f}".replace(",", " ") + " so'm/km"
               if skm > 0 else "o'rnatilmagan")

    route_txt = ""
    if show_finance:
        try:
            from ...core.bot_settings import (route_skm as _route_skm,
                                              route_tariff as _route_tariff)
            from ...config.settings import km_rate_for as _route_rate
            f = context.filters_for(chat_id)
            rid = str(f.get("route") or "").strip()
            if rid:
                rid_first = rid.split()[0]
                t = _route_tariff(rid_first)
                rate = _route_rate(rid_first, 0.0)
                rate_txt = (f"{rate:,.0f}".replace(",", " ") + " so'm"
                            if rate > 0 else "o'rnatilmagan")
                lines = [
                    "",
                    "🏢 <b>FIRMA TARIFLARI</b>",
                    f"  Yo'nalish: {context.short_name(rid_first)}",
                ]
                lines.append(f"  Haydovchi 1 km: {rate_txt}")
                try:
                    rskm = _route_skm(rid_first, skm)
                    lines.append(f"  SKM: {rskm:,.0f} so'm/km"
                                 .replace(",", " "))
                except Exception:
                    pass
                if t.get("no_vat") or t.get("vat"):
                    lines.append(
                        f"  Tarif: {t.get('no_vat', 0):,.0f}".replace(",", " ")
                        + " (QQSsiz) / "
                        + f"{t.get('vat', 0):,.0f}".replace(",", " ")
                        + " (QQS)")
                route_txt = "\n".join(lines)
        except Exception:  # noqa: BLE001 - kichik bezak
            pass

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
    ]
    if show_finance:
        parts += [
            "",
            "⚡ <b>ELEKTR ENERGIYA</b>",
            f"  1 kVt/soat narxi: {el_txt}",
            f"  1 km iste'moli: {ELEC_KWH_PER_KM} kVt/soat",
            "",
            "📋 <b>116-SON QAROR (SKM)</b>",
            f"  1 mashina-km narxi: {skm_txt}",
        ]
        if route_txt:
            parts += route_txt.splitlines()
    parts += [
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


def settings_audit_text(chat_id: int | None, limit: int = 20) -> str:
    """`/settings audit` ekrani: sozlama o'zgarishlari tarixi."""
    from .roles import can
    role = resolve_role(chat_id)
    if not can(role, "salary"):
        return "⛔ Huquq yo'q."
    from ...core.bot_settings import audit_log
    rows = audit_log(limit)
    lines = ["🕐 <b>SETTINGS TARIXI</b>\n"]
    if not rows:
        lines.append("Hozircha o'zgarishlar yo'q.")
        return "\n".join(lines)
    for r in rows:
        key = str(r.get("key") or "?")
        old, new = r.get("old"), r.get("new")
        by = r.get("by") or "dashboard"
        lines.append(
            f"<b>{key}</b>\n"
            f"  {old} → <b>{new}</b>\n"
            f"  🕐 {r.get('when')} | 👤 {by}"
        )
    return "\n".join(lines)


def settings_audit_csv(chat_id: int | None, limit: int = 200) -> str:
    """`/settings audit excel` — sozlamalar tarixini Excel'da ochiladigan
    CSV ko'rinishida qaytaradi."""
    from .roles import can
    role = resolve_role(chat_id)
    if not can(role, "salary"):
        return "⛔ Huquq yo'q."
    from ...core.bot_settings import audit_log
    rows = audit_log(limit)

    def _cell(v) -> str:
        s = "" if v is None else str(v)
        if any(c in s for c in (';', '"', '\n')):
            return '"' + s.replace('"', '""') + '"'
        return s

    lines = ["sep=;", "Key;Eski qiymat;Yangi qiymat;Vaqt;O'zgartiruvchi"]
    for r in rows:
        lines.append(";".join([
            _cell(r.get("key")),
            _cell(r.get("old")),
            _cell(r.get("new")),
            _cell(r.get("when")),
            _cell(r.get("by") or "dashboard"),
        ]))
    return "\n".join(lines)

# ------------------------------------------------------------ /daily_package

def daily_package(filters: dict | None = None,
                  full: bool = False) -> tuple[str, dict]:
    """Kunlik avto-paket (18:00 xulosa / 22:00 toliq paket).

    `full=False` → 18:00: bugungi holat + muammolar + reyslar
    `full=True`  → 22:00: oy tarixi + muammolar + reyslar

    Ops poll sikli soatga qarab `full` ni quradi va `/daily_package`
    orqali hub-spetsifik chat'larga avto-yuboradi.
    """
    f = parse_filters(filters or {})
    parts: list[str] = []
    kbs: list[dict | None] = []

    if full:
        text, kb = month(f)
        parts.append(text)
        kbs.append(kb)
    else:
        text, kb = today(f)
        parts.append(text)
        kbs.append(kb)

    text, kb = problems(f)
    parts.append(text)
    kbs.append(kb)

    text, kb = trips(f)
    parts.append(text)
    kbs.append(kb)

    return "\n\n".join(parts), _combine(*kbs) or {}

# ------------------------------------------------------------ /daily_package

def daily_package(filters: dict | None = None,
                  full: bool = False) -> tuple[str, dict]:
    """Kunlik avto-paket (Blok 1).

    - `full=False` → 18:00 qisqa xulosa: bugungi holat + muammolar + reyslar
    - `full=True`  → 22:00 to'liq paket: oy tarixi + muammolar + reyslar

    Ops poll sikli soatga qarab `full` ni o'zi belgilaydi (18:00/22:00).
    """
    f = parse_filters(filters or {})
    parts: list[str] = []
    kbs: list[dict | None] = []

    fn = month if full else today
    text, kb = fn(f)
    parts.append(text)
    kbs.append(kb)

    text, kb = problems(f)
    parts.append(text)
    kbs.append(kb)

    text, kb = trips(f)
    parts.append(text)
    kbs.append(kb)

    return "\n".join(parts), _combine(*kbs) or {}
