"""Oy oxiri jarima hisoboti — har bir haydovchi bo'yicha qisqa xulosa.

Har oyning oxirgi kunida ADMIN'larga global, har kompaniya chatlariga esa
faqat o'z yo'nalishlari bo'yicha yuboriladi: haydovchi, faol jarimalar soni
va jami summa (kamayish bo'yicha tartiblangan).

Bot poll-tsikli har iteratsiyada `check_and_send()` chaqiradi; u kuniga bir
marta tekshiradi (in-memory interval), oyning oxirgi kunida bir marta
yuboradi (holat fayli bilan — bot restart'da takroriy spam bo'lmaydi).

Holat `state/fines_report.json`:
  {"month": "YYYY-MM"}  # qaysi oy uchun yuborilgani

`FINES_REPORT=off` bilan o'chiriladi. Qo'lda yuborish: `/finesreport`.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

from ...config.settings import telegram_settings
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.tgformat import fmt, table
from ..telegram import send_message
from . import context
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "fines_report.json"

# Sohaviy tekshiruvlar orasidagi interval (kuniga bir marta uchun yetarli).
_CHECK_INTERVAL_S = 300.0

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0  # in-memory hisoblagich (restart'da nollanadi)


def enabled() -> bool:
    s = telegram_settings()
    return str(s.get("fines_report", "on")).lower() not in ("off", "0", "false")


def _last_day_of_month(today: date) -> int:
    """Oyning oxirgi kuni (28–31)."""
    from calendar import monthrange
    return monthrange(today.year, today.month)[1]


def _month_bounds(today: date) -> tuple[str, str]:
    """Bugungi oyning birinchi va oxirgi kuni (YYYY-MM-DD)."""
    last_day = _last_day_of_month(today)
    return today.replace(day=1).isoformat(), \
        today.replace(day=last_day).isoformat()


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - holat yo'q bo'lsa boshidan
        pass
    return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - hisobotni buzmaydi
        print(f"Jarima hisoboti holati saqlanmadi: {exc}")


def _targets() -> list[int]:
    """Global (barcha kompaniyalar) hisobot — faqat ADMIN'lar."""
    roles = configured_roles()
    return [cid for cid, role in roles.items() if role is Role.ADMIN]


def _company_from_filters(filters: dict | None) -> str:
    f = filters or {}
    name = str(f.get("company") or f.get("profile") or "").strip()
    return "" if name in ("__none__", "__deny__") else name


def build_text(month: str = "", company: str = "") -> str | None:
    """Oylik jarima hisoboti matni; haydovchi bo'lmasa None.

    `company` berilsa — faqat shu kompaniya yo'nalishlarida ishlagan
    haydovchilarning jarimalari (schedules orqali bog'lanadi).
    """
    st = get_storage()
    if not st.enabled:
        return None
    if not month:
        month = date.today().strftime("%Y-%m")
    ph = st.db.ph
    params: list = [month]
    where = ("substr(f.date, 1, 7) = " + ph
             + " AND UPPER(COALESCE(f.status, 'ACTIVE')) = 'ACTIVE'")
    if company:
        from ...dashboard.metrics import parse_filters
        try:
            routes = str(parse_filters({"company": company}).get("route") or "")
        except Exception:  # noqa: BLE001
            routes = ""
        rids = [r.strip() for r in routes.replace(",", " ").split() if r.strip()]
        if not rids:
            return None
        marks = ", ".join(ph for _ in rids)
        where += (" AND EXISTS (SELECT 1 FROM schedules s "
                  "WHERE s.driver_id = f.driver_id "
                  "AND substr(s.date, 1, 7) = substr(f.date, 1, 7) "
                  f"AND s.route_id IN ({marks}))")
        params.extend(rids)
    try:
        rows = st.query(
            "SELECT f.driver_id, COALESCE(d.full_name, '') AS name, "
            "COUNT(*) AS cnt, SUM(f.amount) AS total "
            f"FROM driver_fines f LEFT JOIN drivers d ON d.external_id = f.driver_id "
            f"WHERE {where} GROUP BY f.driver_id, name "
            f"ORDER BY total DESC", tuple(params), limit=1000)
    except Exception as exc:  # noqa: BLE001 - DB xatosi bo'sh hisobot
        print(f"Jarima hisoboti o'qilmadi: {exc}")
        return None
    rows = rows or []
    if not rows:
        return None

    total_all = sum(float(r.get("total") or 0) for r in rows)
    cnt_all = sum(int(r.get("cnt") or 0) for r in rows)
    title = f"{month} OYLIK JARIMA HISOBOTI"
    if company:
        title += f" — {company}"
    parts = [
        f"⚠️ <b>{title}</b>",
        "",
        f"Haydovchi soni: {len(rows)} · Jarimalar: {cnt_all} ta",
        f"💸 <b>JAMI: {fmt(total_all, 0)} so'm</b>",
        "",
    ]
    body = table(["Haydovchi", "Soni", "Summa"], [
        [str(r.get("name") or r.get("driver_id") or "-"),
         fmt(r.get("cnt") or 0, 0), fmt(r.get("total") or 0, 0)]
        for r in rows[:30]
    ], max_width=60)
    parts.append(body)
    if len(rows) > 30:
        parts.append(f"… va yana {len(rows) - 30} haydovchi")
    return "\n".join(parts)


