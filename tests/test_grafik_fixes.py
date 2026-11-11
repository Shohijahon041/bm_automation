"""Kunlik grafik tasdiqlash uchun xavfsizlik va barqarorlik testlari."""

from datetime import date
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import grafik_poll
from bm_automation.app.dashboard.fastapi_app import app
from tests.sqlite_backend import SQLiteDatabase

DAY = "2026-11-12"


def _storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "poll_fixes.db"))
    st = storage_for(db)
    st.save_route("r1", name="B-80")
    st.save_route("r2", name="B-28")
    return st


def test_build_poll_preserves_existing_on_empty_fetch(tmp_path, monkeypatch):
    """BM API vaqtincha bo'sh yoki xato qaytarsa, mavjud grafik va tasdiqlar saqlanadi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    # 1. Dastlabki grafik va tasdiqlash
    initial_graphs = [
        {"graphName": "P1", "driverId": "did1", "driverName": "NISHANOV M.", "plateNum": "40 111 AA"},
        {"graphName": "P2", "driverId": "did2", "driverName": "KARIMOV B.", "plateNum": "40 222 BB"},
    ]
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=initial_graphs, route_name="B-80")
    poll["items"][0]["confirmed"] = True
    poll["items"][0]["confirmed_by_name"] = "Muxtor"
    grafik_poll.save_poll(poll)

    # 2. Keyingi tekshiruvda _fetch_duty_graphs bo'sh qaytsa
    monkeypatch.setattr(grafik_poll, "_fetch_duty_graphs", lambda r, d: [])

    poll_reloaded = grafik_poll.build_poll_data("r1", DAY, graphs=None, route_name="B-80")
    assert len(poll_reloaded["items"]) == 2
    assert poll_reloaded["items"][0]["confirmed"] is True
    assert poll_reloaded["items"][0]["confirmed_by_name"] == "Muxtor"


def test_confirm_by_name_when_driver_id_empty_in_duty(tmp_path, monkeypatch):
    """BM API duty da driverId bo'sh bo'lsa ham, ism mos kelsa tasdiqlash ishlaydi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)

    # Haydovchi bazada mavjud
    st.save_driver("did_rahimov", full_name="RAXIMOV SOBIRJON", tin="123456789")
    st.link_driver_telegram("did_rahimov", 999333)

    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    # BM API duty jadvalida driverId bo'sh kelgan
    duty_graphs = [
        {"graphName": "1", "driverId": "", "driverName": "RAXIMOV SOBIRJON", "plateNum": "40 999 AA"},
    ]
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=duty_graphs, route_name="B-80")
    assert poll["items"][0]["confirmed"] is False

    # 1. Tezkor "gme" bilan tasdiqlash
    cq_gme = {
        "id": "cq_name_1",
        "from": {"id": 999333, "first_name": "Sobirjon", "last_name": "Raximov"},
    }
    grafik_poll.handle_group_callback("-100", cq_gme, "gme:r1_20261112")
    poll_after = grafik_poll.get_poll("r1_20261112")
    assert poll_after["items"][0]["confirmed"] is True
    assert poll_after["items"][0]["driver_id"] == "did_rahimov"


