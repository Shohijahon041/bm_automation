"""ADMIN MFA (TOTP), xavfsizlik ogohlantirishlari va CSP regressiyalari.

Qamrov:
- TOTP yadrosi: RFC 6238 vektorlari (±1 qadam oyna), buzuq kod/sir rad etiladi.
- Ogohlantirishlar: login xatolari chegarasi va throttle; backup nosozligi;
  `/api/login` xatolari audit hook orqali ADMIN'larga xabar beradi.
- MFA API: setup → enable → disable; sir faqat kod tasdiqlangandan keyin
  saqlanadi; login kod talab qiladi (noto'g'ri kod sessiya yaratmaydi).
- TOTP reset: ADMIN reset qiladi; MANAGER va boshqa korxona admini qila olmaydi.
- CSP: yangi direktivalar (`base-uri`, `form-action`, `object-src`,
  `frame-ancestors`) javob sarlavhasida mavjud.
"""

import threading
import time

import pytest

from bm_automation.app.dashboard import alerts, deps, totp
from tests.test_security_hardening import senv  # noqa: F401 - pytest fixture

# RFC 6238 ilovasidagi SHA1 siri: b"12345678901234567890" (base32).
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _client():
    from bm_automation.app.dashboard.fastapi_app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture(autouse=True)
def _quiet_alerts(monkeypatch):
    """Har testda ogohlantirishlar tarmoqqa chiqmasin (yuboruvchi almashtiriladi)."""
    alerts.reset_state()
    alerts.set_sender(lambda text: True)
    yield
    alerts.reset_state()
    alerts.set_sender(None)


def _wait(pred, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _collector() -> tuple[list, object]:
    got: list = []
    lock = threading.Lock()

    def send(text):
        with lock:
            got.append(text)
        return True

    return got, send


def _mkuser(st, username: str, password: str = "StrongPass1",
            role: str = "ADMIN", company: str = "", totp_secret: str = "") -> int:
    digest, salt = deps.hash_password(password)
    row_id = st.dashboard_user_add(username, digest, salt, role=role,
                                   company=company)
    assert row_id, "foydalanuvchi yaratilmadi"
    if totp_secret:
        assert st.dashboard_user_update(row_id, totp_secret=totp_secret)
    return row_id


def _session(username: str, row_id: int, role: str, company: str = "") -> str:
    return deps.create_session({"username": username, "id": row_id,
                                "role": role, "company": company})


def _bad_code(secret: str) -> str:
    now = time.time()
    valid = {totp.code_at(secret, now + d * totp.STEP_S) for d in (-1, 0, 1)}
    for cand in ("000000", "111111", "123456", "654321", "999999"):
        if cand not in valid:
            return cand
    raise AssertionError("ziddiyatli kod topilmadi")


# --------------------------------------------------------------- TOTP yadro

@pytest.mark.parametrize("at,expected", [
    (59, "287082"),
    (1111111109, "081804"),
    (1234567890, "005924"),
    (2000000000, "279037"),
    (20000000000, "353130"),
])
def test_totp_rfc6238_vectors(at, expected):
    assert totp.code_at(RFC_SECRET, at) == expected


def test_totp_verify_window_and_rejects():
    now = 1_700_000_000.0
    good = totp.code_at(RFC_SECRET, now)
    assert totp.verify(RFC_SECRET, good, at=now)
    # ±1 qadam: oldingi/keyingi 30 soniyalik kod ham qabul qilinadi.
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now + 30), at=now)
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now - 30), at=now)
    # Ikki qadam narida — rad etiladi.
    assert not totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, now + 120), at=now)
    assert not totp.verify(RFC_SECRET, "abcdef", at=now)
    assert not totp.verify(RFC_SECRET, "12345", at=now)
    assert not totp.verify("", good, at=now)


def test_new_secret_and_otpauth_url():
    secret = totp.new_secret()
    assert len(secret) >= 26 and "=" not in secret
    url = totp.otpauth_url(secret, "ali")
    assert url.startswith("otpauth://totp/")
    assert f"secret={secret}" in url
    assert "issuer=BM%20Dashboard" in url
    assert f"digits={totp.DIGITS}" in url


# --------------------------------------------------------- ogohlantirishlar

def test_login_alert_threshold_and_throttle():
    got, send = _collector()
    alerts.set_sender(send)
    for _ in range(alerts.LOGIN_ALERT_THRESHOLD - 1):
        assert alerts.notify_login_failure("ali", "1.2.3.4", "bad-password") is False
    assert alerts.notify_login_failure("ali", "1.2.3.4", "bad-password") is True
    assert _wait(lambda: len(got) == 1)
    assert "login xatolari" in got[0]
    # Throttle oynasi ichida qo'shimcha xabar ketmaydi.
    for _ in range(3):
        assert alerts.notify_login_failure("ali", "1.2.3.4", "bad-password") is False
    time.sleep(0.1)
    assert len(got) == 1


def test_backup_alert_once_per_window():
    got, send = _collector()
    alerts.set_sender(send)
    assert alerts.notify_backup_failure("PARTIAL", "2 jadval xato") is True
    assert alerts.notify_backup_failure("ERROR", "ulanish yo'q") is False
    assert _wait(lambda: len(got) == 1)
    assert "zaxira nusxa" in got[0]


def test_login_endpoint_triggers_alert(senv):  # noqa: F811
    got, send = _collector()
    alerts.set_sender(send)
    client = _client()
    for _ in range(alerts.LOGIN_ALERT_THRESHOLD):
        assert client.post("/api/login",
                           json={"username": "ghost", "password": "wrongpass1"}
                           ).status_code == 401
    assert _wait(lambda: len(got) == 1)
    assert "ghost" in got[0]


