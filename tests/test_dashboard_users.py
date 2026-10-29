"""Dashboard ko'p korxonali login/sessiya va rol qamrovi testlari (FastAPI).

Avval bu testlar stdlib `DashboardHandler`'ni to'g'ridan-to'g'ri chaqirar edi;
FastAPI migratsiyasidan keyin ular `TestClient` orqali haqiqiy router'larni
sinaydi. Auth/scope/rate-limit logikasi `bm_automation.app.dashboard.deps`da.
"""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.dashboard import deps
from bm_automation.app.db.storage import storage_for
from bm_automation.app.db.tenancy import seed_companies
from tests.sqlite_backend import SQLiteDatabase


def _client() -> TestClient:
    """Har so'rovda yangi TestClient (cookie jar toza, lifespan ishga tushmaydi)."""
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


def _split_headers(headers):
    """Content-Length'ni olib tashlaydi, Cookie'ni ajratib oladi."""
    headers = dict(headers or {})
    cookies = {}
    for k in list(headers):
        if k.lower() == "content-length":
            headers.pop(k)
        elif k.lower() == "cookie":
            for part in headers.pop(k).split(";"):
                kv = part.strip().split("=", 1)
                if len(kv) == 2:
                    cookies[kv[0]] = kv[1]
    return headers, cookies


def _collect_cookies(response) -> dict:
    out = {}
    for k, v in response.headers.multi_items():
        if k.lower() == "set-cookie":
            for part in v.split(";"):
                kv = part.strip().split("=", 1)
                if len(kv) == 2:
                    out[kv[0]] = kv[1]
    return out


def _request(storage, path, method="GET", headers=None, body=None,
             monkeypatch=None):
    """FastAPI router'ni TestClient orqali chaqirib natijani qaytaradi.

    `BM_TEST_MODE` o'chirilgan bo'lishi kerak (haqiqiy auth tekshiriladi).
    Qaytaradi: (status, json, cookies).

    Xato javoblarida FastAPI `{"detail": ...}` qaytaradi; eski shartnoma
    bilan moslash uchun `error`/`ok` maydonlariga normalizatsiya qilinadi.
    """
    hdrs, cookies = _split_headers(headers)
    if body is not None and not any(k.lower() == "content-type" for k in hdrs):
        hdrs["Content-Type"] = "application/json"
    r = _client().request(method, path, headers=hdrs or None,
                          content=body, cookies=cookies or None)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if isinstance(data, dict) and "detail" in data:
        data.setdefault("error", data["detail"])
        data.setdefault("ok", False)
    return r.status_code, data, _collect_cookies(r)


def _raw(env, path, method="GET", headers=None, body=b""):
    """(status, raw bytes) — fayl/HTML javoblarini tekshirish uchun."""
    hdrs, cookies = _split_headers(headers)
    r = _client().request(method, path, headers=hdrs or None,
                          content=body or None, cookies=cookies or None)
    return r.status_code, r.content


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "st.db"))
    return storage_for(db)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Haqiqiy auth uchun muhit: BM_TEST_MODE o'chirilgan, sessiya tmp'da."""
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
    monkeypatch.setattr(deps, "_POST_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(deps, "_LOGIN_MIN_INTERVAL_S", 0.0)
    db = SQLiteDatabase(str(tmp_path / "dash.db"))
    st = storage_for(db)
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage", lambda: st)
    monkeypatch.setattr("bm_automation.app.dashboard.metrics.get_storage",
                        lambda: st)
    # Test kompaniyasi: XTEST -> route r1
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE", tmp_path / "profiles.json")
    db2 = SQLiteDatabase(str(tmp_path / "profiles_seed.db"))
    _st2 = storage_for(db2)
    _st2.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    _st2.save_route(external_id="r2", name="20-yo'nalish", entity_type="ROUTE")
    profiles_mod.save_profiles({
        "active": "XTEST",
        "profiles": [
            {"name": "XTEST", "routeVariantId": "r1",
             "routeName": "10-yo'nalish",
             "profileId": "", "start1": "", "start2": ""},
            {"name": "YTEST", "routeVariantId": "r2",
             "routeName": "20-yo'nalish",
             "profileId": "", "start1": "", "start2": ""},
        ],
    })
    seed_companies(st, [
        {"name": "XTEST", "routeVariantId": "r1"},
        {"name": "YTEST", "routeVariantId": "r2"},
    ])
    return st


def _login(env, username, password):
    body = json.dumps({"username": username, "password": password}).encode()
    headers = {"Content-Length": str(len(body))}
    return _request(env, "/api/login", method="POST", headers=headers, body=body)


# ---------------------------------------------------------------- auth

def test_password_policy_rejects_weak_values():
    """Parol siyosati: qisqa, keng tarqalgan va login bilan bir xil parol rad etiladi."""
    assert deps.password_error("qisqa") is not None
    assert deps.password_error("12345678") is not None
    assert deps.password_error("ali", "ali") is not None
    assert deps.password_error("parol1234") == ""


def test_schema_creates_dashboard_users(storage):
    """Storagema dashboard_users jadvali mavjud va ishlaydi."""
    uid = storage.dashboard_user_add("ali", "hash", "salt", "MANAGER",
                                     "XTEST", 1)
    assert uid > 0
    row = storage.dashboard_user_get(username="ali")
    assert row["role"] == "MANAGER"
    assert row["company"] == "XTEST"
    assert row["salt"] == "salt"
    assert storage.dashboard_users_count() == 1


def test_dashboard_user_unique_username(storage):
    """Bir xil username ikki marta qo'shilmaydi (UNIQUE)."""
    storage.dashboard_user_add("ali", "h1", "s1")
    storage.dashboard_user_add("ali", "h2", "s2")
    rows = storage.dashboard_users_list()
    assert len(rows) == 1
    assert rows[0]["salt"] == "s1"


def test_dashboard_user_update_and_delete(storage):
    uid = storage.dashboard_user_add("ali", "hash", "salt")
    assert storage.dashboard_user_update(uid, role="DIRECTOR",
                                         company="XTEST", active=0)
    row = storage.dashboard_user_get(row_id=uid)
    assert row["role"] == "DIRECTOR"
    assert row["company"] == "XTEST"
    assert row["active"] == 0
    assert storage.delete_dashboard_user(uid)
    assert storage.dashboard_user_get(row_id=uid) is None


def test_dashboard_user_data_roundtrip(storage):
    """`data` ustuni (masalan {"super": true}) saqlanadi va qayta o'qiladi."""
    uid = storage.dashboard_user_add("boss", "h", "s", role="ADMIN",
                                     data='{"super": true}')
    assert uid > 0
    row = storage.dashboard_user_get(row_id=uid)
    assert '"super": true' in row["data"]
    # Default data '{}'.
    uid2 = storage.dashboard_user_add("oddiy", "h", "s", role="ADMIN")
    assert storage.dashboard_user_get(row_id=uid2)["data"] == "{}"


def test_password_hash_and_verify(env):
    """Parol salt bilan xeshlanadi va to'g'ri tekshiriladi."""
    digest, salt = deps.hash_password("sirli-parol")
    assert digest and salt and digest != "sirli-parol"
    assert deps.verify_password("sirli-parol", salt, digest)
    assert not deps.verify_password("boshqa", salt, digest)
    # Turli salt -> turli hash
    digest2, salt2 = deps.hash_password("sirli-parol")
    assert digest2 != digest


def test_login_requires_auth(env, monkeypatch):
    """Ruxsatsiz API so'rovi 401 qaytaradi."""
    status, data, _ = _request(env, "/api/summary", monkeypatch=monkeypatch)
    assert status == 401
    assert "Kirish" in data.get("error", "")


