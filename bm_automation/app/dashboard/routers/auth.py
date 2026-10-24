"""Auth router — login, logout, me, dashboard-users."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ...core.sections import clean_sections
from ..deps import (
    PAGE_KEYS, Scope, audit_event, client_ip,
    create_session, drop_session, drop_user_sessions, effective_pages,
    hash_password, optional_scope, password_error, user_tg,
    is_super_username, login_allowed, login_fail, login_success,
    refresh_session_cookie, resolve_scope, session_user, super_admin_names,
    sync_env_admin_password, verify_password,
    invalidate_super_cache, invalidate_user_access,
    _ROLE_RANK, _session_token_from_request, _dashboard_token,
    request_is_https,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


_UNSET = object()


def _clean_pages(raw) -> tuple[list | None, str]:
    """Payload'dagi `pages`ni tekshiradi -> (ro'yxat|None, xato).

    None/'' -> (None, ''): rol bo'yicha standart (cheklovsiz, eski holat).
    Noma'lum kalit bo'lsa xato qaytadi (jimgina tashlab yuborilmaydi).
    """
    return clean_sections(raw)


def _clean_tg(raw) -> tuple[str, str]:
    """Telegram chat_id'ni tekshiradi -> (qiymat, xato).

    Bo'sh qiymat = bog'lanishni olib tashlash. Faqat raqamlar (ixtiyoriy
    '-100...' guruh ID'si ham) qabul qilinadi.
    """
    tg = str(raw or "").strip()
    if not tg:
        return "", ""
    digits = tg[1:] if tg.startswith("-") else tg
    if not digits.isdigit():
        return "", "Telegram chat ID faqat raqamlardan iborat bo'lishi kerak"
    return tg, ""


def _user_data(cur_data: str, pages=_UNSET, tg_id=_UNSET) -> str:
    """`data` JSON'ida `pages`/`telegram_chat_id`ni yangilaydi.

    Faqat yuborilgan maydonlar o'zgaradi, boshqa kalitlar saqlanadi.
    """
    try:
        data = json.loads(str(cur_data or "{}"))
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if pages is not _UNSET:
        if pages is None:
            data.pop("pages", None)
        else:
            data["pages"] = list(pages)
    if tg_id is not _UNSET:
        tg = str(tg_id or "").strip()
        if tg:
            data["telegram_chat_id"] = tg
        else:
            data.pop("telegram_chat_id", None)
    return json.dumps(data, ensure_ascii=False)


def _row_tg(row: dict) -> str:
    """Qatordagi `data.telegram_chat_id` (bog'langan Telegram akkaunt)."""
    try:
        data = json.loads(str((row or {}).get("data") or "{}"))
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("telegram_chat_id") or "")


def _row_pages(row: dict) -> list | None:
    """Qatordagi `data.pages` (None = rol bo'yicha standart)."""
    try:
        data = json.loads(str((row or {}).get("data") or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pages = data.get("pages")
    if not isinstance(pages, list):
        return None
    return [str(p) for p in pages if str(p) in PAGE_KEYS]


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/login")
def login(body: LoginRequest, request: Request, response: Response):
    username = body.username.strip().lower()
    password = body.password
    if not username or not password:
        raise HTTPException(400, "Login va parol kiriting")

    ip = client_ip(request)
    if not login_allowed(ip, username):
        audit_event("login.blocked", request, user=username, ok=False,
                    detail="rate-limit")
        raise HTTPException(429, "Juda ko'p xato urinish — 15 daqiqa kuting")

    from ...db.storage import get_storage
    user = get_storage().dashboard_user_get(username=username)
    if not user or int(user.get("active") or 0) == 0:
        login_fail(ip, username)
        audit_event("login.fail", request, user=username, ok=False,
                    detail="not-found-or-inactive")
        raise HTTPException(401, "Login yoki parol noto'g'ri")

    if not verify_password(password, str(user.get("salt") or ""), str(user.get("password_hash") or "")):
        login_fail(ip, username)
        audit_event("login.fail", request, user=username, ok=False,
                    detail="bad-password")
        raise HTTPException(401, "Login yoki parol noto'g'ri")

    role = str(user.get("role") or "VIEWER").strip().upper()
    company = str(user.get("company") or "").strip()
    # Kompaniya akkaunti bloklangan/o'chirilgan bo'lsa sessiya yaratilmaydi.
    # Aks holda foydalanuvchi "muvaffaqiyatli" kirib, har bir API'da 403
    # olardi (sabab ko'rinmasdi). Parol tekshiruvidan keyin — login
    # mavjudligini oshkor qilmaslik uchun.
    if role != "ADMIN":
        from ...core import tenant
        comp = tenant.company_state(company)
        if comp is None or not tenant.company_active(comp):
            login_fail(ip, username)
            audit_event(
                "login.fail", request, user=username, target=company, ok=False,
                detail="unknown-company" if comp is None else "inactive-company")
            raise HTTPException(
                403, "Kompaniya topilmadi yoki faol emas. "
                     "Administratorga murojaat qiling")

    login_success(ip, username)
    token = create_session({"username": username, "id": user.get("id"),
                            "role": role, "company": company})
    audit_event("login.success", request, user=username, target=company,
                detail=role)

    from fastapi.responses import JSONResponse as _JR
    result = _JR({"ok": True, "username": username, "role": role, "company": company})
    result.set_cookie("bm_session", token, path="/", httponly=True,
                      samesite="lax", secure=request_is_https(request),
                      max_age=12 * 3600)
    return result


@router.post("/api/logout")
def logout(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    token = _session_token_from_request(request, creds)
    actor = session_user(token) or {}
    drop_session(token)
    audit_event("logout", request, user=str(actor.get("username") or ""),
                target=str(actor.get("company") or ""))
    from fastapi.responses import JSONResponse as _JR
    result = _JR({"ok": True})
    result.delete_cookie("bm_session", path="/")
    return result


@router.get("/api/me")
def me(request: Request, response: Response,
             scope: dict | None = Depends(optional_scope)):
    # Login qilmagan SPA ham shu endpointni probe qiladi: 401 o'rniga
    # 200 {"ok": false} qaytadi (konsol/network shovqini yo'q). Qolgan
    # API'lar 401 bilan himoyalangan.
    if not scope:
        return {"ok": False, "authenticated": False}
    # Kuki silanadi: SPA ochiq turgan har ~30 soniyada shu so'rovni
    # yuboradi — muddat shu yerda to'ldiriladi (12 soatdan keyin
    # chetlab yuborilmaydi). Sessiya yopilgan bo'lsa — kuki yangilanmaydi.
    refresh_session_cookie(request, response)
    from ...core.profiles import all_profiles, company_names, get_profile
    company_name = scope.get("company") or ""
    profile = get_profile(company_name) if company_name else None
    is_admin = bool(scope.get("is_admin"))
    # Kompaniyalar ro'yxati faqat ADMIN uchun: oddiy foydalanuvchi boshqa
    # korxonalar nomlarini ko'rishi shart emas (tenant ma'lumoti).
    companies = (company_names() if is_admin
                 else ([company_name] if company_name else []))
    routes = [{"routeVariantId": str(p.get("routeVariantId") or ""),
               "routeName": str(p.get("routeName") or "")}
              for p in all_profiles()
              if str(p.get("name") or "").strip().lower()
              == company_name.strip().lower()]
    if not routes and company_name:
        # profiles.json'da yozuvi yo'q tenantlar (masalan DEMO korxona):
        # yo'nalishlar `routes` jadvalidan olinadi — company_routes fallback.
        from ...core.tenant import company_routes
        try:
            ids = company_routes(company_name)
            names: dict[str, str] = {}
            if ids:
                from ...db.storage import get_storage
                st = get_storage()
                if st.enabled:
                    ph = st.db.ph
                    marks = ",".join(ph for _ in ids)
                    for r in st.query(
                            "SELECT external_id, name FROM routes WHERE "
                            f"external_id IN ({marks})", tuple(ids),
                            limit=1000):
                        rid = str(r.get("external_id") or "")
                        if rid:
                            names[rid] = str(r.get("name") or "")
            routes = [{"routeVariantId": rid,
                       "routeName": names.get(rid) or rid}
                      for rid in ids]
        except Exception:  # noqa: BLE001 - DB yo'q bo'lsa bo'sh ro'yxat qoladi
            routes = []
    return {
        "ok": True,
        "username": scope.get("username") or "",
        "role": scope.get("role") or "VIEWER",
        "is_admin": is_admin,
        "super": (is_super_username(scope.get("username") or "")
                  if is_admin else False),
        "login_type": "session",
        "company": {
            "name": company_name,
            "routeName": str((profile or {}).get("routeName") or ""),
            "routeVariantId": str((profile or {}).get("routeVariantId") or ""),
            "routes": routes,
            "routeCount": len(routes),
        },
        "companies": companies,
        # Per-user bo'limlar: None = rol bo'yicha cheklovsiz; ro'yxat = faqat
        # shu bo'limlar ishlaydi (frontend shu bo'yicha menyu/sahifalarni
        # yashiradi, server esa sahifaga xos API'larni to'sadi).
        "pages": effective_pages(scope),
        "pages_all": list(PAGE_KEYS),
        # Telegram akkaunt bog'lanishi (admin panelda kiritadi) — bot shu
        # akkauntga shu foydalanuvchining bo'lim ruxsatlarini qo'llaydi.
        "telegram_chat_id": user_tg(scope),
    }


@router.get("/api/dashboard-users")
def list_dashboard_users(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ...core.profiles import grouped_profiles
    st = get_storage()
    groups = grouped_profiles()
    by_name = {str(g.get("name") or "").lower(): g for g in groups}
    super_names = super_admin_names()
    caller_is_admin = bool(scope.get("is_admin"))
    caller_company = str(scope.get("company") or "")

    rows = []
    for u in st.dashboard_users_list():
        c = str(u.get("company") or "")
        if not caller_is_admin and c != caller_company:
            continue
        profile = by_name.get(c.lower()) if c else None
        uname = str(u.get("username") or "").lower()
        rows.append({
            "id": u.get("id"),
            "username": u.get("username"),
            "role": u.get("role"),
            "company": c,
            "company_route": str((profile or {}).get("routeName") or ""),
            "active": int(u.get("active") or 0) == 1,
            "super": uname in super_names,
            # None = rol bo'yicha standart; ro'yxat = aniq tanlangan bo'limlar.
            "pages": _row_pages(u),
            "telegram_chat_id": _row_tg(u),
        })

    def _comp(g: dict) -> dict:
        return {"name": str(g.get("name") or ""),
                "routeVariantId": str(g.get("routeVariantId") or ""),
                "routeName": str(g.get("routeName") or ""),
                "routes": g.get("routes") or []}

    if caller_is_admin:
        comps = [_comp(g) for g in groups]
    else:
        g = by_name.get(caller_company.lower())
        comps = ([_comp(g)] if g
                 else ([{"name": caller_company, "routeVariantId": "",
                         "routeName": "", "routes": []}]
                       if caller_company else []))

    caller = str(scope.get("username") or "").strip().lower()
    return {"ok": True, "users": rows, "companies": comps,
            "is_super": caller in super_names}


@router.post("/api/dashboard-users")
async def manage_dashboard_users(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ...core.profiles import all_profiles

    payload = await request.json()
    st = get_storage()
    action = str(payload.get("action") or "").strip()
    profiles = all_profiles()
    by_name = {str(p.get("name") or "").lower(): p for p in profiles}
    by_rvid = {str(p.get("routeVariantId") or ""): p for p in profiles if p.get("routeVariantId")}

    def _resolve_company(val: str) -> str:
        val = (val or "").strip()
        if not val:
            return ""
        p = by_name.get(val.lower())
        if not p:
            p = by_rvid.get(val)
        return str((p or {}).get("name") or "")

    caller = str(scope.get("username") or "").strip().lower()
    caller_is_admin = bool(scope.get("is_admin"))
    caller_company = str(scope.get("company") or "")
    caller_rank = _ROLE_RANK.get(str(scope.get("role") or "VIEWER"), 0)
    super_names = super_admin_names()
    caller_is_super = caller in super_names

    if action in ("create", "update"):
        username = str(payload.get("username") or "").strip().lower()
        password = str(payload.get("password") or "")
        row_id = payload.get("id")
        try:
            row_id = int(row_id) if row_id not in (None, "", "0") else 0
        except (TypeError, ValueError):
            row_id = 0

        if action == "create":
            role = str(payload.get("role") or "VIEWER").strip().upper()
            if role not in _ROLE_RANK:
                role = "VIEWER"
            company = _resolve_company(str(payload.get("company") or ""))
            if role == "ADMIN" and not caller_is_super:
                return {"ok": False, "error": "ADMIN foydalanuvchi yaratish huquqi faqat Super Adminda"}
            if not caller_is_admin:
                company = caller_company
                if _ROLE_RANK.get(role, 0) >= caller_rank:
                    return {"ok": False, "error": "O'z darajangizdan past rol yarata olasiz"}
            if role != "ADMIN" and not company:
                return {"ok": False, "error": "Noto'g'ri kompaniya tanlandi"}
            if role == "ADMIN":
                company = ""
            # SaaS limiti: korxona reyestridagi max_users (standart 5).
            # Avval faqat kompaniya-admin sahifasi tekshirardi — panel orqali
            # yaratishda cheklov chetlab o'tilardi.
            if company and st.enabled:
                row = st.query(
                    f"SELECT max_users FROM companies WHERE name = {st.db.ph}",
                    (company,), limit=1)
                max_u = int((row[0].get("max_users") if row else 0) or 5)
                cnt = st.query(
                    f"SELECT COUNT(*) AS c FROM dashboard_users "
                    f"WHERE company = {st.db.ph}", (company,), limit=1)
                if int((cnt[0].get("c") if cnt else 0) or 0) >= max_u:
                    return {"ok": False,
                            "error": f"Maksimal foydalanuvchi soni ({max_u}) yetib qo'yilgan"}
            if not username:
                return {"ok": False, "error": "Login kiritish shart"}
            if st.dashboard_user_get(username=username):
                return {"ok": False, "error": "Bu login allaqachon mavjud"}
            perr = password_error(password, username)
            if perr:
                return {"ok": False, "error": perr}
            pages, perr = _clean_pages(payload.get("pages"))
            if perr:
                return {"ok": False, "error": perr}
            if role == "ADMIN":
                pages = None
            tg_id, tg_err = _clean_tg(payload.get("telegram_chat_id"))
            if tg_err:
                return {"ok": False, "error": tg_err}
            digest, salt = hash_password(password)
            uid = st.dashboard_user_add(
                username=username, salt=salt, password_hash=digest,
                role=role, company=company, active=1,
                data=_user_data("{}", pages=pages, tg_id=tg_id))
            if uid:
                invalidate_user_access(username)
                invalidate_super_cache()
                audit_event("dashboard_user.create", request,
                            user=caller, target=username, detail=role)
            return {"ok": bool(uid), "id": uid}

        cur = st.dashboard_user_get(row_id=row_id) if row_id else None
        if not cur and username:
            cur = st.dashboard_user_get(username=username)
        if not cur:
            return {"ok": False, "error": "Foydalanuvchi topilmadi"}
        row_id = int(cur.get("id") or row_id or 0)
        cur_name = str(cur.get("username") or "").lower()
        cur_role = str(cur.get("role") or "VIEWER").strip().upper()
        cur_company = str(cur.get("company") or "")
        target_is_super = cur_name in super_names

        if target_is_super and not caller_is_super:
            return {"ok": False, "error": "Super adminni boshqa foydalanuvchi tahrirlay olmaydi"}
        if username and cur_name != username:
            existing = st.dashboard_user_get(username=username)
            if existing and int(existing.get("id")) != row_id:
                return {"ok": False, "error": "Bu login allaqachon mavjud"}
        if target_is_super:
            if str(payload.get("role") or "").strip().upper() not in ("", "ADMIN"):
                return {"ok": False, "error": "Super admin roli daxlsiz"}
            if payload.get("active") not in (None, ""):
                return {"ok": False, "error": "Super admin faolligini o'chirib bo'lmaydi"}
            closed = 0
            if password:
                perr = password_error(password, cur_name)
                if perr:
                    return {"ok": False, "error": perr}
                digest, salt = hash_password(password)
                st.dashboard_user_update(row_id=row_id, salt=salt, password_hash=digest)
                # Parol o'zgardi → shu akkauntning boshqa qurilmalardagi
                # sessiyalari darhol yopiladi (eski parol bilan kirib
                # bo'lmaydi). O'z-o'zini yangilasayotgan bo'lsa — joriy
                # qurilma sessiyasi saqlanadi.
                closed = drop_user_sessions(
                    cur_name,
                    keep_token=(_session_token_from_request(request, None)
                                if cur_name == caller else ""))
                sync_env_admin_password(cur_name, password)
                audit_event("dashboard_user.password", request,
                            user=caller, target=cur_name)
            return {"ok": True, "closed_sessions": closed}

        role = str(payload.get("role") or cur_role).strip().upper()
        if role not in _ROLE_RANK:
            role = "VIEWER"
        if role == "ADMIN" and not caller_is_super:
            return {"ok": False, "error": "ADMIN rolga o'tkazish huquqi faqat Super Adminda"}
        if not caller_is_admin:
            if cur_company != caller_company:
                return {"ok": False, "error": "Boshqa korxona foydalanuvchisini tahrirlay olmaysiz"}
            # Joriy (tahrirlanayotgan) daraja ham tekshiriladi: aks holda
            # MANAGER o'zidan yuqori DIRECTOR'ning parolini almashtirib,
            # uning nomidan tizimga kirishi mumkin edi. O'zini tahrirlash
            # (shaxsiy parol) bunga kirmaydi.
            if cur_name != caller and _ROLE_RANK.get(cur_role, 0) >= caller_rank:
                return {"ok": False,
                        "error": "Bu foydalanuvchini tahrirlash huquqi yo'q"}
            if role != cur_role and _ROLE_RANK.get(role, 0) >= caller_rank:
                return {"ok": False, "error": "O'z darajangizdan past rol belgilang"}
            target_company = caller_company
        else:
            target_company = _resolve_company(str(payload.get("company") or "")) if payload.get("company") not in (None, "") else cur_company
        if role != "ADMIN" and not target_company:
            return {"ok": False, "error": "Noto'g'ri kompaniya tanlandi"}
        if role == "ADMIN":
            target_company = ""
        # Kompaniyani almashtirish ham limitga bo'ysunadi (o'rin ko'chirish).
        if (st.enabled and role != "ADMIN" and target_company
                and target_company != cur_company):
            row = st.query(
                f"SELECT max_users FROM companies WHERE name = {st.db.ph}",
                (target_company,), limit=1)
            max_u = int((row[0].get("max_users") if row else 0) or 5)
            cnt = st.query(
                f"SELECT COUNT(*) AS c FROM dashboard_users "
                f"WHERE company = {st.db.ph}", (target_company,), limit=1)
            if int((cnt[0].get("c") if cnt else 0) or 0) >= max_u:
                return {"ok": False,
                        "error": f"Maksimal foydalanuvchi soni ({max_u}) yetib qo'yilgan"}
        active = int(payload.get("active") or 0) if payload.get("active") not in (None, "") else int(cur.get("active") or 0)
        # Bo'limlar (per-user ruxsat): kalit yuborilmagan bo'lsa tegmaymiz,
        # `pages: null` yuborilsa — cheklov olib tashlanadi (rol bo'yicha).
        data_arg = None
        if "pages" in payload or "telegram_chat_id" in payload:
            pages: list | None | object = _UNSET
            if "pages" in payload:
                pages, perr = _clean_pages(payload.get("pages"))
                if perr:
                    return {"ok": False, "error": perr}
                if role == "ADMIN":
                    pages = None
            tg_id: str | object = _UNSET
            if "telegram_chat_id" in payload:
                tg_id, tg_err = _clean_tg(payload.get("telegram_chat_id"))
                if tg_err:
                    return {"ok": False, "error": tg_err}
            data_arg = _user_data(str(cur.get("data") or ""),
                                  pages=pages, tg_id=tg_id)
        if password:
            perr = password_error(password, cur_name)
            if perr:
                return {"ok": False, "error": perr}
        new_name = str(username or "").strip().lower() or cur_name
        sensitive_change = (
            role != cur_role or target_company != cur_company
            or active == 0 or new_name != cur_name)
        ok = st.dashboard_user_update(row_id=row_id, username=username or None,
                                      role=role, company=target_company,
                                      active=active, data=data_arg)
        closed = 0
        if ok and password:
            digest, salt = hash_password(password)
            if not st.dashboard_user_update(row_id=row_id, salt=salt,
                                            password_hash=digest):
                return {"ok": False, "error": "Parolni saqlab bo'lmadi"}
        if ok and (password or sensitive_change):
            # Rol/kompaniya/faollik o'zgarsa yoki parol yangilansa eski
            # sessiyalar yopiladi. O'z parolini o'zgartirgan foydalanuvchining
            # joriy qurilmasi qoladi; rol/kompaniya o'zgarsa esa qayta kirish
            # talab qilinadi (eski ruxsatlar bilan ishlash mumkin emas).
            keep = ""
            if password and not sensitive_change and cur_name == caller:
                keep = _session_token_from_request(request, None)
            closed = drop_user_sessions(cur_name, keep_token=keep)
        if ok:
            invalidate_user_access(cur_name)
            invalidate_user_access(new_name)
            invalidate_super_cache()
            audit_event("dashboard_user.update", request, user=caller,
                        target=cur_name,
                        detail=f"role={role} active={active} "
                               f"sessions_closed={closed}")
        if password and ok:
            sync_env_admin_password(cur_name, password)
        return {"ok": ok, "closed_sessions": closed}

    if action == "delete":
        if not caller_is_super:
            return {"ok": False, "error": "O'chirish huquqi faqat Super Adminda"}
        try:
            row_id = int(payload.get("id") or 0)
        except (TypeError, ValueError):
            row_id = 0
        if not row_id:
            uname = str(payload.get("username") or "").strip().lower()
            if not uname:
                return {"ok": False, "error": "id yoki username kerak"}
            cur = st.dashboard_user_get(username=uname)
            if not cur:
                return {"ok": True}
            row_id = int(cur.get("id") or 0)
        row = st.dashboard_user_get(row_id=row_id)
        if not row:
            return {"ok": True}
        uname = str(row.get("username") or "").lower()
        if uname in super_names:
            return {"ok": False, "error": "Super adminni o'chirib bo'lmaydi"}
        if uname == caller:
            return {"ok": False, "error": "O'zingizni o'chira olmaysiz"}
        ok = st.delete_dashboard_user(row_id)
        if ok:
            drop_user_sessions(uname)
            invalidate_user_access(uname)
            invalidate_super_cache()
            audit_event("dashboard_user.delete", request, user=caller,
                        target=uname)
        return {"ok": ok}

    return {"ok": False, "error": "Noma'lum action"}


class ChangePasswordRequest(BaseModel):
    current_password: str = ""
    new_password: str = ""


@router.post("/api/change-password")
def change_password(body: ChangePasswordRequest, request: Request,
                          response: Response,
                          scope: dict = Depends(Scope(min_role=0))):
    """Joriy foydalanuvchi o'z parolini o'zgartiradi (Settings oynasi).

    Xavfsizlik: joriy parol talab qilinadi va parol o'zgargan zahoti
    akkauntning **boshqa** sessiyalari yopiladi — eski parol bilan
    kirish va eski sessiyalar bilan ishlash imkoniyati yo'qoladi.
    Joriy qurilma sessiyasi (cookie) saqlanib, muddati qayta yangilanadi.
    """
    from ...db.storage import get_storage
    username = str(scope.get("username") or "").strip().lower()
    if not username:
        raise HTTPException(401, "Kirish talab qilinadi")
    st = get_storage()
    user = st.dashboard_user_get(username=username)
    if not user:
        raise HTTPException(404, "Akkaunt topilmadi")
    if not verify_password(body.current_password or "",
                           str(user.get("salt") or ""),
                           str(user.get("password_hash") or "")):
        raise HTTPException(400, "Joriy parol noto'g'ri")
    new_password = (body.new_password or "").strip()
    perr = password_error(new_password, username)
    if perr:
        raise HTTPException(400, perr)
    if new_password == str(body.current_password or "").strip():
        raise HTTPException(400, "Yangi parol joriy paroldan farq qilishi kerak")
    digest, salt = hash_password(new_password)
    if not st.dashboard_user_update(int(user["id"]), salt=salt,
                                    password_hash=digest):
        raise HTTPException(500, "Parolni saqlab bo'lmadi")
    keep = _session_token_from_request(request, None)
    closed = drop_user_sessions(username, keep_token=keep)
    sync_env_admin_password(username, new_password)
    refresh_session_cookie(request, response)
    audit_event("password.change", request, user=username,
                target=username, detail=f"sessions_closed={closed}")
    return {"ok": True, "closed_sessions": closed}
