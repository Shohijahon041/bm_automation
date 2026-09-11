"""Bot foydalanuvchisi uchun faol kompaniya (profil) holati.

Har bir chat_id o'zi ko'rayotgan kompaniyani tanlashi mumkin (inline
tugmalar orqali). Tanlov `state/company.json` faylida saqlanadi, shuning
uchun bot qayta ishga tushganda ham saqlanib qoladi.

`filters_for(chat_id)` render/metrika funksiyalariga uzatiladigan filter
dict'ini qaytaradi — faol kompaniya bo'lsa uning yo'nalish(lar)i bo'yicha
cheklangan, aks holda bo'sh (hammasi).

Ko'p-firmali ruxsat (multi-company):
  - ADMIN chat_id barcha kompaniyalarni ko'radi (tanlashi mumkin);
  - kompaniya egasi (ownerChatIds) faqat o'z kompaniyasini ko'radi —
    `filters_for` buni avtomatik majburlaydi.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ...core.profiles import all_profiles, get_profile, owner_chat_ids
from ...utils.io import atomic_write
from .roles import Role, resolve_role

STATE_FILE = Path("state") / "company.json"

_ACTIVE: dict[int, str] = {}
_ACTIVE_ROUTE: dict[int, str] = {}
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
        chats = {}
        for cid, name in _ACTIVE.items():
            route = _ACTIVE_ROUTE.get(cid, "")
            chats[str(cid)] = {"name": name, "route": route} if route else name
        atomic_write(STATE_FILE, json.dumps(
            {"chats": chats}, ensure_ascii=False, indent=2))
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
                cid = int(k)
                if isinstance(v, dict):
                    _ACTIVE[cid] = str(v.get("name", ""))
                    _ACTIVE_ROUTE[cid] = str(v.get("route", ""))
                else:
                    _ACTIVE[cid] = str(v)
            except (TypeError, ValueError):
                continue


def allowed_names(chat_id: int) -> list[str]:
    """Foydalanuvchi ko'ra oladigan kompaniya nomlari.

    ADMIN -> hammasi; aks holda ownerChatIds ro'yxatidagi kompaniyalar +
    dashboard'dan `dispatcher_routes` orqali biriktirilgan kompaniyalar.
    Noyob nomlar qaytariladi (bir kompaniya bir nechta profilga ega bo'lsa ham).
    """
    role = resolve_role(chat_id)
    if role is Role.ADMIN:
        return list(dict.fromkeys(
            str(p.get("name") or "") for p in all_profiles()
            if (p.get("name") or "").strip()))
    names = [str(p.get("name") or "") for p in all_profiles()
             if chat_id in owner_chat_ids(p)]
    names.extend(assigned_profile_names(chat_id))
    return list(dict.fromkeys(n for n in names if n.strip()))


def can_view(chat_id: int, name: str) -> bool:
    """Foydalanuvchi berilgan kompaniyani ko'ra oladimi?"""
    if not name:
        return True
    return name in allowed_names(chat_id)


def get_active(chat_id: int) -> str:
    _init()
    active = _ACTIVE.get(chat_id, "")
    if active and not can_view(chat_id, active):
        return ""
    return active


def set_active(chat_id: int, name: str, route: str = "") -> str:
    _init()
    name = (name or "").strip()
    route = (route or "").strip()
    if name and not get_profile(name):
        raise ValueError(f"Kompaniya topilmadi: {name}")
    if name and not can_view(chat_id, name):
        raise ValueError("Siz bu kompaniyani ko'ra olmaysiz")
    if route:
        if name and route not in company_route_ids(name):
            raise ValueError("Bu yo'nalish kompaniyaga tegishli emas")
        scoped = route_scope(chat_id)
        if scoped and route not in scoped:
            raise ValueError("Siz bu yo'nalishni tanlay olmaysiz")
    with _LOCK:
        if name:
            _ACTIVE[chat_id] = name
            if route:
                _ACTIVE_ROUTE[chat_id] = route
            else:
                _ACTIVE_ROUTE.pop(chat_id, None)
        else:
            _ACTIVE.pop(chat_id, None)
            _ACTIVE_ROUTE.pop(chat_id, None)
        _save()
    return name


def clear(chat_id: int) -> None:
    _init()
    with _LOCK:
        _ACTIVE.pop(chat_id, None)
        _ACTIVE_ROUTE.pop(chat_id, None)
        _save()


def clear_route(chat_id: int) -> None:
    """Faqat yo'nalish tanlovini tozalash (kompaniya tanlovi saqlanadi)."""
    _init()
    with _LOCK:
        _ACTIVE_ROUTE.pop(chat_id, None)
        _save()


def profile_routes(name: str) -> list[str]:
    """Kompaniyaning yo'nalish(lar)i — routeVariantId (bo'sh qismlar olinmaydi)."""
    p = get_profile(name)
    if not p:
        return []
    raw = str(p.get("routeVariantId") or "").strip()
    ids = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    return ids


def profiles_with_routes(chat_id: int | None = None) -> list[dict]:
    """Yo'nalishi bor bo'lgan kompaniyalar (tanlash ro'yxati uchun).

    chat_id berilgan bo'lsa, faqat ruxsat etilgan kompaniyalar qaytadi va
    dispetcherga biriktirilgan bo'lsa — faqat o'sha yo'nalish(lar) ro'yxati.
    Har bir profil (routeVariantId) alohida yozuv sifatida qaytariladi.
    """
    allowed = None
    scoped = None
    if chat_id is not None:
        allowed = set(allowed_names(chat_id))
        scoped = set(route_scope(chat_id)) or None
    out = []
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        if allowed is not None and name not in allowed:
            continue
        raw = str(p.get("routeVariantId") or "").strip()
        routes = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
        if scoped:
            routes = [r for r in routes if r in scoped]
        if not routes:
            continue
        label = short_name(name)
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


def profile_for_route(route_id: str) -> tuple[str, str] | None:
    """routeVariantId bo'yicha (name, route_id) qaytaradi."""
    route_id = (route_id or "").strip()
    if not route_id:
        return None
    for p in all_profiles():
        raw = str(p.get("routeVariantId") or "").strip()
        ids = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
        if route_id in ids:
            name = str(p.get("name") or "").strip()
            if name:
                return (name, route_id)
    return None


def assigned_profile_names(chat_id: int) -> list[str]:
    """Dispetcherga biriktirilgan yo'nalishlar bo'yicha kompaniya nomlari.

    `dispatcher_routes` dagi har bir route_id uchun kompaniya profili
    topiladi (routeVariantId mosligi orqali); topilmasa saqlangan
    `company`/`route_name` maydoniga tayaniladi. Noyob nomlar qaytariladi.
    Bo'sh bo'lsa bo'sh ro'yxat — chaqiruvchi hollati bo'yicha qaror qiladi.
    """
    try:
        from ...db import get_storage
        st = get_storage()
        routes = st.dispatcher_routes(chat_id)
    except Exception:
        return []
    names: list[str] = []
    for r in routes or []:
        rid = str(r.get("route_id") or "").strip()
        name = ""
        if rid:
            hit = profile_for_route(rid)
            if hit:
                name = hit[0]
        if not name:
            name = str(r.get("company") or r.get("route_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def filters_for(chat_id: int) -> dict:
    """Faol kompaniya bo'yicha filter; bo'lmasa bo'sh dict (hammasi).

    ADMIN bo'lmaganlar (kompaniya egasi yoki biriktirilgan yo'nalishli
    dispetcher) uchun faqat o'z kompaniyasi/yo'nalishi bo'yicha majburiy
    filter qaytariladi. Biriktirilgan yo'nalishlar kompaniyaning hammasini
    emas, faqat biriktirilgan route_id(lar)ni ko'rsatadi.
    """
    role = resolve_role(chat_id)
    if role is not Role.ADMIN:
        owned = allowed_names(chat_id)
        if not owned:
            return {"profile": "__none__", "route": "__deny__"}
        scoped = route_scope(chat_id)
        scoped_set = set(scoped)
        # Tanlangan yo'nalish ruxsat etilmagan bo'lsa e'tiborsiz qoldiramiz.
        active_route = _ACTIVE_ROUTE.get(chat_id, "").strip()
        if active_route and scoped_set and active_route not in scoped_set:
            active_route = ""

        if len(owned) == 1:
            if active_route:
                return {"profile": owned[0], "route": active_route}
            if scoped:
                return {"profile": owned[0], "route": " ".join(scoped)}
            return _filter_for(owned[0])

        # Bir nechta kompaniya:
        # 1) aniq route tanlangan bo'lsa — qaysi kompaniyaga tegishli ekanini topamiz
        if active_route:
            holders = [n for n in owned if active_route in company_route_ids(n)]
            if len(holders) == 1:
                return {"profile": holders[0], "route": active_route}
        name = get_active(chat_id)
        if name:
            cr = company_route_ids(name)
            if scoped_set:
                cr = [r for r in cr if r in scoped_set]
            if cr:
                return {"profile": name, "route": " ".join(cr)}
            return _filter_for(name)
        # Tanlov yo'q — faqat ruxsat etilgan yo'nalishlar bo'yicha cheklaymiz
        if scoped:
            return {"route": " ".join(scoped)}
        return {}

    # ADMIN: faol tanlov bo'lsa shu kompaniya bo'yicha, aks holda hammasi.
    name = get_active(chat_id)
    if not name:
        return {}
    active_route = _ACTIVE_ROUTE.get(chat_id, "")
    if active_route:
        return {"profile": name, "route": active_route}
    return _filter_for(name)


def _filter_for(name: str) -> dict:
    """Kompaniya nomi bo'yicha filter — barcha yo'nalishlarni yig'adi."""
    f: dict = {"profile": name}
    rids = company_route_ids(name)
    if rids:
        f["route"] = " ".join(rids)
    return f


def company_route_ids(name: str) -> list[str]:
    """Kompaniyaning barcha yo'nalish(lar)i (profile'lar bo'ylab yig'ilib)."""
    name_lower = str(name or "").strip().lower()
    out: list[str] = []
    for p in all_profiles():
        if str(p.get("name") or "").strip().lower() == name_lower:
            raw = str(p.get("routeVariantId") or "").strip()
            out.extend(t.strip() for t in raw.replace(",", " ").split() if t.strip())
    return list(dict.fromkeys(out))


def assigned_route_ids(chat_id: int) -> list[str]:
    """Dashboard'dan biriktirilgan aniq route_id'lar (noyob).

    Bo'sh simni yoki takroriylarni olib tashlaydi.
    """
    try:
        from ...db import get_storage
        st = get_storage()
        routes = st.dispatcher_routes(chat_id)
    except Exception:  # noqa: BLE001
        return []
    return list(dict.fromkeys(
        str(r.get("route_id") or "").strip()
        for r in (routes or []) if (r.get("route_id") or "").strip()))


def route_scope(chat_id: int) -> list[str]:
    """Foydalanuvchiga ruxsat etilgan route_id'lar to'plami.

    ADMIN -> bo'sh (cheklov yo'q); aks holda biriktirilgan yo'nalishlar +
    egalik qilgan kompaniya(lar)ning barcha yo'nalishlari. Bo'sh ro'yxat —
    hech qanday route cheklovi yo'q.
    """
    if resolve_role(chat_id) is Role.ADMIN:
        return []
    rids = assigned_route_ids(chat_id)
    for p in all_profiles():
        if chat_id in owner_chat_ids(p):
            raw = str(p.get("routeVariantId") or "").strip()
            rids.extend(t.strip() for t in raw.replace(",", " ").split() if t.strip())
    return list(dict.fromkeys(rid for rid in rids if rid))