def check_and_send() -> None:
    """Oyning oxirgi kunida bir marta ADMIN/DISPATCHER'ga yuboradi."""
    global _LAST_CHECK_AT
    if not enabled() or not get_storage().enabled:
        return
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    today = date.today()
    if today.day != _last_day_of_month(today):
        return
    with _LOCK:
        month = today.strftime("%Y-%m")
        if _load().get("month") == month:
            return
        admins = _targets()
        global_text = build_text(month)
        # Har kompaniya faqat o'z jarimalarini oladi.
        by_company: dict[str, tuple[list[int], str]] = {}
        for name in context.company_names():
            chats = [c for c in context.chats_for_company(name)
                     if c not in admins]
            if not chats:
                continue
            ctext = build_text(month, name)
            if ctext:
                by_company[name] = (chats, ctext)
        if not global_text and not by_company:
            return
        _save({"month": month})
        if global_text:
            for cid in admins:
                try:
                    send_message(global_text, chat_id=str(cid))
                except Exception as exc:  # noqa: BLE001
                    print(f"Jarima hisoboti yuborilmadi [{cid}]: {exc}")
        for chats, ctext in by_company.values():
            for cid in chats:
                try:
                    send_message(ctext, chat_id=str(cid))
                except Exception as exc:  # noqa: BLE001
                    print(f"Jarima hisoboti yuborilmadi [{cid}]: {exc}")


def send_now(filters: dict | None = None) -> str:
    """Qo'lda yuborish (`/finesreport`) — so'rovchi doirasidagi matn."""
    company = _company_from_filters(filters)
    text = build_text(company=company) if company else build_text()
    if not text:
        return "⚠️ Jarima hisoboti tayyorlanmadi (DB o'chirilgan yoki jarimalar yo'q)."
    return text


def status_text() -> str:
    """Holat matni (`/alerts` ekranida ko'rsatilishi mumkin)."""
    s = _load()
    today = date.today()
    sent = s.get("month") == today.strftime("%Y-%m")
    mode = "✅ yonilgan" if enabled() else "⛔ o'chirilgan"
    state = "✓ bu oy yuborilgan" if sent else "bu oy hali yuborilmagan"
    return "\n".join([
        "⚠️ <b>OYLIK JARIMA HISOBOTI</b>",
        "",
        "Oyning oxirgi kunida har bir haydovchi bo'yicha faol jarimalar",
        "jami ADMIN/DISPATCHER'ga avtomatik yuboriladi.",
        f"Rejim: {mode} · Holat: {state}",
        f"So'nggi yuborilgan oy: {s.get('month') or '—'}",
        "",
        "Qo'lda yuborish: <b>/finesreport</b>",
    ])
