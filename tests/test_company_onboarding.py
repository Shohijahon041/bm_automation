"""Kompaniya onboarding testlari — `POST /api/admin/companies/onboard`.

Bir so'rovda bajariladigan qadamlar:
  * `profiles.json` yozuvi (kompaniya + yo'nalish + BM kredensiallari);
  * `companies` reyestri (tarif/obuna/limitlar);
  * `profiles` DB qatori (route → kompaniya xaritasi);
  * dashboard foydalanuvchisi (login + parol + rol + kompaniya).

Fixture va yordamchilar `tests/test_dashboard_users.py`dan olinadi.
"""

import json

import pytest

from tests.test_dashboard_users import (  # noqa: F401  (env fixture shu yerda)
    _login, _mk, _request, env,
)

from bm_automation.app.core import companies as companies_mod
from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.core import tenant as tenant_mod
from bm_automation.app.dashboard.routers import system as system_router

_BASE = {
    "name": "YANGI KORXONA",
    "routeVariantId": "r9",
    "routeName": "9-yo'nalish",
    "bmUsername": "bm_login",
    "bmPassword": "bm_parol",
    "userUsername": "yangi_admin",
    "userPassword": "sirli123",
    "userRole": "DIRECTOR",
    "verify": False,
    "sync": False,
}


def _super(env, username="root"):
    return _mk(env, username, "ADMIN", data='{"super": true}')


def _onboard(env, hdr, **overrides):
    payload = {**_BASE, **overrides}
    body = json.dumps(payload).encode()
    return _request(env, "/api/admin/companies/onboard", method="POST",
                    headers={**hdr, "Content-Length": str(len(body))},
                    body=body)


def _steps(data):
    return {s["key"]: s for s in (data.get("steps") or [])}


# --------------------------------------------------------------- to'liq oqim

def test_onboard_creates_profile_registry_and_user(env):
    """Bitta so'rovda profil, kredensial, reyestr, DB qatori va user yaratiladi."""
    st = env
    status, data, _ = _onboard(env, _super(env))
    assert status == 200 and data.get("ok") is True, data

    steps = _steps(data)
    for key in ("profile", "credentials", "registry", "profiles_db", "user"):
        assert steps[key]["ok"] is True, (key, steps[key])
    assert data.get("slug") == "yangi-korxona"

    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeVariantId"] == "r9"
    assert prof["routeName"] == "9-yo'nalish"
    assert prof["username"] == "bm_login" and prof["password"] == "bm_parol"

    row = st.find("companies", slug="yangi-korxona")
    assert row and row["name"] == "YANGI KORXONA"
    assert row["status"] == "active"

    db_prof = st.query(f"SELECT name, route_id, company_id FROM profiles "
                       f"WHERE name = {st.db.ph}", ("YANGI KORXONA",), limit=1)
    assert db_prof and db_prof[0]["route_id"] == "r9"
    assert int(db_prof[0]["company_id"]) == int(row["id"])

    # route → kompaniya xaritasi (dashboard scope uchun) ishlaydi
    tenant_mod.reset()
    assert tenant_mod.company_id_for_route("r9") == int(row["id"])

    user = st.dashboard_user_get(username="yangi_admin")
    assert user["role"] == "DIRECTOR"
    assert user["company"] == "YANGI KORXONA"
    assert user["active"] == 1

    status, login, cookies = _login(env, "yangi_admin", "sirli123")
    assert status == 200 and login.get("ok") is True
    assert "bm_session" in cookies


def test_onboard_applies_registry_fields(env):
    """Tarif, obuna muddati va limitlar `companies` qatoriga yoziladi."""
    st = env
    status, data, _ = _onboard(env, _super(env), plan="pro",
                               expireDate="2027-12-31", maxUsers=7,
                               phone="+998901112233")
    assert data.get("ok") is True, data
    row = st.find("companies", slug="yangi-korxona")
    assert row["subscription_plan"] == "pro"
    assert row["subscription_expire_date"] == "2027-12-31"
    assert int(row["max_users"]) == 7
    assert row["phone"] == "+998901112233"


