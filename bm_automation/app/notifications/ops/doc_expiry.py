"""Hujjat muddati bildirishnomalari — haydovchilar shaxsiy chatiga eslatma.

Telegram'ga bog'langan va bildirishnoma yoqilgan haydovchilarning passport
yoki guvohnoma hujjat muddati yaqinlashgan (30 kundan kam) yoki o'tib ketgan
bo'lsa, o'sha haydovchining shaxsiy chatiga qisqa bildirishnoma yuboriladi.

Bot poll-tsikli har iteratsiyada `check_and_send()` chaqiradi; u kuniga bir
marta (holat fayli bilan) ishlaydi — bot restart'da takroriy spam bo'lmaydi.
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
from .render import _expiry_state

STATE_FILE = Path("state") / "doc_expiry.json"

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
        print(f"Hujjat muddati holati saqlanmadi: {exc}")


def _recipients() -> list[dict]:
    """Telegram'ga bog'langan va bildirishnoma yoqilgan haydovchilar."""
    st = get_storage()
    if not st.enabled:
        return []
    try:
        rows = st.query(
            "SELECT * FROM driver_profiles "
            "WHERE telegram_chat_id <> '' AND notification_enabled = 1 "
            "AND blacklisted = 0")
        return rows or []
    except Exception:  # noqa: BLE001 - DB xatosi xabar emas
        return []


def _build_text(profile: dict) -> str | None:
    """Muddati yaqin/o'tgan hujjatlar satrlarini yig'adi; bo'lmasa None."""
    lines: list[str] = []
    for field, label in (("passport_expiry", "🪪 Passport"),
                         ("license_expiry", "🚘 Guvohnoma")):
        value = str(profile.get(field) or "").strip()
        if not value:
            continue
        state = _expiry_state(value)
        if not state or state[0] == "✅":
            continue
        lines.append(f"  {label}: {state[0]} ({state[1]})")
    if not lines:
        return None
    return ("📅 <b>HUJJAT MUDDATI ESLATMALARI</b>\n\n"
            + "\n".join(lines)
            + "\n\nIltimos hujjatlaringizni yangilang.")


def _send_to(chat_id: str, text: str) -> None:
    try:
        send_message(text, chat_id=str(chat_id))
    except Exception as exc:  # noqa: BLE001
        print(f"Hujjat muddati bildirishnomasi yuborilmadi [{chat_id}]: {exc}")


def check_and_send() -> None:
    """Kuniga bir marta barcha bog'langan haydovchilarga eslatma yuboradi."""
    global _LAST_CHECK_AT
    st = get_storage()
    if not st.enabled:
        return
    with _LOCK:
        now = time.time()
        if now - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
            return
        _LAST_CHECK_AT = now

        today = date.today().isoformat()
        if _load().get("date") == today:
            return

        for profile in _recipients():
            try:
                text = _build_text(profile)
                if not text:
                    continue
                _send_to(str(profile.get("telegram_chat_id") or ""), text)
            except Exception as exc:  # noqa: BLE001
                print(f"Hujjat muddati tekshiruvi xatosi: {exc}")

        _save({"date": today})


def status_text() -> str:
    """`/doc` ekrani uchun holat matni."""
    state = _load()
    last = state.get("date") or ""
    return "\n".join([
        "📅 <b>HUJJAT MUDDATI</b>",
        "",
        "Telegram'ga bog'langan haydovchilarga kuniga bir marta hujjat",
        "muddati yaqinlashgan/o'tgan eslatma yuboriladi (passport, guvohnoma).",
        f"So'nggi yuborilgan sana: {last or '—'}",
    ])