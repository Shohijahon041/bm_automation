"""Ma'lumotlar bazasi qatlami testlari (SQLite / tmp).

O'z ichiga oladi: sxema yaratish, idempotent upsert (dublikatsiz),
qayta yuklashda yozuvni buzmaslik, tranzaksiya rollback, DB ishlamasa
degrade, BM API → DB sync va AutomationLogger.
"""

import pytest

from bm_automation.app.db.base import Database
from bm_automation.app.db.models import (SyncSource, TABLES, TripRecord,
                                         TripStatus, now_utc)
from bm_automation.app.db.storage import (Storage, get_storage,
                                          reset_storage, storage_for)
from tests.sqlite_backend import SQLiteDatabase


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "test.db"))
    return storage_for(db)


def test_schema_creates_all_tables(storage):
    assert storage.enabled
    assert storage.db.available
    tables = {t["name"] for t in storage.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("profiles", "routes", "vehicles", "drivers", "driver_profiles",
                  "driver_work_logs", "driver_fines", "duties",
                  "schedules", "waybills", "trips", "route_daily", "trip_statuses",
                  "reports", "report_runs", "errors", "notifications",
                  "automation_runs"):
        assert table in tables, f"jadval yo'q: {table}"


def test_init_db_enables_rls_on_postgres():
    from contextlib import nullcontext

    from bm_automation.app.db.schema import init_db

    calls = []

    class FakeDB:
        driver = "postgres"
        ph = "%s"
        available = True
        transaction = nullcontext

        def execute(self, sql, params=None):
            calls.append(sql)
            return 1

        def executescript(self, script):
            for stmt in script.split(";\n"):
                calls.append(stmt)

    assert init_db(FakeDB())
    alters = [s for s in calls if "ENABLE ROW LEVEL SECURITY" in s]
    assert len(alters) == len(TABLES)
    assert all(f'public."{t}"' in s for t, s in zip(TABLES, alters))


def test_seed_statuses(storage):
    rows = storage.query("SELECT code FROM trip_statuses ORDER BY code")
    codes = [r["code"] for r in rows]
    assert codes == sorted(TripStatus.values())
    assert len(codes) == 6


def test_upsert_profile_idempotent(storage):
    storage.save_profile(external_id="p1", name="FERGANATEX",
                         route_id="r1", data={"x": 1})
    storage.save_profile(external_id="p1", name="FERGANATEX",
                         route_id="r1", data={"x": 1})
    rows = storage.query("SELECT * FROM profiles")
    assert len(rows) == 1
    assert storage.query("SELECT COUNT(*) AS n FROM profiles")[0]["n"] == 1


def test_bulk_upsert_dedups_keys(storage):
    """Bitta chunk ichidagi dublikat kalitlar ON CONFLICT'ni buzmaydi.

    Haqiqiy waybill hisobotida bir avtobus kuniga bir necha marta bir
    yo'nalishda yuradi — (date, plate, direction) kaliti takrorlanadi.
    Row-by-row semantika: oxirgi qiymat yutadi.
    """
    rows = [
        {"date": "2026-08-10", "route_id": "r1", "plate_number": "01A123AA",
         "direction": "UP", "vehicle_id": "v1", "driver_id": "d1",
         "status": "ACCEPTED", "data": "{}"},
        {"date": "2026-08-10", "route_id": "r1", "plate_number": "01A123AA",
         "direction": "UP", "vehicle_id": "v1", "driver_id": "d9",
         "status": "REJECTED", "data": "{}"},
    ]
    inserted, updated = storage.bulk_upsert(
        "waybills", ["date", "route_id", "plate_number", "direction"], rows)
    assert inserted == 1 and updated == 0
    got = storage.query("SELECT driver_id, status FROM waybills")
    assert len(got) == 1
    assert got[0]["driver_id"] == "d9"   # oxirgi qiymat saqlanadi
    assert got[0]["status"] == "REJECTED"


def test_reload_does_not_corrupt(storage):
    storage.save_route(external_id="r1", name="Eski nom",
                       entity_type="ROUTE", is_brutto=True)
    created_before = storage.query(
        "SELECT created_at, updated_at FROM routes WHERE external_id='r1'")[0]
    storage.save_route(external_id="r1", name="Yangi nom",
                       entity_type="ROUTE", is_brutto=True)
    rows = storage.query("SELECT * FROM routes")
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Yangi nom"          # ma'lumot yangilandi
    assert row["created_at"] == created_before["created_at"]  # buzilmadi
    assert row["updated_at"] != created_before["updated_at"]


def test_trip_unique_key(storage):
    base = TripRecord(date="2026-08-10", route_id="r1", vehicle_id="v1",
                      driver_id="d1", planned_time="06:00",
                      status=TripStatus.ACCEPTED.value, source=SyncSource.DUTY.value)
    assert storage.save_trip(base) == "inserted"
    # bir xil trip yana kelsa — yangilanadi, dublikat yaratilmaydi
    assert storage.save_trip(base) == "updated"
    other = TripRecord(date="2026-08-10", route_id="r1", vehicle_id="v1",
                       driver_id="d2", planned_time="06:00",
                       source=SyncSource.DUTY.value)
    assert storage.save_trip(other) == "inserted"
    rows = storage.query("SELECT * FROM trips")
    assert len(rows) == 2


def test_trip_status_normalize():
    assert TripStatus.normalize("ACCEPTED") == "ACCEPTED"
    assert TripStatus.normalize("NOMA_LUM") == "PENDING_ACCESS"
    assert TripStatus.normalize("") == "PENDING_ACCESS"
    assert TripStatus.normalize(None) == "PENDING_ACCESS"


def test_transaction_rollback(storage):
    try:
        with storage.db.transaction():
            storage.save_driver(external_id="d1", full_name="Aliyev")
            storage.save_driver(external_id="d2", full_name="Karimov")
            raise RuntimeError("to'xtat")
    except RuntimeError:
        pass
    assert storage.query("SELECT COUNT(*) AS n FROM drivers")[0]["n"] == 0


def test_transaction_commit(storage):
    with storage.db.transaction():
        storage.save_driver(external_id="d1", full_name="Aliyev")
    assert storage.query("SELECT COUNT(*) AS n FROM drivers")[0]["n"] == 1


def test_driver_profile_and_financial_records(storage):
    storage.save_driver(external_id="d1", full_name="Aliyev")
    storage.save_driver_profile(
        "d1", phone="998901234567", rating=4.7, km_rate=1250,
notification_enabled=True, passport_number="AB1234567")
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 128.5, 7, "Kundalik qayd")
    assert storage.add_driver_fine("d1", "2026-08-10", 50000, "Kechikish")
    profile = storage.find("driver_profiles", driver_id="d1")
    assert profile["rating"] == 4.7
    assert profile["notification_enabled"] == 1
    assert storage.query("SELECT distance_km FROM driver_work_logs")[0]["distance_km"] == 128.5
    assert storage.query("SELECT amount FROM driver_fines")[0]["amount"] == 50000