def test_onboard_owner_ids_saved_to_profile(env):
    """Ega Telegram ID'lari profilga ro'yxat ko'rinishida saqlanadi."""
    status, data, _ = _onboard(env, _super(env), ownerChatIds="111, 222 ;333")
    assert data.get("ok") is True, data
    assert profiles_mod.get_profile("YANGI KORXONA")["ownerChatIds"] == [111, 222, 333]


# ------------------------------------------------------------------ huquqlar

def test_onboard_requires_super_admin(env):
    """Oddiy ADMIN (super emas) kompaniya qo'sha olmaydi."""
    status, data, _ = _onboard(env, _mk(env, "boss", "ADMIN"))
    assert status == 403
    assert data.get("ok") is False
    assert profiles_mod.get_profile("YANGI KORXONA") is None


# ---------------------------------------------------------------- validatsiya

def test_onboard_rejects_duplicate_name(env):
    """Mavjud kompaniya nomi bilan qayta qo'shilmaydi."""
    status, data, _ = _onboard(env, _super(env), name="XTEST",
                               routeVariantId="r7")
    assert data.get("ok") is False
    assert "allaqachon" in data["error"]


def test_onboard_rejects_route_taken(env):
    """Boshqa kompaniyaga bog'langan routeVariantId qabul qilinmaydi."""
    status, data, _ = _onboard(env, _super(env), routeVariantId="r1")
    assert data.get("ok") is False
    assert "yo'nalish" in data["error"].lower()
    assert profiles_mod.get_profile("YANGI KORXONA") is None


@pytest.mark.parametrize("overrides,needle", [
    ({"routeVariantId": ""}, "routeVariantId"),
    ({"bmPassword": ""}, "BM login"),
    ({"userRole": "ADMIN"}, "Rol"),
    ({"userPassword": "ab"}, "kamida 8"),
    ({"expireDate": "2026-13-40"}, "YYYY-MM-DD"),
])
def test_onboard_validation_errors(env, overrides, needle):
    """Noto'g'ri payload hech narsa yozmasdan xato qaytaradi."""
    status, data, _ = _onboard(env, _super(env), **overrides)
    assert data.get("ok") is False
    assert needle in data["error"]
    assert profiles_mod.get_profile("YANGI KORXONA") is None


def test_onboard_rejects_taken_login(env):
    """Mavjud dashboard logini bilan foydalanuvchi yaratilmaydi."""
    env.dashboard_user_add("band", "h", "s", role="VIEWER")
    status, data, _ = _onboard(env, _super(env), userUsername="band")
    assert data.get("ok") is False
    assert "login" in data["error"].lower()
    assert profiles_mod.get_profile("YANGI KORXONA") is None


def _strict_select_only(st):
    """Storage'ni Postgres kabi qattiq qiladi: `query` faqat SELECT.

    Ishlab chiqarishda `query()` SELECT-only (`db.query`) — UPDATE'dan keyin
    fetch "didn't produce records" xatosi beradi. Test SQLite'da esa UPDATE
    jim o'tib ketadi, shuning uchun bunday xato faqat shu simulyatsiya bilan
    ushlanadi. (Yozuvlar `_exec` orqali o'tishi shart.)
    """
    real_query = st.query

    def guard(sql, params=(), limit=None):
        head = str(sql).strip().upper()
        if not (head.startswith(("SELECT ", "(SELECT ", "WITH ", "(SELECT\n"))):
            raise AssertionError(f"Yozuv query() orqali ketyapti: {head[:40]}")
        return real_query(sql, params, limit=limit)

    st.query = guard
    return st


def test_onboarding_writes_use_exec_not_query(env, monkeypatch):
    """Reyestr/registry yozuvlari `_exec` orqali — Postgres'da `query` xato beradi."""
    st = _strict_select_only(env)
    status, data, _ = _onboard(env, _super(env), plan="pro", maxUsers=7,
                               phone="+998901112233", verify=False, sync=False)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert steps["registry"]["ok"] is True, steps["registry"]
    row = st.find("companies", slug="yangi-korxona")
    assert row["subscription_plan"] == "pro"


