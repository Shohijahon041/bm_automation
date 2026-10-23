"""Yangi FastAPI dashboard router'lari uchun pytest testlari.

Qamrov:
  * app kontrakti — asosiy yo'llar ro'yxatda, OpenAPI schema, SPA, xavfsizlik
    sarlavhalari (middleware).
  * deps.py — parol hash/verify, login brute-force himoyasi, POST rate limit,
    scope/force_route qoidalari, sessiya round-tripi, super admin.
  * Scope dependency — 401 (auth yo'q), 403 (rol yetarli emas), 200 (admin),
    is_admin bypass.
  * SQLite-seed bilan bir nechta o'qish endpoint'i (jonli BM API / Postgres'ga
    tegmaydi).

conftest autouse BM_TEST_MODE=1 o'rnatadi, shuning uchun himoyalangan
endpoint'lar autentifikatsiyasiz ADMIN sifatida javob beradi.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from bm_automation.app.dashboard import deps
from bm_automation.app.dashboard.deps import (
    Scope,
    create_session,
    drop_session,
    force_route,
    hash_password,
    is_super_username,
    login_allowed,
    login_fail,
    login_success,
    rate_limit_post,
    scope_routes,
    session_user,
    verify_password,
)
from bm_automation.app.db import storage as storage_mod
from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus
from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase


# ---------------------------------------------------------------------------
# Yordamchilar / fixture'lar
# ---------------------------------------------------------------------------

def _fake_request(method: str, path: str) -> Request:
    """Scope dependency'ni to'g'ridan-to'g'ri sinash uchun yengil HTTP Request."""
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 5555),
        "scheme": "http",
        "server": ("testserver", 80),
    })


@pytest.fixture
def client():
    """TestClient — lifespan'ni ISHGA TUSHIRMAYDI (tarmoq/GPS side-effect yo'q)."""
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


@pytest.fixture
def seeded_storage(monkeypatch, tmp_path):
    """SQLite'ga urug'langan Storage'ni singleton sifatida o'rnatadi."""
    deps.invalidate_super_cache()
    db = SQLiteDatabase(str(tmp_path / "bm_test.db"))
    st = storage_for(db)
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="r2", name="20-yo'nalish", entity_type="ROUTE")
    st.save_vehicle(external_id="v1", plate_number="01A123AA",
                    garage_number="G1", model="Mercedes", route_id="r1")
    st.save_driver(external_id="d1", full_name="Aliyev Aliy")

    def trip(d, rid, vid, drv, pt, status, actual=""):
        return TripRecord(date=d, route_id=rid, vehicle_id=vid, driver_id=drv,
                          planned_time=pt, actual_time=actual, status=status,
                          source=SyncSource.DUTY.value)

    st.save_trip(trip("2026-08-10", "r1", "v1", "d1", "06:00",
                      TripStatus.ACCEPTED.value, actual="06:15"))
    st.save_trip(trip("2026-08-10", "r1", "v1", "d1", "07:00",
                      TripStatus.REJECTED.value))

    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", st)
    yield st
    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", None)


