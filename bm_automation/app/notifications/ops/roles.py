"""Role-based access — ADMIN / DISPATCHER / MANAGER / VIEWER.

Rollar chat_id bo'yicha aniqlanadi (ustunlik):

- `TG_ADMIN_IDS` / `TG_DISPATCHER_IDS` / `TG_MANAGER_IDS` / `TG_DRIVER_IDS`
  — env orqali aniq rol berilgan chat ID'lar (eng yuqori ustunlik);
- Admin runtime'da tayinlagan rol (`/setrole`, `bot_users.json` da saqlanadi);
- DB haydovchi bog'lanishi (`driver_link`) → DRIVER;
- `TG_DEFAULT_ROLE` — boshqa barchasiga (standart `viewer`).

Kirish modeli:
- <b>Ochiq rejim (default):</b> `TG_ALLOWED_IDS` ko'rsatilmagan va
  `TG_STRICT_ACCESS` o'chirilgan bo'lsa, hamma /start bosishi mumkin.
  Xavfsiz amallar (`can`) roli orqali cheklanadi.
- <b>Qat'iy rejim:</b> `TG_ALLOWED_IDS` berilgan yoki `TG_STRICT_ACCESS=on`
  bo'lsa, bot faqat ro'yxatdagi chat'lar uchun ishlaydi — qolganlar
  butunlay bloklanadi (`is_allowed`).

ADMIN barcha amallarga ega; DISPATCHER ko'rish + eksport/sinxron;
MANAGER faqat o'z firmasi ma'lumotlarini ko'radi (amal huquqlari yo'q);
VIEWER faqat ko'rish.
"""

from __future__ import annotations

import json
import os
import threading
from enum import Enum

from ...config.settings import telegram_settings
from ...core.sections import sections_allowed


class Role(str, Enum):
    """Foydalanuvchi roli (yuqori tartib kattaroq huquq)."""

    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    MANAGER = "MANAGER"
    DRIVER = "DRIVER"
    VIEWER = "VIEWER"


_RANK = {Role.ADMIN: 4, Role.DISPATCHER: 3, Role.MANAGER: 2, Role.DRIVER: 1, Role.VIEWER: 0}

# Haydovchi chat_id → driver_id keshi (DB dan aniqlanganlar) — thread-safe
_DRIVER_CHAT_CACHE: dict[int, str] = {}
_DRIVER_CHAT_LOCK = threading.Lock()


def driver_id_for_chat(chat_id: int) -> str | None:
    """chat_id haydovchiga tegishlimi? driver_id qaytaradi, yo'qsa None."""
    if not chat_id:
        return None
    with _DRIVER_CHAT_LOCK:
        if chat_id in _DRIVER_CHAT_CACHE:
            return _DRIVER_CHAT_CACHE[chat_id]
    try:
        from ...db import get_storage
        st = get_storage()
        row = st.find_driver_by_telegram(chat_id)
        if row:
            did = str(row.get("driver_id") or "")
            if did:
                with _DRIVER_CHAT_LOCK:
                    _DRIVER_CHAT_CACHE[chat_id] = did
                return did
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_driver_role(chat_id: int) -> Role | None:
    """DB dan haydovchi aniqlansa DRIVER qaytaradi (qora ro'yxatdan tashqari)."""
    did = driver_id_for_chat(chat_id)
    if did:
        try:
            from ...db import get_storage
            profile = get_storage().find("driver_profiles", driver_id=str(did)) or {}
            if profile.get("blacklisted"):
                return None
        except Exception:  # noqa: BLE001
            pass
        return Role.DRIVER
    return None

# Qaysi rol qaysi amalni bajarishi mumkin. Ro'yxatda yo'q amallar hammaga ochiq.
_ACTION_ROLES: dict[str, set[Role]] = {
    "sync": {Role.ADMIN, Role.DISPATCHER},             # /sync, 🔄 Sync, Grafik
    "resend": {Role.ADMIN, Role.DISPATCHER},            # /resend (qayta yuborish)
    "addcompany": {Role.ADMIN},                         # /addcompany (yangi firma)
    "document": {Role.ADMIN, Role.DISPATCHER},          # Excel yuklab to'ldirish
    "export": {Role.ADMIN, Role.DISPATCHER},             # /export
    "driver_edit": {Role.ADMIN, Role.DISPATCHER},       # kunlik qayd / jarima kiritish
    "plan": {Role.ADMIN, Role.DISPATCHER},              # ertangi reja
    "salary": {Role.ADMIN, Role.MANAGER},  # oylik/maosh (faqat ADMIN/MANAGER)
}