def test_edit_writes_use_exec_not_query(env, monkeypatch):
    """Tahrirlashda ham registry yozuvi `_exec` orqali o'tadi."""
    _created(env)
    st = _strict_select_only(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            plan="premium", verify=False, sync=False)
    assert data.get("ok") is True, data
    assert _steps(data)["registry"]["ok"] is True
    assert st.find("companies", slug="yangi-korxona")["subscription_plan"] == "premium"


# ----------------------------------------------------------- tahrirlash (edit)

def _edit(env, hdr, **payload):
    body = json.dumps(payload).encode()
    return _request(env, "/api/admin/companies/edit", method="POST",
                    headers={**hdr, "Content-Length": str(len(body))},
                    body=body)


def _created(env):
    """Test uchun kompaniyani onboarding orqali yaratadi."""
    status, data, _ = _onboard(env, _super(env))
    assert data.get("ok") is True, data
    return data


def test_edit_updates_profile_registry_and_db(env):
    """Tahrirlash yo'nalish, kredensial va reyestr maydonlarini yangilaydi."""
    st = env
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            routeVariantId="r10", routeName="10-yangi",
                            bmUsername="bm_new", bmPassword="bm_new_pass",
                            plan="premium", maxUsers=9, phone="+998900000000",
                            verify=False, sync=False)
    assert status == 200 and data.get("ok") is True, data
    steps = _steps(data)
    for key in ("profile", "credentials", "registry", "profiles_db"):
        assert steps[key]["ok"] is True, (key, steps[key])

    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeVariantId"] == "r10"
    assert prof["routeName"] == "10-yangi"
    assert prof["username"] == "bm_new" and prof["password"] == "bm_new_pass"

    row = st.find("companies", slug="yangi-korxona")
    assert row["subscription_plan"] == "premium"
    assert int(row["max_users"]) == 9
    assert row["phone"] == "+998900000000"

    db_prof = st.query(f"SELECT route_id FROM profiles WHERE name = {st.db.ph}",
                       ("YANGI KORXONA",), limit=1)
    assert db_prof and db_prof[0]["route_id"] == "r10"

    tenant_mod.reset()
    assert tenant_mod.company_id_for_route("r10") == int(row["id"])


def test_edit_resets_dashboard_password_and_role(env):
    """Mavjud foydalanuvchi paroli va roli yangilanadi (eski parol ishlamaydi)."""
    st = env
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            userUsername="yangi_admin", userPassword="yangi456",
                            userRole="MANAGER", verify=False, sync=False)
    assert data.get("ok") is True, data
    assert _steps(data)["user"]["ok"] is True
    user = st.dashboard_user_get(username="yangi_admin")
    assert user["role"] == "MANAGER" and user["active"] == 1
    status, _, _ = _login(env, "yangi_admin", "sirli123")
    assert status != 200
    status, login, cookies = _login(env, "yangi_admin", "yangi456")
    assert status == 200 and "bm_session" in cookies


def test_edit_creates_missing_user(env):
    """Tahrirlashda ko'rsatilgan yangi login mavjud bo'lmasa — yaratiladi."""
    st = env
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            userUsername="ikkinchi", userPassword="Kuchli#2026",
                            userRole="DISPATCHER", verify=False, sync=False)
    assert data.get("ok") is True, data
    user = st.dashboard_user_get(username="ikkinchi")
    assert user["role"] == "DISPATCHER"
    assert user["company"] == "YANGI KORXONA"


def test_edit_username_only_keeps_password(env):
    """Faqat login berilsa parol saqlanadi (qisman kredensial yangilanish)."""
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            bmUsername="bm_login2", verify=False, sync=False)
    assert data.get("ok") is True, data
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["username"] == "bm_login2"
    assert prof["password"] == "bm_parol"


