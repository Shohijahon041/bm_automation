"""Production-grade BMClient testlari.

Tarmoqqa chiqmasdan, soxta session orqali:
  * retry/backoff qoidalari (4xx retry qilinmaydi, 5xx faqat idempotent)
  * 401 -> refresh -> original so'rov qayta bajarilishi
  * 401 -> refresh ishlamasa -> relogin / BMAuthError
  * rate-limit (429) — Retry-After
  * correlation ID va timeout uzatilishi
  * JSON validatsiya va tokenlar xavfsizligi
"""

from __future__ import annotations

import json
import logging

import pytest

from bm_automation.app.api.client import (
    BMApiError,
    BMAuthError,
    BMClient,
    BMJSONError,
    BMRateLimitError,
)

TOKEN = "secret-access-token-123"
REFRESH = "secret-refresh-token-456"


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None,
                 text="", content=b""):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.content = content
        if json_body is not None:
            self.text = json.dumps(json_body, ensure_ascii=False)
            self.content = self.text.encode("utf-8")
        else:
            self.text = text
            self.content = content or self.text.encode("utf-8")

    def json(self):
        if self.status_code == 204 or not self.text:
            raise ValueError("no json")
        try:
            return json.loads(self.text)
        except ValueError:
            raise ValueError("invalid json")

    def iter_content(self, chunk_size=65536):
        yield self.content


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, params=None, json=None, timeout=None,
                headers=None, stream=False):
        self.calls.append({
            "method": method.upper(), "url": url, "params": params,
            "json": json, "timeout": timeout, "headers": headers, "stream": stream,
        })
        if not self._responses:
            return FakeResponse(500, text="no more responses")
        item = self._responses.pop(0)
        if callable(item):
            return item(self.calls[-1])
        return item


class _Cfg:
    base_url = "https://bm.example.test"
    organization = "TEST"
    username = "user"
    password = "pass"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import bm_automation.app.core.tokens as tokens_mod

    monkeypatch.setattr(tokens_mod, "TOKENS_FILE", tmp_path / "tokens.json")
    monkeypatch.setattr(tokens_mod, "TOKENS_DIR", tmp_path / "tokens")
    c = BMClient(config=_Cfg())
    c.access_token = TOKEN
    c.refresh_token = REFRESH
    c.auto_relogin = False
    c.logger.disabled = True
    return c


def _ok(body=None, headers=None, status=200):
    return FakeResponse(status, json_body=body, headers=headers)


def _err(status, body=None, headers=None, text=""):
    return FakeResponse(status, json_body=body, headers=headers, text=text)


# ---------- JSON validatsiya / parse ----------

def test_get_unwraps_data(client):
    client.session = FakeSession([_ok({"data": {"a": 1}})])
    assert client.get("/x") == {"a": 1}


def test_get_returns_list(client):
    client.session = FakeSession([_ok([1, 2, 3])])
    assert client.get("/x") == [1, 2, 3]


def test_error_status_and_message(client):
    client.session = FakeSession([_err(404, {"message": "topilmadi"})])
    with pytest.raises(BMApiError) as ei:
        client.get("/x")
    assert ei.value.status == 404
    assert "topilmadi" in str(ei.value)


def test_invalid_json_raises(client):
    resp = FakeResponse(200, text="<html>not json</html>")
    resp.headers = {"Content-Type": "application/json"}
    client.session = FakeSession([resp])
    with pytest.raises(BMJSONError):
        client.get("/x")


# ---------- Retry qoidalari ----------

def test_4xx_not_retried(client):
    client.session = FakeSession([_err(404), _ok()])
    with pytest.raises(BMApiError):
        client.get("/x", retries=3)
    assert len(client.session.calls) == 1


def test_5xx_retried_for_get(client, monkeypatch):
    monkeypatch.setattr(client, "_backoff_sleep", lambda attempt: None)
    client.session = FakeSession([_err(500), _err(500), _ok({"data": "ok"})])
    assert client.get("/x", retries=2) == "ok"
    assert len(client.session.calls) == 3


