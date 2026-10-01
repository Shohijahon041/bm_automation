"""Tenant bootstrap testlari (tenancy.py) — SQLite/tmp.

Qamrov: slugify (kirill translit), kompaniyalar seed (idempotent,
find-then-insert — qo'lda kiritilgan sozlamalar saqlanadi), multi-route
kompaniyani birlashtirish, company_id backfill (route / driver / nom
bo'yicha), Storage.enabled integratsiyasi.
"""

import pytest

from bm_automation.app.db.storage import Storage, storage_for
from bm_automation.app.db.tenancy import (backfill_company_ids,
                                          bootstrap_tenancy, seed_companies,
                                          slugify)
from tests.sqlite_backend import SQLiteDatabase

# AND ECO 4 ta yo'nalish bilan — bitta kompaniya bo'lishi kerak.
PROFILES = [
    {"name": "FERGANATEX", "routeVariantId": "route-a"},
    {"name": "ASL SUNDAY MCHJ", "routeVariantId": "route-b"},
    {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "route-c1"},
    {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "route-c2"},
    {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "route-c3"},
    {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "route-c4"},
]


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "test.db"))
    return storage_for(db)


def _ph(storage) -> str:
    return storage.db.ph


# ---------------------------------------------------------------- slugify

def test_slugify_translit_and_cleanup():
    assert slugify("AND ECO TRANSPORT MCHJ") == "and-eco-transport-mchj"
    assert slugify("FERGANATEX") == "ferganatex"
    assert slugify("Фарғона Транс") == "fargona-trans"
    assert slugify("  Ko'ksaroy  ") == "koksaroy"
    assert slugify("").startswith("company-")


# ------------------------------------------------------------------- seed

def test_seed_companies_merges_multi_route(storage):
    ids = seed_companies(storage, PROFILES)
    assert len(ids) == 3
    rows = storage.query("SELECT slug FROM companies ORDER BY slug")
    assert [r["slug"] for r in rows] == [
        "and-eco-transport-mchj", "asl-sunday-mchj", "ferganatex"]
    assert ids["and-eco-transport-mchj"] != ids["ferganatex"]


def test_seed_companies_idempotent_keeps_settings(storage):
    ids1 = seed_companies(storage, PROFILES)
    # Qo'lda tahrirlangan maydonlar (obuna/status) — seed ularni buzmasin.
    ph = _ph(storage)
    fer = ids1["ferganatex"]
    storage._exec(
        f"UPDATE companies SET subscription_plan={ph}, status={ph} "
        f"WHERE id={ph}", ("pro", "blocked", fer))
    ids2 = seed_companies(storage, PROFILES)
    assert ids2 == ids1
    n = storage.query("SELECT COUNT(*) AS n FROM companies")[0]["n"]
    assert n == 3
    row = storage.find("companies", slug="ferganatex")
    assert row["subscription_plan"] == "pro"
    assert row["status"] == "blocked"


# ---------------------------------------------------------------- backfill

def test_backfill_route_tables(storage):
    ids = seed_companies(storage, PROFILES)
    storage.insert("trips", {"date": "2026-10-01", "route_id": "route-c1",
                             "status": "ACCEPTED"})
    storage.insert("trips", {"date": "2026-10-01", "route_id": "route-a",
                             "status": "ACCEPTED"})
    storage.insert("trips", {"date": "2026-10-01", "route_id": "route-unknown",
                             "status": "ACCEPTED"})
    storage.insert("vehicles", {"external_id": "v1", "route_id": "route-c2"})
    storage.insert("routes", {"external_id": "route-a", "name": "10-yo'nalish"})
    storage.insert("sms_route_flags", {"route_id": "route-c4", "enabled": 0})

    backfill_company_ids(storage, PROFILES, ids)

    eco, fer = ids["and-eco-transport-mchj"], ids["ferganatex"]
    trips = {r["route_id"]: r["company_id"] for r in storage.query(
        "SELECT route_id, company_id FROM trips")}
    assert trips["route-c1"] == eco
    assert trips["route-a"] == fer
    assert trips["route-unknown"] == 0  # noma'lum — tegilmaydi
    assert storage.find("vehicles", external_id="v1")["company_id"] == eco
    assert storage.find("routes", external_id="route-a")["company_id"] == fer
    assert storage.find("sms_route_flags", route_id="route-c4")["company_id"] == eco


