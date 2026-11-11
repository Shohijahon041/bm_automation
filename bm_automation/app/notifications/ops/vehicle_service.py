"""Avtobus texnik xizmat muddati eslatmalari (ADMIN'larga Telegram).

`core.vehicle_service.collect_due` natijalari asosida muddati o'tgan yoki
yaqinlashgan avtobuslar haqida kuniga bir marta eslatma yuboriladi.
Muddati o'tganlar har kuni, yaqinlashganlar 3 kunda bir marta takrorlanadi —
`state/vehicle_service_notify.json` takroriy spamni to'sadi.
"""

from __future__ import annotations

import html
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable

from ...core.vehicle_service import collect_due
from ...utils.io import atomic_write

STATE_FILE = Path("state") / "vehicle_service_notify.json"

# Ichki tekshiruv oralig'i; haqiqiy yuborish dedupe bilan kunlik.
_CHECK_INTERVAL_S = 1800.0
DUE_REPEAT_DAYS = 3

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0
_sender: Callable[[str], bool] | None = None


def set_sender(sender: Callable[[str], bool] | None) -> None:
    """Testlar uchun: yuboruvchini almashtiradi (None = haqiqiy Telegram)."""
    global _sender
    _sender = sender


def reset_state() -> None:
    """Testlar uchun: interval hisoblagichini nolga qaytaradi."""
    global _LAST_CHECK_AT
    with _LOCK:
        _LAST_CHECK_AT = 0.0


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
    except Exception as exc:  # noqa: BLE001 - eslatmani buzmaydi
        print(f"Xizmat eslatmasi holati saqlanmadi: {exc}")


def _send(text: str) -> bool:
    if _sender is None and os.getenv("BM_TEST_MODE") == "1":
        return False
    sender = _sender
    if sender is None:
        from ...dashboard import alerts
        sender = alerts.send_admin_text
    try:
        return bool(sender(text))
    except Exception as exc:  # noqa: BLE001
        print(f"Xizmat eslatmasi yuborilmadi: {exc}")
        return False


def _fmt_date(raw: str) -> str:
    try:
        return date.fromisoformat(raw).strftime("%d.%m.%Y")
    except ValueError:
        return raw


def _when(item: dict) -> str:
    parts: list[str] = []
    if item.get("next_service_date"):
        days = int(item.get("days_left") or 0)
        if days < 0:
            parts.append(f"{abs(days)} kun o'tdi")
        elif days == 0:
            parts.append("bugun")
        else:
            parts.append(f"{days} kun qoldi")
    km = int(item.get("km_left") or 0)
    if not item.get("next_service_date") and km:
        parts.append(f"{abs(km)} km " + ("o'tdi" if km < 0 else "qoldi"))
    return " · ".join(parts)


def _line(item: dict) -> str:
    plate = html.escape(str(item.get("plate_number") or item.get("vehicle_id") or "?"))
    model = html.escape(str(item.get("model") or ""))
    service = html.escape(str(item.get("service_type") or ""))
    when = _when(item)
    head = f"• <b>{plate}</b>"
    if model:
        head += f" ({model})"
    tail = " · ".join(x for x in (when, service) if x)
    if item.get("next_service_date"):
        tail = (tail + " · " if tail else "") + _fmt_date(item["next_service_date"])
    return head + (" — " + tail if tail else "")


def build_message(items: list[dict]) -> str:
    overdue = [x for x in items if x.get("state") == "overdue"]
    due = [x for x in items if x.get("state") == "due"]
    lines = ["🛠 <b>TEXNIK XIZMAT ESLATMASI</b>", ""]
    if overdue:
        lines.append("❗ <b>Muddati o'tgan:</b>")
        lines.extend(_line(x) for x in overdue[:20])
        lines.append("")
    if due:
        lines.append("⏳ <b>Yaqinlashgan:</b>")
        lines.extend(_line(x) for x in due[:20])
        lines.append("")
    lines.append("Batafsil: Dashboard → Avtobuslar → xizmat jurnali.")
    return "\n".join(lines)


def _pending(items: list[dict], sent: dict, today: date) -> list[dict]:
    """Yuborilishi kerak bo'lganlar (overdue — kunlik, due — 3 kunda bir)."""
    pending = []
    for item in items:
        vid = str(item.get("vehicle_id") or "")
        if not vid:
            continue
        prev = sent.get(vid) or {}
        raw = str(prev.get("last_sent") or "")
        try:
            days = (today - date.fromisoformat(raw)).days
        except ValueError:
            days = 10_000
        if item.get("state") == "overdue":
            if days >= 1:
                pending.append(item)
        elif days >= DUE_REPEAT_DAYS:
            pending.append(item)
    return pending


def check_and_send(today: date | None = None) -> bool:
    """Muddatli xizmatlar bo'yicha ADMIN'larga eslatma yuboradi."""
    global _LAST_CHECK_AT
    with _LOCK:
        now = time.time()
        if now - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
            return False
        _LAST_CHECK_AT = now

    today = today or date.today()
    try:
        items = collect_due(today=today)
    except Exception as exc:  # noqa: BLE001 - DB xatosi botni to'xtatmaydi
        print(f"Xizmat muddati tekshiruvi xatosi: {exc}")
        return False
    if not items:
        return False

    state = _load()
    sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    pending = _pending(items, sent, today)
    if not pending:
        return False
    if not _send(build_message(pending)):
        return False

    active = {str(x.get("vehicle_id") or "") for x in items}
    new_sent = {vid: rec for vid, rec in sent.items() if vid in active}
    for item in pending:
        new_sent[str(item["vehicle_id"])] = {
            "last_sent": today.isoformat(),
            "state": item.get("state") or "",
            "plate_number": item.get("plate_number") or "",
        }
    _save({"sent": new_sent})
    return True
