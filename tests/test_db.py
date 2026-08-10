"""Ma'lumotlar bazasi qatlami testlari (SQLite / tmp).

O'z ichiga oladi: sxema yaratish, idempotent upsert (dublikatsiz),
qayta yuklashda yozuvni buzmaslik, tranzaksiya rollback, DB ishlamasa
degrade, BM API → DB sync va AutomationLogger.
"""

import pytest

from bm_automation.app.db.base import Database
from bm_automation.app.db.models import (SyncSource, TripRecord, TripStatus,
                                         now_utc)
from bm_automation.app.db.storage import (Storage, get_storage,
                                          reset_storage, storage_for)


@pytest.fixture()
def storage(tmp_path):
    db = Database(driver="sqlite", path=str(tmp_path / "test.db"))
    return storage_for(db)


def test_schema_creates_all_tables(storage):
    assert storage.enabled
    assert storage.db.available
    tables = {t["name"] for t in storage.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("profiles", "routes", "vehicles", "drivers", "duties",
                  "schedules", "waybills", "trips", "trip_statuses",
                  "reports", "report_runs", "errors", "notifications",
                  "automation_runs"):
        assert table in tables, f"jadval yo'q: {table}"


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


def test_get_storage_singleton_and_degrade(tmp_path, monkeypatch):
    monkeypatch.setenv("BM_DB_PATH", str(tmp_path / "singleton.db"))
    monkeypatch.setenv("BM_DB_DRIVER", "sqlite")
    reset_storage()
    try:
        s1 = get_storage()
        s2 = get_storage()
        assert s1 is s2
    finally:
        reset_storage()


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
    # ZERO_MILEAGE waybill'ga yoziladi, lekin trips'ga EMAS (haqiqiy qatnov emas)
    assert storage.query("SELECT COUNT(*) AS n FROM waybills")[0]["n"] == 3
    trips = storage.query("SELECT * FROM trips WHERE status='ACCEPTED'")
    assert len(trips) == 1
    assert trips[0]["source"] == SyncSource.WAYBILL.value
    assert trips[0]["actual_time"] == "2026-08-10T06:39:55"
    assert trips[0]["planned_time"] == "2026-08-10T06:01:34"
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 2
    # idempotent
    sync_waybills(storage, client, "r1", "2026-08-10", "2026-08-10")
    assert storage.query("SELECT COUNT(*) AS n FROM waybills")[0]["n"] == 3
    assert storage.query("SELECT COUNT(*) AS n FROM trips")[0]["n"] == 2


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
    assert all(not getattr(r, "error", "") for r in results)


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
