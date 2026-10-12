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


def test_fresh_login_skips_browser_when_token_valid(monkeypatch):
    """Amaldagi token bo'lsa brauzer umuman ochilmaydi (server xotirasi)."""
    import importlib

    from bm_automation.app.core import tokens as tok
    from bm_automation.app.services import daily_service as ds
    bl = importlib.import_module("bm_automation.app.auth.browser_login")

    called = {"browser": 0}
    monkeypatch.setattr(tok, "has_valid_access_token", lambda *a, **k: True)
    monkeypatch.setattr(bl, "browser_login",
                        lambda **k: called.__setitem__("browser", called["browser"] + 1))

    assert ds._fresh_login() is True
    assert called["browser"] == 0


def test_fresh_login_uses_browser_when_no_token(monkeypatch):
    import importlib

    from bm_automation.app.core import tokens as tok
    from bm_automation.app.services import daily_service as ds
    bl = importlib.import_module("bm_automation.app.auth.browser_login")

    called = {"browser": 0}

    def _bl(**_k):
        called["browser"] += 1
        return {"access_token": "A"}

    monkeypatch.setattr(tok, "has_valid_access_token", lambda *a, **k: False)
    monkeypatch.setattr(bl, "browser_login", _bl)
    monkeypatch.setattr(ds.time, "sleep", lambda *a, **k: None)

    assert ds._fresh_login(retries=2) is True
    assert called["browser"] == 1


