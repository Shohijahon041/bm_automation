"""GPS ↔ qo'lda km solishtirma testlari (gps/compare.py, gps/report.py)."""

from datetime import date

import pytest

from bm_automation.app.db.storage import storage_for
from bm_automation.app.gps import compare, report as greport
from tests.sqlite_backend import SQLiteDatabase


@pytest.fixture()
def cmp_env(monkeypatch, tmp_path):
    """SQLite storage + minimal gps jadvallari."""
    db = SQLiteDatabase(str(tmp_path / "cmp.db"))
    st = storage_for(db)
    db.execute(
        "CREATE TABLE IF NOT EXISTS gps_devices ("
        "imei TEXT PRIMARY KEY, vehicle_id TEXT DEFAULT '')")
    db.execute(
        "CREATE TABLE IF NOT EXISTS gps_daily ("
        "imei TEXT, day TEXT, distance_m REAL DEFAULT 0, "
        "points INTEGER DEFAULT 0, PRIMARY KEY (imei, day))")
    monkeypatch.setattr(compare, "get_storage", lambda: st)
    return st, db


def _seed(db, imei, vehicle_id, day, km, points=10):
    db.execute("INSERT OR REPLACE INTO gps_devices (imei, vehicle_id) "
               "VALUES (?, ?)", (imei, vehicle_id))
    db.execute("INSERT OR REPLACE INTO gps_daily (imei, day, distance_m, "
               "points) VALUES (?, ?, ?, ?)", (imei, day, km * 1000.0, points))


def test_daily_comparison_merges_manual(cmp_env):
    st, db = cmp_env
    st.save_vehicle(external_id="v1", plate_number="01.240-AAB")
    _seed(db, "111", "v1", "2026-10-06", 100.0)
    # Qo'lda yozuv + AVTO yozuv (hisobga olinmaydi) + qurilmasiz yozuv
    st.save_driver_work_log("2026-10-06", "d1", "v1", 120.0, 2,
                            note="Qo'lda")
    st.save_driver_work_log("2026-10-06", "d2", "", 30.0, 1, note="")
    st.save_driver_work_log("2026-10-06", "d3", "v1", 999.0, 9, note="AVTO")
    rows = compare.daily_comparison("2026-10-06", "2026-10-06")
    assert len(rows) == 1
    r = rows[0]
    assert r["vehicle_id"] == "v1"
    assert r["plate"] == "01.240-AAB"
    assert r["gps_km"] == 100.0
    assert r["manual_km"] == 120.0  # AVTO va qurilmasiz yozuv qo'shilmadi
    assert r["diff_km"] == 20.0
    assert r["diff_pct"] == 20.0


def test_unbound_device_skipped(cmp_env):
    st, db = cmp_env
    _seed(db, "222", "", "2026-10-06", 50.0)
    assert compare.daily_comparison("2026-10-06", "2026-10-06") == []


def test_scope_filters_by_vehicles(cmp_env):
    st, db = cmp_env
    st.save_vehicle(external_id="v1", plate_number="A")
    st.save_vehicle(external_id="v2", plate_number="B")
    _seed(db, "111", "v1", "2026-10-06", 100.0)
    _seed(db, "222", "v2", "2026-10-06", 50.0)
    rows = compare.daily_comparison("2026-10-06", "2026-10-06",
                                    scope_vehicles={"v1"})
    assert [r["vehicle_id"] for r in rows] == ["v1"]
    rows = compare.daily_comparison("2026-10-06", "2026-10-06",
                                    scope_vehicles=set())
    assert rows == []


