"""To'g'ridan-to'g'ri OneID (SSO) login oqimi testlari.

/sign-in sahifasi bloklangan muhitlarda (server) ishlatiladigan
`_direct_sso_code` / `_exchange_code` va `browser_login` marshrutizatsiyasi.
Tarmoq/brauzer talab qilmaydi — BMClient va save_tokens monkeypatch qilinadi.
"""

import importlib

import pytest

bl = importlib.import_module("bm_automation.app.auth.browser_login")
client_mod = importlib.import_module("bm_automation.app.api.client")
prof_mod = importlib.import_module("bm_automation.app.core.profiles")


def test_direct_sso_url_has_expected_params():
    assert "sso.egov.uz" in bl.DIRECT_SSO_URL
    assert "client_id=bm_dtransport_uz" in bl.DIRECT_SSO_URL
    assert "redirect_uri=https%3A%2F%2Fbm.dtransport.uz%2Flogin%2Foneid" in bl.DIRECT_SSO_URL


def test_exchange_code_saves_tokens_and_profiles(monkeypatch):
    saved = {}
    discovered = {}

    class FakeClient:
        def __init__(self):
            self.config = type("C", (), {"base_url": "https://bmapi.dtransport.uz"})()

        def login_via_oneid(self, code):
            assert code == "abc123"
            return {
                "token": {"access_token": "A", "refresh_token": "R"},
                "profiles": [{"id": "p1", "organizationName": "ORG"}],
            }

    monkeypatch.setattr(client_mod, "BMClient", FakeClient)
    monkeypatch.setattr(bl, "save_tokens", lambda a, r, **k: saved.update(access=a, refresh=r, **k))
    monkeypatch.setattr(prof_mod, "discover_from_oneid",
                        lambda caps, **k: discovered.update(n=len(caps)) or [])

    out = bl._exchange_code("abc123", "")
    assert out["access_token"] == "A"
    assert saved["access"] == "A"
    assert saved["refresh"] == "R"
    assert saved["organization"] == "ORG"
    assert discovered.get("n") == 1


def test_exchange_code_raises_without_token(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.config = type("C", (), {"base_url": "x"})()

        def login_via_oneid(self, code):
            return {"token": {}, "profiles": []}

    monkeypatch.setattr(client_mod, "BMClient", FakeClient)
    with pytest.raises(RuntimeError):
        bl._exchange_code("x", "")


def test_browser_login_uses_direct_sso(monkeypatch):
    called = {}
    monkeypatch.setattr(bl, "_direct_sso_code",
                        lambda u, p, h, t: called.update(code=1) or "CODE")
    monkeypatch.setattr(bl, "_exchange_code",
                        lambda code, org: called.update(exchange=code) or {"access_token": "A"})
    cfg = type("C", (), {"username": "u", "password": "p", "organization": "",
                         "base_url": "https://bmapi.dtransport.uz"})()
    monkeypatch.setattr(bl, "get_config", lambda: cfg)

    out = bl.browser_login()
    assert out["access_token"] == "A"
    assert called.get("exchange") == "CODE"


def test_browser_login_skips_direct_for_test_site(monkeypatch):
    """Test muhiti uchun to'g'ridan-to'g'ri SSO ishlatilmaydi (fallback oqim)."""
    called = {"direct": 0}

    def boom(*a, **k):
        called["direct"] += 1
        raise AssertionError("direct ssо test saytda chaqilmasligi kerak")

    monkeypatch.setattr(bl, "_direct_sso_code", boom)
    cfg = type("C", (), {"username": "u", "password": "p", "organization": "",
                         "base_url": "https://testapi-bm.dtransport.uz"})()
    monkeypatch.setattr(bl, "get_config", lambda: cfg)

    # Brauzer ochilmasligi uchun sync_playwright'ni ham almashtiramiz.
    class FakePW:
        def __enter__(self):
            raise RuntimeError("stop")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bl, "sync_playwright", lambda: FakePW())
    with pytest.raises(RuntimeError):
        bl.browser_login()
    assert called["direct"] == 0
