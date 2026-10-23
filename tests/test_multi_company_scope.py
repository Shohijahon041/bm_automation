"""Ko'p korxonali (multi-tenant) scoping testlari.

Qamrov:
  - kompaniya/yo'nalish chatlari boshqa firma dispetcheriga oqmaydi;
  - grafik manzillari yo'nalish egasi kompaniya bilan cheklanadi;
  - kunlik xulosa va jarima hisoboti har kompaniyaga alohida boradi;
  - muammo alertlari holati kompaniya bo'yicha alohida yuritiladi.
"""

import types
from datetime import datetime

from bm_automation.app.notifications.ops import (context, daily_summary,
                                                 fines_report, grafik_sms,
                                                 problem_alerts)
from bm_automation.app.notifications.ops.roles import Role


def _profiles():
    return [
        {"name": "A MCHJ", "routeVariantId": "r-a", "ownerChatIds": [700]},
        {"name": "B MCHJ", "routeVariantId": "r-b", "ownerChatIds": [800]},
    ]


def test_chats_for_route_only_owner(monkeypatch):
    monkeypatch.setattr(context, "all_profiles", _profiles)
    monkeypatch.setattr(context, "configured_roles",
                        lambda: {111: Role.ADMIN, 222: Role.DISPATCHER,
                                 333: Role.DISPATCHER})
    monkeypatch.setattr(
        context, "allowed_names",
        lambda cid: ["A MCHJ"] if cid == 222
        else (["B MCHJ"] if cid == 333 else []))

    assert context.companies_for_route("r-a") == ["A MCHJ"]
    assert context.chats_for_route("r-a") == [111, 222, 700]
    assert context.chats_for_route("r-b") == [111, 333, 800]


def test_bot_targets_scoped_and_personal(monkeypatch):
    monkeypatch.setattr(context, "chats_for_route", lambda rid: [111, 222])
    assert grafik_sms.bot_targets("r-a", None) == ["111", "222"]
    assert grafik_sms.bot_targets("r-a", "-100GROUP") == ["111", "222"]
    assert grafik_sms.bot_targets("r-a", "777") == ["777"]


def test_daily_summary_per_company(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(daily_summary, "STATE_FILE", tmp_path / "ds.json")
    monkeypatch.setattr(
        daily_summary, "telegram_settings",
        lambda: {"daily_summary": "on", "daily_summary_hour": "0"})
    monkeypatch.setattr(
        daily_summary, "get_storage",
        lambda: types.SimpleNamespace(enabled=True))
    monkeypatch.setattr(daily_summary, "configured_roles",
                        lambda: {111: Role.ADMIN})
    monkeypatch.setattr(daily_summary, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(
        daily_summary.time, "localtime",
        lambda: types.SimpleNamespace(tm_hour=9))
    monkeypatch.setattr(
        daily_summary, "send_message",
        lambda text, chat_id=None, **kw: sent.append((text, chat_id)))
    monkeypatch.setattr(daily_summary.context, "company_names",
                        lambda: ["A MCHJ", "B MCHJ"])
    monkeypatch.setattr(
        daily_summary.context, "chats_for_company",
        lambda name: [111, 222] if name == "A MCHJ" else [333])

    def _summary(filters=None):
        return "GLOBAL" if not filters else f"CO:{filters.get('company')}"

    monkeypatch.setattr(daily_summary.assistant, "daily_summary", _summary)
    monkeypatch.setattr(daily_summary.assistant, "_maybe_llm",
                        lambda _a, local, _q: local)

    daily_summary.check_and_send()

    assert sent == [("GLOBAL", "111"),
                    ("CO:A MCHJ", "222"),
                    ("CO:B MCHJ", "333")]


class _LastDay:
    @classmethod
    def today(cls):
        from datetime import date
        return date(2026, 10, 31)


def test_fines_report_per_company(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(fines_report, "STATE_FILE", tmp_path / "fr.json")
    monkeypatch.setattr(fines_report, "date", _LastDay)
    monkeypatch.setattr(fines_report, "get_storage",
                        lambda: types.SimpleNamespace(enabled=True))
    monkeypatch.setattr(fines_report, "enabled", lambda: True)
    monkeypatch.setattr(fines_report, "_LAST_CHECK_AT", 0.0)
    monkeypatch.setattr(fines_report, "_targets", lambda: [111])
    monkeypatch.setattr(fines_report.context, "company_names",
                        lambda: ["A MCHJ", "B MCHJ"])
    monkeypatch.setattr(
        fines_report.context, "chats_for_company",
        lambda name: [111, 222] if name == "A MCHJ" else [333])
    monkeypatch.setattr(
        fines_report, "build_text",
        lambda month="", company="": ("GLOBAL" if not company
                                      else f"CO:{company}"))
    monkeypatch.setattr(
        fines_report, "send_message",
        lambda text, chat_id=None, **kw: sent.append((text, chat_id)))

    fines_report.check_and_send()

    assert sent == [("GLOBAL", "111"),
                    ("CO:A MCHJ", "222"),
                    ("CO:B MCHJ", "333")]


def test_fines_build_text_company_filter(tmp_path, monkeypatch):
    """Jarima hisoboti schedules orqali faqat o'z yo'nalishi haydovchilarini oladi."""
    from bm_automation.app.core import profiles as profiles_mod
    from bm_automation.app.db.storage import storage_for
    from tests.sqlite_backend import SQLiteDatabase

    st = storage_for(SQLiteDatabase(str(tmp_path / "scope.db")))
    st.insert("drivers", {"external_id": "d1", "full_name": "Ali"})
    st.insert("drivers", {"external_id": "d2", "full_name": "Vali"})
    st.insert("driver_fines", {"driver_id": "d1", "date": "2026-10-05",
                               "amount": 100, "status": "ACTIVE"})
    st.insert("driver_fines", {"driver_id": "d2", "date": "2026-10-06",
                               "amount": 200, "status": "ACTIVE"})
    st.insert("schedules", {"date": "2026-10-05", "route_id": "r-a",
                            "driver_id": "d1"})
    st.insert("schedules", {"date": "2026-10-06", "route_id": "r-b",
                            "driver_id": "d2"})
    monkeypatch.setattr(fines_report, "get_storage", lambda: st)
    monkeypatch.setattr(profiles_mod, "all_profiles", _profiles)

    text = fines_report.build_text("2026-10", "A MCHJ")
    assert text and "Ali" in text and "Vali" not in text
    assert "A MCHJ" in text


def test_problem_state_per_company_changes_only_own():
    now = datetime(2026, 10, 19, 12, 0)
    counts = {"gps": 2, "technical": 0, "schedule": 0, "unknown": 0}
    state, text = problem_alerts._next_state(
        {}, "2026-10-19", counts, now, "A MCHJ")
    assert state and text is None  # birinchi — jim baseline

    state2, text2 = problem_alerts._next_state(
        state, "2026-10-19", {**counts, "gps": 3}, now, "A MCHJ")
    assert state2 and text2
    assert "A MCHJ" in text2 and "GPS: +1" in text2

    state3, text3 = problem_alerts._next_state(
        state2, "2026-10-19", {**counts, "gps": 3}, now, "B MCHJ")
    assert text3 is None
    assert state3 is None  # o'zgarish yo'q — holat yozilmaydi
