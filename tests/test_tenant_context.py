"""Tenant kontekst testlari (Phase 1) — SQLite/tmp + TestClient.

Qamrov:
- ``core.tenant`` — company_routes (profiles.json + DB fallback),
  company_active (status/obuna/fail-open), company_id_* topish.
- ``Storage._tenant_fill`` — insert/upsert/bulk_upsert avtomatik
  company_id; mavjud qiymat 0 bilan bosilmasligi.
- ``deps`` — sessiyada company_id/multi-route, force_route (vergul),
  tenant_guard (403/fail-open/skip holatlari).
- Integratsiya — /api/routes multi-route, bloklangan kompaniya 403.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.core import tenant as tenant_mod
from bm_automation.app.core.tenant import company_active
from bm_automation.app.dashboard import deps
from bm_automation.app.db.storage import storage_for
from bm_automation.app.db.tenancy import seed_companies
from tests.sqlite_backend import SQLiteDatabase

COMPANIES = [
    {"name": "OKCO TRANSPORT", "routeVariantId": "ro1"},
    {"name": "OKCO TRANSPORT", "routeVariantId": "ro2"},
    {"name": "BLOCKCO MCHJ", "routeVariantId": "rb1"},
]


def _client() -> TestClient:
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


def _mk_request(path: str, cookies: dict | None = None,
                headers: dict | None = None) -> Request:
    raw = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    hdrs = [(b"cookie", raw.encode())] if raw else []
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), str(v).encode()))
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "",
        "headers": hdrs,
        "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
    }
    return Request(scope)


@pytest.fixture()
def tenv(monkeypatch, tmp_path):
    """Haqiqiy auth muhiti: BM_TEST_MODE o'chirilgan, tenant izolatsiya."""
    monkeypatch.delenv("BM_TEST_MODE", raising=False)
    monkeypatch.setattr(deps, "_dashboard_token", lambda: "")
    monkeypatch.setattr(deps, "_sessions_file",
                        lambda: tmp_path / "sessions.json")
    with deps._SESSION_LOCK:
        deps._sessions = {}
    with deps._POST_LOCK:
        deps._last_post_at.clear()
    with deps._LOGIN_LOCK:
        deps._login_fails.clear()
        deps._login_blocked_until.clear()
    deps.invalidate_super_cache()
    deps.invalidate_user_access()

    db = SQLiteDatabase(str(tmp_path / "tenant.db"))
    st = storage_for(db)
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage", lambda: st)

    monkeypatch.setattr(profiles_mod, "PROFILES_FILE",
                        tmp_path / "profiles.json")
    profiles_mod.save_profiles({
        "active": "OKCO TRANSPORT",
        "profiles": [
            {"name": "OKCO TRANSPORT", "routeVariantId": "ro1",
             "routeName": "1-yo'nalish"},
            {"name": "OKCO TRANSPORT", "routeVariantId": "ro2",
             "routeName": "2-yo'nalish"},
            {"name": "BLOCKCO MCHJ", "routeVariantId": "rb1",
             "routeName": "Blok yo'nalish"},
        ],
    })

    ids = seed_companies(st, COMPANIES)
    st._exec(f"UPDATE companies SET status={st.db.ph} WHERE slug={st.db.ph}",
             ("blocked", "blockco-mchj"))
    tenant_mod.reset()
    return {"st": st, "ids": ids}


# ------------------------------------------------------------ company_routes

def test_company_routes_multi_from_profiles_json(tenv):
    assert tenant_mod.company_routes("OKCO TRANSPORT") == ["ro1", "ro2"]
    assert tenant_mod.company_routes("okco transport") == ["ro1", "ro2"]
    assert tenant_mod.company_routes("BLOCKCO MCHJ") == ["rb1"]
    assert tenant_mod.company_routes("Noma'lum") == []