@pytest.fixture
def no_post_rate_limit(monkeypatch):
    """Ketma-ket POST'lar uchun POST rate-limit'ni bo'shatadi.

    Limit mantig'ining o'z testi alohida (test_rate_limit_post_min_interval);
    funksional testlar 1 soniyalik intervalga tiqilib qolmasin. Holat
    monkeypatch orqali izolyatsiya qilinadi — boshqa testlarga ta'sir yo'q.
    """
    monkeypatch.setattr(deps, "_POST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(deps, "_last_post_at", {})


# ---------------------------------------------------------------------------
# App kontrakti
# ---------------------------------------------------------------------------

def test_core_routes_registered(client):
    paths = {getattr(r, "path", "") for r in client.app.routes}
    for p in (
        "/api/login", "/api/logout", "/api/me", "/api/summary", "/api/routes",
        "/api/month", "/api/attendance", "/api/brutto", "/api/drivers",
        "/api/settings", "/api/health", "/api/myai/status",
    ):
        assert p in paths, f"yo'qolgan yo'l: {p}"


def test_docs_disabled_without_test_mode(monkeypatch):
    """Productionda OpenAPI/docs yopiq bo'lishi kerak."""
    monkeypatch.delenv("BM_TEST_MODE", raising=False)
    monkeypatch.delenv("DASHBOARD_DOCS", raising=False)
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import create_app
    app = create_app()
    c = TestClient(app)
    assert c.get("/api/openapi.json").status_code == 404
    assert c.get("/api/docs").status_code == 404


def test_openapi_schema_shape(client):
    schema = client.app.openapi()
    assert schema["openapi"].startswith("3.")
    assert "/api/login" in schema["paths"]
    assert "post" in schema["paths"]["/api/login"]
    assert "/api/summary" in schema["paths"]


def test_openapi_alias_redirects_to_live_spec(monkeypatch):
    """Docs yoqilganda eski `/openapi.json` jonli sxemaga yo'naltiriladi."""
    monkeypatch.setenv("BM_TEST_MODE", "1")
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import create_app
    client = TestClient(create_app())
    r = client.get("/openapi.json", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers.get("location") == "/api/openapi.json"


def test_security_headers_present(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("cache-control") == "no-store"
    assert r.headers.get("x-robots-tag") == "noindex, nofollow"


def test_spa_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Autentifikatsiya / scope (endpoint darajasi)
# ---------------------------------------------------------------------------

def test_me_is_admin_under_test_mode(client):
    r = client.get("/api/me")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["role"] == "ADMIN"
    assert data["is_admin"] is True


def test_protected_endpoint_401_without_auth(client, monkeypatch):
    monkeypatch.delenv("BM_TEST_MODE", raising=False)
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    r = client.get("/api/summary")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Scope dependency (to'g'ridan-to'g'ri)
# ---------------------------------------------------------------------------

def test_scope_401_when_no_scope(monkeypatch):
    async def fake_resolve(request, creds=None):
        return None
    monkeypatch.setattr(deps, "resolve_scope", fake_resolve)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(Scope()(_fake_request("GET", "/api/me"), None))
    assert ei.value.status_code == 401


def test_scope_403_when_role_too_low(monkeypatch):
    async def fake_resolve(request, creds=None):
        return {"username": "u", "role": "VIEWER", "company": "",
                "is_admin": False, "routes": []}
    monkeypatch.setattr(deps, "resolve_scope", fake_resolve)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(Scope(min_role=4)(_fake_request("GET", "/api/me"), None))
    assert ei.value.status_code == 403


def test_scope_200_when_role_sufficient(monkeypatch):
    async def fake_resolve(request, creds=None):
        return {"username": "u", "role": "MANAGER", "company": "",
                "is_admin": False, "routes": []}
    monkeypatch.setattr(deps, "resolve_scope", fake_resolve)
    scope = asyncio.run(Scope(min_role=2)(_fake_request("GET", "/api/me"), None))
    assert scope["role"] == "MANAGER"


def test_scope_is_admin_bypasses_role(monkeypatch):
    async def fake_resolve(request, creds=None):
        return {"username": "root", "role": "VIEWER", "company": "",
                "is_admin": True, "routes": None}
    monkeypatch.setattr(deps, "resolve_scope", fake_resolve)
    # is_admin=True — min_role talabi e'tiborga olinmaydi
    scope = asyncio.run(Scope(min_role=4)(_fake_request("GET", "/api/users"), None))
    assert scope["is_admin"] is True


# ---------------------------------------------------------------------------
# deps: parol
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip():
    digest, salt = hash_password("secret123")
    assert verify_password("secret123", salt, digest) is True
    assert verify_password("wrong", salt, digest) is False
    # bo'sh salt/expected -> False (xavfsiz rad)
    assert verify_password("secret123", "", digest) is False
    assert verify_password("secret123", salt, "") is False


def test_password_hash_deterministic_with_fixed_salt():
    d1, s1 = hash_password("abc", salt="fixedsalt")
    d2, s2 = hash_password("abc", salt="fixedsalt")
    assert s1 == s2 == "fixedsalt"
    assert d1 == d2


def test_password_hash_random_salt_unique():
    d1, s1 = hash_password("abc")
    d2, s2 = hash_password("abc")
    assert s1 != s2
    assert d1 != d2


# ---------------------------------------------------------------------------
# deps: login brute-force himoyasi
# ---------------------------------------------------------------------------

def test_login_locks_after_max_fails():
    ip, user = "9.9.9.9", "mallory"
    login_success(ip, user)  # oldingi holatni tozalash
    for _ in range(deps._LOGIN_MAX_FAILS):
        login_fail(ip, user)
    assert login_allowed(ip, user) is False      # bloklandi
    assert login_allowed(ip, user) is False      # hali ham blokda
    login_success(ip, user)                       # blokni ochadi
    assert login_allowed(ip, user) is True


def test_login_min_interval_between_attempts():
    ip, user = "8.8.8.8", "carol"
    login_success(ip, user)
    login_fail(ip, user)                 # birinchi muvaffaqiyatsiz
    # darhol qayta urinish — min interval tufayli rad etiladi
    assert login_allowed(ip, user) is False
    login_success(ip, user)


# ---------------------------------------------------------------------------
# deps: POST rate limit
# ---------------------------------------------------------------------------

def test_rate_limit_post_min_interval():
    ep = f"/api/test/{time.time_ns()}"
    assert rate_limit_post(ep) is True
    assert rate_limit_post(ep) is False   # 1 soniya ichida takroriy rad


# ---------------------------------------------------------------------------
# deps: force_route / scope_routes
# ---------------------------------------------------------------------------

def test_scope_routes():
    assert scope_routes({"is_admin": True}) is None
    assert scope_routes({"is_admin": False, "routes": ["a", "b"]}) == {"a", "b"}
    assert scope_routes({}) is None


def test_force_route_admin_unchanged():
    admin = {"is_admin": True}
    assert force_route(admin, "/api/summary", {"route": "r9"}) == {"route": "r9"}


def test_force_route_scopes_non_admin():
    viewer = {"is_admin": False, "routes": ["r1"]}
    assert force_route(viewer, "/api/summary", {"route": "r9"})["route"] == "r1"
    # qamrovli bo'lmagan yo'l o'zgarmaydi
    assert force_route(viewer, "/api/health", {"x": 1}) == {"x": 1}


def test_force_route_without_routes_uses_sentinel():
    empty = {"is_admin": False, "routes": []}
    assert force_route(empty, "/api/summary", {})["route"] == "\x00_qamrovsiz\x00"


# ---------------------------------------------------------------------------
# deps: sessiya round-tripi
# ---------------------------------------------------------------------------

def test_session_roundtrip(monkeypatch, tmp_path, seeded_storage):
    monkeypatch.setattr(deps, "_sessions_file", lambda: tmp_path / "sessions.json")
    token = create_session({"username": "ali", "role": "MANAGER",
                            "company": "FERGANATEX"})
    user = session_user(token)
    assert user is not None
    assert user["username"] == "ali"
    assert user["role"] == "MANAGER"
    assert user["is_admin"] is False

    drop_session(token)
    assert session_user(token) is None


def test_session_unknown_token_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "_sessions_file", lambda: tmp_path / "sessions.json")
    assert session_user("nope-does-not-exist") is None


# ---------------------------------------------------------------------------
# deps: super admin
# ---------------------------------------------------------------------------

def test_is_super_username_from_env(monkeypatch, seeded_storage):
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "Root")
    assert is_super_username("root") is True
    assert is_super_username("ROOT ") is True
    assert is_super_username("other") is False


# ---------------------------------------------------------------------------
# Router smoke testlari (SQLite-seed, tarmoqsiz)
# ---------------------------------------------------------------------------

def test_routes_endpoint_returns_seeded(client, seeded_storage):
    r = client.get("/api/routes")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert isinstance(data["routes"], list)


def test_summary_endpoint_ok(client, seeded_storage):
    r = client.get("/api/summary?month=2026-08")
    assert r.status_code == 200


def test_month_endpoint_ok(client, seeded_storage):
    r = client.get("/api/month?month=2026-08")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_work_log_save_delete_roundtrip(client, seeded_storage,
                                        no_post_rate_limit):
    """Kunlik km qaydi: saqlash (upsert) + o'chirish (natural kalit).

    Regressiya: router mavjud bo'lmagan `storage.work_log_add` /
    `work_log_delete` chaqirar edi — endpoint 500 qaytarib, frontendda
    "Server javobi o'qilmadi" chiqardi.
    """
    body = {"date": "2026-08-12", "vehicle_id": "v1",
            "distance_km": 125.5, "trip_count": 3, "note": "Odometr"}
    r = client.post("/api/drivers/d1/work-log", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True

    # Xuddi shu natural kalitda qayta saqlash — upsert: dublikat bo'lmaydi.
    r = client.post("/api/drivers/d1/work-log",
                    json={**body, "distance_km": 130.0, "trip_count": 4,
                          "note": "Tuzatildi"})
    assert r.status_code == 200 and r.json()["ok"] is True

    detail = client.get("/api/drivers/d1?month=2026-08").json()
    logs = [x for x in detail["work_logs"] if x["date"] == "2026-08-12"]
    assert len(logs) == 1
    assert logs[0]["distance_km"] == 130.0 and logs[0]["trip_count"] == 4

    r = client.post("/api/drivers/d1/work-log-delete",
                    json={"date": "2026-08-12", "vehicle_id": "v1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    detail = client.get("/api/drivers/d1?month=2026-08").json()
    assert [x for x in detail["work_logs"] if x["date"] == "2026-08-12"] == []


def test_work_log_validation_rejects_bad_input(client, seeded_storage,
                                               no_post_rate_limit):
    r = client.post("/api/drivers/d1/work-log",
                    json={"date": "12.08.2026", "distance_km": 5})
    assert r.status_code == 400
    r = client.post("/api/drivers/d1/work-log",
                    json={"date": "2026-08-12", "distance_km": "abc"})
    assert r.status_code == 400
    r = client.post("/api/drivers/d1/work-log-delete",
                    json={"date": "2026-08-12", "vehicle_id": "yo'q"})
    assert r.status_code == 400


def test_fine_save_and_telegram_unlink(client, seeded_storage,
                                       no_post_rate_limit):
    """Jarima saqlash va Telegram bog'lanishni uzish ham AttributeError'lar
    tufayli buzilgan edi (fine_add / unlink_driver_telegram)."""
    r = client.post("/api/drivers/d1/fine",
                    json={"date": "2026-08-12", "amount": 150000,
                          "reason": "Kechikish"})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = client.post("/api/users/555/link-driver", json={"driver_id": ""})
    assert r.status_code == 200 and r.json()["ok"] is True


# ---------------------------------------------------------------------------
# BM'dan haydovchi to'ldirish (bm-sync)
# ---------------------------------------------------------------------------

def test_bm_sync_routes_registered(client):
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert "/api/drivers/{driver_id}/bm-sync" in paths
    assert "/api/drivers/bm-sync" in paths


def test_driver_bm_sync_calls_refresh(client, seeded_storage,
                                      no_post_rate_limit, monkeypatch):
    """Haydovchi karta tugmasi `refresh_driver_from_bm`ni chaqiradi."""
    from bm_automation.app.db import sync as db_sync
    calls: dict = {}

    def fake_refresh(storage, client_arg, driver_id, **kwargs):
        calls["driver_id"] = driver_id
        calls["with_photo"] = kwargs.get("with_photo")
        return {"ok": True, "driver_id": driver_id, "updated": True, "photo": True}

    monkeypatch.setattr(db_sync, "refresh_driver_from_bm", fake_refresh)
    r = client.post("/api/drivers/d1/bm-sync")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["photo"] is True
    assert calls["driver_id"] == "d1"
    assert calls["with_photo"] is True


def test_driver_bm_sync_photo_disabled(client, seeded_storage,
                                       no_post_rate_limit, monkeypatch):
    """`?photo=0` bilan rasm yuklab olinmaydi."""
    from bm_automation.app.db import sync as db_sync
    calls: dict = {}

    def fake_refresh(storage, client_arg, driver_id, **kwargs):
        calls["with_photo"] = kwargs.get("with_photo")
        return {"ok": True, "driver_id": driver_id, "updated": True, "photo": False}

    monkeypatch.setattr(db_sync, "refresh_driver_from_bm", fake_refresh)
    r = client.post("/api/drivers/d1/bm-sync?photo=0")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert calls["with_photo"] is False


def test_driver_bm_sync_error_is_reported(client, seeded_storage,
                                          no_post_rate_limit, monkeypatch):
    """BM'da haydovchi topilmasa xato matni qaytariladi (200 bilan)."""
    from bm_automation.app.db import sync as db_sync

    def fake_refresh(storage, client_arg, driver_id, **kwargs):
        return {"ok": False, "driver_id": driver_id, "error": "haydovchi BM'da topilmadi"}

    monkeypatch.setattr(db_sync, "refresh_driver_from_bm", fake_refresh)
    r = client.post("/api/drivers/d1/bm-sync")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "topilmadi" in r.json()["error"]
