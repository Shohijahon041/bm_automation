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


def test_run_sheet_sends_only_to_own_route_group(monkeypatch, tmp_path):
    """Jadval rasmi FAQAT o'sha yo'nalishga biriktirilgan guruhga yuboriladi."""
    from bm_automation.app.notifications.ops import group_departures as gd
    from bm_automation.app.services import driver_sheet_service as dss
    from bm_automation.app.notifications import telegram as tg

    sent = []

    monkeypatch.setattr(gd, "_route_chats", lambda: {
        "r-asl": ["-100ASL"], "r-ferg": ["-100FERG"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100ASL", "-100FERG"])
    monkeypatch.setattr(dss, "SENT_STATE", tmp_path / "daily_sheet_sent.json")
    monkeypatch.setattr(tg, "send_photo",
                        lambda f, caption="", chat_id=None, **kw:
                        sent.append(chat_id) or True)

    class _FakeClient:
        def get(self, *_a, **_k):
            return {}

    duty = {"graphs": [{"graphName": "P1", "driverName": "A A",
                        "driverId": "d1", "plateNum": "01A001",
                        "startTime": "06:00", "endTime": "18:00",
                        "shiftName": "KUN"}]}

    monkeypatch.setattr(dss, "make_sheet_image", lambda *a, **k: None)
    res = dss.run(_FakeClient(), "r-ferg", date_str="2026-10-01",
                  out_dir=str(tmp_path), send=True, chat_id=None,
                  duty_data=duty)
    assert res["sent"] is True
    assert res["sent_to"] == ["-100FERG"]
    assert sent == ["-100FERG"]


def test_run_sheet_retry_does_not_resend(monkeypatch, tmp_path):
    """Retryda (AND ECO 401 sabab) rasm qayta yuborilmaydi."""
    from bm_automation.app.notifications.ops import group_departures as gd
    from bm_automation.app.services import driver_sheet_service as dss
    from bm_automation.app.notifications import telegram as tg

    sent = []
    monkeypatch.setattr(gd, "_route_chats", lambda: {"r-ferg": ["-100FERG"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100FERG"])
    monkeypatch.setattr(dss, "SENT_STATE", tmp_path / "daily_sheet_sent.json")
    monkeypatch.setattr(tg, "send_photo",
                        lambda f, caption="", chat_id=None, **kw:
                        sent.append(chat_id) or True)

    class _FakeClient:
        def get(self, *_a, **_k):
            return {}

    duty = {"graphs": [{"graphName": "P1", "driverName": "A A",
                        "driverId": "d1", "plateNum": "01A001",
                        "startTime": "06:00", "endTime": "18:00",
                        "shiftName": "KUN"}]}
    monkeypatch.setattr(dss, "make_sheet_image", lambda *a, **k: None)

    first = dss.run(_FakeClient(), "r-ferg", date_str="2026-10-01",
                    out_dir=str(tmp_path), send=True, chat_id=None,
                    duty_data=duty)
    assert first["sent"] is True and sent == ["-100FERG"]

    second = dss.run(_FakeClient(), "r-ferg", date_str="2026-10-01",
                     out_dir=str(tmp_path), send=True, chat_id=None,
                     duty_data=duty)
    assert sent == ["-100FERG"]              # ikkinchisi yuborilmadi
    assert second["already_sent"] is True
    assert second["sent"] is True            # retry muammosiz yakunlanadi

    # Boshqa kun — yana yuboriladi.
    dss.run(_FakeClient(), "r-ferg", date_str="2026-10-02",
            out_dir=str(tmp_path), send=True, chat_id=None, duty_data=duty)
    assert sent == ["-100FERG", "-100FERG"]

    # Qo'lda "qayta yuborish" (chat_id berilgan) — har doim yuboradi.
    dss.run(_FakeClient(), "r-ferg", date_str="2026-10-01",
            out_dir=str(tmp_path), send=True, chat_id="-100FERG",
            duty_data=duty)
    assert sent == ["-100FERG", "-100FERG", "-100FERG"]


def test_run_sheet_unmapped_route_does_not_leak(monkeypatch, tmp_path):
    """Xaritada yo'q yo'nalish boshqa yo'nalish guruhiga yuborilmaydi."""
    from bm_automation.app.notifications.ops import group_departures as gd
    from bm_automation.app.services import driver_sheet_service as dss
    from bm_automation.app.notifications import telegram as tg

    sent = []
    monkeypatch.setattr(gd, "_route_chats", lambda: {
        "r-asl": ["-100ASL"], "r-ferg": ["-100FERG"]})
    monkeypatch.setattr(gd, "_targets", lambda: ["-100ASL", "-100FERG"])
    monkeypatch.setattr(dss, "SENT_STATE", tmp_path / "daily_sheet_sent.json")
    monkeypatch.setattr(tg, "send_photo",
                        lambda f, caption="", chat_id=None, **kw:
                        sent.append(chat_id) or True)

    class _FakeClient:
        def get(self, *_a, **_k):
            return {}

    duty = {"graphs": [{"graphName": "P1", "driverName": "A A",
                        "driverId": "d1", "plateNum": "01A001",
                        "startTime": "06:00", "endTime": "18:00",
                        "shiftName": "KUN"}]}
    monkeypatch.setattr(dss, "make_sheet_image", lambda *a, **k: None)
    res = dss.run(_FakeClient(), "r-eco", date_str="2026-10-01",
                  out_dir=str(tmp_path), send=True, chat_id=None,
                  duty_data=duty)
    assert res["sent"] is False
    assert res["no_group"] is True
    assert sent == []


def test_auth_error_detection():
    assert _auth_error([{"error": "avtorizatsiya talab qilinadi"}]) is True
    assert _auth_error([{"monthly_error": "HTTP 401 talab"}]) is True
    assert _auth_error([{"error": "boshqa xato"}]) is False