def test_backfill_driver_linked_tables(storage):
    ids = seed_companies(storage, PROFILES)
    eco, asl = ids["and-eco-transport-mchj"], ids["asl-sunday-mchj"]
    storage.insert("drivers", {"external_id": "d1", "full_name": "Ali",
                               "route_id": "route-c3"})
    storage.insert("drivers", {"external_id": "d2", "full_name": "Vali",
                               "route_id": "route-b"})
    storage.insert("driver_profiles", {"driver_id": "d1"})
    storage.insert("driver_work_logs", {"date": "2026-10-01", "driver_id": "d1"})
    storage.insert("driver_fines", {"driver_id": "d1", "date": "2026-10-01",
                                    "amount": 15000})
    storage.insert("driver_appeals", {"driver_id": "d2", "title": "Murojaat"})
    storage.insert("avans", {"driver_id": "d1", "amount": 100000})
    storage.insert("driver_fines", {"driver_id": "d-unknown", "date": "2026-10-01",
                                    "amount": 1})

    backfill_company_ids(storage, PROFILES, ids)

    assert storage.find("driver_profiles", driver_id="d1")["company_id"] == eco
    assert storage.find("driver_work_logs", driver_id="d1")["company_id"] == eco
    assert storage.find("driver_fines", driver_id="d1")["company_id"] == eco
    assert storage.find("driver_appeals", driver_id="d2")["company_id"] == asl
    assert storage.find("avans", driver_id="d1")["company_id"] == eco
    unknown = storage.query(
        "SELECT company_id FROM driver_fines WHERE driver_id='d-unknown'")
    assert unknown[0]["company_id"] == 0


def test_backfill_name_tables_and_admin_untouched(storage):
    ids = seed_companies(storage, PROFILES)
    fer, asl, eco = (ids["ferganatex"], ids["asl-sunday-mchj"],
                     ids["and-eco-transport-mchj"])
    storage.insert("profiles", {"name": "AND ECO TRANSPORT MCHJ",
                                "route_id": "route-c1"})
    storage.insert("staff", {"name": "Xodim 1", "company": "Ferganatex"})
    storage.insert("staff", {"name": "Xodim 2", "company": "Noma'lum MCHJ"})
    storage.insert("dashboard_users", {"username": "ali",
                                       "company": "ASL SUNDAY MCHJ"})
    storage.insert("dashboard_users", {"username": "admin", "company": ""})

    backfill_company_ids(storage, PROFILES, ids)

    assert storage.find("profiles", name="AND ECO TRANSPORT MCHJ")["company_id"] == eco
    assert storage.find("staff", name="Xodim 1")["company_id"] == fer  # case-insensitive
    assert storage.find("staff", name="Xodim 2")["company_id"] == 0
    assert storage.find("dashboard_users", username="ali")["company_id"] == asl
    assert storage.find("dashboard_users", username="admin")["company_id"] == 0


def test_bootstrap_idempotent(storage):
    first = bootstrap_tenancy(storage, PROFILES)
    assert first["companies"] == 3
    assert first.get("trips", 0) == 0  # bo'sh bazada yangilanish yo'q

    storage.insert("trips", {"date": "2026-10-01", "route_id": "route-c1"})
    second = bootstrap_tenancy(storage, PROFILES)
    assert second["trips"] == 1

    third = bootstrap_tenancy(storage, PROFILES)
    assert third.get("trips", 0) == 0  # allaqachon bog'langan — qayta yozilmaydi
    n = storage.query("SELECT COUNT(*) AS n FROM companies")[0]["n"]
    assert n == 3


def test_bootstrap_disabled_storage_noop(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "off.db"))
    st = Storage(db=db, enabled=False)
    assert bootstrap_tenancy(st, PROFILES) == {}


# ------------------------------------------------- Storage.enabled wiring

def test_enabled_runs_tenancy_bootstrap_once(monkeypatch):
    import bm_automation.app.db.storage as st_mod
    import bm_automation.app.db.tenancy as tn_mod

    monkeypatch.setattr(st_mod, "init_db", lambda db: True)
    calls: list[bool] = []

    def fake_boot(storage, profiles=None):
        # Bootstrap ichida `enabled` chaqirilsa ham rekursiya bo'lmasin.
        calls.append(storage.enabled)
        return {}

    monkeypatch.setattr(tn_mod, "bootstrap_tenancy", fake_boot)

    class FakePostgres:
        driver = "postgres"
        ph = "%s"
        available = True

    st = st_mod.Storage(db=FakePostgres())
    assert st.enabled is True
    assert st.enabled is True
    assert len(calls) == 1
    assert calls[0] is True


def test_enabled_skips_tenancy_for_sqlite(monkeypatch):
    import bm_automation.app.db.storage as st_mod
    import bm_automation.app.db.tenancy as tn_mod

    monkeypatch.setattr(st_mod, "init_db", lambda db: True)
    calls = []
    monkeypatch.setattr(tn_mod, "bootstrap_tenancy",
                        lambda storage, profiles=None: calls.append(1) or {})

    class FakeSqlite:
        driver = "sqlite"
        ph = "?"
        available = True

    st = st_mod.Storage(db=FakeSqlite())
    assert st.enabled is True
    assert calls == []  # testlarda bootstrap qo'lda chaqiriladi
