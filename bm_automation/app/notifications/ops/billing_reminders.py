"""Oylik to'lov eslatmalari — super adminlar va korxona kontaktiga.

Bot poll-tsikli `check_and_send()` chaqiradi. Kuniga bir marta:

- obuna tugayotgan / grace'dagi / bloklangan yoki joriy oy uchun qarzi bor
  korxonalar ro'yxati barcha ADMIN chat'larga yuboriladi;
- har bir korxonaning `contact_telegram` (raqamli chat ID) manziliga faqat
  o'sha korxona satri yuboriladi.

Holat `state/billing_reminders.json` da saqlanadi — bot restart'da takroriy
spam bo'lmaydi.
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
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "billing_reminders.json"

# Tekshiruvlar orasidagi eng kichik interval (soniya).
_CHECK_INTERVAL_S = 1800.0

_LOCK = threading.Lock()
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
        print(f"To'lov eslatmasi holati saqlanmadi: {exc}")


def _money(value) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return f"{amount:,.0f}".replace(",", " ") + " so'm"


def _line(c: dict, today: date) -> str:
    """Bitta korxona uchun qisqa satr."""
    state = str(c.get("state") or "")
    if state == "grace":
        note = "grace davri — obuna tugagan"
    elif state == "blocked":
        note = "BLOKLANGAN"
    elif state == "due":
        days = c.get("days_left")
        note = f"{days} kun qoldi"
    else:
        note = "joriy oy to'lanmagan"
    expire = str(c.get("subscription_expire_date") or "—")
    debt = c.get("debt") or 0
    money = f"qarz {_money(debt)}" if debt else f"narx {_money(c.get('price'))}"
    return (f"• <b>{c.get('name') or '—'}</b> — {c.get('period')}: {money}"
            f" · obuna {expire} · {note}")


def _admin_chats() -> list[str]:
    """ADMIN rolidagi chat ID'lar (Telegram sozlamasidan)."""
    try:
        roles = configured_roles()
    except Exception:  # noqa: BLE001
        return []
    return [str(cid) for cid, role in roles.items() if role == Role.ADMIN]


def _contact_chat(c: dict) -> str:
    """Korxona kontaktining raqamli chat ID'si (aks holda bo'sh)."""
    raw = str(c.get("contact_telegram") or "").strip().lstrip("@")
    return raw if raw.isdigit() else ""


def _send_to(chat_id: str, text: str) -> None:
    try:
        send_message(text, chat_id=str(chat_id))
    except Exception as exc:  # noqa: BLE001
        print(f"To'lov eslatmasi yuborilmadi [{chat_id}]: {exc}")


def check_and_send() -> None:
    """Kuniga bir marta to'lov eslatmalarini yuboradi."""
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
        if _load().get("date") == today.isoformat():
            return

        try:
            from ...dashboard.billing import due_companies
            items = due_companies(storage=st, today=today)
        except Exception as exc:  # noqa: BLE001
            print(f"To'lov holati hisoblanmadi: {exc}")
            return

        if items:
            admin_text = (
                "💳 <b>OYLIK TO'LOV ESLATMASI</b>\n\n"
                + "\n".join(_line(c, today) for c in items)
                + "\n\nTo'lov: naqd, karta yoki hisob raqami (shartnoma)."
                + "\nGrace tugagach korxona bloklanadi."
            )
            for chat_id in _admin_chats():
                _send_to(chat_id, admin_text)
            for c in items:
                chat_id = _contact_chat(c)
                if chat_id:
                    _send_to(chat_id, (
                        "💳 <b>OYLIK TO'LOV ESLATMASI</b>\n\n"
                        + _line(c, today)
                        + "\n\nIltimos to'lovni amalga oshiring."
                    ))
        _save({"date": today.isoformat(), "count": len(items)})
