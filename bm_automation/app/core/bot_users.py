"""Telegram bot foydalanuvchilarini kuzatish va saqlash.

Har qanday update (xabar/callback) kelganda foydalanuvchi ma'lumotlari
`state/bot_users.json` fayliga yoziladi. Dashboard orqali ko'rish mumkin.

Saqlanadigan ma'lumotlar:
  - chat_id, first_name, username, role, photo_file_id
  - first_seen, last_seen (ISO timestamp)
  - message_count (umumiy xabar soni)
  - activity — oxirgi N ta harakat tarixi (action, detail, timestamp)
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from ..utils.io import atomic_write

STATE_FILE = Path("state") / "bot_users.json"
PHOTO_DIR = Path("state") / "user_photos"

_LOCK = threading.RLock()  # set_role(tarkibida record_activity) qo'yilishini qayta qamrab olishi uchun RLock
_CACHE: dict = {}
_LOADED = False
_LAST_MTIME: float | None = None
_LAST_SIZE: int | None = None

_MAX_ACTIVITY = 50


def _load() -> dict:
    global _LOADED, _CACHE
    if _LOADED:
        return _CACHE
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE = data
    except Exception:
        pass
    _LOADED = True
    return _CACHE


def _reload_if_changed() -> None:
    """Diskdagi fayl o'zgarganda xotiradagi keshi yangilaydi.

    Bot va dashboard alohida jarayonlar — ikkalasi ham bitta faylga
    yozadi/o'qiydi. Xotira keshi eskirib qolmasligi uchun faylning
    mtime/size'iga qarab qayta yuklaymiz (xotirada bo'lgan, hali
    saqlanmagan o'zgarishlarni yo'qotmaslikka harakat qilamiz).
    """
    global _CACHE, _LOADED, _LAST_MTIME, _LAST_SIZE
    try:
        if not STATE_FILE.exists():
            return
        st = STATE_FILE.stat()
        mtime = st.st_mtime
        size = st.st_size
        if _LAST_MTIME == mtime and _LAST_SIZE == size:
            return
        with STATE_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE = data
        _LAST_MTIME = mtime
        _LAST_SIZE = size
        _LOADED = True
    except Exception:
        pass


def _save() -> None:
    """Keshni diskka yozadi va mtime/size'ni yangilaydi.

    mtime/size yangilanmasa, keyingi `_reload_if_changed()` faylni "tashqi
    o'zgarish" deb hisoblab, xotiradagi hali saqlanmagan o'zgarishlarni
    yo'qotib qo'yardi (masalan `set_pages`).
    """
    global _LAST_MTIME, _LAST_SIZE
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(_CACHE, ensure_ascii=False, indent=2))
        try:
            st = STATE_FILE.stat()
            _LAST_MTIME, _LAST_SIZE = st.st_mtime, st.st_size
        except OSError:
            pass
    except Exception as exc:
        print(f"Bot foydalanuvchilari saqlanmadi: {exc}")


def record_user(chat_id: int, first_name: str = "", username: str = "",
                role: str = "", photo_file_id: str = "") -> None:
    """Foydalanuvchi ma'lumotini yangilaydi yoki qo'shadi (thread-safe)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = str(chat_id)
    with _LOCK:
        _reload_if_changed()
        user = _CACHE.get(key)
        if user:
            user["last_seen"] = now
            user["message_count"] = user.get("message_count", 0) + 1
            # Telegram'dan kelgan haqiqiy ma'lumotni doimo yangilaymiz
            user["first_name"] = first_name or user.get("first_name", "")
            user["username"] = username or user.get("username", "")
            if role:
                user["role"] = role
            if photo_file_id:
                user["photo_file_id"] = photo_file_id
        else:
            _CACHE[key] = {
                "chat_id": chat_id,
                "first_name": first_name,
                "username": username,
                "role": role,
                "photo_file_id": photo_file_id,
                "first_seen": now,
                "last_seen": now,
                "message_count": 1,
                "activity": [],
            }
        _save()