def test_delete_driver_work_log(storage):
    """Kunlik km qaydi natural kalit (date, driver, vehicle) bo'yicha
    o'chiriladi; boshqa qaydga ta'sir qilmaydi."""
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 100.0, 2, "A")
    storage.save_driver_work_log("2026-08-10", "d1", "v2", 25.0, 1, "B")
    storage.save_driver_work_log("2026-08-11", "d1", "v1", 80.0, 3, "C")
    assert not storage.delete_driver_work_log("2026-08-10", "d2", "v1")
    assert storage.delete_driver_work_log("2026-08-10", "d1", "v1")
    rows = storage.query("SELECT date, vehicle_id, distance_km FROM driver_work_logs ORDER BY date, vehicle_id")
    assert len(rows) == 2
    assert rows[0]["vehicle_id"] == "v2"
    assert rows[1]["distance_km"] == 80.0


def test_driver_schedule_notification_is_deduplicated(storage, monkeypatch):
    from bm_automation.app.notifications import driver_schedule

    storage.save_driver(external_id="d1", full_name="Aliyev Aliy")
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    storage.save_driver_profile("d1", notification_enabled=True,
                                notification_target="12345")
    storage.save_schedule("2026-08-10", "r1", "G-1", "d1", "v1",
                          start_time="06:30")
    sent = []
    monkeypatch.setattr(driver_schedule, "send_message",
                        lambda message, chat_id: sent.append((message, chat_id)) or True)
    first = driver_schedule.send_driver_schedule_notifications(storage, "r1", "2026-08-10")
    second = driver_schedule.send_driver_schedule_notifications(storage, "r1", "2026-08-10")
    assert first["sent"] == 1 and not first["failed"]
    assert second["skipped"] == 1
    assert len(sent) == 1 and sent[0][1] == "12345"


def test_driver_schedule_prefers_telegram_chat_id(storage, monkeypatch):
    from bm_automation.app.notifications import driver_schedule

    storage.save_driver(external_id="d2", full_name="Karimov Karim")
    storage.save_vehicle(external_id="v2", plate_number="01B456AA", route_id="r1")
    storage.save_driver_profile("d2", notification_enabled=True,
                                notification_target="99890123")
    storage.link_driver_telegram("d2", 777)
    storage.save_schedule("2026-08-10", "r1", "G-2", "d2", "v2",
                          start_time="07:00")
    sent = []
    monkeypatch.setattr(driver_schedule, "send_message",
                        lambda message, chat_id: sent.append((message, chat_id)) or True)
    res = driver_schedule.send_driver_schedule_notifications(storage, "r1", "2026-08-10")
    assert res["sent"] == 1 and not res["failed"]
    assert len(sent) == 1 and sent[0][1] == "777"


def test_link_driver_telegram_enables_notifications(storage):
    storage.save_driver(external_id="d3", full_name="Toshev Toshe")
    storage.save_driver_profile("d3", notification_enabled=False,
                                notification_target="99890124")
    storage.link_driver_telegram("d3", 888)
    profile = storage.find("driver_profiles", driver_id="d3")
    assert profile is not None
    assert profile.get("telegram_chat_id") == "888"
    assert profile.get("notification_enabled") == 1


def test_notification_recipients_skip_blacklisted(storage, monkeypatch):
    from bm_automation.app.notifications.ops import doc_expiry, monthly_results
    storage.save_driver(external_id="d4", full_name="Nosirov")
    storage.save_driver(external_id="d5", full_name="Murodov")
    storage.save_driver_profile("d4", notification_enabled=True)
    storage.save_driver_profile("d5", notification_enabled=True)
    storage.link_driver_telegram("d4", 400)
    storage.link_driver_telegram("d5", 500)
    storage.save_driver_profile("d4", blacklisted=True)

    monkeypatch.setattr(doc_expiry, "get_storage", lambda: storage)
    monkeypatch.setattr(monthly_results, "get_storage", lambda: storage)

    exp = doc_expiry._recipients()
    rids = sorted(str(r["driver_id"]) for r in exp)
    assert "d4" not in rids and "d5" in rids

    mon = monthly_results._recipients()
    mrids = sorted(str(r["driver_id"]) for r in mon)
    assert "d4" not in mrids and "d5" in mrids


