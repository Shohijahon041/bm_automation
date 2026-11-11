"""Kunlik grafik so'rovnomasi (grafik_poll) testlari."""

from datetime import date
from pathlib import Path

from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import grafik_poll
from tests.sqlite_backend import SQLiteDatabase

DAY = "2026-11-12"


def _storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "poll.db"))
    st = storage_for(db)
    st.save_route("r1", name="B-80")
    return st


def _graphs_sample():
    return [
        {
            "graphName": "P1",
            "driverId": "did1",
            "driverName": "NISHANOV MUXTORXON ANVAROVICH",
            "plateNum": "40 123 AAA",
            "startTime": "06:15",
            "endTime": "20:30",
        },
        {
            "graphName": "P2",
            "driverId": "did2",
            "driverName": "KARIMOV BOTIR AKBAROVICH",
            "plateNum": "40 456 BBB",
            "startTime": "06:30",
            "endTime": "21:00",
        },
    ]


def test_compact_driver_name():
    assert grafik_poll.compact_driver_name("NISHANOV MUXTORXON ANVAROVICH") == "Nishanov M."
    assert grafik_poll.compact_driver_name("ALIYEV VALI") == "Aliyev V."
    assert grafik_poll.compact_driver_name("") == "Haydovchi"


def test_build_and_render_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    poll = grafik_poll.build_poll_data(
        "r1", DAY, graphs=_graphs_sample(), route_name="B-80"
    )

    assert poll["poll_id"] == "r1_20261112"
    assert poll["route_name"] == "B-80"
    assert len(poll["items"]) == 2
    assert poll["items"][0]["graph"] == "P1"
    assert poll["items"][0]["confirmed"] is False

    text = grafik_poll.render_poll_text(poll)
    assert "KUNLIK GRAFIKNI TASDIQLASH (SO'ROVNOMA)" in text
    assert "B-80" in text
    assert "0 / 2 ta" in text
    assert "Nishanov M." in text
    assert "Karimov B." in text

    kb = grafik_poll.render_poll_keyboard(poll)
    buttons = kb["inline_keyboard"]
    # 1-qator: tezkor
    assert buttons[0][0]["text"] == "⚡️ O'z grafikimni tasdiqlash"
    assert buttons[0][0]["callback_data"].startswith("gme:")
    # 2-qator: 2 ta haydovchi
    assert len(buttons[1]) == 2
    assert "P1: Nishanov M." in buttons[1][0]["text"]
    assert "P2: Karimov B." in buttons[1][1]["text"]
    # Callback data Telegram limitidan (64 bayt) oshmasligi shart
    for row in buttons:
        for btn in row:
            if "callback_data" in btn:
                assert len(btn["callback_data"].encode("utf-8")) <= 64
            if "url" in btn:
                assert btn["url"].startswith("https://t.me/")


def test_confirm_item_and_re_render(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)
    st.link_driver_telegram("did1", 777111)

    telegram_calls = []

    def mock_call(method, payload=None, **kwargs):
        telegram_calls.append((method, payload))
        if method == "sendMessage":
            return {"message_id": 999}
        return {"ok": True}

    monkeypatch.setattr(grafik_poll, "telegram_call", mock_call)

    # Guruhga yuborish
    send_res = grafik_poll.send_poll_to_groups(
        "r1", DAY, graphs=_graphs_sample(), route_name="B-80", specific_chat_id="-100123"
    )
    assert send_res["sent"] == 1
    assert send_res["items_count"] == 2

    # 1-haydovchi (Nishanov) o'z grafigini bosadi (gck)
    cq = {
        "id": "cq1",
        "from": {
            "id": 777111,
            "first_name": "Muxtorxon",
            "last_name": "Nishanov",
            "username": "muxtor",
        },
    }
    grafik_poll.handle_group_callback("-100123", cq, "gck:r1_20261112:0")

    # Qayta tekshiramiz
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is True
    assert poll["items"][0]["confirmed_by_id"] == 777111
    assert poll["items"][0]["confirmed_by_name"] == "Muxtorxon Nishanov"

    # Matn va klaviatura yangilanishi
    text = grafik_poll.render_poll_text(poll)
    assert "1 / 2 ta" in text
    assert "🟢" in text
    assert "tasdiqladi" in text

    kb = grafik_poll.render_poll_keyboard(poll)
    unconfirmed_btns = [
        btn["text"] for row in kb["inline_keyboard"] for btn in row if "P1:" in btn["text"]
    ]
    assert len(unconfirmed_btns) == 0  # P1 tasdiqlangani uchun tugmasi olib tashlangan