def test_login_wrong_password(env):
    st = env
    st.dashboard_user_add("ali", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    status, data, _ = _login(env, "ali", "noto'g'ri")
    assert status == 401
    assert data.get("ok") is False


def test_login_success_and_session(env):
    st = env
    st.dashboard_user_add("ali", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    status, data, cookies = _login(env, "ali", "to'g'ri")
    assert status == 200 and data["ok"] is True
    assert data["role"] == "DISPATCHER"
    assert data["company"] == "XTEST"
    token = cookies.get("bm_session")
    assert token
    # Sessiya orqali /api/me ishlaydi
    status, data, _ = _request(env, "/api/me",
                               headers={"Cookie": f"bm_session={token}"})
    assert status == 200 and data["ok"] is True
    assert data["username"] == "ali"
    assert data["role"] == "DISPATCHER"
    assert data["company"]["name"] == "XTEST"


def test_logout_destroys_session(env):
    st = env
    st.dashboard_user_add("ali", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "ali", "to'g'ri")
    token = cookies.get("bm_session")
    hdr = {"Cookie": f"bm_session={token}"}
    status, data, _ = _request(env, "/api/logout", method="POST",
                               headers=hdr, body=b"{}")
    assert status == 200 and data["ok"] is True
    status, data, _ = _request(env, "/api/me", headers=hdr)
    assert status == 200
    assert data["ok"] is False and data["authenticated"] is False


def test_me_anonymous_returns_200_false(env):
    """Login qilmagan probe 401 emas, 200 {"ok": false} qaytaradi."""
    status, data, _ = _request(env, "/api/me")
    assert status == 200
    assert data["ok"] is False and data["authenticated"] is False


def test_token_bearer_restores_admin(env, monkeypatch):
    """DASHBOARD_TOKEN bearer'da ADMIN qaytadi (DISPATCHER ham emas)."""
    monkeypatch.setattr(deps, "_dashboard_token", lambda: "master-token")
    status, data, _ = _request(
        env, "/api/routes",
        headers={"Authorization": "Bearer master-token"})
    assert status == 200
    ids = [r.get("id") for r in data["routes"]]
    assert {"r1", "r2"} <= set(ids)


def test_session_wins_over_master_token(env, monkeypatch):
    """Sessiya bilan kirilgan foydalanuvchi master token ustidan o'zi bo'ladi.

    Loopback'da sahifaga in'ektsiya qilingan DASHBOARD_TOKEN har so'rovda
    Authorization header orqali keladi; bu token sessiyani bosib, DISPATCHER
    foydalanuvchini ADMIN qilib ko'rsatmasligi kerak.
    """
    monkeypatch.setattr(deps, "_dashboard_token", lambda: "master-token")
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Authorization": "Bearer master-token"}
    # /api/me sessiya rolini qaytaradi, ADMIN emas.
    status, data, _ = _request(env, "/api/me", headers=hdr)
    assert status == 200 and data["ok"] is True
    assert data["role"] == "DISPATCHER"
    assert data["is_admin"] is False
    # Route'lar ham faqat o'z korxonasi.
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert [r.get("id") for r in data["routes"]] == ["r1"]
    # ADMIN-only endpoint hali ham 403.
    status, _, _ = _request(env, "/api/users", headers=hdr)
    assert status == 403
    # Master token sessiyasiz bo'lsa ADMIN bo'lib qoladi.
    status, data, _ = _request(
        env, "/api/me",
        headers={"Authorization": "Bearer master-token"})
    assert status == 200
    assert data["is_admin"] is True


def test_inactive_user_cannot_login(env):
    st = env
    uid = st.dashboard_user_add("ali", *deps.hash_password("to'g'ri"),
                                role="DISPATCHER", company="XTEST")
    st.dashboard_user_update(uid, active=0)
    status, _, _ = _login(env, "ali", "to'g'ri")
    assert status == 401


# ---------------------------------------------------------------- scope

def test_routes_only_own_company(env, monkeypatch):
    """DISPATCHER faqat o'z korxonasi route'ini ko'radi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert status == 200
    ids = [r.get("id") for r in data["routes"]]
    assert ids == ["r1"]
    assert "r2" not in ids


def test_summary_route_forced_to_company(env):
    """Boshqa korxona route'i so'ralsa ham o'z route'iga kesiladi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/summary?route=r2", headers=hdr)
    assert status == 200
    assert data.get("ok") is True


def test_admin_sees_all_routes(env):
    st = env
    st.dashboard_user_add("admin", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "admin", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    ids = {r.get("id") for r in data["routes"]}
    assert {"r1", "r2"} <= ids


def test_dispatcher_blocked_from_admin_endpoints(env):
    """DISPATCHER /api/users (ADMIN-only) dan 403 oladi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/users", headers=hdr)
    assert status == 403


def test_director_blocked_from_myai(env):
    """DIRECTOR /api/myai (ADMIN-only) dan 403 oladi."""
    st = env
    st.dashboard_user_add("dir", *deps.hash_password("to'g'ri"),
                          role="DIRECTOR", company="XTEST")
    _, _, cookies = _login(env, "dir", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/myai/status", headers=hdr)
    assert status == 403


def test_dispatcher_blocked_from_brutto(env):
    """DISPATCHER brutto (moliyaviy) ni ko'rmaydi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/brutto", headers=hdr)
    assert status == 403


def test_dispatcher_blocked_from_rejects_report(env):
    """Qabul qilinmagan KM hisoboti (moliyaviy) DISPATCHER uchun 403."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/rejects", headers=hdr)
    assert status == 403
    # MANAGER ko'radi.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/rejects", headers=hdr)
    assert status == 200


def test_routes_finance_fields_stripped_for_dispatcher(env):
    """DISPATCHER /api/routes da skm/km_rate/tarif (pul) ko'rmaydi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert status == 200
    for r in data["routes"]:
        assert "skm" not in r
        assert "km_rate" not in r
        assert "tariff_no_vat" not in r
        assert "tariff_vat" not in r
    # MANAGER uchun moliyaviy maydonlar mavjud.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert status == 200
    assert all("km_rate" in r for r in data["routes"])


def test_summary_finance_fields_stripped_for_dispatcher(env):
    """DISPATCHER /api/summary routesida ham pul maydonlari yashirin."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/summary", headers=hdr)
    assert status == 200
    for r in (data.get("routes") or []):
        assert "skm" not in r
        assert "km_rate" not in r


def test_summary_cache_not_mutated_by_dispatcher(env, monkeypatch):
    """DISPATCHER summary so'raganidan keyin keshda finans maydonlari qoladi.

    `_strip_finance` nusxa qaytarishi kerak — aks holda belgilangan cache
    ob'ektidan maydonlar o'chib, keyingi MANAGER ham pulni ko'rmay qoladi.
    """
    from bm_automation.app.dashboard import metrics as metrics_mod
    metrics_mod._summary_cache = {"at": 0.0, "key": "", "data": None}
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/summary", headers=hdr)
    assert status == 200
    assert all("km_rate" not in r for r in (data.get("routes") or []))
    # Keyingi MANAGER xuddi shu cache'dan ma'lumot oladi — finans saqlanadi.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/summary", headers=hdr)
    assert status == 200
    assert all("km_rate" in r for r in (data.get("routes") or []))


# ------------------------------------------------------ user administration

def test_admin_creates_user_via_api(env):
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"action": "create", "username": "meneger",
                       "role": "MANAGER", "company": "XTEST",
                       "password": "parol1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(username="meneger")
    assert row["role"] == "MANAGER"
    assert row["company"] == "XTEST"


def test_nonadmin_cannot_manage_users(env):
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Length": "2", "Content-Type": "application/json"}
    status, _, _ = _request(env, "/api/dashboard-users", method="POST",
                            headers=hdr, body=b"{}")
    assert status == 403


def test_admin_list_users_scrubs_secrets(env):
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    uid = st.dashboard_user_add("ali", *deps.hash_password("x"),
                                role="DISPATCHER", company="XTEST")
    st.dashboard_user_update(uid, active=0)
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"
                     f"; bm_token=abc"}
    status, data, _ = _request(env, "/api/dashboard-users?action=list",
                               headers=hdr)
    assert status == 200
    users = {u["username"]: u for u in data["users"]}
    assert "ali" in users
    assert users["ali"]["active"] is False
    assert users["ali"]["company"] == "XTEST"
    for u in data["users"]:
        assert "password_hash" not in u
        assert "salt" not in u


def test_admin_edits_user_role_and_company(env):
    """TAHRIRLASH: rol va kompaniya almashadi (parol berilmasa o'zgarmaydi)."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    uid = st.dashboard_user_add("ali", *deps.hash_password("eski"),
                                role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"action": "update", "username": "ali",
                       "role": "MANAGER", "company": "YTEST"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(row_id=uid)
    assert row["role"] == "MANAGER"
    assert row["company"] == "YTEST"
    # Parol kiritilmagani uchun eski parol o'z kuchini saqlaydi.
    _, od, _ = _login(env, "ali", "eski")
    assert od.get("ok") is True


def test_admin_deletes_user_by_username(env):
    """O'CHIRISH tugmasi faqat username yuboradi — server username bilan topadi.

    O'chirish faqat SUPER ADMIN'da; super adminni o'zi ham o'chira olmaydi.
    """
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN", data='{"super": true}')
    uid = st.dashboard_user_add("ali", *deps.hash_password("x"),
                                role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"action": "delete", "username": "ali"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    assert st.dashboard_user_get(row_id=uid) is None
    # Super adminning o'zini o'chirish taqiqlangan.
    body2 = json.dumps({"action": "delete", "username": "root"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body2))},
                               body=body2)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="root") is not None


def test_admin_resets_password_only_updates_password(env):
    """PAROL tugmasi: faqat parol yuboriladi — rol/kompaniya o'zgarmaydi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    st.dashboard_user_add("ali", *deps.hash_password("eski"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"action": "update", "username": "ali",
                       "password": "yangi-1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(username="ali")
    assert row["role"] == "DISPATCHER"
    assert row["company"] == "XTEST"
    # Yangi parol kiradi, eski parol kirmaydi.
    _, nd, _ = _login(env, "ali", "yangi-1234")
    assert nd.get("ok") is True
    _, od, _ = _login(env, "ali", "eski")
    assert od.get("ok") is False


def test_admin_toggles_active_only_keeps_company(env):
    """Faol checkbox: faqat active yuboriladi — kompaniya saqlanadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    uid = st.dashboard_user_add("ali", *deps.hash_password("x"),
                                role="DIRECTOR", company="XTEST")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"action": "update", "username": "ali",
                       "active": False}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(row_id=uid)
    assert row["active"] == 0
    assert row["role"] == "DIRECTOR"
    assert row["company"] == "XTEST"


# ------------------------------------------------- super admin (2026)

def _mk(env, username, role, company="XTEST", password="x", data=""):
    """Belgilangan rol/kompaniya foydalanuvchisini yaratib header qaytaradi."""
    st = env
    st.dashboard_user_add(username, *deps.hash_password(password),
                          role=role, company=company, data=data)
    deps.invalidate_super_cache()
    _, _, cookies = _login(env, username, password)
    return {"Cookie": f"bm_session={cookies['bm_session']}",
            "Content-Type": "application/json"}


def test_super_detection_env_and_data(env, monkeypatch):
    """Super = DASHBOARD_ADMIN_USER env yoki data.super qatori."""
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "BIZNES")
    st = env
    st.dashboard_user_add("boss", "h", "s", role="ADMIN",
                          data='{"super": true}')
    st.dashboard_user_add("oddiy", "h", "s", role="ADMIN")
    assert deps.is_super_username("biznes") is True
    assert deps.is_super_username("BOSS") is True
    assert deps.is_super_username("boss") is True
    assert deps.is_super_username("oddiy") is False


def test_me_super_flag(env):
    """/api/me super admin uchun super=true qaytaradi."""
    st = env
    st.dashboard_user_add("basi", *deps.hash_password("x"),
                          role="ADMIN", data='{"super": true}')
    st.dashboard_user_add("oddi", *deps.hash_password("x"),
                          role="ADMIN")
    _, _, cookies = _login(env, "basi", "x")
    status, data, _ = _request(env, "/api/me",
                               headers={"Cookie": f"bm_session={cookies['bm_session']}"})
    assert status == 200 and data.get("super") is True
    _, _, cookies = _login(env, "oddi", "x")
    status, data, _ = _request(env, "/api/me",
                               headers={"Cookie": f"bm_session={cookies['bm_session']}"})
    assert status == 200 and data.get("super") is False


def test_super_telegram_binding_saved(env):
    """Egasining Telegram chat ID'si panel orqali saqlanadi.

    `data.super` bayrog'i yo'qolmaydi va bot tomonidagi `is_super_chat`
    aynan shu maydon orqali egalikni aniqlaydi (`/vault` ruxsati).
    """
    from bm_automation.app.notifications.ops.roles import is_super_chat
    hdr = _mk(env, "basi", "ADMIN", data='{"super": true}')
    body = json.dumps({"action": "update", "username": "basi",
                       "telegram_chat_id": "555001"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers=hdr, body=body)
    assert status == 200 and data.get("ok") is True
    row = env.dashboard_user_get(username="basi")
    saved = json.loads(row["data"])
    assert saved.get("super") is True
    assert saved.get("telegram_chat_id") == "555001"
    assert is_super_chat(555001) is True
    assert is_super_chat(555002) is False


def test_bootstrap_admin_gets_super_flag(env, monkeypatch):
    """Ilgari yaratilgan bootstrap ADMIN `data`si bo'sh bo'lsa ham super
    bayrog'i qo'yiladi.

    Aks holda hech kim super bo'lmay, "Bilim bazasi" bo'limi va
    `/api/knowledge` hammaga yopiq qolardi (VDS'da aynan shu holat edi).
    """
    st = env
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "root")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASS", "parol1234")
    st.dashboard_user_add("root", *deps.hash_password("parol1234"),
                          role="ADMIN", company="", active=1, data='{}')
    assert json.loads(st.dashboard_user_get(username="root")["data"]) == {}

    deps.ensure_bootstrap_admin()

    saved = json.loads(st.dashboard_user_get(username="root")["data"])
    assert saved.get("super") is True
    assert saved.get("pages") is None
    deps.invalidate_super_cache()
    assert deps.is_super_username("root") is True


def test_bootstrap_admin_promoted_to_admin(env, monkeypatch):
    """Bootstrap akkaunt boshqa rolда bo'lsa — ADMIN ga o'tkaziladi va
    bo'limlar ro'yxati olib tashlanadi (super faqat ADMIN bilan ishlaydi)."""
    st = env
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "egasi")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASS", "parol1234")
    st.dashboard_user_add("egasi", *deps.hash_password("parol1234"),
                          role="DIRECTOR", company="XTEST", active=1,
                          data='{"pages": ["overview"]}')

    deps.ensure_bootstrap_admin()

    row = st.dashboard_user_get(username="egasi")
    saved = json.loads(row["data"])
    assert row["role"] == "ADMIN" and (row["company"] or "") == ""
    assert saved.get("super") is True and "pages" not in saved


