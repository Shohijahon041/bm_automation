"""Bot foydalanuvchisi uchun faol kompaniya (profil) holati.

Har bir chat_id o'zi ko'rayotgan kompaniyani tanlashi mumkin (inline
tugmalar orqali). Tanlov `state/company.json` faylida saqlanadi, shuning
uchun bot qayta ishga tushganda ham saqlanib qoladi.

`filters_for(chat_id)` render/metrika funksiyalariga uzatiladigan filter
dict'ini qaytaradi — faol kompaniya bo'lsa uning yo'nalish(lar)i bo'yicha
cheklangan, aks holda bo'sh (hammasi).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ...core.profiles import all_profiles, get_profile
from ...utils.io import atomic_write

STATE_FILE = Path("state") / "company.json"

_ACTIVE: dict[int, str] = {}
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("chats"), dict):
            return data
    except Exception:
        pass
    return {"chats": {}}


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(
            {"chats": _ACTIVE}, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Kompaniya holati saqlanmadi: {exc}")


def _init() -> None:
    if _ACTIVE:
        return
    with _LOCK:
        if _ACTIVE:
            return
        data = _load()
        for k, v in (data.get("chats") or {}).items():
            try:
                _ACTIVE[int(k)] = str(v)
            except (TypeError, ValueError):
                continue


def get_active(chat_id: int) -> str:
    _init()
    return _ACTIVE.get(chat_id, "")


def set_active(chat_id: int, name: str) -> str:
    _init()
    name = (name or "").strip()
    if name and not get_profile(name):
        raise ValueError(f"Kompaniya topilmadi: {name}")
    with _LOCK:
        if name:
            _ACTIVE[chat_id] = name
        else:
            _ACTIVE.pop(chat_id, None)
        _save()
    return name


def clear(chat_id: int) -> None:
    _init()
    with _LOCK:
        _ACTIVE.pop(chat_id, None)
        _save()


def profile_routes(name: str) -> list[str]:
    """Kompaniyaning yo'nalish(lar)i — routeVariantId (bo'sh qismlar olinmaydi)."""
    p = get_profile(name)
    if not p:
        return []
    raw = str(p.get("routeVariantId") or "").strip()
    ids = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    return ids


def profiles_with_routes() -> list[dict]:
    """Yo'nalishi bor bo'lgan kompaniyalar (tanlash ro'yxati uchun)."""
    out = []
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        routes = profile_routes(name)
        label = short_name(name)
        if routes:
            out.append({"name": name, "label": label,
                        "routes": routes,
                        "route_name": str(p.get("routeName") or "")})
    return out


def short_name(name: str) -> str:
    """Kompaniya qisqa nomi (tugmada ko'rsatish uchun)."""
    name = (name or "").strip()
    for token in ("MCHJ", "ХМ", "QK", "ФХ"):
        if name.upper().endswith(token):
            name = name[: -len(token)].strip()
            break
    name = name.strip('"').strip()
    return name or "Kompaniya"


def filters_for(chat_id: int) -> dict:
    """Faol kompaniya bo'yicha filter; bo'lmasa bo'sh dict (hammasi)."""
    name = get_active(chat_id)
    if not name:
        return {}
    routes = profile_routes(name)
    f: dict = {"profile": name}
    if routes:
        f["route"] = " ".join(routes)
    return f
