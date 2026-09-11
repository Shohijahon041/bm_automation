"""Interaktiv ro'yxatdan o'tkazish (addcompany) va login holati.

`/addcompany` ADMIN tomonidan bosqichma-bosqich bajariladi:
   1. Firma nomi
   2. routeVariantId
   3. Ega chat ID(lari), vergul bilan
   4. Firma BM login (ixtiyoriy, bo'sh = keyin /login orqali)
   5. Parol

`/login` foydalanuvchi tomonidan bosqichma-bosqich bajariladi:
   1. Firma BM login (username)
   2. Parol

Holat per-chat `state/registration.json` faylida saqlanadi (bot restart'da
ham yo'qolmaydi). Yakunlangach `commit` profillarga yozadi.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ...utils.io import atomic_write

STATE_FILE = Path("state") / "registration.json"

_STEPS = ("name", "route", "owners", "username", "password")
_LOGIN_STEPS = ("username", "password")

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
        print(f"Ro'yxat holati saqlanmadi: {exc}")


def _steps(st: dict) -> tuple:
    return _LOGIN_STEPS if st.get("flow") == "login" else _STEPS


def start(chat_id: int, flow: str = "addcompany") -> dict:
    _load()
    with _LOCK:
        st = _PENDING.get(chat_id)
        if st:
            st["flow"] = flow
            st["step"] = _LOGIN_STEPS[0] if flow == "login" else _STEPS[0]
            st["data"] = {}
            _save()
            return st
        st = {"flow": flow, "step": _LOGIN_STEPS[0] if flow == "login" else _STEPS[0],
              "data": {}}
        _PENDING[chat_id] = st
        _save()
        return st


def current(chat_id: int) -> dict | None:
    _load()
    return _PENDING.get(chat_id)


def is_login(chat_id: int) -> bool:
    st = current(chat_id)
    return bool(st and st.get("flow") == "login")


def set_value(chat_id: int, value: str) -> str | None:
    """Joriy bosqichga qiymat yozadi, keyingi bosqichga o'tadi.

    `username` bosqichida skip-token ('-', '0', 'skip', 'yoq') kiritilsa
    parolni o'tkazib yuboramiz. Yakun bo'lsa None qaytaradi (commit qilish
    kerak).
    """
    _load()
    with _LOCK:
        st = _PENDING.get(chat_id)
        if not st:
            return None
        data = st["data"]
        step = st["step"]
        steps = _steps(st)
        value = (value or "").strip()

        if step == "username" and st.get("flow") != "login" and \
                value.lower() in ("-", "0", "skip", "yoq", "no", "none"):
            value = ""

        if step == "owners":
            data["owners"] = value
        else:
            data[step] = value

        idx = steps.index(step) if step in steps else -1
        nxt = steps[idx + 1] if 0 <= idx < len(steps) - 1 else "done"
        if step == "username" and st.get("flow") != "login" and not value:
            nxt = "done"
        st["step"] = nxt
        _save()
        return st["step"]


def cancel(chat_id: int) -> None:
    _load()
    with _LOCK:
        _PENDING.pop(chat_id, None)
        _save()


def commit(chat_id: int) -> dict | None:
    """Yakunlangan ro'yxatni profillarga yozadi va holatni tozalaydi."""
    _load()
    with _LOCK:
        st = _PENDING.pop(chat_id, None)
        if not st:
            return None
        data = dict(st.get("data") or {})
        _save()
        return data


def prompts() -> dict:
    return {
        "name": "🏢 Firma nomini kiriting (masalan: <b>GOLDEN BUS MCHJ</b>):",
        "route": "🛣 Yo'nalish ID (routeVariantId) kiriting:",
        "owners": "👤 Firma egasi Telegram chat ID(lari)ni vergul bilan kiriting "
                  "(masalan: <code>123456789, 987654321</code>):",
        "username": "🔑 Firma BM logini (username) kiriting. Bo'lishi shart emas — "
                    "bo'sh yuborsangiz, ega keyin /login orqali kiritadi:",
        "password": "🔒 Parolni kiriting:",
    }


def login_prompts() -> dict:
    return {
        "username": "🔑 Firma BM loginingizni (username) kiriting:",
        "password": "🔒 Parolingizni kiriting:",
    }