def test_driver_schedule_message_shows_shift_summary():
    from bm_automation.app.notifications.driver_schedule import (_fmt_busy,
                                                                 _message,
                                                                 _hhmm)
    assert _hhmm("06:00") == "06:00"
    assert _hhmm("2026-08-10T14:30:45") == "14:30"
    assert _hhmm("") == ""
    assert _fmt_busy("22:00", "06:00") == "22:00 (kechasi 06:00 gacha)"
    assert _fmt_busy("08:00", "18:30") == "08:00 — 18:30"
    msg = _message("Aliyev Aliy", "2026-08-10", [
        {"vehicle": "01A123AA", "graph_name": "G-1",
         "start_time": "06:00", "end_time": "09:30",
         "trip_count": 4, "shift_name": "ERTALABKI"},
        {"vehicle": "01B456AA", "graph_name": "G-2",
         "start_time": "10:00", "end_time": "14:30",
         "trip_count": 8, "shift_name": "KUNDUZGI"},
    ])
    assert "Ishni boshlash: <b>06:00</b>" in msg
    assert "Ishni tugatish: <b>14:30</b>" in msg
    assert "Qatnovlar soni: <b>12</b>" in msg
    assert "<b>4</b>" in msg and "<b>8</b>" in msg
    assert "ERTALABKI" in msg and "KUNDUZGI" in msg
    assert "01A123AA" in msg and "01B456AA" in msg


def test_storage_disabled_when_db_unavailable():
    # psycopg o'rnatilmagan bo'lsa ham, agar o'rnatilgan bo'lsa noto'g'ri DSN
    db = Database(driver="postgres",
                  dsn="postgresql://user:pass@localhost:1/nonexistent")
    storage = storage_for(db)
    assert not storage.enabled
    # no-op metodlar xato KO'TARMAYDI
    assert storage.save_profile(name="X", route_id="r") == "updated"
    assert storage.record_error(source="t", message="m") is False
    assert storage.counts()["profiles"] == 0
    assert storage.trips(limit=5) == []
    assert storage.find("profiles", name="X") is None
    assert storage.list_errors() == []


def test_get_storage_singleton_and_degrade(monkeypatch):
    monkeypatch.setenv(
        "SUPABASE_DB_DSN",
        "postgresql://user:pass@localhost:1/nonexistent")
    monkeypatch.delenv("BM_DB_DSN", raising=False)
    reset_storage()
    try:
        s1 = get_storage()
        s2 = get_storage()
        assert s1 is s2
        assert not s1.enabled  # DSN noto'g'ri — DB'siz no-op rejim
    finally:
        reset_storage()


def test_supabase_dsn_is_preferred_and_uses_ssl(monkeypatch):
    from bm_automation.app.config.settings import db_settings

    monkeypatch.setenv("BM_DB_DSN", "postgresql://legacy:secret@localhost/db")
    monkeypatch.setenv(
        "SUPABASE_DB_DSN",
        "postgresql://postgres.ref:secret@aws-0-test.pooler.supabase.com:5432/postgres",
    )
    monkeypatch.delenv("SUPABASE_DB_SSLMODE", raising=False)
    settings = db_settings()
    assert settings["driver"] == "postgres"
    assert settings["dsn"].startswith("postgresql://postgres.ref:")
    assert settings["dsn"].endswith("sslmode=require")


# ------------------------------------------------------------- sync testlari

class _FakeClient:
    """Minimal BMClient o'rnini bosuvchi — sync testlar uchun."""

    def __init__(self, get=None, post=None):
        self._get = get if get is not None else []
        self._post = post if post is not None else []

    def get(self, path, params=None, **kwargs):
        return self._get

    def post(self, path, json=None, **kwargs):
        return self._post


def test_sync_profiles_idempotent(storage):
    from bm_automation.app.db.sync import sync_profiles
    profiles = [
        {"name": "FERGANATEX", "routeVariantId": "r1",
         "profileId": "", "start1": "Prez Oldi", "start2": "Oybek"},
        {"name": "ASL", "routeVariantId": "r2"},
    ]
    r1 = sync_profiles(storage, profiles=profiles)
    assert r1.inserted == 2 and r1.updated == 0 and not r1.error
    r2 = sync_profiles(storage, profiles=profiles)
    assert r2.inserted == 0 and r2.updated == 2 and not r2.error
    rows = storage.query("SELECT * FROM profiles")
    assert len(rows) == 2


def test_sync_duties_saves_schedules_only(storage):
    from bm_automation.app.db.sync import sync_duties
    duty = {
        "id": "duty1", "shiftId": "s1", "date": "2026-08-10",
        "graphs": [
            {"graphName": "P1", "shiftName": "DU", "driverName": "Aliyev",
             "driverId": "drv1", "vehicleId": "v1", "plateNum": "01A123AA",
             "startTime": "06:00", "endTime": "14:00", "tripCount": 5,
             "secondDriverName": "Karimov", "secondDriverId": "drv2",
             "secondStartTime": "14:00"},
        ],
    }
    client = _FakeClient(get=duty)
    r1 = sync_duties(storage, client, "r1", "2026-08-10")
    assert not r1.error
    assert storage.query("SELECT COUNT(*) AS n FROM duties")[0]["n"] == 1
    assert storage.query("SELECT COUNT(*) AS n FROM schedules")[0]["n"] == 1
    # trips duty'dan yaratilmaydi — ular waybills orqali keladi
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 0
    r2 = sync_duties(storage, client, "r1", "2026-08-10")
    assert not r2.error
    assert storage.query("SELECT COUNT(*) AS n FROM schedules")[0]["n"] == 1
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 0