def test_edit_password_only_keeps_username(env):
    """Faqat parol berilsa login saqlanadi."""
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            bmPassword="bm_parol2", verify=False, sync=False)
    assert data.get("ok") is True, data
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["username"] == "bm_login"
    assert prof["password"] == "bm_parol2"


def test_edit_clears_owner_ids(env):
    """Bo'sh `ownerChatIds` egalar ro'yxatini tozalaydi."""
    _created(env)
    _edit(env, _super(env), name="YANGI KORXONA", ownerChatIds="111,222",
          verify=False, sync=False)
    assert profiles_mod.get_profile("YANGI KORXONA")["ownerChatIds"] == [111, 222]
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            ownerChatIds="", verify=False, sync=False)
    assert data.get("ok") is True, data
    assert profiles_mod.get_profile("YANGI KORXONA")["ownerChatIds"] == []


def test_edit_rejects_route_of_other_company(env):
    """Boshqa kompaniyaning yo'nalishi tanlansa xato (profil o'zgarmaydi)."""
    _created(env)
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            routeVariantId="r1", verify=False, sync=False)
    assert data.get("ok") is False
    assert "yo'nalish" in data["error"].lower()
    assert profiles_mod.get_profile("YANGI KORXONA")["routeVariantId"] == "r9"


def test_edit_unknown_company(env):
    """Mavjud bo'lmagan kompaniya tahrirlanmaydi."""
    status, data, _ = _edit(env, _super(env), name="YO'Q KORXONA",
                            verify=False, sync=False)
    assert data.get("ok") is False and "topilmadi" in data["error"]


def test_edit_requires_super_admin(env):
    """Oddiy ADMIN tahrirlay olmaydi."""
    _created(env)
    hdr = _mk(env, "boss3", "ADMIN")
    status, data, _ = _edit(env, hdr, name="YANGI KORXONA",
                            routeName="x", verify=False, sync=False)
    assert status == 403 and data.get("ok") is False
    assert profiles_mod.get_profile("YANGI KORXONA")["routeName"] == "9-yo'nalish"


# ----------------------------------------------------- SaaS reyestr endpointlari

def test_registry_create_update_activate(env):
    """`/api/admin/companies` CRUD ishlaydi (avval `now_iso` importi buzilgan edi)."""
    st = env
    hdr = _super(env)

    status, data, _ = _onboard(env, hdr)   # reyestr qatori shu yerda yaratiladi
    assert data.get("ok") is True, data
    row = st.find("companies", slug="yangi-korxona")
    cid = int(row["id"])

    body = json.dumps({"subscription_plan": "premium"}).encode()
    status, upd, _ = _request(env, f"/api/admin/companies/{cid}", method="PUT",
                              headers={**hdr, "Content-Length": str(len(body))},
                              body=body)
    assert status == 200 and upd.get("ok") is True, upd
    assert st.find("companies", id=cid)["subscription_plan"] == "premium"

    body = json.dumps({"days": 30}).encode()
    status, act, _ = _request(env, f"/api/admin/companies/{cid}/activate",
                              method="POST",
                              headers={**hdr, "Content-Length": str(len(body))},
                              body=body)
    assert status == 200 and act.get("ok") is True, act
    re_row = st.find("companies", id=cid)
    assert re_row["status"] == "active"
    assert re_row["subscription_expire_date"] == act["expire_date"]


# ----------------------------------------------------- BM tekshiruv va sinxron

def test_onboard_verify_failure_is_warning(env, monkeypatch):
    """BM kredensiallari ishlamasa — ogohlantirish, kompaniya baribir qo'shiladi."""
    def _boom(profile):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(companies_mod, "own_client", _boom)
    status, data, _ = _onboard(env, _super(env), verify=True, sync=True)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert steps["verify"]["ok"] is False
    assert any("401" in w for w in data["warnings"])
    assert steps["sync"]["ok"] is False      # token yo'q — sinxron boshlanmaydi
    assert profiles_mod.get_profile("YANGI KORXONA") is not None


