"""Interaktiv haydovchi qayd / jarima kiritish (Telegram).

Haydovchi kartasidagi tugmalar orqali boshlanadi:

- ``dlog:<driver_id>`` — kunlik km qaydi flow:
    1. Sana (YYYY-MM-DD)
    2. Avtobus ID (ixtiyoriy)
    3. Bosib o'tilgan masofa (km)
    4. Qatnovlar soni

- ``dfine:<driver_id>`` — jarima flow:
    1. Sana (YYYY-MM-DD)
    2. Summa (so'm)
    3. Sabab (ixtiyoriy)

- ``appeal`` — haydovchi murojaat flow (`/murojaat`):
    1. Mavzu (ixtiyoriy)
    2. Matn (majburiy)

Holat per-chat ``state/driver_entry.json`` faylida saqlanadi (bot restart'da
ham yo'qolmaydi). Yakunlangach ``dispatch.commit_driver_entry`` storage'ga
yozadi va yangilangan kartani ko'rsatadi.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from ...utils.io import atomic_write

STATE_FILE = Path("state") / "driver_entry.json"

_STEPS = {
    "log": ("date", "vehicle", "km", "trips"),
    "fine": ("date", "amount", "reason"),
    "appeal": ("title", "text"),
}

_PENDING: dict[int, dict] = {}
_LOCK = threading.Lock()


def _load() -> None:
    if _PENDING:
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("chats"), dict):
            for k, v in data["chats"].items():
                try:
                    _PENDING[int(k)] = v
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(
            {"chats": _PENDING}, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Haydovchi qayd holati saqlanmadi: {exc}")


def start(chat_id: int, flow: str, driver_id: str) -> None:
    _load()
    with _LOCK:
        _PENDING[chat_id] = {"flow": flow, "driver_id": str(driver_id or ""),
                             "step": _STEPS[flow][0], "data": {}}
        _save()


def current(chat_id: int) -> dict | None:
    _load()
    return _PENDING.get(chat_id)


def cancel(chat_id: int) -> None:
    _load()
    with _LOCK:
        _PENDING.pop(chat_id, None)
        _save()


def _validate(step: str, value: str) -> str | None:
    value = (value or "").strip()
    if step == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return ("Sana noto'g'ri. Format: <code>YYYY-MM-DD</code> "
                    "(masalan: 2026-08-13).")
    elif step in ("km", "amount"):
        try:
            if float(value) < 0:
                raise ValueError
        except (TypeError, ValueError):
            return "Raqam kiriting (0 dan katta yoki teng)."
    elif step == "trips":
        try:
            if int(value) < 0:
                raise ValueError
        except (TypeError, ValueError):
            return "Butun son kiriting (0 dan katta yoki teng)."
    elif step == "title":
        if not value:
            return "Mavzu yozing yoki o'tkazib yubormoqchi bo'lsangiz <code>-</code>."
    elif step == "text":
        if not value:
            return "Murojaat matnini kiriting."
    return None


def set_value(chat_id: int, value: str) -> str | None:
    """Joriy bosqichga qiymat yozadi va keyingisiga o'tadi.

    Qaytaradi: keyingi bosqich nomi yoki `None` (yakun — commit qilish
    kerak). Yaroqsiz qiymatda `ValueError` ko'tariladi — dispatch qayta
    so'raydi.
    """
    _load()
    value = (value or "").strip()
    with _LOCK:
        st = _PENDING.get(chat_id)
        if not st:
            return None
        step = st["step"]
        err = _validate(step, value)
        if err:
            raise ValueError(err)
        if step in ("vehicle", "reason", "title") and \
                value.lower() in ("-", "0", "skip", "yoq", "no", "none"):
            value = ""
        st["data"][step] = value
        steps = _STEPS[st["flow"]]
        idx = steps.index(step) if step in steps else -1
        nxt = steps[idx + 1] if 0 <= idx < len(steps) - 1 else "done"
        st["step"] = nxt
        _save()
        return None if nxt == "done" else nxt


def commit(chat_id: int) -> tuple | None:
    """Yakunlangan flow'ni oladi va holatni tozalaydi."""
    _load()
    with _LOCK:
        st = _PENDING.pop(chat_id, None)
        if not st:
            return None
        data = dict(st.get("data") or {})
        _save()
        return data, st.get("flow", ""), st.get("driver_id", "")


def prompts() -> dict:
    return {
        "date": "📅 Sana (<code>YYYY-MM-DD</code>):",
        "vehicle": "🚌 Avtobus ID yoki nomi (bo'sh qoldirish mumkin: <code>-</code>):",
        "km": "📏 Bosib o'tilgan masofa (km):",
        "trips": "🔁 Qatnovlar soni:",
        "amount": "💸 Jarima summasi (so'm):",
        "reason": "📝 Sabab (bo'sh qoldirish mumkin: <code>-</code>):",
        "title": "📋 Mavzu (qisqa, ixtiyoriy; o'tkazib yuborish: <code>-</code>):",
        "text": "✍️ Murojaat yoki taklif matni:",
    }