def test_run_daily_fresh_login_called_once(monkeypatch):
    """Retry siklida brauzer login faqat bir marta chaqirilsin."""
    from bm_automation.app.services import daily_service as ds

    calls = {"fresh": 0, "once": 0}

    class _Log:
        def start(self, **k):
            return 1

        def finish(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    monkeypatch.setattr(ds, "AutomationLogger", lambda: _Log())
    monkeypatch.setattr(ds, "_fresh_login",
                        lambda *a, **k: calls.__setitem__("fresh", calls["fresh"] + 1) or True)

    def _once(**k):
        calls["once"] += 1
        return [{"error": "boom"}]

    monkeypatch.setattr(ds, "run_daily_once", _once)
    monkeypatch.setattr(ds, "_has_failures", lambda r: True)
    monkeypatch.setattr(ds.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ds, "record_run", lambda *a, **k: None)
    monkeypatch.setattr(ds, "_notify_summary", lambda *a, **k: None)

    ds.run_daily(send=True, max_attempts=4, attempt_delay=0)

    assert calls["fresh"] == 1
    assert calls["once"] == 4


def _daily_env(monkeypatch, calls, profile, raise_token=False):
    """run_daily_once token oqimi testlari uchun izolyatsiya."""
    from types import SimpleNamespace

    from bm_automation.app.services import daily_service as ds

    class _FakeClient:
        def __init__(self):
            self.access_token = "main-access"
            self.refresh_token = "main-refresh"
            self.session = SimpleNamespace(headers={})

        def login(self):
            calls.append(("login",))

        def login_for_profile(self, pid, fallback_to_main=True):
            calls.append(("login_for_profile", pid, fallback_to_main))
            if raise_token:
                raise RuntimeError("boshqa xato")

        def login_by_profile(self, pid):  # eski yo'l — chaqirilmasligi kerak
            calls.append(("login_by_profile", pid))

    def _fake_sheet(*_a, **_k):
        calls.append(("run_sheet",))
        return {"image": "sheet.png", "sent": True}

    monkeypatch.setattr(ds, "BMClient", _FakeClient)
    monkeypatch.setattr(ds, "all_profiles", lambda: [profile])
    monkeypatch.setattr(ds, "run_sheet", _fake_sheet)
    monkeypatch.setattr(ds, "run_summary",
                        lambda *_a, **_k: {"excel_file": "monthly.xlsx",
                                           "days_with_duty": 5})
    return ds


def test_run_daily_once_uses_login_for_profile(monkeypatch):
    """Profil tokeni login_for_profile orqali olinadi (401/403 fallback) —
    kompaniya butunlay o'tkazib yuborilmaydi (AND ECO 401 holati)."""
    calls = []
    profile = {"name": "AND ECO TRANSPORT MCHJ", "profileId": "p-1",
               "routeVariantId": "r-1"}
    ds = _daily_env(monkeypatch, calls, profile)

    results = ds.run_daily_once(send=False)

    assert ("login_for_profile", "p-1", True) in calls
    assert not any(c[0] == "login_by_profile" for c in calls)
    assert ("run_sheet",) in calls
    assert results[0]["profile"] == "AND ECO TRANSPORT MCHJ"
    assert results[0].get("error") is None


def test_run_daily_once_token_error_skips_profile(monkeypatch):
    """401/403 dan boshqa token xatosi — kompaniya o'tkazib yuboriladi
    (xato yozib qo'yiladi, jadval yaratilmaydi)."""
    calls = []
    profile = {"name": "AND ECO TRANSPORT MCHJ", "profileId": "p-1",
               "routeVariantId": "r-1"}
    ds = _daily_env(monkeypatch, calls, profile, raise_token=True)

    results = ds.run_daily_once(send=False)

    assert ("run_sheet",) not in calls
    assert results[0]["error"].startswith("kompaniya tokeni olinmadi")


def test_run_all_uses_login_for_profile(monkeypatch, tmp_path):
    """'Grafik' oqimi (run_all) ham 401/403 da asosiy tokenga qaytadi."""
    from bm_automation.app.core import profiles as profiles_mod
    from bm_automation.app.services import driver_sheet_service as dss

    calls = []
    profile = {"name": "AND ECO TRANSPORT MCHJ", "profileId": "p-9",
               "routeVariantId": "r-9"}
    monkeypatch.setattr(profiles_mod, "all_profiles", lambda: [profile])

    class _FakeClient:
        def login_for_profile(self, pid, fallback_to_main=True):
            calls.append(("login_for_profile", pid, fallback_to_main))

        def login_by_profile(self, pid):
            calls.append(("login_by_profile", pid))

    def _fake_run(client, route_id, **_kw):
        calls.append(("run", route_id))
        return {"image": "x.png", "profile": profile["name"]}

    monkeypatch.setattr(dss, "run", _fake_run)

    results = dss.run_all(_FakeClient(), date_str="2026-10-09",
                          out_dir=str(tmp_path), send=False)

    assert ("login_for_profile", "p-9", True) in calls
    assert not any(c[0] == "login_by_profile" for c in calls)
    assert results[0]["profile"] == "AND ECO TRANSPORT MCHJ"
    assert results[0]["image"] == "x.png"


# ------------------------------------------------ oylik dedup (retry spam)

def _monthly_env(monkeypatch, tmp_path, calls):
    """run_daily_once(send=True) oylik dedup testlari uchun izolyatsiya."""
    from types import SimpleNamespace

    from bm_automation.app.db import storage as storage_mod
    from bm_automation.app.db import sync as sync_mod
    from bm_automation.app.notifications import driver_schedule as drv_mod
    from bm_automation.app.notifications import sms_notify as sms_mod
    from bm_automation.app.services import daily_service as ds

    sent_docs = []

    class _FakeClient:
        def __init__(self):
            self.access_token = "main-access"
            self.refresh_token = "main-refresh"
            self.session = SimpleNamespace(headers={})

        def login(self):
            calls.append(("login",))

        def login_for_profile(self, pid, fallback_to_main=True):
            calls.append(("login_for_profile", pid, fallback_to_main))

    profile = {"name": "AND ECO TRANSPORT MCHJ", "profileId": "p-1",
               "routeVariantId": "r-1"}
    monkeypatch.setattr(ds, "BMClient", _FakeClient)
    monkeypatch.setattr(ds, "all_profiles", lambda: [profile])
    monkeypatch.setattr(ds, "run_sheet",
                        lambda *_a, **_k: {"image": "sheet.png", "sent": True,
                                           "no_group": True})
    monkeypatch.setattr(ds, "run_summary",
                        lambda *_a, **_k: {"excel_file": "monthly.xlsx",
                                           "days_with_duty": 5})
    monkeypatch.setattr(ds, "send_document",
                        lambda f, caption="", chat_id=None, **kw:
                        sent_docs.append((str(chat_id), f)) or True)
    monkeypatch.setattr(ds, "resend_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(ds, "MONTHLY_SENT_STATE", tmp_path / "daily_monthly_sent.json")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: object())
    monkeypatch.setattr(sync_mod, "sync_duties",
                        lambda *a, **k: SimpleNamespace(error=None))
    monkeypatch.setattr(drv_mod, "send_driver_schedule_notifications",
                        lambda *a, **k: {"sent": 0, "failed": 0, "errors": []})
    monkeypatch.setattr(sms_mod, "send_driver_schedule_sms",
                        lambda *a, **k: {"sent": 0, "failed": 0, "invalid": 0,
                                         "removed": 0, "errors": []})
    return ds, sent_docs


def test_run_daily_once_monthly_not_resent_on_retry(monkeypatch, tmp_path):
    """Retry urinishida (jadval 404 tufayli) oylik Excel qayta
    yuborilmaydi — bugungi 6 marta takror yuborilish muammosi."""
    calls = []
    ds, sent_docs = _monthly_env(monkeypatch, tmp_path, calls)

    r1 = ds.run_daily_once(send=True)
    assert r1[0]["monthly_sent"] is True
    assert len(sent_docs) == 1
    assert _has_failures(r1) is False

    r2 = ds.run_daily_once(send=True)
    assert r2[0]["monthly_sent"] is True
    assert r2[0]["monthly_already_sent"] is True
    assert not r2[0].get("monthly_error")
    assert len(sent_docs) == 1              # takror yuborilmadi
    assert _has_failures(r2) is False


def test_run_daily_once_monthly_manual_resend_always_sends(monkeypatch, tmp_path):
    """Qo'lda /resend (shaxsiy chat_id) dedupdan qat'i nazar yuboriladi."""
    calls = []
    ds, sent_docs = _monthly_env(monkeypatch, tmp_path, calls)

    ds.run_daily_once(send=True)                 # avtomatik — 1 marta
    ds.run_daily_once(send=True, chat_id="777")  # qo'lda — yana yuboriladi

    assert len(sent_docs) == 2
    assert sent_docs[1][0] == "777"


def test_run_daily_once_monthly_group_chat_dedups(monkeypatch, tmp_path):
    """Guruh chat_id bilan ham retry takror yubormaydi."""
    calls = []
    ds, sent_docs = _monthly_env(monkeypatch, tmp_path, calls)

    ds.run_daily_once(send=True, chat_id="-100G")
    ds.run_daily_once(send=True, chat_id="-100G")

    assert len(sent_docs) == 1


def test_run_daily_once_monthly_error_allows_retry(monkeypatch, tmp_path):
    """Oylik yuborish xato bo'lsa belgilanmaydi — keyingi urinishda qayta."""
    calls = []
    ds, sent_docs = _monthly_env(monkeypatch, tmp_path, calls)

    attempts = {"n": 0}

    def _fail_once(f, caption="", chat_id=None, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("telegram xatosi")
        sent_docs.append((str(chat_id), f))
        return True

    monkeypatch.setattr(ds, "send_document", _fail_once)

    r1 = ds.run_daily_once(send=True)
    assert r1[0]["monthly_error"] == "telegram xatosi"

    r2 = ds.run_daily_once(send=True)
    assert r2[0]["monthly_sent"] is True
    assert len(sent_docs) == 1