# ------------------------------------------------------------------ MFA API

def test_mfa_status_requires_session(senv):  # noqa: F811
    assert _client().get("/api/mfa/status").status_code == 401


def test_login_requires_totp_code(senv):  # noqa: F811
    st = senv["st"]
    secret = totp.new_secret()
    _mkuser(st, "mfa_admin", role="ADMIN", totp_secret=secret)
    client = _client()

    # Parol to'g'ri, kod yo'q → sessiya yaratilmaydi.
    r = client.post("/api/login", json={"username": "mfa_admin",
                                        "password": "StrongPass1"})
    assert r.status_code == 401
    assert r.json().get("mfa_required") is True
    assert "bm_session" not in client.cookies

    # Noto'g'ri kod ham sessiya yaratmaydi.
    r = client.post("/api/login", json={"username": "mfa_admin",
                                        "password": "StrongPass1",
                                        "code": _bad_code(secret)})
    assert r.status_code == 401
    assert r.json().get("mfa_required") is True
    assert "bm_session" not in client.cookies

    # To'g'ri kod → kirish.
    r = client.post("/api/login", json={"username": "mfa_admin",
                                        "password": "StrongPass1",
                                        "code": totp.code_at(secret)})
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert client.cookies.get("bm_session")


def test_mfa_setup_enable_disable_flow(senv):  # noqa: F811
    st = senv["st"]
    row_id = _mkuser(st, "self_mfa", role="ADMIN")
    tok = _session("self_mfa", row_id, "ADMIN")
    cookies = {"bm_session": tok}
    client = _client()

    assert client.get("/api/mfa/status", cookies=cookies).json()["enabled"] is False

    # Parol qayta tasdiqlanishi shart.
    r = client.post("/api/mfa/setup", cookies=cookies,
                    json={"password": "wrong-pass"})
    assert r.status_code == 400

    r = client.post("/api/mfa/setup", cookies=cookies,
                    json={"password": "StrongPass1"})
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert r.json()["otpauth"].startswith("otpauth://totp/")
    # Tasdiqlanmagan sir saqlanmaydi.
    assert str(st.dashboard_user_get(username="self_mfa").get("totp_secret") or "") == ""

    r = client.post("/api/mfa/enable", cookies=cookies,
                    json={"code": _bad_code(secret)})
    assert r.status_code == 400
    assert str(st.dashboard_user_get(username="self_mfa").get("totp_secret") or "") == ""

    r = client.post("/api/mfa/enable", cookies=cookies,
                    json={"code": totp.code_at(secret)})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert str(st.dashboard_user_get(username="self_mfa").get("totp_secret") or "") == secret
    assert client.get("/api/mfa/status", cookies=cookies).json()["enabled"] is True

    r = client.post("/api/mfa/disable", cookies=cookies,
                    json={"password": "wrong-pass"})
    assert r.status_code == 400
    assert str(st.dashboard_user_get(username="self_mfa").get("totp_secret") or "") == secret

    r = client.post("/api/mfa/disable", cookies=cookies,
                    json={"password": "StrongPass1"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert str(st.dashboard_user_get(username="self_mfa").get("totp_secret") or "") == ""


def test_dashboard_users_list_reports_mfa_flag(senv):  # noqa: F811
    st = senv["st"]
    _mkuser(st, "plain_user", role="MANAGER", company="ATEST")
    _mkuser(st, "mfa_user", role="MANAGER", company="ATEST",
            totp_secret=totp.new_secret())
    tok = _session("root", 999, "ADMIN")
    users = {u["username"]: u for u in
             _client().get("/api/dashboard-users", cookies={"bm_session": tok})
             .json()["users"]}
    assert users["plain_user"]["mfa"] is False
    assert users["mfa_user"]["mfa"] is True


def test_mfa_reset_permissions_and_clear(senv):  # noqa: F811
    st = senv["st"]
    target = _mkuser(st, "victim", role="MANAGER", company="ATEST",
                     totp_secret=totp.new_secret())
    client = _client()

    # MANAGER reset qila olmaydi.
    tok = _session("mgr", 501, "MANAGER", "ATEST")
    r = client.post("/api/dashboard-users", cookies={"bm_session": tok},
                    json={"action": "mfa_reset", "username": "victim"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert str(st.dashboard_user_get(username="victim").get("totp_secret") or "") != ""

    # Boshqa korxona admini ham qila olmaydi.
    tok_b = _session("b_admin", 502, "ADMIN", "BTEST")
    r = client.post("/api/dashboard-users", cookies={"bm_session": tok_b},
                    json={"action": "mfa_reset", "username": "victim"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert str(st.dashboard_user_get(username="victim").get("totp_secret") or "") != ""

    # O'z korxonasi admini reset qiladi.
    tok_a = _session("a_admin", 503, "ADMIN", "ATEST")
    r = client.post("/api/dashboard-users", cookies={"bm_session": tok_a},
                    json={"action": "mfa_reset", "username": "victim"})
    assert r.status_code == 200 and r.json()["ok"] is True
    row = st.dashboard_user_get(username="victim")
    assert int(row["id"]) == target
    assert str(row.get("totp_secret") or "") == ""


# ---------------------------------------------------------------------- CSP

def test_csp_contains_hardening_directives(senv):  # noqa: F811
    r = _client().get("/")
    csp = r.headers.get("content-security-policy") or ""
    for directive in ("base-uri 'self'", "form-action 'self'",
                      "object-src 'none'", "frame-ancestors 'none'"):
        assert directive in csp
    assert r.headers.get("x-frame-options") == "DENY"
