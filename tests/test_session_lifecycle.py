"""Sessiya tsikli testlari.

Qoplaydi:
  * sessiya muddati SILANADI — faol panel 12 soatdan keyin chetlab yuborilmaydi;
  * mutlaq chek — sessiya 7 kundan oshmaydi;
  * parol o'zgarganda (Settings yoki admin paneli) boshqa sessiyalar yopiladi,
    joriy qurilma qoladi;
  * `/api/me` (SPA har ~30 soniyada) `bm_session` kuki muddatini yangilaydi;
  * GPS UI 401 javobda panel loginiga qaytaradi.

Fixture va yordamchilar `tests/test_dashboard_users.py`dan olinadi (haqiqiy
auth: `BM_TEST_MODE` o'chirilgan, sessiya/DB tmp'da).
"""

import json
import time
from pathlib import Path

from tests.test_dashboard_users import (  # noqa: F401  (env fixture shu yerda)
    _client, _json_hdr, _login, _post_users, _request, env,
)

from bm_automation.app.dashboard import deps

_GPS_UI = (Path(__file__).resolve().parents[1] /
           "bm_automation" / "app" / "gps" / "gps_ui.html")


def _age_session(token, *, expires_in=60, last_seen=0, created_at=None):
    """Sessiyani qaritadi: muddati deyarli tugagan, oxirgi faollik uzoq oldin."""
    with deps._SESSION_LOCK:
        deps._load_sessions()
        s = deps._sessions[token]
        s["expires_at"] = time.time() + expires_in
        s["last_seen"] = last_seen
        if created_at is not None:
            s["created_at"] = created_at
        deps._save_sessions()


def _session_field(token, key):
    with deps._SESSION_LOCK:
        deps._load_sessions()
        return deps._sessions.get(token, {}).get(key)


# ------------------------------------------------------------- muddat silashi

def test_session_slides_on_activity(env):
    """Faol sessiya muddati to'ldiriladi — panel ochiq turgan kuni chetlab yuborilmaydi."""
    tok = deps.create_session({"username": "ali", "role": "VIEWER", "company": ""})
    _age_session(tok, expires_in=60, last_seen=0)

    scope = deps.session_user(tok)
    assert scope and scope["username"] == "ali"

    assert _session_field(tok, "expires_at") > time.time() + deps._SESSION_MAX_AGE_S - 60
    assert _session_field(tok, "last_seen") > time.time() - 5


def test_session_touch_is_throttled(env):
    """Muddat har so'rovda emas, 10 daqiqada bir marta yangilanadi (disk yozuvi)."""
    tok = deps.create_session({"username": "ali", "role": "VIEWER", "company": ""})
    _age_session(tok, expires_in=3600, last_seen=time.time() - 60)  # yaqinda tekshirilgan
    before = _session_field(tok, "expires_at")
    assert deps.session_user(tok) is not None
    assert _session_field(tok, "expires_at") == before


def test_session_absolute_cap(env):
    """Nechun faol bo'lmasin, sessiya 7 kundan (mutlaq chek) oshmaydi."""
    tok = deps.create_session({"username": "ali", "role": "VIEWER", "company": ""})
    _age_session(tok, expires_in=3600,
                 created_at=time.time() - (deps._SESSION_ABS_MAX_S + 60))

    assert deps.session_user(tok) is None
    with deps._SESSION_LOCK:
        deps._load_sessions()
        assert tok not in deps._sessions


def test_me_refreshes_session_cookie(env):
    """`/api/me` faol sessiyada kuki qayta yozadi (Max-Age=12 soat)."""
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    tok = cookies["bm_session"]

    r = _client().get("/api/me", cookies={"bm_session": tok})
    assert r.status_code == 200 and r.json().get("ok") is True
    set_cookies = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
    assert any(v.startswith("bm_session=") and "Max-Age=43200" in v
               for v in set_cookies), f"kuki qayta yozilmadi: {set_cookies}"


def test_me_does_not_reissue_cookie_without_session(env):
    """Sessiya yopilgan bo'lsa (masalan parol o'zgargan) kuki qayta yozilmaydi."""
    r = _client().get("/api/me", cookies={"bm_session": "yoq-tok"})
    assert r.status_code == 200
    assert r.json() == {"ok": False, "authenticated": False}
    set_cookies = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
    assert not any(v.startswith("bm_session=") for v in set_cookies)


# --------------------------------------------- parol o'zgarishi → sessiya yopish

