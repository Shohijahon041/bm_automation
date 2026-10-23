"""Kamera GPS ↔ Teltonika GPS solishtirma testlari (gps/camera.py).

Kamera joyi xarita/nazoratda ishlatilmaydi — faqat yordamchi:
qaysi avtobusda ekanini aniqlash (taklif) va joy farqini (drift) ko'rish.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bm_automation.app.db.storage import storage_for
from bm_automation.app.gps import camera, storage as gstorage
from tests.sqlite_backend import SQLiteDatabase

_DEV_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS gps_devices ("
    "imei TEXT PRIMARY KEY, vehicle_id TEXT DEFAULT '', name TEXT DEFAULT '', "
    "model TEXT DEFAULT 'FMB920', kind TEXT DEFAULT 'teltonika', "
    "last_seen TEXT, last_lat REAL, last_lng REAL, "
    "last_speed INTEGER DEFAULT 0, last_ignition INTEGER DEFAULT 0, "
    "last_satellites INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, "
    "count_in INTEGER DEFAULT 0, count_out INTEGER DEFAULT 0, "
    "count_updated_at TEXT)"
)


@pytest.fixture()
def cam_env(monkeypatch, tmp_path):
    db = SQLiteDatabase(str(tmp_path / "cam.db"))
    st = storage_for(db)
    db.execute(_DEV_SCHEMA)
    monkeypatch.setattr(gstorage, "get_storage", lambda: st)
    monkeypatch.setattr(camera, "get_storage", lambda: st)
    st.save_vehicle(external_id="v1", plate_number="01.240-AAB", route_id="r1")
    st.save_vehicle(external_id="v2", plate_number="01.300-BCD", route_id="r2")
    return st, db


def _dev(db, imei, vehicle_id, lat=None, lng=None, kind="teltonika",
         age_s=0):
    """Qator yozadi; age_s — `last_seen` qancha sekund oldin bo'lsin."""
    seen = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    db.execute(
        "INSERT OR REPLACE INTO gps_devices "
        "(imei, vehicle_id, kind, last_seen, last_lat, last_lng) "
        "VALUES (?,?,?,?,?,?)",
        (imei, vehicle_id, kind, seen.isoformat(), lat, lng),
    )


def test_camera_ok_when_positions_match(cam_env):
    st, db = cam_env
    _dev(db, "111", "v1", 41.3000, 69.2500)                       # Teltonika
    _dev(db, "555", "v1", 41.3001, 69.2501, kind="camera")        # ~13 m
    rows = camera.assist()
    assert len(rows) == 1
    r = rows[0]
    assert r["phone"] == "555"
    assert r["vehicle_id"] == "v1"
    assert r["status"] == "ok"
    assert r["dist_m"] is not None and r["dist_m"] < 100


def test_camera_drift_when_far_from_teltonika(cam_env):
    st, db = cam_env
    _dev(db, "111", "v1", 41.3000, 69.2500)
    _dev(db, "222", "v2", 40.0000, 69.0000)                       # boshqa shahar
    _dev(db, "666", "v2", 41.3000, 69.2500, kind="camera")        # v2'da lekin uzoq
    rows = camera.assist()
    drift = [r for r in rows if r["phone"] == "666"][0]
    assert drift["status"] == "drift"
    assert drift["dist_m"] > 1000
    assert "tekshiring" in drift["status_text"]


def test_camera_suggests_nearest_bus_when_unbound(cam_env):
    st, db = cam_env
    _dev(db, "111", "v1", 41.3000, 69.2500)
    _dev(db, "777", "", 41.3002, 69.2502, kind="camera")          # bog'lanmagan
    rows = camera.assist()
    r = [x for x in rows if x["phone"] == "777"][0]
    assert r["status"] == "suggest"
    assert r["suggest_vehicle_id"] == "v1"
    assert r["suggest_plate"] == "01.240-AAB"
    assert r["suggest_dist_m"] is not None and r["suggest_dist_m"] < 100


def test_camera_without_gps_is_flagged(cam_env):
    st, db = cam_env
    _dev(db, "111", "v1", 41.3, 69.25)
    _dev(db, "888", "v1", None, None, kind="camera")              # joy yo'q
    rows = camera.assist()
    r = [x for x in rows if x["phone"] == "888"][0]
    assert r["status"] == "no_camera_gps"
    assert r["lat"] is None


def test_teltonika_only_filters_cameras():
    rows = [
        {"imei": "111", "kind": "teltonika"},
        {"imei": "555", "kind": "camera"},
        {"imei": "222"},                       # kind yo'q (eski mock) — Teltonika
    ]
    assert [r["imei"] for r in gstorage.teltonika_only(rows)] == ["111", "222"]


def test_list_camera_devices_returns_camera_rows_only(cam_env):
    st, db = cam_env
    _dev(db, "111", "v1", 41.3, 69.25)
    _dev(db, "555", "v1", 41.3, 69.25, kind="camera")
    cams = gstorage.list_camera_devices()
    assert [c["imei"] for c in cams] == ["555"]
    assert cams[0]["kind"] == "camera"