def _ids(raw: str) -> list[int]:
    out = []
    for tok in raw.replace(",", " ").replace(";", " ").split():
        tok = tok.strip()
        if tok.isdigit():
            out.append(int(tok))
    return out


def configured_roles() -> dict[int, Role]:
    """Sozlangan chat_id → rol xaritasi."""
    s = telegram_settings()
    out: dict[int, Role] = {}
    for cid in _ids(s.get("admin_ids", "")):
        out.setdefault(cid, Role.ADMIN)
    for cid in _ids(s.get("dispatcher_ids", "")):
        out.setdefault(cid, Role.DISPATCHER)
    for cid in _ids(s.get("manager_ids", "")):
        out.setdefault(cid, Role.MANAGER)
    for cid in _ids(s.get("driver_ids", "")):
        out.setdefault(cid, Role.DRIVER)
    # Eski konfiguratsiya: chat_id/driver_chat_id ham ADMIN hisoblanadi
    for key in ("chat_id", "driver_chat_id"):
        raw = str(s.get(key) or "").strip()
        if raw.isdigit():
            out.setdefault(int(raw), Role.ADMIN)
    return out


def allowed_ids() -> list[int]:
    """Qat'iy rejimda ruxsat etilgan chat ID'lar ro'yxati.

    Qat'iy rejim `TG_ALLOWED_IDS` aniq ko'rsatilganda yoki
    `TG_STRICT_ACCESS=on` bo'lganda faollashadi. U holda ro'yxat
    `TG_ALLOWED_IDS` (ustun) + admin/dispatcher/manager/driver + eski
    chat_id'lar birlashtiriladi.
    """
    s = telegram_settings()
    strict_raw = str(s.get("strict_access") or "").strip().lower()
    ids = _ids(s.get("allowed_ids", ""))
    if ids:
        return _strict_combined(s)
    if strict_raw in ("on", "1", "true", "yes"):
        return _strict_combined(s)
    return []  # ochiq rejim



def _strict_combined(s: dict) -> list[int]:
    ids = _ids(s.get("allowed_ids", ""))
    ids += _ids(s.get("admin_ids", "")) + _ids(s.get("dispatcher_ids", ""))
    ids += _ids(s.get("manager_ids", "")) + _ids(s.get("driver_ids", ""))
    for key in ("chat_id", "driver_chat_id"):
        raw = str(s.get(key) or "").strip()
        if raw.isdigit():
            ids.append(int(raw))
    return list(dict.fromkeys(ids))


def is_allowed(chat_id: int | None) -> bool:
    """chat_id botdan foydalanish huquqiga egami.

    - <b>Ochiq rejim (default):</b> `TG_ALLOWED_IDS` ko'rsatilmagan va
      `TG_STRICT_ACCESS` o'chirilgan bo'lsa, barcha foydalanuvchilarga
      /start kirishiga ruxsat beriladi. Xavfsiz amallar roli orqali
      cheklanadi (viewer default), `can()` huquqni nazorat qiladi.
    - <b>Qat'iy rejim:</b> `TG_ALLOWED_IDS` berilgan yoki
      `TG_STRICT_ACCESS=on` bo'lsa, faqat ro'yxatdagi chat'lar ishlaydi.
    """
    if chat_id is None:
        return False
    ids = allowed_ids()
    if not ids:
        return True
    return int(chat_id) in ids


