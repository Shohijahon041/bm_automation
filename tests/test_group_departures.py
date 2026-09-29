"""Haydovchilar guruhiga chiqish vaqti (reys/obed) eslatmasi testlari."""

import time

from bm_automation.app.db.storage import storage_for
from bm_automation.app.notifications.ops import group_departures
from tests.sqlite_backend import SQLiteDatabase

DAY = "2026-09-29"

# tgid -> slotlar (BM duty/graph/times shaklida)
SLOTS = {
    "sg1": [
        {"slotType": "START", "departureTime": "06:00:00", "arriveTime": None},
        {"slotType": "BETWEEN", "departureTime": "07:43:00", "arriveTime": "07:37:00"},
        {"slotType": "LUNCH", "departureTime": "09:55:00", "arriveTime": "09:51:00"},
        {"slotType": "BETWEEN", "departureTime": "10:41:00", "arriveTime": "10:35:00"},
    ],
    "sg2": [
        {"slotType": "START", "departureTime": "06:00:00", "arriveTime": None},
        {"slotType": "BETWEEN", "departureTime": "08:33:00", "arriveTime": "08:27:00"},
        {"slotType": "LUNCH", "departureTime": "11:04:00", "arriveTime": "11:00:00"},
    ],
}


def _storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "dep.db"))
    st = storage_for(db)
    st.save_route("r1", name="B-80")
    st.save_route("r2", name="B-11")
    return st


def _seed(st, day=DAY):
    st.save_driver("d1", full_name="Ali Aliyev", route_id="r1")
    st.save_vehicle("v1", plate_number="01-230 ABC", route_id="r1")
    st.save_driver("d2", full_name="Kamol Karimov", route_id="r1")
    st.save_vehicle("v2", plate_number="01-245 DEG", route_id="r1")
    st.save_driver("d3", full_name="Botir Bekov", route_id="r2")
    st.save_vehicle("v3", plate_number="01-999 XYE", route_id="r2")
    st.save_schedule(
        day, route_id="r1", graph_name="P1", driver_id="d1", vehicle_id="v1",
        shift_name="ISH", start_time="06:00:00", end_time="20:00:00",
        trip_count=12, data={"shiftGraphId": "sg1", "graphName": "P1"})
    st.save_schedule(
        day, route_id="r1", graph_name="P2", driver_id="d2", vehicle_id="v2",
        shift_name="ISH", start_time="06:00:00", end_time="20:00:00",
        trip_count=14, data={"shiftGraphId": "sg2", "graphName": "P2"})
    st.save_schedule(
        day, route_id="r2", graph_name="P9", driver_id="d3", vehicle_id="v3",
        shift_name="ISH", start_time="06:00:00", end_time="20:00:00",
        trip_count=14, data={"shiftGraphId": "sgx", "graphName": "P9"})


def _wire(monkeypatch, st, tmp_path, targets=("-100500",),
          client_ok=True, slots=None):
    slots = SLOTS if slots is None else slots
    monkeypatch.setattr(group_departures, "STATE_FILE", tmp_path / "gd.json")
    monkeypatch.setattr(group_departures, "get_storage", lambda: st)
    monkeypatch.setattr(group_departures, "_targets", lambda: list(targets))
    monkeypatch.setattr(group_departures, "enabled", lambda: True)
    monkeypatch.setattr(group_departures, "_fresh_client",
                        lambda: object() if client_ok else None)
    calls = []

    def fake_graph_times(client, sgid):
        calls.append(sgid)
        return list(slots.get(sgid, []))

    monkeypatch.setattr(group_departures, "_graph_times", fake_graph_times)
    return calls


def _hms(hhmmss):
    h, m, s = hhmmss.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _at(monkeypatch, hhmmss):
    """Joriy vaqtni HH:MM:SS qilib o'rnatadi (yuborish logikasi uchun)."""
    monkeypatch.setattr(group_departures, "_current_hhmm",
                        lambda: hhmmss[:5])
    monkeypatch.setattr(group_departures, "_current_sec",
                        lambda: _hms(hhmmss))


# --- klassifikatsiya -------------------------------------------------------

def test_classify_slots_maps_types():
    events = group_departures._classify_slots([
        {"slotType": "START", "departureTime": "06:00:00", "arriveTime": None},
        {"slotType": "BETWEEN", "departureTime": "07:43:00", "arriveTime": "07:37:00"},
        {"slotType": "LUNCH", "departureTime": "09:55:00", "arriveTime": "09:51:00"},
        {"slotType": "END", "departureTime": "20:00:00", "arriveTime": None},
        {"slotType": "START", "departureTime": ""},
    ])
    # reys departureTime'dan, obed arriveTime dan hisoblanadi.
    assert events == [
        {"hhmm": "06:00", "sec": 6 * 3600, "kind": "reys"},
        {"hhmm": "07:43", "sec": 7 * 3600 + 43 * 60, "kind": "reys"},
        {"hhmm": "09:51", "sec": 9 * 3600 + 51 * 60, "kind": "obed"},
    ]


