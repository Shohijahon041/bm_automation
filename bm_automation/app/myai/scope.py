"""MyAI uchun korxona (tenant) scope'i.

Task parametrlarida ``scope`` bo'lsa, DB tool faqat shu kompaniyaning
yo'nalishlari bo'yicha ma'lumot qaytaradi. Scope berilmasa (CLI, ichki
vazifalar) — avvalgidek cheklovsiz.

Scope lug'ati::

    {"is_admin": False, "company": "AND ECO", "companies": ["AND ECO"],
     "company_id": 3, "routes": ["<route uuid>", ...], "deny": False}
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_SCOPE: ContextVar[dict | None] = ContextVar("myai_scope", default=None)


def normalize_scope(raw: Any) -> dict | None:
    """Scope lug'atini tekshirilgan ko'rinishga keltiradi.

    ``is_admin`` — cheklov yo'q. Aks holda ``routes`` bo'sh bo'lsa
    ``deny=True`` (fail-closed): scope aniqlanmagan foydalanuvchi hech
    qanday korxona ma'lumotini ko'rmaydi.
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("is_admin"):
        return {"is_admin": True}
    routes = [str(r).strip() for r in (raw.get("routes") or [])
              if str(r).strip()]
    company = str(raw.get("company") or "").strip()
    companies = [str(c).strip() for c in (raw.get("companies") or [])
                 if str(c).strip()]
    if company and company not in companies:
        companies.insert(0, company)
    try:
        company_id = int(raw.get("company_id") or 0)
    except (TypeError, ValueError):
        company_id = 0
    return {
        "is_admin": False,
        "company": company or (companies[0] if len(companies) == 1 else ""),
        "companies": companies,
        "company_id": company_id,
        "routes": list(dict.fromkeys(routes)),
        "deny": bool(raw.get("deny")) or not (routes or companies
                                              or company_id),
    }


def set_scope(raw: Any):
    """Joriy task uchun scope o'rnatadi (token qaytaradi)."""
    return _SCOPE.set(normalize_scope(raw))


def reset_scope(token) -> None:
    _SCOPE.reset(token)


def current_scope() -> dict | None:
    return _SCOPE.get()


def is_admin() -> bool:
    """Cheklovsiz (admin/CLI) rejimmi?"""
    s = current_scope()
    return s is None or bool(s.get("is_admin"))


def allowed_routes(storage=None) -> list[str] | None:
    """Ruxsat etilgan yo'nalishlar ro'yxati.

    ``None`` — cheklov yo'q (admin). Bo'sh ro'yxat — fail-closed:
    foydalanuvchiga hech qanday ma'lumot ko'rsatilmaydi.
    """
    s = current_scope()
    if not s or s.get("is_admin"):
        return None
    cached = s.get("_routes_cache")
    if cached is not None:
        return cached
    routes = [str(r).strip() for r in (s.get("routes") or [])
              if str(r).strip()]
    if not routes and s.get("company_id") and storage is not None:
        try:
            rows = storage.query(
                "SELECT external_id FROM routes WHERE company_id = %s "
                "AND external_id <> ''", (int(s["company_id"]),))
            routes = [str(r.get("external_id") or "").strip() for r in rows]
            routes = [r for r in routes if r]
        except Exception:  # noqa: BLE001
            routes = []
    routes = list(dict.fromkeys(routes))
    s["_routes_cache"] = routes
    return routes


def allowed_companies() -> list[str] | None:
    """Ruxsat etilgan kompaniya nomlari; ``None`` — cheklov yo'q."""
    s = current_scope()
    if not s or s.get("is_admin"):
        return None
    return [str(c).strip() for c in (s.get("companies") or [])
            if str(c).strip()]


def scope_denied(message: str = "Bu ma'lumot boshqa korxonaga tegishli") -> dict:
    """Agent/tool uchun bir xil ko'rinishdagi rad javobi."""
    return {"found": False, "count": 0, "error": message,
            "scope_denied": True}


def scope_for_chat(chat_id: int | str | None) -> dict:
    """Telegram chat uchun kompaniya scope'i (bot bridge ishlatadi)."""
    if chat_id in (None, ""):
        return {"is_admin": True}
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return {"is_admin": True}
    from ..notifications.ops.roles import Role, resolve_role
    from ..notifications.ops import context as bot_context
    from ..core import tenant

    try:
        role = resolve_role(cid)
    except Exception:  # noqa: BLE001
        role = None
    if role is Role.ADMIN:
        return {"is_admin": True}
    try:
        names = bot_context.allowed_names(cid)
    except Exception:  # noqa: BLE001
        names = []
    routes: list[str] = []
    for name in names:
        try:
            found = tenant.company_routes(name)
        except Exception:  # noqa: BLE001
            found = []
        for rid in found:
            if rid not in routes:
                routes.append(rid)
    try:
        for rid in bot_context.assigned_route_ids(cid):
            if rid not in routes:
                routes.append(rid)
    except Exception:  # noqa: BLE001
        pass
    return {
        "is_admin": False,
        "company": names[0] if len(names) == 1 else "",
        "companies": names,
        "routes": routes,
        "deny": not (names or routes),
    }


def scope_for_session(scope: dict | None) -> dict:
    """Dashboard sessiyasi (deps.Scope) dan MyAI scope'i.

    Rol=ADMIN bo'lsa ham sessiyada kompaniya bo'lishi mumkin — bunday
    akkaunt GLOBAL hisoblanmaydi (korxona admini faqat o'z korxonasini
    ko'radi). Faqat kompaniyasi ham, yo'nalishlari ham yo'q ADMIN —
    cheklovsiz.
    """
    scope = scope or {}
    company = str(scope.get("company") or "").strip()
    routes = [str(r).strip() for r in (scope.get("routes") or [])
              if str(r).strip()]
    if scope.get("is_admin") and not (company or routes):
        return {"is_admin": True}
    company_id = 0
    try:
        company_id = int(scope.get("company_id") or 0)
    except (TypeError, ValueError):
        company_id = 0
    if company and (not routes or not company_id):
        try:
            from ..core import tenant
            if not routes:
                routes = [str(r).strip() for r in tenant.company_routes(company)
                          if str(r).strip()]
            if not company_id:
                company_id = int(tenant.company_id_for_name(company) or 0)
        except Exception:  # noqa: BLE001
            pass
    return {
        "is_admin": False,
        "company": company,
        "companies": [company] if company else [],
        "company_id": company_id,
        "routes": list(dict.fromkeys(routes)),
        "deny": not (company or routes or company_id),
    }


def scope_company_label(raw: Any) -> str:
    """Task yozuviga saqlanadigan korxona yorlig'i ('' = global/admin)."""
    scope = normalize_scope(raw)
    if not scope or scope.get("is_admin"):
        return ""
    names = [str(c).strip() for c in (scope.get("companies") or [])
             if str(c).strip()]
    if not names and scope.get("company"):
        names = [str(scope["company"])]
    return ", ".join(dict.fromkeys(names))
