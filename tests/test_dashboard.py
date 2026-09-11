"""Dashboard metrikalari va eksport testlari (SQLite / tmp)."""

import io
import json

import pytest

from bm_automation.app.db.base import Database
from bm_automation.app.db.models import SyncSource, TripRecord, TripStatus
from bm_automation.app.db.storage import storage_for
from bm_automation.app.dashboard import export as dexport
from bm_automation.app.dashboard.metrics import Metrics
from bm_automation.app.dashboard.server import DashboardHandler
from tests.sqlite_backend import SQLiteDatabase


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
    db = SQLiteDatabase(str(tmp_path / "dash.db"))
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
    assert t["under_review"] == 2
    assert t["planned"] == 0  # schedules yo'q


def test_today_not_active_buses(met):
    f = {"date": "2026-08-11", "from": "2026-08-11", "to": "2026-08-11"}
    t = met.today(f)
    assert t["total_trips"] == 1
    assert t["active_buses"] == 1
    assert t["not_active_buses"] == 1  # v2 11-kuni chiqmagan


def test_monthly(met):
    """Kunlik statistika — oy davomida har kun uchun."""
    f = {"from": "2026-08-01", "to": "2026-08-11"}
    data = met.monthly(f)
    assert data["from"] == "2026-08-01"
    assert data["to"] == "2026-08-11"
    days = {d["date"]: d for d in data["days"]}
    assert len(data["days"]) == 11
    d10 = days["2026-08-10"]
    assert d10["total"] == 2          # REJECTED sayt "Jami"siga kirmaydi
    assert d10["actual"] == 1
    assert d10["accepted"] == 2
    assert d10["rejected"] == 1
    # gps: ACCEPTED + actual_time yo'q (07:00 reysi); texnik: REJECTED
    assert d10["problems"]["gps"] == 1
    assert d10["problems"]["technical"] == 1
    assert d10["problems"]["schedule"] == 0
    assert d10["problems_total"] == 2
    d11 = days["2026-08-11"]
    assert d11["total"] == 0          # PENDING_ACCESS sayt "Jami"siga kirmaydi
    assert d11["problems"]["schedule"] == 1  # PENDING_ACCESS
    t = data["totals"]
    assert t["total"] == 2
    assert t["accepted"] == 2
    assert t["rejected"] == 1
    assert t["problems"] == {"gps": 1, "technical": 1, "schedule": 1}
    assert t["problems_total"] == 3


def test_monthly_planned_from_schedules_and_waybills(met):
    """Rejalashtirilgan — schedules trip_count; texnik — trips'dan (REJECTED)."""
    storage = met.storage
    storage.save_schedule(date="2026-08-10", route_id="r1", graph_name="P1",
                          driver_id="d1", vehicle_id="v1",
                          start_time="06:00", trip_count=10)
    storage.save_waybill(date="2026-08-10", route_id="r1",
                         plate_number="01A123AA", direction="F",
                         vehicle_id="v1", driver_id="d1",
                         status="ZERO_MILEAGE")
    f = {"from": "2026-08-01", "to": "2026-08-11"}
    days = {d["date"]: d for d in met.monthly(f)["days"]}
    d10 = days["2026-08-10"]
    assert d10["planned"] == 10
    assert d10["problems"]["technical"] == 1  # faqat REJECTED trip
    assert d10["zero_mileage"] == 0           # waybill nolligi trips'da emas
    assert d10["under_review"] == 2           # qabul qilinganlar (2 ACCEPTED)
    assert d10["performance"] == pytest.approx(10.0, abs=0.2)


def test_monthly_month_param(met):
    """`month=YYYY-MM` parametri — oy boshidan bugungacha (bugungi kunda)."""
    data = met.monthly({"month": "2026-08"})
    assert data["month"] == "2026-08"
    assert data["from"] == "2026-08-01"
    # bugungacha cheklangan; seed faqat 10/11-kunlarida bor
    total = sum(d["total"] for d in data["days"])
    assert total == 2


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