def test_5xx_not_retried_for_post(client):
    client.session = FakeSession([_err(500), _ok()])
    with pytest.raises(BMApiError):
        client.post("/x", json={})
    assert len(client.session.calls) == 1


def test_retries_zero_no_retry(client):
    client.session = FakeSession([_err(500), _ok()])
    with pytest.raises(BMApiError):
        client.get("/x", retries=0)
    assert len(client.session.calls) == 1


# ---------- Auth: 401 -> refresh -> replay ----------

def test_401_refresh_and_replay(client):
    client.session = FakeSession([
        _err(401),
        _ok({"data": {"access_token": "new-access", "refresh_token": "new-refresh"}}),
        _ok({"data": "replayed"}),
    ])
    assert client.get("/secure") == "replayed"
    methods = [c["method"] for c in client.session.calls]
    assert methods == ["GET", "POST", "GET"]
    assert client.access_token == "new-access"
    assert client.refresh_token == "new-refresh"


def test_401_twice_raises_auth_error(client):
    client.session = FakeSession([
        _err(401),
        _ok({"data": {"access_token": "new-access", "refresh_token": "new-refresh"}}),
        _err(401),
    ])
    with pytest.raises(BMAuthError):
        client.get("/secure")
    assert len(client.session.calls) == 3


def test_refresh_failure_raises_auth_error_without_secret(client):
    client.session = FakeSession([
        _err(401),
        _err(400, {"message": "bad refresh"}),
    ])
    with pytest.raises(BMAuthError) as ei:
        client.get("/secure")
    assert REFRESH not in str(ei.value)
    assert TOKEN not in str(ei.value)


def test_refresh_failure_relogin_flow(client, monkeypatch):
    """refresh ishlamasa -> password login -> OneID login (browser) -> replay."""
    called = {}

    def fake_browser_login(**kwargs):
        called["browser"] = kwargs.get("headless")

    def fake_load_from_file():
        client.access_token = "browser-access"
        client.refresh_token = "browser-refresh"
        return True

    import importlib
    browser_login_module = importlib.import_module("bm_automation.app.auth.browser_login")
    monkeypatch.setattr(browser_login_module, "browser_login", fake_browser_login)
    monkeypatch.setattr(client, "load_tokens_from_file", fake_load_from_file)

    client.session = FakeSession([
        _err(401),
        _err(400, {"message": "refresh eskirgan"}),
        _err(401, {"message": "password xato"}),
        _ok({"data": "after-relogin"}),
    ])
    client.auto_relogin = True
    assert client.get("/secure") == "after-relogin"
    assert called.get("browser") is True
    assert client.access_token == "browser-access"
    methods = [c["method"] for c in client.session.calls]
    assert methods == ["GET", "POST", "POST", "GET"]


def test_login_refreshes_when_access_expired(client, monkeypatch):
    """Muddati o'tgan access -> password o'rniga avval refresh ishlatiladi."""
    def fake_load_from_file():
        client.access_token = "expired-access"
        client.refresh_token = "saved-refresh"
        return True

    monkeypatch.setattr(client, "load_tokens_from_file", fake_load_from_file)
    monkeypatch.setattr(client, "_token_remaining", lambda t: -1)

    client.session = FakeSession([
        _ok({"data": {"access_token": "fresh-access",
                      "refresh_token": "fresh-refresh"}}),
    ])
    client.login()
    assert client.access_token == "fresh-access"
    methods = [c["method"] for c in client.session.calls]
    assert methods == ["POST"]  # faqat refresh; password POST qilinmaydi
    assert "refresh-token" in client.session.calls[0]["url"]


def test_login_falls_back_to_password_when_refresh_fails(client, monkeypatch):
    """Refresh eskirgan bo'lsa login() username/password bilan davom etadi."""
    def fake_load_from_file():
        client.access_token = "expired-access"
        client.refresh_token = "saved-refresh"
        return True

    monkeypatch.setattr(client, "load_tokens_from_file", fake_load_from_file)
    monkeypatch.setattr(client, "_token_remaining", lambda t: -1)

    client.session = FakeSession([
        _err(401, {"message": "refresh eskirgan"}),
        _ok({"data": {"access_token": "pw-access",
                      "refresh_token": "pw-refresh"}}),
    ])
    client.login()
    assert client.access_token == "pw-access"
    methods = [c["method"] for c in client.session.calls]
    assert methods == ["POST", "POST"]


