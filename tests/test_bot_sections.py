"""Bot bo'lim ruxsatlari (per-user sections) testlari.

Panelda berilgan bo'limlar botda ham amal qiladi: bot foydalanuvchisiga
aniq ro'yxat berilsa (yoki Telegram akkaunt panel foydalanuvchisiga
bog'langan bo'lsa) — faqat shu bo'limlar buyruqlari bajariladi.
"""

import json
import re
from pathlib import Path

import pytest

from bm_automation.app.core import bot_users, sections
from bm_automation.app.dashboard import deps as dash_deps
from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import dispatch, kb, render, roles
from bm_automation.app.notifications.ops.roles import Role
from tests.sqlite_backend import SQLiteDatabase

_WEB = Path("bm_automation/app/dashboard/web/index.html")


@pytest.fixture()
def bot_state(monkeypatch, tmp_path):
    """bot_users holatini tmp'ga izolyatsiya qiladi (jonli state o'qilmasin)."""
    monkeypatch.setattr(bot_users, "STATE_FILE", tmp_path / "bot_users.json")
    monkeypatch.setattr(bot_users, "PHOTO_DIR", tmp_path / "photos")

    def _reset():
        with bot_users._LOCK:
            bot_users._CACHE = {}
            bot_users._LOADED = False
            bot_users._LAST_MTIME = None
            bot_users._LAST_SIZE = None

    _reset()
    yield
    _reset()


@pytest.fixture()
def storage(tmp_path):
    return storage_for(SQLiteDatabase(str(tmp_path / "st.db")))


def _sent(monkeypatch):
    """dispatch.reply ni ushlab, yuborilgan matnlarni qaytaradi."""
    out: list = []
    monkeypatch.setattr(
        dispatch, "reply",
        lambda chat_id, text, reply_markup=None: out.append((chat_id, text)))
    monkeypatch.setattr(dispatch, "answer", lambda *a, **k: None)
    return out


# --------------------------------------------------------------- kalitlar

def test_section_keys_match_panel():
    """Panel `PAGE_ROLES` kalitlari Python ro'yxati bilan bir xil bo'lishi shart."""
    html = _WEB.read_text(encoding="utf-8")
    m = re.search(r"const PAGE_ROLES\s*=\s*\{(.*?)\};", html, re.S)
    assert m, "PAGE_ROLES topilmadi"
    keys = set(re.findall(r"'?([\w-]+)'?\s*:\s*\d+", m.group(1)))
    assert keys == set(sections.SECTION_KEYS)
    # Har bir kalit uchun o'zbekcha nom bo'lishi kerak.
    for key in sections.SECTION_KEYS:
        assert sections.SECTION_LABELS.get(key)


def test_clean_sections_validation():
    assert sections.clean_sections(None) == (None, "")
    assert sections.clean_sections("") == (None, "")
    assert sections.clean_sections([]) == ([], "")
    pages, err = sections.clean_sections(["drivers", "drivers", "overview"])
    assert pages == ["drivers", "overview"] and err == ""
    pages, err = sections.clean_sections(["drivers", "yoq-bolim"])
    assert pages is None and "yoq-bolim" in err
    # Ro'yxat emas — xato.
    assert sections.clean_sections("drivers")[1]


def test_section_for_command_mapping():
    assert sections.section_for_command("/vehicles") == "vehicles"
    assert sections.section_for_command("TOP") == "top-performance"
    assert sections.section_for_command("nav:drivers") == ""  # nav: prefiksi yo'q
    assert sections.section_for_command("help") == ""
    assert sections.section_for_command("myrole") == ""


# --------------------------------------------------- bot_users: saqlash

def test_bot_user_pages_roundtrip(bot_state):
    bot_users.record_user(555, first_name="Ali")
    assert bot_users.set_pages(555, ["overview", "routes"]) is True
    assert bot_users.get_pages(555) == ["overview", "routes"]
    acts = [a["action"] for a in bot_users.get_user(555)["activity"]]
    assert "pages_changed" in acts
    # None — cheklov olib tashlanadi.
    assert bot_users.set_pages(555, None) is True
    assert bot_users.get_pages(555) is None
    # Noma'lum kalit — xato, holat o'zgarmaydi.
    with pytest.raises(ValueError):
        bot_users.set_pages(555, ["nomalum"])
    assert bot_users.get_pages(555) is None
    # Ro'yxatda yo'q foydalanuvchi.
    assert bot_users.set_pages(999, ["overview"]) is False