def test_quick_confirm_gme(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)

    # Karimov (did2) ning telegram_chat_id si bazada mavjud
    st.save_driver_profile("did2", telegram_chat_id="888222")

    telegram_calls = []
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    # Karimov "⚡️ O'z grafikimni tasdiqlash" tugmasini bosadi
    cq = {
        "id": "cq2",
        "from": {
            "id": 888222,
            "first_name": "Botir",
            "last_name": "Karimov",
        },
    }
    grafik_poll.handle_group_callback("-100123", cq, "gme:r1_20261112")

    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][1]["confirmed"] is True
    assert poll["items"][1]["confirmed_by_id"] == 888222


def test_reset_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    poll = grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    poll["items"][0]["confirmed"] = True
    poll["items"][0]["confirmed_by_name"] = "Muxtor"
    grafik_poll.save_poll(poll)

    res = grafik_poll.reset_confirmation("r1", DAY, "P1")
    assert res is True

    updated = grafik_poll.get_poll("r1_20261112")
    assert updated["items"][0]["confirmed"] is False
    assert updated["items"][0]["confirmed_by_name"] == ""


def test_ops_group_update_and_commands(tmp_path, monkeypatch):
    from bm_automation.app.notifications.ops import ops
    from bm_automation.app.notifications.ops.roles import Role
    import bm_automation.app.notifications.ops.group_departures as gd

    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)
    st.link_driver_telegram("did1", 12345)
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **kw: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(gd, "_route_chats", lambda: {"r1": ["-100999"]})

    messages_sent = []

    def mock_send_message(text, chat_id=None, **kw):
        messages_sent.append((chat_id, text))
        return True

    monkeypatch.setattr(ops, "send_message", mock_send_message)
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 555})

    # 1. Guruhda callback query kelganda
    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    cb_upd = {
        "callback_query": {
            "id": "cq_grp",
            "from": {"id": 12345, "first_name": "Haydovchi1"},
            "message": {"chat": {"id": -100999, "type": "supergroup"}},
            "data": "gck:r1_20261112:0",
        }
    }
    ops._handle_update(cb_upd)
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is True

    # 2. Guruhda /sorov_status buyrug'i
    msg_upd = {
        "message": {
            "chat": {"id": -100999, "type": "supergroup"},
            "from": {"id": 111, "first_name": "Admin"},
            "text": f"/sorov_status {DAY}",
        }
    }
    ops._handle_update(msg_upd)
    assert any("SO'ROVNOMA HOLATI" in m[1] for m in messages_sent)

    # 3. Guruhda /sorov_bekor P1
    msg_reset = {
        "message": {
            "chat": {"id": -100999, "type": "supergroup"},
            "from": {"id": 111, "first_name": "Admin"},
            "text": f"/sorov_bekor P1 {DAY}",
        }
    }
    ops._handle_update(msg_reset)
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is False