def test_onboard_sync_uses_company_client(env, monkeypatch):
    """Sinxron kompaniyaning O'Z tokeni bilan boshlanadi (asosiy token emas)."""
    sentinel = object()
    calls = {}

    monkeypatch.setattr(companies_mod, "own_client", lambda profile: sentinel)
    monkeypatch.setattr(companies_mod, "route_accessible",
                        lambda client, rid: True)

    def _sync(client, storage, routes, days=None):
        calls["client"] = client
        calls["routes"] = list(routes)
        return {"ok": True, "started": True, "routes": list(routes)}

    monkeypatch.setattr(system_router, "start_company_sync", _sync)

    status, data, _ = _onboard(env, _super(env), verify=True, sync=True)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert steps["verify"]["ok"] and steps["route_access"]["ok"]
    assert steps["sync"]["ok"] is True
    assert calls["client"] is sentinel
    assert calls["routes"] == ["r9"]


def test_start_company_sync_passes_own_client(env, monkeypatch):
    """`start_company_sync` berilgan client'ni fon oqimiga uzatadi."""
    from datetime import date

    sentinel = object()
    captured = {}

    monkeypatch.setattr(system_router, "_DAILY_SYNC_BG",
                        {"running": False, "progress": "", "result": None})
    monkeypatch.setattr(system_router, "_sync_interval_ok", lambda: None)

    def _bg(bg, storage, days, routes, client=None):
        captured.update(days=days, routes=routes, client=client)

    monkeypatch.setattr(system_router, "_start_sync_bg", _bg)

    res = system_router.start_company_sync(sentinel, env, ["r9"])
    assert res["ok"] is True and res["routes"] == ["r9"]
    assert captured["client"] is sentinel
    assert captured["days"] == [date.today().isoformat()]

    empty = system_router.start_company_sync(sentinel, env, [""])
    assert empty["ok"] is False and "Yo'nalish" in empty["error"]


def test_onboard_sync_without_verify_still_builds_client(env, monkeypatch):
    """Tekshiruv o'chirilgan bo'lsa ham sinxron uchun token olinadi."""
    sentinel = object()
    calls = {}

    monkeypatch.setattr(companies_mod, "own_client", lambda profile: sentinel)

    def _sync(client, storage, routes, days=None):
        calls["client"] = client
        return {"ok": True, "started": True, "routes": list(routes)}

    monkeypatch.setattr(system_router, "start_company_sync", _sync)

    status, data, _ = _onboard(env, _super(env), verify=False, sync=True)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert "verify" not in steps
    assert steps["sync"]["ok"] is True
    assert calls["client"] is sentinel


# ------------------------------------------------- konechka nomlari (auto)

def test_onboard_autofills_konechka_from_waybills(env, monkeypatch):
    """Yangi kompaniya konechkalari waybill'lardan avtomatik yoziladi."""
    from bm_automation.app.services import driver_sheet_service as dss

    monkeypatch.setattr(dss, "detect_profile_fields",
                        lambda prof, date_str="": {
                            "start1": "Qoqon yo'li",
                            "start2": "Sil kassaliklar shifoxonasi (Konechka)"})
    status, data, _ = _onboard(env, _super(env), verify=False, sync=False)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert steps["konechka"]["ok"] is True
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["start1"] == "Qoqon yo'li"
    assert prof["start2"] == "Sil kassaliklar shifoxonasi (Konechka)"


def test_onboard_autofills_route_name_from_waybills(env, monkeypatch):
    """Yo'nalish nomi bo'sh bo'lsa BM waybill'idagi rasmiy nom yoziladi."""
    from bm_automation.app.services import driver_sheet_service as dss

    monkeypatch.setattr(dss, "detect_profile_fields",
                        lambda prof, date_str="": {
                            "routeName": "12",
                            "start1": "Qoqon yo'li",
                            "start2": "Sil kassaliklar shifoxonasi (Konechka)"})
    status, data, _ = _onboard(env, _super(env), routeName="", verify=False,
                               sync=False)
    assert data.get("ok") is True, data
    steps = _steps(data)
    assert steps["route_name"]["ok"] is True
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeName"] == "12"