def test_get_pages_none_without_user(bot_state):
    assert bot_users.get_pages(1234) is None


# --------------------------------------------------- roles: bo'lim ruxsati

def test_section_allowed_with_explicit_list(bot_state):
    bot_users.record_user(556)
    bot_users.set_pages(556, ["overview"])
    assert roles.section_allowed(556, "overview", role=Role.DISPATCHER) is True
    assert roles.section_allowed(556, "vehicles", role=Role.DISPATCHER) is False
    # ADMIN har doim ruxsatli.
    assert roles.section_allowed(556, "vehicles", role=Role.ADMIN) is True
    # Bo'limsiz so'rov (bo'sh kalit) — ruxsat.
    assert roles.section_allowed(556, "", role=Role.DISPATCHER) is True


def test_section_allowed_unrestricted(bot_state):
    bot_users.record_user(557)
    assert roles.section_allowed(557, "vehicles", role=Role.DISPATCHER) is True
    # bot_users'da umuman yo'q chat — cheklov yo'q (rol bo'yicha).
    assert roles.section_allowed(778899, "vehicles", role=Role.VIEWER) is True


def test_pages_for_prefers_bot_over_dashboard(bot_state, storage, monkeypatch):
    """Bot ro'yxati panel akkaunti ro'yxatidan ustun; panel bog'lanishi ham ishlaydi."""
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage",
                        lambda: storage)
    digest, salt = dash_deps.hash_password("x")
    uid = storage.dashboard_user_add(
        "ali", digest, salt, role="DISPATCHER", company="XTEST", active=1,
        data=json.dumps({"pages": ["overview"], "telegram_chat_id": "600"}))
    assert roles.pages_for(600) == ["overview"]

    bot_users.record_user(600)
    bot_users.set_pages(600, ["routes"])
    assert roles.pages_for(600) == ["routes"]

    # Panel ro'yxati olib tashlansa va bot ro'yxati bo'lmasa — cheklov yo'q.
    bot_users.set_pages(600, None)
    storage.dashboard_user_update(
        row_id=uid, data=json.dumps({"telegram_chat_id": "600"}))
    assert roles.pages_for(600) is None

    # Faol bo'lmagan panel akkaunti hisobga olinmaydi.
    storage.dashboard_user_update(
        row_id=uid, active=0,
        data=json.dumps({"pages": ["overview"], "telegram_chat_id": "600"}))
    assert roles.pages_for(600) is None


# ---------------------------------------------------- dispatch: buyruqlar

def test_dispatch_blocks_command_outside_list(bot_state, monkeypatch):
    bot_users.record_user(700, first_name="Ali")
    bot_users.set_pages(700, ["overview"])
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DISPATCHER)
    monkeypatch.setattr(dispatch.render, "vehicles", lambda f=None: ("OK", {}))
    sent = _sent(monkeypatch)

    dispatch.handle_message(700, "/vehicles")
    assert sent and sent[-1][1] == sections.section_denied_text("vehicles")
    assert "Avtobuslar" in sent[-1][1]

    # Ro'yxatdan tashqarida bo'lgan navigatsiya buyrug'i ham to'siladi.
    dispatch.handle_message(700, "🚌 Avtobuslar")
    assert sent[-1][1] == sections.section_denied_text("vehicles")


def test_dispatch_allows_listed_command(bot_state, monkeypatch):
    bot_users.record_user(701)
    bot_users.set_pages(701, ["vehicles"])
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DISPATCHER)
    monkeypatch.setattr(dispatch.render,
                        "vehicles", lambda f=None: ("AVTOBUSLAR", {}))
    sent = _sent(monkeypatch)
    dispatch.handle_message(701, "/vehicles")
    assert sent and sent[-1][1] == "AVTOBUSLAR"


