"""GPS HTTP API ko'p korxonali qamrov testlari (bm_session cookie).

Dashboard :8080 bilan bir xil qoida: master token (ADMIN) mavjud bo'lsa ham,
sessiya orqali kirgan xodim faqat o'z kompaniyasi qurilmalarini ko'radi;
qurilma/kamera/geofence boshqaruvi esa faqat ADMIN uchun.
"""

import io
import json

import pytest

from bm_automation.app.db.storage import storage_for
from bm_automation.app.gps import api as gps_api
from bm_automation.app.gps import relay, report as greport
from bm_automation.app.gps import storage as gstorage
from bm_automation.app.gps.api import GpsHandler
from tests.sqlite_backend import SQLiteDatabase

_FAKE_DEVICES = [
    {"imei": "111", "vehicle_id": "v1", "name": "A", "online": True,
     "last_speed": 5, "route_id": "r1"},
    {"imei": "222", "vehicle_id": "v2", "name": "B", "online": True,
     "last_speed": 0, "route_id": "r2"},
    {"imei": "333", "vehicle_id": "", "name": "C", "online": False,
     "last_speed": 0, "route_id": ""},
]
_FAKE_CAMERAS = [
    {"id": 1, "name": "k1", "vehicle_id": "v1", "url": "cam1"},
    {"id": 2, "name": "k2", "vehicle_id": "v2", "url": "cam2"},
]
_FAKE_FENCES = [
    {"id": 1, "name": "f1", "vehicle_id": "v1"},
    {"id": 2, "name": "f2", "vehicle_id": "v2"},
]
_FAKE_ALERTS = [
    {"id": 1, "vehicle_id": "v1", "imei": "111"},
    {"id": 2, "vehicle_id": "v2", "imei": "222"},
]
_FAKE_DAILY = [
    {"imei": "111", "date": "d1"},
    {"imei": "222", "date": "d2"},
]
_FAKE_POSITIONS = [
    {"imei": "111", "vehicle_id": "v1", "ts": 1},
    {"imei": "222", "vehicle_id": "v2", "ts": 2},
]
_FAKE_PATHS = [
    {"route_id": "r1", "name": "1-yo'nalish", "tolerance_m": 150,
     "enabled": 1, "points": [[41.0, 69.0], [41.0, 69.01]]},
    {"route_id": "r2", "name": "2-yo'nalish", "tolerance_m": 150,
     "enabled": 1, "points": [[40.0, 69.0], [40.0, 69.01]]},
]

_SCOPE = {"username": "ali", "role": "DIRECTOR", "company": "XTEST",
          "is_admin": False, "routes": ["r1"]}


class _FakeConn:
    def __init__(self, body=b""):
        self._body = body
        self._wbuf = io.BytesIO()

    def makefile(self, mode, *args):
        return io.BytesIO(self._body) if mode.startswith("r") else self._wbuf

    def sendall(self, data):
        self._wbuf.write(data)