def test_manual_without_gps_row(cmp_env):
    """Qo'lda yozuv bor, GPS yig'ama yo'q — alohida qator (GPS 0)."""
    st, db = cmp_env
    st.save_vehicle(external_id="v9", plate_number="09.909-XYZ")
    st.save_driver_work_log("2026-10-05", "d1", "v9", 90.0, 1,
                            note="Qo'lda")
    rows = compare.daily_comparison("2026-10-05", "2026-10-05")
    assert len(rows) == 1
    assert rows[0]["gps_km"] == 0.0
    assert rows[0]["manual_km"] == 90.0
    assert rows[0]["diff_pct"] is None
    assert rows[0]["plate"] == "09.909-XYZ"  # vehicles'dan to'ldirildi


def test_summing_multiple_manual_entries(cmp_env):
    st, db = cmp_env
    st.save_vehicle(external_id="v1")
    _seed(db, "111", "v1", "2026-10-06", 100.0)
    st.save_driver_work_log("2026-10-06", "d1", "v1", 60.0, 1, note="A")
    st.save_driver_work_log("2026-10-06", "d2", "v1", 40.0, 1, note="B")
    rows = compare.daily_comparison("2026-10-06", "2026-10-06")
    assert rows[0]["manual_km"] == 100.0
    assert rows[0]["manual_entries"] == 2


def _row(gps, manual, pct=None, plate="X"):
    diff = manual - gps
    if pct is None and gps > 0.05:
        pct = round(diff / gps * 100.0, 1)
    return {"plate": plate, "vehicle_id": "v", "imei": "i", "gps_km": gps,
            "manual_km": manual, "diff_km": round(diff, 1),
            "diff_pct": pct}


def test_flagged_rows_kinds():
    rows = [
        _row(100.0, 130.0, plate="A"),   # +30% → over
        _row(100.0, 75.0, plate="B"),    # -25% → under
        _row(100.0, 105.0, plate="C"),   # +5% → jim
        _row(0.0, 50.0, plate="D"),      # GPS yo'q
        _row(80.0, 0.0, plate="E"),      # qayd yo'q
        _row(5.0, 0.0, plate="F"),       # ahamiyatsiz
        _row(0.2, 0.1, plate="G"),       # shovqin
    ]
    flags = compare.flagged_rows(rows, tol_pct=20)
    kinds = {r["plate"]: k for r, k in flags}
    assert kinds == {"A": "over", "B": "under", "D": "no_gps",
                     "E": "no_decl"}


def test_control_block_format(monkeypatch):
    rows = [
        _row(100.0, 130.0, plate="A"),
        _row(0.0, 50.0, plate="D"),
        _row(80.0, 0.0, plate="E"),
        _row(100.0, 105.0, plate="C"),
    ]
    monkeypatch.setattr("bm_automation.app.gps.compare.daily_comparison",
                        lambda f, t, scope_vehicles=None: rows)
    monkeypatch.setattr(greport.config, "VS_MANUAL_REPORT", True)
    monkeypatch.setattr(greport.config, "VS_MANUAL_TOLERANCE_PCT", 20)
    text = greport.build_control_block(date(2026, 10, 6))
    assert "Km nazorati" in text
    assert "A" in text and "+30%" in text
    assert "GPS yo'q" in text
    assert "qayd yo'q" in text
    assert "C" not in text  # tolerance ichida — ko'rsatilmaydi


def test_control_block_disabled(monkeypatch):
    monkeypatch.setattr(greport.config, "VS_MANUAL_REPORT", False)
    assert greport.build_control_block(date(2026, 10, 6)) == ""


def test_build_report_appends_block(monkeypatch):
    monkeypatch.setattr(greport.storage, "daily",
                        lambda imei="", date_from="", date_to="": [])
    rows = [_row(100.0, 130.0, plate="A")]
    monkeypatch.setattr("bm_automation.app.gps.compare.daily_comparison",
                        lambda f, t, scope_vehicles=None: rows)
    monkeypatch.setattr(greport.config, "VS_MANUAL_REPORT", True)
    text = greport.build_report(date(2026, 10, 6))
    assert "Kunlik yurish hisoboti" in text
    assert "Ma'lumot yo'q" in text
    assert "Km nazorati" in text