def test_sync_duties_replaces_stale_schedules(storage):
    """Duty'da haydovchi almashtirilsa eski grafik qatorlari o'chiriladi.

    Aks holda `schedules` jadvalida kunning eski (grafik, haydovchi) juftliklari
    qolib, Reja (SUM(trip_count)) sun'iy ko'payadi (masalan 136 o'rniga 176).
    """
    from bm_automation.app.db.sync import sync_duties

    duty1 = {
        "id": "duty1", "shiftId": "s1", "date": "2026-08-11",
        "graphs": [
            {"graphName": "P1", "driverId": "drv1", "vehicleId": "v1",
             "tripCount": 5},
            {"graphName": "P2", "driverId": "drv2", "vehicleId": "v2",
             "tripCount": 6},
        ],
    }
    duty2 = {
        "id": "duty1", "shiftId": "s1", "date": "2026-08-11",
        "graphs": [
            {"graphName": "P1", "driverId": "drv3", "vehicleId": "v1",
             "tripCount": 5},
            {"graphName": "P2", "driverId": "drv2", "vehicleId": "v2",
             "tripCount": 6},
        ],
    }
    sync_duties(storage, _FakeClient(get=duty1), "r1", "2026-08-11")
    sync_duties(storage, _FakeClient(get=duty2), "r1", "2026-08-11")
    rows = storage.query(
        "SELECT graph_name, driver_id FROM schedules "
        "WHERE date='2026-08-11' ORDER BY graph_name")
    assert [tuple(r.values()) for r in rows] == [("P1", "drv3"), ("P2", "drv2")]


def test_delete_missing_postgres_column_major_params():
    """PostgreSQL unnest DELETE uchun parametrlar ustun bo'yicha tuziladi.

    Eski (satr bo'yicha) tartibda placeholders soni (kalitlar soni) bilan
    parametrlar soni (kalitlar × o'chiriladigan) mos kelmas edi — 4 placeholder,
    12 parametr xatosi. unnest() satrlarni ustunlarni birlashtirib qaytaradi,
    shuning uchun har bir ustunning barcha qiymatlari bitta array'da bo'lishi
    kerak.
    """
    from contextlib import nullcontext

    captured = {}

    class FakePG:
        driver = "postgres"
        ph = "%s"
        available = True
        transaction = nullcontext

        def query(self, sql, params=None, limit=None):
            return [
                {"date": "2026-08-11", "route_id": "r1",
                 "graph_name": "P1", "driver_id": "old1"},
                {"date": "2026-08-11", "route_id": "r1",
                 "graph_name": "P1", "driver_id": "old2"},
                {"date": "2026-08-11", "route_id": "r1",
                 "graph_name": "P2", "driver_id": "old3"},
            ]

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = tuple(params or ())
            return 2  # 2 ta eski qator o'chiriladi

    st = Storage(FakePG(), enabled=True)
    st._schema_ready = True
    keep = {("2026-08-11", "r1", "P2", "old3")}
    n = st.delete_missing(
        "schedules", ["date", "route_id", "graph_name", "driver_id"],
        "r1", "2026-08-11", "2026-08-11", keep)
    assert n == 2  # faqat P1 eski qatorlari o'chiriladi; P2/old3 saqlanadi
    assert captured["sql"].count("%s") == 4  # 4 kalit = 4 placeholder (array'lar)
    assert captured["params"] == (
        ["2026-08-11", "2026-08-11"],  # date ustuni array'i
        ["r1", "r1"],                  # route_id ustuni array'i
        ["P1", "P1"],                  # graph_name ustuni array'i
        ["old1", "old2"],              # driver_id ustuni array'i
    )


def test_sync_handles_404_as_no_data(storage):
    """BM API hali yaratilmagan hisobot uchun 404 qaytarsa — xato emas.

    Bugungi sana uchun `gross/trip` TRIP_NOT_FOUND, `duty` DUTY_NOT_FOUND
    qaytaradi — bu dastur xatosi emas, ma'lumot hali yo'q.
    """
    from bm_automation.app.api.client import BMApiError
    from bm_automation.app.db.sync import _not_found, sync_duties, sync_gross_trips

    assert _not_found(BMApiError(404, code="TRIP_NOT_FOUND"))
    assert _not_found(BMApiError(404, code="DUTY_NOT_FOUND"))
    assert _not_found(BMApiError(404))
    assert not _not_found(BMApiError(500))
    assert not _not_found(BMApiError(403))

    class _NotFoundClient(_FakeClient):
        def get(self, path, params=None, **kwargs):
            raise BMApiError(404, code="TRIP_NOT_FOUND")

    r1 = sync_gross_trips(storage, _NotFoundClient(), "r1", "2026-08-12")
    assert not r1.error
    assert r1.total == 0
    r2 = sync_duties(storage, _NotFoundClient(), "r1", "2026-08-12")
    assert not r2.error
    assert r2.total == 0