def test_api_grafik_poll_confirm_and_reset(tmp_path, monkeypatch):
    """FastAPI orqali /api/grafik-poll/confirm va /api/grafik-poll/reset ishlashi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    graphs = [
        {"graphName": "1", "driverId": "d1", "driverName": "ALIEV A.", "plateNum": "40 111 AA"},
    ]
    grafik_poll.build_poll_data("r1", DAY, graphs=graphs, route_name="B-80")

    client = TestClient(app, cookies={"bm_session": "test_sess"})
    from bm_automation.app.dashboard import deps

    async def fake_resolve(req, creds=None):
        return {
            "user_id": 1, "username": "dispetcher_test", "role": "ADMIN",
            "is_admin": True, "company_id": None, "company": None
        }

    monkeypatch.setattr(deps, "resolve_scope", fake_resolve)

    # 1. Confirm
    res_conf = client.post("/api/grafik-poll/confirm", json={
        "routeVariantId": "r1", "date": DAY, "graph": "1"
    })
    assert res_conf.status_code == 200
    assert res_conf.json()["ok"] is True

    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is True
    assert "dispetcher_test" in poll["items"][0]["confirmed_by_name"]

    # 2. Reset
    res_reset = client.post("/api/grafik-poll/reset", json={
        "routeVariantId": "r1", "date": DAY, "graph": "1"
    })
    assert res_reset.status_code == 200
    assert res_reset.json()["ok"] is True

    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is False


def test_dispatch_commands_with_route_filter(tmp_path, monkeypatch):
    """Dispetcher buyruqlarida yo'nalish ko'rsatilganda faqat o'sha yo'nalishga ta'sir qilishi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    from bm_automation.app.notifications.ops import dispatch
    from bm_automation.app.notifications.ops.roles import Role

    # 2 ta alohida yo'nalish
    grafik_poll.build_poll_data("r1", DAY, graphs=[{"graphName": "1", "driverName": "Haydovchi 1"}], route_name="B-80")
    grafik_poll.build_poll_data("r2", DAY, graphs=[{"graphName": "1", "driverName": "Haydovchi 2"}], route_name="B-28")

    replies = []
    monkeypatch.setattr(dispatch, "reply", lambda cid, text: replies.append(text))
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(dispatch, "can", lambda r, p: True)

    from bm_automation.app.core import profiles
    monkeypatch.setattr(profiles, "all_profiles", lambda: [
        {"routeVariantId": "r1", "routeName": "B-80"},
        {"routeVariantId": "r2", "routeName": "B-28"},
    ])

    # Faqat r1 (B-80) dagi Grafik 1 ni tasdiqlash
    # Buyruq: /sorov_tasdiq 1 B-80 2026-11-12
    dispatch.handle_message(12345, f"/sorov_tasdiq 1 B-80 {DAY}")

    poll1 = grafik_poll.get_poll("r1_20261112")
    poll2 = grafik_poll.get_poll("r2_20261112")

    assert poll1["items"][0]["confirmed"] is True
    assert poll2["items"][0]["confirmed"] is False  # r2 tasdiqlanmasdan qolishi kerak!


def test_all_confirmed_triggers_schedule_image_and_message(tmp_path, monkeypatch):
    """Barcha haydovchilar tasdiqlaganda rasm va yakuniy xabarni guruhlarga yuboradi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    # So'rovnomada 2 ta haydovchi
    duty_graphs = [
        {"graphName": "1", "driverId": "did1", "driverName": "ALIEV A.", "plateNum": "40 111 AA", "startTime": "06:00", "endTime": "19:00"},
        {"graphName": "2", "driverId": "did2", "driverName": "VALIEV V.", "plateNum": "40 222 BB", "startTime": "06:15", "endTime": "19:15"},
    ]
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=duty_graphs, route_name="B-80")
    poll["chat_messages"] = [{"chat_id": "-100123", "message_id": 55}]
    grafik_poll.save_poll(poll)

    photos_sent = []
    messages_sent = []
    from bm_automation.app.notifications import telegram

    def fake_send_photo(file_path, caption="", chat_id=None, **kw):
        photos_sent.append({"file": file_path, "caption": caption, "chat_id": chat_id})
        return True

    def fake_send_message(text, chat_id=None, **kw):
        messages_sent.append({"text": text, "chat_id": chat_id})
        return True

    monkeypatch.setattr(telegram, "send_photo", fake_send_photo)
    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    # 1. Faqat 1-haydovchi tasdiqlaydi (hali barchasi emas)
    grafik_poll.confirm_by_admin("r1", DAY, "1")
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is True
    assert poll["items"][1]["confirmed"] is False
    assert not poll.get("all_confirmed_sent")
    assert len(photos_sent) == 0

    # 2. 2-haydovchi ham tasdiqlaydi -> BARCHASI tasdiqlandi!
    grafik_poll.confirm_by_admin("r1", DAY, "2")
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is True
    assert poll["items"][1]["confirmed"] is True
    assert poll.get("all_confirmed_sent") is True

    # Rasm yuborilganini tekshiramiz
    assert len(photos_sent) == 1
    assert photos_sent[0]["chat_id"] == "-100123"
    assert "BARCHA SHAFYORLAR XABAR TOPDI" in photos_sent[0]["caption"]
    assert Path(photos_sent[0]["file"]).exists()

    # 3. Allaqachon yuborilgan bo'lsa, qayta yuborilmaydi (takrorlanishdan himoya)
    grafik_poll._check_and_notify_all_confirmed(poll)
    assert len(photos_sent) == 1

    # 4. Agar bittasi bekor qilinsa, all_confirmed_sent qayta ochiladi
    grafik_poll.reset_confirmation("r1", DAY, "2")
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll.get("all_confirmed_sent") is False


def test_cross_group_callback_protection(tmp_path, monkeypatch):
    """Boshqa (noto'g'ri) guruhdan kelgan callback query rad etiladi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    graphs = [{"graphName": "1", "driverId": "did1", "driverName": "ALIEV A.", "plateNum": "40 111 AA"}]
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=graphs, route_name="B-80")
    # Faqat -100111 guruhiga biriktirilgan
    poll["chat_messages"] = [{"chat_id": "-100111", "message_id": 10}]
    grafik_poll.save_poll(poll)

    answers = []
    def fake_answer(cq_id, text, alert=False):
        answers.append({"text": text, "alert": alert})

    monkeypatch.setattr(grafik_poll, "_answer_cq", fake_answer)

    # Noto'g'ri guruh (-100999) dan callback bosildi
    cq = {
        "id": "cq_foreign_group",
        "from": {"id": 12345, "first_name": "Test"},
    }
    grafik_poll.handle_group_callback("-100999", cq, "gck:r1_20261112:0")

    assert len(answers) == 1
    assert "tegishli emas" in answers[0]["text"]
    assert answers[0]["alert"] is True

    # Poll tasdiqlanmasdan qolgan
    poll_after = grafik_poll.get_poll("r1_20261112")
    assert poll_after["items"][0]["confirmed"] is False


