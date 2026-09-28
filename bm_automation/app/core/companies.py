"""Ko'p-firmali tizim: firma bo'yicha client va login.

Har bir kompaniya:
  - o'z kredensiallariga ega bo'lsa (username/password) -> alohida BMClient
    quriladi va token shu kompaniya nomi ostida alohida faylda saqlanadi;
  - aks holda adminning asosiy tokeni ishlatiladi va (agar profileId bo'lsa)
    login_by_profile orqali shu profil tokeni olinadi.

`client_for_profile` barcha repo/dashboard qatlamlari uchun yagona kirish
nuqtasi. Har doim YANGI client qaytaradi — chaqiruvchi uni o'zi boshqaradi.
"""

from __future__ import annotations

from typing import Optional

from ..config.settings import Config, get_config
from .tokens import company_tokens_path
from .profiles import get_profile

__all__ = ["client_for_profile", "client_for_name", "company_token_path"]


def company_token_path(name: str):
    return company_tokens_path(name)


def _own_client(profile: dict) -> "object":
    """Kompaniyaning o'z kredensiallari bilan client quriladi."""
    from ..api.client import BMClient

    base = get_config().base_url
    cfg = Config(
        base_url=base,
        username=str(profile.get("username") or ""),
        password=str(profile.get("password") or ""),
        organization=str(profile.get("name") or ""),
    )
    client = BMClient(cfg, token_path=str(company_token_path(profile["name"])))
    client.login()
    return client


def _admin_client(profile: dict) -> "object":
    """Adminning asosiy tokeni; profileId bo'lsa shu profilga o'tiladi."""
    from ..api.client import BMClient

    client = BMClient()
    client.login()
    pid = str(profile.get("profileId") or "").strip()
    if pid:
        client.login_for_profile(pid, fallback_to_main=True)
    return client


def client_for_profile(profile: dict):
    """Profil (kompaniya) uchun authenticated client qaytaradi.

    Kompaniyaning o'z kredensiallari bo'lsa, avval o'z clienti + tokeni
    sinab ko'riladi (brauzer orqali oldindan login qilingan bo'lsa);
    ishlamasa admin tokeni (profileId bo'lsa shu profil bilan).
    """
    name = str(profile.get("name") or "").strip()
    if not name:
        raise ValueError("Profil nomi ko'rsatilmagan")
    own = bool((profile.get("username") or "").strip()
               and (profile.get("password") or "").strip())
    try:
        if own:
            try:
                return _own_client(profile)
            except Exception:
                return _admin_client(profile)
        return _admin_client(profile)
    except Exception as exc:
        raise type(exc)(f"[{name}] client qurish xatosi: {exc}") from exc


def client_for_name(name: str, username: str = "", password: str = ""):
    """Kompaniya nomi bo'yicha client. (username/password berilmasa
    saqlangan profil kredensiallaridan foydalanadi.)"""
    profile = get_profile(name)
    if not profile:
        raise ValueError(f"Kompaniya topilmadi: {name}")
    if username and password:
        profile = dict(profile)
        profile["username"] = username
        profile["password"] = password
    return client_for_profile(profile)


def _norm(s: str) -> str:
    """Firma nomlarini solishtirish uchun normalizatsiya (probel/katta harf)."""
    import re

    return re.sub(r"\s+", " ", str(s or "").replace("\u201c", "").replace("\u201d", "")).strip().lower()


def _detect_via_route(client, route_id: str) -> bool:
    """Berilgan token bilan route'ga kirish mumkinligini tekshiradi.

    Boshqa firmaga tegishli routeVariantId joriy token bilan 500/403
    qaytaradi, o'z firmasining route'i 200 -> qaysi firma ekanini shu
    orqali aniqlaymiz.
    """
    from ..repositories.gross_repo import GrossRepository

    from datetime import date

    today = date.today().isoformat()
    try:
        data = GrossRepository(client).route(route_id, today, today)
        return isinstance(data, dict) and bool(data)
    except Exception:
        return False


def detect_company(username: str, password: str):
    """Yangi login/parol bilan qaysi firma ekanini avtomatik aniqlaydi.

    BM API'da username/password autentifikatsiya OneID sabab ishlamaydi
    (401), shuning uchun firma `profiles.json` da saqlangan kredensiallar
    bilan solishtirib topiladi. Agar kredensial oldindan saqlangan bo'lmasa,
    API orqali ham sinab ko'riladi (ba'zi firmalar to'g'ridan-to'g'ri login
    qilishsa).

    Qaytaradi: (profile, client) — topilmasa (None, None).
    """
    from ..api.client import BMClient
    from ..config.settings import Config, get_config
    from .profiles import all_profiles

    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return None, None

    # 1) Saqlangan kredensiallar bilan solishtirish (asosiy usul).
    for p in all_profiles():
        saved_u = str(p.get("username") or "").strip()
        saved_p = str(p.get("password") or "").strip()
        if saved_u and saved_u.lower() == username.lower() and saved_p == password:
            return p, None  # client keyin client_for_profile orqali olinadi

    # 2) Fallback: to'g'ridan-to'g'ri API login (agar ishlasa).
    base = get_config().base_url
    cfg = Config(
        base_url=base,
        username=username,
        password=password,
        organization="",
    )
    client = BMClient(cfg, token_path=str(company_tokens_path("_detect")))
    try:
        client.login(force=True)
    except Exception:
        return None, None

    candidates = []
    for p in all_profiles():
        rid = str(p.get("routeVariantId") or "").strip()
        if not rid:
            continue
        if _detect_via_route(client, rid):
            candidates.append(p)

    # Bir nechta mos kelsa (masalan admin tokeni) — orgName bilan solishtiramiz.
    if len(candidates) > 1:
        name = _norm(username)
        for p in candidates:
            if name and name in _norm(p.get("name") or ""):
                return p, client
        for p in candidates:
            org = _norm(p.get("orgName") or p.get("routeName") or "")
            if org and name in org:
                return p, client
        return None, client
    if candidates:
        return candidates[0], client
    return None, client