def test_sync_waybills_statuses(storage):
    from bm_automation.app.db.sync import sync_waybills
    rows = [
        {"date": "2026-08-10", "plateNumber": "01A123AA",
         "driverId": "drv1", "vehicleId": "v1", "status": "ACCEPTED",
         "startTm": "2026-08-10T06:01:34", "endTm": "2026-08-10T06:39:55",
         "direction": "UP"},
        {"date": "2026-08-10", "plateNumber": "01B456BB",
         "driverId": "drv2", "vehicleId": "v2", "status": "REJECTED"},
        {"date": "2026-08-10", "plateNumber": "01C789CC",
         "driverId": "drv3", "vehicleId": "v3", "status": "ZERO_MILEAGE"},
    ]
    client = _FakeClient(post=rows)
    r = sync_waybills(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert not r.error
    # ZERO_MILEAGE ham trips'ga yoziladi (jami = reja + nollik)
    assert storage.query("SELECT COUNT(*) AS n FROM waybills")[0]["n"] == 3
    trips = storage.query("SELECT * FROM trips WHERE status='ACCEPTED'")
    assert len(trips) == 1
    assert trips[0]["source"] == SyncSource.WAYBILL.value
    assert trips[0]["actual_time"] == "2026-08-10T06:39:55"
    assert trips[0]["planned_time"] == "2026-08-10T06:01:34"
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 3
    assert storage.query("SELECT COUNT(*) AS n FROM trips"
                         " WHERE status='ZERO_MILEAGE'")[0]["n"] == 1
    # idempotent
    sync_waybills(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert storage.query("SELECT COUNT(*) AS n FROM waybills")[0]["n"] == 3
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 3


def test_sync_waybills_replace_deletes_stale(storage):
    """Replace-sync: hisobotda yo'q WAYBILL qatorlari o'chiriladi (gross/duty tegilmaydi)."""
    from bm_automation.app.db.sync import sync_waybills, sync_gross_trips
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    # Hisobotda endi yo'q eski WAYBILL trip + saqlanib qolishi kerak bo'lgan gross trip
    storage.save_trip(TripRecord(
        date="2026-08-10", route_id="r1", vehicle_id="v1", driver_id="d1",
        planned_time="06:00", status="ACCEPTED", source=SyncSource.WAYBILL.value))
    storage.save_trip(TripRecord(
        date="2026-08-10", route_id="r1", vehicle_id="v1", driver_id="d2",
        planned_time="07:00", status="ACCEPTED", source=SyncSource.GROSS_TRIP.value))
    rows = [
        {"date": "2026-08-10", "plateNumber": "01A123AA", "driverId": "d9",
         "vehicleId": "v1", "status": "ACCEPTED",
         "startTm": "2026-08-10T08:00:00", "direction": "UP"},
    ]
    client = _FakeClient(post=rows)
    r = sync_waybills(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert not r.error
    trips = storage.query("SELECT * FROM trips WHERE date='2026-08-10'")
    assert len(trips) == 2                    # eski WAYBILL o'chdi, GROSS_TRIP qoldi
    sources = {t["source"] for t in trips}
    assert sources == {SyncSource.WAYBILL.value, SyncSource.GROSS_TRIP.value}


def test_sync_waybills_maps_plate_to_vehicle(storage):
    from bm_automation.app.db.sync import sync_waybills
    storage.save_vehicle(external_id="veh1", plate_number="01A123AA",
                         route_id="r1")
    rows = [
        {"date": "2026-08-10", "plateNum": "01A123AA", "driverId": "drv1",
         "tripStatus": "ACCEPTED", "startTm": "2026-08-10T06:01:34"},
        {"date": "2026-08-10", "plateNum": "01A123AA", "driverId": "drv1",
         "tripStatus": "ACCEPTED", "startTm": "2026-08-10T06:51:25"},
    ]
    client = _FakeClient(post=rows)
    sync_waybills(storage, client, "r1", "2026-08-10", "2026-08-10")
    trips = storage.query("SELECT * FROM trips ORDER BY planned_time")
    assert len(trips) == 2            # bir xil haydovchining 2 reysi saqlanadi
    assert {t["vehicle_id"] for t in trips} == {"veh1"}


def test_sync_all_profiles(storage, monkeypatch):
    import bm_automation.app.core.profiles as pmod
    from bm_automation.app.db import sync as db_sync_mod

    class _Client(_FakeClient):
        def __init__(self):
            super().__init__(get=[], post=[])
            self.access_token = "main"
            self.refresh_token = "main-r"
            self.profile_log = []
            import types
            self.session = types.SimpleNamespace(
                headers={"Authorization": "Bearer main"})

        def login_by_profile(self, pid):
            self.profile_log.append(pid)
            self.session.headers["Authorization"] = f"Bearer {pid}"

        def _extract_items(self, data):
            return [], {}

    monkeypatch.setattr(
        pmod, "all_profiles",
        lambda: [
            {"name": "A", "routeVariantId": "rA", "profileId": ""},
            {"name": "B", "routeVariantId": "rB", "profileId": "pidB"},
        ])
    client = _Client()
    results = db_sync_mod.sync_all_profiles(storage, client, "2026-08-10")
    # faqat profileId'lilar o'z tokenini oladi; bo'sh bo'lgan asosiy token bilan
    assert client.profile_log == ["pidB"]
    assert results
    # profil darajasidagi xatolar bo'lmasligi kerak
    assert not [r for r in results
                if r.entity.startswith("profiles/") and r.error]


def test_sync_routes_walks_tree(storage):
    from bm_automation.app.db.sync import sync_routes
    tree = [
        {"id": "reg1", "name": "Farg'ona", "entityType": "REGION",
         "children": [
             {"id": "park1", "name": "Park", "entityType": "PARK",
              "children": [
                  {"id": "route1", "name": "10-yo'nalish", "entityType": "ROUTE"},
              ]},
         ]},
    ]
    client = _FakeClient(get=tree)
    r = sync_routes(storage, client)
    assert not r.error
    assert r.total == 3
    assert storage.query("SELECT COUNT(*) AS n FROM routes")[0]["n"] == 3
    route = storage.find("routes", external_id="route1")
    assert route and route["parent_id"] == "park1"


def test_automation_logger(storage):
    from bm_automation.app.db.sync import AutomationLogger
    log = AutomationLogger(storage)
    run_id = log.start(trigger="auto", sheet_date="2026-08-11")
    assert log.finish(run_id, [{"profile": "A", "sent": True}]) == "OK"
    log.error("daily", ValueError("token"), context={"p": "A"})
    assert storage.query("SELECT COUNT(*) AS n FROM automation_runs")[0]["n"] == 1
    run = storage.find("automation_runs", run_id=run_id)
    assert run["status"] == "OK"
    assert storage.query("SELECT COUNT(*) AS n FROM errors")[0]["n"] == 1


def test_automation_logger_failure_status(storage):
    from bm_automation.app.db.sync import AutomationLogger
    log = AutomationLogger(storage)
    run_id = log.start(trigger="manual")
    status = log.finish(run_id, [{"profile": "A", "error": "HTTP 401"}])
    assert status == "ERROR"


def test_trips_filter(storage):
    storage.save_trip(TripRecord(date="2026-08-10", route_id="r1",
                                 status=TripStatus.ACCEPTED.value,
                                 planned_time="06:00"))
    storage.save_trip(TripRecord(date="2026-08-11", route_id="r1",
                                 status=TripStatus.REJECTED.value,
                                 planned_time="07:00"))
    assert len(storage.trips(date="2026-08-10")) == 1
    assert len(storage.trips(status="ACCEPTED")) == 1
    assert len(storage.trips()) == 2


def _waybill_trip(date_str, route_id, vehicle_id, driver_id, planned_time,
                  gps_odo, status="ACCEPTED", source=SyncSource.WAYBILL.value):
    return TripRecord(
        date=date_str, route_id=route_id, vehicle_id=vehicle_id,
        driver_id=driver_id, planned_time=planned_time, status=status,
        source=source, data={"gpsOdo": gps_odo, "routeOdo": gps_odo})


def test_sync_work_logs_aggregates_daily_km(storage):
    from bm_automation.app.db.sync import sync_work_logs
    # d1: 2 reys (12.5 + 7.0 km) + ZERO_MILEAGE (0 km); d2: 1 reys (5 km)
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 12.5))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "07:00", 7.0))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "08:00", 0.0,
                                    status="ZERO_MILEAGE"))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v2", "d2", "06:30", 5.0))
    # DUTY reysi (km'siz) hisobga kirmaydi
    storage.save_trip(TripRecord(date="2026-08-10", route_id="r1", vehicle_id="v1",
                                 driver_id="d1", planned_time="09:00",
                                 source=SyncSource.DUTY.value))
    r = sync_work_logs(storage, "2026-08-10", "2026-08-10")
    assert not r.error
    logs = {row["driver_id"]: row for row in storage.query(
        "SELECT * FROM driver_work_logs ORDER BY driver_id")}
    assert set(logs) == {"d1", "d2"}
    assert logs["d1"]["distance_km"] == 19.5      # ZERO_MILEAGE 0 km qo'shmaydi
    assert logs["d1"]["trip_count"] == 3
    assert logs["d1"]["note"] == "AVTO"
    assert logs["d2"]["distance_km"] == 5.0
    assert logs["d2"]["trip_count"] == 1
    # idempotent: qayta hisoblash dublikat yaratmaydi
    sync_work_logs(storage, "2026-08-10", "2026-08-10")
    assert storage.query("SELECT COUNT(*) AS n FROM driver_work_logs")[0]["n"] == 2