def _request(monkeypatch, path, method="GET", headers=None, body=b"",
             scope=None):
    """GpsHandler'ni to'g'ridan-to'g'ri chaqirib (status, json) qaytaradi."""
    monkeypatch.setattr(gps_api, "_maybe_purge", lambda: None)
    monkeypatch.setattr(gps_api, "_user_scope", lambda _h: scope)
    conn = _FakeConn(body)
    h = object.__new__(GpsHandler)
    h.server = type("S", (), {"daemon_threads": True})()
    h.client_address = ("127.0.0.1", 0)
    h.command = method
    h.path = path
    h.requestline = f"{method} {path} HTTP/1.0"
    h.request_version = "HTTP/1.0"
    h.headers = {"Content-Length": str(len(body)), **(headers or {})}
    h.rfile = io.BytesIO(body or b"")
    h.wfile = conn._wbuf
    getattr(h, f"do_{method}")()
    raw = conn._wbuf.getvalue()
    head, _, payload = raw.partition(b"\r\n\r\n")
    status = int(head.split()[1]) if head else 0
    data = json.loads(payload.decode("utf-8")) if payload else {}
    return status, data


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """Master token + SQLite storage (vehicles v1->r1, v2->r2)."""
    monkeypatch.setattr(gps_api.config, "GPS_API_TOKEN", "master")
    db = SQLiteDatabase(str(tmp_path / "gps.db"))
    st = storage_for(db)
    st.save_vehicle(external_id="v1", route_id="r1")
    st.save_vehicle(external_id="v2", route_id="r2")
    st.save_route(external_id="r1", name="1-yo'nalish")
    st.save_route(external_id="r2", name="2-yo'nalish")
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage",
                        lambda: st)
    monkeypatch.setattr(gstorage, "get_storage", lambda: st)
    monkeypatch.setattr(gstorage, "list_devices", lambda: _FAKE_DEVICES)
    monkeypatch.setattr(gstorage, "list_cameras", lambda: _FAKE_CAMERAS)
    monkeypatch.setattr(gstorage, "list_geofences", lambda: _FAKE_FENCES)
    monkeypatch.setattr(gstorage, "list_alerts",
                        lambda limit=50, imei="": _FAKE_ALERTS)
    monkeypatch.setattr(gstorage, "daily",
                        lambda imei="", date_from="", date_to="": _FAKE_DAILY)
    monkeypatch.setattr(
        gstorage, "list_route_paths",
        lambda only_enabled=False: _FAKE_PATHS)
    monkeypatch.setattr(
        gstorage, "positions",
        lambda imei="", vehicle_id="", date_from="", date_to="",
        limit=5000: _FAKE_POSITIONS)
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(relay, "status",
                        lambda: {"1": {"running": True, "pid": 1},
                                 "2": {"running": False, "pid": 0}})
    return st


def _auth():
    return {"Authorization": "Bearer master"}


# --------------------------------------------------------------- qamrov

