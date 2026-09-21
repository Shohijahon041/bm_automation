"""SMS xabarnoma testlari (SQLite / tmp, gateway mock).

Asosiy scenario — stale PENDING: gateway tasdiqlashi kelmayan yozuv
turib qolsa, grafik o'zgarganda haydovchiga KORREKSIYA (UPDATED)
yuborilishi kerak; matn o'zgarmagan bo'lsa dublikat yuborilmaydi.
Grafikdan chiqarilgan haydovchi (hatto avvalgi yozuv PENDING bo'lsa ham)
"chiqarildi" xabarini olishi kerak.
"""

import pytest

from bm_automation.app.notifications import sms_notify
from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase

DATE = "2026-09-12"
ROUTE = "dfbfbe00-38a2-4ecc-8f3b-15b790308cbc"


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "test.db"))
    return storage_for(db)


@pytest.fixture()
def fake_gateway(monkeypatch):
    """send_sms'ni mock qiladi; chaqiruvlar tarixini qaytaradi."""
    calls = []

    def _fake(phone, text):
        calls.append({"phone": phone, "text": text})
        return {"ok": True, "phone": phone, "status": "SENT",
                "message_id": f"mid-{len(calls)}", "error": "", "states": []}

    monkeypatch.setattr(sms_notify, "send_sms", _fake)
    return calls


def _row(driver_id="D1", name="ALIYEV", graph="P1", start="06:00",
         end="12:00", vehicle="01A123BC"):
    return {
        "driver_id": driver_id, "name": name, "graph_name": graph,
        "shift_name": "DU", "start_time": start, "end_time": end,
        "trip_count": 3, "vehicle": vehicle,
        "phone": "+998901234567", "second": "0",
    }


def _send_one(storage, rows, result):
    key = (rows[0]["driver_id"], rows[0]["phone"], rows[0]["name"])
    return sms_notify._send_one(
        storage, key, rows, DATE, ROUTE, "10-yo'nalish", result)


def test_first_send_goes_through(storage, fake_gateway):
    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    entry = _send_one(storage, [_row()], result)
    assert result["sent"] == 1
    assert entry["status"] == "SENT"
    assert len(fake_gateway) == 1


def test_stale_pending_with_changed_graph_sends_correction(
        storage, fake_gateway):
    """PENDING qotib qolgan + grafik o'zgargan -> UPDATED korreksiya yuboriladi."""
    old_rows = [_row(graph="P1", start="06:00")]
    old_msg = sms_notify._message("ALIYEV", DATE, old_rows,
                                  route_name="10-yo'nalish", d_phone="")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message=old_msg, status="PENDING")

    # Grafikda chiqish vaqti o'zgargan
    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    entry = _send_one(storage, [_row(graph="P1", start="07:30")], result)

    assert result["updated"] == 1
    assert result["skipped"] == 0
    assert entry["status"] == "UPDATED"
    assert len(fake_gateway) == 1
    # Korreksiya matni yangi vaqtni o'z ichiga oladi
    assert "07:30" in fake_gateway[0]["text"]


def test_stale_pending_with_unchanged_graph_skips(storage, fake_gateway):
    """PENDING qotgan, lekin grafik O'ZGARMAGAN -> dublikat yuborilmaydi."""
    old_rows = [_row(graph="P1", start="06:00")]
    old_msg = sms_notify._message("ALIYEV", DATE, old_rows,
                                  route_name="10-yo'nalish", d_phone="")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message=old_msg, status="PENDING")

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    entry = _send_one(storage, [_row(graph="P1", start="06:00")], result)

    assert result["skipped"] == 1
    assert result["updated"] == 0
    assert entry["status"] == "SKIPPED"
    assert fake_gateway == []


def test_sent_history_still_blocks_duplicate(storage, fake_gateway):
    """SENT yozuv + o'zgarmagan grafik -> SKIPPED (dublikat yo'q)."""
    old_rows = [_row(graph="P1", start="06:00")]
    old_msg = sms_notify._message("ALIYEV", DATE, old_rows,
                                  route_name="10-yo'nalish", d_phone="")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message=old_msg, status="SENT")

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    entry = _send_one(storage, [_row(graph="P1", start="06:00")], result)
    assert result["skipped"] == 1
    assert fake_gateway == []


