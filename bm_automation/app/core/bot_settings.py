"""Bot orqali o'zgartiriladigan sozlamalar (`state/bot_settings.json`).

- `km_rate` — global 1 km narxi (so'm). Bot orqali o'zgartiriladi va
  `KM_RATE` env'dan ham ustun turadi (haydovchi oyligi hisobiga ta'sir qiladi).
  `0`/yo'q bo'lsa standart ustunlik tartibi ishlaydi (env > profil > firma).
- `langs` — chat_id → til (`uz` / `ru`).

Fayl atomik yoziladi, bot qayta ishga tushsa ham saqlanib qoladi.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..utils.io import atomic_write

STATE_FILE = Path("state") / "bot_settings.json"

_LOCK = threading.Lock()
_CACHE: dict = {}
_LOADED = False

# Interaktiv oqimlar uchun kutilayotgan kirish (chat_id -> sozlama kaliti)
_PENDING: dict[int, str] = {}


def _load() -> dict:
    global _LOADED
    if _LOADED:
        return _CACHE
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE.update(data)
    except Exception:
        pass
    _LOADED = True
    return _CACHE


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(_CACHE, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - kichik bezak, xato bo'lsa o'tkazib yuboramiz
        print(f"Bot sozlamalari saqlanmadi: {exc}")


def _reset() -> None:
    """Testlar uchun: xotira holatini tozalaydi."""
    global _LOADED
    with _LOCK:
        _CACHE.clear()
        _LOADED = False
        _PENDING.clear()


# ---------------------------------------------------------------- km narxi

def km_rate() -> float:
    """Bot orqali o'rnatilgan global 1 km narxi (0 bo'lsa o'rnatilmagan)."""
    try:
        return float(_load().get("km_rate") or 0)
    except (TypeError, ValueError):
        return 0.0


def set_km_rate(value: float | None) -> float:
    """Global 1 km narxini o'rnatadi (0/None — o'chirish, asl holatga qaytish)."""
    with _LOCK:
        _load()
        value = max(float(value or 0), 0.0)
        _CACHE["km_rate"] = value
        _save()
    return value


# ----------------------------------------------------- elektr energiya narxi

# Elektr energiya hisob-kitobi koeffitsiyenti: 1 km uchun kVt/soat iste'moli
ELEC_KWH_PER_KM = 0.955


def elec_price() -> float:
    """Bot orqali o'rnatilgan global 1 kVt/soat elektr narxi (so'm)."""
    try:
        return float(_load().get("elec_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def set_elec_price(value: float | None) -> float:
    """1 kVt/soat elektr narxini o'rnatadi (0/None — o'chirish)."""
    with _LOCK:
        _load()
        value = max(float(value or 0), 0.0)
        _CACHE["elec_price"] = value
        _save()
    return value


# ------------------------------------------------------------------- til

def lang(chat_id: int) -> str:
    """Foydalanuvchi tili (`uz` / `ru`)."""
    langs = _load().get("langs") or {}
    return str(langs.get(str(chat_id), "uz")) if str(langs.get(str(chat_id), "uz")) in ("uz", "ru") else "uz"


def set_lang(chat_id: int, value: str) -> str:
    """Foydalanuvchi tilini o'rnatadi (`uz` / `ru`)."""
    value = value if value in ("uz", "ru") else "uz"
    with _LOCK:
        _load()
        _CACHE.setdefault("langs", {})[str(chat_id)] = value
        _save()
    return value


# --------------------------------------------------- interaktiv sozlamalar

def pending(chat_id: int) -> str | None:
    """chat_id uchun kutilayotgan sozlama oqimi kaliti (masalan `km_rate`)."""
    return _PENDING.get(chat_id)


def set_pending(chat_id: int, key: str) -> None:
    _PENDING[chat_id] = key


def clear_pending(chat_id: int) -> None:
    _PENDING.pop(chat_id, None)
