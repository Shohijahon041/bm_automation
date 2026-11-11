"""Bot `/login` oqimi: kredensial tekshiruvi, egalik va xato xabarlari.

Regressiya: `_run_login` `set_owner_chat_ids` ni import qilardi, lekin bunday
funksiya `profiles.py` da yo'q edi — har qanday login NameError bilan
tugardi. Qo'shimcha talab: tasdiqlanmagan login saqlangan kredensial
ustidan yozilmasligi kerak.
"""

import time

import pytest

from bm_automation.app.core import companies, profiles
from bm_automation.app.notifications.ops import dispatch


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "PROFILES_FILE", tmp_path / "profiles.json")
    profiles.save_profiles({"active": "XTEST", "profiles": [
        {"name": "XTEST", "routeVariantId": "r1",
         "username": "bm_user", "password": "bm_secret",
         "ownerChatIds": [111]},
    ]})
    replies: list[str] = []
    stats: list[tuple] = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda chat_id, text, **_kw: replies.append(str(text)))
    monkeypatch.setattr(dispatch, "show_company_stats",
                        lambda chat_id, name, owners: stats.append(
                            (chat_id, name, owners)))
    return {"replies": replies, "stats": stats}


def _wait(replies: list[str]) -> None:
    end = time.time() + 3.0
    while time.time() < end and len(replies) < 2:
        time.sleep(0.02)


def test_wrong_credentials_do_not_overwrite_saved(env, monkeypatch):
    """Login/parol xato — saqlangan kredensial va egalik o'zgarmaydi."""
    monkeypatch.setattr(companies, "detect_company", lambda u, p: (None, None))
    dispatch._run_login(555, "bm_user", "xato_parol")
    _wait(env["replies"])

    assert any("Login yoki parol xato" in t for t in env["replies"])
    prof = profiles.get_profile("XTEST")
    assert prof["password"] == "bm_secret"
    assert prof["ownerChatIds"] == [111]
    assert not env["stats"]


def test_valid_credentials_attach_owner(env, monkeypatch):
    """Tasdiqlangan login kompaniyaga ulaydi va egasini qo'shadi."""
    prof = profiles.get_profile("XTEST")
    monkeypatch.setattr(companies, "detect_company",
                        lambda u, p: (prof, object()))
    dispatch._run_login(555, "bm_user", "bm_secret")
    _wait(env["replies"])

    assert any("ulandingiz" in t for t in env["replies"])
    assert profiles.get_profile("XTEST")["ownerChatIds"] == [111, 555]
    assert env["stats"] == [(555, "XTEST", [555])]


def test_valid_login_without_company_is_not_attached(env, monkeypatch):
    """Kredensial to'g'ri, firma aniqlanmasa — egalik yozilmaydi."""
    monkeypatch.setattr(companies, "detect_company",
                        lambda u, p: (None, object()))
    dispatch._run_login(555, "bm_user", "bm_secret")
    _wait(env["replies"])

    assert any("firma aniqlanmadi" in t for t in env["replies"])
    assert profiles.get_profile("XTEST")["ownerChatIds"] == [111]


def test_detection_error_reports_network_message(env, monkeypatch):
    """Aniqlashda istisno — foydalanuvchiga tushunarli xabar, traceback emas."""
    def _boom(u, p):
        raise RuntimeError("timeout")

    monkeypatch.setattr(companies, "detect_company", _boom)
    dispatch._run_login(555, "bm_user", "bm_secret")
    _wait(env["replies"])

    assert any("tarmoq xatosi" in t for t in env["replies"])
    assert not any("timeout" in t for t in env["replies"])


def test_set_owner_chat_ids_updates_all_rows(monkeypatch, tmp_path):
    """Egalik kompaniyaning barcha yo'nalish qatorlariga yoziladi."""
    monkeypatch.setattr(profiles, "PROFILES_FILE", tmp_path / "profiles.json")
    profiles.save_profiles({"profiles": [
        {"name": "XTEST", "routeVariantId": "r1"},
        {"name": "XTEST", "routeVariantId": "r2"},
    ]})
    profiles.set_owner_chat_ids("XTEST", ["111", 111, "222", "xato"])
    data = profiles.load_profiles()
    assert all(p.get("ownerChatIds") == [111, 222]
               for p in data["profiles"])