def test_login_falls_back_to_oneid_when_password_fails(client, monkeypatch):
    """Password ham 401 bo'lsa auto_relogin OneID brauzer oqimini ishlatadi."""
    called = {}

    def fake_browser_login(**kwargs):
        called["browser"] = True

    def fake_load_from_file():
        client.access_token = "oneid-access"
        client.refresh_token = "oneid-refresh"
        return True

    import importlib
    browser_login_module = importlib.import_module("bm_automation.app.auth.browser_login")
    monkeypatch.setattr(browser_login_module, "browser_login", fake_browser_login)
    monkeypatch.setattr(client, "load_tokens_from_file", fake_load_from_file)
    monkeypatch.setattr(client, "_token_remaining", lambda t: -1)

    client.session = FakeSession([
        _err(401, {"message": "refresh eskirgan"}),
        _err(401, {"message": "password xato"}),
    ])
    client.auto_relogin = True
    client.login()
    assert called.get("browser") is True
    assert client.access_token == "oneid-access"


def test_refresh_race_retries_with_rotated_token(client, tmp_path):
    """Boshqa jarayon refresh aylantirgan bo'lsa, yangi token bilan qayta uriniladi."""
    import json

    (tmp_path / "tokens.json").write_text(json.dumps({
        "access_token": "stale-access",
        "refresh_token": "rotated-refresh",
    }), encoding="utf-8")

    client.session = FakeSession([
        _err(401, {"message": "refresh eskirgan"}),
        _ok({"data": {"access_token": "new-access",
                      "refresh_token": "new-refresh"}}),
    ])
    client.refresh_token = "old-refresh"
    client._refresh()
    assert client.access_token == "new-access"
    assert client.refresh_token == "new-refresh"
    methods = [c["method"] for c in client.session.calls]
    assert methods == ["POST", "POST"]
    assert client.session.calls[1]["json"]["refreshToken"] == "rotated-refresh"


# ---------- Rate-limit (429) ----------

def test_429_not_retried_for_post(client):
    client.session = FakeSession([_err(429, headers={"Retry-After": "5"})])
    with pytest.raises(BMRateLimitError) as ei:
        client.post("/x", json={})
    assert ei.value.status == 429
    assert ei.value.retry_after == 5.0
    assert len(client.session.calls) == 1


def test_429_retried_for_get_respecting_retry_after(client, monkeypatch):
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(client.__class__, "_backoff_seconds", lambda self, a: 0.0)
    monkeypatch.setattr("time.sleep", fake_sleep)
    client.session = FakeSession([
        _err(429, headers={"Retry-After": "2"}),
        _ok({"data": "ok"}),
    ])
    assert client.get("/x", retries=2) == "ok"
    assert sleeps == [2.0]
    assert len(client.session.calls) == 2


# ---------- Correlation ID / timeout ----------

def test_correlation_id_header_sent(client):
    client.session = FakeSession([_ok()])
    client.get("/x")
    header = client.session.calls[0]["headers"]["X-Correlation-ID"]
    assert header and len(header) > 8


def test_timeout_passed_through(client):
    client.session = FakeSession([_ok()])
    client.get("/x", timeout=5)
    assert client.session.calls[0]["timeout"] == 5


def test_default_timeout(client):
    client.session = FakeSession([_ok()])
    client.get("/x")
    assert client.session.calls[0]["timeout"] == 120.0


# ---------- Security ----------

def test_error_detail_redacts_tokens(client):
    client.session = FakeSession([
        _err(500, {"message": "x", "data": {"leak": TOKEN}}),
    ])
    with pytest.raises(BMApiError) as ei:
        client.get("/x", retries=0)
    assert TOKEN not in str(ei.value)


