"""Buyruqlar va callback'larni yo'naltirish (role-gated).

`handle_message` — matnli buyruqlar va reply-menyu tugmalari;
`handle_callback` — inline tugmalar (nav / prob / sync / exp / rs).
"""

from __future__ import annotations

import threading
from datetime import date as _date
from typing import Any

from ...db.models import TripStatus
from ...db.storage import get_storage
from ...utils.logger import get_logger
from ..telegram import telegram_call
from . import context, kb, legacy, render
from .roles import can, resolve_role
from .text import (DENIED_TEXT, HELP_TEXT, SYNC_STARTED, WELCOME_TEXT)

log = get_logger("bm_automation.bot")

# Reply-menyu tugmalari matni -> handler nomi
MENU_MAP = {
    "📊 dashboard": "today",
    "🚌 avtobuslar": "vehicles",
    "👨‍✈️ haydovchilar": "drivers",
    "🛣 yo'nalishlar": "routes",
    "📋 reyslar": "trips",
    "⚠️ muammolar": "problems",
    "📈 hisobot": "reports",
    "🔄 sync": "sync",
    "🏢 firmalar": "profiles",
    "⚙️ settings": "settings",
    "❓ yordam": "help",
    "❓ help": "help",
}


def reply(chat_id: int, text: str, reply_markup=None) -> None:
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)
    except Exception as exc:
        log.warning("sendMessage xatosi: %s", exc)


def answer(cq: dict, text: str = "") -> None:
    try:
        telegram_call("answerCallbackQuery", {
            "callback_query_id": cq["id"], "text": text})
    except Exception:
        pass


# ------------------------------------------------------------- sync

def _sync_summary(results: list) -> str:
    lines = ["🔄 <b>Sinxronlash tugadi</b>", ""]
    for r in results:
        ent = getattr(r, "entity", r)
        ins = getattr(r, "inserted", 0)
        upd = getattr(r, "updated", 0)
        err = getattr(r, "error", "")
        base = f"  {ent}: +{ins} yangi / {upd} yangilangan"
        lines.append(base + (f"  ⚠️ {err}" if err else ""))
    return "\n".join(lines)


def _do_sync(chat_id: int) -> None:
    try:
        from ...db import sync as dsync

        storage = get_storage()
        results = [dsync.sync_statuses(storage), dsync.sync_profiles(storage)]

        try:
            client = legacy.ensure_client()
            results.append(dsync.sync_routes(storage, client))
        except Exception as exc:  # noqa: BLE001 - api xatosi syncni buzmaydi
            results.append(type("R", (), {"entity": "api", "inserted": 0,
                                          "updated": 0, "error": str(exc)})())

        try:
            client = legacy.ensure_client()
            date_str = _date.today().isoformat()
            results += dsync.sync_all_profiles(storage, client, date_str)
        except Exception as exc:  # noqa: BLE001
            storage.record_error(source="bot.sync", message=str(exc))

        reply(chat_id, _sync_summary(results), reply_markup=kb.nav_kb("nav:sync"))
    except Exception as exc:  # noqa: BLE001
        storage = get_storage()
        storage.record_error(source="bot.sync", message=str(exc))
        reply(chat_id, f"Sync xatosi: {exc}")


def start_sync(chat_id: int) -> None:
    reply(chat_id, SYNC_STARTED)
    threading.Thread(target=_do_sync, args=(chat_id,), daemon=True).start()


# ------------------------------------------------------------- export

_SCOPES = ("today", "routes", "vehicles", "drivers", "trips", "all")
_FORMATS = ("csv", "xlsx", "pdf")


def _parse_export_args(parts: list[str]) -> tuple[str, str]:
    fmt, scope = "xlsx", "all"
    for tok in parts[1:]:
        t = tok.lower()
        if t in _FORMATS:
            fmt = t
        elif t in _SCOPES:
            scope = t
    return fmt, scope


def _do_export(chat_id: int, fmt: str, scope: str) -> None:
    try:
        from ...dashboard.export import build_export, filename
        from ...dashboard.metrics import parse_filters

        f = parse_filters({})
        data = build_export(f, fmt, scope)
        name = filename(fmt, scope, f.get("date", ""))
        reply(chat_id, f"📎 Eksport tayyor: {name}")
        from ..telegram import send_bytes
        send_bytes(name, data, caption=f"Dashboard eksport ({scope})",
                   chat_id=chat_id)
    except Exception as exc:  # noqa: BLE001
        reply(chat_id, f"Eksport xatosi: {exc}")


def start_export(chat_id: int, fmt: str, scope: str) -> None:
    reply(chat_id, "📤 Eksport tayyorlanmoqda...")
    threading.Thread(target=_do_export, args=(chat_id, fmt, scope),
                     daemon=True).start()


# ------------------------------------------------------------ /trips args

def _trip_filters(parts: list[str]) -> dict:
    f: dict[str, str] = {}
    for tok in parts[1:]:
        tok = tok.strip()
        if not tok:
            continue
        try:
            _date.fromisoformat(tok)
            f["date"] = tok
            continue
        except ValueError:
            pass
        up = tok.upper()
        if TripStatus.is_valid(up):
            f["status"] = up
    return f


# --------------------------------------------------------------- routing

