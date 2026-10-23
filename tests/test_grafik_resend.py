"""Tugma so'rovida grafikni qayta yuborish (resend_if_sent) testlari.

Qamrov:
  - allaqachon yuborilgan grafik o'zgarmagan bo'lsa ham qayta yuboriladi
    (Excel + rasm, "♻️" belgisisiz);
  - ma'lumot o'zgargan bo'lsa "♻️ YANGILANGAN" holda yuboriladi va faqat
    o'zgargan haydovchilarga kartalar ketadi;
  - qayta yuborish paytida ikkinchi so'rov yangi oqim ochmaydi ("waiting");
  - fingerprint holati yangilanadi; xulosa matnida yangi qatorlar chiqadi.
"""

from datetime import date

import pytest


def _duty(driver="Aliyev", did="d1", plate="01A123AA", graph="P1"):
    return {"graphs": [{
        "graphName": graph,
        "driverName": driver,
        "driverId": did,
        "plateNum": plate,
        "startTime": "06:00",
        "endTime": "14:00",
        "shiftName": "DU",
        "hasSecond": False,
    }]}


class _FakeRepo:
    duty = None

    def __init__(self, client):
        pass

    def by_date(self, rid, date_str):
        return _FakeRepo.duty


@pytest.fixture()
def resend_env(tmp_path, monkeypatch):
    """Qayta yuborish uchun izolyatsiyalangan muhit (tarmoqsiz)."""
    import daily_grafik as dg

    dg._INFLIGHT.clear()
    monkeypatch.setattr(dg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(dg, "DutyRepository", _FakeRepo)
    # Manzillar FAQAT bot ichida (`_bot_targets` haqiqiy ishlaydi):
    # yo'nalish egasi kompaniya chatlari — guruh aralashmasligini tekshirish.
    monkeypatch.setattr(
        "bm_automation.app.notifications.ops.context.chats_for_route",
        lambda rid: ["-100admin"])

    png = tmp_path / "sheet.png"
    png.write_bytes(b"png")
    monkeypatch.setattr(
        "bm_automation.app.services.export_service.build_export",
        lambda *a, **k: tmp_path / "grafik.xlsx")
    monkeypatch.setattr(
        "bm_automation.app.services.driver_sheet_service.run",
        lambda *a, **k: {"image": str(png)})

    sent = {"docs": [], "photos": [], "msgs": [], "cards": []}
    monkeypatch.setattr(
        dg, "send_document",
        lambda path, caption=None, chat_id=None:
        sent["docs"].append((path, caption, chat_id)))
    monkeypatch.setattr(
        dg, "send_photo",
        lambda path, caption=None, chat_id=None:
        sent["photos"].append((path, caption, chat_id)))
    monkeypatch.setattr(
        dg, "send_message",
        lambda text, chat_id=None: sent["msgs"].append((text, chat_id)))
    monkeypatch.setattr(
        dg, "_send_personal_cards",
        lambda res, rid, d, label, admin, only_driver_ids=None:
        sent["cards"].append((rid, label, only_driver_ids)))
    return dg, sent


DAY = date(2026, 10, 2)
ROUTE = ("", "r-1", "img", "B-80", 0)


def test_resend_unchanged_sends_files(resend_env):
    """O'zgarmagan bo'lsa ham tugma so'rovida Excel+rasm qayta yuboriladi."""
    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    duty = _duty()
    total, drivers = dg._duty_fingerprint(duty)
    dg._mark_grafik_sent(rid, ds, hash=total, drivers=drivers)  # avval yuborilgan
    _FakeRepo.duty = duty

    res = dg._process_route(ROUTE, None, DAY, {}, "777", "ISH KUNI",
                            retry_until_published=True, resend_if_sent=True)

    assert res == [("resent", "B-80", True)]
    # So'rov shaxsiy chatdan — FAQAT shu chatga (guruhlarga emas).
    assert len(sent["docs"]) == 1 and sent["docs"][0][2] == "777"
    assert len(sent["photos"]) == 1 and sent["photos"][0][2] == "777"
    # O'zgarishsiz — "♻️" belgisi yo'q, oddiy sarlavha.
    assert not sent["docs"][0][1].startswith("♻️")
    assert not sent["photos"][0][1].startswith("♻️")
    assert sent["cards"] == []  # o'zgarish yo'q — kartalar yuborilmaydi
    # Kuzatuv yangi (bir xil) hash bilan davom etadi.
    info = dg._grafik_sent_info(rid, ds)
    assert info["hash"] == total and info["drivers"] == drivers
    # Claim bo'shatilgan.
    assert "r-1|2026-10-02" not in dg._INFLIGHT


def test_resend_changed_marks_and_cards(resend_env):
    """O'zgargan bo'lsa yangilangan holda ketadi, kartalar faqat
    o'zgargan haydovchilarga, fingerprint yangilanadi."""
    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    old = _duty(driver="Aliyev", did="d1")
    old_total, old_drivers = dg._duty_fingerprint(old)
    dg._mark_grafik_sent(rid, ds, hash=old_total, drivers=old_drivers)

    new = _duty(driver="Karimov", did="d1")  # haydovchi almashgan
    _FakeRepo.duty = new
    new_total, new_drivers = dg._duty_fingerprint(new)
    assert new_total != old_total

    res = dg._process_route(ROUTE, None, DAY, {}, "777", "ISH KUNI",
                            retry_until_published=True, resend_if_sent=True)

    assert res == [("resent_new", "B-80", True)]
    assert sent["docs"][0][1].startswith("♻️ YANGILANGAN — ")
    assert sent["photos"][0][1].startswith("♻️ YANGILANGAN — ")
    assert sent["docs"][0][2] == "777" and sent["photos"][0][2] == "777"
    assert sent["cards"] == [(rid, "B-80", {"d1"})]
    info = dg._grafik_sent_info(rid, ds)
    assert info["hash"] == new_total and info["drivers"] == new_drivers


def test_resend_group_chat_goes_to_admin_chats(resend_env):
    """Guruh chat_id bilan so'rov — guruhga EMAS, ADMIN/DISPATCHER'ga.

    Grafik guruhlarga umuman yuborilmaydi: guruhdan kelgan so'rov ham
    bot ichidagi ADMIN/DISPATCHER chatlariga yo'naltiriladi."""
    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    duty = _duty()
    total, drivers = dg._duty_fingerprint(duty)
    dg._mark_grafik_sent(rid, ds, hash=total, drivers=drivers)
    _FakeRepo.duty = duty

    res = dg._process_route(ROUTE, None, DAY, {}, "-100guruh", "ISH KUNI",
                            retry_until_published=True, resend_if_sent=True)

    assert res == [("resent", "B-80", True)]
    assert [d[2] for d in sent["docs"]] == ["-100admin"]
    assert [p[2] for p in sent["photos"]] == ["-100admin"]


def test_resend_waits_when_claimed(resend_env):
    """Qayta yuborish oqimi ishlayotganda ikkinchi so'rov yangi oqim
    ochmaydi — takror yuborish bo'lmaydi."""
    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    dg._mark_grafik_sent(rid, ds, hash="h", drivers={})
    dg._INFLIGHT[f"{rid}|{ds}"] = "B-80"  # boshqa oqim ishlamoqda
    try:
        res = dg._process_route(ROUTE, None, DAY, {}, None, "ISH KUNI",
                                resend_if_sent=True)
    finally:
        dg._INFLIGHT.clear()
    assert res == [("waiting", "B-80", True)]
    assert sent["docs"] == []


def test_resend_without_flag_still_skips(resend_env):
    """resend_if_sent berilmasa — eski xatti-harakat: takrorlanmaydi."""
    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    dg._mark_grafik_sent(rid, ds, hash="h", drivers={})

    res = dg._process_route(ROUTE, None, DAY, {}, None, "ISH KUNI",
                            retry_until_published=True)

    assert res == [("already", "B-80", True)]
    assert sent["docs"] == [] and sent["photos"] == []


def test_resend_404_returns_error(resend_env, monkeypatch):
    """Qayta yuborishda duty topilmasa (404) — xato natijasi, jim qolmaydi."""
    from bm_automation.app.api.client import BMApiError

    dg, sent = resend_env
    rid, ds = "r-1", "2026-10-02"
    dg._mark_grafik_sent(rid, ds, hash="h", drivers={})

    class _NotFoundRepo:
        def __init__(self, client):
            pass

        def by_date(self, rid, date_str):
            raise BMApiError(404)

    monkeypatch.setattr(dg, "DutyRepository", _NotFoundRepo)
    res = dg._process_route(ROUTE, None, DAY, {}, None, "ISH KUNI",
                            resend_if_sent=True)

    assert res == [("error", "B-80", False)]
    assert sent["docs"] == []


def test_manual_request_skips_sms(resend_env, monkeypatch):
    """notify=False — qo'lda/bot so'rovida haydovchilarga SMS ketmaydi."""
    dg, sent = resend_env
    _FakeRepo.duty = _duty()
    calls = []
    monkeypatch.setattr(
        "bm_automation.app.notifications.sms_notify.send_sms_for_graphs",
        lambda *a, **k: calls.append((a, k)) or {
            "sent": 1, "skipped": 0, "failed": 0, "invalid": 0,
            "removed": 0, "errors": []})

    res = dg._process_route(ROUTE, None, DAY, {}, None, "ISH KUNI",
                            retry_until_published=False, notify=False)

    assert ("image", "B-80", True) in res
    assert calls == []  # SMS chaqirilmadi


def test_auto_flow_sends_sms(resend_env, monkeypatch):
    """notify=True (avtomatik oqim) — SMS yuboriladi."""
    dg, sent = resend_env
    _FakeRepo.duty = _duty()
    calls = []
    monkeypatch.setattr(
        "bm_automation.app.db.storage.get_storage", lambda: object())
    monkeypatch.setattr(
        "bm_automation.app.notifications.sms_notify.send_sms_for_graphs",
        lambda *a, **k: calls.append((a, k)) or {
            "sent": 1, "skipped": 0, "failed": 0, "invalid": 0,
            "removed": 0, "errors": []})

    res = dg._process_route(ROUTE, None, DAY, {}, None, "ISH KUNI",
                            retry_until_published=False, notify=True)

    assert ("image", "B-80", True) in res
    assert len(calls) == 1


def test_summary_text_has_resend_lines():
    import daily_grafik as dg

    text = dg._summary_text(
        {"resent_new": ["B-80"], "resent": ["10-YO'NALISH"]},
        date(2026, 10, 2), "Ertaga")
    assert "♻️ Yangilangan holda qayta yuborildi: B-80" in text
    assert "📤 Qayta yuborildi: 10-YO'NALISH" in text


def test_summarize_results_kinds():
    import daily_grafik as dg

    out = dg._summarize_results([("resent_new", "B-80", True),
                                 ("resent", "B-7", True)])
    assert out["resent_new"] == ["B-80"]
    assert out["resent"] == ["B-7"]
