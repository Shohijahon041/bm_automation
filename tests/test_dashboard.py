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


def test_km_rate_route_level(met, monkeypatch):
    """Yo'nalish darajasida route_km — kompaniya profilidan ustun."""
    import bm_automation.app.core.bot_settings as bs
    import bm_automation.app.core.profiles as pmod
    from bm_automation.app.config.settings import km_rate_for
    monkeypatch.setenv("KM_RATE", "")
    monkeypatch.setattr(
        pmod, "all_profiles",
        lambda: [{"name": "ASL", "routeVariantId": "r1", "kmRate": 2363.8}])
    # route_km yo'q — kompaniya profili qo'llanadi
    assert km_rate_for("r1", 0.0) == 2363.8
    # route_km o'rnatildi — kompaniya profilidan ustun
    monkeypatch.setattr(bs, "route_km",
                        lambda rid, default=0.0: 3000.0 if rid == "r1" else default)
    assert km_rate_for("r1", 0.0) == 3000.0
    # Haydovchi shaxsiy narxi route_km dan ham ustun
    assert km_rate_for("r1", 4500.0) == 4500.0


def test_update_km_rate_without_drivers(met, monkeypatch):
    """Dashboard'dan km narx o'rnatish haydovchilar bo'lmasa ham ishlaydi."""
    import bm_automation.app.core.bot_settings as bs
    from bm_automation.app.dashboard.server import DashboardHandler
    saved = {}
    monkeypatch.setattr(bs, "set_route_km",
                        lambda rid, val: saved.update({rid: val}) or val)
    handler = object.__new__(DashboardHandler)
    res = handler._update_km_rate({"route_id": "r99", "km_rate": 2500})
    assert res["ok"] is True
    assert res["route_id"] == "r99" and res["km_rate"] == 2500
    assert saved == {"r99": 2500}
    # Yaroqsiz qiymat rad etiladi
    assert handler._update_km_rate({"route_id": "r99", "km_rate": -5})["ok"] is False
    assert handler._update_km_rate({"km_rate": 100})["ok"] is False


def test_routes_km_rate_field(met, monkeypatch):
    """routes() va route_options() da km_rate maydoni mavjud."""
    import bm_automation.app.dashboard.metrics as metrics
    monkeypatch.setattr(metrics, "_route_km_value", lambda rid: 1234.0)
    f = {"from": "2026-08-10", "to": "2026-08-11"}
    rows = met.routes(f)
    assert rows and all(r["km_rate"] == 1234.0 for r in rows)


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
    storage.save_driver_work_log("2026-08-10", "d1", "v1", 100.0, 2, note="AVTO",
                                 distance_plan=150.0, trip_plan=6)
    storage.save_driver_work_log("2026-08-10", "d1", "v2", 25.0, 1, note="Qo'lda")
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    d1 = {x["driver_id"]: x for x in met.drivers(f)}["d1"]
    assert d1["km"] == 125.0            # AVTO 100.0 + qo'lda 25.0
    assert d1["automatic_km"] == 100.0  # AVTO endi avtomatik km manbasi
    assert d1["manual_trips"] == 1
    assert d1["trips"] == 2
    # saytdagi brutto-route reja ustunlari AVTO qayddan olinadi
    assert d1["plan_km"] == 150.0
    assert d1["plan_trips"] == 6
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


