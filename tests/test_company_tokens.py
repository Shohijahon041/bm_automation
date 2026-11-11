"""Kompaniya tokeni izolyatsiyasi va dashboard holati testlari."""

import json

import pytest

from bm_automation.app.core import companies, tokens


def _profile():
    return {"name": "ASL SUNDAY MCHJ", "profileId": "p-1",
            "routeVariantId": "r-1", "username": "u", "password": "p"}


def test_client_for_profile_fails_closed_without_admin_fallback(monkeypatch):
    monkeypatch.delenv("BM_ALLOW_COMPANY_ADMIN_FALLBACK", raising=False)
    monkeypatch.setattr(companies, "_own_client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("token yo'q")))
    called = []
    monkeypatch.setattr(companies, "_admin_client",
                        lambda *a, **k: called.append(True) or object())

    with pytest.raises(RuntimeError, match="tokeniga o'tilmadi"):
        companies.client_for_profile(_profile())
    assert called == []


def test_client_for_profile_admin_fallback_opt_in(monkeypatch):
    monkeypatch.setenv("BM_ALLOW_COMPANY_ADMIN_FALLBACK", "1")
    monkeypatch.setattr(companies, "_own_client",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("token yo'q")))
    sentinel = object()
    monkeypatch.setattr(companies, "_admin_client", lambda *a, **k: sentinel)
    monkeypatch.setattr(companies, "_warn_foreign_token", lambda *a, **k: None)

    assert companies.client_for_profile(_profile()) is sentinel


def test_company_token_state_ok(monkeypatch, tmp_path):
    target = tmp_path / "ASL.json"
    monkeypatch.setattr(companies, "company_token_path", lambda name: target)
    tokens.save_tokens("not-a-jwt", refresh_token="r",
                       organization="ASL SUNDAY MCHJ", path=target)

    state = companies.company_token_state({
        "name": "ASL SUNDAY MCHJ", "routeCount": 2, "has_credentials": True,
        "routes": [{"profileId": "p-1"}, {"profileId": "p-2"}]})

    assert state["status"] == "ok"
    assert state["valid"] is True
    assert state["last_refresh_ts"] > 0
    assert state["remaining_s"] > 0
    assert state["route_count"] == 2
    assert state["profile_ids"] == ["p-1", "p-2"]
    assert "access_token" not in state


def test_company_tokens_endpoint_returns_states_only(monkeypatch):
    from bm_automation.app.dashboard.routers import admin

    monkeypatch.setattr(admin, "_company_in_scope", lambda scope, name: True)
    monkeypatch.setattr(companies, "company_token_state",
                        lambda c: {"name": c["name"], "status": "ok"})

    from bm_automation.app.core import profiles as profiles_mod
    monkeypatch.setattr(profiles_mod, "grouped_profiles",
                        lambda: [{"name": "ASL SUNDAY MCHJ"}])

    data = admin.get_company_tokens(None, {})
    assert data["ok"] is True
    assert data["companies"] == [{"name": "ASL SUNDAY MCHJ", "status": "ok"}]
    assert data["refresh_interval_hours"] == 2


def test_company_token_state_expired_and_no_credentials(monkeypatch, tmp_path):
    expired = tmp_path / "ASL.json"
    expired.write_text(json.dumps({
        "access_token": "not-a-jwt",
        "expires_at": "2020-01-01T00:00:00",
        "obtained_at": "2020-01-01T00:00:00",
    }), encoding="utf-8")
    monkeypatch.setattr(companies, "company_token_path", lambda name: expired)

    state = companies.company_token_state({
        "name": "ASL SUNDAY MCHJ", "routeCount": 1, "has_credentials": True})
    assert state["status"] == "expired"

    empty = tmp_path / "empty.json"
    monkeypatch.setattr(companies, "company_token_path", lambda name: empty)
    state = companies.company_token_state({
        "name": "YANGI", "routeCount": 1, "has_credentials": False})
    assert state["status"] == "no_credentials"
    assert state["has_token"] is False


def test_token_health_records_success_and_error(tmp_path):
    from bm_automation.app.core import token_health

    token_health.record("ASL SUNDAY MCHJ", "refreshed")
    rec = token_health.get("asl  sunday mchj")  # nom normalizatsiya qilinadi
    assert rec["last_result"] == "ok"
    assert rec["last_ok_result"] == "refreshed"
    assert rec["last_ok_at"]
    assert rec["consecutive_errors"] == 0

    token_health.record("ASL SUNDAY MCHJ", "error", "401 Unauthorized")
    rec = token_health.get("ASL SUNDAY MCHJ")
    assert rec["last_result"] == "error"
    assert rec["last_error"] == "401 Unauthorized"
    assert rec["consecutive_errors"] == 1
    # oxirgi muvaffaqiyatli refresh saqlanib qoladi
    assert rec["last_ok_at"]
    assert rec["last_ok_result"] == "refreshed"

    token_health.record("ASL SUNDAY MCHJ", "timeout")
    assert token_health.get("ASL SUNDAY MCHJ")["consecutive_errors"] == 2

    token_health.record("ASL SUNDAY MCHJ", "relogin")
    rec = token_health.get("ASL SUNDAY MCHJ")
    assert rec["consecutive_errors"] == 0
    assert rec["last_error"] == ""
    assert rec["last_ok_result"] == "relogin"
    # token qiymati tarix fayliga yozilmaydi
    raw = token_health.STATE_FILE.read_text(encoding="utf-8")
    assert "access_token" not in raw and "refresh_token" not in raw