def test_driver_finance_metrics(met):
    storage = met.storage
    storage.save_driver_profile("d1", km_rate=1200, rating=4.8,
                                notification_enabled=True, blacklisted=True)
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 125.5, 2)
    storage.add_driver_fine("d1", "2026-08-10", 50_000, "Kechikish")
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    d1 = {x["driver_id"]: x for x in met.drivers(f)}["d1"]
    assert d1["km"] == 125.5
    assert d1["gross_pay"] == 150600
    assert d1["tax"] == 18072
    assert d1["fines"] == 50000
    assert d1["net_pay"] == 82528
    assert d1["blacklisted"] is True
    detail = met.driver_detail("d1", f)
    assert detail["profile"]["rating"] == 4.8
    assert detail["work_logs"][0]["distance_km"] == 125.5


def test_km_rate_global_override(met, monkeypatch):
    storage = met.storage
    storage.save_driver_profile("d1", km_rate=1200)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    # KM_RATE bo'sh — profil qiymati ishlatiladi
    monkeypatch.setenv("KM_RATE", "")
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["d1"] == 1200
    # Yangi tartib: driver profil > KM_RATE env (shaxsiy narx saqlanadi)
    monkeypatch.setenv("KM_RATE", "2000")
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["d1"] == 1200
    detail = met.driver_detail("d1", f)
    assert detail["driver"]["km_rate"] == 1200


def test_km_rate_by_route(met, monkeypatch):
    """Yo'nalish bo'yicha kmRate (profiles.json `kmRate`) qo'llanadi."""
    import bm_automation.app.core.profiles as pmod
    storage = met.storage
    storage.save_driver(external_id="dRX", full_name="Route haydovchi",
                        route_id="rB80")
    storage.save_driver_profile("dRX", km_rate=0)
    monkeypatch.setenv("KM_RATE", "")
    monkeypatch.setattr(
        pmod, "all_profiles",
        lambda: [{"name": "ASL", "routeVariantId": "rB80", "kmRate": 2363.8}])
    f = {"from": "2026-08-10", "to": "2026-08-10"}
    d = {x["driver_id"]: x for x in met.drivers(f)}["dRX"]
    assert d["km_rate"] == 2363.8
    detail = met.driver_detail("dRX", f)
    assert detail["driver"]["km_rate"] == 2363.8


def test_km_rate_precedence(met, monkeypatch):
    """Ustunlik: haydovchi km_rate > yo'nalish kmRate > KM_RATE env."""
    import bm_automation.app.core.profiles as pmod
    storage = met.storage
    storage.save_driver(external_id="dRX", full_name="Route haydovchi",
                        route_id="rB80")
    storage.save_driver_profile("dRX", km_rate=1500)  # shaxsiy qiymat
    monkeypatch.setattr(
        pmod, "all_profiles",
        lambda: [{"name": "ASL", "routeVariantId": "rB80", "kmRate": 2363.8}])
    f = {"from": "2026-08-10", "to": "2026-08-10"}
    # 1) shaxsiy km_rate yo'nalishdan ustun
    monkeypatch.setenv("KM_RATE", "")
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["dRX"] == 1500
    # 2) shaxsiy km_rate KM_RATE env'dan ham ustun (yangi tartib)
    monkeypatch.setenv("KM_RATE", "900")
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["dRX"] == 1500


def test_km_rate_bot_editable_overrides_env(met, monkeypatch):
    """Yangi tartib: driver profil > bot_settings > KM_RATE env."""
    import bm_automation.app.core.bot_settings as bs
    storage = met.storage
    storage.save_driver_profile("d1", km_rate=1200)
    monkeypatch.setenv("KM_RATE", "2000")
    monkeypatch.setattr(bs, "km_rate", lambda: 2500.0)
    monkeypatch.setattr(bs, "set_km_rate", lambda v: float(v))
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    # Driver profil (1200) ham bot_settings (2500) dan ustun
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["d1"] == 1200
    # Driver profil 0 bo'lsa — bot_settings (2500) ishlatiladi
    storage.save_driver_profile("d1", km_rate=0)
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["d1"] == 2500
    # Bot_settings ham 0 bo'lsa — KM_RATE env (2000) ishlatiladi
    monkeypatch.setattr(bs, "km_rate", lambda: 0.0)
    assert {x["driver_id"]: x["km_rate"] for x in met.drivers(f)}["d1"] == 2000