def test_api_grafik_poll(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import app
    from bm_automation.app.dashboard import deps

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    async def mock_scope(req, creds=None):
        return {"username": "admin", "role": "ADMIN", "is_admin": True}

    monkeypatch.setattr(deps, "resolve_scope", mock_scope)

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    client = TestClient(app)
    res = client.get(f"/api/grafik-poll?date={DAY}&routeVariantId=r1")
    assert res.status_code == 200
    data = res.json()
    assert data["poll_id"] == "r1_20261112"
    assert len(data["items"]) == 2


def test_unlinked_user_rejected_with_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)

    alert_calls = []

    def mock_call(method, payload=None, **kw):
        if method == "answerCallbackQuery":
            alert_calls.append(payload)
        return {"ok": True}

    monkeypatch.setattr(grafik_poll, "telegram_call", mock_call)

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    # Bog'lanmagan foydalanuvchi (999888) Nishanov (did1) tugmasini bosadi
    cq = {
        "id": "cq_unlinked",
        "from": {
            "id": 999888,
            "first_name": "Begona",
            "last_name": "Odam",
        },
    }
    grafik_poll.handle_group_callback("-100123", cq, "gck:r1_20261112:0")

    # Tasdiqlanmasligi shart!
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][0]["confirmed"] is False

    # Alert modal ko'rsatilgan va unda botga bog'lash eslatmasi bor
    assert len(alert_calls) == 1
    assert alert_calls[0]["show_alert"] is True
    assert "bog'lanmagan" in alert_calls[0]["text"]


def test_linked_driver_cannot_click_another_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)
    st.save_driver("did1", full_name="NISHANOV MUXTORXON ANVAROVICH")
    st.save_driver("did2", full_name="KARIMOV BOTIR AKBAROVICH")

    # Haydovchi A (did1) o'z profilingizga bog'langan
    st.link_driver_telegram("did1", 777111)

    alert_calls = []

    def mock_call(method, payload=None, **kw):
        if method == "answerCallbackQuery":
            alert_calls.append(payload)
        return {"ok": True}

    monkeypatch.setattr(grafik_poll, "telegram_call", mock_call)

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    # Haydovchi A (777111) Haydovchi B (did2, idx 1) ning tugmasini bosadi!
    cq = {
        "id": "cq_other",
        "from": {
            "id": 777111,
            "first_name": "Muxtorxon",
            "last_name": "Nishanov",
        },
    }
    grafik_poll.handle_group_callback("-100123", cq, "gck:r1_20261112:1")

    # Karimov grafigi tasdiqlanmasligi shart!
    poll = grafik_poll.get_poll("r1_20261112")
    assert poll["items"][1]["confirmed"] is False

    # Boshqa haydovchi grafigini tasdiqlay olmaysiz degan taqiq chiqadi
    assert len(alert_calls) == 1
    assert alert_calls[0]["show_alert"] is True
    assert "boshqa haydovchining" in alert_calls[0]["text"].lower()


def test_admin_blocked_from_clicking_driver_button_in_group(tmp_path, monkeypatch):
    from bm_automation.app.notifications.ops.roles import Role
    import bm_automation.app.notifications.ops.roles as roles_mod

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    st = _storage(tmp_path)
    monkeypatch.setattr(grafik_poll, "get_storage", lambda: st)
    st.save_driver("did2", full_name="KARIMOV BOTIR AKBAROVICH")

    # Admin roli
    monkeypatch.setattr(roles_mod, "resolve_role", lambda uid: Role.ADMIN if uid == 555 else Role.VIEWER)

    alert_calls = []

    def mock_call(method, payload=None, **kw):
        if method == "answerCallbackQuery":
            alert_calls.append(payload)
        return {"ok": True}

    monkeypatch.setattr(grafik_poll, "telegram_call", mock_call)

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    # Admin (555) guruhdagi Karimov (idx 1) tugmasini bosadi -> RAD ETILISHI SHART!
    cq = {
        "id": "cq_adm",
        "from": {
            "id": 555,
            "first_name": "Dispetcher",
            "last_name": "Bosh",
        },
    }
    grafik_poll.handle_group_callback("-100123", cq, "gck:r1_20261112:1")

    poll = grafik_poll.get_poll("r1_20261112")
    # Tasdiqlanmagan holda qolishi shart
    assert poll["items"][1]["confirmed"] is False
    assert len(alert_calls) == 1
    assert "dispetchersiz" in alert_calls[0]["text"] or "admin" in alert_calls[0]["text"]

    # Endi admin rasmiy buyruq / Dashboard orqali tasdiqlaydi (confirm_by_admin)
    res = grafik_poll.confirm_by_admin("r1", DAY, "P2", admin_name="Dispetcher")
    assert res is True

    poll2 = grafik_poll.get_poll("r1_20261112")
    assert poll2["items"][1]["confirmed"] is True
    assert "Dispetcher" in poll2["items"][1]["confirmed_by_name"]
    # Adminning chat ID si haydovchiga bog'lanib qolmasligi shart!
    prof = st.query("SELECT * FROM driver_profiles WHERE driver_id = 'did2'")
    if prof:
        assert str(prof[0].get("telegram_chat_id") or "") != "555"


