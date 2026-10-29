"""Qo'lda km qaydi, profiles.json himoyasi va kompaniya tokenlari testlari."""

import json

import pytest

from bm_automation.app.db import storage as storage_mod
from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase


@pytest.fixture
def seeded_storage(monkeypatch, tmp_path):
    """SQLite'ga urug'langan Storage'ni singleton sifatida o'rnatadi."""
    from bm_automation.app.dashboard import deps

    deps.invalidate_super_cache()
    st = storage_for(SQLiteDatabase(str(tmp_path / "bm_km.db")))
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    st.save_vehicle(external_id="v1", plate_number="01A123AA", route_id="r1")
    st.save_driver(external_id="d1", full_name="Aliyev Aliy", route_id="r1")
    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", st)
    yield st
    monkeypatch.setattr(storage_mod, "_NULL_STORAGE", None)


# ---------------------------------------------------------------------------
# Qo'lda km: AVTO qator ustidan yozilsa ham qo'lda qayd bo'lib qoladi
# ---------------------------------------------------------------------------

def test_manual_work_log_keeps_manual_note(seeded_storage):
    """Bo'sh izoh bilan kiritilgan km `AVTO` bo'lib qolmaydi.

    Hodisa: foydalanuvchi 400 km kiritdi, urilgan kalitda sayt hisobi
    (AVTO) qatori bor edi — upsert izohni AVTO qilib qoldirgan va km
    oylikka qo'shilmagan.
    """
    from bm_automation.app.dashboard.routers import drivers as drv

    st = seeded_storage
    st.save_driver_work_log("2026-09-19", "d1", "v1", 232.4, 16, "AVTO",
                            distance_plan=232.4, trip_plan=16, working_day=1)
    res = drv._save_work_log("d1", {"date": "2026-09-19", "vehicle_id": "v1",
                                    "distance_km": 400, "trip_count": 16,
                                    "note": ""})
    assert res["ok"] is True
    row = st.query("SELECT date, vehicle_id, distance_km, trip_count, note,"
                   " working_day FROM driver_work_logs WHERE driver_id = 'd1'"
                   " AND date = '2026-09-19'")[0]
    assert row["note"] == drv.MANUAL_KM_NOTE != "AVTO"
    assert float(row["distance_km"]) == 400.0
    assert int(row["working_day"]) == 1          # reja ko'rsatkichi saqlandi


def test_manual_work_log_note_avto_is_prefixed(seeded_storage):
    """Izohga aynan \"AVTO\" yozilsa ham qo'lda belgisi saqlanadi."""
    from bm_automation.app.dashboard.routers import drivers as drv

    drv._save_work_log("d1", {"date": "2026-09-20", "vehicle_id": "v1",
                              "distance_km": 400, "trip_count": 0,
                              "note": "avto"})
    row = seeded_storage.query(
        "SELECT note FROM driver_work_logs WHERE driver_id = 'd1'"
        " AND date = '2026-09-20'")[0]
    assert row["note"].upper() != "AVTO"
    assert row["note"].startswith(drv.MANUAL_KM_NOTE)


def test_manual_km_reaches_salary_totals(seeded_storage):
    """Qo'lda km umumiy km'ga va oylik brutto'siga qo'shiladi."""
    from bm_automation.app.dashboard import metrics as m
    from bm_automation.app.dashboard.routers import drivers as drv

    st = seeded_storage
    st.save_driver_profile("d1", km_rate=1000)
    st.save_driver_work_log("2026-09-19", "d1", "v1", 232.4, 16, "AVTO")
    drv._save_work_log("d1", {"date": "2026-09-19", "vehicle_id": "v1",
                              "distance_km": 400, "trip_count": 16})
    rows = m.Metrics().drivers({"from": "2026-09-19", "to": "2026-09-19"})
    by_id = {r["driver_id"]: r for r in rows}
    d1 = by_id["d1"]
    assert d1["km"] == pytest.approx(400.0)
    assert d1["gross_pay"] > 0