def test_monthly_accept_rate(met):
    """Qabul qilinganlar / rejadagi qatnovlar nisbati foizda hisoblanadi."""
    storage = met.storage
    storage.save_schedule(date="2026-08-10", route_id="r1", graph_name="P1",
                          driver_id="d1", vehicle_id="v1",
                          start_time="06:00", trip_count=10)
    f = {"from": "2026-08-01", "to": "2026-08-11"}
    data = met.monthly(f)
    d10 = {d["date"]: d for d in data["days"]}["2026-08-10"]
    assert d10["accepted"] == 2
    assert d10["planned"] == 10
    assert d10["accept_rate"] == pytest.approx(20.0)
    assert data["totals"]["accept_rate"] == pytest.approx(20.0)


def test_distance_route_filter(met):
    """Firma pill'langanda masofa (km) faqat shu firma avtobuslarini ko'rsatadi."""
    storage = met.storage

    def rd(plate, rid, fact):
        storage.upsert(
            "route_daily", ["date", "route_id", "vehicle_number"],
            {"date": "2026-08-10", "route_id": rid, "vehicle_number": plate,
             "vehicle_id": plate, "vehicle_brand": "", "shift_name": "",
             "working_day": 0, "trip_plan": 5, "trip_fact": 4, "trip_passed": 3,
             "trip_approved": 3, "distance_plan": 100.0, "distance_fact": fact,
             "distance_fact_extra": 0.0, "last_synced_at": "", "data": "{}"})

    rd("01A001", "r1", 120.5)
    rd("02B002", "r2", 300.0)
    base = {"from": "2026-08-01", "to": "2026-08-11"}

    data = met.distance({**base, "route": "r1"})
    assert [r["plate_number"] for r in data["rows"]] == ["01A001"]
    assert data["totals"]["distance_fact"] == pytest.approx(120.5)

    all_rows = met.distance(base)["rows"]
    assert {r["plate_number"] for r in all_rows} == {"01A001", "02B002"}


def test_drivers_route_filter_separates_firms(met):
    """Firma pill'langanda boshqa firma haydovchilari reestrga aralashmaydi."""
    storage = met.storage
    storage.save_driver(external_id="dF", full_name="Firmachi",
                        route_id="r1")
    storage.save_driver(external_id="dA", full_name="Boshqa firma",
                        route_id="r2")
    storage.save_trip(TripRecord(date="2026-08-10", route_id="r2",
                                 vehicle_id="v1", driver_id="dA",
                                 planned_time="09:00", actual_time="09:05",
                                 status=TripStatus.ACCEPTED.value,
                                 source=SyncSource.DUTY.value))
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10",
         "route": "r1"}
    ids = {x["driver_id"] for x in met.drivers(f)}
    assert "dF" in ids
    assert "dA" not in ids


def test_driver_fines_route_filter(met):
    """Jarimalar ham tanlangan firma bo'yicha chegaralanadi."""
    storage = met.storage
    storage.save_driver(external_id="dF", full_name="Firmachi",
                        route_id="r1")
    storage.save_driver(external_id="dA", full_name="Boshqa firma",
                        route_id="r2")
    storage.add_driver_fine("dF", "2026-08-10", 10_000, "Kechikish")
    storage.add_driver_fine("dA", "2026-08-10", 20_000, "Kechikish")
    f = {"from": "2026-08-10", "to": "2026-08-10", "route": "r1"}
    assert met._driver_fines(f) == {"dF": 10_000.0}


def test_driver_company_and_route_fields(met, monkeypatch):
    """Reestrda har haydovchi uchun firma va yo'nalish nomi ko'rsatiladi."""
    import bm_automation.app.core.profiles as pmod
    storage = met.storage
    storage.save_driver(external_id="d1", full_name="Aliyev Aliy",
                        route_id="r1")
    monkeypatch.setattr(
        pmod, "all_profiles",
        lambda: [{"name": "Firma A", "routeVariantId": "r1",
                  "routeName": "10-yo'nalish", "kmRate": 1000}])
    f = {"from": "2026-08-10", "to": "2026-08-10"}
    d = {x["driver_id"]: x for x in met.drivers(f)}["d1"]
    assert d["company"] == "Firma A"
    assert d["route_name"] == "10-yo'nalish"
    assert d["route_id"] == "r1"