def test_build_poll_resets_confirmation_when_driver_swaps(tmp_path, monkeypatch):
    """BM saytida haydovchi o'zgarganda/almashganda eski tasdiq avtomatik bekor qilinadi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    # 1. Boshlang'ich grafik: P1 da did1
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    poll["items"][0]["confirmed"] = True
    poll["items"][0]["confirmed_by_id"] = 777111
    poll["items"][0]["confirmed_by_name"] = "Nishanov"
    grafik_poll.save_poll(poll)

    # 2. BM saytida grafik yangilandi: P1 ga did2 (Karimov) qo'yildi
    updated_graphs = [
        {
            "graphName": "P1",
            "driverId": "did2",
            "driverName": "KARIMOV BOTIR AKBAROVICH",
            "plateNum": "40 123 AAA",
            "startTime": "06:15",
            "endTime": "20:30",
        },
        {
            "graphName": "P2",
            "driverId": "did1",
            "driverName": "NISHANOV MUXTORXON ANVAROVICH",
            "plateNum": "40 456 BBB",
            "startTime": "06:30",
            "endTime": "21:00",
        },
    ]

    poll2 = grafik_poll.build_poll_data("r1", DAY, graphs=updated_graphs, route_name="B-80")
    # P1 endi Karimov bo'lishi va tasdiqlanishi bekor bo'lishi shart!
    assert poll2["items"][0]["driver_id"] == "did2"
    assert poll2["items"][0]["driver_name"] == "KARIMOV BOTIR AKBAROVICH"
    assert poll2["items"][0]["confirmed"] is False
    assert poll2["items"][0]["confirmed_by_id"] is None


def test_swap_graphs_feature(tmp_path, monkeypatch):
    """Dispetcher ikki grafikni o'zaro almashtirganda (swap) holat to'g'ri yangilanadi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    poll = grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    poll["items"][0]["confirmed"] = True
    poll["items"][1]["confirmed"] = True
    grafik_poll.save_poll(poll)

    # P1 va P2 ni almashtirish
    res = grafik_poll.swap_graphs("r1", DAY, "P1", "P2", admin_name="Bosh Dispetcher")
    assert res["ok"] is True
    assert res["graph1"] == "P1"
    assert res["driver1"] == "Karimov B."
    assert res["graph2"] == "P2"
    assert res["driver2"] == "Nishanov M."

    updated = grafik_poll.get_poll("r1_20261112")
    # Har ikki grafik tasdiqlash uchun qayta ochilgan bo'lishi shart
    assert updated["items"][0]["driver_id"] == "did2"
    assert updated["items"][0]["confirmed"] is False
    assert updated["items"][1]["driver_id"] == "did1"
    assert updated["items"][1]["confirmed"] is False