def test_group_command_route_filtering(tmp_path, monkeypatch):
    """Guruh ichida /sorov_status yoki /sorov_tasdiq faqat shu guruh yo'nalishini ko'radi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    from bm_automation.app.notifications.ops import dispatch
    from bm_automation.app.notifications.ops.roles import Role
    from bm_automation.app.core import profiles

    # r1 -> -100111 (B-80)
    # r2 -> -100222 (B-28)
    monkeypatch.setattr(profiles, "all_profiles", lambda: [
        {"routeVariantId": "r1", "routeName": "B-80"},
        {"routeVariantId": "r2", "routeName": "B-28"},
    ])
    from bm_automation.app.notifications.ops import group_departures
    monkeypatch.setattr(group_departures, "_route_chats", lambda: {
        "r1": ["-100111"],
        "r2": ["-100222"],
    })

    grafik_poll.build_poll_data("r1", DAY, graphs=[{"graphName": "1", "driverName": "H1"}], route_name="B-80")
    grafik_poll.build_poll_data("r2", DAY, graphs=[{"graphName": "1", "driverName": "H2"}], route_name="B-28")

    replies = []
    monkeypatch.setattr(dispatch, "reply", lambda cid, text: replies.append((cid, text)))
    monkeypatch.setattr(dispatch, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(dispatch, "can", lambda r, p: True)

    # 1. -100111 guruhida /sorov_status buyrug'i: faqat B-80 chiqishi kerak
    dispatch.handle_message("-100111", f"/sorov_status {DAY}")
    assert len(replies) == 1
    assert "B-80" in replies[0][1]
    assert "B-28" not in replies[0][1]

    # 2. -100111 guruhida turib, boshqa yo'nalishni (B-28) tasdiqlamoqchi bo'lsa - rad etiladi
    replies.clear()
    dispatch.handle_message("-100111", f"/sorov_tasdiq 1 B-28 {DAY}")
    assert any("faqat unga biriktirilgan" in r[1] for r in replies)

    # r2 o'zgarishsiz tasdiqlanmay qolgan
    poll2 = grafik_poll.get_poll("r2_20261112")
    assert poll2["items"][0]["confirmed"] is False