def test_work_logs_avto_replaces_trips_km(met):
    """AVTO (brutto-route) km trips km'ni almashtiradi; qo'lda qayd qo'shiladi."""
    storage = met.storage
    # d1 10-08 v1'da 2 ACCEPTED reys (DUTY, odo km=0) — AVTO 100.0 ustun
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 100.0, 2, note="AVTO")
    storage.save_driver_work_log("2026-08-10", "d1", "v2", 25.0, 1, note="Qo'lda")
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    d1 = {x["driver_id"]: x for x in met.drivers(f)}["d1"]
    assert d1["km"] == 125.0            # AVTO 100.0 + qo'lda 25.0
    assert d1["automatic_km"] == 100.0  # AVTO endi avtomatik km manbasi
    assert d1["manual_trips"] == 1
    assert d1["trips"] == 2
    detail = met.driver_detail("d1", f)
    rows = {(r["vehicle_id"], r["note"]): r["distance_km"]
            for r in detail["work_logs"]}
    assert rows == {("v1", "AVTO"): 100.0, ("v2", "Qo'lda"): 25.0}


def test_work_logs_avto_merge_per_key(met):
    """AVTO qayd faqat shu (sana, haydovchi, avtobus) uchun trips km'ni almashtiradi."""
    storage = met.storage
    # d1: 10-08 v1 (2 reys) uchun AVTO bor; 11-08 v1 (1 reys) uchun yo'q
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 80.0, 3, note="AVTO")
    f = {"from": "2026-08-10", "to": "2026-08-11"}
    d1 = {x["driver_id"]: x for x in met.drivers(f)}["d1"]
    assert d1["km"] == 80.0       # 10-08 AVTO 80.0 + 11-08 trips km=0
    assert d1["trips"] == 4       # AVTO trip_count 3 + 11-08 1 reys
    assert d1["working_days"] == 2
    assert d1["attendance"] == 100.0


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


def test_system_cached(met, monkeypatch):
    """Tashqi servis pingleari qisqa muddatga keshlanadi."""
    import bm_automation.app.dashboard.metrics as mmod
    monkeypatch.setattr(mmod.Metrics, "_health_cache", {"at": 0.0, "data": None})
    met.storage.record_error(source="daily", message="X", context={"p": "A"})
    calls = []
    monkeypatch.setattr(met, "_bm_status",
                        lambda: (calls.append(1) or {"ok": True, "status": 200}))
    s1 = met.system()
    s2 = met.system()
    assert s1 == s2
    assert len(calls) == 1


def test_driver_photo_url(met):
    """Haydovchi rasmi yuklangan bo'lsa photo_url qaytadi."""
    f = {"from": "2026-08-01", "to": "2026-08-11", "month": ""}
    by_id = {x["driver_id"]: x for x in met.drivers(f)}
    assert by_id["d1"]["photo_url"] == ""
    met.storage.save_driver_profile("d1", photo_path="data/driver_photos/d1.jpg")
    by_id = {x["driver_id"]: x for x in met.drivers(f)}
    assert by_id["d1"]["photo_url"] == "/api/drivers/d1/photo"
    det = met.driver_detail("d1", f)
    assert det["driver"]["photo_url"] == "/api/drivers/d1/photo"
    det2 = met.driver_detail("d3", {"month": "2026-08"})
    assert det2 is None or det2["driver"]["photo_url"] == ""


# ---------------------------------------------------------------- eksport

@pytest.fixture()
def seeded_storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "exp.db"))
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


def test_export_drivers_month_scope(patch_storage):
    """Reestr eksporti oy bo'yicha ishlaydi va fayl nomida oy qatnashadi."""
    data = dexport.build_export({"month": "2026-08"}, "csv", "drivers")
    assert b"Aliyev" in data
    name = dexport.filename("csv", "drivers", "2026-08")
    assert "202608" in name and name.endswith(".csv")


