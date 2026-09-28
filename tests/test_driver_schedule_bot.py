"""dsched callback (shaxsiy grafik kartasi) va guruh rejimi testlari."""

import datetime

import pytest

from bm_automation.app.notifications.ops import dispatch, kb, ops
from bm_automation.app.notifications.ops.roles import Role
from bm_automation.app.notifications import telegram as tg
from bm_automation.app.exporters import sheet_image
from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "sched.db"))
    return storage_for(db)


# ----------------------------------------------------------- driver_schedule_kb

def test_driver_schedule_kb_layout():
    mk = kb.driver_schedule_kb("d1")
    rows = mk["inline_keyboard"]
    datas = [b["callback_data"] for row in rows for b in row]
    assert "dsched:d1:1" in datas          # ertaga (asosiy tugma)
    assert "dsched:d1:0" in datas          # bugun
    assert "dsched:d1:-1" in datas         # kechagi
    texts = [b["text"] for row in rows for b in row]
    assert any("Grafikim" in t for t in texts)


def test_cb_rejects_overlong_data():
    """Telegram callback_data 64 bayt chegarasi — _cb kesmaydi, xato ko'taradi."""
    long_id = "x" * 80
    with pytest.raises(ValueError):
        kb._cb("d:", long_id)
    with pytest.raises(ValueError):
        kb.driver_schedule_kb(long_id)
    with pytest.raises(ValueError):
        kb.driver_card_kb(long_id)
    # qisqa id — ishlaydi va kesilmaydi
    data = kb._cb("dsched:", "d1", ":1")
    assert data == "dsched:d1:1"


def test_cb_byte_aware_limit():
    """Limit bayt bo'yicha — kirill belgilar (2 bayt) ham to'g'ri sanaladi."""
    long_cyr = "Й" * 34  # 34 × 2 = 68 bayt > 64
    with pytest.raises(ValueError):
        kb._cb("d:", long_cyr)
    ok = "Й" * 31  # 62 bayt — sig'adi
    assert len(kb._cb("d:", ok).encode("utf-8")) <= 64


def test_dsched_callback_parses_offset(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "_send_driver_schedule",
                        lambda did, cid, f, day_offset=1:
                        calls.append((did, cid, day_offset)))
    monkeypatch.setattr(dispatch, "answer", lambda *a, **k: None)
    dispatch.handle_callback(111, {"id": "q"}, "dsched:abc:0")
    assert calls == [("abc", 111, 0)]
    dispatch.handle_callback(111, {"id": "q"}, "dsched:abc:-1")
    assert calls[-1] == ("abc", 111, -1)


def test_dsched_callback_default_offset(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "_send_driver_schedule",
                        lambda did, cid, f, day_offset=1:
                        calls.append((did, cid, day_offset)))
    monkeypatch.setattr(dispatch, "answer", lambda *a, **k: None)
    dispatch.handle_callback(111, {"id": "q"}, "dsched:abc")
    assert calls == [("abc", 111, 1)]


def test_dsched_bad_offset_falls_back(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "_send_driver_schedule",
                        lambda did, cid, f, day_offset=1:
                        calls.append((did, cid, day_offset)))
    monkeypatch.setattr(dispatch, "answer", lambda *a, **k: None)
    dispatch.handle_callback(111, {"id": "q"}, "dsched:abc:xx")
    assert calls == [("abc", 111, 1)]


def test_dsched_denied_for_other_driver(monkeypatch):
    """Haydovchi boshqa haydovchi grafigini ola olmaydi."""
    from bm_automation.app.notifications.ops.text import DENIED_TEXT
    sent = []
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, *a, **k: sent.append(a[0]))
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.DRIVER)
    monkeypatch.setattr(dispatch, "driver_id_for_chat", lambda cid: "other")
    dispatch._send_driver_schedule("someone-else", 555, {})
    assert DENIED_TEXT in sent[0]


def test_dsched_personal_cache_priority(monkeypatch, tmp_path):
    """Keshlangan shaxsiy karta bo'lsa darhol yuboriladi (1-prioritet)."""
    import pathlib
    # Kesh: bugun va ertaga uchun personal rasm yaratamiz
    today = datetime.date.today()
    personal_dir = tmp_path / "personal"
    personal_dir.mkdir()
    tomorrow = today + datetime.timedelta(days=1)
    png_t = personal_dir / f"personal_abc12345_{tomorrow:%Y%m%d}_deadbeef.png"
    png_t.write_bytes(b"png")
    png_b = personal_dir / f"personal_abc12345_{today:%Y%m%d}_deadbeef.png"
    png_b.write_bytes(b"png")

    sent = []
    monkeypatch.setattr(dispatch, "get_storage",
                        lambda: type("S", (), {"enabled": True})())
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(tg, "send_photo",
                        lambda path, caption="", chat_id=None, **kw:
                        sent.append((path, caption)) or True)

    # funksiya ichidagi _P("reports", "personal") — modul globals'ida
    # bo'lsa shuni ishlatadi, aks holda pathlib.Path:
    monkeypatch.setattr(dispatch, "_P",
                        lambda *parts: personal_dir, raising=False)
    dispatch._send_driver_schedule("deadbeef-1234", 111, {}, day_offset=1)
    assert sent, "keshdagi personal karta yuborilishi kerak"
    assert sent[0][0].endswith(png_t.name)   # ertangi karta