def test_company_routes_db_fallback(tenv, monkeypatch, tmp_path):
    """profiles.json bo'sh bo'lsa DB `profiles` jadvalidan olinadi."""
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE",
                        tmp_path / "empty.json")
    st, ids = tenv["st"], tenv["ids"]
    st.insert("profiles", {"name": "OKCO TRANSPORT", "route_id": "ro1"})
    tenant_mod.reset()
    routes = tenant_mod.company_routes("OKCO TRANSPORT", storage=st)
    assert routes == ["ro1"]
    assert st.find("profiles", name="OKCO TRANSPORT")["company_id"] == \
        ids["okco-transport"]


# ------------------------------------------------------------ company_active

def test_company_active_semantics():
    assert company_active(None) is True              # noma'lum — fail-open
    assert company_active({"status": "active"}) is True
    assert company_active({"status": "ACTIVE"}) is True
    assert company_active({"status": "blocked"}) is False
    assert company_active({"status": "suspended"}) is False
    # Soft-delete/arxiv: korxona o'chirilgan bo'lsa ham bloklangan hisoblanadi.
    assert company_active({"status": "deleted"}) is False
    assert company_active({"status": "archived"}) is False
    assert company_active({"status": "", "subscription_expire_date":
                           "2020-01-01"}) is False   # muddati o'tgan
    assert company_active({"status": "", "subscription_expire_date":
                           "2099-12-31"}) is True
    assert company_active({"status": "", "subscription_expire_date":
                           "buzuq-sana"}) is True    # parse xatosi — fail-open
    assert company_active({"status": "", "subscription_expire_date": ""}) is True


# ------------------------------------------------------- Storage auto-fill

def test_fill_by_route_and_name(tenv):
    st, ids = tenv["st"], tenv["ids"]
    okco, blockco = ids["okco-transport"], ids["blockco-mchj"]

    st.insert("profiles", {"name": "OKCO TRANSPORT", "route_id": "ro1"})
    assert st.find("profiles", name="OKCO TRANSPORT")["company_id"] == okco

    st.insert("vehicles", {"external_id": "v1", "route_id": "ro1"})
    st.insert("vehicles", {"external_id": "v2", "route_id": "rb1"})
    st.insert("vehicles", {"external_id": "v3", "route_id": "nomalum"})
    assert st.find("vehicles", external_id="v1")["company_id"] == okco
    assert st.find("vehicles", external_id="v2")["company_id"] == blockco
    assert st.find("vehicles", external_id="v3")["company_id"] == 0

    # ro2 DB profiles jadvalida yo'q — profiles.json fallback ishlaydi.
    st.insert("drivers", {"external_id": "d1", "route_id": "ro2"})
    assert st.find("drivers", external_id="d1")["company_id"] == okco
    st.insert("driver_fines", {"driver_id": "d1", "date": "2026-10-01",
                               "amount": 1000})
    assert st.find("driver_fines", driver_id="d1")["company_id"] == okco


def test_upsert_does_not_clobber_existing_company_id(tenv):
    st, ids = tenv["st"], tenv["ids"]
    okco = ids["okco-transport"]

    # company_id allaqachon qo'lda belgilangan — noma'lum route bilan
    # yangilash uni 0 ga tushirmasligi kerak.
    now = "2026-10-01T00:00:00Z"
    st._exec(f"INSERT INTO vehicles (external_id, company_id, created_at, "
             f"updated_at) VALUES ({st.db.ph}, {st.db.ph}, {st.db.ph}, "
             f"{st.db.ph})", ("v42", 42, now, now))
    st.upsert("vehicles", ["external_id"],
              {"external_id": "v42", "route_id": "nomalum"})
    assert st.find("vehicles", external_id="v42")["company_id"] == 42

    # Bo'sh (0) qiymat esa aniq route berilganda to'ldiriladi.
    st._exec(f"INSERT INTO vehicles (external_id, company_id, created_at, "
             f"updated_at) VALUES ({st.db.ph}, {st.db.ph}, {st.db.ph}, "
             f"{st.db.ph})", ("v43", 0, now, now))
    st.upsert("vehicles", ["external_id"],
              {"external_id": "v43", "route_id": "ro1"})
    assert st.find("vehicles", external_id="v43")["company_id"] == okco