def test_change_password_closes_other_sessions(env):
    """Settings orqali parol o'zgarganda boshqa qurilmalar yopiladi, joriy qoladi."""
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    _, _, c1 = _login(env, "root", "to'g'ri")
    _, _, c2 = _login(env, "root", "to'g'ri")
    current, other = c1["bm_session"], c2["bm_session"]
    assert current != other

    body = json.dumps({"current_password": "to'g'ri",
                       "new_password": "yangi-parol"}).encode()
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        headers={"Cookie": f"bm_session={current}",
                 "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True
    assert data.get("closed_sessions") == 1

    assert deps.session_user(other) is None          # eski qurilma yopildi
    assert deps.session_user(current) is not None    # joriy qurilma qoldi
    assert _login(env, "root", "yangi-parol")[0] == 200
    assert _login(env, "root", "to'g'ri")[0] == 401  # eski parol ishlamaydi


def test_change_password_requires_auth(env):
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        body=json.dumps({"current_password": "x",
                         "new_password": "yyyy"}).encode())
    assert status == 401
    assert data.get("ok") is False


def test_change_password_wrong_current_rejected(env):
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    tok = cookies["bm_session"]
    body = json.dumps({"current_password": "xato",
                       "new_password": "yangi-parol"}).encode()
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        headers={"Cookie": f"bm_session={tok}",
                 "Content-Length": str(len(body))}, body=body)
    assert status == 400 and data.get("ok") is False
    # parol o'zgarmadi
    assert _login(env, "root", "to'g'ri")[0] == 200


def test_change_password_short_new_rejected(env):
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN")
    _, _, cookies = _login(env, "root", "to'g'ri")
    body = json.dumps({"current_password": "to'g'ri", "new_password": "ab"}).encode()
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        headers={"Cookie": f"bm_session={cookies['bm_session']}",
                 "Content-Length": str(len(body))}, body=body)
    assert status == 400 and data.get("ok") is False


def test_admin_reset_closes_target_sessions(env):
    """Admin panelidan boshqa odam parolini yangilasayotgan — uning eski
    sessiyalari yopiladi, adminniki qoladi."""
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN",
                           data='{"super": true}')
    env.dashboard_user_add("ali", *deps.hash_password("eski"), role="DISPATCHER",
                           company="XTEST")
    _, _, ali_cookies = _login(env, "ali", "eski")
    ali_tok = ali_cookies["bm_session"]
    _, _, root_cookies = _login(env, "root", "to'g'ri")
    root_tok = root_cookies["bm_session"]

    status, data, _ = _post_users(env, _json_hdr(env, "root"),
                                  {"action": "update", "username": "ali",
                                   "password": "yangi-parol"})
    assert status == 200 and data.get("ok") is True
    assert data.get("closed_sessions") == 1

    assert deps.session_user(ali_tok) is None
    assert deps.session_user(root_tok) is not None
    assert _login(env, "ali", "yangi-parol")[0] == 200


def test_admin_reset_own_password_keeps_current_session(env):
    """O'z parolini paneldan yangilasayotgan — joriy qurilma sessiyasi qoladi."""
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN",
                           data='{"super": true}')
    _, _, cookies = _login(env, "root", "to'g'ri")
    tok = cookies["bm_session"]

    status, data, _ = _post_users(
        env, {"Content-Type": "application/json",
              "Cookie": f"bm_session={tok}"},
        {"action": "update", "username": "root", "password": "yangi-parol"})
    assert status == 200 and data.get("ok") is True
    assert data.get("closed_sessions") == 0
    assert deps.session_user(tok) is not None


# --------------------------- bootstrap admin paroli → .env sinxroni (1 yo'l)