def test_sync_work_logs_preserves_manual_and_removes_stale(storage):
    from bm_automation.app.db.sync import sync_work_logs
    # (d1,v1) uchun qo'lda qayd mavjud — AVTO ustiga yozilmaydi
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 99.0, 2,
                                 note="Qo'lda")
    # (d9,v9) uchun eski AVTO qayd — trips'da yo'q, o'chirilishi kerak
    storage.save_driver_work_log("2026-08-10", "d9", "v9", 42.0, 1, note="AVTO")
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 12.5))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v2", "d2", "06:30", 5.0))
    sync_work_logs(storage, "2026-08-10", "2026-08-10")
    rows = {row["vehicle_id"]: row for row in storage.query(
        "SELECT * FROM driver_work_logs")}
    # qo'lda qayd buzilmaydi, AVTO ham qo'shilmaydi
    assert rows["v1"]["distance_km"] == 99.0 and rows["v1"]["note"] == "Qo'lda"
    # eski AVTO (v9) o'chirildi, yangi AVTO (v2) qo'shildi
    assert "v9" not in rows
    assert rows["v2"]["distance_km"] == 5.0 and rows["v2"]["note"] == "AVTO"


def test_sync_work_logs_route_scope(storage):
    from bm_automation.app.db.sync import sync_work_logs
    storage.save_driver(external_id="d1", full_name="Aliyev", route_id="r1")
    storage.save_driver(external_id="d9", full_name="Karimov", route_id="r2")
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 10.0))
    storage.save_trip(_waybill_trip("2026-08-10", "r2", "v9", "d9", "06:00", 20.0))
    sync_work_logs(storage, "2026-08-10", "2026-08-10", route_id="r1")
    rows = storage.query("SELECT * FROM driver_work_logs")
    assert len(rows) == 1 and rows[0]["driver_id"] == "d1"
    assert rows[0]["distance_km"] == 10.0