def record_activity(chat_id: int, action: str, detail: str = "") -> None:
    """Foydalanuvchi harakatini qayd qiladi (thread-safe, cheklangan)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = str(chat_id)
    with _LOCK:
        _reload_if_changed()
        user = _CACHE.get(key)
        if not user:
            _CACHE[key] = {
                "chat_id": chat_id,
                "first_name": "",
                "username": "",
                "role": "",
                "photo_file_id": "",
                "first_seen": now,
                "last_seen": now,
                "message_count": 0,
                "activity": [],
            }
            user = _CACHE[key]
        user["last_seen"] = now
        user.setdefault("activity", [])
        user["activity"].append({
            "action": action,
            "detail": detail[:500],
            "at": now,
        })
        if len(user["activity"]) > _MAX_ACTIVITY:
            user["activity"] = user["activity"][-_MAX_ACTIVITY:]
        _save()


def set_role(chat_id: int, role: str) -> bool:
    """Foydalanuvchi rolini o'zgartiradi."""
    key = str(chat_id)
    with _LOCK:
        _load()
        user = _CACHE.get(key)
        if not user:
            return False
        user["role"] = role
        record_activity(chat_id, "role_changed", f"Yangi rol: {role}")
        _save()
    return True


def set_pages(chat_id: int, pages: list | None) -> bool:
    """Foydalanuvchining bo'limlar ro'yxatini o'rnatadi (None = cheklovsiz).

    Ro'yxat berilsa — bot faqat shu bo'limlar buyruqlarini bajaradi
    (`app/core/sections.py` kalitlari). Ro'yxat `None` bo'lsa cheklov olib
    tashlanadi (faqat rol bo'yicha ishlaydi).
    """
    from .sections import clean_sections
    if pages is not None:
        pages, err = clean_sections(list(pages))
        if err:
            raise ValueError(err)
    key = str(chat_id)
    with _LOCK:
        _load()
        user = _CACHE.get(key)
        if not user:
            return False
        if pages is None:
            user.pop("pages", None)
        else:
            user["pages"] = list(pages)
        detail = "Barcha bo'limlar (cheklovsiz)" if pages is None else \
            f"Bo'limlar: {', '.join(pages) if pages else 'hech biri'}"
        record_activity(chat_id, "pages_changed", detail)
        _save()
    return True


def get_pages(chat_id: int) -> list | None:
    """Foydalanuvchiga aniq belgilangan bo'limlar (None = cheklovsiz)."""
    user = get_user(chat_id)
    if not user:
        return None
    pages = user.get("pages")
    if not isinstance(pages, list):
        return None
    return [str(p) for p in pages]


def remove_user(chat_id: int) -> bool:
    """Foydalanuvchini ro'yxatdan o'chiradi."""
    key = str(chat_id)
    with _LOCK:
        _load()
        if key in _CACHE:
            _CACHE[key]["active"] = False
            _save()
            return True
    return False


def update_photo(chat_id: int, photo_file_id: str) -> None:
    """Foydalanuvchi Telegram profil rasmini yangilaydi."""
    key = str(chat_id)
    with _LOCK:
        _load()
        user = _CACHE.get(key)
        if user:
            user["photo_file_id"] = photo_file_id
            _save()


def get_users() -> list[dict]:
    """Barcha foydalanuvchilarni qaytaradi (ro'yxat shaklida)."""
    with _LOCK:
        _reload_if_changed()
        users = []
        for key, u in _CACHE.items():
            if isinstance(u, dict):
                u.setdefault("activity", [])
                users.append(u)
        users.sort(key=lambda u: u.get("last_seen", ""), reverse=True)
        return users


def get_user(chat_id: int) -> dict | None:
    """Bitta foydalanuvchini qaytaradi."""
    with _LOCK:
        _reload_if_changed()
        user = _CACHE.get(str(chat_id))
        if user:
            user.setdefault("activity", [])
        return user


def user_count() -> int:
    """Jami foydalanuvchilar soni."""
    with _LOCK:
        _reload_if_changed()
        return len([k for k in _CACHE if isinstance(_CACHE[k], dict)])
