"""Demo korxona (demo-seed) testlari.

Qamrov:
- kompaniya, foydalanuvchi va namuna katalog yaratilishi;
- real ma'lumotlarga tegmasligi va oynaning idempotent qayta yozilishi;
- oyna oxiri (real reys sanasi) va ogohlantirish;
- login bandligi / parol siyosati;
- tenant scope, metrikalar, firma pill'lari va CLI ulanishi.
"""

import json
from datetime import date

import pytest

from bm_automation.app.core import bot_settings
from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.core import tenant as tenant_mod
from bm_automation.app.dashboard import deps
from bm_automation.app.dashboard import demo_seed
from bm_automation.app.dashboard import metrics as metrics_mod
from bm_automation.app.dashboard.demo_seed import (
    DEMO_COMPANY, DEMO_PAGES, seed_demo)
from bm_automation.app.dashboard.metrics import Metrics
from bm_automation.app.db.storage import Storage, storage_for
from tests.sqlite_backend import SQLiteDatabase

_FILTER_KEYS = ("date", "from", "to", "route", "vehicle", "driver",
                "status", "company")


def _f(**kw) -> dict:
    out = {k: "" for k in _FILTER_KEYS}
    out.update(kw)
    return out


@pytest.fixture()
def denv(monkeypatch, tmp_path):
    db = SQLiteDatabase(str(tmp_path / "demo.db"))
    st = storage_for(db)
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage", lambda: st)
    monkeypatch.setattr(metrics_mod, "get_storage", lambda: st)
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE", tmp_path / "profiles.json")
    profiles_mod.save_profiles({"active": "", "profiles": []})
    tenant_mod.reset()
    deps.invalidate_user_access()
    yield st
    tenant_mod.reset()
    deps.invalidate_user_access()


def test_seed_creates_company_user_and_catalog(denv):
    st = denv
    res = seed_demo(storage=st, username="demo", password="DemoParol123",
                    days=7, end_date="2026-10-20")

    assert res["company"] == DEMO_COMPANY
    assert res["company_created"] is True
    assert res["company_active"] is True
    assert res["user_created"] is True
    assert res["password"] == ""          # parol foydalanuvchi bergan
    assert res["window"] == {"from": "2026-10-14", "to": "2026-10-20",
                             "days": 7}
    assert res["counts"] == {
        "drivers": 8, "vehicles": 6, "trips": 7 * 6 * 4,
        "route_daily": 7 * 6, "schedules": 7 * 6, "work_logs": 7 * 6,
        "fines": 2, "appeals": 2, "avans": 2,
    }

    company = st.find("companies", slug="demo-korxona")
    assert company["status"] == "active"
    assert company["subscription_expire_date"] == ""

    user = st.dashboard_user_get(username="demo")
    assert user["role"] == "MANAGER"
    assert user["company"] == DEMO_COMPANY
    assert user["company_id"] == res["company_id"]
    assert user["active"] == 1
    assert deps.verify_password("DemoParol123", user["salt"],
                                user["password_hash"])
    data = json.loads(user["data"])
    assert data["demo"] is True
    assert set(data["pages"]) == set(DEMO_PAGES)

    cid = res["company_id"]
    assert {r["route_id"] for r in st.query(
        "SELECT route_id FROM profiles")} == {"DEMO-R01", "DEMO-R02"}
    assert st.query("SELECT COUNT(*) AS n FROM vehicles")[0]["n"] == 6
    assert st.query("SELECT COUNT(*) AS n FROM driver_profiles")[0]["n"] == 8
    for table in ("profiles", "routes", "vehicles", "drivers",
                  "driver_profiles", "trips", "route_daily", "schedules",
                  "driver_work_logs", "driver_fines", "driver_appeals",
                  "avans"):
        bad = st.query(
            f"SELECT COUNT(*) AS n FROM {table} WHERE company_id != {cid}"
        )[0]["n"]
        assert bad == 0, table

    assert tenant_mod.company_routes(DEMO_COMPANY, storage=st) == [
        "DEMO-R01", "DEMO-R02"]
    assert deps._company_routes(DEMO_COMPANY) == ["DEMO-R01", "DEMO-R02"]


