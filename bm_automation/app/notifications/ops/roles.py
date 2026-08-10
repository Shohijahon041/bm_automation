"""Role-based access — ADMIN / DISPATCHER / VIEWER.

Rollar chat_id bo'yicha beriladi (bot foydalanuvchilarni bilmaydi):

- `TG_ADMIN_IDS` — vergul bilan ajratilgan chat ID'lar → ADMIN;
- `TG_DISPATCHER_IDS` — chat ID'lar → DISPATCHER;
- `TG_CHAT_ID` / `TG_DRIVER_CHAT_ID` (eski sozlamalar) → ADMIN;
- `TG_DEFAULT_ROLE` — ro'yxatga kirmagan chat uchun rol (standart `viewer`).

Hech qanday rol sozlanmagan bo'lsa hamma ADMIN deb hisoblanadi (eski ochiq
xatti-harakat bilan moslashuv).

ADMIN barcha amallarga ega; DISPATCHER ko'rish + eksport/sinxron;
VIEWER faqat ko'rish.
"""

from __future__ import annotations

from enum import Enum

from ...config.settings import telegram_settings


class Role(str, Enum):
    """Foydalanuvchi roli (yuqori tartib kattaroq huquq)."""

    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    VIEWER = "VIEWER"


_RANK = {Role.ADMIN: 3, Role.DISPATCHER: 2, Role.VIEWER: 1}

# Qaysi rol qaysi amalni bajarishi mumkin. Ro'yxatda yo'q amallar hammaga ochiq.
_ACTION_ROLES: dict[str, set[Role]] = {
    "sync": {Role.ADMIN},                          # /sync, 🔄 Sync
    "resend": {Role.ADMIN},                        # /resend
    "document": {Role.ADMIN, Role.DISPATCHER},     # Excel yuklab to'ldirish
    "export": {Role.ADMIN, Role.DISPATCHER},       # /export
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
    # Eski konfiguratsiya: chat_id/driver_chat_id ham ADMIN hisoblanadi
    for key in ("chat_id", "driver_chat_id"):
        raw = str(s.get(key) or "").strip()
        if raw.isdigit():
            out.setdefault(int(raw), Role.ADMIN)
    return out


def resolve_role(chat_id: int | None) -> Role:
    """chat_id uchun rolni aniqlaydi (sozlanmagan bo'lsa default)."""
    chat_id = int(chat_id) if chat_id is not None else 0
    roles = configured_roles()
    if not roles:
        # Hech kim sozlanmagan → eski ochiq rejim (hamma ADMIN)
        return Role.ADMIN
    if chat_id in roles:
        return roles[chat_id]
    default = (telegram_settings().get("default_role") or "viewer").upper()
    return Role(default) if default in Role.__members__ else Role.VIEWER


def can(role: Role, action: str) -> bool:
    """Rol berilgan amalni bajarishi mumkinmi."""
    allowed = _ACTION_ROLES.get(action)
    if allowed is None:
        return True
    return role in allowed


def require_role(chat_id: int, action: str) -> Role | None:
    """Ruxsat bo'lsa rol, bo'lmasa None qaytaradi."""
    role = resolve_role(chat_id)
    return role if can(role, action) else None


def role_label(role: Role) -> str:
    labels = {
        Role.ADMIN: "🛡 ADMIN",
        Role.DISPATCHER: "📡 DISPATCHER",
        Role.VIEWER: "👀 VIEWER",
    }
    return labels.get(role, role.value)