def test_devices_scoped_to_company(env, monkeypatch):
    """Kompaniya foydalanuvchisi faqat o'z avtomobillarini ko'radi."""
    status, data = _request(monkeypatch, "/api/gps/devices",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["111"]
    # Bog'lanmagan qurilma ham ko'rinmaydi.
    assert "333" not in [d["imei"] for d in data["devices"]]
    # ADMIN (sessiyasiz) hammasini ko'radi.
    status, data = _request(monkeypatch, "/api/gps/devices",
                            headers=_auth(), scope=None)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["111", "222", "333"]


def test_vehicles_list_bound_and_scope(env, monkeypatch):
    """/api/gps/vehicles: mavjud avtobuslar + bog'langan qurilma + qamrov."""
    fake = [
        {"external_id": "v1", "plate_number": "01 A", "garage_number": "1",
         "model": "", "route_id": "r1", "device_imei": "111",
         "device_name": "A"},
        {"external_id": "v2", "plate_number": "02 B", "garage_number": "2",
         "model": "", "route_id": "r2", "device_imei": "", "device_name": ""},
    ]
    monkeypatch.setattr(gstorage, "list_vehicles", lambda: list(fake))

    # ADMIN: barcha avtobuslar, bog'langan qurilma bilan.
    status, data = _request(monkeypatch, "/api/gps/vehicles",
                            headers=_auth(), scope=None)
    assert status == 200
    assert [v["external_id"] for v in data["vehicles"]] == ["v1", "v2"]
    assert data["vehicles"][0]["device_imei"] == "111"
    assert data["vehicles"][1]["device_imei"] == ""

    # Kompaniya qamrovi (faqat r1 yo'nalishi) -> faqat v1 qaytadi.
    status, data = _request(monkeypatch, "/api/gps/vehicles",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert [v["external_id"] for v in data["vehicles"]] == ["v1"]


def test_delete_device_admin(env, monkeypatch):
    """DELETE /api/gps/devices/<imei>: qurilmani ro'yxatdan o'chirish."""
    seen = {}
    monkeypatch.setattr(gstorage, "delete_device",
                        lambda imei: seen.setdefault("imei", imei) or True)
    status, data = _request(monkeypatch, "/api/gps/devices/111",
                            method="DELETE", headers=_auth(), scope=None)
    assert status == 200
    assert data.get("ok") is True
    assert seen.get("imei") == "111"


def test_geofence_edit_updates_fields(env, monkeypatch):
    """PATCH /api/gps/geofences/<id>: nom/koordinata/radius tahrirlanadi."""
    seen = {}

    def _upd(fid, name, lat, lng, radius_m, vehicle_id=""):
        seen.update(fid=fid, name=name, lat=lat, lng=lng,
                    radius_m=radius_m, vehicle_id=vehicle_id)
        return True

    monkeypatch.setattr(gstorage, "update_geofence", _upd)
    body = json.dumps({"name": "Depo", "lat": 41.31, "lng": 69.24,
                       "radius_m": 700, "vehicle_id": "v1"}).encode()
    status, data = _request(monkeypatch, "/api/gps/geofences/1",
                            method="PATCH", headers=_auth(), body=body,
                            scope=None)
    assert status == 200
    assert data.get("ok") is True
    assert seen["fid"] == 1
    assert seen["name"] == "Depo"
    assert seen["lat"] == 41.31 and seen["lng"] == 69.24
    assert seen["radius_m"] == 700
    assert seen["vehicle_id"] == "v1"


def test_positions_scoped_to_company(env, monkeypatch):
    status, data = _request(monkeypatch, "/api/gps/positions",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert [p["imei"] for p in data["positions"]] == ["111"]
    status, data = _request(monkeypatch, "/api/gps/positions",
                            headers=_auth(), scope=None)
    assert [p["imei"] for p in data["positions"]] == ["111", "222"]


def test_cameras_and_relay_scoped(env, monkeypatch):
    status, data = _request(monkeypatch, "/api/video/cameras",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert [c["id"] for c in data["cameras"]] == [1]
    # Relay ro'yxati ham o'z kameralari bilan cheklanadi.
    status, data = _request(monkeypatch, "/api/video/relay",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert set(data["relays"].keys()) == {"1"}


def test_geofences_alerts_daily_scoped(env, monkeypatch):
    status, data = _request(monkeypatch, "/api/gps/geofences",
                            headers=_auth(), scope=_SCOPE)
    assert [f["id"] for f in data["geofences"]] == [1]
    status, data = _request(monkeypatch, "/api/gps/alerts",
                            headers=_auth(), scope=_SCOPE)
    assert [a["id"] for a in data["alerts"]] == [1]
    status, data = _request(monkeypatch, "/api/gps/daily",
                            headers=_auth(), scope=_SCOPE)
    assert [d["imei"] for d in data["daily"]] == ["111"]


def test_hls_camera_allowed(env, monkeypatch):
    """Hls segmenti o'z kamerasiga tegishli bo'lsa ochiladi, aks holda yo'q."""
    h = object.__new__(GpsHandler)
    monkeypatch.setattr(gps_api, "_user_scope", lambda _h: _SCOPE)
    assert h._camera_allowed(1) is True
    assert h._camera_allowed(2) is False
    # Admin uchun har qanday kamera ruxsat etiladi.
    monkeypatch.setattr(gps_api, "_user_scope", lambda _h: None)
    assert h._camera_allowed(2) is True


def test_company_without_routes_fail_closed(env, monkeypatch):
    """Qamrovsiz korxona bo'lsa bo'sh ro'yxat (qiymatlar oqib chiqmaydi)."""
    scope = {"username": "x", "role": "DIRECTOR", "company": "YO'Q",
             "is_admin": False, "routes": []}
    status, data = _request(monkeypatch, "/api/gps/devices",
                            headers=_auth(), scope=scope)
    assert status == 200 and data["devices"] == []


def test_routes_scoped_and_named(env, monkeypatch):
    """/api/gps/routes qurilmalardan yo'nalishlarni nom bilan yig'adi."""
    status, data = _request(monkeypatch, "/api/gps/routes",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["ok"] is True
    # Faqat kompaniya qurilmasi (v1 -> r1) kiradi.
    assert [r["id"] for r in data["routes"]] == ["r1"]
    r = data["routes"][0]
    assert r["name"] == "1-yo'nalish"
    assert r["devices"] == 1 and r["online"] == 1
    # ADMIN (sessiyasiz) hammasini ko'radi (r1, r2) — nom bo'yicha saralangan.
    status, data = _request(monkeypatch, "/api/gps/routes",
                            headers=_auth(), scope=None)
    assert [r["id"] for r in data["routes"]] == ["r1", "r2"]
    assert [r["name"] for r in data["routes"]] == ["1-yo'nalish", "2-yo'nalish"]


def test_devices_route_filter(env, monkeypatch):
    """?route= qurilmalarni yo'nalish bo'yicha filtrlaydi."""
    status, data = _request(monkeypatch, "/api/gps/devices?route=r2",
                            headers=_auth(), scope=None)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["222"]
    status, data = _request(monkeypatch, "/api/gps/devices?route=r1",
                            headers=_auth(), scope=None)
    assert [d["imei"] for d in data["devices"]] == ["111"]
    # Boshqa kompaniya qurilmasi qamrovdan tashqari — bo'sh.
    status, data = _request(monkeypatch, "/api/gps/devices?route=r2",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["devices"] == []


def test_route_paths_scoped(env, monkeypatch):
    """/api/gps/route-paths qamrovga rioya qiladi (faqat o'z yo'nalishi)."""
    status, data = _request(monkeypatch, "/api/gps/route-paths",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert [p["route_id"] for p in data["paths"]] == ["r1"]
    assert data["paths"][0]["points_count"] == 2
    # ADMIN — hammasi.
    status, data = _request(monkeypatch, "/api/gps/route-paths",
                            headers=_auth(), scope=None)
    assert sorted(p["route_id"] for p in data["paths"]) == ["r1", "r2"]


def test_route_paths_admin_write_only(env, monkeypatch):
    """Yo'lak saqlash/toggle/o'chirish faqat ADMIN uchun."""
    body = b'{"route_id":"r1","points":[[41.0,69.0],[41.0,69.01]]}'
    status, _ = _request(monkeypatch, "/api/gps/route-paths", method="POST",
                         headers=_auth(), scope=_SCOPE, body=body)
    assert status == 403
    status, _ = _request(monkeypatch, "/api/gps/route-paths/r1",
                         method="PATCH", headers=_auth(), scope=_SCOPE,
                         body=b'{"enabled":false}')
    assert status == 403
    status, _ = _request(monkeypatch, "/api/gps/route-paths/r1",
                         method="DELETE", headers=_auth(), scope=_SCOPE)
    assert status == 403


def test_route_paths_save_and_learn(env, monkeypatch):
    saved = []
    monkeypatch.setattr(
        gstorage, "upsert_route_path",
        lambda route_id, points, name="", tolerance_m=150:
        saved.append((route_id, len(points), name, tolerance_m)) or True)
    # Qo'lda nuqtalar bilan saqlash (nom route_names'dan olinadi).
    status, data = _request(
        monkeypatch, "/api/gps/route-paths", method="POST", headers=_auth(),
        scope=None, body=b'{"route_id":"r1","points":[[41.0,69.0],[41.0,69.01]]}')
    assert status == 200 and data["ok"] is True and data["points"] == 2
    assert saved[-1] == ("r1", 2, "1-yo'nalish",
                         gps_api.config.ROUTE_DEVIATION_TOLERANCE_M)
    # O'rganish (points yo'q) — route_alert.learn_route_path chaqiriladi.
    monkeypatch.setattr(
        gps_api.route_alert, "learn_route_path",
        lambda route_id, date_from="", date_to="", imei="", simplify_m=None:
        [[41.0, 69.0], [41.0, 69.01], [41.0, 69.02]])
    status, data = _request(
        monkeypatch, "/api/gps/route-paths", method="POST", headers=_auth(),
        scope=None, body=b'{"route_id":"r1","learn":true,"tolerance_m":250}')
    assert status == 200 and data["points"] == 3
    assert saved[-1][3] == 250
    # O'rganish natijasi bo'sh -> 400.
    monkeypatch.setattr(gps_api.route_alert, "learn_route_path",
                        lambda *a, **k: [])
    status, _ = _request(
        monkeypatch, "/api/gps/route-paths", method="POST", headers=_auth(),
        scope=None, body=b'{"route_id":"r2","learn":true}')
    assert status == 400


def test_route_paths_patch_delete(env, monkeypatch):
    toggled, deleted = [], []
    monkeypatch.setattr(gstorage, "set_route_path_enabled",
                        lambda rid, en: toggled.append((rid, en)))
    monkeypatch.setattr(gstorage, "delete_route_path",
                        lambda rid: deleted.append(rid))
    status, _ = _request(monkeypatch, "/api/gps/route-paths/r1",
                         method="PATCH", headers=_auth(), scope=None,
                         body=b'{"enabled":false}')
    assert status == 200 and toggled == [("r1", False)]
    status, _ = _request(monkeypatch, "/api/gps/route-paths/r1",
                         method="DELETE", headers=_auth(), scope=None)
    assert status == 200 and deleted == ["r1"]


# ----------------------------------------------------------- boshqaruv

def test_write_ops_admin_only(env, monkeypatch):
    """Qurilma/kamera/geofence boshqaruvi faqat ADMIN uchun."""
    status, _ = _request(monkeypatch, "/api/gps/devices",
                         method="POST", headers=_auth(), scope=_SCOPE,
                         body=b'{"imei":"999","vehicle_id":"v1"}')
    assert status == 403
    status, _ = _request(monkeypatch, "/api/gps/devices", method="PATCH",
                         headers=_auth(), scope=_SCOPE,
                         body=b'{"enabled":false}')
    assert status == 403
    status, _ = _request(monkeypatch, "/api/video/cameras/1", method="DELETE",
                         headers=_auth(), scope=_SCOPE)
    assert status == 403
    # ADMIN sessiya ham boshqaruvdan foydalana oladi (gate o'tadi).
    admin = {"username": "root", "role": "ADMIN", "company": "",
             "is_admin": True, "routes": None}
    calls = []
    monkeypatch.setattr(gstorage, "register_device",
                        lambda imei: calls.append(("reg", imei)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, _ = _request(monkeypatch, "/api/gps/devices", method="POST",
                         headers=_auth(), scope=admin,
                         body=b'{"imei":"999","vehicle_id":"v1"}')
    assert status == 200
    assert calls == [("reg", "999"), ("bind", "999", "v1", "")]


def test_admin_write_allowed(env, monkeypatch):
    """Master token (sessiyasiz) bilan qurilma ro'yxatga olinadi."""
    calls = []
    monkeypatch.setattr(gstorage, "register_device",
                        lambda imei: calls.append(("reg", imei)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, data = _request(monkeypatch, "/api/gps/devices", method="POST",
                            headers=_auth(),
                            body=b'{"imei":"999","vehicle_id":"v9","name":"X"}',
                            scope=None)
    assert status == 200 and data.get("ok") is True
    assert calls == [("reg", "999"), ("bind", "999", "v9", "X")]


# --------------------------------------------------------------- token

def _patch_session(monkeypatch, valid_tok="sess-tok"):
    monkeypatch.setattr(
        "bm_automation.app.dashboard.deps.session_user",
        lambda tok: _SCOPE if tok == valid_tok else None)


def test_master_bearer_and_query_token(env, monkeypatch):
    status, _ = _request(monkeypatch, "/api/gps/devices",
                         headers=_auth(), scope=None)
    assert status == 200
    status, _ = _request(monkeypatch, "/api/gps/devices?token=master",
                         headers={}, scope=None)
    assert status == 200


def test_empty_bearer_with_session_cookie(env, monkeypatch):
    """Dashboard `bmFetch` bo'sh `Bearer ` yuboradi — sessiya cookie ishlashi kerak."""
    _patch_session(monkeypatch)
    status, data = _request(
        monkeypatch, "/api/gps/devices",
        headers={"Authorization": "Bearer ", "Cookie": "bm_session=sess-tok"},
        scope=_SCOPE)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["111"]


def test_empty_bearer_does_not_block_query_token(env, monkeypatch):
    """Bo'sh Bearer ?token= ni bloklamasligi kerak (eski kod erta False qaytarardi)."""
    _patch_session(monkeypatch, valid_tok="boshqa")
    status, _ = _request(
        monkeypatch, "/api/gps/devices?token=master",
        headers={"Authorization": "Bearer "}, scope=None)
    assert status == 200


def test_invalid_session_rejected(env, monkeypatch):
    _patch_session(monkeypatch)
    status, data = _request(
        monkeypatch, "/api/gps/devices",
        headers={"Authorization": "Bearer ", "Cookie": "bm_session=bad"},
        scope=None)
    assert status == 401
    assert "Bearer" in data["error"]


def test_no_auth_rejected(env, monkeypatch):
    _patch_session(monkeypatch, valid_tok="boshqa")
    status, _ = _request(monkeypatch, "/api/gps/devices", headers={},
                         scope=None)
    assert status == 401

# ------------------------------------------------- stops / vs-manual / send

def test_stops_scoped_and_served(env, monkeypatch):
    """To'xtashlar endpointi qamrovga rioya qiladi, natijaga plate qo'shiladi."""
    calls = []

    def fake_stops_for(imei, date_from="", date_to="", min_s=None,
                       radius_m=None):
        calls.append(imei)
        return [{"start": "2026-10-07T07:00:00+00:00",
                 "end": "2026-10-07T07:20:00+00:00", "duration_s": 1200,
                 "lat": 41.3, "lng": 69.2, "points": 5, "max_speed": 0,
                 "ignition_off": True}]

    monkeypatch.setattr(gps_api.stops, "stops_for", fake_stops_for)
    # Boshqa kompaniya qurilmasi — hech narsa chaqirilmaydi (fail-closed).
    status, data = _request(monkeypatch, "/api/gps/stops?imei=222",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["count"] == 0 and calls == []
    # O'z qurilmasi — natija keladi va kontekst maydonlari qo'shiladi.
    status, data = _request(monkeypatch, "/api/gps/stops?imei=111",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["count"] == 1
    assert calls == ["111"]
    s = data["stops"][0]
    assert s["imei"] == "111" and s["vehicle_id"] == "v1"
    assert s["duration_s"] == 1200
    # Admin (sessiyasiz) boshqa qurilmani ham ko'radi.
    status, data = _request(monkeypatch, "/api/gps/stops?imei=222",
                            headers=_auth(), scope=None)
    assert status == 200 and data["count"] == 1
    assert calls[-1] == "222"


def test_vs_manual_scope_and_defaults(env, monkeypatch):
    seen = {}

    def fake_compare(date_from, date_to, scope_vehicles=None):
        seen["args"] = (date_from, date_to, scope_vehicles)
        return [{"day": date_from, "vehicle_id": "v1", "gps_km": 100.0,
                 "manual_km": 120.0, "diff_km": 20.0, "diff_pct": 20.0}]

    monkeypatch.setattr(gps_api.compare, "daily_comparison", fake_compare)
    status, data = _request(
        monkeypatch, "/api/gps/vs-manual?from=2026-10-01&to=2026-10-07",
        headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["count"] == 1
    assert seen["args"] == ("2026-10-01", "2026-10-07", {"v1"})
    # ADMIN: qamrovsiz (None).
    _request(monkeypatch, "/api/gps/vs-manual?from=2026-10-01&to=2026-10-07",
             headers=_auth(), scope=None)
    assert seen["args"] == ("2026-10-01", "2026-10-07", None)
    # Default: to = bugun, from = 6 kun oldin.
    status, data = _request(monkeypatch, "/api/gps/vs-manual",
                            headers=_auth(), scope=None)
    assert status == 200 and data["from"] <= data["to"]


def test_daily_send_admin_only(env, monkeypatch):
    """Kunlik hisobotni qo'lda yuborish faqat ADMIN uchun."""
    from datetime import date as _date

    calls = []
    monkeypatch.setattr(greport, "send_daily_report",
                        lambda day=None: (calls.append(day), True)[1])
    # Non-admin — 403, chaqirilmaydi.
    status, _ = _request(monkeypatch, "/api/gps/daily/send", method="POST",
                         headers=_auth(), scope=_SCOPE,
                         body=b'{"day":"2026-10-06"}')
    assert status == 403 and calls == []
    # ADMIN — yuboriladi, sana parse qilinadi.
    status, data = _request(monkeypatch, "/api/gps/daily/send", method="POST",
                            headers=_auth(), scope=None,
                            body=b'{"day":"2026-10-06"}')
    assert status == 200 and data["ok"] is True
    assert calls == [_date(2026, 10, 6)]
    # Noto'g'ri sana — 400.
    status, data = _request(monkeypatch, "/api/gps/daily/send", method="POST",
                            headers=_auth(), scope=None,
                            body=b'{"day":"xx"}')
    assert status == 400 and calls == [_date(2026, 10, 6)]


# ------------------------------------------------- JT1078 jonli oqimlar

def test_jt_streams_endpoint(env, monkeypatch):
    """/api/video/jt-streams: avto-aniqlangan 4G kamera oqimlari."""
    monkeypatch.setattr(gps_api.config, "JT808_ENABLED", True)
    monkeypatch.setattr(gps_api.jt1078, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(gps_api.jt1078, "streams",
                        lambda: [{"sim": "123", "channel": 1, "bytes": 2048,
                                  "hls": "/hls/jt_123_1.m3u8"}])
    monkeypatch.setattr(gps_api.jt808, "connected_phones", lambda: ["123"])
    monkeypatch.setattr(gps_api.jt1078, "recent_streams",
                        lambda: [{"sim": "123", "channel": 1, "active": False,
                                  "last_error": "kamera uzildi"}])
    monkeypatch.setattr(gps_api.config, "JT1078_AUDIO", True)
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=None)
    assert status == 200
    assert data["ok"] is True and data["enabled"] is True
    assert data["ffmpeg"] is True
    assert data["audio_enabled"] is True
    assert data["count"] == 1
    assert data["streams"][0]["hls"] == "/hls/jt_123_1.m3u8"
    assert data["devices"] == ["123"]
    # Diagnostika: faol oqimlar + yaqinda yopilganlar (oxirgi xatosi bilan)
    assert data["recent"][0]["last_error"] == "kamera uzildi"
    assert "audio_rate" in data


def test_jt_streams_disabled(env, monkeypatch):
    """JT808 o'chirilgan bo'lsa bo'sh ro'yxat (xatosiz)."""
    monkeypatch.setattr(gps_api.config, "JT808_ENABLED", False)
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=None)
    assert status == 200
    assert data["enabled"] is False
    assert data["streams"] == [] and data["devices"] == []
    assert data["recent"] == []


def test_jt_live_start_and_stop(env, monkeypatch):
    """POST /api/video/jt-live|jt-stop: 0x9101/0x9102 buyrug'i yuboriladi."""
    calls = []
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    monkeypatch.setattr(gps_api.config, "JT1078_TCP_PORT", 6107)
    monkeypatch.setattr(gps_api.jt808, "send_live_request",
                        lambda *a, **k: calls.append(("start", a, k)) or True)
    monkeypatch.setattr(gps_api.jt808, "send_live_stop",
                        lambda *a, **k: calls.append(("stop", a, k)) or True)
    body = json.dumps({"phone": "123", "channel": 2}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-live", method="POST",
                            headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert data["host"] == "1.2.3.4" and data["port"] == 6107
    assert calls[-1][0] == "start" and calls[-1][1][0] == "123"
    status, data = _request(monkeypatch, "/api/video/jt-stop", method="POST",
                            headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert calls[-1][0] == "stop" and calls[-1][1] == ("123", 2)


def test_jt_live_requires_admin_and_phone(env, monkeypatch):
    # Non-admin -> 403.
    status, _ = _request(monkeypatch, "/api/video/jt-live", method="POST",
                         headers=_auth(), scope=_SCOPE,
                         body=b'{"phone":"123"}')
    assert status == 403
    # phone yo'q -> 400.
    status, data = _request(monkeypatch, "/api/video/jt-live", method="POST",
                            headers=_auth(), scope=None, body=b'{}')
    assert status == 400
    # media host belgilanmagan -> 400.
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "")
    status, data = _request(monkeypatch, "/api/video/jt-live", method="POST",
                            headers=_auth(), scope=None,
                            body=b'{"phone":"123"}')
    assert status == 400 and "host" in data["error"]


# ------------------------------------------------- relay on-demand (100+ kamera)

def test_relay_start_endpoint(env, monkeypatch):
    """POST /api/video/relay: on-demand relay ko'tariladi (viewer belgilanadi)."""
    calls = []
    monkeypatch.setattr(relay, "resolve_camera",
                        lambda cid: {"id": cid, "url": "rtsp://x",
                                     "source_type": "rtsp", "enabled": 1})
    monkeypatch.setattr(relay, "note_viewer",
                        lambda cid: calls.append(("view", cid)))
    monkeypatch.setattr(relay, "ensure_for_camera",
                        lambda cam: calls.append(("start", cam["id"])) or True)
    monkeypatch.setattr(relay, "public_url",
                        lambda cid: "/hls/cam%d.m3u8" % cid)
    body = json.dumps({"id": 7}).encode()
    status, data = _request(monkeypatch, "/api/video/relay", method="POST",
                            headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert data["hls"] == "/hls/cam7.m3u8"
    assert ("view", 7) in calls and ("start", 7) in calls
    # id yo'q -> 400.
    status, _ = _request(monkeypatch, "/api/video/relay", method="POST",
                         headers=_auth(), scope=None, body=b"{}")
    assert status == 400
    # kamera topilmasa -> 404.
    monkeypatch.setattr(relay, "resolve_camera", lambda cid: None)
    status, _ = _request(monkeypatch, "/api/video/relay", method="POST",
                         headers=_auth(), scope=None, body=body)
    assert status == 404
    # Non-admin -> 403 (yozuv amali).
    status, _ = _request(monkeypatch, "/api/video/relay", method="POST",
                         headers=_auth(), scope=_SCOPE, body=body)
    assert status == 403


def test_relay_stop_endpoint(env, monkeypatch):
    stopped = []
    monkeypatch.setattr(relay, "stop_for_camera",
                        lambda cid: stopped.append(cid))
    status, data = _request(monkeypatch, "/api/video/relay-stop", method="POST",
                            headers=_auth(), scope=None,
                            body=json.dumps({"id": 3}).encode())
    assert status == 200 and data["ok"] is True and stopped == [3]
    status, _ = _request(monkeypatch, "/api/video/relay-stop", method="POST",
                         headers=_auth(), scope=None, body=b"{}")
    assert status == 400


def test_relay_status_reports_limit(env, monkeypatch):
    status, data = _request(monkeypatch, "/api/video/relay",
                            headers=_auth(), scope=None)
    assert status == 200 and "limit" in data