def test_daily_limit_blocks_third_send(storage, fake_gateway):
    """Kunlik limit: 2 ta SMS (1 smena + 1 tuzatish) dan keyin uchinchisi
    bloklanadi — haydovchiga dublikat SMS yuqmaydi (balans tejash)."""
    # 1) Birinchi SMS yuboriladi (SENT)
    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    entry = _send_one(storage, [_row(graph="P1", start="06:00")], result)
    assert result["sent"] == 1
    assert entry["status"] == "SENT"

    # 2) Grafik o'zgardi -> korreksiya yuboriladi (ikkinchi SMS)
    result2 = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
               "invalid": 0, "updated": 0, "removed": 0,
               "errors": [], "drivers": []}
    entry2 = _send_one(storage, [_row(graph="P1", start="07:30")], result2)
    assert result2["updated"] == 1
    assert entry2["status"] == "UPDATED"

    # 3) Yana o'zgarish -> UCHINCHI SMS bloklanadi (kunlik 2 limit)
    result3 = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
               "invalid": 0, "updated": 0, "removed": 0,
               "errors": [], "drivers": []}
    entry3 = _send_one(storage, [_row(graph="P1", start="08:00")], result3)
    assert result3["sent"] == 0
    assert result3["updated"] == 0
    assert result3["skipped"] == 1
    assert entry3["status"] == "SKIPPED"
    assert entry3["error"] == "kunlik limit (2) to'ldi"
    # Gateway'ga har safar 1 tadan — jami 2 ta SMS ketgan
    assert len(fake_gateway) == 2


def test_daily_limit_blocks_removed_when_full(storage, fake_gateway):
    """'Chiqarildi' xabari kunlik limit to'lgach bloklanadi."""
    # Haydovchi allaqachon 2 ta SMS olgan (SENT + UPDATED)
    for i, stt in enumerate(("SENT", "UPDATED")):
        storage.record_sms(driver_id="D2", name="D2",
                           phone="+998901234568", route_id=ROUTE,
                           route_name="10-yo'nalish", schedule_date=DATE,
                           message=f"xabar-{i}", status=stt)

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    sms_notify._notify_removed(
        storage, DATE, ROUTE, "10-yo'nalish",
        {("D1", "+998901234567", "ALIYEV")}, result)

    assert result["removed"] == 0
    assert fake_gateway == []


def test_pending_removed_not_renotified(storage, fake_gateway):
    """PENDING 'chiqarildi' yozuvi bor bo'lsa — takroriy yuborilmaydi."""
    storage.record_sms(driver_id="D2", name="D2", phone="+998901234568",
                       route_id=ROUTE, route_name="10-yo'nalish",
                       schedule_date=DATE, message="chiqarildi xabari",
                       status="PENDING")

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    sms_notify._notify_removed(
        storage, DATE, ROUTE, "10-yo'nalish",
        {("D1", "+998901234567", "ALIYEV")}, result)

    assert result["removed"] == 0
    assert fake_gateway == []


def test_retry_stale_pending_daily_limit_supersedes(storage, fake_gateway,
                                                    monkeypatch):
    """Kunlik limit to'lgan bo'lsa stale PENDING qayta yuborilmaydi."""
    from datetime import date as _date
    today = _date.today().isoformat()
    # 2 ta yozuv bor: biri stale PENDING, biri SENT (kunlik limit to'la)
    old_created = (sms_notify.datetime.now(sms_notify.timezone.utc)
                   - sms_notify.timedelta(hours=2)).isoformat()
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=today,
                       message="test-xabar", status="PENDING",
                       message_id="mid-stale")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=today,
                       message="boshqa", status="SENT")
    st_db = storage.db
    ph = st_db.ph
    st_db.execute(
        f"UPDATE sms_log SET created_at = {ph} WHERE message_id = {ph}",
        (old_created, "mid-stale"))

    monkeypatch.setattr(sms_notify, "fetch_sms_status",
                        lambda mid: {"ok": True, "state": "UNKNOWN"})
    monkeypatch.setattr(sms_notify, "_LAST_STALE_POLL", 0.0)
    monkeypatch.setattr(sms_notify, "_sent_success_elsewhere",
                        lambda *a, **k: False)

    res = sms_notify.retry_stale_pending(storage, stale_hours=1.0, limit=10)

    assert res["resent"] == 0
    assert res["skipped"] == 1
    assert fake_gateway == []
    rows = storage.query("SELECT status FROM sms_log ORDER BY id")
    assert [r["status"] for r in rows] == ["SUPERSEDED", "SENT"]