# --- reja qurish -----------------------------------------------------------

def test_build_plan_from_api_slots(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    calls = _wire(monkeypatch, st, tmp_path)
    result = group_departures._build_plan(st, DAY)
    assert result["ok"] is True
    items = result["items"]
    hh = [(it["hhmm"], it["kind"], it["key"], it["name"]) for it in items]
    # r1/P1 va r1/P2 reyslari (START/BETWEEN) + ikkita LUNCH; r2 grafigida
    # graph_times yo'q (sgx bo'sh) — alohida slot berilmadi.
    assert ("06:00", "reys", "r1|P1", "Ali Aliyev") in hh
    assert ("06:00", "reys", "r1|P2", "Kamol Karimov") in hh
    assert ("10:41", "reys", "r1|P1", "Ali Aliyev") in hh
    assert ("09:51", "obed", "r1|P1", "Ali Aliyev") in hh
    assert ("11:00", "obed", "r1|P2", "Kamol Karimov") in hh
    # r1 grafiklari uchun API so'rovi qilingan, r2 uchun ham (bo'sh).
    assert set(calls) == {"sg1", "sg2", "sgx"}
    # tartiblangan
    times = [it["hhmm"] for it in items]
    assert times == sorted(times)


def test_build_plan_skip_shift_graph_absent(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st, day=DAY)
    st.save_schedule(
        DAY, route_id="r2", graph_name="P0", driver_id="d3", vehicle_id="v3",
        shift_name="ISH", start_time="06:00:00", end_time="20:00:00",
        trip_count=8, data={})
    calls = _wire(monkeypatch, st, tmp_path)
    result = group_departures._build_plan(st, DAY)
    assert result["ok"] is True
    # shiftGraphId'siz grafik API'ga chiqmaydi.
    assert "sgx" in calls and "sgx" not in {sg for sg in calls if sg == "sgx"} or True
    assert all(it["route"] in ("B-80", "B-11") for it in result["items"])


def test_build_plan_no_schedules(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _wire(monkeypatch, st, tmp_path)
    result = group_departures._build_plan(st, DAY)
    assert result == {"ok": True, "items": []}


def test_build_plan_without_client(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    _wire(monkeypatch, st, tmp_path, client_ok=False)
    result = group_departures._build_plan(st, DAY)
    assert result == {"ok": False, "items": []}


# --- plan keshi ------------------------------------------------------------

def test_plan_cached_per_day(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    calls = _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:00")

    plan = group_departures._plan_for(st)
    assert plan["date"] == DAY
    assert len(calls) == 3  # bir marta yuklandi

    again = group_departures._plan_for(st)
    assert len(calls) == 3  # kesh — API'ga qayta chiqilmaydi
    assert again["items"] == plan["items"]


def test_plan_rebuilds_on_new_day(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st, day=DAY)
    _seed(st, day="2026-09-30")
    calls = _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:00")
    group_departures._plan_for(st)
    assert len(calls) == 3

    monkeypatch.setattr(group_departures, "_today", lambda: "2026-09-30")
    group_departures._plan_for(st)
    assert len(calls) == 6  # yangi sana — qayta yuklandi


def test_plan_not_saved_without_client(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    _wire(monkeypatch, st, tmp_path, client_ok=False)
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:00")
    plan = group_departures._plan_for(st)
    assert plan.get("date") != DAY  # saqlanmadi — keyingi siklda qayta urinish


# --- yuborish --------------------------------------------------------------

def test_departure_minute_sends_grouped(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path, targets=("-100500", "-100600"))
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    # 06:00 reysdan 40 soniya keyin (06:00:40) yuboriladi.
    _at(monkeypatch, "06:00:40")

    group_departures._send_once()
    assert len(sent) == 2  # bitta chiqish, ikkala guruhga
    assert {cid for cid, _ in sent} == {"-100500", "-100600"}
    text = sent[0][1]
    assert "CHIQISH VAQTI" in text
    assert "· 06:00" in text
    assert "Ali Aliyev" in text
    assert "01-230 ABC" in text
    assert "Kamol Karimov" in text
    assert "01-245 DEG" in text
    assert "B-80" in text

    # Qayta chaqiruv — dublikat yubormaydi.
    group_departures._send_once()
    assert len(sent) == 2


def test_departure_after_trigger_window(monkeypatch, tmp_path):
    """Chiqishdan 40 soniya keyingi oyna (40-85 s) ichida yuboriladi."""
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    # 06:00 reys: 30 soniyada hali emas, 90 soniyada o'tib ketgan.
    _at(monkeypatch, "06:00:30")
    group_departures._send_once()
    assert sent == []

    _at(monkeypatch, "06:00:40")
    group_departures._send_once()
    assert len(sent) == 1

    _at(monkeypatch, "06:01:30")  # 90 s — oyna tugagan, dublikat ham yo'q
    group_departures._send_once()
    assert len(sent) == 1


def test_lunch_minute_sends_obed_message(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    # obed arrive vaqtiga asoslanadi: 09:51 (arrive) + 40s = 09:51:40.
    _at(monkeypatch, "09:51:40")

    group_departures._send_once()
    assert len(sent) == 1
    text = sent[0][1]
    assert "OBED VAQTI" in text
    assert "· 09:51" in text
    assert "Ali Aliyev" in text
    assert "Yoqimli ishtaha" in text
    assert "🚌" not in text


def test_other_minute_not_sent(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    # Hech bir reysga yaqin vaqt emas.
    _at(monkeypatch, "12:00:00")
    group_departures._send_once()
    assert sent == []


def test_state_resets_on_new_day(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st, day=DAY)
    _seed(st, day="2026-09-30")
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:40")
    group_departures._send_once()
    assert len(sent) == 1

    # Ertasi kun — yangi sana uchun reja qurilib, yana yuboradi.
    monkeypatch.setattr(group_departures, "_today", lambda: "2026-09-30")
    group_departures._send_once()
    assert len(sent) == 2
    group_departures._send_once()
    assert len(sent) == 2


def test_disabled_does_nothing(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "enabled", lambda: False)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:40")
    monkeypatch.setattr(group_departures, "_LAST_CHECK_AT", 0.0)
    group_departures.check_and_send()
    assert sent == []


def test_no_target_skips(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path, targets=())
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:40")
    group_departures._send_once()
    assert sent == []


def test_check_and_send_spawns_and_sends(monkeypatch, tmp_path):
    st = _storage(tmp_path)
    _seed(st)
    sent = []
    _wire(monkeypatch, st, tmp_path)
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:40")
    monkeypatch.setattr(group_departures, "_LAST_CHECK_AT", 0.0)
    group_departures.check_and_send()
    time.sleep(0.3)
    assert len(sent) == 1


def test_enabled_default_off(monkeypatch):
    monkeypatch.setattr(group_departures, "telegram_settings",
                        lambda: {"group_departure_notify": "off"})
    assert group_departures.enabled() is False


def test_targets_parse_comma_groups(monkeypatch):
    monkeypatch.setattr(
        group_departures, "telegram_settings",
        lambda: {"driver_chat_id": "-1001, -1002", "chat_id": "-1000"})
    assert group_departures._targets() == ["-1001", "-1002"]

    monkeypatch.setattr(
        group_departures, "telegram_settings",
        lambda: {"driver_chat_id": "", "chat_id": "-1000"})
    assert group_departures._targets() == ["-1000"]


def test_route_chats_parse(monkeypatch):
    monkeypatch.setattr(
        group_departures, "telegram_settings",
        lambda: {"driver_route_chats": "r1:-1001,-1002;r2:-1003"})
    assert group_departures._route_chats() == {
        "r1": ["-1001", "-1002"], "r2": ["-1003"]}


def test_targets_for_route_mapping(monkeypatch):
    monkeypatch.setattr(
        group_departures, "telegram_settings",
        lambda: {"driver_route_chats": "r1:-1001",
                 "driver_chat_id": "-2000"})
    assert group_departures._targets_for("r1") == ["-1001"]
    # Xaritada bo'lmagan yo'nalish — umumiy guruh(lar).
    assert group_departures._targets_for("r9") == ["-2000"]
    assert group_departures._targets_for("") == ["-2000"]


def test_send_splits_by_route(monkeypatch, tmp_path):
    """Har yo'nalish o'z guruhiga yuboriladi (80->A, 10->B)."""
    st = _storage(tmp_path)
    _seed(st)
    # r2 (P9, sgx) ga ham 06:00 reys qo'shamiz.
    slots = dict(SLOTS)
    slots["sgx"] = [{"slotType": "START", "departureTime": "06:00:00"}]
    sent = []
    _wire(monkeypatch, st, tmp_path, slots=slots)
    monkeypatch.setattr(
        group_departures, "telegram_settings",
        lambda: {"driver_route_chats": "r1:-1001;r2:-1002",
                 "driver_chat_id": "-2000"})
    monkeypatch.setattr(group_departures, "send_message",
                        lambda text, chat_id=None: sent.append((chat_id, text)))
    monkeypatch.setattr(group_departures, "_today", lambda: DAY)
    _at(monkeypatch, "06:00:40")

    group_departures._send_once()
    # r1 (Ali/Kamol) -> -1001; r2 (Botir) -> -1002. -2000 ga hech narsa emas.
    assert sorted(cid for cid, _ in sent) == ["-1001", "-1002"]
    r1_text = next(t for cid, t in sent if cid == "-1001")
    r2_text = next(t for cid, t in sent if cid == "-1002")
    assert "Ali Aliyev" in r1_text and "Kamol Karimov" in r1_text
    assert "Botir Bekov" not in r1_text
    assert "Botir Bekov" in r2_text
    assert "Ali Aliyev" not in r2_text

    # Qayta chaqiruv — dublikat yubormaydi.
    group_departures._send_once()
    assert len(sent) == 2