def _write_env_file(path, **kv):
    lines = [f"{k}={v}" for k, v in kv.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_change_password_syncs_bootstrap_admin_env(env, tmp_path, monkeypatch):
    """Bootstrap admin o'z parolini o'zgartirsa, `.env` ham yangilanadi —
    aks holda keyingi restart eski .env parolini qaytarib yuborardi."""
    env_file = tmp_path / ".env"
    _write_env_file(env_file, DASHBOARD_ADMIN_USER="root",
                    DASHBOARD_ADMIN_PASS="eski-env", BM_USERNAME="ali")
    monkeypatch.setattr(deps, "_env_file", lambda: env_file)
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "root")
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN",
                           data='{"super": true}')
    _, _, cookies = _login(env, "root", "to'g'ri")

    body = json.dumps({"current_password": "to'g'ri",
                       "new_password": "yangi #1 parol"}).encode()
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        headers={"Cookie": f"bm_session={cookies['bm_session']}",
                 "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True

    txt = env_file.read_text(encoding="utf-8")
    assert "DASHBOARD_ADMIN_PASS" in txt
    from dotenv import dotenv_values
    assert dotenv_values(str(env_file))["DASHBOARD_ADMIN_PASS"] == "yangi #1 parol"
    assert dotenv_values(str(env_file))["BM_USERNAME"] == "ali"  # qolgan kalitlar saqlanadi


def test_change_password_ignores_env_for_other_users(env, tmp_path, monkeypatch):
    """Oddiy foydalanuvchi parol o'zgartirsa `.env`ga tegilmaydi."""
    env_file = tmp_path / ".env"
    _write_env_file(env_file, DASHBOARD_ADMIN_USER="root",
                    DASHBOARD_ADMIN_PASS="eshki")
    monkeypatch.setattr(deps, "_env_file", lambda: env_file)
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "root")
    env.dashboard_user_add("ali", *deps.hash_password("eski"), role="DISPATCHER",
                           company="XTEST")
    _, _, cookies = _login(env, "ali", "eski")

    body = json.dumps({"current_password": "eski",
                       "new_password": "yangi-parol"}).encode()
    status, data, _ = _request(
        env, "/api/change-password", method="POST",
        headers={"Cookie": f"bm_session={cookies['bm_session']}",
                 "Content-Length": str(len(body))}, body=body)
    assert status == 200 and data.get("ok") is True
    assert "DASHBOARD_ADMIN_PASS=eshki" in env_file.read_text(encoding="utf-8")


def test_admin_panel_reset_syncs_env_for_bootstrap_admin(env, tmp_path, monkeypatch):
    """Admin panelidan (dashboard-users) bootstrap admin parolini yangilasayam
    `.env` sinxronlanadi — restart parolni buzmasin."""
    env_file = tmp_path / ".env"
    _write_env_file(env_file, DASHBOARD_ADMIN_USER="root",
                    DASHBOARD_ADMIN_PASS="eski-env")
    monkeypatch.setattr(deps, "_env_file", lambda: env_file)
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "root")
    env.dashboard_user_add("root", *deps.hash_password("to'g'ri"), role="ADMIN",
                           data='{"super": true}')
    _, _, cookies = _login(env, "root", "to'g'ri")

    status, data, _ = _post_users(
        env, {"Content-Type": "application/json",
              "Cookie": f"bm_session={cookies['bm_session']}"},
        {"action": "update", "username": "root", "password": "boshqa-parol"})
    assert status == 200 and data.get("ok") is True
    from dotenv import dotenv_values
    assert dotenv_values(str(env_file))["DASHBOARD_ADMIN_PASS"] == "boshqa-parol"


def test_env_sync_helper_direct():
    """`sync_env_admin_password`: noto'g'ri foydalanuvchi/bo'sh parol → False."""
    assert deps.sync_env_admin_password("ali", "x") is False
    assert deps.sync_env_admin_password("", "x") is False
    assert deps.sync_env_admin_password("admin", "") is False


def test_session_cap_prunes_oldest(env):
    """Bitta akkaunt uchun 8 tadan ortiq sessiya saqlanmaydi."""
    tokens = [deps.create_session(
        {"username": "ali", "role": "VIEWER", "company": ""}) for _ in range(8)]
    with deps._SESSION_LOCK:
        deps._load_sessions()
        for i, tok in enumerate(tokens):
            deps._sessions[tok]["last_seen"] = i
        deps._save_sessions()
    newest = deps.create_session(
        {"username": "ali", "role": "VIEWER", "company": ""})
    assert deps.session_user(tokens[0]) is None
    assert deps.session_user(tokens[-1]) is not None
    assert deps.session_user(newest) is not None


def test_drop_user_sessions_helper(env):
    """`drop_user_sessions` faqat shu foydalanuvchini yopadi va joriyni saqlaydi."""
    a1 = deps.create_session({"username": "ali", "role": "VIEWER", "company": ""})
    a2 = deps.create_session({"username": "ali", "role": "VIEWER", "company": ""})
    b1 = deps.create_session({"username": "bola", "role": "VIEWER", "company": ""})

    closed = deps.drop_user_sessions("ALI", keep_token=a1)
    assert closed == 1
    assert deps.session_user(a1) is not None
    assert deps.session_user(a2) is None
    assert deps.session_user(b1) is not None


# ------------------------------------------------------------- GPS 401 → login

def test_gps_ui_redirects_to_panel_on_401():
    """GPS UI 401'da jim bo'sh sahifa emas, xabar ko'rsatib panelga qaytaradi."""
    src = _GPS_UI.read_text(encoding="utf-8")
    assert "function _authRequired" in src
    assert "r.status === 401" in src
    assert "location.href = '/'" in src
    # kuki bilan so'rov (panel sessiyasi bilan ochilishi uchun)
    assert "credentials: 'same-origin'" in src