def test_not_accepted_km_report_includes_manual_km(met):
    """Qo'lda (driver_work_logs, note != 'AVTO') km/reys faqat AMALDA
    (bajarilgan) tomonga qo'shiladi: Amalda km + Amalda reyslar. Reja
    o'zgarmaydi — shuning uchun qabul qilinmagan (reja − amalda)
    qo'lda qayd hisobiga kamayadi."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_plan": 5, "trip_fact": 3, "distance_plan": 150.0,
        "distance_fact": 90.0})
    stor.save_driver_work_log("2026-08-10", "d1", "v1", 30.0, 1,
                              note="Qo'lda")
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    rows = {r["name"]: r for r in d["rows"]}
    assert "Aliyev Aliy" in rows
    r = rows["Aliyev Aliy"]
    assert r["plan_km"] == 150.0          # reja o'zgarmaydi (30 qo'lda emas)
    assert r["fact_km"] == 120.0          # 90 amalda + 30 qo'lda
    assert r["manual_km"] == 30.0
    assert r["plan_reys"] == 5            # reja o'zgarmaydi (1 qo'lda emas)
    assert r["fact_reys"] == 4            # 3 amalda + 1 qo'lda reys
    assert r["qabul_qilinmagan"] == 1     # 5 - 4 → qo'lda qayd defitsit yopadi
    assert r["diff"] == 30.0              # 150 - 120
    assert d["totals"]["manual_km"] == 30.0
    assert d["totals"]["diff"] == 30.0


def test_not_accepted_km_report_ignores_avto_work_logs(met):
    """AVTO (brutto-route) qaydlari fakt km'ga qo'shilmaydi — aks holda
    ikki marta hisoblanish bo'lardi (fakt km allaqachon route_daily'dan)."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_plan": 5, "trip_fact": 3, "distance_plan": 150.0,
        "distance_fact": 90.0})
    stor.save_driver_work_log("2026-08-10", "d1", "v1", 50.0, 2, note="AVTO")
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    r = next(r for r in d["rows"] if r["name"] == "Aliyev Aliy")
    assert r["manual_km"] == 0.0
    assert r["fact_km"] == 90.0  # faqat route_daily distance_fact


def test_not_accepted_km_report_shows_all_worked_drivers(met):
    """Xato (qabul qilinmagan reysi 0) qilmagan ishlagan haydovchilar ham
    hisobotda chiqadi — faqat muammolilar emas."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_plan": 5, "trip_fact": 3, "distance_plan": 150.0,
        "distance_fact": 90.0})
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-10", "route_id": "r1", "vehicle_id": "v2",
        "vehicle_number": "01B456BB", "working_day": 1,
        "trip_plan": 3, "trip_fact": 3, "distance_plan": 80.0,
        "distance_fact": 80.0})
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    rows = {r["name"]: r for r in d["rows"]}
    assert "Aliyev Aliy" in rows
    assert "Karimov Karim" in rows                       # qabul qilinmagan = 0
    assert rows["Karimov Karim"]["qabul_qilinmagan"] == 0
    assert rows["Karimov Karim"]["fact_reys"] == 3
    assert rows["Aliyev Aliy"]["qabul_qilinmagan"] == 2


def test_not_accepted_report_reserve_time_split(met):
    """Bitta grafik kuni ikki haydovchi/avtobus almashganda (buzilish →
    reserve avtobus): reja vaqtga proporsional bo'linadi, qabul qilinmagan
    reyslar ertalab birinchi chiqqan avtobus haydovchisiga tegishli bo'ladi.
    Amalda (fact) rejadan ortiq bo'lsa ham manfiy ko'rsatilmaydi (clamp)."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-15", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 0,
        "trip_plan": 0, "trip_fact": 3, "distance_plan": 0.0,
        "distance_fact": 30.0})       # buzilgan avtobus — fact bor, reja yo'q
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-15", "route_id": "r1", "vehicle_id": "v2",
        "vehicle_number": "01B456BB", "working_day": 1,
        "trip_plan": 14, "trip_fact": 8, "distance_plan": 200.0,
        "distance_fact": 110.0})      # kunlik rejani olib yuradi
    stor.save_schedule("2026-08-15", "r1", "P1", driver_id="d1",
                       vehicle_id="v1", start_time="06:00:00",
                       end_time="12:00:00", trip_count=14)
    stor.save_schedule("2026-08-15", "r1", "P1", driver_id="d2",
                       vehicle_id="v2", start_time="12:00:00",
                       end_time="20:00:00", trip_count=14)
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    rows = {r["name"]: r for r in d["rows"]}
    assert rows["Aliyev Aliy"]["plan_reys"] == 6      # 14 * 360/840
    assert rows["Aliyev Aliy"]["fact_reys"] == 3      # o'z avtobus faktidan
    assert rows["Aliyev Aliy"]["qabul_qilinmagan"] == 3
    assert round(rows["Aliyev Aliy"]["plan_km"], 2) == 85.71
    assert rows["Aliyev Aliy"]["fact_km"] == 30.0
    assert rows["Karimov Karim"]["plan_reys"] == 8    # 14 * 480/840
    assert rows["Karimov Karim"]["fact_reys"] == 8
    assert rows["Karimov Karim"]["qabul_qilinmagan"] == 0
    assert d["totals"]["plan_reys"] == 14
    assert d["totals"]["fact_reys"] == 11
    assert d["totals"]["qabul_qilinmagan"] == 3