def test_company_token_state_merges_history(monkeypatch, tmp_path):
    from bm_automation.app.core import token_health

    target = tmp_path / "ASL.json"
    monkeypatch.setattr(companies, "company_token_path", lambda name: target)
    tokens.save_tokens("not-a-jwt", organization="ASL SUNDAY MCHJ", path=target)
    token_health.record("ASL SUNDAY MCHJ", "refreshed")

    state = companies.company_token_state({
        "name": "ASL SUNDAY MCHJ", "has_credentials": True})

    assert state["status"] == "ok"
    assert state["organization_matches"] is True
    assert state["last_ok_result"] == "refreshed"
    assert state["last_ok_ts"] > 0
    assert state["last_attempt_ts"] > 0
    assert state["consecutive_errors"] == 0


def test_company_token_state_wrong_company(monkeypatch, tmp_path):
    target = tmp_path / "ASL.json"
    monkeypatch.setattr(companies, "company_token_path", lambda name: target)
    tokens.save_tokens("not-a-jwt", organization="BOSHQA MCHJ", path=target)

    state = companies.company_token_state({
        "name": "ASL SUNDAY MCHJ", "has_credentials": True})

    assert state["status"] == "wrong_company"
    assert state["organization_matches"] is False


class _FakeClient:
    def keepalive(self):
        return "refreshed"


def _patch_refresh(monkeypatch, names, state_org_matches=True, any_scope=True):
    from bm_automation.app.core import profiles as profiles_mod

    monkeypatch.setattr(companies, "own_client", lambda p: _FakeClient())
    monkeypatch.setattr(profiles_mod, "profiles_with_credentials",
                        lambda: [{"name": n, "username": "u", "password": "p"}
                                 for n in names])
    monkeypatch.setattr(profiles_mod, "grouped_profiles", lambda: [])
    monkeypatch.setattr(companies, "company_token_state",
                        lambda c: {"name": c.get("name"),
                                   "organization_matches": state_org_matches})
    if any_scope:
        from bm_automation.app.dashboard.routers import admin
        monkeypatch.setattr(admin, "_company_in_scope",
                            lambda scope, name: True)


def test_refresh_company_tokens_super_refreshes_all(monkeypatch):
    from bm_automation.app.dashboard.routers import admin
    from bm_automation.app.core import token_health

    _patch_refresh(monkeypatch, ["ASL SUNDAY MCHJ", "AND ECO TRANSPORT MCHJ"])

    data = admin.refresh_company_tokens(
        None, {}, {"username": "root", "is_admin": True, "super": True})

    assert data["ok"] is True
    assert [r["name"] for r in data["results"]] == [
        "ASL SUNDAY MCHJ", "AND ECO TRANSPORT MCHJ"]
    assert all(r["ok"] and r["result"] == "refreshed"
               for r in data["results"])
    assert "access_token" not in json.dumps(data)
    assert token_health.get("ASL SUNDAY MCHJ")["last_result"] == "ok"


def test_refresh_company_tokens_non_super_only_own_company(monkeypatch):
    from bm_automation.app.dashboard.routers import admin

    _patch_refresh(monkeypatch, ["ASL SUNDAY MCHJ", "AND ECO TRANSPORT MCHJ"],
                   any_scope=False)
    scope = {"username": "asl-admin", "is_admin": True,
             "company": "ASL SUNDAY MCHJ"}

    data = admin.refresh_company_tokens(None, {}, scope)

    assert [r["name"] for r in data["results"]] == ["ASL SUNDAY MCHJ"]

    with pytest.raises(Exception) as exc:
        admin.refresh_company_tokens(None,
                                     {"company": "AND ECO TRANSPORT MCHJ"},
                                     scope)
    assert getattr(exc.value, "status_code", 0) == 403


def test_refresh_company_tokens_records_error(monkeypatch):
    from bm_automation.app.core import profiles as profiles_mod
    from bm_automation.app.core import token_health
    from bm_automation.app.dashboard.routers import admin

    _patch_refresh(monkeypatch, ["ASL SUNDAY MCHJ"])

    def _boom(_p):
        raise RuntimeError("login rad etildi")

    monkeypatch.setattr(companies, "own_client", _boom)
    monkeypatch.setattr(profiles_mod, "profiles_with_credentials",
                        lambda: [{"name": "ASL SUNDAY MCHJ"}])

    data = admin.refresh_company_tokens(
        None, {}, {"username": "root", "is_admin": True, "super": True})

    assert data["ok"] is True
    assert data["results"][0]["ok"] is False
    assert "login rad etildi" in data["results"][0]["error"]
    rec = token_health.get("ASL SUNDAY MCHJ")
    assert rec["last_result"] == "error"
    assert rec["consecutive_errors"] == 1

