"""Yo'nalish yo'lagi (marshrutdan chiqish) nazorati testlari."""

from bm_automation.app.gps import route_alert
from bm_automation.app.gps import storage as gstorage


# ------------------------------------------------------------ geometriya

def test_point_segment_distance():
    """Nuqta segmentga yaqin/tashqarida to'g'ri masofa beradi."""
    # segment (41.0,69.0)-(41.0,69.01), nuqta segment ustida
    d = route_alert.point_segment_distance_m(41.0, 69.005, 41.0, 69.0,
                                             41.0, 69.01)
    assert d < 5
    # nuqta segmentdan tashqarida — eng yaqin chekka (0.01° lon ≈ 840 m)
    d2 = route_alert.point_segment_distance_m(41.0, 69.02, 41.0, 69.0,
                                              41.0, 69.01)
    assert 780 < d2 < 900


def test_point_to_polyline():
    pts = [[41.0, 69.0], [41.0, 69.01], [41.01, 69.01]]
    # poliliniyaga yaqin nuqta
    assert route_alert.point_to_polyline_m(41.005, 69.01, pts) < 60
    # uzoq nuqta
    assert route_alert.point_to_polyline_m(41.05, 69.05, pts) > 3000


def test_point_to_polyline_single_and_empty():
    assert route_alert.point_to_polyline_m(41.0, 69.0, []) == float("inf")
    assert route_alert.point_to_polyline_m(41.0, 69.0,
                                           [[41.0, 69.0]]) < 1


def test_simplify_path_collinear():
    """To'g'ri chiziqdagi nuqtalar faqat ikki chetga qisqaradi."""
    pts = [[41.0, 69.0 + i * 0.0001] for i in range(10)]
    out = route_alert.simplify_path(pts, 5)
    assert out == [[41.0, 69.0], [41.0, 69.0009]]


def test_simplify_path_keeps_bends():
    pts = [[41.0, 69.0], [41.0, 69.005], [41.02, 69.005], [41.02, 69.01]]
    out = route_alert.simplify_path(pts, 20)
    assert len(out) == 4  # burilishlar saqlanadi


# ------------------------------------------------------------ check_position

def test_check_position_deviation_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(route_alert, "_route_for", lambda imei: "r1")
    monkeypatch.setattr(route_alert, "_last_alert", {})
    monkeypatch.setattr(route_alert.config, "ROUTE_DEVIATION_TELEGRAM", False)
    monkeypatch.setattr(gstorage, "get_route_path", lambda rid: {
        "route_id": "r1", "name": "B-80", "enabled": 1, "tolerance_m": 100,
        "points": [[41.0, 69.0], [41.0, 69.01]],
    })
    monkeypatch.setattr(gstorage, "insert_alert", lambda **kw: calls.append(kw))
    # yo'lakdan uzoq nuqta -> alert
    route_alert.check_position("111", 41.0, 69.1, 30)
    assert len(calls) == 1
    assert calls[0]["kind"] == "route_deviation"
    # debounce: keyingi tez chaqiruv alert qo'shmaydi
    route_alert.check_position("111", 41.0, 69.1, 30)
    assert len(calls) == 1


def test_check_position_inside_no_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(route_alert, "_route_for", lambda imei: "r1")
    monkeypatch.setattr(route_alert, "_last_alert", {})
    monkeypatch.setattr(route_alert.config, "ROUTE_DEVIATION_TELEGRAM", False)
    monkeypatch.setattr(gstorage, "get_route_path", lambda rid: {
        "route_id": "r1", "name": "B-80", "enabled": 1, "tolerance_m": 300,
        "points": [[41.0, 69.0], [41.0, 69.01]],
    })
    monkeypatch.setattr(gstorage, "insert_alert", lambda **kw: calls.append(kw))
    route_alert.check_position("111", 41.0, 69.005, 30)
    assert calls == []


def test_check_position_no_route_or_path(monkeypatch):
    calls = []
    monkeypatch.setattr(route_alert, "_route_for", lambda imei: "")
    monkeypatch.setattr(gstorage, "get_route_path", lambda rid: None)
    monkeypatch.setattr(gstorage, "insert_alert", lambda **kw: calls.append(kw))
    route_alert.check_position("111", 41.0, 69.1, 30)
    assert calls == []


# ------------------------------------------------------------ learn

def test_learn_route_path(monkeypatch):
    monkeypatch.setattr(gstorage, "list_devices", lambda: [
        {"imei": "111", "route_id": "r1"},
        {"imei": "222", "route_id": "r2"},
    ])
    pts = [{"ts": "2026-10-07T07:00:00Z", "lat": 41.0 + i * 0.0001,
            "lng": 69.0} for i in range(10)]
    monkeypatch.setattr(
        gstorage, "positions",
        lambda imei="", date_from="", date_to="", limit=5000:
        (pts if imei == "111" else []))
    out = route_alert.learn_route_path("r1", simplify_m=5)
    assert len(out) >= 2
    assert out[0] == [41.0, 69.0]
    # noma'lum yo'nalish -> bo'sh
    assert route_alert.learn_route_path("yo'q") == []
