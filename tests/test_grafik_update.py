"""Grafik yangilanishi kuzatuvi (grafik_update) testlari.

Qamrov:
  - duty fingerprint: barqarorlik + o'zgarishni aniqlash;
  - `_mark_grafik_sent` hash bilan saqlash / eski format mosligi;
  - baseline qabul qilish (eski True yozuv qayta yuborilmaydi);
  - o'zgarishda qayta yuborish + holat yangilanishi;
  - o'zgarmaganda jim turishi; boshqa kun/tur yozuvlari tashlanmasligi;
  - `_send_personal_cards(only_driver_ids=...)` filtri va jim rejim.
"""

from datetime import date

import pytest


def _duty(driver="Aliyev", did="d1", plate="01A123AA",
          second=None, second_id=None, graph="P1"):
    g = {
        "graphName": graph,
        "driverName": driver,
        "driverId": did,
        "plateNum": plate,
        "startTime": "06:00",
        "endTime": "14:00",
        "shiftName": "DU",
        "hasSecond": bool(second),
    }
    if second:
        g["secondDriverName"] = second
        g["secondDriverId"] = second_id
    return {"graphs": [g]}


class _FakeRepo:
    """DutyRepository o'rniga — natija tashqaridan beriladi."""

    duty = None
    calls: list = []

    def __init__(self, client):
        pass

    def by_date(self, rid, date_str):
        _FakeRepo.calls.append((rid, date_str))
        return _FakeRepo.duty