def test_bootstrap_admin_keeps_other_data(env, monkeypatch):
    """Super bayrog'i qo'yilganda `data` ichidagi boshqa kalitlar saqlanadi."""
    st = env
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "root")
    monkeypatch.setenv("DASHBOARD_ADMIN_PASS", "parol1234")
    st.dashboard_user_add("root", *deps.hash_password("parol1234"),
                          role="ADMIN", active=1,
                          data='{"telegram_chat_id": "555001"}')

    deps.ensure_bootstrap_admin()

    saved = json.loads(st.dashboard_user_get(username="root")["data"])
    assert saved.get("super") is True
    assert saved.get("telegram_chat_id") == "555001"


def test_knowledge_api_super_only(env, monkeypatch, tmp_path):
    """Bilim bazasi API: faqat super ADMIN; oddiy ADMIN 403 oladi."""
    monkeypatch.setenv("KNOWLEDGE_VAULT_DIR", str(tmp_path / "vault"))
    hdr_super = _mk(env, "basi", "ADMIN", data='{"super": true}')
    hdr_admin = _mk(env, "root", "ADMIN")

    status, data, _ = _request(env, "/api/knowledge/list", headers=hdr_admin)
    assert status == 403
    status, _, _ = _request(env, "/api/knowledge/stats", headers=hdr_admin)
    assert status == 403
    status, _, _ = _request(env, "/api/knowledge/graph", headers=hdr_admin)
    assert status == 403
    status, data, _ = _request(env, "/api/knowledge/list", headers=hdr_super)
    assert status == 200 and data.get("ok") is True
    status, data, _ = _request(env, "/api/knowledge/graph", headers=hdr_super)
    assert status == 200 and data.get("ok") is True


