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
    monkeypatch.setattr(gstorage, "list_camera_devices", lambda: [
        {"imei": "123", "vehicle_id": "v1", "plate_number": "01 A"},
        {"imei": "456", "vehicle_id": "v2", "plate_number": "02 B"},
    ])
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
    assert "url" not in data["cameras"][0]
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


def test_jt_hls_scoped_to_company(env, monkeypatch):
    monkeypatch.setattr(
        GpsHandler, "_serve_hls",
        lambda self, fname: self._json({"ok": True, "file": fname}),
    )
    status, data = _request(monkeypatch, "/hls/jt_123_1.m3u8",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200 and data["ok"] is True
    status, _ = _request(monkeypatch, "/hls/jt_456_1.m3u8",
                         headers=_auth(), scope=_SCOPE)
    assert status == 404
    status, _ = _request(monkeypatch, "/hls/jtpb_456_1.m3u8",
                         headers=_auth(), scope=_SCOPE)
    assert status == 404


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
                        lambda imei, kind="teltonika":
                        calls.append(("reg", imei, kind)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, _ = _request(monkeypatch, "/api/gps/devices", method="POST",
                         headers=_auth(), scope=admin,
                         body=b'{"imei":"999","vehicle_id":"v1"}')
    assert status == 200
    assert calls == [("reg", "999", "teltonika"), ("bind", "999", "v1", "")]


def test_admin_write_allowed(env, monkeypatch):
    """Master token (sessiyasiz) bilan qurilma ro'yxatga olinadi."""
    calls = []
    monkeypatch.setattr(gstorage, "register_device",
                        lambda imei, kind="teltonika":
                        calls.append(("reg", imei, kind)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, data = _request(monkeypatch, "/api/gps/devices", method="POST",
                            headers=_auth(),
                            body=b'{"imei":"999","vehicle_id":"v9","name":"X"}',
                            scope=None)
    assert status == 200 and data.get("ok") is True
    assert calls == [("reg", "999", "teltonika"), ("bind", "999", "v9", "X")]


# ------------------------------------------- qurilma turlari (3 turdagi model)

def test_post_device_kind_counter(env, monkeypatch):
    """Avtobusdagi 3-qurilma modeli: sanagich (counter) ro'yxatga olinadi."""
    calls = []
    monkeypatch.setattr(gstorage, "register_device",
                        lambda imei, kind="teltonika":
                        calls.append(("reg", imei, kind)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, data = _request(
        monkeypatch, "/api/gps/devices", method="POST", headers=_auth(),
        body=b'{"imei":"444555666777","kind":"counter","vehicle_id":"v2"}')
    assert status == 200
    assert data.get("kind") == "counter"
    assert calls == [("reg", "444555666777", "counter"),
                     ("bind", "444555666777", "v2", "")]


def test_post_device_kind_camera_autodetect_12_digit(env, monkeypatch):
    """12 xonali IMEI avtomatik 'camera' bo'ladi (JT808 BCD 6 bayt)."""
    calls = []
    monkeypatch.setattr(gstorage, "register_device",
                        lambda imei, kind="teltonika":
                        calls.append(("reg", imei, kind)))
    monkeypatch.setattr(gstorage, "bind_device",
                        lambda imei, vid, name: calls.append(
                            ("bind", imei, vid, name)))
    status, data = _request(
        monkeypatch, "/api/gps/devices", method="POST", headers=_auth(),
        body=b'{"imei":"123456789012","vehicle_id":"v1"}')
    assert status == 200
    assert data.get("kind") == "camera"
    assert calls[0] == ("reg", "123456789012", "camera")


def test_post_device_bad_kind_rejected(env, monkeypatch):
    status, data = _request(
        monkeypatch, "/api/gps/devices", method="POST", headers=_auth(),
        body=b'{"imei":"1234567890123","kind":"satelit"}')
    assert status == 400
    assert "kind" in data.get("error", "")


def test_patch_device_kind(env, monkeypatch):
    """PATCH orqali qurilma turi o'zgartiriladi (mas. GPS -> sanagich)."""
    seen = {}
    monkeypatch.setattr(gstorage, "set_device_kind",
                        lambda imei, kind: seen.setdefault(imei, kind) or True)
    status, data = _request(
        monkeypatch, "/api/gps/devices/111", method="PATCH", headers=_auth(),
        body=b'{"kind":"counter"}')
    assert status == 200
    assert data.get("ok") is True
    assert seen.get("111") == "counter"


def test_post_counter_counts(env, monkeypatch):
    """Sanagich kirish/chiqish hisobi yoziladi (sonlarga o'giriladi)."""
    calls = []
    monkeypatch.setattr(gstorage, "update_counter_counts",
                        lambda imei, i, o: calls.append((imei, i, o)) or True)
    status, data = _request(
        monkeypatch, "/api/gps/counters", method="POST", headers=_auth(),
        body=b'{"imei":"555","in_count":"7","out_count":"2"}')
    assert status == 200 and data.get("ok") is True
    assert calls == [("555", 7, 2)]


def test_post_counter_bad_counts(env, monkeypatch):
    status, data = _request(
        monkeypatch, "/api/gps/counters", method="POST", headers=_auth(),
        body=b'{"imei":"555","in_count":"ko\'p"}')
    assert status == 400


def test_post_counter_requires_imei(env, monkeypatch):
    status, data = _request(
        monkeypatch, "/api/gps/counters", method="POST", headers=_auth(),
        body=b'{"in_count":1,"out_count":0}')
    assert status == 400


def test_get_devices_kind_all_includes_cameras(env, monkeypatch):
    """Boshqaruv dialogi uchun ?kind=all barcha turlarni qaytaradi."""
    mixed = list(_FAKE_DEVICES) + [
        {"imei": "999999999999", "vehicle_id": "v1", "name": "kamera",
         "kind": "camera", "online": True},
        {"imei": "555", "vehicle_id": "", "name": "sanagich",
         "kind": "counter", "online": False},
    ]
    monkeypatch.setattr(gstorage, "list_devices", lambda: list(mixed))
    status, data = _request(monkeypatch, "/api/gps/devices?kind=all",
                            headers=_auth(), scope=None)
    assert status == 200
    imes = [d["imei"] for d in data["devices"]]
    assert "999999999999" in imes and "555" in imes
    # Default so'rov — xarita uchun faqat Teltonika.
    status, data = _request(monkeypatch, "/api/gps/devices",
                            headers=_auth(), scope=None)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["111", "222", "333"]


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
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=None)
    assert status == 200
    assert data["ok"] is True and data["enabled"] is True
    assert data["ffmpeg"] is True
    assert data["count"] == 1
    assert data["streams"][0]["hls"] == "/hls/jt_123_1.m3u8"
    assert data["devices"] == ["123"]
    # Diagnostika: faol oqimlar + yaqinda yopilganlar (oxirgi xatosi bilan)
    assert data["recent"][0]["last_error"] == "kamera uzildi"


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
    """POST /api/video/jt-live|jt-stop: 0x9101/0x9102 + muxer darhol yopiladi."""
    calls = []
    closed = []
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    monkeypatch.setattr(gps_api.config, "JT1078_TCP_PORT", 6107)
    monkeypatch.setattr(gps_api.jt808, "send_live_request",
                        lambda *a, **k: calls.append(("start", a, k)) or True)
    monkeypatch.setattr(gps_api.jt808, "send_live_stop",
                        lambda *a, **k: calls.append(("stop", a, k)) or True)
    monkeypatch.setattr(gps_api.jt1078, "close_stream",
                        lambda sim, ch, mode="live": closed.append((sim, ch, mode)) or True)
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
    # To'xtatish = ffmpeg muxer ham DARHOL yopiladi (idle reaper kutilmaydi).
    assert ("123", 2, "live") in closed
    assert data["closed"] is True


def test_jt_live_uses_role_and_company_scope(env, monkeypatch):
    audits = []
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    monkeypatch.setattr(gps_api.jt808, "send_live_request", lambda *a, **k: True)
    monkeypatch.setattr(gps_api.jt1078, "allow_stream", lambda *a: None)
    monkeypatch.setattr(gstorage, "log_video_audit",
                        lambda *a, **k: audits.append((a, k)))

    viewer = {**_SCOPE, "role": "VIEWER"}
    status, _ = _request(monkeypatch, "/api/video/jt-live", method="POST",
                         headers=_auth(), scope=viewer,
                         body=b'{"phone":"123"}')
    assert status == 403

    dispatcher = {**_SCOPE, "role": "DISPATCHER"}
    status, data = _request(monkeypatch, "/api/video/jt-live", method="POST",
                            headers=_auth(), scope=dispatcher,
                            body=b'{"phone":"123"}')
    assert status == 200 and data["ok"] is True
    assert audits[-1][0][3] == "live_start"
    assert audits[-1][1]["vehicle_id"] == "v1"

    status, _ = _request(monkeypatch, "/api/video/jt-live", method="POST",
                         headers=_auth(), scope=dispatcher,
                         body=b'{"phone":"456"}')
    assert status == 404

    status, _ = _request(monkeypatch, "/api/video/jt-live", method="POST",
                         headers=_auth(), scope=None, body=b'{}')
    assert status == 400
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "")
    status, data = _request(monkeypatch, "/api/video/jt-live", method="POST",
                            headers=_auth(), scope=None,
                            body=b'{"phone":"123"}')
    assert status == 400 and "host" in data["error"]


def test_jt_inputs_are_validated(env, monkeypatch):
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    status, _ = _request(
        monkeypatch, "/api/video/jt-live", method="POST", headers=_auth(),
        scope=None, body=b'{"phone":"123","channel":0,"stream_type":9}',
    )
    assert status == 400
    status, _ = _request(
        monkeypatch, "/api/video/jt-files", method="POST", headers=_auth(),
        scope=None,
        body=b'{"phone":"123","channel":1,"start":"x","end":"y"}',
    )
    assert status == 400
    status, _ = _request(
        monkeypatch, "/api/video/jt-playback-control", method="POST",
        headers=_auth(), scope=None,
        body=b'{"phone":"123","channel":1,"control":9}',
    )
    assert status == 400


def test_jt_files_listing_exposes_progress(env, monkeypatch):
    """GET /api/video/jt-files: total/received/complete bilan ro'yxat holati."""
    info = {
        "at": 123.0, "requested_at": 100.0, "items": [{"channel": 1}],
        "received": 1, "total": 3, "complete": False, "packets": 1,
        "request": {"channel": 0, "start": "2026-10-12T02:00:00",
                    "end": "2026-10-12T03:00:00"},
        "age_s": 23.0, "since_last_s": 1.0,
    }
    monkeypatch.setattr(gps_api.jt808, "resources_info", lambda phone: info)
    status, data = _request(
        monkeypatch, "/api/video/jt-files?phone=123", headers=_auth(),
        scope=None)
    assert status == 200 and data["ok"] is True
    assert data["received"] == 1 and data["total"] == 3
    assert data["complete"] is False and data["packets"] == 1
    assert data["request"]["channel"] == 0


def test_jt_files_request_allows_channel_zero(env, monkeypatch):
    """channel=0 — BARCHA kanallar ro'yxati (avval 1..255 bilan rad etilardi)."""
    calls = []

    def fake_send(phone, channel, start, end, **kw):
        calls.append((phone, channel, kw))
        return True

    monkeypatch.setattr(gps_api.jt808, "send_resource_list", fake_send)
    body = json.dumps({"phone": "123", "channel": 0,
                       "start": "2026-10-12 02:00:00",
                       "end": "2026-10-12 03:00:00"}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-files", method="POST",
                            headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True and data["sent"] is True
    assert calls[-1][0] == "123" and calls[-1][1] == 0
    assert calls[-1][2]["request_start"] == "2026-10-12 02:00:00"
    assert calls[-1][2]["request_end"] == "2026-10-12 03:00:00"


def test_jt_playback_endpoint(env, monkeypatch):
    """POST /api/video/jt-playback: 0x9201 yuboriladi, playback belgilanadi."""
    pb, marked = [], []
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    monkeypatch.setattr(gps_api.config, "JT1078_TCP_PORT", 6107)
    monkeypatch.setattr(gps_api.jt808, "send_playback",
                        lambda *a, **k: pb.append((a, k)) or True)
    monkeypatch.setattr(gps_api.jt1078, "set_playback",
                        lambda *a: marked.append(a))
    body = json.dumps({"phone": "123", "channel": 2,
                       "start": "2026-10-13 15:06:00",
                       "end": "2026-10-13 15:16:00"}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert data["hls"] == "/hls/jtpb_123_2.m3u8"
    assert data["flv"] is None      # pilot OFF → HTTP-FLV yo'q
    assert pb[-1][0][0] == "123" and pb[-1][0][1] == "1.2.3.4"
    assert pb[-1][0][2] == 6107 and pb[-1][0][5] == 2
    # start/end BCD[6] ga aylantirilgan
    assert len(pb[-1][0][3]) == 6 and len(pb[-1][0][4]) == 6
    assert marked[-1] == ("123", 2)


def test_jt_playback_endpoint_pilot_urls(env, monkeypatch):
    """Pilot (ZLMediaKit) YOQILGANDA playback javobidagi manzil ham ZLM'ga
    o'g'riladi — eski qattiq `/hls/jtpb_*` 404 berar edi (pleyer shu sabab
    Xotira oynasida qora ekran ko'rsatardi)."""
    monkeypatch.setattr(gps_api.config, "JT1078_MEDIA_HOST", "1.2.3.4")
    monkeypatch.setattr(gps_api.jt808, "send_playback",
                        lambda *a, **k: True)
    monkeypatch.setattr(gps_api.jt1078, "set_playback", lambda *a: None)
    monkeypatch.setattr(gps_api.config, "ZLMEDIAKIT_RTMP",
                        "rtmp://127.0.0.1:1935/live")
    monkeypatch.setattr(gps_api.config, "ZLMEDIAKIT_HTTP", "https://x/zl")
    body = json.dumps({"phone": "123", "channel": 2,
                       "start": "2026-10-13 15:06:00",
                       "end": "2026-10-13 15:16:00"}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200
    assert data["hls"] == "https://x/zl/live/jtpb_123_2/hls.m3u8"
    assert data["flv"] == "https://x/zl/live/jtpb_123_2.live.flv"


def test_jt_playback_stop_closes_both_muxers(env, monkeypatch):
    """0x9202 stop: jtpb muxer DARHOL yopiladi + jonli muxer ham (qurilma
    buyruqni tinglamasa qolgan kadrlar 'jonli' sifatida qayta ffmpeg
    ochib, karta 'to'xtatilmagan'dek ko'rinardi)."""
    closed = []
    cleared = []
    monkeypatch.setattr(gps_api.jt808, "send_playback_control",
                        lambda *a, **k: True)
    monkeypatch.setattr(gps_api.jt1078, "clear_playback",
                        lambda *a: cleared.append(a))
    monkeypatch.setattr(gps_api.jt1078, "close_stream",
                        lambda sim, ch, mode="live": closed.append((sim, ch, mode)) or True)
    body = json.dumps({"phone": "123", "channel": 4, "control": 2}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback-control",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert ("123", 4, "playback") in closed
    assert ("123", 4, "live") in closed
    assert cleared[-1] == ("123", 4)
    assert data["closed"] is True
    # Pauza (control=1) muxer'ni YOPMASIN — oqim davom etadi.
    closed.clear()
    body = json.dumps({"phone": "123", "channel": 4, "control": 1}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback-control",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and closed == []


def test_jt_playback_control_endpoint(env, monkeypatch):
    """POST /api/video/jt-playback-control: speed va drag(time) uzatiladi."""
    calls, cleared = [], []
    monkeypatch.setattr(gps_api.jt808, "send_playback_control",
                        lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(gps_api.jt1078, "clear_playback",
                        lambda *a: cleared.append(a))
    # drag=5 + vaqt -> time_bcd BCD[6]
    body = json.dumps({"phone": "123", "channel": 1, "control": 5,
                       "time": "2026-10-13 15:10:00"}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback-control",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and data["ok"] is True
    assert calls[-1][0] == "123" and calls[-1][1] == 1 and calls[-1][2] == 5
    assert isinstance(calls[-1][4], bytes) and len(calls[-1][4]) == 6
    # tez oldinga + speed; vaqt yo'q -> time_bcd None
    body = json.dumps({"phone": "123", "channel": 2, "control": 3,
                       "speed": 4}).encode()
    status, data = _request(monkeypatch, "/api/video/jt-playback-control",
                            method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and data["speed"] == 4
    assert calls[-1][2] == 3 and calls[-1][3] == 4 and calls[-1][4] is None
    # stop -> playback rejimi o'chiriladi
    body = json.dumps({"phone": "123", "channel": 1, "control": 2}).encode()
    status, _ = _request(monkeypatch, "/api/video/jt-playback-control",
                         method="POST", headers=_auth(), scope=None, body=body)
    assert status == 200 and cleared and cleared[-1] == ("123", 1)


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
    # VIEWER video amallarini bajara olmaydi.
    viewer = {**_SCOPE, "role": "VIEWER"}
    status, _ = _request(monkeypatch, "/api/video/relay", method="POST",
                         headers=_auth(), scope=viewer, body=body)
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


# ------------------------------------------------- kamera ↔ GPS (yordamchi)

def test_devices_exclude_camera_kind(env, monkeypatch):
    """Kamera (kind='camera') qurilmalari xarita/devices ro'yxatiga chiqmaydi."""
    monkeypatch.setattr(gstorage, "list_devices", lambda: [
        {"imei": "111", "vehicle_id": "v1", "online": True,
         "kind": "teltonika"},
        {"imei": "555", "vehicle_id": "v1", "online": True,
         "kind": "camera"},
        {"imei": "222", "vehicle_id": "v2", "online": True},  # kind yo'q
    ])
    status, data = _request(monkeypatch, "/api/gps/devices",
                            headers=_auth(), scope=None)
    assert status == 200
    assert [d["imei"] for d in data["devices"]] == ["111", "222"]


def test_camera_assist_endpoint_scoped(env, monkeypatch):
    """/api/gps/camera-assist: kamera GPS ↔ Teltonika tahlili, qamrov bilan."""
    monkeypatch.setattr(gps_api.camera, "assist", lambda *a, **k: [
        {"phone": "555", "vehicle_id": "v1", "status": "ok",
         "status_text": "GPS mos", "dist_m": 12.0},
        {"phone": "666", "vehicle_id": "v2", "status": "drift",
         "status_text": "GPS farqi katta", "dist_m": 2500.0},
        {"phone": "777", "vehicle_id": "", "status": "unbound",
         "status_text": "Avtobusga bog'lanmagan", "dist_m": None},
    ])
    status, data = _request(monkeypatch, "/api/gps/camera-assist",
                            headers=_auth(), scope=None)
    assert status == 200
    assert data["count"] == 3
    assert data["threshold_m"] == gps_api.config.GPS_CAMERA_MATCH_M
    # DIRECTOR faqat r1 (v1) — v2 tushib qoladi, bog'lanmaganlar qoladi
    status, data = _request(monkeypatch, "/api/gps/camera-assist",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert sorted(i["phone"] for i in data["items"]) == ["555", "777"]


def test_jt_streams_carries_bindings_and_assist(env, monkeypatch):
    """jt-streams: kamera↔avtobus bog'lanishi, kanal soni va GPS holati."""
    monkeypatch.setattr(gps_api.config, "JT808_ENABLED", True)
    monkeypatch.setattr(gps_api.jt1078, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(gps_api.jt1078, "streams", lambda: [])
    monkeypatch.setattr(gps_api.jt1078, "recent_streams", lambda: [])
    monkeypatch.setattr(gps_api.jt808, "connected_phones", lambda: ["555"])
    monkeypatch.setattr(gstorage, "list_camera_devices", lambda: [
        {"imei": "555", "vehicle_id": "v1", "plate_number": "01.240-AAB",
         "name": ""},
    ])
    monkeypatch.setattr(gps_api.camera, "assist", lambda *a, **k: [
        {"phone": "555", "vehicle_id": "v1", "status": "ok",
         "status_text": "GPS mos"},
    ])
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=None)
    assert status == 200
    assert data["bindings"]["555"]["vehicle_id"] == "v1"
    assert data["bindings"]["555"]["plate"] == "01.240-AAB"
    assert data["camera_channels"] >= 1
    assert data["assist"][0]["status"] == "ok"
    assert data["assist_threshold_m"] == gps_api.config.GPS_CAMERA_MATCH_M


def test_jt_streams_scoped_to_company(env, monkeypatch):
    """Non-admin boshqa korxona kameralari/raqamlarini ko'rmasligi kerak.

    Kamera telefon raqami, avtobus raqami va oqim manzili boshqa
    korxonaga oid ma'lumot — qamrov qo'llanilishi shart.
    """
    monkeypatch.setattr(gps_api.config, "JT808_ENABLED", True)
    monkeypatch.setattr(gps_api.jt1078, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(gps_api.jt1078, "streams", lambda: [
        {"sim": "555", "channel": 1, "bytes": 1,
         "hls": "/hls/jt_555_1.m3u8"},
        {"sim": "666", "channel": 1, "bytes": 1,
         "hls": "/hls/jt_666_1.m3u8"},
    ])
    monkeypatch.setattr(gps_api.jt1078, "recent_streams", lambda: [
        {"sim": "555", "channel": 1, "last_error": "uzildi"},
        {"sim": "666", "channel": 1, "last_error": "uzildi"},
    ])
    monkeypatch.setattr(gps_api.jt808, "connected_phones",
                        lambda: ["555", "666"])
    monkeypatch.setattr(gstorage, "list_camera_devices", lambda: [
        {"imei": "555", "vehicle_id": "v1", "plate_number": "01.240-AAB",
         "name": ""},
        {"imei": "666", "vehicle_id": "v2", "plate_number": "01.300-BCD",
         "name": ""},
    ])
    monkeypatch.setattr(gps_api.camera, "assist", lambda *a, **k: [
        {"phone": "555", "vehicle_id": "v1", "status": "ok",
         "status_text": "GPS mos"},
        {"phone": "666", "vehicle_id": "v2", "status": "ok",
         "status_text": "GPS mos"},
        {"phone": "777", "vehicle_id": "", "status": "unbound",
         "status_text": "Avtobusga bog'lanmagan",
         "suggest_vehicle_id": "v2", "suggest_plate": "01.300-BCD",
         "suggest_dist_m": 12.0},
    ])
    # ADMIN (sessiyasiz) — hammasi ko'rinadi
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=None)
    assert status == 200
    assert set(data["bindings"]) == {"555", "666"}
    assert [s["sim"] for s in data["streams"]] == ["555", "666"]
    assert [s["sim"] for s in data["recent"]] == ["555", "666"]
    assert data["devices"] == ["555", "666"]
    assert len(data["assist"]) == 3
    # DIRECTOR (r1 -> v1): faqat o'z korxonasi; bog'lanmagan kamera qoladi,
    # lekin boshqa korxona avtobusiga tavsiya ko'rsatilmaydi
    status, data = _request(monkeypatch, "/api/video/jt-streams",
                            headers=_auth(), scope=_SCOPE)
    assert status == 200
    assert set(data["bindings"]) == {"555"}
    assert [s["sim"] for s in data["streams"]] == ["555"]
    assert [s["sim"] for s in data["recent"]] == ["555"]
    assert data["devices"] == ["555"]
    assert sorted(a["phone"] for a in data["assist"]) == ["555", "777"]
    lone = [a for a in data["assist"] if a["phone"] == "777"][0]
    assert lone["suggest_plate"] == ""


def test_gps_video_token_matches_dashboard_verifier(env, monkeypatch):
    """GPS API bergan token dashboard verifikatoridan o'tishi kerak.

    Ilgari bu yerda alohida format (4 maydon) va ochiq `"dev-secret"`
    kaliti ishlatilardi: bunday token hech qanday verifikatordan
    o'tmasdi (dashboard 5 maydon kutadi).
    """
    from bm_automation.app.dashboard.routers.admin import _verify_video_token

    class _H:
        headers = {}

    tok = GpsHandler._sign_video_token(_H(), 7, "grid", 300)
    assert len(tok.split("|")) == 5
    assert _verify_video_token(tok) == (7, 0, "grid")

