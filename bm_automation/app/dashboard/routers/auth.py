"""Auth router — login, logout, me, dashboard-users."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..deps import (
    Scope, client_ip, create_session, drop_session, hash_password,
    is_super_username, login_allowed, login_fail, login_success,
    resolve_scope, session_user, super_admin_names, verify_password,
    _ROLE_RANK, _session_token_from_request, _dashboard_token,
    request_is_https,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/api/login")
async def login(body: LoginRequest, request: Request, response: Response):
    username = body.username.strip().lower()
    password = body.password
    if not username or not password:
        raise HTTPException(400, "Login va parol kiriting")

    ip = client_ip(request)
    if not login_allowed(ip, username):
        raise HTTPException(429, "Juda ko'p xato urinish — 15 daqiqa kuting")

    from ...db.storage import get_storage
    user = get_storage().dashboard_user_get(username=username)
    if not user or int(user.get("active") or 0) == 0:
        login_fail(ip, username)
        raise HTTPException(401, "Login yoki parol noto'g'ri")

    if not verify_password(password, str(user.get("salt") or ""), str(user.get("password_hash") or "")):
        login_fail(ip, username)
        raise HTTPException(401, "Login yoki parol noto'g'ri")

    login_success(ip, username)
    role = str(user.get("role") or "VIEWER").strip().upper()
    company = str(user.get("company") or "").strip()
    token = create_session({"username": username, "role": role, "company": company})

    resp = JSONResponse = None
    from fastapi.responses import JSONResponse as _JR
    result = _JR({"ok": True, "username": username, "role": role, "company": company})
    result.set_cookie("bm_session", token, path="/", httponly=True,
                      samesite="lax", secure=request_is_https(request),
                      max_age=12 * 3600)
    return result


@router.post("/api/logout")
async def logout(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    token = _session_token_from_request(request, creds)
    drop_session(token)
    from fastapi.responses import JSONResponse as _JR
    result = _JR({"ok": True})
    result.delete_cookie("bm_session", path="/")
    return result


@router.get("/api/me")
async def me(request: Request, scope: dict = Depends(Scope(min_role=0))):
    from ...core.profiles import get_profile, all_profiles
    company_name = scope.get("company") or ""
    profile = get_profile(company_name) if company_name else None
    return {
        "ok": True,
        "username": scope.get("username") or "",
        "role": scope.get("role") or "VIEWER",
        "is_admin": bool(scope.get("is_admin")),
        "super": is_super_username(scope.get("username") or ""),
        "login_type": "session",
        "company": {
            "name": company_name,
            "routeName": str((profile or {}).get("routeName") or ""),
            "routeVariantId": str((profile or {}).get("routeVariantId") or ""),
        },
        "companies": [str(p.get("name") or "") for p in all_profiles()],
    }


@router.get("/api/dashboard-users")
async def list_dashboard_users(request: Request, scope: dict = Depends(Scope())):
    from ...db.storage import get_storage
    from ...core.profiles import all_profiles
    st = get_storage()
    profiles = all_profiles()
    by_name = {str(p.get("name") or "").lower(): p for p in profiles}
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
        })

    if caller_is_admin:
        comps = [
            {"name": str(p.get("name") or ""),
             "routeVariantId": str(p.get("routeVariantId") or ""),
             "routeName": str(p.get("routeName") or "")}
            for p in profiles]
    else:
        p = by_name.get(caller_company.lower()) or {}
        comps = ([{"name": caller_company,
                   "routeVariantId": str(p.get("routeVariantId") or ""),
                   "routeName": str(p.get("routeName") or "")}]
                 if caller_company else [])

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
            if not username:
                return {"ok": False, "error": "Login kiritish shart"}
            if st.dashboard_user_get(username=username):
                return {"ok": False, "error": "Bu login allaqachon mavjud"}
            if len(password) < 4:
                return {"ok": False, "error": "Parol kamida 4 belgi"}
            digest, salt = hash_password(password)
            uid = st.dashboard_user_add(
                username=username, salt=salt, password_hash=digest,
                role=role, company=company, active=1)
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
            if password:
                digest, salt = hash_password(password)
                st.dashboard_user_update(row_id=row_id, salt=salt, password_hash=digest)
            return {"ok": True}

        role = str(payload.get("role") or cur_role).strip().upper()
        if role not in _ROLE_RANK:
            role = "VIEWER"
        if role == "ADMIN" and not caller_is_super:
            return {"ok": False, "error": "ADMIN rolga o'tkazish huquqi faqat Super Adminda"}
        if not caller_is_admin:
            if cur_company != caller_company:
                return {"ok": False, "error": "Boshqa korxona foydalanuvchisini tahrirlay olmaysiz"}
            if role != cur_role and _ROLE_RANK.get(role, 0) >= caller_rank:
                return {"ok": False, "error": "O'z darajangizdan past rol belgilang"}
            target_company = caller_company
        else:
            target_company = _resolve_company(str(payload.get("company") or "")) if payload.get("company") not in (None, "") else cur_company
        if role != "ADMIN" and not target_company:
            return {"ok": False, "error": "Noto'g'ri kompaniya tanlandi"}
        if role == "ADMIN":
            target_company = ""
        active = int(payload.get("active") or 0) if payload.get("active") not in (None, "") else int(cur.get("active") or 0)
        ok = st.dashboard_user_update(row_id=row_id, username=username or None,
                                      role=role, company=target_company, active=active)
        if password:
            digest, salt = hash_password(password)
            st.dashboard_user_update(row_id=row_id, salt=salt, password_hash=digest)
        return {"ok": ok}

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
        return {"ok": st.delete_dashboard_user(row_id)}

    return {"ok": False, "error": "Noma'lum action"}