def _parse_role(value: str) -> Role | None:
    """Rol matnini Role ga aylantiradi (noto'g'ri bo'lsa None)."""
    if not value:
        return None
    v = str(value).strip().upper()
    # Emoji/prefix (masalan "🛡 ADMIN" yoki "Диспетчер") dan tozalaymiz
    v = "".join(ch for ch in v if ch.isalpha() or ch.isspace()).strip()
    if v in Role.__members__:
        return Role(v)
    aliases = {
        "ADMIN": Role.ADMIN, "ADMINISTRATOR": Role.ADMIN, "ADMINISTRATORLAR": Role.ADMIN,
        "DISPATCHER": Role.DISPATCHER, "DISPETCHER": Role.DISPATCHER,
        "MANAGER": Role.MANAGER,
        "DRIVER": Role.DRIVER, "HAYDOVCHI": Role.DRIVER,
        "VIEWER": Role.VIEWER, "KUZATUVCHI": Role.VIEWER,
    }
    return aliases.get(v)


def stored_role(chat_id: int) -> Role | None:
    """bot_users.json da admin tayinlagan rolni qaytaradi (yo'q bo'lsa None)."""
    try:
        from ...core import bot_users
        u = bot_users.get_user(chat_id)
        if not u:
            return None
        return _parse_role(u.get("role"))
    except Exception:  # noqa: BLE001
        return None


def resolve_role(chat_id: int | None) -> Role:
    """chat_id uchun rolni aniqlaydi (sozlanmagan bo'lsa default).

    Prioritet: env (TG_) sozlamalari → admin tayinlagan rol (bot_users)
    → DB haydovchi → default role.
    """
    chat_id = int(chat_id) if chat_id is not None else 0
    roles = configured_roles()
    if not roles:
        # Env sozlanmagan bo'lsa ham tayinlangan rol / haydovchi hurmat qilinadi.
        # Aks holda xavfsiz default (odatda VIEWER) — hamma ADMIN bo'lib qolmasin.
        assigned = stored_role(chat_id)
        if assigned:
            return assigned
        driver_role = _resolve_driver_role(chat_id)
        if driver_role:
            return driver_role
        default = (telegram_settings().get("default_role") or "viewer").upper()
        return Role(default) if default in Role.__members__ else Role.VIEWER
    if chat_id in roles:
        return roles[chat_id]
    # Admin tayinlagan rol (only if chat_id not in env) — runtime override
    assigned = stored_role(chat_id)
    # DB dan haydovchi tekshirish. Muhim: haydovchi keyinchalik bog'langan
    # bo'lsa, bot_usersda qolgan eski VIEWER rolidan USTUN turadi — aks
    # holda bog'langan haydovchi VIEWER rejimida qolib, barcha bo'limlarni
    # ko'rib qoladi. Baland rollar (ADMIN/DISPATCHER/MANAGER) o'z qoladi.
    driver_role = _resolve_driver_role(chat_id)
    if driver_role is Role.DRIVER and assigned is Role.VIEWER:
        return driver_role
    if assigned:
        return assigned
    if driver_role:
        return driver_role
    # Dashboard'dan yo'nalish biriktirilganlar — aniq rol berilmagan bo'lsa
    # dispetcher hisoblanadi (biriktirilgan yo'nalish(lar) bo'yicha ishlaydi).
    try:
        from ...db import get_storage
        if get_storage().has_dispatcher_routes(chat_id):
            return Role.DISPATCHER
    except Exception:  # noqa: BLE001
        pass
    default = (telegram_settings().get("default_role") or "viewer").upper()
    return Role(default) if default in Role.__members__ else Role.VIEWER


def can(role: Role, action: str) -> bool:
    """Rol berilgan amalni bajarishi mumkinmi."""
    allowed = _ACTION_ROLES.get(action)
    if allowed is None:
        return True
    return role in allowed


# ---------------------------------------------------------------------------
# Bo'limlar (per-user ruxsatlar)
# ---------------------------------------------------------------------------

def bot_pages(chat_id: int | None) -> list | None:
    """bot_users.json da admin belgilagan bo'limlar (None = cheklovsiz)."""
    if chat_id is None:
        return None
    try:
        from ...core import bot_users
        return bot_users.get_pages(int(chat_id))
    except Exception:  # noqa: BLE001
        return None