def test_not_accepted_report_clamps_negative_diff(met):
    """Amalda (fact) rejadan (plan) ortiq bo'lsa — qabul qilinmagan reyslar
    va km manfiy chiqmaydi (0 ga kesiladi): reja hech qachon amaldan kichik
    ko'rsatilmaydi."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-16", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_plan": 5, "trip_fact": 7, "distance_plan": 150.0,
        "distance_fact": 210.0})      # kam reja, ortiq bajarilgan ish
    stor.save_schedule("2026-08-16", "r1", "P1", driver_id="d1",
                       vehicle_id="v1", start_time="06:00:00",
                       end_time="20:00:00", trip_count=5)
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    rows = {r["name"]: r for r in d["rows"]}
    r = rows["Aliyev Aliy"]
    assert r["plan_reys"] == 5
    assert r["fact_reys"] == 7
    assert r["qabul_qilinmagan"] == 0       # 5 - 7 = -2 → 0
    assert r["plan_km"] == 150.0
    assert r["fact_km"] == 210.0
    assert r["diff"] == 0.0                  # 150 - 210 = -60 → 0
    assert r["sum_no_vat"] == 0.0
    assert r["sum_vat"] == 0.0
    assert d["totals"]["qabul_qilinmagan"] == 0
    assert d["totals"]["diff"] == 0.0


def test_not_accepted_report_unattributed_working_day(met):
    """wd=1 lekin na schedules, na trips bo'lgan kun jami sayt bilan mos
    bo'lishi uchun 'Atribut qilinmagan' haydovchiga kiritiladi."""
    stor = met.storage
    stor.upsert("route_daily", ["date", "route_id", "vehicle_number"], {
        "date": "2026-08-16", "route_id": "r1", "vehicle_id": "v1",
        "vehicle_number": "01A123AA", "working_day": 1,
        "trip_plan": 5, "trip_fact": 0, "distance_plan": 100.0,
        "distance_fact": 0.0})
    d = met.not_accepted_km_report({"from": "2026-08-01", "to": "2026-08-31"})
    orph = next(r for r in d["rows"] if "Atribut qilinmagan" in r["name"])
    assert orph["plan_reys"] == 5
    assert orph["fact_reys"] == 0
    assert orph["qabul_qilinmagan"] == 5
    assert orph["days"] == 1
    assert d["totals"]["plan_reys"] == 5                      # sayt bilan mos
    assert d["totals"]["qabul_qilinmagan"] == 5


def test_attendance_includes_manual_work_log_days(seeded_storage, monkeypatch):
    """Ishga chiqish kalendarida qo'lda (driver_work_logs, note != 'AVTO')
    ish kunlari ham ko'rinadi; trips bor kunda trips ustun turadi; qo'lda
    reys soni 0 bo'lsa ham kun ishlangan deb kiritiladi. AVTO (brutto-route)
    ishlangan kunlar ham kalendarga kiritiladi, ammo qo'lda qayd ustun."""
    import bm_automation.app.db.storage as dbs
    seeded_storage.save_driver(external_id="d1", full_name="Aliyev Aliy",
                               route_id="r1")
    seeded_storage.save_driver_work_log("2026-08-14", "d1", "v1", 257.2, 14,
                                        note="GPS xatoligi uchun qo'shildi")
    seeded_storage.save_driver_work_log("2026-08-15", "d1", "v1", 257.2, 14,
                                        note="GPS xatoligi uchun qo'shildi")
    seeded_storage.save_driver_work_log("2026-08-16", "d1", "v1", 50.0, 0,
                                        note="KM qaydi")
    seeded_storage.save_driver_work_log("2026-08-10", "d1", "v1", 30.0, 3,
                                        note="Bosib tuzatish")
    seeded_storage.save_driver_work_log("2026-08-17", "d1", "v1", 60.0, 5,
                                        note="AVTO", working_day=1)
    seeded_storage.save_driver_work_log("2026-08-18", "d1", "v1", 70.0, 7,
                                        note="AVTO", working_day=0)
    monkeypatch.setattr(dbs, "get_storage", lambda: seeded_storage)
    d = _get("/api/attendance?month=2026-08")
    assert d["ok"] is True
    by_id = {x["driver_id"]: x for x in d["drivers"]}
    d1 = by_id["d1"]
    assert d1["work_days"]["2026-08-10"] == 2    # trips ustun (qo'lda 3 emas)
    assert d1["work_days"]["2026-08-14"] == 14
    assert d1["work_days"]["2026-08-15"] == 14
    assert d1["work_days"]["2026-08-16"] == 0    # reys 0 bo'lsa ham kiritiladi
    assert d1["work_days"]["2026-08-17"] == 5    # AVTO sayti ishlagan kun kiritiladi
    assert "2026-08-18" not in d1["work_days"]   # working_day=0 AVTO kiritilmaydi
    # Yo'nalish filtri qo'lda kunlarni ham o'z ichiga oladi
    d2 = _get("/api/attendance?month=2026-08&route=r1")
    by_id2 = {x["driver_id"]: x for x in d2["drivers"]}
    assert "2026-08-14" in by_id2["d1"]["work_days"]
    d3 = _get("/api/attendance?month=2026-08&route=r2")
    assert "d1" not in {x["driver_id"] for x in d3["drivers"]}