def test_retry_failed_daily_limit_blocks(storage, fake_gateway, monkeypatch):
    """Dashboard qayta yuborish ham kunlik limitga bo'ysunadi."""
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message="eshik-teshik", status="FAILED")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message="ishga", status="SENT")
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message="tuzatish", status="UPDATED")

    monkeypatch.setattr(sms_notify, "_sent_success_elsewhere",
                        lambda *a, **k: False)
    res = sms_notify.retry_failed_sms(storage, limit=10)

    assert res["sent"] == 0
    assert res["skipped"] >= 1
    assert fake_gateway == []


def test_removed_driver_with_pending_row_notified(storage, fake_gateway):
    """PENDING yozuvli haydovchi grafikdan chiqarilsa 'chiqarildi' SMS ketadi."""
    old_rows = [_row(graph="P1", start="06:00")]
    old_msg = sms_notify._message("D2", DATE, old_rows,
                                  route_name="10-yo'nalish", d_phone="")
    storage.record_sms(driver_id="D2", name="D2", phone="+998901234568",
                       route_id=ROUTE, route_name="10-yo'nalish",
                       schedule_date=DATE, message=old_msg, status="PENDING")

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    # Hozirgi grafikda faqat D1 bor — D2 chiqarilgan
    sms_notify._notify_removed(
        storage, DATE, ROUTE, "10-yo'nalish",
        {("D1", "+998901234567", "ALIYEV")}, result)

    assert result["removed"] == 1
    assert len(fake_gateway) == 1
    assert fake_gateway[0]["phone"] == "+998901234568"


def test_removed_driver_with_annulled_not_renotified(storage, fake_gateway):
    """'Chiqarildi' SMS allaqachon yuborilgan (ANNULLED) — qayta yuborilmaydi."""
    storage.record_sms(driver_id="D2", name="D2", phone="+998901234568",
                       route_id=ROUTE, route_name="10-yo'nalish",
                       schedule_date=DATE, message="chiqarildi",
                       status="ANNULLED")

    result = {"sent": 0, "skipped": 0, "failed": 0, "pending": 0,
              "invalid": 0, "updated": 0, "removed": 0,
              "errors": [], "drivers": []}
    sms_notify._notify_removed(
        storage, DATE, ROUTE, "10-yo'nalish",
        {("D1", "+998901234567", "ALIYEV")}, result)

    assert result["removed"] == 0
    assert fake_gateway == []


# --------------------------------------------------------- retry_failed_sms


def test_retry_failed_resends_pending_rows(storage, fake_gateway, monkeypatch):
    """Stale PENDING yozuv QAYTA YUBORILADI — dashboard 'Qayta yuborish' ishlaydi.

    (Regressiya: avval retry o'z PENDING yozuvini `_sent_before` orqali
    o'zi bloklar edi — tugma samarasiz bo'lardi.)
    """
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message="eshik-teshik", status="PENDING",
                       message_id="mid-old")

    # _sent_success_elsewhere: boshqa muvaffaqiyatli yozuv yo'q
    monkeypatch.setattr(sms_notify, "_sent_success_elsewhere",
                        lambda *a, **k: False)
    res = sms_notify.retry_failed_sms(storage, limit=10)

    assert len(res["retried"]) == 1
    assert res["sent"] == 1
    assert len(fake_gateway) == 1
    assert fake_gateway[0]["phone"] == "+998901234567"


def test_retry_failed_skips_when_other_row_sent(storage, fake_gateway,
                                                monkeypatch):
    """Boshqa yozuv allaqachon SENT bo'lsa — qayta yuborilmaydi (dublikat yo'q)."""
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=DATE,
                       message="eshik-teshik", status="PENDING")

    monkeypatch.setattr(sms_notify, "_sent_success_elsewhere",
                        lambda *a, **k: True)
    res = sms_notify.retry_failed_sms(storage, limit=10)

    assert res["skipped"] == 1
    assert fake_gateway == []


