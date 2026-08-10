"""Ko'rsatkichlarni Telegram matniga aylantirish (render).

Har bir funksiya `(text, reply_markup)` qaytaradi; `reply_markup` inline
navigatsiya (muammo tugmalari / nav) yoki `None`.
"""

from __future__ import annotations

from typing import Any

from ...dashboard.metrics import Metrics, parse_filters
from ...db.models import TripStatus
from ...db.storage import get_storage
from ...utils.logger import get_logger
from . import context, kb
from .roles import Role, configured_roles, resolve_role, role_label

log = get_logger("bm_automation.bot")


def _met() -> Metrics:
    return Metrics()


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
    text = (
        f"{title} — {t['date']}\n\n"
        "🚌 <b>AVTOBUSLAR</b>\n"
        f"{t['total_buses']} jami\n"
        f"{t['active_buses']} faol\n"
        f"{t['not_active_buses']} muammoli\n\n"
        "📋 <b>REYSLAR</b>\n"
        f"{t['total_trips']} rejalashtirilgan\n"
        f"{t['completed']} bajarilgan\n"
        f"{t['accepted']} qabul qilingan\n"
        f"{t['not_accepted']} qabul qilinmagan\n\n"
        "⚠️ <b>MUAMMOLAR</b>\n"
        f"{c['gps']} GPS\n"
        f"{c['technical']} texnik\n"
        f"{c['schedule']} jadval\n"
        f"{c['unknown']} noma'lum"
    )
    return text, _combine(kb.problems_kb(p), _profile_bar(f), kb.nav_kb("nav:sync"))


# --------------------------------------------------------------- /problems

def _problem_lines(items: list[dict], limit: int = 15) -> list[str]:
    lines = []
    for it in items[:limit]:
        lines.append(
            f"  {it['planned_time'] or '--:--'} | {it['vehicle']} | "
            f"{it['route']} | {it['driver']} | {it['status']}")
    if len(items) > limit:
        lines.append(f"  ... yana {len(items) - limit} ta")
    return lines


def problems(filters: dict | None = None) -> tuple[str, dict]:
    f = parse_filters(filters)
    m = _met()
    p = m.problems(f)
    c = p["counts"]
    title = f"⚠️ <b>MUAMMOLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [f"{title} — {f['date']}",
             f"Jami: {sum(c.values())} ta"]
    for key, label in PROBLEM_LABELS.items():
        items = p["items"][key]
        parts.append("")
        parts.append(f"<b>{label} ({len(items)})</b>")
        if items:
            parts.extend(_problem_lines(items))
        else:
            parts.append("  yo'q")
    return "\n".join(parts), _combine(kb.problems_kb(p), _profile_bar(f), kb.nav_kb("nav:sync"))


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
        f"{title} — {f['date']}",
        f"Jami: {len(items)} ta",
        "",
    ]
    if not items:
        parts.append("  yo'q ✓")
    for i, it in enumerate(items[:20], 1):
        line = (f"{i}. {it['planned_time'] or '--:--'} | {it['vehicle'] or '-'}\n"
                f"   Yo'nalish: {it['route'] or '-'}\n"
                f"   Haydovchi: {it['driver'] or '-'}\n"
                f"   Holat: {it['status'] or '-'}")
        if it.get("actual_time"):
            line += f"\n   Haqiqiy: {it['actual_time']}"
        parts.append(line)
    if len(items) > 20:
        parts.append(f"... yana {len(items) - 20} ta")
    return "\n".join(parts), _combine(_profile_bar(f), kb.back_kb("nav:problems"))


# ---------------------------------------------------------------- /routes

def routes(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().routes(f)
    title = f"🛣 <b>YO'NALISHLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [f"{title} — {f['date']}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    for r in rows[:limit]:
        parts.append(
            f"<b>{r['name']}</b>\n"
            f"  plan {r['planned']} · amalga {r['actual']} · "
            f"qabul {r['accepted']} · rad {r['rejected']} · "
            f"perf {r['performance']}%")
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta yo'nalish")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:routes"))


# -------------------------------------------------------------- /vehicles

def vehicles(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().vehicles(f)
    title = f"🚌 <b>AVTOBUSLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [f"{title} — {f['date']}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    for v in rows[:limit]:
        icon = "🟢" if v["status"] == "faol" else "🔴"
        parts.append(
            f"{icon} {v['plate_number']} · {v['route_id'] or '-'}\n"
            f"  {v['trips']} reys · muammo {v['issues']} · "
            f"{v['last_activity']}")
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta avtobus")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:vehicles"))


# --------------------------------------------------------------- /drivers

def drivers(filters: dict | None = None, limit: int = 25) -> tuple[str, dict]:
    f = parse_filters(filters)
    rows = _met().drivers(f)
    title = f"👨‍✈️ <b>HAYDOVCHILAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [f"{title} — {f['date']}", ""]
    if not rows:
        parts.append("Ma'lumot yo'q.")
    for d in rows[:limit]:
        parts.append(
            f"{d['name']}\n"
            f"  {d['trips']} reys · {d['working_days']} kun · "
            f"qatnash {d['attendance']}% · muammo {d['issues']}")
    if len(rows) > limit:
        parts.append(f"... yana {len(rows) - limit} ta haydovchi")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:drivers"))