def test_dsched_generates_from_schedules(monkeypatch, tmp_path, storage):
    """schedules jadvalidan shaxsiy karta generatsiya qilinadi (2-prioritet)."""
    from bm_automation.app.db.storage import get_storage
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    storage.save_schedule(
        date=tomorrow.isoformat(), route_id="r1", graph_name="P3",
        driver_id="dead1", vehicle_id="60A123BC", shift_name="ISM",
        start_time="06:13", end_time="20:37", trip_count=16)
    storage.save_driver(external_id="dead1", full_name="MATISAYEV MURODJON")

    generated = []
    monkeypatch.setattr(dispatch, "get_storage", lambda: storage)
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(sheet_image, "make_personal_sheet_image",
                        lambda row, out, **kw: generated.append(row) or out)
    monkeypatch.setattr(tg, "send_photo",
                        lambda path, caption="", chat_id=None, **kw:
                        generated.append(("sent", path)) or True)
    monkeypatch.setattr(dispatch, "_P",
                        lambda *parts: tmp_path.joinpath(*parts),
                        raising=False)
    dispatch._send_driver_schedule("dead1", 111, {}, day_offset=1)
    assert any(isinstance(x, tuple) and x[0] == "sent" for x in generated), \
        "schedules dan karta chizilishi kerak"
    row = generated[0]
    assert row["graph"] == "P3" and row["bus"] == "60A123BC"
    assert row["driver"] == "MATISAYEV MURODJON"


def test_dsched_no_data_replies_not_found(monkeypatch, tmp_path, storage):
    """Grafik topilmasa 'topilmadi' xabari chiqadi."""
    sent = []
    monkeypatch.setattr(dispatch, "get_storage", lambda: storage)
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    from bm_automation.app.core import profiles as profiles_mod
    monkeypatch.setattr(profiles_mod, "all_profiles", lambda: [])
    monkeypatch.setattr(dispatch, "reply",
                        lambda cid, *a, **k: sent.append(a[0]))
    monkeypatch.setattr(dispatch, "_P",
                        lambda *parts: tmp_path.joinpath(*parts),
                        raising=False)
    dispatch._send_driver_schedule("nobody", 111, {}, day_offset=1)
    assert "GRAFIK TOPILMADI" in sent[0]


# --------------------------------------------------------------- guruh rejimi

def _group_msg(text, chat_id=-1001234567, from_id=555):
    return {"message": {"chat": {"id": chat_id, "type": "supergroup"},
                        "text": text,
                        "from": {"id": from_id, "first_name": "Test",
                                 "username": "user"}}}


def _private_msg(text, chat_id=111):
    return {"message": {"chat": {"id": chat_id, "type": "private"},
                        "text": text}}


def test_is_group_detects_supergroup():
    assert ops._is_group(_group_msg("salom"))
    assert not ops._is_group(_private_msg("salom"))


def test_group_silent_without_mention(monkeypatch):
    """Guruhda oddiy xabar — bot JIM (hech narsa ishlanmaydi)."""
    handled = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: handled.append("u"))
    monkeypatch.setattr(ops.dispatch, "handle_message",
                        lambda cid, text: handled.append(text))
    ops._handle_update(_group_msg("bugun havo chiroyli"))
    assert not handled, "guruhda eslatmasiz xabarga javob bo'lmasligi kerak"


def test_group_plain_message_still_recorded(monkeypatch):
    """Bug 4: guruhdagi oddiy xabar ham group_stats ga yoziladi.

    Ilgari `record_group_message` faqat @bot eslatma//buyruq bo'lganida
    chaqirilar edi — panel faqat botga murojaatlarni sanar edi.
    """
    recorded = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_group_message",
                        lambda *a, **k: recorded.append((a, k)))
    monkeypatch.setattr(ops.dispatch, "handle_message",
                        lambda cid, text: None)
    ops._handle_update(_group_msg("bugun havo chiroyli"))
    assert len(recorded) == 1, "oddiy guruh xabari statistika fayliga yoziladi"
    upd = _group_msg("bugun havo chiroyli")
    sender = ((upd.get("message") or {}).get("from") or {}).get("id")
    assert recorded[0][0][2].get("id") == sender


def test_group_edited_message_recorded(monkeypatch):
    """Bug 4: tahrirlangan guruh xabari ham statistikaga tushadi."""
    recorded = []
    upd = {"edited_message": {
        "chat": {"id": -100999, "type": "group", "title": "Reys"},
        "text": "19:00 da ketadi",
        "from": {"id": 601, "first_name": "D", "username": "disp"},
    }}
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_group_message",
                        lambda *a, **k: recorded.append(True))
    monkeypatch.setattr(ops.dispatch, "handle_message",
                        lambda cid, text: None)
    ops._handle_update(upd)
    assert recorded == [True]