def test_non_super_admin_cannot_delete(env):
    """O'chirish huquqi faqat super admin; oddiy ADMIN o'chira olmaydi."""
    st = env
    uid = st.dashboard_user_add("ali", *deps.hash_password("x"),
                                role="DISPATCHER", company="XTEST")
    hdr = _mk(env, "root2", "ADMIN")
    body = json.dumps({"action": "delete", "username": "ali"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert "Super Adminda" in data.get("error", "")
    assert st.dashboard_user_get(row_id=uid) is not None


def test_super_admin_deletes_others_but_not_self(env):
    """Super admin boshqasini o'chiradi; o'zini o'chirolmaydi."""
    st = env
    uid = st.dashboard_user_add("ali", *deps.hash_password("x"),
                                role="DIRECTOR", company="XTEST")
    hdr = _mk(env, "basi2", "ADMIN", data='{"super": true}')
    body = json.dumps({"action": "delete", "username": "ali"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    assert st.dashboard_user_get(row_id=uid) is None
    body = json.dumps({"action": "delete", "username": "basi2"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="basi2") is not None


def test_non_super_admin_cannot_delete_super(env):
    """Boshqa ADMIN super adminni o'chira olmaydi."""
    st = env
    st.dashboard_user_add("basi", *deps.hash_password("x"),
                          role="ADMIN", data='{"super": true}')
    hdr = _mk(env, "root", "ADMIN")
    body = json.dumps({"action": "delete", "username": "basi"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="basi") is not None


def test_only_super_creates_admin_role(env):
    """ADMIN rol yaratish faqat super admin'da."""
    st = env
    hdr = _mk(env, "root", "ADMIN")
    body = json.dumps({"action": "create", "username": "hacker",
                       "role": "ADMIN", "password": "parol1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="hacker") is None
    # Super admin ADMIN yaratadi.
    hdr2 = _mk(env, "basi", "ADMIN", data='{"super": true}')
    body = json.dumps({"action": "create", "username": "admin2",
                       "role": "ADMIN", "password": "parol1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr2, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    assert st.dashboard_user_get(username="admin2")["role"] == "ADMIN"


def test_super_admin_role_and_company_immutable(env):
    """Super admin qatori: rol/kompaniya/faollik daxlsiz (parol mumkin)."""
    st = env
    st.dashboard_user_add("basi", *deps.hash_password("x"),
                          role="ADMIN", data='{"super": true}')
    hdr = _mk(env, "basi2", "ADMIN", data='{"super": true}')
    body = json.dumps({"action": "update", "username": "basi",
                       "role": "MANAGER", "company": "XTEST",
                       "active": False}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    row = st.dashboard_user_get(username="basi")
    assert row["role"] == "ADMIN"
    assert row["company"] == ""
    assert row["active"] == 1
    # Parol o'zgartirish mumkin.
    body = json.dumps({"action": "update", "username": "basi",
                       "password": "yangi-1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    _, nd, _ = _login(env, "basi", "yangi-1234")
    assert nd.get("ok") is True


def test_dispatcher_cannot_list_dashboard_users(env):
    """Ro'yxat ham MANAGER+ (rang 2); DISPATCHER 403 oladi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("x"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "x")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/dashboard-users", headers=hdr)
    assert status == 403


def test_manager_users_list_scoped(env):
    """MANAGER faqat o'z korxonasi foydalanuvchilarini ko'radi."""
    st = env
    st.dashboard_user_add("basi", *deps.hash_password("x"),
                          role="ADMIN", data='{"super": true}')
    st.dashboard_user_add("disp", *deps.hash_password("x"),
                          role="DISPATCHER", company="XTEST")
    st.dashboard_user_add("dir", *deps.hash_password("x"),
                          role="DIRECTOR", company="YTEST")
    hdr = _mk(env, "mgr", "MANAGER", "XTEST")
    status, data, _ = _request(env, "/api/dashboard-users", headers=hdr)
    assert status == 200 and data.get("ok") is True
    assert data.get("is_super") is False
    names = {u["username"] for u in data["users"]}
    assert names == {"mgr", "disp"}
    assert "dir" not in names
    assert "basi" not in names          # super (kompaniya yo'q) ko'rinmaydi
    assert all("super" in u for u in data["users"])
    assert [c["name"] for c in data["companies"]] == ["XTEST"]


def test_manager_create_scoped_and_role_capped(env):
    """MANAGER o'z darajasidan yuqori rol yarata olmaydi; kompaniya majburan."""
    st = env
    hdr = _mk(env, "mgr", "MANAGER", "XTEST")
    body = json.dumps({"action": "create", "username": "dir2",
                       "role": "DIRECTOR", "password": "parol1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="dir2") is None
    # VIEWER yaratadi — kompaniya o'z korxonasiga yoziladi.
    body = json.dumps({"action": "create", "username": "viewer2",
                       "role": "VIEWER", "company": "YTEST",
                       "password": "parol1234"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(username="viewer2")
    assert row["company"] == "XTEST"
    assert row["role"] == "VIEWER"


def test_manager_cannot_edit_other_company_user(env):
    """MANAGER boshqa korxona foydalanuvchisini tahrirlay olmaydi."""
    st = env
    st.dashboard_user_add("dir", *deps.hash_password("x"),
                          role="DIRECTOR", company="YTEST")
    hdr = _mk(env, "mgr", "MANAGER", "XTEST")
    body = json.dumps({"action": "update", "username": "dir",
                       "role": "VIEWER"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="dir")["role"] == "DIRECTOR"


def test_manager_cannot_promote_peer_or_admin(env):
    """MANAGER tengi yoki o'zidan yuqori rol belgilay olmaydi; ADMIN ham taqiq."""
    st = env
    st.dashboard_user_add("pee", *deps.hash_password("x"),
                          role="MANAGER", company="XTEST")
    hdr = _mk(env, "mgr", "MANAGER", "XTEST")
    body = json.dumps({"action": "update", "username": "pee",
                       "role": "DIRECTOR"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    body = json.dumps({"action": "update", "username": "pee",
                       "role": "ADMIN"}).encode()
    status, data, _ = _request(env, "/api/dashboard-users", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert data.get("ok") is False
    assert st.dashboard_user_get(username="pee")["role"] == "MANAGER"


def test_super_list_is_super_and_has_companies(env):
    """Super admin ro'yxati is_super=true va barcha kompaniyalarni qamraydi."""
    st = env
    st.dashboard_user_add("basi", *deps.hash_password("x"),
                          role="ADMIN", data='{"super": true}')
    st.dashboard_user_add("disp", *deps.hash_password("x"),
                          role="DISPATCHER", company="XTEST")
    hdr = _mk(env, "basi2", "ADMIN", data='{"super": true}')
    status, data, _ = _request(env, "/api/dashboard-users", headers=hdr)
    assert status == 200 and data.get("ok") is True
    assert data.get("is_super") is True
    names = {u["username"] for u in data["users"]}
    assert {"basi", "basi2", "disp"} <= names
    assert all("super" in u for u in data["users"])
    by_name = {u["username"]: u for u in data["users"]}
    assert by_name["basi"]["super"] is True
    assert by_name["disp"]["super"] is False
    comps = {c["name"] for c in data["companies"]}
    assert {"XTEST", "YTEST"} <= comps


# ------------------------------------------------- rol/xavfsizlik hardending

def test_viewer_can_login_but_only_me(env):
    """VIEWER /api/me ni ko'radi, boshqa endpointlardan 403 oladi."""
    st = env
    st.dashboard_user_add("viewer", *deps.hash_password("to'g'ri"),
                          role="VIEWER", company="XTEST")
    _, _, cookies = _login(env, "viewer", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/me", headers=hdr)
    assert status == 200 and data["ok"] is True
    assert data["role"] == "VIEWER"
    status, _, _ = _request(env, "/api/summary", headers=hdr)
    assert status == 403


def test_dispatcher_blocked_from_sms_log(env):
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/sms-log", headers=hdr)
    assert status == 403


def test_dispatcher_blocked_from_sms_routes(env):
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/sms/routes", headers=hdr)
    assert status == 403


def test_dispatcher_cannot_see_electricity_price(env):
    """Elektr narxi/energiya hisoboti moliyaviy — DISPATCHER (rang 2) ko'rmaydi."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    status, _, _ = _request(env, "/api/electricity/price", headers=hdr)
    assert status == 403
    status, _, _ = _request(env, "/api/electricity", headers=hdr)
    assert status == 403
    body = json.dumps({"rate": 10}).encode()
    status, _, _ = _request(env, "/api/electricity/price", method="POST",
                            headers={**hdr, "Content-Length": str(len(body))},
                            body=body)
    assert status == 403
    # MANAGER (rang 2) ko'radi.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/electricity/price", headers=hdr)
    assert status == 200


def test_electricity_endpoint_scopes_to_company_routes(env):
    """Ko'p korxonali rejim: /api/electricity qatorlari haqiqatda bajarilgan
    yo'nalish (route_daily) bo'yicha filtrlanadi — XTEST hisobotida YTEST
    yo'nalishi (r2) ko'rinmaydi, hatto haydovchi asosiy yo'nalishi r2 bo'lsa."""
    st = env
    st.save_driver(external_id="d1", full_name="Aliyev Aliy", route_id="r1")
    st.save_driver(external_id="d2", full_name="Karimov Karim", route_id="r2")
    st.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_fact": 12, "distance_fact": 180.0})
    # r1'da o'rinbosar ishlagan r2 haydovchisi — km r1'ga tegishli
    st.save_driver_work_log("2026-08-10", "d2", "v1", 180.0, 12,
                            note="AVTO")
    st.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r2", "vehicle_id": "v3",
        "vehicle_number": "03C789CC", "working_day": 1,
        "trip_fact": 10, "distance_fact": 150.0})
    st.save_driver_work_log("2026-08-10", "d2", "v3", 150.0, 10,
                            note="AVTO")
    hdr = _mk(env, "xtest_dir", "DIRECTOR", "XTEST", password="x")
    status, data, _ = _request(
        env, "/api/electricity?from=2026-08-01&to=2026-08-31", headers=hdr)
    assert status == 200
    assert [r["route_id"] for r in data["routes"]] == ["r1"]
    assert data["totals"]["km"] == 180.0
    assert {d["company"] for d in data["drivers"]} == {"XTEST"}


def test_dispatcher_blocked_from_telegram_user_admin(env):
    """/api/users/* POST (rol/delete/link) faqat ADMIN uchun."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"role": "ADMIN"}).encode()
    status, _, _ = _request(env, "/api/users/5/role", method="POST",
                            headers={**hdr, "Content-Length": str(len(body))},
                            body=body)
    assert status == 403


def test_dispatcher_blocked_from_dispatcher_routing(env):
    """Dispetcher route/telefon konfiguratsiyasi faqat ADMIN uchun."""
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"routes": []}).encode()
    status, _, _ = _request(env, "/api/dispatchers/5/routes", method="POST",
                            headers={**hdr, "Content-Length": str(len(body))},
                            body=body)
    assert status == 403


def test_director_sms_log_scoped(env, monkeypatch):
    """DIRECTOR faqat o'z korxonasi route'idagi SMS jurnalini ko'radi."""
    from bm_automation.app.notifications import sms_notify as sn
    rows = [
        {"id": 1, "route_id": "r2", "route_name": "20-yo'nalish",
         "phone": "1", "status": "DELIVERED", "name": "A", "message": "m"},
        {"id": 2, "route_id": "r1", "route_name": "10-yo'nalish",
         "phone": "2", "status": "DELIVERED", "name": "B", "message": "m"},
    ]
    monkeypatch.setattr(sn, "sms_log", lambda *a, **k: {
        "rows": rows, "count": 2,
        "counts": {"DELIVERED": 2, "total": 2}, "configured": True})
    st = env
    st.dashboard_user_add("dir", *deps.hash_password("to'g'ri"),
                          role="DIRECTOR", company="XTEST")
    _, _, cookies = _login(env, "dir", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/sms-log", headers=hdr)
    assert status == 200 and data["ok"] is True
    assert [r["id"] for r in data["rows"]] == [2]
    assert data["count"] == 1
    assert data["counts"]["total"] == 1
    # Admin esa hammasini ko'radi.
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/sms-log", headers=hdr)
    assert status == 200
    assert len(data["rows"]) == 2
    assert data["count"] == 2


def test_director_sms_routes_scoped(env):
    """DIRECTOR SMS yo'nalishlar ro'yxatida faqat o'z korxonasini ko'radi."""
    st = env
    st.dashboard_user_add("dir", *deps.hash_password("to'g'ri"),
                          role="DIRECTOR", company="XTEST")
    _, _, cookies = _login(env, "dir", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/sms/routes", headers=hdr)
    assert status == 200 and data["ok"] is True
    ids = [r["route_id"] for r in data["routes"]]
    assert ids == ["r1"]


class _FakeDocMgr:
    def __init__(self, docs):
        self._docs = docs

    def list_documents(self, category="", status="", search="", driver_id=""):
        return [dict(d) for d in self._docs]

    def get_document(self, doc_id):
        return next((dict(d) for d in self._docs if d["id"] == doc_id), None)


def test_director_documents_scoped_by_driver_route(env, monkeypatch):
    """DIRECTOR faqat o'z route'idagi haydovchilar xujjatlarini ko'radi."""
    from bm_automation.app.documents import manager as mgr_mod
    docs = [
        {"id": 1, "title": "Shartnoma-1", "driver_id": "d1"},
        {"id": 2, "title": "Shartnoma-2", "driver_id": "d2"},
    ]
    monkeypatch.setattr(mgr_mod, "get_manager",
                        lambda: _FakeDocMgr(docs))
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    st.dashboard_user_add("dir", *deps.hash_password("to'g'ri"),
                          role="DIRECTOR", company="XTEST")
    _, _, cookies = _login(env, "dir", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/documents", headers=hdr)
    assert status == 200 and data["ok"] is True
    assert [d["id"] for d in data["documents"]] == [1]
    # Tafsilot: o'z route'idagi xujjat ochiladi, boshqasi 404 beradi.
    status, _, _ = _request(env, "/api/documents/1", headers=hdr)
    assert status == 200
    status, _, _ = _request(env, "/api/documents/2", headers=hdr)
    assert status == 404
    # Admin ikkalasini ham ko'radi.
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/documents", headers=hdr)
    assert status == 200
    assert [d["id"] for d in data["documents"]] == [1, 2]


# ------------------------------------------- haydovchi/avtobus PII qamrovi

def test_driver_detail_scoped_to_route(env, monkeypatch):
    """/api/drivers/{id} (haydovchi karta — moliyaviy) DISPATCHER uchun 403."""
    called = []

    def fake_detail(self, driver_id, f=None):
        called.append(driver_id)
        return {"ok": True, "driver_id": driver_id, "driver": {}}

    monkeypatch.setattr(
        "bm_automation.app.dashboard.metrics.Metrics.driver_detail", fake_detail)
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/drivers/d1", headers=hdr)
    assert status == 403
    assert called == []
    # MANAGER o'z korxonasi haydovchisini ko'radi, boshqasiniki 404.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/drivers/d1", headers=hdr)
    assert status == 200
    assert called == ["d1"]
    status, _, _ = _request(env, "/api/drivers/d2", headers=hdr)
    assert status == 404
    assert called == ["d1"]
    # Admin ikkalasini ham ocha oladi.
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/drivers/d2", headers=hdr)
    assert status == 200
    assert called == ["d1", "d2"]


def test_drivers_list_scoped(env):
    """/api/drivers/list faqat o'z route'i haydovchilarini qaytaradi (MANAGER)."""
    st = env
    st.save_driver("d1", full_name="AAA", route_id="r1")
    st.save_driver("d2", full_name="BBB", route_id="r2")
    # DISPATCHER moliyaviy bo'limga kira olmaydi.
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, "/api/drivers/list", headers=hdr)
    assert status == 403
    # MANAGER o'z route'i ro'yxatini oladi.
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/drivers/list", headers=hdr)
    assert status == 200 and data["ok"] is True
    assert [d["driver_id"] for d in data["drivers"]] == ["d1"]
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/drivers/list", headers=hdr)
    assert {d["driver_id"] for d in data["drivers"]} == {"d1", "d2"}


def test_driver_write_scoped_to_route(env):
    """/api/drivers/{id} POST (profil/rasm) boshqa route uchun 404."""
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}",
           "Content-Type": "application/json"}
    body = json.dumps({"phone": "+998"}).encode()
    status, _, _ = _request(env, "/api/drivers/d2/profile", method="POST",
                            headers={**hdr, "Content-Length": str(len(body))},
                            body=body)
    assert status == 404


def test_vehicle_detail_scoped(env, monkeypatch):
    """/api/vehicles/{id} boshqa route avtobusi uchun 404 beradi."""
    from bm_automation.app.dashboard import metrics as metrics_mod
    monkeypatch.setattr(metrics_mod.Metrics, "vehicle_detail",
                        lambda self, vid, f: {"ok": True, "id": vid})
    st = env
    st.save_vehicle("v1", plate_number="01A", route_id="r1")
    st.save_vehicle("v2", plate_number="02B", route_id="r2")
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/vehicles/v1", headers=hdr)
    assert status == 200 and data["ok"] is True
    status, _, _ = _request(env, "/api/vehicles/v2", headers=hdr)
    assert status == 404


def test_appeals_scoped(env, monkeypatch):
    """/api/appeals ro'yxat va o'chirish o'z route'i bilan cheklanadi."""
    from bm_automation.app.dashboard import metrics as metrics_mod
    monkeypatch.setattr(metrics_mod.Metrics, "appeals_report", lambda self, f: {
        "filters": dict(f or {}),
        "appeals": [
            {"id": 1, "driver_id": "d1", "driver_name": "Ali",
             "title": "T1", "text": "", "status": "YANGI", "reply": "",
             "replied_at": "", "created_at": "", "updated_at": ""},
            {"id": 2, "driver_id": "d2", "driver_name": "Vali",
             "title": "T2", "text": "", "status": "YANGI", "reply": "",
             "replied_at": "", "created_at": "", "updated_at": ""},
        ],
        "total": 2,
    })
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    aid1 = st.appeal_add("d1", title="T1")
    aid2 = st.appeal_add("d2", title="T2")
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/appeals", headers=hdr)
    assert status == 200
    assert [a["id"] for a in data["appeals"]] == [1]
    # Boshqa route murojaatini o'chirish mumkin emas.
    body = json.dumps({"action": "delete", "id": aid2}).encode()
    status, data, _ = _request(env, "/api/appeals", method="POST",
                               headers={**hdr,
                                        "Content-Length": str(len(body)),
                                        "Content-Type": "application/json"},
                               body=body)
    assert status == 404 and data.get("ok") is False
    # O'z route'idagini o'chirish mumkin.
    body = json.dumps({"action": "delete", "id": aid1}).encode()
    status, data, _ = _request(env, "/api/appeals", method="POST",
                               headers={**hdr,
                                        "Content-Length": str(len(body)),
                                        "Content-Type": "application/json"},
                               body=body)
    assert status == 200 and data.get("ok") is True
    ids = [r["id"] for r in st.appeals_list()]
    assert ids == [aid2]


# ----------------------------------------------------- hisobotlar qamrovi

def test_salary_report_scoped_to_company(env, monkeypatch):
    """Ish haqi hisoboti faqat o'z korxonasi route'larini qamraydi."""
    from bm_automation.app.dashboard import export as export_mod
    calls = []

    def fake_export(from_date, to_date, route=""):
        calls.append((from_date, to_date, route))
        return b"{}"

    monkeypatch.setattr(export_mod, "salary_export", fake_export)
    st = env
    st.dashboard_user_add("mgr", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="XTEST")
    _, _, cookies = _login(env, "mgr", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    q = "/api/salary?from=2026-01-01&to=2026-01-31"
    status, _, _ = _request(env, q, headers=hdr)
    assert status == 200
    assert calls and calls[0][2] == "r1"
    # DISPATCHER salary'ga kira olmaydi (rang 2).
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    _, _, cookies = _login(env, "disp", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, q, headers=hdr)
    assert status == 403
    # ADMIN to'liq eksport qiladi (route bo'sh).
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, _, _ = _request(env, q, headers=hdr)
    assert status == 200
    assert calls[-1][2] == ""


def test_salary_export_filters_by_route(monkeypatch):
    """salary_export route parametri bilan faqat o'sha yo'nalishlarni oladi."""
    from io import BytesIO

    from bm_automation.app.dashboard import export as export_mod
    from openpyxl import load_workbook

    ALL = [
        {"driver_id": "d1", "name": "Ali", "route_id": "r1", "trips": 5,
         "working_days": 3, "km": 100, "km_rate": 10, "gross_pay": 1000,
         "tax": 120, "fines": 0, "net_pay": 880, "rating": 5,
         "attendance": 100},
        {"driver_id": "d2", "name": "Vali", "route_id": "r2", "trips": 4,
         "working_days": 2, "km": 90, "km_rate": 10, "gross_pay": 900,
         "tax": 108, "fines": 50, "net_pay": 742, "rating": 4,
         "attendance": 90},
    ]

    class FakeMet:
        def drivers(self, f):
            sel = f.get("route") or ""
            ids = {t for t in str(sel).replace(",", " ").split() if t}
            return [d for d in ALL if not ids or d["route_id"] in ids]

        def _companies(self):
            return {}

        def _route_names(self):
            return {}

    monkeypatch.setattr(export_mod.m, "Metrics", FakeMet)
    buf = BytesIO(export_mod.salary_export("2026-01-01", "2026-01-31",
                                           route="r1"))
    assert set(load_workbook(buf).sheetnames) == {"Xulosa", "Noma'lum — r1"}
    buf = BytesIO(export_mod.salary_export("2026-01-01", "2026-01-31"))
    assert set(load_workbook(buf).sheetnames) == {
        "Xulosa", "Noma'lum — r1", "Noma'lum — r2"}


# ------------------------------------------- xavfsizlik yopilishi (2026)
#
# Ko'p korxonali rejimda ochiq qolgan yozish/o'qish teshiklari:
#   1) /api/kmrate, /api/route-skm - endi rol (MANAGER=2) + route qamrovi.
#   2) /api/electricity/price - global sozlama -> faqat ADMIN.
#   3) /api/export?scope=settings|tariffs - global/butun-firma -> faqat ADMIN.
#   4) xujjatlar: autofill/html/create/attach/detach - PII qamrovi.
#   5) haydovchi yozish (yangi qayd + ko'chirish) route qamrovi.
#   6) qamrovsiz kompaniya -> fail-closed (hech narsa ko'rinmaydi).


def _hdr(env, role, company="XTEST", password="to'g'ri"):
    """Berilgan rol/kompaniya foydalanuvchisini yaratib sessiya header qaytaradi."""
    st = env
    st.dashboard_user_add(f"u-{role}-{company}", *deps.hash_password(password),
                          role=role, company=company)
    _, _, cookies = _login(env, f"u-{role}-{company}", password)
    return {"Cookie": f"bm_session={cookies['bm_session']}"}


def test_kmrate_route_scope_and_role(env, monkeypatch):
    """kmrate: boshqa korxona route'i 404; o'z route'i 200; DISPATCHER 403."""
    calls = []

    def fake(route_id, km_rate):
        calls.append(route_id)

    monkeypatch.setattr(
        "bm_automation.app.core.bot_settings.set_route_km", fake)
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"route_id": "r2", "km_rate": 10}).encode()
    status, _, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                            body=body)
    assert status == 404
    assert calls == []
    body = json.dumps({"route_id": "r1", "km_rate": 10}).encode()
    status, data, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                               body=body)
    assert status == 200 and data.get("ok") is True
    assert calls == ["r1"]
    # DISPATCHER rol bo'yicha 403 (rang 2 talab qilinadi).
    hdr = _hdr(env, "DISPATCHER")
    body = json.dumps({"route_id": "r1", "km_rate": 10}).encode()
    status, _, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                            body=body)
    assert status == 403
    # ADMIN istalgan route'ni yozadi.
    hdr = _hdr(env, "ADMIN", company="")
    body = json.dumps({"route_id": "r2", "km_rate": 10}).encode()
    status, _, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                            body=body)
    assert status == 200
    assert calls == ["r1", "r2"]


def test_route_skm_route_scope(env, monkeypatch):
    """route-skm: boshqa korxona route'i 404, o'z route'i 200."""
    calls = []

    def fake(rid, val):
        calls.append(rid)
        return val

    monkeypatch.setattr(
        "bm_automation.app.core.bot_settings.set_route_skm", fake)
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"route_id": "r2", "skm": 5}).encode()
    status, _, _ = _request(env, "/api/route-skm", method="POST", headers=hdr,
                            body=body)
    assert status == 404 and calls == []
    body = json.dumps({"route_id": "r1", "skm": 5}).encode()
    status, _, _ = _request(env, "/api/route-skm", method="POST", headers=hdr,
                            body=body)
    assert status == 200 and calls == ["r1"]


def test_rejects_tariff_route_scope(env, monkeypatch):
    """rejects/tariff: boshqa korxona route'i 404, o'z route'i 200."""
    calls = []

    def fake(rid, no_vat, vat):
        calls.append(rid)
        return {"no_vat": no_vat, "vat": vat}

    monkeypatch.setattr(
        "bm_automation.app.core.bot_settings.set_route_tariff", fake)
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"route_id": "r2", "vat": 1, "no_vat": 1}).encode()
    status, _, _ = _request(env, "/api/rejects/tariff", method="POST",
                            headers=hdr, body=body)
    assert status == 404 and calls == []
    body = json.dumps({"route_id": "r1", "vat": 1, "no_vat": 1}).encode()
    status, _, _ = _request(env, "/api/rejects/tariff", method="POST",
                            headers=hdr, body=body)
    assert status == 200 and calls == ["r1"]


def test_sms_routes_route_scope(env, monkeypatch):
    """sms/routes: boshqa korxona route'i uchun yoqish 404 beradi."""
    calls = []

    def fake(route_id="", enabled=None):
        calls.append((route_id, enabled))
        return {}

    monkeypatch.setattr(
        "bm_automation.app.notifications.sms_notify.sms_route_set", fake)
    hdr = _hdr(env, "DIRECTOR")
    body = json.dumps({"route_id": "r2", "enabled": True}).encode()
    status, _, _ = _request(env, "/api/sms/routes", method="POST", headers=hdr,
                            body=body)
    assert status == 404 and calls == []
    body = json.dumps({"route_id": "r1", "enabled": True}).encode()
    status, _, _ = _request(env, "/api/sms/routes", method="POST", headers=hdr,
                            body=body)
    assert status == 200 and calls == [("r1", True)]


def test_sms_send_route_scope(env, monkeypatch):
    """Qo'lda SMS: non-admin o'z route'iga bog'lanadi, begona route 404."""
    from bm_automation.app.notifications import sms_notify as sn
    calls = []

    def fake(**kw):
        calls.append(kw)
        return {"status": "SENT", "logged": "inserted"}

    monkeypatch.setattr(sn, "send_and_log", fake)
    hdr = _hdr(env, "DIRECTOR")
    body = json.dumps({"phone": "+998901234567", "text": "Salom",
                       "route_id": "r2"}).encode()
    status, _, _ = _request(env, "/api/sms/send", method="POST", headers=hdr,
                            body=body)
    assert status == 404 and calls == []
    # Bitta yo'nalishli kompaniyada route avtomatik tanlanadi va jurnalga yoziladi.
    body = json.dumps({"phone": "+998901234567", "text": "Salom"}).encode()
    status, data, _ = _request(env, "/api/sms/send", method="POST", headers=hdr,
                               body=body)
    assert status == 200 and data["route_id"] == "r1"
    assert calls[-1]["route_id"] == "r1"
    assert calls[-1]["route_name"] == "10-yo'nalish"
    # ADMIN uchun yo'nalishsiz (umumiy) yuborish mumkin.
    ahdr = _hdr(env, "ADMIN")
    status, data, _ = _request(env, "/api/sms/send", method="POST", headers=ahdr,
                               body=body)
    assert status == 200 and data["route_id"] == ""
    assert calls[-1]["route_id"] == ""
    body = json.dumps({"phone": "+998901234567", "text": "Salom",
                       "route_id": "r2"}).encode()
    status, data, _ = _request(env, "/api/sms/send", method="POST", headers=ahdr,
                               body=body)
    assert status == 200 and data["route_id"] == "r2"
    assert calls[-1]["route_name"] == "20-yo'nalish"


def test_sms_send_requires_route_when_multiple(env, monkeypatch):
    """Ikki yo'nalishli kompaniyada route tanlanmasa 400 — SMS yuborilmaydi."""
    from bm_automation.app.notifications import sms_notify as sn
    calls = []
    monkeypatch.setattr(sn, "send_and_log",
                        lambda **kw: calls.append(kw) or {"status": "SENT"})
    data = profiles_mod.load_profiles()
    data["profiles"].append({"name": "XTEST", "routeVariantId": "r2",
                             "routeName": "20-yo'nalish", "profileId": "",
                             "start1": "", "start2": ""})
    profiles_mod.save_profiles(data)
    hdr = _hdr(env, "DIRECTOR")
    body = json.dumps({"phone": "+998901234567", "text": "Salom"}).encode()
    status, _, _ = _request(env, "/api/sms/send", method="POST", headers=hdr,
                            body=body)
    assert status == 400 and calls == []
    body = json.dumps({"phone": "+998901234567", "text": "Salom",
                       "route_id": "r1"}).encode()
    status, _, _ = _request(env, "/api/sms/send", method="POST", headers=hdr,
                            body=body)
    assert status == 200 and calls[-1]["route_id"] == "r1"


def test_electricity_price_manager_blocked(env):
    """electricity/price global sozlama - faqat ADMIN o'zgartiradi."""
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"rate": 1100}).encode()
    status, _, _ = _request(env, "/api/electricity/price", method="POST",
                            headers=hdr, body=body)
    assert status == 403
    hdr = _hdr(env, "ADMIN", company="")
    status, data, _ = _request(env, "/api/electricity/price", method="POST",
                               headers=hdr, body=body)
    assert status == 200 and data.get("ok") is True


def test_export_settings_tariffs_admin_only(env, monkeypatch):
    """Global sozlamalar/barcha firma tariflari eksporti - faqat ADMIN."""
    calls = []

    def fake_export(params, allow_financial=True):
        calls.append(params)
        return None

    monkeypatch.setattr(
        "bm_automation.app.dashboard.export.run_export", fake_export)
    hdr = _hdr(env, "DIRECTOR")
    for scope in ("settings", "tariffs"):
        status, data, _ = _request(env, f"/api/export?scope={scope}",
                                   headers=hdr)
        assert status == 403
        assert "ruxsat" in data.get("error", "")
    # Qolgan eksport turlari o'z korxonasiga kesiladi va AKT'ga o'tadi.
    status, _, _ = _request(env, "/api/export?scope=trips", headers=hdr)
    assert status != 403
    assert calls and calls[0].get("route") == "r1"
    # ADMIN settings/tariffsni ham ocha oladi (AKT'ga o'tadi, 403 emas).
    hdr = _hdr(env, "ADMIN", company="")
    before = len(calls)
    for scope in ("settings", "tariffs"):
        status, _, _ = _request(env, f"/api/export?scope={scope}", headers=hdr)
        assert status != 403
    assert len(calls) == before + 2


def test_document_autofill_scoped(env, monkeypatch):
    """Autofill boshqa kompaniya haydovchisi uchun 404 (PII himoyasi)."""
    from bm_automation.app.documents import manager as mgr_mod
    calls = []

    class _AutofillMgr:
        def autofill_data(self, driver_id):
            calls.append(driver_id)
            return {"ok": True, "driver_id": driver_id}

    monkeypatch.setattr(mgr_mod, "get_manager", lambda: _AutofillMgr())
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    hdr = _hdr(env, "DIRECTOR")
    status, data, _ = _request(env, "/api/documents/autofill?driver_id=d1",
                               headers=hdr)
    assert status == 200 and data.get("ok") is True
    assert calls == ["d1"]
    status, _, _ = _request(env, "/api/documents/autofill?driver_id=d2",
                            headers=hdr)
    assert status == 404
    assert calls == ["d1"]


def test_document_html_scoped(env, monkeypatch):
    """/api/documents/{id}/html boshqa kompaniya xujjati uchun 404."""
    from bm_automation.app.documents import manager as mgr_mod
    docs = [
        {"id": 1, "title": "T1", "driver_id": "d1"},
        {"id": 2, "title": "T2", "driver_id": "d2"},
    ]

    class _HtmlMgr(_FakeDocMgr):
        def get_html(self, doc_id):
            return "OK"

    monkeypatch.setattr(mgr_mod, "get_manager", lambda: _HtmlMgr(docs))
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    hdr = _hdr(env, "DIRECTOR")
    status, raw = _raw(env, "/api/documents/1/html", headers=hdr)
    assert status == 200
    assert b"OK" in raw
    status, _ = _raw(env, "/api/documents/2/html", headers=hdr)
    assert status == 404


def test_document_create_and_attach_scoped(env, monkeypatch):
    """Xujjat yaratish/attach boshqa kompaniya haydovchisi uchun 404."""
    from bm_automation.app.documents import manager as mgr_mod
    docs = []

    class _FullMgr(_FakeDocMgr):
        def get_document(self, doc_id):
            return None if not docs else _FakeDocMgr(docs).get_document(doc_id)

        def create_document(self, **kw):
            docs.append({"id": 1, "driver_id": kw.get("driver_id", "")})
            return {"ok": True, "id": 1, "driver_id": kw.get("driver_id", "")}

        def attach_file(self, doc_id, file_path):
            return {"ok": True}

        def detach_file(self, doc_id, file_path):
            return {"ok": True}

    monkeypatch.setattr(mgr_mod, "get_manager", lambda: _FullMgr(docs))
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    hdr = _hdr(env, "DIRECTOR")
    # O'z korxonasi haydovchisiga yaratish mumkin.
    body = json.dumps({"template_key": "shartnoma", "driver_id": "d1"}).encode()
    status, data, _ = _request(env, "/api/documents", method="POST",
                               headers=hdr, body=body)
    assert status == 200 and data.get("ok") is True
    # Boshqa korxona haydovchisiga yaratish 404.
    body = json.dumps({"template_key": "shartnoma", "driver_id": "d2"}).encode()
    status, _, _ = _request(env, "/api/documents", method="POST",
                            headers=hdr, body=body)
    assert status == 404
    assert len(docs) == 1


def test_document_attach_detach_scoped(env, monkeypatch):
    """attach/detach boshqa kompaniya xujjati uchun 404."""
    from bm_automation.app.documents import manager as mgr_mod
    docs = [
        {"id": 1, "title": "T1", "driver_id": "d1"},
        {"id": 2, "title": "T2", "driver_id": "d2"},
    ]

    class _Mgr(_FakeDocMgr):
        def attach_file(self, doc_id, file_path):
            return {"ok": True}

        def detach_file(self, doc_id, file_path):
            return {"ok": True}

    monkeypatch.setattr(mgr_mod, "get_manager", lambda: _Mgr(docs))
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.save_driver("d2", full_name="Vali", route_id="r2")
    hdr = _hdr(env, "DIRECTOR")
    body = json.dumps({"file_path": "photo.jpg"}).encode()
    status, _, _ = _request(env, "/api/documents/2/attach", method="POST",
                            headers=hdr, body=body)
    assert status == 404
    status, _, _ = _request(env, "/api/documents/1/attach", method="POST",
                            headers=hdr, body=body)
    assert status == 200
    status, _, _ = _request(env, "/api/documents/2/detach", method="POST",
                            headers=hdr, body=body)
    assert status == 404
    status, _, _ = _request(env, "/api/documents/1/detach", method="POST",
                            headers=hdr, body=body)
    assert status == 200


def test_driver_create_route_scope(env, monkeypatch):
    """Yangi haydovchi faqat o'z korxonasi route'iga qo'shiladi."""
    from bm_automation.app.dashboard.routers import drivers as drivers_mod
    calls = []

    def fake(payload, driver_id="", scope=None):
        calls.append(payload.get("route_id"))
        return {"ok": True, "driver_id": "new1"}

    monkeypatch.setattr(drivers_mod, "_save_driver", fake)
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"full_name": "Yangi", "route_id": "r2"}).encode()
    status, _, _ = _request(env, "/api/drivers", method="POST", headers=hdr,
                            body=body)
    assert status == 404 and calls == []
    body = json.dumps({"full_name": "Yangi", "route_id": "r1"}).encode()
    status, data, _ = _request(env, "/api/drivers", method="POST", headers=hdr,
                               body=body)
    assert status == 200 and calls == ["r1"]
    # Route'siz ham qo'shib bo'lmaydi (fail-closed).
    body = json.dumps({"full_name": "Yangi"}).encode()
    status, _, _ = _request(env, "/api/drivers", method="POST", headers=hdr,
                            body=body)
    assert status == 404 and calls == ["r1"]


def test_driver_profile_cannot_move_company(env, monkeypatch):
    """Haydovchini boshqa korxona route'iga ko'chirish 404 beradi."""
    from bm_automation.app.dashboard.routers import drivers as drivers_mod
    calls = []

    def fake(payload, driver_id="", scope=None):
        calls.append((driver_id, payload.get("route_id")))
        return {"ok": True}

    monkeypatch.setattr(drivers_mod, "_save_driver", fake)
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    hdr = _hdr(env, "MANAGER")
    body = json.dumps({"route_id": "r2"}).encode()
    status, _, _ = _request(env, "/api/drivers/d1/profile", method="POST",
                            headers=hdr, body=body)
    assert status == 404 and calls == []
    body = json.dumps({"route_id": "r1"}).encode()
    status, _, _ = _request(env, "/api/drivers/d1/profile", method="POST",
                            headers=hdr, body=body)
    assert status == 200 and calls == [("d1", "r1")]


def test_driver_detail_forced_company_route(env):
    """Detail so'rovidagi route parametri o'z korxonasi bilan chegaralanadi."""
    from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus

    st = env
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="r2", name="20-yo'nalish", entity_type="ROUTE")
    st.save_vehicle(external_id="v1", plate_number="01A111AA", route_id="r1")
    st.save_vehicle(external_id="v2", plate_number="02B222BB", route_id="r2")
    st.save_driver("d1", full_name="Ali", route_id="r1")
    for rid, vid, pt in (("r1", "v1", "08:00"), ("r2", "v2", "09:00")):
        st.save_trip(TripRecord(
            date="2026-08-10", route_id=rid, vehicle_id=vid, driver_id="d1",
            planned_time=pt, actual_time=pt, status=TripStatus.ACCEPTED.value,
            source=SyncSource.DUTY.value))
    st.save_driver_work_log("2026-08-10", "d1", "v1", 10.0, 1, note="O'z")
    st.save_driver_work_log("2026-08-10", "d1", "v2", 99.0, 1, note="Boshqa")

    hdr = _hdr(env, "MANAGER")
    status, data, _ = _request(env, "/api/drivers/d1?route=r2", headers=hdr)
    assert status == 200
    assert all(t.get("route_id") != "r2" for t in data["trips"])
    assert all(w.get("vehicle_id") != "v2" for w in data["work_logs"])
    status, _, _ = _request(env, "/api/vehicles/v2", headers=hdr)
    assert status == 404


def test_unknown_company_fail_closed(env, monkeypatch):
    """Noma'lum kompaniya akkaunti kira olmaydi; eski sessiya ham 403."""
    st = env
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.dashboard_user_add("nobody", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="MAVJUD_EMAS")
    status, data, cookies = _login(env, "nobody", "to'g'ri")
    assert status == 403 and data.get("ok") is False
    assert "bm_session" not in cookies
    # Kompaniya keyin o'chirilgan/yo'qolgan bo'lsa, avval yaratilgan
    # sessiya ham API'ga kira olmaydi (tenant_guard fail-closed).
    tok = deps.create_session({"username": "nobody", "id": 99,
                               "role": "MANAGER", "company": "MAVJUD_EMAS"})
    hdr = {"Cookie": f"bm_session={tok}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert status == 403 and data.get("ok") is False
    # Haydovchi tafsiloti ham ochilmaydi (tashqaridan driver_id ko'rinmaydi).
    calls = []

    def fake_detail(self, driver_id, f=None):
        calls.append(driver_id)
        return {"ok": True, "driver": {}}

    monkeypatch.setattr(
        "bm_automation.app.dashboard.metrics.Metrics.driver_detail", fake_detail)
    status, _, _ = _request(env, "/api/drivers/d1", headers=hdr)
    assert status == 403 and calls == []
    # Yozish ham yopiq.
    body = json.dumps({"route_id": "r1", "km_rate": 10}).encode()
    status, _, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                            body=body)
    assert status == 403


def test_known_company_without_routes_sees_nothing(env):
    """Mavjud, lekin yo'nalishsiz kompaniya 200 + bo'sh ro'yxat oladi."""
    st = env
    seed_companies(st, [{"name": "MAVJUD_EMAS"}])
    st.save_driver("d1", full_name="Ali", route_id="r1")
    st.dashboard_user_add("nobody", *deps.hash_password("to'g'ri"),
                          role="MANAGER", company="MAVJUD_EMAS")
    _, _, cookies = _login(env, "nobody", "to'g'ri")
    hdr = {"Cookie": f"bm_session={cookies['bm_session']}"}
    status, data, _ = _request(env, "/api/routes", headers=hdr)
    assert status == 200 and data["routes"] == []
    status, _, _ = _request(env, "/api/drivers/d1", headers=hdr)
    assert status == 404
    body = json.dumps({"route_id": "r1", "km_rate": 10}).encode()
    status, _, _ = _request(env, "/api/kmrate", method="POST", headers=hdr,
                            body=body)
    assert status == 404


# ------------------------------------------------------- xavfsizlik himoya

def test_login_bruteforce_lockout(env):
    """8 xato urinishdan keyin login 15 daqiqaga qulflanadi (username+IP)."""
    digest, salt = deps.hash_password("secret")
    env.dashboard_user_add("ali", digest, salt, "MANAGER", "XTEST", 1)
    for i in range(8):
        st, d, _ = _login(env, "ali", "wrong")
        assert st == 401, f"urinish {i} 401 bo'lishi kerak edi, {st}"
    st, d, _ = _login(env, "ali", "wrong")
    assert st == 429
    # Qulflangan davrda hatto to'g'ri parol ham qabul qilinmaydi.
    st, d, _ = _login(env, "ali", "secret")
    assert st == 429
    # Boshqa foydalanuvchi/IP qulflanmaydi (faqat username+IP juftligi).
    st, d, _ = _login(env, "nonexistent", "x")
    assert st == 401


def test_login_success_resets_fail_count(env):
    """Muvaffaqiyatli login xato sanog'ini tozalaydi."""
    digest, salt = deps.hash_password("secret")
    env.dashboard_user_add("ali", digest, salt, "MANAGER", "XTEST", 1)
    for _ in range(4):
        _login(env, "ali", "wrong")
    st, d, _ = _login(env, "ali", "secret")
    assert st == 200
    for _ in range(4):
        st, d, _ = _login(env, "ali", "wrong")
        assert st == 401


def test_username_special_chars_parameterized(env):
    """`'`/`;` li login parametrli lookup bilan xavfsiz ishlaydi.

    Frontend usernameni ``esc()`` bilan HTML'dan tozalaydi; server parametri
    SQL ishlatadi — in'ektsiya urinadigan username jadvalni buzishi mumkin.
    """
    name = "x'); DROP TABLE dashboard_users;--"
    digest, salt = deps.hash_password("secret")
    uid = env.dashboard_user_add(name, digest, salt, "MANAGER", "XTEST", 1)
    assert uid
    st, d, _ = _login(env, name, "secret")
    assert st == 200
    # Jadvalan faqat bitta qator — boshqalarga ziyon yetmadi.
    assert env.dashboard_users_count() == 1
    rows = env.dashboard_users_list()
    assert len(rows) == 1 and rows[0]["username"] == name.lower()


# ------------------------------------------------ oylik summa toggle

def _admin_env(env):
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                           role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    return {"Cookie": f"bm_session={cookies['bm_session']}",
            "Content-Type": "application/json"}


def test_show_driver_salary_default_off(env):
    """Standart holatda botda oylik ko'rsatilmaydi (dashboardda doim ko'rinadi)."""
    hdr = _admin_env(env)
    status, data, _ = _request(env, "/api/settings", headers=hdr)
    assert status == 200
    assert data["global"]["show_driver_salary"] is False


def test_show_driver_salary_toggle_persists(env):
    """Sozlama saqlanadi va qayta o'qilganda saqlanib qoladi."""
    hdr = _admin_env(env)
    body = json.dumps({"show_driver_salary": False}).encode()
    status, data, _ = _request(env, "/api/settings", method="POST",
                               headers={**hdr, "Content-Length": str(len(body))},
                               body=body)
    assert status == 200 and data["ok"] is True
    assert "show_driver_salary" in data["changed"]
    status, data, _ = _request(env, "/api/settings", headers=hdr)
    assert data["global"]["show_driver_salary"] is False
    # Qayta yoqiladi.
    body = json.dumps({"show_driver_salary": True}).encode()
    status, _, _ = _request(env, "/api/settings", method="POST",
                            headers={**hdr, "Content-Length": str(len(body))},
                            body=body)
    assert status == 200
    status, data, _ = _request(env, "/api/settings", headers=hdr)
    assert data["global"]["show_driver_salary"] is True


def test_drivers_directory_shows_salary_when_bot_flag_off(env, monkeypatch):
    """Bot sozlamasi o'chik bo'lsa ham dashboardda summalar ko'rinadi."""
    import bm_automation.app.dashboard.metrics as mmod

    rows = [
        {"driver_id": "d1", "name": "Ali", "phone": "+998901234567",
         "company": "XTEST", "route_name": "10-yo'nalish",
         "km": 2500.0, "km_rate": 2000.0, "working_days": 20, "trips": 40,
         "gross_pay": 5_000_000.0, "tax": 600_000.0, "fines": 50_000.0,
         "net_pay": 4_350_000.0, "rating": 4.5, "blacklisted": False,
         "automatic_km": 0.0, "manual_trips": 0,
         "notification_enabled": False},
    ]
    canned = {"filters": {"month": "2026-08"}, "drivers": rows,
              "totals": {"drivers": 1, "active": 1, "blacklisted": 0,
                         "km": 2500.0, "gross_pay": 5_000_000.0,
                         "tax": 600_000.0, "net_pay": 4_350_000.0,
                         "fines": 50_000.0}}
    monkeypatch.setattr(mmod.Metrics, "driver_directory",
                        lambda self, f=None: copy.deepcopy(canned))

    hdr = _admin_env(env)
    body = json.dumps({"show_driver_salary": False}).encode()
    _request(env, "/api/settings", method="POST",
             headers={**hdr, "Content-Length": str(len(body))}, body=body)
    status, data, _ = _request(env, "/api/drivers", headers=hdr)
    assert status == 200
    dr = data["drivers"][0]
    assert dr["net_pay"] == 4_350_000.0
    assert dr["gross_pay"] == 5_000_000.0
    assert dr["tax"] == 600_000.0 and dr["fines"] == 50_000.0
    assert dr["km_rate"] == 2000.0
    assert data["totals"]["net_pay"] == 4_350_000.0
    assert data["totals"]["km"] == 2500.0


def test_driver_detail_shows_salary_when_bot_flag_off(env, monkeypatch):
    import bm_automation.app.dashboard.metrics as mmod

    detail = {
        "driver": {"driver_id": "d1", "name": "Ali", "km": 2500.0,
                   "km_rate": 2000.0, "working_days": 20, "trips": 40,
                   "gross_pay": 5_000_000.0, "tax": 600_000.0,
                   "fines": 50_000.0, "net_pay": 4_350_000.0},
        "profile": {}, "trips": [], "work_logs": [], "fine_rows": [],
        "appeals": [], "fines_total": 50_000.0, "telegram_user": None,
    }
    monkeypatch.setattr(mmod.Metrics, "driver_detail",
                        lambda self, driver_id, f=None: copy.deepcopy(detail))
    hdr = _admin_env(env)
    body = json.dumps({"show_driver_salary": False}).encode()
    _request(env, "/api/settings", method="POST",
             headers={**hdr, "Content-Length": str(len(body))}, body=body)
    status, data, _ = _request(env, "/api/drivers/d1", headers=hdr)
    assert status == 200
    d = data["driver"]
    assert d["net_pay"] == 4_350_000.0
    assert d["gross_pay"] == 5_000_000.0
    assert d["tax"] == 600_000.0 and d["fines"] == 50_000.0
    assert d["km_rate"] == 2000.0
    assert data["fines_total"] == 50_000.0


def test_user_photo_cached_redirect(env, monkeypatch):
    """Rasm endpoint Telegram'ni bir marta chaqiradi (TTL kesh) va redirect qaytaradi.

    Bu endpoint sinxron (threadpool) bo'lishi shart — aks holda sekin Telegram
    chaqiruvi dashboard event loop'ini bloklab qo'yadi (Batafsil osilishi).
    """
    from bm_automation.app.core import bot_users
    from bm_automation.app.dashboard.routers import admin as admin_mod
    from bm_automation.app.notifications import telegram as tg_mod

    calls = []
    monkeypatch.setattr(bot_users, "get_user",
                        lambda uid: {"chat_id": uid, "photo_file_id": "F1"})
    monkeypatch.setattr(
        tg_mod, "get_profile_photo_url",
        lambda chat_id, photo_file_id="": calls.append(chat_id) or "https://t.me/p.jpg")
    admin_mod._PHOTO_CACHE.clear()

    import inspect
    assert not inspect.iscoroutinefunction(admin_mod.get_user_photo)

    hdr = _hdr(env, "ADMIN")
    hdrs, cookies = _split_headers(hdr)
    for _ in range(2):
        r = _client().request("GET", "/api/users/555/photo", headers=hdrs,
                              cookies=cookies, follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "https://t.me/p.jpg"
    assert calls == [555]

# ------------------------------------------------ per-user bo'limlar (pages)
#
# Har bir dashboard foydalanuvchisiga qaysi bo'limlar ishlashini aniq belgilash
# mumkin: `dashboard_users.data` JSON'idagi `pages` ro'yxati. Ro'yxat bo'lmasa
# (None) — rol bo'yicha standart (eski xatti-harakat). Ro'yxat — roldan
# KENGAYTIRA olmaydi, faqat qisqartiradi; ADMIN uchun qo'llanmaydi.

def _pages_user(env, username, role, pages, company="XTEST", password="to'g'ri"):
    """Foydalanuvchi yaratib, unga bo'limlar ro'yxatini yozadi."""
    st = env
    uid = st.dashboard_user_add(username, *deps.hash_password(password),
                                role=role, company=company)
    st.dashboard_user_update(
        uid, data=json.dumps({"pages": pages}) if pages is not None else "{}")
    return uid


def _login_hdr(env, username, password="to'g'ri"):
    _, _, cookies = _login(env, username, password)
    return {"Cookie": f"bm_session={cookies['bm_session']}"}


def _json_hdr(env, username, password="to'g'ri"):
    return {**_login_hdr(env, username, password),
            "Content-Type": "application/json"}


def _post_users(env, hdr, payload):
    body = json.dumps(payload).encode()
    return _request(env, "/api/dashboard-users", method="POST",
                    headers={**hdr, "Content-Length": str(len(body))},
                    body=body)


def test_admin_creates_user_with_pages(env):
    """CREATE'da bo'limlar saqlanadi va ro'yxatda qaytadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    hdr = _json_hdr(env, "root")
    status, data, _ = _post_users(env, hdr, {
        "action": "create", "username": "ali", "role": "MANAGER",
        "company": "XTEST", "password": "parol1234", "pages": ["drivers", "video"]})
    assert status == 200 and data.get("ok") is True
    row = st.dashboard_user_get(username="ali")
    assert json.loads(row["data"])["pages"] == ["drivers", "video"]
    status, lst, _ = _request(env, "/api/dashboard-users",
                              headers=_login_hdr(env, "root"))
    users = {u["username"]: u for u in lst["users"]}
    assert users["ali"]["pages"] == ["drivers", "video"]
    assert users["root"]["pages"] is None            # ADMIN — cheklovsiz


def test_me_returns_effective_pages(env):
    """Sessiya egasi /api/me'da o'z bo'limlarini ko'radi (None = standart)."""
    _pages_user(env, "ali", "MANAGER", ["drivers", "alerts"])
    _pages_user(env, "vali", "MANAGER", None)
    _, me1, _ = _request(env, "/api/me", headers=_login_hdr(env, "ali"))
    assert me1["pages"] == ["drivers", "alerts"]
    assert "overview" in (me1.get("pages_all") or [])
    _, me2, _ = _request(env, "/api/me", headers=_login_hdr(env, "vali"))
    assert me2["pages"] is None


def test_pages_block_section_api(env):
    """Ro'yxatda yo'q bo'lim API'si 403 (menyu yashirish bilan cheklanmaydi)."""
    _pages_user(env, "ali", "MANAGER", ["drivers"])
    hdr = _login_hdr(env, "ali")
    status, data, _ = _request(env, "/api/electricity", headers=hdr)
    assert status == 403
    assert "ruxsat" in str(data.get("error") or "").lower()
    status, _, _ = _request(env, "/api/drivers", headers=hdr)
    assert status != 403


def test_pages_allow_listed_section_api(env):
    """Ro'yxatda bor bo'lim ochiq, boshqasi yopiq (ikkala tomon ham)."""
    _pages_user(env, "ali", "MANAGER", ["electricity"])
    hdr = _login_hdr(env, "ali")
    status, _, _ = _request(env, "/api/electricity", headers=hdr)
    assert status != 403
    status, _, _ = _request(env, "/api/drivers", headers=hdr)
    assert status == 403


def test_admin_role_ignores_pages(env):
    """ADMIN uchun per-user ro'yxat qo'llanmaydi (to'liq kirish)."""
    _pages_user(env, "boss", "ADMIN", ["overview"], company="")
    hdr = _login_hdr(env, "boss")
    _, me, _ = _request(env, "/api/me", headers=hdr)
    assert me["pages"] is None
    status, _, _ = _request(env, "/api/electricity", headers=hdr)
    assert status != 403


def test_super_user_ignores_pages(env, monkeypatch):
    """Super admin uchun ham cheklov qo'llanmaydi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"),
                          role="ADMIN", data='{"super": true}')
    st.dashboard_user_update(st.dashboard_user_get(username="root")["id"],
                             data=json.dumps({"super": True,
                                              "pages": ["overview"]}))
    hdr = _login_hdr(env, "root")
    _, me, _ = _request(env, "/api/me", headers=hdr)
    assert me["pages"] is None


def test_unknown_page_key_rejected(env):
    """Noma'lum bo'lim kaliti jimgina qabul qilinmaydi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    status, data, _ = _post_users(env, _json_hdr(env, "root"), {
        "action": "create", "username": "ali", "role": "MANAGER",
        "company": "XTEST", "password": "parol1234",
        "pages": ["drivers", "yoq-bolim"]})
    assert data.get("ok") is False
    assert "Noma'lum bo'lim" in str(data.get("error") or "")
    assert st.dashboard_user_get(username="ali") is None


def test_pages_null_clears_and_missing_key_preserves(env):
    """`pages: null` — cheklov olib tashlanadi; kalit yuborilmasa — saqlanadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    uid = _pages_user(env, "ali", "MANAGER", ["drivers"])
    hdr = _json_hdr(env, "root")
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "ali", "role": "MANAGER",
        "company": "XTEST"})
    assert status == 200 and data.get("ok") is True
    assert json.loads(st.dashboard_user_get(row_id=uid)["data"])["pages"] == [
        "drivers"]
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "ali", "pages": None})
    assert status == 200 and data.get("ok") is True
    assert "pages" not in json.loads(st.dashboard_user_get(row_id=uid)["data"])


def test_pages_update_keeps_other_data_keys(env):
    """`data` JSON'idagi boshqa kalitlar (masalan super bayrog'i) saqlanadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    uid = st.dashboard_user_add("ali", *deps.hash_password("to'g'ri"),
                                role="MANAGER", company="XTEST")
    st.dashboard_user_update(uid, data=json.dumps({"izoh": "test"}))
    status, data, _ = _post_users(env, _json_hdr(env, "root"), {
        "action": "update", "username": "ali", "pages": ["drivers"]})
    assert status == 200 and data.get("ok") is True
    saved = json.loads(st.dashboard_user_get(row_id=uid)["data"])
    assert saved["izoh"] == "test"
    assert saved["pages"] == ["drivers"]


# ------------------------------------------------ Telegram akkaunt bog'lash

def test_dashboard_user_telegram_link_round_trip(env):
    """Telegram chat_id CREATE'da saqlanadi, ro'yxatda va /api/me'da qaytadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    hdr = _json_hdr(env, "root")
    status, data, _ = _post_users(env, hdr, {
        "action": "create", "username": "ali", "role": "MANAGER",
        "company": "XTEST", "password": "parol1234",
        "pages": ["overview"], "telegram_chat_id": "123456"})
    assert status == 200 and data.get("ok") is True
    saved = json.loads(st.dashboard_user_get(username="ali")["data"])
    assert saved["telegram_chat_id"] == "123456"
    assert saved["pages"] == ["overview"]

    status, data, _ = _request(env, "/api/dashboard-users?action=list",
                               headers=_login_hdr(env, "root"))
    row = [u for u in data["users"] if u["username"] == "ali"][0]
    assert row["telegram_chat_id"] == "123456"

    # Foydalanuvchining o'zi ham bog'langan Telegram ID'ni ko'radi.
    _, me, _ = _request(env, "/api/me", headers=_login_hdr(env, "ali", "parol1234"))
    assert me.get("telegram_chat_id") == "123456"

    # Noto'g'ri ID rad etiladi, saqlangan qiymat o'zgarmaydi.
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "ali", "telegram_chat_id": "abc"})
    assert data.get("ok") is False
    saved = json.loads(st.dashboard_user_get(username="ali")["data"])
    assert saved["telegram_chat_id"] == "123456"

    # Bo'sh qiymat — bog'lanish olib tashlanadi.
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "ali", "telegram_chat_id": ""})
    assert status == 200 and data.get("ok") is True
    saved = json.loads(st.dashboard_user_get(username="ali")["data"])
    assert "telegram_chat_id" not in saved


def test_telegram_link_change_keeps_pages(env):
    """Faqat Telegram ID yangilanganda bo'limlar saqlanib qoladi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    hdr = _json_hdr(env, "root")
    st.dashboard_user_add("ali", *deps.hash_password("1234"),
                          role="MANAGER", company="XTEST",
                          data=json.dumps({"pages": ["drivers"]}))
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "ali", "telegram_chat_id": "777"})
    assert status == 200 and data.get("ok") is True
    saved = json.loads(st.dashboard_user_get(username="ali")["data"])
    assert saved["pages"] == ["drivers"]
    assert saved["telegram_chat_id"] == "777"


# ------------------------------------- bot foydalanuvchisi bo'limlari (API)

def _bot_user(monkeypatch, tmp_path, chat_id, pages=None):
    from bm_automation.app.core import bot_users
    monkeypatch.setattr(bot_users, "STATE_FILE", tmp_path / "bot_users.json")
    with bot_users._LOCK:
        bot_users._CACHE = {}
        bot_users._LOADED = False
    bot_users.record_user(chat_id, first_name=f"User{chat_id}")
    if pages is not None:
        bot_users.set_pages(chat_id, pages)
    return bot_users


def test_admin_sets_bot_user_pages(env, monkeypatch, tmp_path):
    """POST /api/users/{uid}/pages — bo'limlar belgilanadi va olib tashlanadi."""
    bu = _bot_user(monkeypatch, tmp_path, 556, ["overview"])
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    hdr = _json_hdr(env, "root")

    body = json.dumps({"pages": ["drivers", "video"]}).encode()
    status, data, _ = _request(
        env, "/api/users/556/pages", method="POST",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True
    assert bu.get_pages(556) == ["drivers", "video"]

    body = json.dumps({"pages": None}).encode()
    status, data, _ = _request(
        env, "/api/users/556/pages", method="POST",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True
    assert bu.get_pages(556) is None

    # Noma'lum bo'lim jimgina saqlanmaydi — xato qaytadi.
    body = json.dumps({"pages": ["yoq-bolim"]}).encode()
    status, data, _ = _request(
        env, "/api/users/556/pages", method="POST",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert data.get("ok") is False and data.get("error")
    assert bu.get_pages(556) is None


def test_bot_user_pages_requires_admin(env, monkeypatch, tmp_path):
    _bot_user(monkeypatch, tmp_path, 557)
    st = env
    st.dashboard_user_add("disp", *deps.hash_password("to'g'ri"),
                          role="DISPATCHER", company="XTEST")
    hdr = _json_hdr(env, "disp")
    body = json.dumps({"pages": ["overview"]}).encode()
    status, _, _ = _request(
        env, "/api/users/557/pages", method="POST",
        headers={**hdr, "Content-Length": str(len(body))}, body=body)
    assert status == 403


def test_users_list_exposes_pages(env, monkeypatch, tmp_path):
    _bot_user(monkeypatch, tmp_path, 558, ["overview", "routes"])
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    status, data, _ = _request(env, "/api/users",
                               headers=_login_hdr(env, "root"))
    assert status == 200
    row = [u for u in data["users"] if int(u["chat_id"]) == 558][0]
    assert row["pages"] == ["overview", "routes"]


def test_users_list_links_drivers_in_batch(env, monkeypatch, tmp_path):
    """/api/users haydovchini telegram va telefon bo'yicha bog'laydi (batch)."""
    from bm_automation.app.core import bot_users
    monkeypatch.setattr(bot_users, "STATE_FILE", tmp_path / "bot_users.json")
    with bot_users._LOCK:
        bot_users._CACHE = {}
        bot_users._LOADED = False
    bot_users.record_user(601, first_name="User601")
    bot_users.record_user(602, first_name="User602")
    with bot_users._LOCK:
        bot_users._CACHE["602"]["phone"] = "+998 93 733 45 60"
        bot_users._save()

    env.save_driver_profile("d1", telegram_chat_id="601")
    env.save_driver_profile("d2", phone="93-733-45-60")
    env.upsert("drivers", ["external_id"],
               {"external_id": "d1", "full_name": "Ali Valiyev"})
    env.upsert("drivers", ["external_id"],
               {"external_id": "d2", "full_name": "Vali Karimov"})
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")

    status, data, _ = _request(env, "/api/users",
                               headers=_login_hdr(env, "root"))
    assert status == 200
    rows = {int(u["chat_id"]): u for u in data["users"]}
    assert rows[601]["linked"] is True
    assert rows[601]["driver_name"] == "Ali Valiyev"
    assert rows[601]["driver_id"] == "d1"
    assert rows[602]["linked"] is True
    assert rows[602]["driver_name"] == "Vali Karimov"
    assert rows[602]["driver_id"] == "d2"


def test_user_detail_shows_dashboard_link(env, monkeypatch, tmp_path):
    """Bog'langan panel akkaunti va uning bo'limlari /api/users/{uid}da qaytadi."""
    _bot_user(monkeypatch, tmp_path, 559)
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    st.dashboard_user_add("ali", *deps.hash_password("1234"),
                          role="MANAGER", company="XTEST",
                          data=json.dumps({"pages": ["drivers"],
                                           "telegram_chat_id": "559"}))
    status, data, _ = _request(env, "/api/users/559",
                               headers=_login_hdr(env, "root"))
    assert status == 200
    assert data["dashboard_user"]["username"] == "ali"
    assert data["dashboard_user"]["pages"] == ["drivers"]


# ------------------------------------------------------------ max_users

def _set_max_users(st, company, value):
    st._exec(f"UPDATE companies SET max_users = {st.db.ph} "
             f"WHERE name = {st.db.ph}", (value, company))


def test_panel_create_respects_company_max_users(env):
    """Panel orqali foydalanuvchi yaratish korxona limitiga bo'ysunadi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    st.dashboard_user_add("mavjud", *deps.hash_password("x"),
                          role="VIEWER", company="XTEST")
    _set_max_users(st, "XTEST", 1)
    hdr = _json_hdr(env, "root")
    status, data, _ = _post_users(env, hdr, {
        "action": "create", "username": "yangi", "role": "VIEWER",
        "company": "XTEST", "password": "parol1234"})
    assert data.get("ok") is False
    assert "Maksimal foydalanuvchi soni (1)" in str(data.get("error") or "")
    assert st.dashboard_user_get(username="yangi") is None
    # Limit oshirilgach yaratish mumkin.
    _set_max_users(st, "XTEST", 2)
    status, data, _ = _post_users(env, hdr, {
        "action": "create", "username": "yangi", "role": "VIEWER",
        "company": "XTEST", "password": "parol1234"})
    assert status == 200 and data.get("ok") is True


def test_company_move_respects_target_max_users(env):
    """Foydalanuvchini boshqa korxonaga ko'chirishda o'sha korxona limiti tekshiriladi."""
    st = env
    st.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    st.dashboard_user_add("kochir", *deps.hash_password("x"),
                          role="DISPATCHER", company="XTEST")
    st.dashboard_user_add("y1", *deps.hash_password("x"),
                          role="VIEWER", company="YTEST")
    _set_max_users(st, "YTEST", 1)
    hdr = _json_hdr(env, "root")
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "kochir", "company": "YTEST"})
    assert data.get("ok") is False
    assert "Maksimal foydalanuvchi soni (1)" in str(data.get("error") or "")
    assert st.dashboard_user_get(username="kochir")["company"] == "XTEST"
    # O'rin bo'shagach (yoki limit oshgach) ko'chirish o'tadi.
    _set_max_users(st, "YTEST", 2)
    status, data, _ = _post_users(env, hdr, {
        "action": "update", "username": "kochir", "company": "YTEST"})
    assert status == 200 and data.get("ok") is True
    assert st.dashboard_user_get(username="kochir")["company"] == "YTEST"