def test_onboard_manual_route_name_wins_over_autofill(env, monkeypatch):
    """Qo'lda kiritilgan yo'nalish nomi BM nomi bilan bosilmaydi."""
    from bm_automation.app.services import driver_sheet_service as dss

    monkeypatch.setattr(dss, "detect_profile_fields",
                        lambda prof, date_str="": {
                            "routeName": "12",
                            "start1": "BM 1", "start2": "BM 2"})
    status, data, _ = _onboard(env, _super(env), verify=False, sync=False)
    assert data.get("ok") is True, data
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeName"] == "9-yo'nalish"
    assert "route_name" not in _steps(data)


def test_onboard_manual_starts_win_over_autofill(env, monkeypatch):
    """Qo'lda kiritilgan konechkalar avtomatik aniqlash bilan bosilmaydi."""
    from bm_automation.app.services import driver_sheet_service as dss

    def _boom(*a, **k):
        raise AssertionError("qo'lda to'ldirilgan bo'lsa so'ralmasin")

    monkeypatch.setattr(dss, "detect_profile_fields", _boom)
    status, data, _ = _onboard(env, _super(env), verify=False, sync=False,
                               start1="Qo'lda 1", start2="Qo'lda 2")
    assert data.get("ok") is True, data
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["start1"] == "Qo'lda 1" and prof["start2"] == "Qo'lda 2"
    assert "konechka" not in _steps(data)


def test_edit_route_change_resets_starts_and_autofills(env, monkeypatch):
    """Yo'nalish almashsa eski konechkalar qolmaydi — yangisidan aniqlanadi."""
    from bm_automation.app.services import driver_sheet_service as dss

    _created(env)
    profiles_mod.update_profile_fields("YANGI KORXONA",
                                       {"start1": "Eski 1", "start2": "Eski 2"})
    monkeypatch.setattr(dss, "detect_profile_fields",
                        lambda prof, date_str="": {"start1": "Yangi 1",
                                                   "start2": "Yangi 2"})
    status, data, _ = _edit(env, _super(env), name="YANGI KORXONA",
                            routeVariantId="r10", verify=False, sync=False)
    assert data.get("ok") is True, data
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeVariantId"] == "r10"
    assert prof["start1"] == "Yangi 1" and prof["start2"] == "Yangi 2"
    assert not prof["routeName"]  # eski nom yangi yo'nalishga qolmadi


def test_detect_company_fields_returns_waybill_names(env, monkeypatch):
    """`/api/admin/companies/detect` nomlarni qaytaradi, lekin saqlamaydi."""
    from bm_automation.app.services import driver_sheet_service as dss

    _created(env)
    monkeypatch.setattr(dss, "detect_profile_fields",
                        lambda prof, date_str="": {
                            "routeName": "12",
                            "start1": "Qoqon yo'li",
                            "start2": "Sil (Konechka)"})
    body = json.dumps({"name": "YANGI KORXONA"}).encode()
    status, data, _ = _request(
        env, "/api/admin/companies/detect", method="POST",
        headers={**_super(env), "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True, data
    assert data["routeName"] == "12"
    assert data["start1"] == "Qoqon yo'li"
    prof = profiles_mod.get_profile("YANGI KORXONA")
    assert prof["routeName"] == "9-yo'nalish"  # saqlanmadi


def test_detect_company_fields_requires_super_admin(env):
    body = json.dumps({"name": "YANGI KORXONA"}).encode()
    status, data, _ = _request(
        env, "/api/admin/companies/detect", method="POST",
        headers={**_mk(env, "boss", "ADMIN"), "Content-Length": str(len(body))},
        body=body)
    assert status == 403 and data.get("ok") is False