@pytest.fixture()
def gu_env(tmp_path, monkeypatch):
    """grafik_update uchun izolyatsiyalangan muhit."""
    import daily_grafik as dg
    from bm_automation.app.notifications.ops import grafik_update as gu

    monkeypatch.setattr(dg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(
        "bm_automation.app.repositories.duty_repo.DutyRepository", _FakeRepo)
    monkeypatch.setattr(
        "bm_automation.app.notifications.ops.grafik_sms._fresh_client",
        lambda: object())
    _FakeRepo.calls = []
    _FakeRepo.duty = None
    return dg, gu


def test_fingerprint_deterministic_and_change_detection():
    import daily_grafik as dg

    d1 = _duty(driver="Aliyev", did="d1")
    d2 = _duty(driver="Aliyev", did="d1")
    h1, dr1 = dg._duty_fingerprint(d1)
    h2, dr2 = dg._duty_fingerprint(d2)
    assert h1 == h2 and dr1 == dr2
    assert h1 and set(dr1) == {"d1"}

    # Haydovchi almashdi -> umumiy hash ham, shu haydovchi hash'i ham o'zgaradi.
    h3, dr3 = dg._duty_fingerprint(_duty(driver="Karimov", did="d1"))
    assert h3 != h1 and dr3["d1"] != dr1["d1"]

    # Hash QATOR darajasida: bitta qatordagi ikkinchi haydovchi almashsa,
    # o'sha qatordagi hamma haydovchi hash'i o'zgaradi (ularning kartasi
    # ham shu qatorni ko'rsatadi) — boshqa qator haydovchisi tegilmaydi.
    d4 = _duty(second="Valiyev", second_id="d9")
    _h4, dr4 = dg._duty_fingerprint(d4)
    d5 = _duty(second="Rahimov", second_id="d9")
    _h5, dr5 = dg._duty_fingerprint(d5)
    assert dr4["d1"] == dr4["d9"]          # bir qatordagilar bir hash'da
    assert dr5["d1"] == dr5["d9"]
    assert dr4["d9"] != dr5["d9"]          # 2-haydovchi o'zgargan
    assert dr4["d1"] != dr5["d1"]          # (qator hash'i — 1-haydovchida ham)
    d6 = _duty(second="Valiyev", second_id="d9")  # d4 bilan bir xil qator
    d6["graphs"].append(_duty(driver="Boshqa", did="d-other", graph="P9",
                              plate="02B456BB")["graphs"][0])
    _h6, dr6 = dg._duty_fingerprint(d6)
    assert dr6["d1"] == dr4["d1"]          # boshqa qator hash'ga ta'sir qilmaydi
    assert dr6["d-other"] != dr4["d1"]

    # Safar soni/vehicleId kirmaydi — ko'rinmas maydon o'zgarishi hash'ni
    # o'zgartirmasligi kerak.
    d7 = _duty(driver="Aliyev", did="d1")
    d7["graphs"][0]["tripCount"] = 99
    d7["graphs"][0]["vehicleId"] = "x-999"
    h7, _ = dg._duty_fingerprint(d7)
    assert h7 == h1


def test_mark_sent_hash_roundtrip(gu_env):
    dg, _gu = gu_env
    rid, ds = "r-1", "2026-10-02"
    dg._mark_grafik_sent(rid, ds, hash="abc123", drivers={"d1": "abc123"})
    info = dg._grafik_sent_info(rid, ds)
    assert isinstance(info, dict)
    assert info["hash"] == "abc123" and info["drivers"] == {"d1": "abc123"}
    assert "at" in info
    assert dg._grafik_was_sent(rid, ds) is True

    # Eski (True) yozuv: `_grafik_was_sent` True, lekin hash yo'q.
    dg._mark_grafik_sent("r-2", ds)
    assert dg._grafik_was_sent("r-2", ds) is True
    assert dg._grafik_sent_info("r-2", ds) is None


def test_run_once_adopts_baseline_silently(gu_env, monkeypatch):
    """Hash'siz eski yozuv qayta yuborilmaydi — jimgina baseline yoziladi."""
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    ds = date.today().isoformat()
    monkeypatch.setattr(dg, "ROUTES",
                        [("", rid, "img", "B-80", 0)])
    dg._mark_grafik_sent(rid, ds)  # eski format
    duty = _duty()
    _FakeRepo.duty = duty
    sent = []
    monkeypatch.setattr(gu, "_send_update",
                        lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(gu, "_notify", lambda r: sent.append(r))

    gu._run_once()

    assert _FakeRepo.calls == [(rid, ds)]
    assert sent == [], "baseline'da hech narsa yuborilmasligi kerak"
    info = dg._grafik_sent_info(rid, ds)
    assert isinstance(info, dict)
    total, drivers = dg._duty_fingerprint(duty)
    assert info["hash"] == total and info["drivers"] == drivers


def test_run_once_resends_on_change(gu_env, monkeypatch):
    """Duty o'zgarsa qayta yuboriladi va yangi hash saqlanadi."""
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    ds = date.today().isoformat()
    monkeypatch.setattr(dg, "ROUTES", [("", rid, "img", "B-80", 0)])

    old = _duty(driver="Aliyev", did="d1")
    old_total, old_drivers = dg._duty_fingerprint(old)
    dg._mark_grafik_sent(rid, ds, hash=old_total, drivers=old_drivers)

    new = _duty(driver="Karimov", did="d1")
    _FakeRepo.duty = new
    called = {}

    def fake_send_update(client, route_tuple, duty, date_str, changed):
        called["args"] = (route_tuple, duty, date_str, changed)
        return True

    monkeypatch.setattr(gu, "_send_update", fake_send_update)
    reports = []
    monkeypatch.setattr(gu, "_notify", lambda r: reports.extend(r))

    gu._run_once()

    assert "args" in called
    route_tuple, duty, date_str, changed = called["args"]
    assert route_tuple[1] == rid and date_str == ds
    assert changed == {"d1"}
    info = dg._grafik_sent_info(rid, ds)
    new_total, new_drivers = dg._duty_fingerprint(new)
    assert info["hash"] == new_total and info["drivers"] == new_drivers
    assert reports and "B-80" in reports[0]


def test_run_once_skips_unchanged(gu_env, monkeypatch):
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    ds = date.today().isoformat()
    monkeypatch.setattr(dg, "ROUTES", [("", rid, "img", "B-80", 0)])
    duty = _duty()
    total, drivers = dg._duty_fingerprint(duty)
    dg._mark_grafik_sent(rid, ds, hash=total, drivers=drivers)
    _FakeRepo.duty = duty
    sent = []
    monkeypatch.setattr(gu, "_send_update",
                        lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(gu, "_notify", lambda r: sent.append(r))

    gu._run_once()

    assert _FakeRepo.calls == [(rid, ds)]
    assert sent == []


def test_run_once_filters_dates_and_kinds(gu_env, monkeypatch):
    """O'tgan kun, 'notfound|'/'error|' yozuvlari tekshirilmaydi."""
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    monkeypatch.setattr(dg, "ROUTES", [("", rid, "img", "B-80", 0)])
    dg._mark_grafik_sent(rid, "2000-01-01")
    dg._grafik_notify_once(rid, date.today().isoformat(), "notfound")
    dg._grafik_notify_once(rid, date.today().isoformat(), "error")
    _FakeRepo.duty = _duty()
    sent = []
    monkeypatch.setattr(gu, "_send_update",
                        lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(gu, "_notify", lambda r: sent.append(r))

    gu._run_once()

    assert _FakeRepo.calls == []
    assert sent == []


def test_run_once_skips_when_client_missing(gu_env, monkeypatch):
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    monkeypatch.setattr(dg, "ROUTES", [("", rid, "img", "B-80", 0)])
    dg._mark_grafik_sent(rid, date.today().isoformat())
    monkeypatch.setattr(
        "bm_automation.app.notifications.ops.grafik_sms._fresh_client",
        lambda: None)
    gu._run_once()
    assert _FakeRepo.calls == []


def test_run_once_skips_inflight_route(gu_env, monkeypatch):
    """Tugma (qo'lda) oqimi ishlayotgan yo'nalish kuzatuvchi tomonidan
    tekshirilmaydi — takror yuborish bo'lmaydi."""
    dg, gu = gu_env
    rid = "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375"
    ds = date.today().isoformat()
    monkeypatch.setattr(dg, "ROUTES", [("", rid, "img", "B-80", 0)])
    old = _duty(driver="Aliyev", did="d1")
    old_total, old_drivers = dg._duty_fingerprint(old)
    dg._mark_grafik_sent(rid, ds, hash=old_total, drivers=old_drivers)
    _FakeRepo.duty = _duty(driver="Karimov", did="d1")  # o'zgargan

    dg._INFLIGHT.clear()
    dg._INFLIGHT[f"{rid}|{ds}"] = "B-80"  # tugma oqimi ishlamoqda
    sent = []
    monkeypatch.setattr(gu, "_send_update",
                        lambda *a, **k: sent.append(a) or True)
    monkeypatch.setattr(gu, "_notify", lambda r: sent.append(r))
    try:
        gu._run_once()
    finally:
        dg._INFLIGHT.clear()

    assert sent == []
    assert _FakeRepo.calls == []  # duty ham so'ralmadi (o'tkazib yuborildi)


class _FakeStorage:
    enabled = True
    db = None  # km_stats so'rovi xato beradi -> km_stats=None (kodda ushlangan)

    def find(self, table, **kw):
        did = kw.get("driver_id", "")
        return {"driver_id": did, "telegram_chat_id": "-100" + did,
                "notification_enabled": True}

    def dispatcher_phone(self, rid):
        return ""


def test_personal_cards_only_changed_ids_silent(gu_env, monkeypatch, tmp_path):
    """only_driver_ids — faqat shu haydovchilarga; admin_chat_id None — jim."""
    import daily_grafik as dg

    monkeypatch.setattr(dg, "REPORTS", tmp_path)
    monkeypatch.setattr(dg, "REFRESH_IMAGE", True)
    monkeypatch.setattr(
        "bm_automation.app.db.storage.get_storage", lambda: _FakeStorage())
    monkeypatch.setattr(
        "bm_automation.app.exporters.sheet_image.make_personal_sheet_image",
        lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(
        "bm_automation.app.notifications.telegram.send_photo",
        lambda path, caption="", chat_id=None, **k: sent.append(chat_id))
    admin = []
    monkeypatch.setattr(dg, "send_message",
                        lambda *a, **k: admin.append(a) or True)

    res = {
        "date": "2026-10-02",
        "route_name": "B-80",
        "rows": {"UP": [{
            "graph": "P1", "driver": "Aliyev / Valiyev",
            "driver_id": "d1", "second_driver_id": "d9",
            "bus": "01A123AA", "start": "06:00", "end": "14:00",
            "shift": "DU",
        }]},
        "drivers": [{"id": "d1", "name": "Aliyev"},
                    {"id": "d9", "name": "Valiyev"}],
    }
    dg._send_personal_cards(res, "r-1", date(2026, 10, 2), "B-80", None,
                            only_driver_ids={"d9"})
    # Faqat d9'ga yuborildi; admin xulosasi (chat_id=None) chaqirilmadi.
    assert sent == ["-100d9"]
    assert admin == []


def test_check_and_send_interval_gate(gu_env, monkeypatch):
    import threading
    import time as _time

    _dg, gu = gu_env
    calls = []
    monkeypatch.setattr(gu, "_run_once", lambda: calls.append(1))
    monkeypatch.setattr(gu, "_THREAD", None)
    # Yaqinda tekshirilgan -> jim.
    monkeypatch.setattr(gu, "_LAST_CHECK_AT", _time.time())
    gu.check_and_send()
    assert calls == []
    # Muddati o'tgan -> fon oqimi ishga tushadi.
    monkeypatch.setattr(gu, "_LAST_CHECK_AT", 0.0)
    gu.check_and_send()
    th = gu._THREAD
    if isinstance(th, threading.Thread):
        th.join(timeout=5)
    assert calls == [1]