def test_tabel_data_includes_manual_work_log_days(seeded_storage, monkeypatch):
    """Oylik tabel eksportida qo'lda ish kunlari hisobga olinadi; Ish kuni
    soni ham shu kunlarni qo'shib, mavjudlik bo'yicha sanaladi."""
    import bm_automation.app.db.storage as dbs
    seeded_storage.save_driver_work_log("2026-08-14", "d1", "v1", 257.2, 14,
                                        note="GPS xatoligi uchun qo'shildi")
    seeded_storage.save_driver_work_log("2026-08-15", "d1", "v1", 257.2, 14,
                                        note="GPS xatoligi uchun qo'shildi")
    seeded_storage.save_driver_work_log("2026-08-16", "d1", "v1", 50.0, 0,
                                        note="KM qaydi")
    seeded_storage.save_driver_work_log("2026-08-10", "d1", "v1", 30.0, 3,
                                        note="Bosib tuzatish")
    seeded_storage.save_driver_work_log("2026-08-17", "d1", "v1", 60.0, 5,
                                        note="AVTO")
    monkeypatch.setattr(dbs, "get_storage", lambda: seeded_storage)
    _, cols, out = dexport._tabel_data("2026-08", "")
    day_idx = {int(cols[i]): i for i in range(3, 3 + 31)}
    total_idx = len(cols) - 1                     # "Ish kuni" ustuni
    row = next(r for r in out if r[1] == "Aliyev Aliy")
    assert row[day_idx[10]] == 2                  # trips ustun
    assert row[day_idx[14]] == 14
    assert row[day_idx[15]] == 14
    assert row[day_idx[16]] == 0                  # reys 0 → qatorda 0
    assert row[day_idx[17]] == ""                 # AVTO yo'q
    assert row[total_idx] == 4                    # 10, 14, 15, 16


def _save_route_daily(storage, date_str, route_id, vehicle_id, vehicle_number,
                      **kw):
    """route_daily qaydini yozadi (sync.upsert orqali)."""
    row = {
        "date": date_str, "route_id": route_id, "vehicle_id": vehicle_id,
        "vehicle_number": vehicle_number,
        "working_day": 0, "trip_plan": 0, "trip_fact": 0,
        "trip_passed": 0, "trip_approved": 0,
        "distance_plan": 0.0, "distance_fact": 0.0, "distance_fact_extra": 0.0,
    }
    row.update({k: v for k, v in kw.items()})
    return storage.upsert("route_daily", ["date", "route_id", "vehicle_number"],
                          row)


def test_today_uses_route_daily_over_trips(met):
    """Sayt brutto-route (route_daily) bo'lsa reja/amalda/faol avtobuslar
    saytdan olinadi — trips/schedules emas."""
    _save_route_daily(met.storage, "2026-08-10", "r1", "v1", "01A123AA",
                      working_day=1, trip_plan=20, trip_fact=16)
    _save_route_daily(met.storage, "2026-08-10", "r1", "v2", "01B456BB",
                      working_day=1, trip_plan=18, trip_fact=14)
    met.storage.save_schedule(date="2026-08-10", route_id="r1", graph_name="P1",
                              driver_id="d1", vehicle_id="v1",
                              start_time="06:00", trip_count=10)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    t = met.today(f)
    assert t["planned"] == 38            # trip_plan yig'indisi (schedules 10 emas)
    assert t["completed"] == 30          # trip_fact yig'indisi (trips 3 emas)
    assert t["active_buses"] == 2        # working_day>0 avtobuslar