def test_group_command_processed(monkeypatch):
    """Guruhda admin /buyruq yuborsa — qisqa javob (dispatch EMAS)."""
    sent = []
    handled = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(ops.dispatch, "handle_message",
                        lambda cid, text: handled.append(text))
    monkeypatch.setattr(ops, "send_message",
                        lambda text, **k: sent.append(text))
    monkeypatch.setattr(ops.context, "filters_for", lambda cid: {})
    from bm_automation.app.notifications.ops import render as _render_mod
    monkeypatch.setattr(_render_mod, "today_short",
                        lambda f, **k: "QISQA TODAY")
    ops._handle_update(_group_msg("/today"))
    assert handled == [], "guruhda to'liq dispatch ishlamaydi"
    assert any("QISQA TODAY" in s for s in sent)


def test_group_command_regular_member_silent(monkeypatch):
    """Guruhda oddiy a'zo /buyruq yuborsa — bot JIM."""
    sent = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.VIEWER)
    monkeypatch.setattr(ops, "send_message",
                        lambda text, **k: sent.append(text))
    ops._handle_update(_group_msg("/today"))
    assert not sent, "oddiy a'zoga javob bo'lmasligi kerak"


def test_group_mention_processed(monkeypatch):
    """Guruhda dispetcher @bot eslatmasi bilan xabar yuborsa — javob."""
    sent = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.DISPATCHER)
    monkeypatch.setattr(ops, "send_message",
                        lambda text, **k: sent.append(text))
    ops._handle_update(_group_msg("@testbot bugungi reyslar?"))
    assert sent, "dispetcher eslatmasiga javob bo'lishi kerak"


def test_group_reply_to_bot_processed(monkeypatch):
    """Guruhda manager bot xabariga javob yuborsa — e'tiborga olinadi."""
    sent = []
    upd = {"message": {
        "chat": {"id": -100999, "type": "group"},
        "text": "yaxshi, rahmat",
        "from": {"id": 556, "first_name": "M", "username": "mgr"},
        "reply_to_message": {"from": {"is_bot": True, "id": 42}},
    }}
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **k: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.MANAGER)
    monkeypatch.setattr(ops, "send_message",
                        lambda text, **k: sent.append(text))
    ops._handle_update(upd)
    assert sent, "manager bot javobiga javob qaytarilishi kerak"


def test_private_always_processed(monkeypatch):
    """Shaxsiy chatda eslatma talab qilinmaydi."""
    handled = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops.dispatch, "handle_message",
                        lambda cid, text: handled.append(text))
    ops._handle_update(_private_msg("salom, bugun qanday?"))
    assert handled == ["salom, bugun qanday?"]


def test_new_user_notification_skipped_in_group(monkeypatch):
    """Guruhda yangi foydalanuvchi haqida adminlarga xabar yuborilmaydi."""
    notified = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "get_user", lambda cid: None)  # is_new=True
    monkeypatch.setattr(ops, "_notify_new_user",
                        lambda cid: notified.append(cid))
    ops._handle_update(_group_msg("/start"))
    assert not notified


def test_new_user_notification_named_private(monkeypatch):
    """Ismli yangi shaxsiy foydalanuvchi — adminlarga ogohlantirish."""
    notified = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "get_user", lambda cid: None)  # is_new=True
    monkeypatch.setattr(ops, "_notify_new_user",
                        lambda cid: notified.append(cid))
    upd = _private_msg("/start", chat_id=123)
    upd["message"]["from"] = {"id": 123, "first_name": "Ali",
                              "username": "ali"}
    ops._handle_update(upd)
    assert notified == [123]


def test_new_user_notification_skipped_technical(monkeypatch):
    """Nomsiz/texnik update (kanal posti, bot holati) — ogohlantirish YO'Q."""
    notified = []
    monkeypatch.setattr(ops, "_bot_username_cached", lambda: "testbot")
    monkeypatch.setattr(ops, "record_user", lambda *a, **k: None)
    monkeypatch.setattr(ops, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(ops, "is_allowed", lambda cid: True)
    monkeypatch.setattr(ops, "get_user", lambda cid: None)  # is_new=True
    monkeypatch.setattr(ops, "_notify_new_user",
                        lambda cid: notified.append(cid))

    # Kanal posti — nomli from yo'q, faqat sender_chat.
    channel = {"channel_post": {
        "chat": {"id": -100987, "type": "channel", "title": "Kanal"},
        "sender_chat": {"id": -100987, "title": "Kanal"},
        "text": "yangilik",
    }}
    ops._handle_update(channel)
    assert not notified, "kanal postidan ogohlantirish bo'lmasligi kerak"

    # Bot holati o'zgarishi — shaxsiy from yo'q.
    mcm = {"my_chat_member": {
        "chat": {"id": -100987, "type": "channel", "title": "Kanal"},
        "from": {"id": 999, "first_name": ""},
        "old_chat_member": {"status": "member"},
        "new_chat_member": {"status": "administrator"},
    }}
    ops._handle_update(mcm)
    assert not notified, "bot holati update'dan ogohlantirish bo'lmasligi kerak"