def test_dispatch_unrestricted_user_unaffected(bot_state, monkeypatch):
    """Ro'yxat belgilanmagan foydalanuvchi avvalgidek ishlaydi."""
    bot_users.record_user(702)
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DISPATCHER)
    monkeypatch.setattr(dispatch.render,
                        "vehicles", lambda f=None: ("AVTOBUSLAR", {}))
    sent = _sent(monkeypatch)
    dispatch.handle_message(702, "/vehicles")
    assert sent and sent[-1][1] == "AVTOBUSLAR"


def test_dispatch_admin_bypasses_list(bot_state, monkeypatch):
    bot_users.record_user(703)
    bot_users.set_pages(703, ["overview"])
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(dispatch.render,
                        "vehicles", lambda f=None: ("AVTOBUSLAR", {}))
    sent = _sent(monkeypatch)
    dispatch.handle_message(703, "/vehicles")
    assert sent and sent[-1][1] == "AVTOBUSLAR"


def test_callback_nav_blocked_by_list(bot_state, monkeypatch):
    bot_users.record_user(704)
    bot_users.set_pages(704, ["overview"])
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DISPATCHER)
    monkeypatch.setattr(dispatch.context, "filters_for", lambda cid: {})
    monkeypatch.setattr(dispatch.render, "vehicles", lambda f=None: ("OK", {}))
    sent = _sent(monkeypatch)
    dispatch.handle_callback(704, {"id": 1}, "nav:vehicles")
    assert sent and sent[-1][1] == sections.section_denied_text("vehicles")
    # Ro'yxatdagi bo'lim (overview) esa o'tadi.
    dispatch.handle_callback(704, {"id": 2}, "nav:dashboard")
    assert sent[-1][1] != sections.section_denied_text("overview")


# ---------------------------------------------- reyting: moliyaviy ko'rsatkich

class _FakeMet:
    def drivers(self, _f):
        return []


def test_leaderboard_hides_net_from_non_salary(monkeypatch):
    monkeypatch.setattr(render, "_met", lambda: _FakeMet())
    monkeypatch.setattr(roles, "resolve_role", lambda cid: Role.DISPATCHER)
    text, _kb = render.leaderboard({}, metric="net", chat_id=555)
    assert "Netto" not in text
    assert "Masofa" in text


def test_leaderboard_net_for_admin(monkeypatch):
    monkeypatch.setattr(render, "_met", lambda: _FakeMet())
    monkeypatch.setattr(roles, "resolve_role", lambda cid: Role.ADMIN)
    text, _kb = render.leaderboard({}, metric="net", chat_id=555)
    assert "Netto" in text


def test_dispatch_top_net_denied_without_salary(bot_state, monkeypatch):
    bot_users.record_user(705)
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DISPATCHER)
    sent = _sent(monkeypatch)
    dispatch.handle_message(705, "/top net")
    from bm_automation.app.notifications.ops.text import DENIED_TEXT
    assert sent and sent[-1][1] == DENIED_TEXT


# ------------------------------------------------------------ menyu filtri

def test_menu_hides_blocked_sections(bot_state, monkeypatch):
    bot_users.record_user(710)
    bot_users.set_pages(710, ["overview", "settings"])
    monkeypatch.setattr(roles, "resolve_role", lambda cid: Role.DISPATCHER)
    kb_ = kb.main_menu_kb(710)
    flat = [btn for row in kb_["keyboard"] for btn in row]
    assert "📊 Dashboard" in flat
    assert "⚙️ Settings" in flat
    assert "🚌 Avtobuslar" not in flat
    assert "🏆 Reyting" not in flat
    assert "📋 Reyslar" not in flat


def test_menu_unchanged_without_list(bot_state, monkeypatch):
    bot_users.record_user(711)
    monkeypatch.setattr(roles, "resolve_role", lambda cid: Role.DISPATCHER)
    kb_ = kb.main_menu_kb(711)
    flat = [btn for row in kb_["keyboard"] for btn in row]
    assert "🚌 Avtobuslar" in flat
    assert "🏆 Reyting" in flat