def test_sync_work_logs_registers_unknown_driver(storage):
    """Waybill'da bor-u drivers'da yo'q haydovchi ro'yxatga olinib, hisoblanadi."""
    from bm_automation.app.db.sync import sync_route_daily, sync_work_logs
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    sync_route_daily(storage, _FakeClient(get=_gross_route_data()),
                     "r1", "2026-08-10", "2026-08-10")
    storage.save_trip(TripRecord(
        date="2026-08-10", route_id="r1", vehicle_id="v1", driver_id="dX",
        planned_time="06:00", status="ACCEPTED", source=SyncSource.WAYBILL.value,
        data={"gpsOdo": 12.5, "routeOdo": 12.5, "driverName": "O'RINBOSAR ISMLARI"}))
    # dX drivers jadvalida yo'q — scope route_id bo'lishi kerak
    r = sync_work_logs(storage, "2026-08-10", "2026-08-10", route_id="r1")
    assert not r.error
    logs = storage.query("SELECT * FROM driver_work_logs")
    assert len(logs) == 1 and logs[0]["driver_id"] == "dX"
    assert logs[0]["distance_km"] == pytest.approx(232.4)  # gross km
    assert logs[0]["trip_count"] == 16
    # haydovchi avtomatik ro'yxatga olindi
    drv = storage.find("drivers", external_id="dX")
    assert drv and drv["full_name"] == "O'RINBOSAR ISMLARI"
    assert drv["route_id"] == "r1"


def _gross_route_data():
    """Gross/route javob dublikati (2 avtobus, 1 kun)."""
    return {
        "routeVariantId": "r1", "routeName": "B-80",
        "dates": {
            "2026-08-10": {"vehicles": [
                {"date": "2026-08-10", "vehicleNumber": "01A123AA",
                 "vehicleBrand": "Mercedes", "shiftName": "DU",
                 "workingDay": 1, "tripPlan": 20, "tripFact": 16,
                 "tripFactPassed": 16, "tripFactApproved": 15,
                 "distancePlan": 250.0, "distanceFact": 232.4,
                 "distanceFactExtra": 10.0},
                {"date": "2026-08-10", "vehicleNumber": "01B456BB",
                 "vehicleBrand": "Hyundai", "shiftName": "DU",
                 "workingDay": 1, "tripPlan": 18, "tripFact": 14,
                 "distancePlan": 240.0, "distanceFact": 220.0},
            ]},
        },
    }


def test_sync_route_daily_idempotent(storage):
    """Gross route hisoboti route_daily'ga yoziladi (plate → vehicle_id)."""
    from bm_automation.app.db.sync import sync_route_daily
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    client = _FakeClient(get=_gross_route_data())
    r1 = sync_route_daily(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert not r1.error
    assert r1.inserted == 2
    rows = storage.query("SELECT * FROM route_daily ORDER BY vehicle_number")
    assert len(rows) == 2
    row = {x["vehicle_number"]: x for x in rows}["01A123AA"]
    assert row["vehicle_id"] == "v1"            # plate → vehicles jadvali orqali
    assert row["distance_fact"] == 232.4
    assert row["trip_fact"] == 16
    assert row["working_day"] == 1
    assert row["route_id"] == "r1"
    # idempotent — dublikat yaratilmaydi
    r2 = sync_route_daily(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert r2.inserted == 0 and r2.updated == 2
    assert storage.query("SELECT COUNT(*) AS n FROM route_daily")[0]["n"] == 2


def test_sync_work_logs_uses_gross_km(storage):
    """route_daily distanceFact odo km'dan ustun — rasmiy hisob-kitob."""
    from bm_automation.app.db.sync import sync_route_daily, sync_work_logs
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    sync_route_daily(storage, _FakeClient(get=_gross_route_data()),
                     "r1", "2026-08-10", "2026-08-10")
    # d1: 2 reys, odo 12.5+7.0=19.5 — lekin gross km ustuvor (232.4)
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 12.5))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "07:00", 7.0))
    r = sync_work_logs(storage, "2026-08-10", "2026-08-10")
    assert not r.error
    log = storage.query("SELECT * FROM driver_work_logs")[0]
    assert log["driver_id"] == "d1"
    assert log["distance_km"] == pytest.approx(232.4)
    assert log["trip_count"] == 16
    assert log["note"] == "AVTO"
    # saytdagi brutto-route qo'shimcha ustunlari AVTO qaydga yoziladi
    assert log["distance_plan"] == pytest.approx(250.0)
    assert log["trip_plan"] == 20
    assert log["working_day"] == 1
    assert log["trip_passed"] == 16
    assert log["trip_approved"] == 15


