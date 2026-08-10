"""Dashboard metrikalari va eksport testlari (SQLite / tmp)."""

import pytest

from bm_automation.app.db.base import Database
from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus
from bm_automation.app.db.storage import storage_for
from bm_automation.app.dashboard import export as dexport
from bm_automation.app.dashboard.metrics import Metrics


def _seed(storage):
    storage.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    storage.save_route(external_id="r2", name="20-yo'nalish", entity_type="ROUTE")
    storage.save_vehicle(external_id="v1", plate_number="01A123AA",
                         garage_number="G1", model="Mercedes", route_id="r1")
    storage.save_vehicle(external_id="v2", plate_number="01B456BB",
                         garage_number="G2", model="Hyundai", route_id="r1")
    storage.save_driver(external_id="d1", full_name="Aliyev Aliy")
    storage.save_driver(external_id="d2", full_name="Karimov Karim")

    def trip(d, rid, vid, drv, pt, status, actual=""):
        return TripRecord(date=d, route_id=rid, vehicle_id=vid, driver_id=drv,
                          planned_time=pt, actual_time=actual, status=status,
                          source=SyncSource.DUTY.value)

    storage.save_trip(trip("2026-08-10", "r1", "v1", "d1", "06:00",
                           TripStatus.ACCEPTED.value, actual="06:15"))
    storage.save_trip(trip("2026-08-10", "r1", "v1", "d1", "07:00",
                           TripStatus.ACCEPTED.value))
    storage.save_trip(trip("2026-08-10", "r1", "v2", "d2", "06:30",
                           TripStatus.REJECTED.value))
    storage.save_trip(trip("2026-08-11", "r1", "v1", "d1", "06:00",
                           TripStatus.PENDING_ACCESS.value))


@pytest.fixture()
def met(tmp_path):
    db = Database(driver="sqlite", path=str(tmp_path / "dash.db"))
    storage = storage_for(db)
    _seed(storage)
    return Metrics(storage)


