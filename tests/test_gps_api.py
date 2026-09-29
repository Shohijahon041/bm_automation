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
from bm_automation.app.gps import relay, storage as gstorage
from bm_automation.app.gps.api import GpsHandler
from tests.sqlite_backend import SQLiteDatabase

_FAKE_DEVICES = [
    {"imei": "111", "vehicle_id": "v1", "name": "A", "online": True,
     "last_speed": 5},
    {"imei": "222", "vehicle_id": "v2", "name": "B", "online": True,
     "last_speed": 0},
    {"imei": "333", "vehicle_id": "", "name": "C", "online": False,
     "last_speed": 0},
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
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage",
                        lambda: st)
    monkeypatch.setattr(gstorage, "list_devices", lambda: _FAKE_DEVICES)
    monkeypatch.setattr(gstorage, "list_cameras", lambda: _FAKE_CAMERAS)
    monkeypatch.setattr(gstorage, "list_geofences", lambda: _FAKE_FENCES)
    monkeypatch.setattr(gstorage, "list_alerts",
                        lambda limit=50, imei="": _FAKE_ALERTS)
    monkeypatch.setattr(gstorage, "daily",
                        lambda imei="", date_from="", date_to="": _FAKE_DAILY)
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