def linked_dashboard_pages(chat_id: int | None) -> list | None:
    """Telegram akkaunti panel foydalanuvchisiga bog'langan bo'lsa — uning
    bo'limlari (panelda berilgan ruxsat botda ham amal qiladi).

    Bog'lanish `dashboard_users.data.telegram_chat_id` orqali saqlanadi.
    ``super``/o'chirilgan akkauntlar hisobga olinmaydi.
    """
    if chat_id is None:
        return None
    try:
        from ...db.storage import get_storage
        st = get_storage()
        if not st.enabled:
            return None
        want = str(int(chat_id))
        for row in st.dashboard_users_list():
            try:
                data = json.loads(str(row.get("data") or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("telegram_chat_id") or "").strip() != want:
                continue
            if not int(row.get("active") or 0):
                continue
            pages = data.get("pages")
            if isinstance(pages, list):
                return [str(p) for p in pages]
            return None
    except Exception:  # noqa: BLE001
        return None
    return None


def is_super_chat(chat_id: int | None) -> bool:
    """chat_id tizim egasiga (super-admin) tegishlimi?

    Ikkita manba:
      - `TG_OWNER_IDS` env — vergul bilan ajratilgan egalar chat ID'lari;
      - `dashboard_users` jadvalidagi `super` bayrog'li akkauntga bog'langan
        Telegram chat (panel'da kiritilgan `telegram_chat_id`).

    Bilim bazasi kabi faqat egasiga ko'rinadigan bo'limlar shu tekshiruv
    orqali himoyalangan (dashboard tomonidagi `is_super` bilan bir xil).
    """
    if not chat_id:
        return False
    try:
        raw = str(os.environ.get("TG_OWNER_IDS") or "")
        if raw:
            for item in raw.replace(";", ",").split(","):
                item = item.strip()
                if item and item.lstrip("-").isdigit() and int(item) == int(chat_id):
                    return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...db.storage import get_storage
        st = get_storage()
        if not st.enabled:
            return False
        want = str(int(chat_id))
        for row in st.dashboard_users_list():
            try:
                data = json.loads(str(row.get("data") or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or not data.get("super"):
                continue
            if not int(row.get("active") or 0):
                continue
            if str(data.get("telegram_chat_id") or "").strip() != want:
                continue
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def pages_for(chat_id: int | None) -> list | None:
    """Amaldagi bo'limlar ro'yxati (`None` = faqat rol bo'yicha cheklovsiz).

    Ustunlik: bot foydalanuvchisiga berilgan ro'yxat → panel
    foydalanuvchisiga bog'langan ro'yxat → cheklov yo'q.
    """
    pages = bot_pages(chat_id)
    if pages is not None:
        return pages
    return linked_dashboard_pages(chat_id)


def section_allowed(chat_id: int | None, section: str,
                    role: "Role | None" = None) -> bool:
    """chat_id shu bo'lim buyruq/bo'limidan foydalana oladimi.

    ADMIN har doim ruxsatli; ro'yxat berilmagan bo'lsa — faqat rol amal
    qiladi (eski xatti-harakat).
    """
    if not section:
        return True
    try:
        role = role if role is not None else resolve_role(chat_id)
    except Exception:  # noqa: BLE001
        role = None
    if role is Role.ADMIN:
        return True
    try:
        return sections_allowed(pages_for(chat_id), section)
    except Exception:  # noqa: BLE001
        return True


def sections_of(chat_id: int | None) -> list[str] | None:
    """Foydalanuvchiga ochiq bo'limlar (None = cheklovsiz)."""
    return pages_for(chat_id)


def require_role(chat_id: int, action: str) -> Role | None:
    """Ruxsat bo'lsa rol, bo'lmasa None qaytaradi."""
    role = resolve_role(chat_id)
    return role if can(role, action) else None


def role_label(role: Role) -> str:
    labels = {
        Role.ADMIN: "🛡 ADMIN",
        Role.DISPATCHER: "📡 DISPATCHER",
        Role.MANAGER: "🏢 FIRMA BOSHQARUVCHI",
        Role.DRIVER: "🚗 HAYDOVCHI",
        Role.VIEWER: "👀 VIEWER",
    }
    return labels.get(role, role.value)