def test_seed_is_idempotent_and_isolated(denv):
    st = denv
    st.insert("trips", {
        "date": "2026-10-18", "route_id": "REAL-R1", "vehicle_id": "real-v1",
        "driver_id": "real-d1", "planned_time": "08:00",
        "status": "ACCEPTED", "distance_km": 10.0,
    })

    seed_demo(storage=st, username="demo", password="DemoParol123",
              days=5, end_date="2026-10-20")
    user = st.dashboard_user_get(username="demo")
    old_hash = user["password_hash"]

    # Qayta ishga tushirish: yangi oyna, parol saqlanadi
    res = seed_demo(storage=st, username="demo", days=3,
                    end_date="2026-10-21")
    assert res["user_created"] is False
    assert res["password"] == ""
    assert res["counts"]["trips"] == 3 * 6 * 4

    user = st.dashboard_user_get(username="demo")
    assert user["password_hash"] == old_hash
    assert json.loads(user["data"])["pages"] == list(DEMO_PAGES)

    # Demo jadval faqat yangi oynada, real ma'lumot tegilmagan
    assert st.query("SELECT COUNT(*) AS n FROM trips "
                    "WHERE route_id LIKE 'DEMO-%'")[0]["n"] == 72
    assert st.query("SELECT COUNT(*) AS n FROM trips "
                    "WHERE route_id = 'REAL-R1'")[0]["n"] == 1
    assert st.query("SELECT COUNT(*) AS n FROM dashboard_users")[0]["n"] == 1
    assert st.query("SELECT COUNT(*) AS n FROM companies")[0]["n"] == 1
    assert st.query("SELECT COUNT(*) AS n FROM driver_fines")[0]["n"] == 2


def test_generated_password_and_reset(denv):
    st = denv
    first = seed_demo(storage=st, days=2, end_date="2026-10-20")
    assert len(first["password"]) >= 8
    user = st.dashboard_user_get(username="demo")
    assert deps.verify_password(first["password"], user["salt"],
                                user["password_hash"])

    second = seed_demo(storage=st, username="demo", password="YangiParol123",
                       days=2, end_date="2026-10-20")
    assert second["password"] == ""
    user = st.dashboard_user_get(username="demo")
    assert deps.verify_password("YangiParol123", user["salt"],
                                user["password_hash"])
    assert not deps.verify_password(first["password"], user["salt"],
                                    user["password_hash"])


def test_end_date_uses_real_trip_and_warns(denv):
    st = denv
    assert demo_seed.resolve_end_date(st) == date.today().isoformat()

    st.insert("trips", {
        "date": "2026-10-18", "route_id": "REAL-R1", "vehicle_id": "real-v1",
        "driver_id": "real-d1", "planned_time": "08:00",
    })
    assert demo_seed.resolve_end_date(st) == "2026-10-18"

    # Demo reyslari standart sanani siljitmaydi
    seed_demo(storage=st, days=3, end_date="2026-10-18")
    assert demo_seed.resolve_end_date(st) == "2026-10-18"

    future = seed_demo(storage=st, days=2, end_date="2026-10-25")
    assert any("standart sanasi" in w for w in future["warnings"])


def test_username_collision_is_rejected_before_writes(denv):
    st = denv
    digest, salt = deps.hash_password("BoshqaParol1")
    st.dashboard_user_add("demo", digest, salt, "MANAGER", "REAL KORXONA",
                          1, "{}")

    with pytest.raises(ValueError, match="REAL KORXONA"):
        seed_demo(storage=st, username="demo", password="DemoParol123",
                  days=3, end_date="2026-10-20")

    assert st.query("SELECT COUNT(*) AS n FROM companies")[0]["n"] == 0
    user = st.dashboard_user_get(username="demo")
    assert user["company"] == "REAL KORXONA"


def test_password_policy_and_bad_input(denv):
    st = denv
    with pytest.raises(ValueError):
        seed_demo(storage=st, username="demo", password="qisqa",
                  days=3, end_date="2026-10-20")
    with pytest.raises(ValueError):
        seed_demo(storage=st, username="demo", days=0,
                  end_date="2026-10-20")
    with pytest.raises(ValueError):
        seed_demo(storage=st, username="BOSH harf", days=3,
                  end_date="2026-10-20")
    with pytest.raises(ValueError):
        seed_demo(storage=st, username="demo", days=3,
                  end_date="20-10-2026")
    assert st.query("SELECT COUNT(*) AS n FROM companies")[0]["n"] == 0