def test_bulk_upsert_fills_company_id(tenv):
    st, ids = tenv["st"], tenv["ids"]
    okco = ids["okco-transport"]
    inserted, updated = st.bulk_upsert("vehicles", ["external_id"], [
        {"external_id": "b1", "route_id": "ro1"},
        {"external_id": "b2", "route_id": "ro1"},
        {"external_id": "b3", "route_id": "nomalum"},
    ])
    assert (inserted, updated) == (3, 0)
    assert st.find("vehicles", external_id="b1")["company_id"] == okco
    assert st.find("vehicles", external_id="b2")["company_id"] == okco
    assert st.find("vehicles", external_id="b3")["company_id"] == 0


# ------------------------------------------------------------- session_user

def test_session_user_company_id_and_multi_routes(tenv):
    ids = tenv["ids"]
    tok = deps.create_session({"username": "mgr", "id": 7,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})
    s = deps.session_user(tok)
    assert s["user_id"] == 7
    assert s["company_id"] == ids["okco-transport"]
    assert s["routes"] == ["ro1", "ro2"]
    assert s["is_admin"] is False

    tok_admin = deps.create_session({"username": "super", "id": 1,
                                     "role": "ADMIN", "company": ""})
    a = deps.session_user(tok_admin)
    assert a["is_admin"] is True
    assert a["company_id"] == 0
    assert a["routes"] is None


# --------------------------------------------------------------- force_route

def test_force_route_multi():
    params = deps.force_route(
        {"role": "MANAGER", "routes": ["ro1", "ro2"]}, "/api/summary", {})
    assert params["route"] == "ro1,ro2"

    params = deps.force_route(
        {"role": "MANAGER", "routes": ["r1"]}, "/api/month", {})
    assert params["route"] == "r1"

    params = deps.force_route(
        {"role": "MANAGER", "routes": []}, "/api/attendance", {})
    assert params["route"] == "\x00_qamrovsiz\x00"

    # ADMIN va qamrovsiz yo'l — tegilmaydi.
    assert deps.force_route(
        {"role": "ADMIN", "is_admin": True, "routes": None},
        "/api/summary", {}) == {}
    assert deps.force_route(
        {"role": "MANAGER", "routes": ["ro1"]}, "/api/me",
        {"route": "x"})["route"] == "x"


# -------------------------------------------------------------- tenant_guard

def test_tenant_guard_blocks_inactive_company(tenv):
    ids = tenv["ids"]
    tok = deps.create_session({"username": "blocked", "id": 9,
                               "role": "MANAGER", "company": "BLOCKCO MCHJ"})
    req = _mk_request("/api/routes", cookies={"bm_session": tok})
    resp = deps.tenant_guard(req)
    assert resp is not None and resp.status_code == 403
    assert req.state.tenant["company_id"] == ids["blockco-mchj"]


def test_tenant_guard_allows_active_blocks_unknown(tenv):
    ids = tenv["ids"]
    tok = deps.create_session({"username": "mgr", "id": 7,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})
    req = _mk_request("/api/routes", cookies={"bm_session": tok})
    assert deps.tenant_guard(req) is None
    assert req.state.tenant["company_id"] == ids["okco-transport"]

    # Noma'lum kompaniya — fail-closed (403).
    tok2 = deps.create_session({"username": "new", "id": 8,
                                "role": "DISPATCHER", "company": "YANGI MCHJ"})
    req2 = _mk_request("/api/routes", cookies={"bm_session": tok2})
    assert deps.tenant_guard(req2).status_code == 403

    # Kompaniyasi yo'q non-admin ham bloklanadi.
    tok3 = deps.create_session({"username": "nocompany", "id": 10,
                                "role": "DISPATCHER", "company": ""})
    assert deps.tenant_guard(
        _mk_request("/api/routes", cookies={"bm_session": tok3})).status_code == 403

    # ADMIN noma'lum/bo'sh kompaniya bilan ham o'tadi.
    tok4 = deps.create_session({"username": "root", "id": 1,
                                "role": "ADMIN", "company": "YANGI MCHJ"})
    assert deps.tenant_guard(
        _mk_request("/api/routes", cookies={"bm_session": tok4})) is None