def test_assist_threshold_env_override(cam_env, monkeypatch):
    """Chegara env orqali o'zgaradi (GPS_CAMERA_MATCH_M)."""
    st, db = cam_env
    monkeypatch.setattr(gstorage.config, "GPS_CAMERA_MATCH_M", 5)
    _dev(db, "111", "v1", 41.3000, 69.2500)
    _dev(db, "555", "v1", 41.3100, 69.2500, kind="camera")   # ~1.1 km
    assert camera.assist()[0]["status"] == "drift"
    monkeypatch.setattr(gstorage.config, "GPS_CAMERA_MATCH_M", 5000)
    assert camera.assist()[0]["status"] == "ok"


def test_stale_camera_gps_is_not_compared(cam_env):
    """Kamera joyi eski -> taqqoslanmaydi (soxta "drift" chiqmasin).

    Kamera GPS'ni faqat ulangan sessiyada yuboradi, avtobus esa doim
    harakatda: eski kamera koordinatasini hozirgi Teltonika joyi bilan
    solishtirish har doim katta farq (drift) beradi.
    """
    st, db = cam_env
    _dev(db, "111", "v1", 41.3000, 69.2500)                    # Teltonika
    _dev(db, "555", "v1", 40.0000, 69.0000, kind="camera",
         age_s=gstorage.config.GPS_CAMERA_FRESH_S + 120)
    r = camera.assist()[0]
    assert r["status"] == "stale"
    assert r["dist_m"] is None                # masofa hisoblanmagan
    assert r["tel_last_seen_s"] is not None


def test_teltonika_offline_is_not_compared(cam_env):
    """Teltonika ham eskirgan bo'lsa (avtobus aloqasi yo'q) taqqoslanmaydi."""
    st, db = cam_env
    _dev(db, "111", "v1", 40.0000, 69.0000,
         age_s=gstorage.config.GPS_CAMERA_FRESH_S + 60)
    _dev(db, "555", "v1", 41.3000, 69.2500, kind="camera")
    r = camera.assist()[0]
    assert r["status"] == "teltonika_offline"
    assert r["dist_m"] is None


def test_freshest_teltonika_used_per_vehicle(cam_env):
    """Bir avtobusda 2 Teltonika: o'lik (eski joyli) qurilma ishlatilmaydi."""
    st, db = cam_env
    _dev(db, "111", "v1", 40.0000, 69.0000,
         age_s=gstorage.config.GPS_CAMERA_FRESH_S + 3600)       # o'lik
    _dev(db, "999", "v1", 41.3000, 69.2500)                    # yangi joy
    _dev(db, "555", "v1", 41.3001, 69.2501, kind="camera")
    r = camera.assist()[0]
    assert r["status"] == "ok"
    assert r["dist_m"] is not None and r["dist_m"] < 100


def test_unbound_camera_ignores_stale_teltonika(cam_env):
    """Tavsiya faqat YANGI joyli avtobus uchun beriladi."""
    st, db = cam_env
    _dev(db, "111", "v1", 41.3001, 69.2501,
         age_s=gstorage.config.GPS_CAMERA_FRESH_S + 300)
    _dev(db, "777", "", 41.3000, 69.2500, kind="camera")
    r = camera.assist()[0]
    assert r["status"] == "unbound"
    assert r["suggest_vehicle_id"] == ""


def test_fresh_window_env_override(cam_env, monkeypatch):
    """GPS_CAMERA_FRESH_S=0 -> yosh tekshirilmaydi (eski xatti-harakat)."""
    st, db = cam_env
    monkeypatch.setattr(gstorage.config, "GPS_CAMERA_FRESH_S", 0)
    _dev(db, "111", "v1", 41.3000, 69.2500)
    _dev(db, "555", "v1", 40.0000, 69.0000, kind="camera", age_s=7200)
    assert camera.assist()[0]["status"] == "drift"


def test_list_camera_devices_sets_online(cam_env):
    """Kamera qatorida ham `online`/`last_seen_s` hisoblanishi kerak."""
    st, db = cam_env
    _dev(db, "555", "v1", 41.3, 69.25, kind="camera")
    _dev(db, "666", "v1", 41.3, 69.25, kind="camera",
         age_s=gstorage.config.GPS_ONLINE_WINDOW_S + 60)
    rows = {r["imei"]: r for r in gstorage.list_camera_devices()}
    assert rows["555"]["online"] is True
    assert rows["666"]["online"] is False
    assert rows["555"]["last_seen_s"] != ""
    assert rows["555"]["kind"] == "camera"


def test_migrate_marks_12_digit_imei_as_camera(cam_env):
    """Doimiy migratsiya: 12 xonali IMEI (JT808 kamera) 'camera' bo'ladi,
    15 xonali (Teltonika) tegilmaydi. Idempotent."""
    st, db = cam_env
    _dev(db, "221080328726", "v1", kind="teltonika")        # 12 xona — kamera
    _dev(db, "350424106661230", "v1", kind="teltonika")     # 15 xona — Teltonika
    gstorage._migrate()
    gstorage._migrate()                                     # idempotent
    kinds = {r["imei"]: r["kind"]
             for r in db.query("SELECT imei, kind FROM gps_devices")}
    assert kinds["221080328726"] == "camera"
    assert kinds["350424106661230"] == "teltonika"