def test_today_falls_back_when_no_route_daily(met):
    """route_daily qaydi bo'lmasa eski trips/schedules mantiq ishlaydi."""
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    t = met.today(f)
    assert t["planned"] == 0             # schedules yo'q
    assert t["completed"] == 2           # trips'dagi bajarilganlar
    assert t["active_buses"] == 2        # trips'dagi avtobuslar


def test_monthly_uses_route_daily_over_trips(met):
    """monthly'da route_daily bor kun uchun planned/total/accepted saytdan."""
    _save_route_daily(met.storage, "2026-08-10", "r1", "v1", "01A123AA",
                      working_day=1, trip_plan=20, trip_fact=16,
                      trip_approved=15)
    f = {"from": "2026-08-01", "to": "2026-08-11"}
    days = {d["date"]: d for d in met.monthly(f)["days"]}
    d10 = days["2026-08-10"]
    assert d10["planned"] == 20          # trip_plan (schedules 0 emas)
    assert d10["total"] == 16            # trip_fact (trips statuslar emas)
    assert d10["accepted"] == 15         # trip_approved
    assert d10["completed"] == 16
    d11 = days["2026-08-11"]             # route_daily yo'q — trips fallback
    assert d11["planned"] == 0
    assert d11["total"] == 0             # PENDING_ACCESS "Jami"ga kirmaydi


def test_routes_uses_route_daily_approved(met):
    """routes'da planned/actual/accepted route_daily'dan ustuvor."""
    _save_route_daily(met.storage, "2026-08-10", "r1", "v1", "01A123AA",
                      working_day=1, trip_plan=20, trip_fact=16,
                      trip_approved=15, trip_passed=16)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    r = [x for x in met.routes(f) if x["route_id"] == "r1"][0]
    assert r["planned"] == 20            # trip_plan
    assert r["actual"] == 16             # trip_fact
    assert r["accepted"] == 15           # trip_approved (trips accepted 2 emas)


def test_vehicles_uses_route_daily_fact(met):
    """vehicles'da trips soni route_daily trip_fact'dan — saytdan."""
    _save_route_daily(met.storage, "2026-08-10", "r1", "v1", "01A123AA",
                      working_day=1, trip_plan=20, trip_fact=16)
    _save_route_daily(met.storage, "2026-08-10", "r1", "v2", "01B456BB",
                      working_day=0, trip_plan=18, trip_fact=0)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    by_id = {v["vehicle_id"]: v for v in met.vehicles(f)}
    assert by_id["v1"]["trips"] == 16    # trip_fact (trips 2 emas)
    assert by_id["v1"]["planned"] == 20
    assert by_id["v1"]["work_days"] == 1
    assert by_id["v2"]["trips"] == 0     # working_day=0, trip_fact=0
    assert by_id["v2"]["status"] == "faol emas"


def test_brutto_uses_per_route_skm(monkeypatch, met):
    """Brutto: har bir yo'nalish (firma) uchun alohida SKM qo'llanadi."""
    from bm_automation.app.dashboard.metrics import brutto
    import bm_automation.app.core.bot_settings as bs

    # d1 r1'da AVTO 100.0 km — SKM r1 uchun 20000, r2 uchun 16176 (default)
    met.storage.save_driver_work_log("2026-08-10", "d1", "v1", 100.0, 2,
                                     note="AVTO", distance_plan=100.0,
                                     trip_plan=2)
    monkeypatch.setattr(bs, "route_skm", lambda rid, dflt=0.0: 20000.0
                        if rid == "r1" else 16176.0)
    monkeypatch.setattr(bs, "brutto_skm", lambda: 16176.0)
    monkeypatch.setattr("bm_automation.app.dashboard.metrics.get_storage",
                        lambda: met.storage)
    f = {"date": "2026-08-10", "from": "2026-08-10", "to": "2026-08-10"}
    rep = brutto(f)
    d1 = [r for r in rep["rows"] if r["driver_id"] == "d1"][0]
    assert d1["skm"] == 20000.0
    assert d1["lr"] == 100.0
    assert d1["lf"] == 100.0
    # Lf=100=Lr, Kamal=2, Kstjb=0, Kmaq=0 → S = 20000 * 100
    assert abs(d1["tolov"] - 20000.0 * 100.0) < 0.01