# ------------------------------------------------------- resolve_route_name


def test_resolve_route_name_prefers_profile(storage, monkeypatch):
    """Kanonik nom profil'dan olinadi (har doim barqaror manba)."""
    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles",
        lambda: [{"name": "FERGANATEX", "routeVariantId": ROUTE,
                  "routeName": "10-yo'nalish"}])
    assert sms_notify.resolve_route_name(storage, ROUTE) == "10-yo'nalish"


def test_resolve_route_name_falls_back(storage, monkeypatch):
    """Profil topilmasa fallback ishlatiladi."""
    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles", lambda: [])
    assert sms_notify.resolve_route_name(
        storage, "unknown-id", fallback="B-80") == "B-80"


# ------------------------------------------------------ retry_stale_pending


def test_retry_stale_pending_resent_when_unknown(storage, fake_gateway,
                                                 monkeypatch):
    """Holati aniq chiqmasa (UNKNOWN) stale PENDING qayta yuboriladi."""
    recent_date = sms_notify.date.today().isoformat()
    # 2 soat oldingi yozuv (cutoff = 1 soat)
    old_created = (sms_notify.datetime.now(sms_notify.timezone.utc)
                   - sms_notify.timedelta(hours=2)).isoformat()
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=recent_date,
                       message="test-xabar", status="PENDING",
                       message_id="mid-stale")
    # record_sms created_at'ni o'zi yozadi — eski sana bilan yangilaymiz
    st_db = storage.db
    ph = st_db.ph
    st_db.execute(
        f"UPDATE sms_log SET created_at = {ph} WHERE message_id = {ph}",
        (old_created, "mid-stale"))

    # Holat so'rovi UNKNOWN qaytarsin
    monkeypatch.setattr(sms_notify, "fetch_sms_status",
                        lambda mid: {"ok": True, "state": "UNKNOWN"})
    # Interval tekshiruvini chetlab o'tamiz
    monkeypatch.setattr(sms_notify, "_LAST_STALE_POLL", 0.0)

    res = sms_notify.retry_stale_pending(storage, stale_hours=1.0, limit=10)

    assert res["polled"] == 1
    assert res["resent"] == 1
    assert len(fake_gateway) == 1
    # Yangi yozuv qo'shildi (tarix saqlanadi)
    rows = storage.query("SELECT status FROM sms_log ORDER BY id")
    assert [r["status"] for r in rows] == ["PENDING", "SENT"]


def test_retry_stale_pending_resolved_via_gateway(storage, fake_gateway,
                                                  monkeypatch):
    """Gateway aniq DELIVERED desa — qayta yuborilmaydi, faqat yangilanadi."""
    recent_date = sms_notify.date.today().isoformat()
    old_created = (sms_notify.datetime.now(sms_notify.timezone.utc)
                   - sms_notify.timedelta(hours=2)).isoformat()
    storage.record_sms(driver_id="D1", name="ALIYEV",
                       phone="+998901234567", route_id=ROUTE,
                       route_name="10-yo'nalish", schedule_date=recent_date,
                       message="test-xabar", status="PENDING",
                       message_id="mid-stale2")
    st_db = storage.db
    ph = st_db.ph
    st_db.execute(
        f"UPDATE sms_log SET created_at = {ph} WHERE message_id = {ph}",
        (old_created, "mid-stale2"))

    monkeypatch.setattr(sms_notify, "fetch_sms_status",
                        lambda mid: {"ok": True, "state": "DELIVERED"})
    monkeypatch.setattr(sms_notify, "_LAST_STALE_POLL", 0.0)

    res = sms_notify.retry_stale_pending(storage, stale_hours=1.0, limit=10)

    assert res["resolved"] == 1
    assert res["resent"] == 0
    assert fake_gateway == []
    rows = storage.query("SELECT status FROM sms_log ORDER BY id")
    assert [r["status"] for r in rows] == ["DELIVERED"]
