"""Xavfsizlik hardening regressiya testlari (tenant A/B, CSRF, fail-closed).

Qamrov:
- Tenant A/B: foydalanuvchi faqat o'z korxonasi yo'nalishlarini ko'radi;
  so'rovdagi `route`/`company` boshqa tenantga burib yuborilmaydi;
  yo'nalishi yo'q korxona `/api/salary` orqali butun bazani eksport qila
  olmaydi (fail-closed 403).
- Kompaniya sozlamalari: non-super ADMIN boshqa korxona kredensialini
  o'zgartira/o'chira/o'rinlashtira olmaydi; o'z korxonasi bilan ishlay oladi.
- `.env`: BM_BASE_URL/BM_USERNAME kabi kritik kalitlar faqat super ADMIN.
- CSRF: cross-site POST (Origin / Sec-Fetch-Site) 403, same-origin o'tadi.
- `/api/backup/run`: faqat POST va faqat ADMIN (rank 4).
- GPS API: token bo'sh bo'lsa faqat loopback; token hmac bilan solishtiriladi.

Barcha ma'lumot sintez (tmp SQLite + tmp profiles.json) — real mijoz
ma'lumoti ishlatilmaydi.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.dashboard import deps
from bm_automation.app.db.storage import storage_for
from bm_automation.app.db.tenancy import seed_companies
from tests.sqlite_backend import SQLiteDatabase


def _client() -> TestClient:
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


@pytest.fixture()
def senv(monkeypatch, tmp_path):
    """Haqiqiy auth muhiti: BM_TEST_MODE o'chirilgan, barcha narsa tmp'da."""
    monkeypatch.delenv("BM_TEST_MODE", raising=False)
    monkeypatch.setattr(deps, "_dashboard_token", lambda: "")
    monkeypatch.setattr(deps, "_sessions_file",
                        lambda: tmp_path / "sessions.json")
    monkeypatch.setattr(deps, "_POST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(deps, "_LOGIN_MIN_INTERVAL_S", 0.0)
    with deps._SESSION_LOCK:
        deps._sessions.clear()
    with deps._POST_LOCK:
        deps._last_post_at.clear()
    with deps._LOGIN_LOCK:
        deps._login_fails.clear()
        deps._login_blocked_until.clear()
    deps.invalidate_super_cache()
    deps.invalidate_user_access()
    # Super admin ro'yxati testda aniq bo'lsin (real `.env`ga bog'liq emas).
    monkeypatch.setattr(deps, "super_admin_names", lambda: {"root"})

    db = SQLiteDatabase(str(tmp_path / "sec.db"))
    st = storage_for(db)
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage", lambda: st)
    monkeypatch.setattr("bm_automation.app.dashboard.metrics.get_storage",
                        lambda: st)

    monkeypatch.setattr(profiles_mod, "PROFILES_FILE",
                        tmp_path / "profiles.json")
    profiles_mod.save_profiles({
        "active": "ATEST",
        "profiles": [
            {"name": "ATEST", "routeVariantId": "ra1",
             "routeName": "A1 yo'nalish"},
            {"name": "BTEST", "routeVariantId": "rb1",
             "routeName": "B1 yo'nalish"},
            # Yo'nalishi yo'q (lekin faol) korxona — fail-closed sinovi uchun.
            {"name": "CTEST", "routeVariantId": "", "routeName": ""},
        ],
    })
    ids = seed_companies(st, [
        {"name": "ATEST", "routeVariantId": "ra1"},
        {"name": "BTEST", "routeVariantId": "rb1"},
        {"name": "CTEST", "routeVariantId": ""},
    ])
    st.save_route(external_id="ra1", name="A1 yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="rb1", name="B1 yo'nalish", entity_type="ROUTE")

    # `.env` — hech qachon haqiqiy faylga yozilmasin.
    from bm_automation.app.dashboard.routers import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_ENV_PATH", tmp_path / "env-test")
    return {"st": st, "ids": ids, "tmp": tmp_path}


def _login(role: str, company: str, username: str = "u1") -> str:
    return deps.create_session({"username": username, "id": 101,
                                "role": role, "company": company})


# ----------------------------------------------------------- tenant A/B

def test_tenant_a_sees_only_own_routes(senv):
    tok = _login("MANAGER", "ATEST")
    r = _client().get("/api/routes", cookies={"bm_session": tok})
    assert r.status_code == 200
    ids = sorted(str(x.get("id")) for x in r.json().get("routes") or [])
    assert ids == ["ra1"]


def test_tenant_b_sees_only_own_routes(senv):
    tok = _login("MANAGER", "BTEST")
    r = _client().get("/api/routes", cookies={"bm_session": tok})
    assert r.status_code == 200
    ids = sorted(str(x.get("id")) for x in r.json().get("routes") or [])
    assert ids == ["rb1"]


def test_force_route_overrides_cross_tenant_param(senv):
    """Tenant A so'rovda `route=rb1` yuborsa ham o'z qamrovi qo'llanadi."""
    scope = {"is_admin": False, "company": "ATEST", "routes": ["ra1"]}
    out = deps.force_route(scope, "/api/month", {"route": "rb1"})
    assert out["route"] == "ra1"
    # Yo'nalishi yo'q tenantda parametr sentinel bilan yopiladi.
    empty = deps.force_route({"is_admin": False, "company": "CTEST",
                              "routes": []}, "/api/month", {"route": "rb1"})
    assert empty["route"] == "\x00_qamrovsiz\x00"