def test_route_options_only_profiles(monkeypatch):
    """Pill'lar faqat profiles.json firmalaridan; DB routes jadvali pill bermaydi."""
    import bm_automation.app.dashboard.metrics as mmod

    class _StubStorage:
        class _DB:
            ph = "%s"
        db = _DB()

    companies = {
        "r1": {"company": "Firma A", "route_name": "10-yo'nalish"},
        "r2": {"company": "Firma B", "route_name": ""},
    }
    monkeypatch.setattr(mmod, "get_storage", lambda: _StubStorage())
    monkeypatch.setattr(mmod.Metrics, "_companies", lambda self: companies)
    monkeypatch.setattr(mmod.Metrics, "_route_names",
                        lambda self: {"r2": "20-yo'nalish", "rX": "test-yo'nalish"})
    opts = {o["id"]: o for o in mmod.route_options()}
    assert set(opts) == {"r1", "r2"}
    assert opts["r1"]["name"] == "Firma A"
    assert opts["r1"]["route_name"] == "10-yo'nalish"
    assert opts["r2"]["name"] == "Firma B"
    assert opts["r2"]["route_name"] == "20-yo'nalish"   # bo'sh → DB nomi fallback


def test_db_status_reports_connection(met):
    """_db_status() SELECT 1 orqali ulanishni tekshiradi va maydonlarni to'ldiradi."""
    db = met._db_status()
    assert db["connected"] is True
    assert "latency_ms" in db and db["latency_ms"] >= 0
    assert set(("host", "port", "dbname", "error")) <= set(db)


def test_parse_dsn_uri():
    """Supabase pooler DSN'idan host/port/dbname ajratib olinadi."""
    info = Metrics._parse_dsn(
        "postgresql://postgres.xyz:secret@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require")
    assert info["host"] == "aws-0-ap-northeast-1.pooler.supabase.com"
    assert info["port"] == "5432"
    assert info["dbname"] == "postgres"


def test_system_includes_db_connection_details(met, monkeypatch):
    """system() database blokida Postgres ulanish holati ko'rsatiladi."""
    monkeypatch.setattr(met, "_health_cache", {"at": 0.0, "data": None})
    d = met.system()["database"]
    assert d["ok"] is True
    assert "connected" in d
    assert "latency_ms" in d


# ---------------------------------------------------------------- HTTP handler


class _FakeSocket:
    def __init__(self):
        self._wbuf = io.BytesIO()

    def makefile(self, mode, *args):
        return io.BytesIO() if mode.startswith("r") else self._wbuf

    def sendall(self, data):
        self._wbuf.write(data)


def _get(path):
    server = type("S", (), {"daemon_threads": True})()
    sock = _FakeSocket()
    h = DashboardHandler(sock, ("127.0.0.1", 0), server)
    h.command = "GET"
    h.path = path
    h.requestline = "GET " + path + " HTTP/1.0"
    h.request_version = "HTTP/1.0"
    h.headers = {}
    h.do_GET()
    return json.loads(sock._wbuf.getvalue().split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def test_api_insights_endpoint(monkeypatch):
    """/api/insights analiz natijasi va tavsiyalarni qaytaradi."""
    import bm_automation.app.notifications.ops.self_review as srv
    import bm_automation.app.notifications.ops.openrouter as _or
    monkeypatch.setattr(srv, "enabled", lambda: True)
    monkeypatch.setattr(srv, "analyze", lambda days=None: {
        "days": days or 7, "start": "2026-08-01", "end": "2026-08-08",
        "error_total": 3, "error_days": 2, "top_errors": [],
        "weekday": "SHAN", "failures": 1, "failure_triggers": [],
        "recurring": [], "log_tail": [], "drivers": {}})
    monkeypatch.setattr(srv, "_rule_recs", lambda a: ["Tavsiya 1"])
    monkeypatch.setattr(_or, "configured", lambda: False)
    d = _get("/api/insights?days=5")
    assert d["ok"] is True and d["enabled"] is True
    assert d["insights"]["days"] == 5
    assert d["insights"]["failures"] == 1
    assert d["insights"]["recommendations"] == ["Tavsiya 1"]
    assert d.get("ai_configured") is False


def test_api_insights_default_days(monkeypatch):
    """days parametrsiz — standart davr ishlatiladi."""
    import bm_automation.app.notifications.ops.self_review as srv
    import bm_automation.app.notifications.ops.openrouter as _or
    monkeypatch.setattr(srv, "enabled", lambda: False)
    monkeypatch.setattr(srv, "analyze", lambda days=None: {"days": days or 14})
    monkeypatch.setattr(srv, "_rule_recs", lambda a: [])
    monkeypatch.setattr(_or, "configured", lambda: False)
    d = _get("/api/insights")
    assert d["ok"] is True and d["enabled"] is False
    assert d["insights"]["days"] == 14
