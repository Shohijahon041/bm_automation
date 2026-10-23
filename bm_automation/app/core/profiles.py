"""Kompaniya (profil) konfiguratsiyasi.

Har bir kompaniya uchun:
    - name        — qulay nom
    - profileId   — OneID profili ID (get-token-by-profile uchun; bo'sh = joriy token)
    - routeVariantId — kompaniyaning yo'nalishi
    - routeName   — jadval sarlavhasida ko'rsatiladigan yo'nalish nomi
    - kmRate      — yo'nalish uchun 1 km narxi (so'm); ustunlik tartibida:
      haydovchining shaxsiy `km_rate` → dashboard'dan o'rnatilgan `route_km`
      → kompaniya `kmRate` (global/env emas)
    - start1      — 1-chiqish konechkasi nomi (masalan "Prez Oldi")
    - start2      — 2-chiqish konechkasi nomi (masalan "Oybek Massiv")

Ko'p-firmali tizim (multi-company):
    - ownerChatIds — kompaniya egasi Telegram chat ID(lar)i ro'yxati.
      Ega faqat o'z kompaniyasini ko'radi; ADMIN hammasini.
    - company_groups — firma nomi -> Telegram guruh chat ID(lar)i xaritasi
      (profiles.json yuqori darajasida). Chiqish/reys eslatmalari faqat
      firmaning O'Z guruhiga boradi (boshqa firma guruhiga oqmaydi).
    - username / password — kompaniyaning o'z BM kredensiallari
      (ixtiyoriy). Berilgan bo'lsa, shu kompaniya uchun alohida client
      quriladi va token shu kompaniya nomi ostida alohida faylda saqlanadi.
      Bo'lmasa, adminning asosiy tokeni + login_by_profile(profileId)
      ishlatiladi.

profiles.json faylida saqlanadi. OneID login paytida aniqlangan profillar
ham shu faylga yoziladi (login-browser orqali).
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path

from ..utils.io import atomic_write

PROFILES_FILE = Path(os.getenv("BM_PROFILES_FILE", str(Path(__file__).resolve().parent.parent.parent / "profiles.json")))

DEFAULT_PROFILES = {
    "active": "FERGANATEX",
    "profiles": [
        {
            "name": "FERGANATEX",
            "profileId": "",
            "routeVariantId": "dfbfbe00-38a2-4ecc-8f3b-15b790308cbc",
            "routeName": "10-yo'nalish",
            "start1": "Prez Oldi",
            "start2": "Oybek Massiv",
        }
    ],
}


_PROFILES_LOCK = threading.RLock()
# Fayl mtime bo'yicha kesh: `load_profiles` har so'rovda (sessiya scope'i,
# `/api/me`) chaqiriladi — har safar diskdan o'qish OneDrive/HDD'da sezilarli.
# `save_profiles` keshni darhol yangilaydi, tashqi yozuv esa mtime orqali.
_PROFILES_CACHE: dict = {"path": "", "mtime": None, "data": None}


def load_profiles() -> dict:
    p = PROFILES_FILE
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime is not None:
        with _PROFILES_LOCK:
            c = _PROFILES_CACHE
            if (c["path"] == str(p) and c["mtime"] == mtime
                    and c["data"] is not None):
                return copy.deepcopy(c["data"])
    if not p.exists():
        save_profiles(DEFAULT_PROFILES)
        return copy.deepcopy(DEFAULT_PROFILES)
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        data = copy.deepcopy(DEFAULT_PROFILES)
    if "profiles" not in data:
        data["profiles"] = []
    with _PROFILES_LOCK:
        _PROFILES_CACHE.update(path=str(p), mtime=mtime,
                               data=copy.deepcopy(data))
    return data


def save_profiles(data: dict) -> Path:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(PROFILES_FILE, json.dumps(data, ensure_ascii=False, indent=2))
    try:
        mtime = PROFILES_FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _PROFILES_LOCK:
        _PROFILES_CACHE.update(path=str(PROFILES_FILE), mtime=mtime,
                               data=copy.deepcopy(data))
    return PROFILES_FILE


def get_profile(name: str = "", route_id: str = "") -> dict | None:
    """Kompaniya profili — `route_id` berilsa faqat o'sha yo'nalishdagi.

    Bitta kompaniyaning bir nechta yo'nalishi bor (AND ECO kabi), shuning
    uchun yo'nalish bo'yicha ajratish uchun `route_id` ishlatiladi.
    `route_id` bo'sh — birinchi mos profil qaytariladi (eski xulq).
    """
    data = load_profiles()
    name = (name or data.get("active", "")).strip().lower()
    want_route = str(route_id or "").strip().lower()
    first = None
    for p in data.get("profiles") or []:
        if name != str(p.get("name", "")).lower():
            continue
        if first is None:
            first = p
        if want_route and str(p.get("routeVariantId") or "").strip().lower() == want_route:
            return p
    return first


def all_profiles() -> list[dict]:
    return (load_profiles().get("profiles") or [])


def company_names() -> list[str]:
    """Kompaniya nomlari — bitta nom faqat bir marta (tartib saqlanadi)."""
    out: list[str] = []
    seen: set[str] = set()
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def grouped_profiles() -> list[dict]:
    """Profillarni firma bo'yicha guruhlaydi: bitta firma = N yo'nalish.

    Har bir guruhda `routes` ro'yxati bo'ladi; birinchi yo'nalish maydonlari
    eski API bilan moslik uchun guruhning yuqori darajasiga ham ko'chiriladi.
    Parol hech qachon qaytarilmaydi.
    """
    route_fields = ("routeVariantId", "routeName", "profileId",
                    "start1", "start2", "kmRate", "ownerChatIds")
    groups: dict[str, dict] = {}
    out: list[dict] = []
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        g = groups.get(key)
        if g is None:
            g = {"name": name, "routes": [], "routeCount": 0,
                 "has_credentials": False}
            groups[key] = g
            out.append(g)
        route = {k: p.get(k) for k in route_fields
                 if p.get(k) not in (None, "")}
        g["routes"].append(route)
        g["routeCount"] = len(g["routes"])
        if not g.get("routeVariantId") and route.get("routeVariantId"):
            for k in route_fields:
                if k in route:
                    g[k] = route[k]
        if not g.get("username") and str(p.get("username") or "").strip():
            g["username"] = str(p.get("username") or "").strip()
        if (str(p.get("username") or "").strip()
                and str(p.get("password") or "").strip()):
            g["has_credentials"] = True
    return out


def set_active(name: str) -> str:
    data = load_profiles()
    names = [str(p.get("name", "")).lower() for p in data.get("profiles", [])]
    if name.lower() not in names:
        raise ValueError(f"Profil topilmadi: {name}")
    data["active"] = name
    save_profiles(data)
    return name


def upsert_profile(profile: dict, match_route: str | None = None) -> str:
    """Kompaniya profilini saqlaydi (mavjudini yangilaydi yoki qo'shadi).

    Kalit — (nom, routeVariantId): bitta kompaniyaning bir nechta yo'nalishi
    alohida qator bo'ladi. `routeVariantId` bo'sh bo'lsa faqat nom bo'yicha
    moslashadi (eski xulq — profillarsiz yozuvlar uchun).

    `match_route` — mavjud qatorni avvalgi yo'nalishi bo'yicha topish
    (yo'nalish o'zgartirilayotganda eski qator tahrirlanib qoladi, yangisi
    dublikat sifatida qo'shilmaydi).
    """
    data = load_profiles()
    name = str(profile["name"])
    route = str(match_route if match_route is not None
                else profile.get("routeVariantId") or "").strip().lower()
    for p in data.get("profiles", []):
        if str(p.get("name", "")).lower() != name.lower():
            continue
        if route and str(p.get("routeVariantId") or "").strip().lower() != route:
            continue
        p.update(profile)
        break
    else:
        data.setdefault("profiles", []).append(profile)
    save_profiles(data)
    return name


def owner_chat_ids(profile: dict | None) -> list[int]:
    """Kompaniya egalarining Telegram chat ID'lari ro'yxati."""
    if not profile:
        return []
    raw = profile.get("ownerChatIds") or []
    out = []
    for v in raw if isinstance(raw, list) else [raw]:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out


_GROUPS_KEY = "company_groups"


def _norm_group_id(value) -> str:
    """Guruh chat ID'ini normallashtiradi (matn, `-100...`); yaroqsiz -> ''."""
    s = str(value or "").strip()
    if not s or len(s) > 20:
        return ""
    body = s[1:] if s.startswith("-") else s
    if not body.isdigit():
        return ""
    return s


def _norm_ids(values) -> list[str]:
    out: list[str] = []
    for v in values if isinstance(values, (list, tuple)) else [values]:
        gid = _norm_group_id(v)
        if gid and gid not in out:
            out.append(gid)
    return out


def company_group_ids(name: str) -> list[str]:
    """Firma nomiga bog'langan Telegram guruh chat ID'lari ro'yxati."""
    key = str(name or "").strip().lower()
    if not key:
        return []
    data = load_profiles()
    groups = data.get(_GROUPS_KEY) or {}
    if not isinstance(groups, dict):
        return []
    for gname, ids in groups.items():
        if str(gname).strip().lower() == key:
            return _norm_ids(ids)
    return []


def company_group_map() -> dict:
    """{firma nomi: [guruh chat ID, ...]} — guruh bog'langan firmalar."""
    groups = load_profiles().get(_GROUPS_KEY) or {}
    if not isinstance(groups, dict):
        return {}
    out = {}
    for gname, ids in groups.items():
        norm = _norm_ids(ids)
        if norm:
            out[str(gname)] = norm
    return out


def set_company_group_ids(name: str, ids) -> list[str]:
    """Firmaning guruh ID'larini to'liq almashtiradi (bo'sh ro'yxat — o'chiradi).

    Bitta Telegram guruh faqat bitta firmaga bog'lanadi: berilgan ID'lar
    boshqa firmalarning ro'yxatidan olib tashlanadi. Aks holda reys
    eslatmalari bitta guruhga ikki firma ma'lumoti bilan aralashib ketardi.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("Kompaniya nomi bo'sh")
    norm = _norm_ids(ids)
    data = load_profiles()
    groups = data.get(_GROUPS_KEY)
    if not isinstance(groups, dict):
        groups = {}
    rebuilt: dict[str, list[str]] = {}
    for gname, gids in groups.items():
        if str(gname).strip().lower() == name.lower():
            continue
        kept = [gid for gid in _norm_ids(gids) if gid not in norm]
        if kept:
            rebuilt[str(gname)] = kept
    if norm:
        rebuilt[name] = norm
    data[_GROUPS_KEY] = rebuilt
    save_profiles(data)
    return norm


def add_company_group(name: str, chat_id) -> list[str]:
    """Guruh chat ID'sini firmaning guruh ro'yxatiga qo'shadi (takrorlanmaydi)."""
    gid = _norm_group_id(chat_id)
    if not gid:
        raise ValueError("Guruh chat ID noto'g'ri")
    if not get_profile(name):
        raise ValueError(f"Kompaniya topilmadi: {name}")
    ids = company_group_ids(name)
    if gid not in ids:
        ids.append(gid)
    return set_company_group_ids(name, ids)


def remove_company_group(chat_id) -> int:
    """Guruh chat ID'sini BARCHA firmalardan o'chiradi. O'chirilganlar soni."""
    gid = _norm_group_id(chat_id)
    if not gid:
        return 0
    data = load_profiles()
    groups = data.get(_GROUPS_KEY) or {}
    if not isinstance(groups, dict):
        return 0
    removed = 0
    new_groups = {}
    for gname, ids in groups.items():
        norm = _norm_ids(ids)
        if gid in norm:
            norm = [i for i in norm if i != gid]
            removed += 1
        if norm:
            new_groups[str(gname)] = norm
    if not removed:
        return 0
    data[_GROUPS_KEY] = new_groups
    save_profiles(data)
    return removed


def add_profile(name: str, route_variant_id: str = "", route_name: str = "",
                profile_id: str = "") -> dict:
    """Yangi kompaniya qo'shadi (xuddi shu nom mavjud bo'lsa xato)."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("Kompaniya nomi bo'sh")
    if get_profile(name):
        raise ValueError(f"Kompaniya allaqachon mavjud: {name}")
    return add_route_profile(name, route_variant_id, route_name, profile_id)


def add_route_profile(name: str, route_variant_id: str = "", route_name: str = "",
                      profile_id: str = "") -> dict:
    """Mavjud kompaniyaga YANGI yo'nalish qatorini qo'shadi.

    Nom band bo'lishi mumkin (bir kompaniyaning bir nechta yo'nalishi) —
    shuning uchun xato faqat (nom, routeVariantId) juftligi takrorlansa
    beriladi. Birinchi yo'nalish emas, qolgan maydonlar shu kompaniyadan
    meros olinadi (kredensial, ownerChatIds, kmRate).
    """
    name = str(name or "").strip()
    route = str(route_variant_id or "").strip()
    if not name:
        raise ValueError("Kompaniya nomi bo'sh")
    if route and any(
            str(p.get("name") or "").strip().lower() == name.lower()
            and str(p.get("routeVariantId") or "").strip().lower()
            == route.lower()
            for p in all_profiles()):
        raise ValueError(f"Bu yo'nalish allaqachon mavjud: {name} / {route}")
    base = get_profile(name) or {}
    p = {"name": name,
         "profileId": str(profile_id or "").strip(),
         "routeVariantId": route,
         "routeName": str(route_name or "").strip()}
    for key in ("username", "password", "kmRate", "ownerChatIds"):
        if base.get(key) not in (None, "", []):
            p[key] = base[key]
    upsert_profile(p)
    return p


def set_credentials(name: str, username: str = "", password: str = "") -> dict:
    """Kompaniyaning o'z BM kredensiallarini saqlaydi.

    `username` yoki `password` bo'sh o'tkazilsa — mavjud qiymat SAQLANADI
    (faqat bittasini o'zgartirish mumkin). Ikkalasi ham bo'sh bo'lsa kredensial
    to'liq o'chiriladi.

    Kredensial kompaniya DARAJASIDA, shuning uchun kompaniyaning BARCHA
    yo'nalish qatorlariga yoziladi (bittasiga yozilsa qolgan yo'nalishlar
    sinxronda token ololmaydi).
    """
    data = load_profiles()
    target = str(name or "").strip().lower()
    u = (username or "").strip()
    pw = (password or "").strip()
    touched = False
    for p in data.get("profiles") or []:
        if str(p.get("name") or "").strip().lower() != target:
            continue
        touched = True
        if u:
            p["username"] = u
        if pw:
            p["password"] = pw
        if not u and not pw:
            p.pop("username", None)
            p.pop("password", None)
    if not touched:
        p = {"name": str(name)}
        if u:
            p["username"] = u
        if pw:
            p["password"] = pw
        data.setdefault("profiles", []).append(p)
    save_profiles(data)
    return get_profile(name) or p


def delete_profile(name: str) -> bool:
    """Kompaniyani profiles.json'dan o'chiradi (topilmasa False)."""
    data = load_profiles()
    target = str(name or "").strip().lower()
    before = len(data.get("profiles") or [])
    data["profiles"] = [
        p for p in (data.get("profiles") or [])
        if str(p.get("name", "")).strip().lower() != target
    ]
    if len(data["profiles"]) == before:
        return False
    if str(data.get("active", "")).strip().lower() == target:
        active = next((str(p.get("name")) for p in data["profiles"]), "")
        data["active"] = active
    groups = data.get(_GROUPS_KEY)
    if isinstance(groups, dict):
        data[_GROUPS_KEY] = {
            k: v for k, v in groups.items()
            if str(k).strip().lower() != target
        }
    save_profiles(data)
    return True


def rename_profile(old_name: str, new_name: str) -> int:
    """Kompaniya nomini profiles.json bo'ylab almashtiradi.

    Barcha yo'nalish qatorlari, `active` kaliti va `company_groups` yozuvi
    yangi nomga o'tadi. O'zgargan qatorlar soni qaytadi (0 = topilmadi).
    """
    old = str(old_name or "").strip().lower()
    new = str(new_name or "").strip()
    if not old or not new or old == new.lower():
        return 0
    data = load_profiles()
    changed = 0
    for p in data.get("profiles") or []:
        if str(p.get("name") or "").strip().lower() == old:
            p["name"] = new
            changed += 1
    if str(data.get("active") or "").strip().lower() == old:
        data["active"] = new
        changed += 1
    groups = data.get(_GROUPS_KEY)
    if isinstance(groups, dict):
        merged: dict = {}
        for k, v in groups.items():
            key = new if str(k).strip().lower() == old else k
            if key in merged and isinstance(merged[key], list) and isinstance(v, list):
                merged[key] = merged[key] + [x for x in v if x not in merged[key]]
            else:
                merged[key] = v
        if merged != groups:
            data[_GROUPS_KEY] = merged
            changed += 1
    if changed:
        save_profiles(data)
    return changed


def update_profile_fields(name: str, fields: dict, route_id: str = "") -> dict:
    """Kompaniya maydonlarini qisman yangilaydi (noma'lum maydonlar tashlanadi).

    Ruxsat etilgan maydonlar: profileId, routeVariantId, routeName, kmRate,
    start1, start2, ownerChatIds.

    Bitta kompaniyaning bir nechta yo'nalishi bo'lsa `route_id` bilan ESKI
    yo'nalish qatori tanlanadi; `route_id` bo'sh — nom bo'yicha birinchi qator.
    Keyin maydonlar (jumladan `routeVariantId`) shu qator ustida yangilanadi —
    yo'nalish o'zgarsa ham yangi qator qo'shilmaydi, mavjudi yangilanadi.
    """
    allowed = {"profileId", "routeVariantId", "routeName", "kmRate",
               "start1", "start2", "ownerChatIds"}
    updates = {k: v for k, v in (fields or {}).items() if k in allowed}
    data = load_profiles()
    target = str(name or "").strip().lower()
    want = str(route_id or "").strip().lower()
    chosen = None
    first = None
    for p in data.get("profiles") or []:
        if str(p.get("name", "")).strip().lower() != target:
            continue
        if first is None:
            first = p
        if want and str(p.get("routeVariantId") or "").strip().lower() == want:
            chosen = p
            break
    chosen = chosen or first
    if not chosen:
        raise ValueError(f"Profil topilmadi: {name}"
                         + (f" / {want}" if want else ""))
    chosen.update(updates)
    save_profiles(data)
    return chosen


def profile_by_owner(chat_id: int) -> list[dict]:
    """Berilgan chat ID egalik qiladigan kompaniyalar ro'yxati."""
    out = []
    for p in all_profiles():
        if chat_id in owner_chat_ids(p):
            out.append(p)
    return out


def profiles_with_credentials() -> list[dict]:
    """O'z kredensiallariga ega kompaniyalar (client_for_profile uchun)."""
    return [p for p in all_profiles()
            if (p.get("username") or "").strip()
            and (p.get("password") or "").strip()]


def discover_from_oneid(captured: list[dict], route_name: str = "") -> list[dict]:
    """OneID login paytida ushlangan profillarni faylga qo'shadi.

    captured: login-via-oneid/profiles javobidagi profillar.
    Faqat profileId + org nomi borlarini qo'shadi, mavjudlarini yangilaydi.
    """
    added = []
    for prof in captured or []:
        pid = prof.get("id") or prof.get("profileId")
        org = prof.get("organizationName") or prof.get("fullName") or prof.get("name")
        if not pid or not org:
            continue
        name = re.sub(r'["\u201c\u201d]', "", str(org)).strip()
        update = {"name": name, "profileId": str(pid)}
        # routeName bo'sh bo'lsa mavjud qiymatni o'chirmaydi (upsert merge
        # orqali faqat berilgan maydonlar yangilanadi).
        if route_name:
            update["routeName"] = route_name
        upsert_profile(update)
        added.append({"name": name, "profileId": str(pid)})
    return added