def test_salary_fail_closed_without_routes(senv):
    """Yo'nalishsiz korxona `/api/salary`dan butun bazani ololmaydi."""
    tok = _login("MANAGER", "CTEST")
    r = _client().get("/api/salary?from=2026-09-01&to=2026-09-30",
                      cookies={"bm_session": tok})
    assert r.status_code == 403


def test_salary_allowed_for_positive_scope(senv):
    """Nazorat: o'z yo'nalishi bor korxona hisobotni oladi (403 emas)."""
    tok = _login("MANAGER", "ATEST")
    r = _client().get("/api/salary?from=2026-09-01&to=2026-09-30",
                      cookies={"bm_session": tok})
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats") or r.content[:2] == b"PK"


# ------------------------------------------- company settings (tenant scope)

def _post(cookies: dict, payload: dict, path: str = "/api/settings/companies"):
    return _client().post(path, cookies=cookies, json=payload)


def test_non_super_admin_cannot_change_other_company_creds(senv):
    tok = _login("ADMIN", "ATEST", username="a_admin")
    r = _post({"bm_session": tok}, {"action": "creds", "name": "BTEST",
                                    "username": "evil", "password": "evilpass1"})
    assert r.status_code == 403
    p = profiles_mod.get_profile("BTEST")
    assert p.get("username") != "evil"


def test_non_super_admin_cannot_delete_or_add_company(senv):
    tok = _login("ADMIN", "ATEST", username="a_admin")
    assert _post({"bm_session": tok},
                 {"action": "delete", "name": "BTEST"}).status_code == 403
    assert profiles_mod.get_profile("BTEST") is not None
    assert _post({"bm_session": tok},
                 {"action": "add", "name": "NEWCO",
                  "routeVariantId": "rn1"}).status_code == 403
    assert profiles_mod.get_profile("NEWCO") is None


def test_non_super_admin_can_change_own_company_creds(senv):
    tok = _login("ADMIN", "ATEST", username="a_admin")
    r = _post({"bm_session": tok}, {"action": "creds", "name": "ATEST",
                                    "username": "newuser",
                                    "password": "newpass123"})
    assert r.status_code == 200 and r.json().get("ok") is True
    p = profiles_mod.get_profile("ATEST")
    assert p.get("username") == "newuser"


def test_super_admin_can_touch_any_company(senv):
    tok = _login("ADMIN", "", username="root")
    r = _post({"bm_session": tok}, {"action": "creds", "name": "BTEST",
                                    "username": "buser",
                                    "password": "bpass1234"})
    assert r.status_code == 200 and r.json().get("ok") is True
    assert profiles_mod.get_profile("BTEST").get("username") == "buser"


def test_env_bm_base_url_super_only(senv):
    from bm_automation.app.dashboard.routers import admin as admin_mod

    class _Req:
        def __init__(self, payload):
            self._p = payload

        async def json(self):
            return self._p

    non_super = {"is_admin": True, "username": "a_admin", "company": "ATEST"}
    r = asyncio.run(admin_mod.update_env_settings(
        _Req({"values": {"BM_BASE_URL": "https://evil.example"}}), non_super))
    assert r.get("ok") is False
    assert "BM_BASE_URL" in r.get("error", "")
    assert not (senv["tmp"] / "env-test").exists()

    r = asyncio.run(admin_mod.update_env_settings(
        _Req({"values": {"OPENROUTER_MODEL": "test-model"}}), non_super))
    assert r.get("ok") is True
    text = (senv["tmp"] / "env-test").read_text(encoding="utf-8")
    assert "OPENROUTER_MODEL=test-model" in text

    super_scope = {"is_admin": True, "username": "root", "company": ""}
    r = asyncio.run(admin_mod.update_env_settings(
        _Req({"values": {"BM_BASE_URL": "https://bm.example"}}), super_scope))
    assert r.get("ok") is True
    assert "BM_BASE_URL=https://bm.example" in \
        (senv["tmp"] / "env-test").read_text(encoding="utf-8")