def test_disabled_storage_is_rejected(tmp_path):
    st = Storage(db=SQLiteDatabase(str(tmp_path / "off.db")), enabled=False)
    with pytest.raises(RuntimeError):
        seed_demo(storage=st, days=2, end_date="2026-10-20")


def test_metrics_scope_and_salary(denv):
    st = denv
    st.insert("trips", {
        "date": "2026-10-19", "route_id": "REAL-R1", "vehicle_id": "real-v1",
        "driver_id": "real-d1", "planned_time": "08:00",
        "status": "ACCEPTED", "distance_km": 10.0,
    })
    seed_demo(storage=st, days=5, end_date="2026-10-20")

    m = Metrics(storage=st)
    rows = m.drivers(_f(date="2026-10-19"))
    demo = [r for r in rows if r["company"] == DEMO_COMPANY]
    assert len(demo) == 8
    assert {r["route_id"] for r in demo} == {"DEMO-R01", "DEMO-R02"}
    for r in demo:
        assert r["trips"] > 0
        assert r["km"] > 0
        assert r["km_rate"] == 450.0
        assert r["gross_pay"] > 0 and r["net_pay"] > 0
        assert r["route_name"].startswith("DEMO ")

    # Yo'nalish filtri faqat shu yo'nalish haydovchilarini beradi
    only = m.drivers(_f(date="2026-10-19", route="DEMO-R01"))
    assert only and all(r["route_id"] == "DEMO-R01" for r in only)
    assert all(r["driver_id"].startswith("demo-drv-") for r in only)

    # Kompaniya filtri real qatorlarni qo'shmaydi
    by_company = m.drivers(metrics_mod.parse_filters(
        _f(date="2026-10-19", company=DEMO_COMPANY)))
    assert by_company and all(r["company"] == DEMO_COMPANY for r in by_company)

    companies = m._companies()
    assert companies["DEMO-R01"]["company"] == DEMO_COMPANY
    assert companies["DEMO-R02"]["route_name"].startswith("DEMO ")

    elec = m.electricity_report(_f(**{"from": "2026-10-19",
                                      "to": "2026-10-19"}))
    assert elec["totals"]["km"] > 0
    assert elec["totals"]["kwh"] > 0
    assert elec["totals"]["total"] == 0.0  # narx global sozlama (testda 0)

    bot_settings.set_elec_price(450.0)
    elec = m.electricity_report(_f(**{"from": "2026-10-19",
                                      "to": "2026-10-19"}))
    assert elec["totals"]["total"] > 0


def test_route_options_include_demo_pills(denv):
    st = denv
    seed_demo(storage=st, days=3, end_date="2026-10-20")
    opts = {o["id"]: o for o in metrics_mod.route_options()}
    assert {"DEMO-R01", "DEMO-R02"} <= set(opts)
    assert opts["DEMO-R01"]["company"] == DEMO_COMPANY
    assert opts["DEMO-R01"]["route_name"].startswith("DEMO ")

    # Bloklangan korxona pill'larda ko'rinmaydi
    st._exec(f"UPDATE companies SET status={st.db.ph} "
             f"WHERE slug={st.db.ph}", ("blocked", "demo-korxona"))
    tenant_mod.reset()
    opts = {o["id"]: o for o in metrics_mod.route_options()}
    assert "DEMO-R01" not in opts


def test_cli_parser_wires_demo_seed():
    from bm_automation.cli import commands
    from bm_automation.cli.parser import build_parser

    args = build_parser().parse_args([
        "demo-seed", "--username", "demo2", "--days", "5",
        "--company", "DEMO 2", "--end-date", "2026-10-20",
    ])
    assert args.func is commands.cmd_demo_seed
    assert args.username == "demo2"
    assert args.days == 5
    assert args.company == "DEMO 2"
    assert args.end_date == "2026-10-20"