# ---------------------------------------------------------------------------
# profiles.json: keskin qisqarishda nusxa + ogohlantirish
# ---------------------------------------------------------------------------

def _write_profiles(path, routes):
    data = {"active": routes[0][0] if routes else "",
            "profiles": [{"name": n, "routeVariantId": r} for n, r in routes]}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_save_profiles_backs_up_on_sharp_shrink(tmp_path, monkeypatch, caplog):
    from bm_automation.app.core import profiles as P

    f = tmp_path / "profiles.json"
    monkeypatch.setattr(P, "PROFILES_FILE", f)
    _write_profiles(f, [("A", "r1"), ("B", "r2"), ("C", "r3")])

    with caplog.at_level("WARNING"):
        P.save_profiles({"active": "A", "profiles": [{"name": "A",
                                                     "routeVariantId": "r1"}]})
    backups = list(tmp_path.glob("profiles.json.bak_*"))
    assert backups, "keskin qisqarishda nusxa olinmadi"
    saved = json.loads(backups[0].read_text(encoding="utf-8"))
    assert len(saved["profiles"]) == 3
    assert "keskin qisqarmoqda" in caplog.text


def test_save_profiles_no_backup_on_small_change(tmp_path, monkeypatch):
    from bm_automation.app.core import profiles as P

    f = tmp_path / "profiles.json"
    monkeypatch.setattr(P, "PROFILES_FILE", f)
    _write_profiles(f, [("A", "r1"), ("B", "r2"), ("C", "r3")])

    P.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "r1"},
        {"name": "B", "routeVariantId": "r2"},
        {"name": "C", "routeVariantId": "r3"},
        {"name": "D", "routeVariantId": "r4"}]})
    assert not list(tmp_path.glob("profiles.json.bak_*"))


def test_save_profiles_yields_when_lost_is_single_route(tmp_path, monkeypatch):
    """Bitta yo'nalishni o'chirish odatiy amal — nusxa shart emas."""
    from bm_automation.app.core import profiles as P

    f = tmp_path / "profiles.json"
    monkeypatch.setattr(P, "PROFILES_FILE", f)
    _write_profiles(f, [("A", "r1"), ("A", "r2"), ("B", "r3")])

    P.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "r1"},
        {"name": "B", "routeVariantId": "r3"}]})
    assert not list(tmp_path.glob("profiles.json.bak_*"))


# ---------------------------------------------------------------------------
# Kompaniya tokenlari: har biri o'z faylida
# ---------------------------------------------------------------------------

def test_company_tokens_are_separate(tmp_path, monkeypatch):
    from bm_automation.app.core import tokens as T

    monkeypatch.setattr(T, "TOKENS_DIR", tmp_path / "tokens")
    a = T.company_tokens_path("ASL SUNDAY MCHJ")
    b = T.company_tokens_path("FERGANATEX")
    assert a != b
    assert a.parent == b.parent == tmp_path / "tokens"


def test_browser_relogin_writes_company_token(tmp_path, monkeypatch):
    """Kompaniya clienti brauzer loginida asosiy tokens.json'ni buzmaydi."""
    from bm_automation.app.api import client as C

    captured = {}

    def fake_login(**kwargs):
        captured.update(kwargs)
        return {"access_token": "x"}

    import importlib

    bl_mod = importlib.import_module("bm_automation.app.auth.browser_login")
    monkeypatch.setattr(bl_mod, "browser_login", fake_login)
    monkeypatch.setattr("bm_automation.app.config.settings.browser_login_enabled",
                        lambda: True)
    cl = C.BMClient(token_path=str(tmp_path / "ASL SUNDAY MCHJ.json"))
    cl._browser_relogin()
    assert captured.get("token_path") == str(tmp_path / "ASL SUNDAY MCHJ.json")