def test_tenant_guard_reads_bearer_session(tenv):
    """Sessiya Bearer header'da bo'lsa ham tenant tekshiruvi ishlaydi."""
    tok = deps.create_session({"username": "mgr", "id": 7,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})
    req = _mk_request("/api/routes",
                      headers={"Authorization": f"Bearer {tok}"})
    assert deps.tenant_guard(req) is None
    assert req.state.tenant["company_id"] == tenv["ids"]["okco-transport"]

    tok2 = deps.create_session({"username": "blocked", "id": 9,
                                "role": "MANAGER", "company": "BLOCKCO MCHJ"})
    req2 = _mk_request("/api/routes",
                       headers={"Authorization": f"Bearer {tok2}"})
    resp = deps.tenant_guard(req2)
    assert resp is not None and resp.status_code == 403


def test_tenant_guard_lookup_error_is_closed(tenv, monkeypatch):
    """Tenant bazasi xato bersa — foydalanuvchi kiritilmaydi (fail-closed)."""
    tok = deps.create_session({"username": "mgr", "id": 7,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tenant_mod, "company_state", _boom)
    req = _mk_request("/api/routes", cookies={"bm_session": tok})
    assert deps.tenant_guard(req).status_code == 403


def test_tenant_guard_skips(tenv, monkeypatch):
    tok = deps.create_session({"username": "blocked", "id": 9,
                               "role": "MANAGER", "company": "BLOCKCO MCHJ"})
    # Test rejimi — tekshiruvsiz o'tadi.
    monkeypatch.setenv("BM_TEST_MODE", "1")
    req = _mk_request("/api/routes", cookies={"bm_session": tok})
    assert deps.tenant_guard(req) is None
    monkeypatch.delenv("BM_TEST_MODE", raising=False)

    # API bo'lmagan yo'l, login/logout, cookie yo'q — o'tkaziladi.
    assert deps.tenant_guard(_mk_request("/", {"bm_session": tok})) is None
    assert deps.tenant_guard(
        _mk_request("/api/login", {"bm_session": tok})) is None
    assert deps.tenant_guard(
        _mk_request("/api/logout", {"bm_session": tok})) is None
    assert deps.tenant_guard(_mk_request("/api/routes")) is None


# ------------------------------------------------------------- integratsiya

def test_api_routes_multi_route_via_client(tenv):
    st = tenv["st"]
    st.save_route(external_id="ro1", name="1-yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="ro2", name="2-yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="rb1", name="Blok", entity_type="ROUTE")
    tok = deps.create_session({"username": "mgr", "id": 7,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})
    r = _client().get("/api/routes", cookies={"bm_session": tok})
    assert r.status_code == 200
    ids = sorted(str(x.get("id")) for x in r.json().get("routes") or [])
    assert ids == ["ro1", "ro2"]


def test_blocked_company_api_returns_403(tenv):
    tok = deps.create_session({"username": "blocked", "id": 9,
                               "role": "MANAGER", "company": "BLOCKCO MCHJ"})
    r = _client().get("/api/routes", cookies={"bm_session": tok})
    assert r.status_code == 403
    body = r.json()
    assert body.get("ok") is False


def test_unknown_company_api_fail_closed(tenv):
    tok = deps.create_session({"username": "new", "id": 8,
                               "role": "DISPATCHER", "company": "YANGI MCHJ"})
    r = _client().get("/api/routes", cookies={"bm_session": tok})
    assert r.status_code == 403
    assert r.json().get("ok") is False


# ------------------------------------------------------- parse_filters company

def test_parse_filters_company_expands_to_routes(tenv):
    from bm_automation.app.dashboard.metrics import parse_filters
    f = parse_filters({"company": "OKCO TRANSPORT"})
    assert f["company"] == "OKCO TRANSPORT"
    assert f["route"] == "ro1,ro2"


def test_parse_filters_company_ignored_when_route_given(tenv):
    from bm_automation.app.dashboard.metrics import parse_filters
    f = parse_filters({"company": "OKCO TRANSPORT", "route": "ro2"})
    assert f["route"] == "ro2"


def test_parse_filters_unknown_company_keeps_route_empty(tenv):
    from bm_automation.app.dashboard.metrics import parse_filters
    f = parse_filters({"company": "Yo'q firma"})
    assert f["route"] == ""


# --------------------------------------------------- settings companies CRUD

class _Req:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


def test_settings_companies_crud(tenv):
    """`/api/settings/companies` — creds/add/delete ishlaydi va parol sizmaydi."""
    import asyncio
    from bm_automation.app.dashboard.routers import admin as admin_mod
    from bm_automation.app.core import profiles as pmod

    listed = admin_mod.get_companies(None)
    assert listed["ok"]
    assert all("password" not in c for c in listed["companies"])
    assert all("has_credentials" in c for c in listed["companies"])

    r = asyncio.run(admin_mod.update_companies(_Req(
        {"action": "creds", "name": "OKCO TRANSPORT",
         "username": "u1", "password": "p1"})))
    assert r["ok"]
    p = pmod.get_profile("OKCO TRANSPORT")
    assert p.get("username") == "u1" and p.get("password") == "p1"

    r = asyncio.run(admin_mod.update_companies(_Req(
        {"action": "add", "name": "YANGI MCHJ", "routeVariantId": "ry1"})))
    assert r["ok"]
    assert pmod.get_profile("YANGI MCHJ")["routeVariantId"] == "ry1"

    r = asyncio.run(admin_mod.update_companies(_Req(
        {"action": "delete", "name": "YANGI MCHJ"})))
    assert r["ok"]
    assert pmod.get_profile("YANGI MCHJ") is None


# ------------------------------------------------- company lifecycle (admin)

def test_company_delete_closes_sessions_and_activate_restores(tenv):
    """Soft-delete: sessiyalar yopiladi, login 403; activate qaytaradi."""
    import asyncio
    from bm_automation.app.dashboard.routers import admin as admin_mod

    st, ids = tenv["st"], tenv["ids"]
    cid = ids["okco-transport"]
    st.dashboard_user_add("root", *deps.hash_password("Root12345"),
                          role="ADMIN", data='{"super": true}')
    st.dashboard_user_add("okco1", *deps.hash_password("Parol12345"),
                          role="MANAGER", company="OKCO TRANSPORT")
    st.dashboard_user_add("okco2", *deps.hash_password("Parol12345"),
                          role="MANAGER", company="OKCO TRANSPORT")
    deps.invalidate_super_cache()
    tok = deps.create_session({"username": "okco1", "id": 11,
                               "role": "MANAGER", "company": "OKCO TRANSPORT"})
    assert deps.session_user(tok)
    scope = {"username": "root", "is_admin": True}

    r = asyncio.run(admin_mod.delete_company(cid, _Req({}), scope=scope))
    assert r["ok"]
    assert st.find("companies", id=cid)["status"] == "deleted"
    # Mavjud sessiya darhol kuchini yo'qotadi.
    assert deps.session_user(tok) is None
    assert tenant_mod.company_active(
        tenant_mod.company_state("OKCO TRANSPORT")) is False
    # Parol to'g'ri bo'lsa ham o'chirilgan kompaniya akkaunti kira olmaydi.
    resp = _client().post("/api/login",
                          json={"username": "okco1", "password": "Parol12345"})
    assert resp.status_code == 403

    # Faollashtirish keshni yangilaydi va kirishni tiklaydi.
    r = asyncio.run(admin_mod.activate_company(
        cid, _Req({"days": 30}), scope=scope))
    assert r["ok"]
    assert st.find("companies", id=cid)["status"] == "active"
    assert tenant_mod.company_active(
        tenant_mod.company_state("OKCO TRANSPORT")) is True
    resp = _client().post("/api/login",
                          json={"username": "okco2", "password": "Parol12345"})
    assert resp.status_code == 200 and resp.json().get("ok") is True