def test_api_grafik_poll_swap(tmp_path, monkeypatch):
    """Dashboard /api/grafik-poll/swap endpointi to'g'ri ishlaydi."""
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import app
    from bm_automation.app.dashboard import deps

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: {"message_id": 100})

    async def mock_scope(req, creds=None):
        return {"username": "admin", "role": "ADMIN", "is_admin": True}

    monkeypatch.setattr(deps, "resolve_scope", mock_scope)

    grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")

    client = TestClient(app)
    res = client.post("/api/grafik-poll/swap", json={
        "routeVariantId": "r1",
        "date": DAY,
        "graph1": "1",
        "graph2": "2",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["driver1"] == "Karimov B."
    assert data["driver2"] == "Nishanov M."


def test_send_poll_without_mapped_chat_fails_gracefully(tmp_path, monkeypatch):
    """Biriktirilgan guruhi yo'q yo'nalish umumiy guruhlarga yuborilmasligi shart."""
    import bm_automation.app.notifications.ops.group_departures as gd

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(gd, "_route_chats", lambda: {"r_other": ["-100888"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100999", "-100888"])

    calls = []
    monkeypatch.setattr(grafik_poll, "telegram_call", lambda m, p=None, **kw: calls.append((m, p)))

    res = grafik_poll.send_poll_to_groups(
        "r_unmapped", DAY, graphs=_graphs_sample(), route_name="12-yo'nalish"
    )
    assert res["sent"] == 0
    assert "Telegram guruh biriktirilmagan" in res["error"]
    assert len(calls) == 0  # Hech qanday umumiy guruhga yuborilmagan


def test_group_unmapped_commands_warning(tmp_path, monkeypatch):
    """Biriktirilmagan guruhda /sorov buyrug'i barcha yo'nalishlarni yubormasdan ogohlantiradi."""
    from bm_automation.app.notifications.ops import ops
    from bm_automation.app.notifications.ops.roles import Role
    import bm_automation.app.notifications.ops.group_departures as gd

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(ops, "record_group_message", lambda *a, **kw: None)
    monkeypatch.setattr(ops, "resolve_role", lambda cid: Role.ADMIN)
    monkeypatch.setattr(gd, "_route_chats", lambda: {"r1": ["-100111"]})

    messages_sent = []
    monkeypatch.setattr(ops, "send_message", lambda text, chat_id=None, **kw: messages_sent.append((chat_id, text)))

    msg_upd = {
        "message": {
            "chat": {"id": -100999, "type": "supergroup"},  # Unmapped group
            "from": {"id": 111, "first_name": "Admin"},
            "text": f"/sorov {DAY}",
        }
    }
    ops._handle_update(msg_upd)
    assert len(messages_sent) == 1
    assert "biriktirilgan yo'nalish topilmadi" in messages_sent[0][1]


def test_confirmation_preserved_when_schedule_details_change(tmp_path, monkeypatch):
    """Grafik vaqti yoki avtobusi o'zgarganda, tasdiqlagan haydovchining tasdig'i o'z kuchida qoladi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    # 1. Boshlang'ich so'rovnoma
    poll = grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    # did1 (Nishanov) tasdiqlaydi
    poll["items"][0]["confirmed"] = True
    poll["items"][0]["confirmed_by_id"] = 777111
    poll["items"][0]["confirmed_by_name"] = "Nishanov M."
    poll["items"][0]["confirmed_at"] = "18:30"
    grafik_poll.save_poll(poll)

    # 2. BM saytida yangilangan duty keladi: vaqt va avtobus o'zgardi, lekin haydovchi o'sha
    updated_graphs = [
        {
            "graphName": "P1",
            "driverId": "did1",
            "driverName": "NISHANOV MUXTORXON ANVAROVICH",
            "plateNum": "40 999 ZZZ",  # yangi avtobus
            "startTime": "06:45",      # yangi vaqt
            "endTime": "21:15",
        },
        {
            "graphName": "P2",
            "driverId": "did2",
            "driverName": "KARIMOV BOTIR AKBAROVICH",
            "plateNum": "40 456 BBB",
            "startTime": "06:30",
            "endTime": "21:00",
        },
    ]

    rebuilt = grafik_poll.build_poll_data("r1", DAY, graphs=updated_graphs, route_name="B-80")
    p1 = rebuilt["items"][0]
    p2 = rebuilt["items"][1]

    # did1 tasdig'i saqlangan
    assert p1["confirmed"] is True
    assert p1["confirmed_by_id"] == 777111
    assert p1["confirmed_by_name"] == "Nishanov M."
    assert p1["confirmed_at"] == "18:30"
    # Lekin yangi vaqt va yangi avtobus aks etgan
    assert p1["plate"] == "40 999 ZZZ"
    assert p1["start_time"] == "06:45"

    # did2 esa tasdiqlanmagan holda qolgan
    assert p2["confirmed"] is False


def test_confirmation_preserved_when_driver_moves_graph(tmp_path, monkeypatch):
    """Haydovchi boshqa grafik raqamiga o'tkazilganda, uning tasdig'i o'sha yangi grafikda o'z kuchida qoladi."""
    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")

    poll = grafik_poll.build_poll_data("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    # did1 (P1 da edi) tasdiqlagan
    poll["items"][0]["confirmed"] = True
    poll["items"][0]["confirmed_by_id"] = 777111
    poll["items"][0]["confirmed_by_name"] = "Nishanov M."
    poll["items"][0]["confirmed_at"] = "19:00"
    grafik_poll.save_poll(poll)

    # BM saytida dispetcher almashtirdi: P1 ga did2 (Karimov), P2 ga did1 (Nishanov)
    swapped_graphs = [
        {
            "graphName": "P1",
            "driverId": "did2",
            "driverName": "KARIMOV BOTIR AKBAROVICH",
            "plateNum": "40 123 AAA",
            "startTime": "06:15",
            "endTime": "20:30",
        },
        {
            "graphName": "P2",
            "driverId": "did1",
            "driverName": "NISHANOV MUXTORXON ANVAROVICH",
            "plateNum": "40 456 BBB",
            "startTime": "06:30",
            "endTime": "21:00",
        },
    ]

    rebuilt = grafik_poll.build_poll_data("r1", DAY, graphs=swapped_graphs, route_name="B-80")
    p1 = rebuilt["items"][0]
    p2 = rebuilt["items"][1]

    # P1 dagi yangi haydovchi (Karimov) hali tasdiqlamagan
    assert p1["driver_id"] == "did2"
    assert p1["confirmed"] is False

    # P2 ga o'tgan did1 (Nishanov) tasdig'i o'z kuchida qolgan!
    assert p2["driver_id"] == "did1"
    assert p2["confirmed"] is True
    assert p2["confirmed_by_id"] == 777111
    assert p2["confirmed_by_name"] == "Nishanov M."


def test_sync_poll_with_duty_edits_existing_message(tmp_path, monkeypatch):
    """sync_poll_with_duty chaqirilganda, guruhdagi mavjud so'rovnoma xabari avtomatik tahrirlanadi."""
    import bm_automation.app.notifications.ops.group_departures as gd

    monkeypatch.setattr(grafik_poll, "STATE_FILE", tmp_path / "polls.json")
    monkeypatch.setattr(gd, "_route_chats", lambda: {"r1": ["-100777"]})

    calls = []
    def mock_call(method, payload=None, **kw):
        calls.append((method, payload))
        if method == "sendMessage":
            return {"message_id": 1234}
        return {"ok": True}

    monkeypatch.setattr(grafik_poll, "telegram_call", mock_call)

    # 1. Guruhga birinchi marta so'rovnoma yuborildi
    res1 = grafik_poll.send_poll_to_groups("r1", DAY, graphs=_graphs_sample(), route_name="B-80")
    assert res1["sent"] == 1
    assert any(c[0] == "sendMessage" for c in calls)

    # 2. Grafik o'zgardi va sync_poll_with_duty chaqirildi
    calls.clear()
    updated_graphs = [
        {
            "graphName": "P1",
            "driverId": "did1",
            "driverName": "NISHANOV MUXTORXON ANVAROVICH",
            "plateNum": "40 777 NEW",
            "startTime": "06:00",
            "endTime": "20:00",
        },
    ]

    res2 = grafik_poll.sync_poll_with_duty("r1", DAY, graphs=updated_graphs, route_name="B-80")
    # Yangi xabar yuborilmaydi (sent=0), lekin mavjud xabar editMessageText orqali tahrirlanadi!
    assert res2["sent"] == 0
    edit_calls = [c for c in calls if c[0] == "editMessageText"]
    assert len(edit_calls) == 1
    assert edit_calls[0][1]["message_id"] == 1234
    assert "40 777 NEW" in edit_calls[0][1]["text"]