# ----------------------------------------------------------------- /trips

def _trip_line(t: dict) -> str:
    status = t.get("status") or ""
    icon = {"ACCEPTED": "✅", "APPROVED": "✅", "NOT_ACCEPTED": "❌",
            "REJECTED": "⛔", "ZERO_MILEAGE": "0️⃣", "PENDING_ACCESS": "⏳"}
    return (f"{icon.get(status, '❓')} {t.get('planned_time') or '--:--'} | "
            f"{t.get('route_id') or '-'} | {t.get('vehicle_id') or '-'} | "
            f"{t.get('driver_id') or '-'} | {status}")


def trips(filters: dict | None = None, limit: int = 15) -> tuple[str, dict]:
    f = parse_filters(filters)
    m = _met()
    rows = m.trips(f, limit=limit)
    total = m.today(f)["total_trips"]
    title = f"📋 <b>REYSLAR</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    parts = [f"{title} — {f['date']}", f"Jami: {total} ta"]
    for t in rows:
        parts.append(_trip_line(t))
    if total > limit:
        parts.append(f"... jami {total} ta (ko'rish uchun /export yoki /problems)")
    return "\n".join(parts), _combine(_profile_bar(f), kb.nav_kb("nav:trips"))


# --------------------------------------------------------------- /reports

def reports(filters: dict | None = None, limit: int = 15) -> tuple[str, dict]:
    st = get_storage()
    rows = st.list_reports(limit=limit)
    parts = ["📈 <b>HISOBOTLAR</b>", ""]
    if not rows:
        parts.append("Hisobotlar yo'q. /sync yoki CLI orqali yarating.")
    for r in rows:
        fpath = r.get("file_path") or "-"
        parts.append(
            f"• <b>{r.get('name')}</b> ({r.get('report_type') or '-'})\n"
            f"  davr: {r.get('period_date') or '-'} · {r.get('updated_at') or ''}\n"
            f"  fayl: {fpath}")
    runs = st.list_runs(limit=3)
    if runs:
        parts.append("")
        parts.append("So'nggi avto-ishlar:")
        for run in runs:
            parts.append(f"  {run.get('started_at')} → {run.get('status')} "
                         f"[{run.get('trigger')}]")
    return "\n".join(parts), _combine(kb.nav_kb("nav:reports"))


# --------------------------------------------------------------- /profiles

def profiles(filters: dict | None = None) -> tuple[str, dict]:
    """Kompaniyalar ro'yxati (tanlash ekrani)."""
    f = parse_filters(filters or {})
    active = f.get("profile", "")
    plist = context.profiles_with_routes()
    parts = [
        "🏢 <b>FIRMALAR</b>",
        "",
        "Kompaniyani tanlang — uning ma'lumotlari alohida ko'rsatiladi:",
        "",
    ]
    for p in plist:
        mark = " ✅" if p["name"] == active else ""
        parts.append(
            f"• {p['label']}{mark} — {p['route_name'] or "yo'nalishi yo'q"}")
    if not plist:
        parts.append("Kompaniyalar yo'q. Profil qo'shish kerak (profiles.json).")
    if active:
        parts.append("")
        parts.append("Chiqish uchun quyidagi 'Barchasi / Chiqish' tugmasini bosing.")
    return "\n".join(parts), kb.profiles_kb(plist, active)


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
            f"  {str(e.get('message') or '')[:200]}")
    return "\n".join(parts), _combine(kb.nav_kb("nav:sync"))


# ---------------------------------------------------------------- /status

def status(system: dict | None = None) -> tuple[str, dict]:
    m = _met()
    sysd = system or m.system()
    st = get_storage()
    counts = st.counts()

    api = sysd.get("api", {})
    api_txt = "OK" if api.get("ok") else "XATO"
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
                     f"{str(le.get('message') or '')[:100]}")
    return "\n".join(parts), _combine(kb.nav_kb("nav:sync"))


# -------------------------------------------------------------- /settings

def settings_text(chat_id: int | None) -> str:
    role = resolve_role(chat_id)
    roles = configured_roles()
    admins = sum(1 for r in roles.values() if r is Role.ADMIN)
    dispatchers = sum(1 for r in roles.values() if r is Role.DISPATCHER)
    open_mode = not roles
    parts = [
        "⚙️ <b>SETTINGS</b>",
        "",
        f"Sizning rolingiz: {role_label(role)}",
        "",
        "Rollar (chat_id bo'yicha):",
        f"  ADMIN: {admins} ta",
        f"  DISPATCHER: {dispatchers} ta",
        f"  Boshqalar: VIEWER (standart)" if not open_mode else "  Ochiq rejim (hamma ADMIN)",
    ]
    return "\n".join(parts)
