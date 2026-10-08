"""To'xtashlarni aniqlash testlari (gps/stops.py)."""

from datetime import datetime, timedelta, timezone

from bm_automation.app.gps import stops as stops_mod

_T0 = datetime(2026, 10, 7, 6, 0, 0, tzinfo=timezone.utc)


def _pt(minutes, lat=41.30, lng=69.24, speed=0, ignition=1):
    return {
        "ts": (_T0 + timedelta(minutes=minutes)).isoformat(),
        "lat": lat, "lng": lng, "speed": speed, "ignition": ignition,
        "movement": 1 if speed > 3 else 0,
    }


def test_stationary_run_detected():
    pts = [_pt(i) for i in range(5)]  # 4 daqiqa bir joyda
    found = stops_mod.detect_stops(pts)
    assert len(found) == 1
    s = found[0]
    assert s["duration_s"] == 240
    assert s["points"] == 5
    assert abs(s["lat"] - 41.30) < 1e-6 and abs(s["lng"] - 69.24) < 1e-6
    assert s["ignition_off"] is False


def test_short_pause_not_detected():
    pts = [_pt(0), _pt(1)]  # 1 daqiqa — min 2 daqiqadan kam
    assert stops_mod.detect_stops(pts) == []


def test_single_point_and_empty():
    assert stops_mod.detect_stops([]) == []
    assert stops_mod.detect_stops([_pt(0)]) == []


def test_radius_split():
    pts = [_pt(0), _pt(2), _pt(4),
           _pt(6, lat=41.31, lng=69.24, speed=30),  # 1 km uzoqda, harakat
           _pt(8, lat=41.31), _pt(10, lat=41.31), _pt(12, lat=41.31)]
    found = stops_mod.detect_stops(pts)
    assert len(found) == 2
    assert found[0]["duration_s"] == 240
    assert found[0]["points"] == 3
    assert found[1]["duration_s"] == 240


def test_time_gap_splits():
    pts = [_pt(0), _pt(2), _pt(4), _pt(44), _pt(46), _pt(48)]  # 40 daq tanaffus
    found = stops_mod.detect_stops(pts)
    assert len(found) == 2
    assert all(s["duration_s"] == 240 for s in found)


def test_spike_within_radius_keeps_stop():
    """GPS tezlik shovqini (bitta noto'g'ri tez nuqta) to'xtashni yo'qotmasin."""
    pts = [_pt(0), _pt(2), _pt(4, speed=40), _pt(6), _pt(8), _pt(10)]
    found = stops_mod.detect_stops(pts)
    assert len(found) == 1
    assert found[0]["points"] == 5  # shovqin nuqtasi sanalmaydi
    assert found[0]["duration_s"] == 600


def test_excursion_outside_closes_run():
    pts = [_pt(0), _pt(2), _pt(4),
           _pt(6, lat=41.32, speed=60),  # uzoqqa ketdi
           _pt(10), _pt(12), _pt(14)]
    found = stops_mod.detect_stops(pts)
    assert len(found) == 2
    assert found[0]["duration_s"] == 240
    assert found[1]["duration_s"] == 240


def test_ignition_off_flag():
    pts = [_pt(0, ignition=0), _pt(3, ignition=0), _pt(6, ignition=0)]
    found = stops_mod.detect_stops(pts)
    assert found and found[0]["ignition_off"] is True


def test_custom_min_duration():
    pts = [_pt(0), _pt(1), _pt(2)]
    assert stops_mod.detect_stops(pts, min_duration_s=60)  # 2 daqiqa >= 60s
    assert stops_mod.detect_stops(pts, min_duration_s=600) == []


def test_naive_and_bad_rows_skipped():
    bad = {"ts": "not-a-date", "lat": 41.3, "lng": 69.2}
    naive = {"ts": "2026-10-07T06:00:00", "lat": 41.3, "lng": 69.2,
             "speed": 0}
    naive2 = {"ts": "2026-10-07T06:05:00", "lat": 41.3, "lng": 69.2,
              "speed": 0}
    found = stops_mod.detect_stops([bad, naive, naive2])
    assert len(found) == 1
    assert found[0]["duration_s"] == 300


def test_stops_for_labels_geofence(monkeypatch):
    """To'xtash markazi geofence ichida bo'lsa — zona nomi yoziladi."""
    pts = [_pt(i) for i in (0, 2, 4)]
    monkeypatch.setattr(stops_mod.storage, "positions",
                        lambda imei="", vehicle_id="", date_from="",
                        date_to="", limit=5000: pts)
    monkeypatch.setattr(stops_mod.storage, "_vehicle_id_for",
                        lambda imei: "v1")
    monkeypatch.setattr(stops_mod.storage, "list_geofences",
                        lambda only_enabled=False: [
                            {"id": 1, "name": "Depo", "lat": 41.30,
                             "lng": 69.24, "radius_m": 300,
                             "vehicle_id": "v1", "enabled": 1},
                            {"id": 2, "name": "Boshqa", "lat": 41.30,
                             "lng": 69.24, "radius_m": 300,
                             "vehicle_id": "v2", "enabled": 1},
                            {"id": 3, "name": "Uzoq", "lat": 40.0,
                             "lng": 60.0, "radius_m": 300,
                             "vehicle_id": "v1", "enabled": 1},
                        ])
    found = stops_mod.stops_for("111")
    assert len(found) == 1
    assert found[0].get("fence") == "Depo"


def test_stops_for_no_fence_no_label(monkeypatch):
    pts = [_pt(i) for i in (0, 2, 4)]
    monkeypatch.setattr(stops_mod.storage, "positions",
                        lambda imei="", vehicle_id="", date_from="",
                        date_to="", limit=5000: pts)
    monkeypatch.setattr(stops_mod.storage, "_vehicle_id_for",
                        lambda imei: "v1")
    monkeypatch.setattr(stops_mod.storage, "list_geofences",
                        lambda only_enabled=False: [])
    found = stops_mod.stops_for("111")
    assert len(found) == 1
    assert "fence" not in found[0]
