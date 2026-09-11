"""Ertalabki AI-xulosa — kuniga bir marta ADMIN/DISPATCHER'ga xabar.

Bot poll-tsikli har iteratsiyada `check_and_send()` chaqiradi; u kuniga
bir marta (soat `AI_DAILY_SUMMARY_HOUR`, standart 8) ruxsat etilgan
chat'larga `assistant.daily_summary()` yuboradi. OpenRouter sozlangan
bo'lsa xulosa LLM orqali jonlantiriladi, aks holda lokal qoidaviy matn.

Holat `state/daily_summary.json`:
  {"date": "YYYY-MM-DD"}  # bugun allaqachon yuborilganmi

`AI_DAILY_SUMMARY=off` bilan o'chiriladi. Qo'lda yuborish: `/daily`.
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
from ..telegram import send_message
from . import assistant, kb
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "daily_summary.json"

# Sohaviy tekshiruvlar orasidagi interval (kuniga bir marta uchun yetarli).
_CHECK_INTERVAL_S = 300.0

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0  # in-memory hisoblagich (restart'da nollanadi, holat faylda)


def enabled() -> bool:
    s = telegram_settings()
    return str(s.get("daily_summary", "on")).lower() not in ("off", "0", "false")


def _hour() -> int:
    s = telegram_settings()
    try:
        return max(0, min(23, int(str(s.get("daily_summary_hour", "8")))))
    except (TypeError, ValueError):
        return 8


def _targets() -> list[int]:
    roles = configured_roles()
    return [cid for cid, role in roles.items()
            if role in (Role.ADMIN, Role.DISPATCHER)]


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
    except Exception as exc:  # noqa: BLE001 - xulosani buzmaydi
        print(f"Ertalabki xulosa holati saqlanmadi: {exc}")


def _build_text() -> str | None:
    """Kunlik xulosa matni (LLM sozlangan bo'lsa jonlantiriladi)."""
    if not get_storage().enabled:
        return None
    try:
        local_text = assistant.daily_summary()
    except Exception as exc:  # noqa: BLE001
        print(f"Ertalabki xulosa tayyorlanmadi: {exc}")
        return None
    return assistant._maybe_llm("", local_text, "Ertalabki xulosa tayyorlang")


def check_and_send() -> None:
    """Kuniga bir marta yuboradi (soat chegarasi + holat fayl bilan)."""
    global _LAST_CHECK_AT
    if not enabled() or not get_storage().enabled:
        return
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    if time.localtime().tm_hour < _hour():
        return
    targets = _targets()
    if not targets:
        return
    with _LOCK:
        today = date.today().isoformat()
        if _load().get("date") == today:
            return
        text = _build_text()
        if not text:
            return
        _save({"date": today})
        for cid in targets:
            try:
                send_message(text, chat_id=str(cid),
                             reply_markup=kb.nav_kb("nav:settings"))
            except Exception as exc:  # noqa: BLE001
                print(f"Ertalabki xulosa yuborilmadi [{cid}]: {exc}")


def send_now() -> str:
    """Qo'lda yuborish (`/daily`) — so'rovchi chat'iga matn qaytaradi."""
    with _LOCK:
        text = _build_text()
        if not text:
            return "⚠️ Xulosa tayyorlanmadi (DB o'chirilgan bo'lishi mumkin)."
        _save({"date": date.today().isoformat()})
        return text


def status_text() -> str:
    """Holat matni (masalan, `/alerts` ekranida ko'rsatiladi)."""
    s = _load()
    sent = s.get("date") == date.today().isoformat()
    mode = "✅ yonilgan" if enabled() else "⛔ o'chirilgan"
    state = "✓ bugun yuborilgan" if sent else "bugun hali yuborilmagan"
    return "\n".join([
        "🌅 <b>ERTALABKI XULOSA</b>",
        "",
        f"Rejim: {mode} · soat <b>{_hour()}:00</b>",
        f"Holat: {state}",
        "",
        "Qo'lda yuborish: <b>/daily</b>",
    ])
