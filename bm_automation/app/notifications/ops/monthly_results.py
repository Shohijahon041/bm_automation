"""Oylik natijalar bildirishnomalari — haydovchilar shaxsiy chatiga eslatma.

Har oyning 1-kunida Telegram'ga bog'langan haydovchilarga o'tgan oy
natijalari avtomatik yuboriladi (`driver_inbox` render — Brutto/Netto
`chat_id` gating orqali haydovchidan yashiriladi).

Bot poll-tsikli har iteratsiyada `check_and_send()` chaqiradi; u bir oyda
bir marta (holat fayli bilan) ishlaydi — bot restart'da takroriy spam
bo'lmaydi.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

from ...db.storage import get_storage
from ...utils.io import atomic_write
from ..telegram import send_message
from .render import driver_inbox

STATE_FILE = Path("state") / "monthly_results.json"

# Tekshiruvlar orasidagi eng kichik interval (soniya).
_CHECK_INTERVAL_S = 3600.0

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0  # in-memory interval hisoblagichi (restart'da nollanadi)


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
    except Exception as exc:  # noqa: BLE001 - bildirishnomani buzmaydi
        print(f"Oylik natijalar holati saqlanmadi: {exc}")


def _prev_month(today: date) -> str:
    """O'tgan oy nomi (YYYY-MM)."""
    y, m = today.year, today.month
    if m == 1:
        return f"{y - 1:04d}-12"
    return f"{y:04d}-{m - 1:02d}"


def _recipients() -> list[dict]:
    """Telegram'ga bog'langan va bildirishnoma yoqilgan haydovchilar."""
    st = get_storage()
    if not st.enabled:
        return []
    try:
        rows = st.query(
            "SELECT driver_id, telegram_chat_id, notification_enabled "
            "FROM driver_profiles "
            "WHERE telegram_chat_id <> '' AND notification_enabled = 1 "
            "AND blacklisted = 0")
        return rows or []
    except Exception:  # noqa: BLE001 - DB xatosi xabar emas
        return []


def _send_to(chat_id: str, text: str) -> None:
    try:
        send_message(text, chat_id=str(chat_id))
    except Exception as exc:  # noqa: BLE001
        print(f"Oylik natijalar bildirishnomasi yuborilmadi [{chat_id}]: {exc}")


def check_and_send() -> None:
    """Har oy 1-kunida o'tgan oy natijalarini haydovchilarga yuboradi."""
    global _LAST_CHECK_AT
    st = get_storage()
    if not st.enabled:
        return
    with _LOCK:
        now = time.time()
        if now - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
            return
        _LAST_CHECK_AT = now

        today = date.today()
        if today.day != 1:
            return

        month = _prev_month(today)
        if _load().get("month") == month:
            return

        for profile in _recipients():
            try:
                did = str(profile.get("driver_id") or "").strip()
                chat_id = str(profile.get("telegram_chat_id") or "").strip()
                if not did or not chat_id:
                    continue
                text, _ = driver_inbox(did, {"month": month}, chat_id=int(chat_id) if chat_id.isdigit() else None)
                if not text:
                    continue
                _send_to(chat_id, text)
            except Exception as exc:  # noqa: BLE001
                print(f"Oylik natijalar tekshiruvi xatosi: {exc}")

        _save({"month": month})


def status_text() -> str:
    """`/monthly` ekrani uchun holat matni."""
    state = _load()
    last = state.get("month") or ""
    return "\n".join([
        "📊 <b>OYLIK NATIJALAR</b>",
        "",
        "Har oyning 1-kunida Telegram'ga bog'langan haydovchilarga",
        "o'tgan oy natijalari avtomatik yuboriladi.",
        f"So'nggi yuborilgan oy: {last or '—'}",
    ])