def handle_message(chat_id: int, text: str) -> None:
    text = text.strip()
    if not text:
        return
    low = text.lower()
    parts = text.split()

    cmd = MENU_MAP[low] if low in MENU_MAP and not low.startswith("/") else \
        parts[0].lower().replace("/", "", 1)

    role = resolve_role(chat_id)

    if cmd in ("start", "help"):
        reply(chat_id, WELCOME_TEXT if cmd == "start" else HELP_TEXT,
              reply_markup=kb.main_menu_kb())
        return

    f = context.filters_for(chat_id)

    if cmd == "today":
        reply(chat_id, *render.today(f))
        return
    if cmd == "profiles":
        reply(chat_id, *render.profiles(f))
        return
    if cmd == "routes":
        reply(chat_id, *render.routes(f))
        return
    if cmd == "vehicles":
        reply(chat_id, *render.vehicles(f))
        return
    if cmd == "drivers":
        reply(chat_id, *render.drivers(f))
        return
    if cmd == "trips":
        reply(chat_id, *render.trips(_trip_filters(parts) | f))
        return
    if cmd == "problems":
        reply(chat_id, *render.problems(f))
        return
    if cmd == "reports":
        reply(chat_id, *render.reports())
        return
    if cmd == "errors":
        reply(chat_id, *render.errors())
        return
    if cmd == "status":
        reply(chat_id, *render.status())
        return
    if cmd == "settings":
        reply(chat_id, render.settings_text(chat_id),
              reply_markup=kb.nav_kb("nav:settings"))
        return
    if cmd == "list":
        reply(chat_id, legacy.list_text())
        return

    if cmd == "sync":
        if not can(role, "sync"):
            reply(chat_id, DENIED_TEXT)
            return
        reply(chat_id, "🔄 Sinxronlash bosilsinmi?",
              reply_markup=kb.sync_confirm_kb())
        return

    if cmd == "export":
        if not can(role, "export"):
            reply(chat_id, DENIED_TEXT)
            return
        fmt, scope = _parse_export_args(parts)
        start_export(chat_id, fmt, scope)
        return

    if cmd == "resend":
        if not can(role, "resend"):
            reply(chat_id, DENIED_TEXT)
            return
        name = " ".join(parts[1:]).strip() or None
        legacy.start_resend(chat_id, name)
        return

    reply(chat_id, HELP_TEXT, reply_markup=kb.main_menu_kb())


def handle_callback(chat_id: int, cq: dict, data: str) -> None:
    role = resolve_role(chat_id)
    f = context.filters_for(chat_id)

    if data.startswith("prof:"):
        name = data[5:]
        if name in ("", "__all__"):
            context.clear(chat_id)
            answer(cq, "Hamma kompaniyalar ko'rsatiladi")
            reply(chat_id, *render.profiles(context.filters_for(chat_id)))
            return
        try:
            context.set_active(chat_id, name)
        except ValueError as exc:
            answer(cq, str(exc))
            return
        answer(cq, f"{context.short_name(name)} tanlandi")
        reply(chat_id, *render.today(context.filters_for(chat_id)))
        return

    if data.startswith("nav:"):
        target = data[4:]
        if target == "sync":
            if not can(role, "sync"):
                answer(cq, "Huquq yo'q")
                reply(chat_id, DENIED_TEXT)
                return
            answer(cq, "Sinxronlash")
            reply(chat_id, "🔄 Sinxronlash bosilsinmi?",
                  reply_markup=kb.sync_confirm_kb())
            return
        if target == "profiles":
            answer(cq)
            reply(chat_id, *render.profiles(f))
            return
        if target == "settings":
            answer(cq)
            reply(chat_id, render.settings_text(chat_id),
                  reply_markup=kb.nav_kb("nav:settings"))
            return
        handlers = {
            "dashboard": render.today,
            "vehicles": render.vehicles,
            "drivers": render.drivers,
            "routes": render.routes,
            "trips": render.trips,
            "problems": render.problems,
            "reports": render.reports,
        }
        answer(cq)
        reply(chat_id, *handlers[target](f))
        return

    if data.startswith("prob:"):
        key = data[5:]
        if key not in render.PROBLEM_LABELS:
            answer(cq, "Kategoriya topilmadi")
            return
        answer(cq, "Batafsil")
        reply(chat_id, *render.problem_category(key, f))
        return

    if data == "sync:confirm":
        if not can(role, "sync"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        answer(cq, "Boshlandi")
        start_sync(chat_id)
        return
    if data == "sync:cancel":
        answer(cq, "Bekor qilindi")
        reply(chat_id, "Sinxronlash bekor qilindi.")
        return

    if data.startswith("exp:"):
        legacy.handle_export_callback(chat_id, cq, data[4:])
        return

    if data.startswith("rs:"):
        if not can(role, "resend"):
            answer(cq, "Huquq yo'q")
            return
        name = data[3:]
        if name == "ALL":
            name = None
        answer(cq, "Boshlandi")
        legacy.start_resend(chat_id, name)
        return

    answer(cq)


def handle_document(chat_id: int, doc: dict) -> None:
    role = resolve_role(chat_id)
    if not can(role, "document"):
        print(f"Ruxsatsiz chat document yubordi: {chat_id} ({role.value})")
        reply(chat_id, DENIED_TEXT)
        return
    filename = (doc.get("file_name") or "").strip()
    if not filename.lower().endswith(".xlsx"):
        reply(chat_id, "Faqat .xlsx fayl qabul qilinadi (sayt export formati).")
        return
    file_id = doc.get("file_id")
    if not file_id:
        reply(chat_id, "Fayl ID olinmadi.")
        return
    reply(chat_id, "Fayl qabul qilindi, ishlanmoqda...")
    threading.Thread(target=legacy.process_document,
                     args=(chat_id, file_id, filename), daemon=True).start()