# ------------------------------------------------------------------- CSRF

def test_csrf_blocks_cross_site_origin(senv):
    r = _client().post("/api/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json().get("ok") is False


def test_csrf_blocks_sec_fetch_cross_site(senv):
    r = _client().post("/api/logout", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_csrf_allows_same_origin(senv):
    r = _client().post("/api/logout", headers={"Origin": "http://testserver"})
    assert r.status_code != 403


def test_csrf_does_not_block_get(senv):
    r = _client().get("/api/summary", headers={"Origin": "https://evil.example"})
    assert r.status_code == 401     # auth talab qilinadi, CSRF emas


# ------------------------------------------------------------ backup endpoint

def test_backup_run_is_post_only(senv):
    r = _client().get("/api/backup/run")
    assert r.status_code in (404, 405)


def test_backup_run_requires_admin(senv):
    tok = _login("MANAGER", "ATEST")
    r = _client().post("/api/backup/run", cookies={"bm_session": tok})
    assert r.status_code == 403


def test_backup_run_requires_auth(senv):
    assert _client().post("/api/backup/run").status_code == 401


# -------------------------------------------------------------------- GPS API

class _Headers(dict):
    def get(self, key, default=None):  # noqa: D102 - dict bilan bir xil
        return super().get(key, default)


class _Handler:
    def __init__(self, addr=("127.0.0.1", 5000), auth="", cookie=""):
        self.client_address = addr
        self.headers = _Headers({"Authorization": auth, "Cookie": cookie})


def test_gps_empty_token_loopback_only(monkeypatch):
    from bm_automation.app.gps import api as gps_api
    from bm_automation.app.gps import config as gps_config
    monkeypatch.setattr(gps_config, "GPS_API_TOKEN", "")
    assert gps_api._check_token(_Handler()) is True
    assert gps_api._check_token(_Handler(addr=("203.0.113.7", 5555))) is False


def test_gps_token_bearer_cookie_query(monkeypatch):
    from bm_automation.app.gps import api as gps_api
    from bm_automation.app.gps import config as gps_config
    monkeypatch.setattr(gps_config, "GPS_API_TOKEN", "s3cret")
    remote = ("203.0.113.7", 5555)
    assert gps_api._check_token(_Handler(addr=remote, auth="Bearer s3cret")) is True
    assert gps_api._check_token(_Handler(addr=remote, auth="Bearer wrong")) is False
    assert gps_api._check_token(
        _Handler(addr=remote, cookie="a=1; bm_token=s3cret")) is True
    assert gps_api._check_token(_Handler(addr=remote), {"token": ["s3cret"]}) is True
    assert gps_api._check_token(_Handler(addr=remote), {"token": ["no"]}) is False
    assert gps_api._check_token(_Handler(addr=remote)) is False


# ----------------------------------------------------- sessiya xavfsizligi

def test_session_token_rotation_on_password_change(senv):
    """Parol o'zgarsa boshqa qurilma sessiyalari yopiladi (regressiya)."""
    st = senv["st"]
    st.dashboard_user_add("u1", *deps.hash_password("OldPass123"),
                          role="MANAGER", company="ATEST")
    deps.invalidate_user_access()
    other = deps.create_session({"username": "u1", "id": 101,
                                 "role": "MANAGER", "company": "ATEST"})
    assert deps.session_user(other)
    closed = deps.drop_user_sessions("u1", keep_token="joriy")
    assert closed >= 1
    assert deps.session_user(other) is None


# ------------------------------------------------ SEC-06: GPS CORS whitelist

def _gps_handler(headers: dict):
    from bm_automation.app.gps.api import GpsHandler
    h = object.__new__(GpsHandler)
    h.headers = _Headers(headers)
    return h


def test_gps_cors_rejects_foreign_origin(monkeypatch):
    monkeypatch.delenv("GPS_CORS_ORIGINS", raising=False)
    h = _gps_handler({"Origin": "https://evil.example",
                      "Host": "bmbrutto.uz"})
    assert h._cors_origin() == ""


def test_gps_cors_allows_same_host_and_configured(monkeypatch):
    monkeypatch.setenv("GPS_CORS_ORIGINS", "https://panel.example.uz")
    h = _gps_handler({"Origin": "https://bmbrutto.uz", "Host": "bmbrutto.uz"})
    assert h._cors_origin() == "https://bmbrutto.uz"
    h2 = _gps_handler({"Origin": "https://panel.example.uz", "Host": "x"})
    assert h2._cors_origin() == "https://panel.example.uz"


def test_gps_cors_allows_localhost_dev():
    h = _gps_handler({"Origin": "http://localhost:8093", "Host": "bmbrutto.uz"})
    assert h._cors_origin() == "http://localhost:8093"


# ------------------------------------------------ SEC-08/SEC-12: parol hash

def test_password_hash_versioned_600k():
    digest, salt = deps.hash_password("Sirliparol1")
    assert digest.startswith("pbkdf2_sha256$600000$")
    assert deps.verify_password("Sirliparol1", salt, digest)
    assert not deps.verify_password("boshqa", salt, digest)
    assert deps.needs_rehash(digest) is False


def test_legacy_120k_hash_still_verifies_and_needs_rehash():
    import hashlib
    salt = "abcdef0123456789"
    legacy = hashlib.pbkdf2_hmac(
        "sha256", b"Sirliparol1", salt.encode(), 120_000).hex()
    assert deps._split_hash(legacy) == (120_000, legacy)
    assert deps.verify_password("Sirliparol1", salt, legacy)
    assert deps.needs_rehash(legacy) is True


def test_login_rehashes_legacy_password(senv):
    import hashlib
    st = senv["st"]
    salt = "legacysalt0000001"
    legacy = hashlib.pbkdf2_hmac(
        "sha256", b"EskiParol1", salt.encode(), 120_000).hex()
    uid = st.dashboard_user_add("ali", legacy, salt, role="MANAGER",
                                company="ATEST")
    assert uid
    r = _client().post("/api/login",
                       json={"username": "ali", "password": "EskiParol1"})
    assert r.status_code == 200, r.text
    row = st.dashboard_user_get(username="ali")
    assert str(row.get("password_hash") or "").startswith("pbkdf2_sha256$600000$")
    assert deps.verify_password("EskiParol1", row.get("salt") or "",
                                row.get("password_hash") or "")


def test_dashboard_token_compare_is_constant_time(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "tok-123")
    assert deps._same_token("tok-123", deps._dashboard_token()) is True
    assert deps._same_token("tok-124", deps._dashboard_token()) is False
    assert deps._same_token("", deps._dashboard_token()) is False


# ------------------------------------- SEC-11/SEC-14: health va kompaniyalar

def test_health_requires_manager(senv):
    viewer = _login("VIEWER", "ATEST")
    mgr = _login("MANAGER", "ATEST", username="m1")
    c = _client()
    assert c.get("/api/health", cookies={"bm_session": viewer}).status_code == 403
    assert c.get("/api/health", cookies={"bm_session": mgr}).status_code == 200


def test_companies_list_scoped_for_non_super_admin(senv):
    st = senv["st"]
    st.dashboard_user_add("root", *deps.hash_password("RootParol1"),
                          role="ADMIN", company="", data='{"super": true}')
    st.dashboard_user_add("adm", *deps.hash_password("AdmParol1"),
                          role="ADMIN", company="ATEST")
    deps.invalidate_user_access()
    deps.invalidate_super_cache()
    c = _client()
    r = c.post("/api/login", json={"username": "adm", "password": "AdmParol1"})
    assert r.status_code == 200, r.text
    names = {x["name"] for x in
             c.get("/api/settings/companies", cookies=r.cookies).json()["companies"]}
    assert names == {"ATEST"}
    r2 = c.post("/api/login", json={"username": "root", "password": "RootParol1"})
    assert r2.status_code == 200, r2.text
    names2 = {x["name"] for x in
              c.get("/api/settings/companies", cookies=r2.cookies).json()["companies"]}
    assert {"ATEST", "BTEST", "CTEST"} <= names2


# ------------------------------------------------ SEC-14: Host-header guard

def test_host_guard_blocks_unknown_host(senv):
    c = _client()
    tok = _login("ADMIN", "")
    assert c.get("/api/me", cookies={"bm_session": tok}).status_code == 200
    bad = c.get("/api/me", cookies={"bm_session": tok},
                headers={"Host": "evil.example"})
    assert bad.status_code == 400
