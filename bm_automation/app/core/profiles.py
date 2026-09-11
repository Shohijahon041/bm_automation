"""Kompaniya (profil) konfiguratsiyasi.

Har bir kompaniya uchun:
    - name        — qulay nom
    - profileId   — OneID profili ID (get-token-by-profile uchun; bo'sh = joriy token)
    - routeVariantId — kompaniyaning yo'nalishi
    - routeName   — jadval sarlavhasida ko'rsatiladigan yo'nalish nomi
    - kmRate      — yo'nalish uchun 1 km narxi (so'm); ustunlik tartibida
      eng pastda: bot orqali o'rnatilgan → `KM_RATE` env → haydovchining
      shaxsiy `km_rate` → kompaniya `kmRate`
    - start1      — 1-chiqish konechkasi nomi (masalan "Prez Oldi")
    - start2      — 2-chiqish konechkasi nomi (masalan "Oybek Massiv")

Ko'p-firmali tizim (multi-company):
    - ownerChatIds — kompaniya egasi Telegram chat ID(lar)i ro'yxati.
      Ega faqat o'z kompaniyasini ko'radi; ADMIN hammasini.
    - username / password — kompaniyaning o'z BM kredensiallari
      (ixtiyoriy). Berilgan bo'lsa, shu kompaniya uchun alohida client
      quriladi va token shu kompaniya nomi ostida alohida faylda saqlanadi.
      Bo'lmasa, adminning asosiy tokeni + login_by_profile(profileId)
      ishlatiladi.

profiles.json faylida saqlanadi. OneID login paytida aniqlangan profillar
ham shu faylga yoziladi (login-browser orqali).
"""

from __future__ import annotations

import json
import os
import re
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


def load_profiles() -> dict:
    if not PROFILES_FILE.exists():
        save_profiles(DEFAULT_PROFILES)
        return json.loads(json.dumps(DEFAULT_PROFILES))
    try:
        with open(PROFILES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        data = DEFAULT_PROFILES
    if "profiles" not in data:
        data["profiles"] = []
    return data


def save_profiles(data: dict) -> Path:
    PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(PROFILES_FILE, json.dumps(data, ensure_ascii=False, indent=2))
    return PROFILES_FILE


def get_profile(name: str = "") -> dict | None:
    data = load_profiles()
    name = (name or data.get("active", "")).strip().lower()
    for p in data.get("profiles") or []:
        if name == str(p.get("name", "")).lower():
            return p
    return None


def all_profiles() -> list[dict]:
    return (load_profiles().get("profiles") or [])


def set_active(name: str) -> str:
    data = load_profiles()
    names = [str(p.get("name", "")).lower() for p in data.get("profiles", [])]
    if name.lower() not in names:
        raise ValueError(f"Profil topilmadi: {name}")
    data["active"] = name
    save_profiles(data)
    return name


def upsert_profile(profile: dict) -> str:
    data = load_profiles()
    name = str(profile["name"])
    for p in data.get("profiles", []):
        if str(p.get("name", "")).lower() == name.lower():
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


def set_owner_chat_ids(name: str, chat_ids: list[int]) -> dict:
    """Kompaniyaga egalarni biriktiradi (upsert orqali)."""
    p = get_profile(name) or {"name": name}
    p["ownerChatIds"] = [int(c) for c in chat_ids if str(c).strip().lstrip("-").isdigit()]
    upsert_profile(p)
    return p


def set_credentials(name: str, username: str, password: str) -> dict:
    """Kompaniyaning o'z BM kredensiallarini saqlaydi."""
    p = get_profile(name) or {"name": name}
    p["username"] = (username or "").strip()
    p["password"] = (password or "").strip()
    upsert_profile(p)
    return p


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