def test_no_secrets_in_logged_payload(client):
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record.getMessage())

    handler = Capture()
    client.logger.disabled = False
    client.logger.addHandler(handler)
    client.logger.setLevel(logging.INFO)
    client.session = FakeSession([_ok({"data": "ok"})])
    client.get("/x")
    joined = "\n".join(handler.records)
    assert TOKEN not in joined
    assert REFRESH not in joined
    assert "Authorization" not in joined


# ---------- Pagination ----------

def test_list_all_single_page(client):
    client.session = FakeSession([
        _ok({"data": {"content": [1, 2], "totalElements": 2}}),
    ])
    assert client.list_all("/items") == [1, 2]
    assert len(client.session.calls) == 1


# ---------- Download ----------

def test_download_writes_file(client, tmp_path):
    client.session = FakeSession([
        _err(401),
        _ok({"data": {"access_token": "new-access", "refresh_token": "new-refresh"}}),
        FakeResponse(200, content=b"BINARY-DATA", headers={"Content-Type": "application/vnd.ms-excel"}),
    ])
    out = tmp_path / "f.xlsx"
    result = client.download("/report", str(out))
    assert result == str(out)
    assert out.read_bytes() == b"BINARY-DATA"
    assert client.session.calls[0]["stream"] is True


def test_download_error_raises(client, tmp_path):
    client.session = FakeSession([_err(500)])
    with pytest.raises(BMApiError) as ei:
        client.download("/report", str(tmp_path / "f.xlsx"))
    assert ei.value.status == 500


# ---------- login_for_profile ----------

def test_profile_ok_sets_token(client):
    client.session = FakeSession([
        _ok({"data": {"access_token": "PROF-A", "refresh_token": "PROF-R"}}),
    ])
    client.login_for_profile("101")
    assert client.access_token == "PROF-A"
    assert client.session.headers["Authorization"] == "Bearer PROF-A"


def test_profile_401_falls_back_to_main(client, monkeypatch):
    loaded = []
    client.session = FakeSession([_err(401)])
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    client.access_token = "MAIN-A"
    client.login_for_profile("101")
    assert loaded, "401 (profil mavjud emas) — asosiy tokenga qaytishi kerak"
    assert client.access_token == "MAIN-A"


def test_profile_403_falls_back_to_main(client, monkeypatch):
    loaded = []
    client.session = FakeSession([_err(403)])
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    client.login_for_profile("101")
    assert loaded, "403 (ruxsat yo'q) — asosiy tokenga qaytishi kerak"


def test_profile_500_raises_no_fallback(client, monkeypatch):
    loaded = []
    client.session = FakeSession([_err(500)])
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    with pytest.raises(BMApiError) as ei:
        client.login_for_profile("101")
    assert ei.value.status == 500
    assert not loaded, "500 — asosiy tokenga tushish MUMKIN EMAS"


def test_profile_auth_error_falls_back_to_main(client, monkeypatch):
    """BMAuthError (login noto'g'ri javob) — asosiy tokenga qaytadi."""
    loaded = []
    monkeypatch.setattr(client, "login_by_profile",
                        lambda pid: (_ for _ in ()).throw(BMAuthError(200)))
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    client.login_for_profile("101")
    assert loaded


def test_profile_auth_error_no_fallback_raises(client, monkeypatch):
    loaded = []
    monkeypatch.setattr(client, "login_by_profile",
                        lambda pid: (_ for _ in ()).throw(BMAuthError(200)))
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    with pytest.raises(BMAuthError):
        client.login_for_profile("101", fallback_to_main=False)
    assert not loaded


def test_profile_fallback_disabled_raises(client, monkeypatch):
    loaded = []
    client.session = FakeSession([_err(403)])
    monkeypatch.setattr(client, "load_tokens_from_file",
                        lambda: loaded.append(True))
    with pytest.raises(BMApiError):
        client.login_for_profile("101", fallback_to_main=False)
    assert not loaded


def test_profile_no_token_payload_raises_auth(client):
    client.session = FakeSession([_ok({"data": {"message": "no token"}})])
    with pytest.raises(BMAuthError):
        client.login_by_profile("101")


def test_profile_empty_id_noop(client, monkeypatch):
    client.session = FakeSession([])
    client.login_for_profile("  ")
    assert not client.session.calls