def test_today_metrics(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    t = met.today(f)
    assert t["total_buses"] == 2
    assert t["active_buses"] == 2
    assert t["not_active_buses"] == 0
    assert t["total_trips"] == 3
    assert t["accepted"] == 2
    assert t["completed"] == 2
    assert t["rejected"] == 1
    assert t["pending"] == 0
    assert t["zero_mileage"] == 0


def test_today_not_active_buses(met):
    f = {"date": "2026-08-11", "from": "2026-08-11", "to": "2026-08-11"}
    t = met.today(f)
    assert t["total_trips"] == 1
    assert t["active_buses"] == 1
    assert t["not_active_buses"] == 1  # v2 11-kuni chiqmagan


def test_routes_metrics(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    rows = met.routes(f)
    assert len(rows) == 1
    r = rows[0]
    assert r["route_id"] == "r1"
    assert r["planned"] == 3
    assert r["actual"] == 1
    assert r["accepted"] == 2
    assert r["not_accepted"] == 0
    assert r["rejected"] == 1
    assert r["performance"] == pytest.approx(33.3, abs=0.2)


def test_routes_planned_from_schedules(met):
    """Rejadagi qatnovlar — duty jadvalidagi trip_count yig'indisi."""
    storage = met.storage
    storage.save_schedule(date="2026-08-10", route_id="r1", graph_name="P1",
                          driver_id="d1", vehicle_id="v1",
                          start_time="06:00", trip_count=10)
    storage.save_schedule(date="2026-08-10", route_id="r1", graph_name="P2",
                          driver_id="d2", vehicle_id="v2",
                          start_time="07:00", trip_count=5)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    rows = met.routes(f)
    r = [x for x in rows if x["route_id"] == "r1"][0]
    assert r["planned"] == 15          # trip_count yig'indisi, COUNT(3) emas
    assert r["accepted"] == 2
    assert r["performance"] == pytest.approx(6.7, abs=0.2)


def test_vehicles_metrics(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    rows = met.vehicles(f)
    by_id = {v["vehicle_id"]: v for v in rows}
    assert by_id["v1"]["trips"] == 2
    assert by_id["v1"]["issues"] == 0
    assert by_id["v1"]["status"] == "faol"
    assert by_id["v2"]["trips"] == 1
    assert by_id["v2"]["issues"] == 1
    assert by_id["v2"]["status"] == "faol"


def test_drivers_metrics(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    rows = met.drivers(f)
    by_id = {d["driver_id"]: d for d in rows}
    assert by_id["d1"]["trips"] == 2
    assert by_id["d1"]["working_days"] == 1
    assert by_id["d1"]["attendance"] == 100.0
    assert by_id["d2"]["issues"] == 1


def test_drivers_attendance_range(met):
    f = {"from": "2026-08-10", "to": "2026-08-11"}
    rows = met.drivers(f)
    by_id = {d["driver_id"]: d for d in rows}
    assert by_id["d1"]["trips"] == 3
    assert by_id["d1"]["working_days"] == 2
    assert by_id["d1"]["attendance"] == 100.0
    assert by_id["d2"]["working_days"] == 1
    assert by_id["d2"]["attendance"] == 50.0


def test_status_filter(met):
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10",
         "status": "REJECTED"}
    t = met.today(f)
    assert t["total_trips"] == 1
    assert t["rejected"] == 1
    assert len(met.trips(f)) == 1


def test_disabled_db_returns_empty(tmp_path):
    db = Database(driver="postgres",
                  dsn="postgresql://user:pass@localhost:1/nonexistent")
    storage = storage_for(db)
    met = Metrics(storage)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    t = met.today(f)
    assert t["total_trips"] == 0 and t["total_buses"] == 0
    assert met.routes(f) == []
    assert met.vehicles(f) == []
    assert met.drivers(f) == []


def test_system_last_sync_and_error(met, monkeypatch):
    met.storage.record_error(source="daily", message="HTTP 401", context={"p": "A"})
    monkeypatch.setattr(met, "_bm_status",
                        lambda: {"ok": True, "status": 401, "ms": 100})
    monkeypatch.setattr(met, "_telegram_status",
                        lambda: {"ok": False, "configured": False})
    s = met.system()
    assert s["database"]["ok"] is True
    assert s["api"]["ok"] is True
    assert s["last_sync"] != ""
    assert s["last_error"] is not None
    assert s["last_error"]["message"] == "HTTP 401"


# ---------------------------------------------------------------- eksport

@pytest.fixture()
def seeded_storage(tmp_path):
    db = Database(driver="sqlite", path=str(tmp_path / "exp.db"))
    storage = storage_for(db)
    _seed(storage)
    return storage


@pytest.fixture()
def patch_storage(monkeypatch, seeded_storage):
    import bm_automation.app.dashboard.metrics as mmod
    monkeypatch.setattr(mmod, "get_storage", lambda: seeded_storage)


def test_export_csv(patch_storage):
    data = dexport.export_csv({"date": "2026-08-10"}, "trips")
    assert data[:3] == b"\xef\xbb\xbf"          # UTF-8 BOM
    assert b"route_id" in data and b"2026-08-10" in data


def test_export_xlsx(patch_storage):
    data = dexport.export_xlsx({"date": "2026-08-10"}, "all")
    assert data[:2] == b"PK"                     # ZIP/xlsx header


def test_export_pdf(patch_storage):
    data = dexport.export_pdf({"date": "2026-08-10"}, "all")
    assert data[:4] == b"%PDF"


def test_export_build_and_filename(patch_storage):
    data = dexport.build_export({"date": "2026-08-10"}, "csv", "routes")
    assert b"performance" in data
    assert dexport.filename("xlsx", "trips", "2026-08-10").endswith(".xlsx")
    assert dexport.content_type("pdf") == "application/pdf"