def test_sync_work_logs_gross_fallback_to_odo(storage):
    """route_daily yo'q bus-day — trips odo km fallback."""
    from bm_automation.app.db.sync import sync_work_logs
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 12.5))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "07:00", 7.0))
    sync_work_logs(storage, "2026-08-10", "2026-08-10")
    log = storage.query("SELECT * FROM driver_work_logs")[0]
    assert log["distance_km"] == pytest.approx(19.5)
    assert log["trip_count"] == 2


def test_sync_work_logs_two_drivers_one_bus(storage):
    """Gross km bir avtobusda eng ko'p reysli haydovchiga, qolganiga odo."""
    from bm_automation.app.db.sync import sync_route_daily, sync_work_logs
    storage.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    sync_route_daily(storage, _FakeClient(get=_gross_route_data()),
                     "r1", "2026-08-10", "2026-08-10")
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "06:00", 12.5))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d1", "07:00", 7.0))
    storage.save_trip(_waybill_trip("2026-08-10", "r1", "v1", "d2", "14:00", 6.0))
    sync_work_logs(storage, "2026-08-10", "2026-08-10")
    logs = {r["driver_id"]: r for r in storage.query("SELECT * FROM driver_work_logs")}
    assert logs["d1"]["distance_km"] == pytest.approx(232.4)
    assert logs["d1"]["trip_count"] == 16
    assert logs["d2"]["distance_km"] == pytest.approx(6.0)   # odo fallback
    assert logs["d2"]["trip_count"] == 1


def test_sync_driver_profiles_fills_detail(storage, monkeypatch):
    from bm_automation.app.db.sync import sync_driver_profiles
    storage.save_driver(
        external_id="drv1", full_name="ALIYEV", route_id="r1",
        data={"id": "drv1", "fullName": "ALIYEV",
              "licenseNumber": "AF1234567", "category": "B, C",
              "licenseExpiryDate": "2030-01-01",
              "phoneNumber": "+9******12", "pinfl": "32******13"})

    class _DetailClient(_FakeClient):
        def get(self, path, params=None, **kwargs):
            assert "/drivers/drv1" in path
            return {"id": "drv1", "fullName": "ALIYEV",
                    "licenseNumber": "AF1234567", "category": "B, C",
                    "licenseExpiryDate": "2030-01-01",
                    "phoneNumber": "+998901234567",
                    "pinfl": "32512757040013"}

    monkeypatch.setenv("KM_RATE", "1000")
    r = sync_driver_profiles(storage, _DetailClient(), "r1", "2026-08-10",
                             force=True)
    assert not r.error and r.inserted == 1
    profile = storage.find("driver_profiles", driver_id="drv1")
    assert profile["phone"] == "+998901234567"
    assert profile["license_number"] == "AF1234567"
    assert profile["license_expiry"] == "2030-01-01"
    assert profile["km_rate"] == 1000.0
    assert storage.find("drivers", external_id="drv1")["tin"] == "32512757040013"


def test_sync_driver_profiles_skips_detail_when_complete(storage):
    from bm_automation.app.db.sync import sync_driver_profiles
    storage.save_driver(
        external_id="drv1", full_name="ALIYEV", route_id="r1",
        data={"id": "drv1", "fullName": "ALIYEV",
              "licenseNumber": "AF1234567", "category": "B, C",
              "licenseExpiryDate": "2030-01-01",
              "phoneNumber": "+9******12", "pinfl": "32******13"})
    storage.save_driver_profile(
        "drv1", phone="+998901234567", license_number="AF1234567")

    class _FailingClient(_FakeClient):
        def get(self, path, params=None, **kwargs):
            raise AssertionError("detail chaqirilmasligi kerak")

    r = sync_driver_profiles(storage, _FailingClient(), "r1", "2026-08-10")
    assert not r.error
    # to'liq telefon maskalangan ro'yxat qiymati bilan ustiga yozilmaydi
    profile = storage.find("driver_profiles", driver_id="drv1")
    assert profile["phone"] == "+998901234567"
    assert profile["license_number"] == "AF1234567"
    # tin ham maskalangan qiymat bilan buzilmaydi
    assert storage.find("drivers", external_id="drv1")["tin"] == ""


def test_db_self_heals_after_transient_failure(monkeypatch):
    """Vaqtinchalik uzilishda DB o'zini tiklaydi (backoff bilan)."""
    from bm_automation.app.db.base import Database

    db = Database(driver="postgres",
                  dsn="postgresql://user:pass@localhost:1/nonexistent")
    assert not db.available
    # backoff — darhol qayta urinish bo'lmaydi (30 soniya)
    assert not db.probe()
    # tiklanishdan keyin qayta urinish muvaffaqiyatli
    db._failed_at = 0.0
    monkeypatch.setattr(db, "_ensure_postgres", lambda: None)
    assert db.probe()
    assert db.available


def test_storage_recovers_enabled(tmp_path):
    """Storage.enabled DB tiklangach True bo'ladi va yozish davom etadi."""
    db = SQLiteDatabase(str(tmp_path / "rec.db"))
    storage = storage_for(db)
    assert storage.enabled

    db.available = False  # "supabase uzildi"
    db._failed_at = 0.0   # backoff muddati o'tgan — qayta urinish mumkin
    assert storage.enabled  # probe tikladi — yozish davom etadi
    db._failed_at = 0.0
    assert storage.enabled
    assert storage.save_route(external_id="r1", name="X",
                              entity_type="ROUTE") == "inserted"
