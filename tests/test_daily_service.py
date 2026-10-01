"""Kunlik vazifa mantiqiy tekshiruvlari."""

from bm_automation.app.services.daily_service import _auth_error, _has_failures


def test_has_failures_empty_is_failure():
    assert _has_failures([]) is True


def test_has_failures_all_sent():
    assert _has_failures([{"sent": True, "monthly_sent": True}]) is False


def test_has_failures_on_error():
    assert _has_failures([{"error": "HTTP 500"}]) is True
    assert _has_failures([{"monthly_error": "0 kun"}]) is True


def test_has_failures_unsent_image():
    assert _has_failures([{"image": "x.png", "sent": False}]) is True


def test_has_failures_no_group_is_not_failure():
    """Guruh biriktirilmagan yo'nalish — retry qilmaslik (xato emas)."""
    assert _has_failures([{"image": "x.png", "sent": False,
                           "no_group": True}]) is False


def _sheet_env(monkeypatch, tmp_path, admin_targets):
    """driver_sheet_service send testlari uchun izolyatsiya."""
    from bm_automation.app.notifications import telegram as tg
    from bm_automation.app.notifications.ops import grafik_sms as gs
    from bm_automation.app.services import driver_sheet_service as dss

    sent = []
    monkeypatch.setattr(gs, "_targets", lambda: list(admin_targets))
    monkeypatch.setattr(dss, "SENT_STATE", tmp_path / "daily_sheet_sent.json")
    monkeypatch.setattr(tg, "send_photo",
                        lambda f, caption="", chat_id=None, **kw:
                        sent.append(chat_id) or True)
    monkeypatch.setattr(tg, "resend_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(dss, "make_sheet_image", lambda *a, **k: None)

    class _FakeClient:
        def get(self, *_a, **_k):
            return {}

    duty = {"graphs": [{"graphName": "P1", "driverName": "A A",
                        "driverId": "d1", "plateNum": "01A001",
                        "startTime": "06:00", "endTime": "18:00",
                        "shiftName": "KUN"}]}
    return dss, _FakeClient(), duty, sent


def test_run_sheet_automatic_goes_to_admin_chats(monkeypatch, tmp_path):
    """Avtomatik oqim (chat_id yo'q): jadval FAQAT bot ichidagi
    ADMIN/DISPATCHER chatlariga — yo'nalish guruhlariga hech qachon."""
    from bm_automation.app.notifications.ops import group_departures as gd

    dss, client, duty, sent = _sheet_env(
        monkeypatch, tmp_path, ["-100ADMIN", "-100DISP"])
    # Guruh sozlamalari mavjud bo'lsa ham ular UMUMAN ishlatilmaydi.
    monkeypatch.setattr(gd, "_route_chats",
                        lambda: {"r-ferg": ["-100FERG"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100FERG"])

    res = dss.run(client, "r-ferg", date_str="2026-10-01",
                  out_dir=str(tmp_path), send=True, chat_id=None,
                  duty_data=duty)
    assert res["sent"] is True
    assert res["sent_to"] == ["-100ADMIN", "-100DISP"]
    assert sent == ["-100ADMIN", "-100DISP"]


def test_run_sheet_retry_does_not_resend(monkeypatch, tmp_path):
    """Retryda (AND ECO 401 sabab) rasm qayta yuborilmaydi."""
    dss, client, duty, sent = _sheet_env(monkeypatch, tmp_path, ["-100ADMIN"])

    first = dss.run(client, "r-ferg", date_str="2026-10-01",
                    out_dir=str(tmp_path), send=True, chat_id=None,
                    duty_data=duty)
    assert first["sent"] is True and sent == ["-100ADMIN"]

    second = dss.run(client, "r-ferg", date_str="2026-10-01",
                     out_dir=str(tmp_path), send=True, chat_id=None,
                     duty_data=duty)
    assert sent == ["-100ADMIN"]             # ikkinchisi yuborilmadi
    assert second["already_sent"] is True
    assert second["sent"] is True            # retry muammosiz yakunlanadi

    # Boshqa kun — yana yuboriladi.
    dss.run(client, "r-ferg", date_str="2026-10-02",
            out_dir=str(tmp_path), send=True, chat_id=None, duty_data=duty)
    assert sent == ["-100ADMIN", "-100ADMIN"]

    # Qo'lda "qayta yuborish" (shaxsiy chat_id) — har doim o'sha chatga.
    dss.run(client, "r-ferg", date_str="2026-10-01",
            out_dir=str(tmp_path), send=True, chat_id="777",
            duty_data=duty)
    assert sent == ["-100ADMIN", "-100ADMIN", "777"]


def test_run_sheet_group_chat_never_receives(monkeypatch, tmp_path):
    """Guruh chat_id bilan chaqiruv ham guruhga yuborilmaydi — bot ichida
    qoladi (ADMIN/DISPATCHER) va takror yuborish qoidasi bilan ishlaydi."""
    dss, client, duty, sent = _sheet_env(monkeypatch, tmp_path, ["-100ADMIN"])

    res = dss.run(client, "r-ferg", date_str="2026-10-01",
                  out_dir=str(tmp_path), send=True, chat_id="-100GURUH",
                  duty_data=duty)
    assert res["sent"] is True
    assert sent == ["-100ADMIN"]
    assert "-100GURUH" not in sent

    # Ikkinchi chaqiruv: guruh so'rovi qo'lda hisoblanmaydi — retry kabi
    # takror yubormaydi.
    res2 = dss.run(client, "r-ferg", date_str="2026-10-01",
                   out_dir=str(tmp_path), send=True, chat_id="-100GURUH",
                   duty_data=duty)
    assert sent == ["-100ADMIN"]
    assert res2["already_sent"] is True


def test_run_sheet_no_targets_no_group(monkeypatch, tmp_path):
    """Yuboriladigan chat umuman yo'q (ADMIN/DISPATCHER ham yo'q) —
    jim qoladi, guruhlarga oqib ketmaydi."""
    from bm_automation.app.notifications.ops import group_departures as gd

    dss, client, duty, sent = _sheet_env(monkeypatch, tmp_path, [])
    monkeypatch.setattr(gd, "_route_chats",
                        lambda: {"r-ferg": ["-100FERG"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100FERG"])

    res = dss.run(client, "r-ferg", date_str="2026-10-01",
                  out_dir=str(tmp_path), send=True, chat_id=None,
                  duty_data=duty)
    assert res["sent"] is False
    assert res["no_group"] is True
    assert sent == []


def test_auth_error_detection():
    assert _auth_error([{"error": "avtorizatsiya talab qilinadi"}]) is True
    assert _auth_error([{"monthly_error": "HTTP 401 talab"}]) is True
    assert _auth_error([{"error": "boshqa xato"}]) is False
