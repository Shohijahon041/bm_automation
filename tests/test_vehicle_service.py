"""Texnik xizmat jurnali, operatsion monitor va majburiy MFA testlari.

Qamrov:
- `core.vehicle_service.due_state` — overdue/due/ok/unknown chegaralari.
- API: qo'shish/ro'yxat/o'chirish, validatsiya, rol (DISPATCHER 403) va
  tenant izolyatsiyasi (boshqa korxona avtobusi 404).
- Monitor: disk chegarasi, DB tekshiruvi, backup yangiligi va alertlar.
- Bot eslatmasi: overdue kunlik, due 3 kunda bir, holat fayli dedupe.
- Majburiy MFA: super-admin API'lari bloklanadi, sozlashdan keyin ochiladi.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone

import pytest

from bm_automation.app.core import vehicle_service as core
from bm_automation.app.dashboard import alerts, deps, monitor, totp
from bm_automation.app.notifications.ops import vehicle_service as remind
from tests.test_security_hardening import senv  # noqa: F401 - pytest fixture


def _client():
    from bm_automation.app.dashboard.fastapi_app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def _mkuser(st, username: str, password: str = "StrongPass1",
            role: str = "ADMIN", company: str = "") -> int:
    digest, salt = deps.hash_password(password)
    row_id = st.dashboard_user_add(username, digest, salt, role=role,
                                   company=company)
    assert row_id
    return row_id


def _session(username: str, row_id: int, role: str, company: str = "") -> str:
    return deps.create_session({"username": username, "id": row_id,
                                "role": role, "company": company})


def _vehicle(st, ext: str, route: str, plate: str = "", model: str = ""):
    st.insert("vehicles", {"external_id": ext, "route_id": route,
                           "plate_number": plate or ext, "model": model})


def _wait(pred, timeout: float = 2.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _collector():
    got: list = []
    lock = threading.Lock()

    def send(text):
        with lock:
            got.append(text)
        return True

    return got, send


# ------------------------------------------------------------ due_state

def test_due_state_states():
    today = date(2026, 11, 4)
    assert core.due_state({}, today=today) == ("unknown", 0, 0)
    overdue = {"next_service_date": "2026-11-01"}
    state, days, _ = core.due_state(overdue, today=today)
    assert state == "overdue" and days == -3
    soon = {"next_service_date": "2026-11-20"}
    assert core.due_state(soon, days_ahead=30, today=today)[0] == "due"
    far = {"next_service_date": "2027-03-01"}
    assert core.due_state(far, days_ahead=30, today=today)[0] == "ok"
    # Km chegarasi sanadan mustaqil ishlaydi.
    km_due = {"next_service_km": 10500, "odometer": 10100}
    assert core.due_state(km_due, km_ahead=500, today=today)[0] == "due"
    km_over = {"next_service_km": 10000, "odometer": 10500}
    assert core.due_state(km_over, km_ahead=500, today=today)[0] == "overdue"


# ------------------------------------------------------------ API + tenant

def test_service_api_crud_scope_and_tenant(senv):  # noqa: F811
    st = senv["st"]
    _vehicle(st, "v-a", "ra1", "01 A 111 AA", "Isuzu")
    _vehicle(st, "v-b", "rb1", "02 B 222 BB")
    client = _client()
    mgr = {"bm_session": _session("svc_mgr", 9101, "MANAGER", "ATEST")}

    payload = {
        "service_date": "2026-10-20",
        "service_type": "Moy va filtrlar",
        "odometer": 154000,
        "cost": 850000,
        "vendor": "Usta Ali",
        "note": "Moy + filtr",
        "next_service_date": "2026-11-01",
        "next_service_km": 160000,
    }
    r = client.post("/api/vehicles/v-a/service", cookies=mgr, json=payload)
    assert r.status_code == 200 and r.json()["ok"] is True

    r = client.get("/api/vehicles/v-a/service", cookies=mgr)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert len(body["logs"]) == 1
    assert body["logs"][0]["service_type"] == "Moy va filtrlar"
    assert body["odometer"] == 154000
    assert "Moy va filtrlar" in body["types"]

    # Muddat o'tgan yozuv upcoming'da ko'rinadi.
    r = client.get("/api/service/upcoming", cookies=mgr)
    items = r.json()["items"]
    assert r.status_code == 200
    assert [x["vehicle_id"] for x in items] == ["v-a"]
    assert items[0]["state"] == "overdue"

    # Validatsiya.
    bad = dict(payload, next_service_date="2026-10-01")
    assert client.post("/api/vehicles/v-a/service", cookies=mgr,
                       json=bad).status_code == 400
    bad = dict(payload, odometer=-5)
    assert client.post("/api/vehicles/v-a/service", cookies=mgr,
                       json=bad).status_code == 400
    bad = dict(payload, service_date="20.10.2026")
    assert client.post("/api/vehicles/v-a/service", cookies=mgr,
                       json=bad).status_code == 400

    # DISPATCHER (rank 1) — 403.
    dsp = {"bm_session": _session("svc_dsp", 9102, "DISPATCHER", "ATEST")}
    assert client.get("/api/service/upcoming", cookies=dsp).status_code == 403
    assert client.get("/api/vehicles/v-a/service", cookies=dsp).status_code == 403

    # Boshqa korxona menejeri o'z avtobusini ko'radi, begonasini emas.
    bmgr = {"bm_session": _session("svc_bmgr", 9103, "MANAGER", "BTEST")}
    assert client.get("/api/vehicles/v-a/service", cookies=bmgr).status_code == 404
    assert client.get("/api/vehicles/v-b/service", cookies=bmgr).status_code == 200

    # O'chirish.
    row_id = body["logs"][0]["id"]
    r = client.delete(f"/api/vehicles/v-a/service/{row_id}", cookies=mgr)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/vehicles/v-a/service", cookies=mgr).json()["logs"] == []
    # Boshqa tenant yozuvni o'chira olmaydi (404).
    assert client.delete(f"/api/vehicles/v-a/service/{row_id}",
                         cookies=bmgr).status_code == 404

    # Admin (super) barcha avtobuslarni ko'radi.
    root = {"bm_session": _session("root", 9104, "ADMIN")}
    assert client.get("/api/vehicles/v-b/service", cookies=root).status_code == 200


# --------------------------------------------------------------- monitor

_Disk = namedtuple("_Disk", "total used free")


def test_monitor_disk_threshold(monkeypatch):
    monkeypatch.setenv("BM_DISK_WARN_PERCENT", "85")
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Disk(100 * 1024 ** 3, 86 * 1024 ** 3,
                                        14 * 1024 ** 3))
    low = monitor.check_disk("/tmp")
    assert low["ok"] is False and low["percent"] == 86.0

    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Disk(100 * 1024 ** 3, 40 * 1024 ** 3,
                                        60 * 1024 ** 3))
    assert monitor.check_disk("/tmp")["ok"] is True


def test_monitor_backup_freshness(tmp_path, monkeypatch):
    monkeypatch.setenv("BM_BACKUP_MAX_AGE_H", "36")
    state = tmp_path / "backup_state.json"
    monkeypatch.setattr(monitor, "_state_file", lambda: state)

    assert monitor.backup_freshness()["skipped"] is True

    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    state.write_text(json.dumps({"last_run": (now - timedelta(hours=10))
                                 .isoformat(), "last_status": "OK"}),
                     encoding="utf-8")
    fresh = monitor.backup_freshness(now=now)
    assert fresh["ok"] is True and fresh["skipped"] is False

    state.write_text(json.dumps({"last_run": (now - timedelta(hours=48))
                                 .isoformat(), "last_status": "OK"}),
                     encoding="utf-8")
    stale = monitor.backup_freshness(now=now)
    assert stale["ok"] is False and stale["age_hours"] == 48.0


def test_monitor_run_checks_alerts(senv, tmp_path, monkeypatch):  # noqa: F811
    alerts.reset_state()
    got, send = _collector()
    alerts.set_sender(send)
    monkeypatch.setenv("BM_DISK_WARN_PERCENT", "85")
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Disk(100 * 1024 ** 3, 90 * 1024 ** 3,
                                        10 * 1024 ** 3))
    state = tmp_path / "backup_state.json"
    state.write_text(json.dumps({
        "last_run": (datetime.now(timezone.utc) - timedelta(hours=72))
        .isoformat(), "last_status": "OK"}), encoding="utf-8")
    monkeypatch.setattr(monitor, "_state_file", lambda: state)

    result = monitor.run_checks(notify=True)
    assert result["disk"]["ok"] is False
    assert result["backup"]["ok"] is False
    assert result["db"]["ok"] is True
    assert _wait(lambda: len(got) == 2)
    text = "\n".join(got)
    assert "disk joyi" in text and "zaxira eskirgan" in text
    alerts.set_sender(None)
    alerts.reset_state()


# ------------------------------------------------------ sekin so'rovlar

def test_slow_query_logged_without_params(tmp_path, caplog):
    from bm_automation.app.db.storage import storage_for
    from tests.sqlite_backend import SQLiteDatabase

    st = storage_for(SQLiteDatabase(str(tmp_path / "slow.db")))
    assert st.enabled
    st._slow_query_ms = 0.001
    with caplog.at_level(logging.WARNING, logger="bm_automation.db"):
        st.query(f"SELECT {st.db.ph} AS secret_value", ("TOPSECRET",))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Sekin so'rov" in text
    assert "TOPSECRET" not in text


# --------------------------------------------------------- bot eslatmasi

def test_reminder_dedupe_and_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(remind, "STATE_FILE", tmp_path / "svc.json")
    remind.reset_state()
    sent: list = []
    remind.set_sender(lambda text: sent.append(text) or True)

    items = [{
        "vehicle_id": "v-a", "plate_number": "01 A 111 AA", "model": "Isuzu",
        "service_type": "Moy va filtrlar", "state": "overdue",
        "next_service_date": "2026-11-01", "next_service_km": 0,
        "days_left": -3, "km_left": 0,
    }]
    monkeypatch.setattr(remind, "collect_due", lambda today=None: items)

    assert remind.check_and_send(today=date(2026, 11, 4)) is True
    assert len(sent) == 1 and "01 A 111 AA" in sent[0]
    # Xuddi shu kuni takror yo'q.
    remind.reset_state()
    assert remind.check_and_send(today=date(2026, 11, 4)) is False
    # Ertasi kun overdue yana eslatiladi.
    remind.reset_state()
    assert remind.check_and_send(today=date(2026, 11, 5)) is True
    assert len(sent) == 2

    # Holat fayli faqat aktiv avtobuslarni saqlaydi.
    data = json.loads((tmp_path / "svc.json").read_text(encoding="utf-8"))
    assert list(data["sent"]) == ["v-a"]
    remind.set_sender(None)


def test_reminder_due_repeats_every_three_days(tmp_path, monkeypatch):
    monkeypatch.setattr(remind, "STATE_FILE", tmp_path / "svc.json")
    due = {"vehicle_id": "v1", "plate_number": "X", "state": "due",
           "next_service_date": "2026-11-20", "days_left": 10, "km_left": 0}
    sent = {"v1": {"last_sent": "2026-11-01"}}
    assert remind._pending([due], sent, date(2026, 11, 3)) == []
    assert remind._pending([due], sent, date(2026, 11, 4)) == [due]
    # Overdue har kuni.
    over = dict(due, state="overdue")
    assert remind._pending([over], sent, date(2026, 11, 2)) == [over]


# ------------------------------------------------------------ majburiy MFA

def test_mfa_setup_required_blocks_api_until_enabled(senv, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DASHBOARD_MFA_REQUIRED", "1")
    st = senv["st"]
    _mkuser(st, "root", role="ADMIN")
    client = _client()

    r = client.post("/api/login", json={"username": "root",
                                        "password": "StrongPass1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/me").json()["mfa_setup_required"] is True
    # Boshqa API'lar yopiq, MFA yo'llari ochiq.
    assert client.get("/api/service/upcoming").status_code == 403
    assert client.get("/api/mfa/status").status_code == 200

    r = client.post("/api/mfa/setup", json={"password": "StrongPass1"})
    assert r.status_code == 200
    secret = r.json()["secret"]
    r = client.post("/api/mfa/enable", json={"code": totp.code_at(secret)})
    assert r.status_code == 200 and r.json()["ok"] is True

    assert client.get("/api/me").json()["mfa_setup_required"] is False
    assert client.get("/api/service/upcoming").status_code == 200


def test_mfa_required_not_applied_to_regular_admin(senv, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DASHBOARD_MFA_REQUIRED", "1")
    st = senv["st"]
    _mkuser(st, "boss", role="ADMIN")
    client = _client()
    r = client.post("/api/login", json={"username": "boss",
                                        "password": "StrongPass1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # Super bo'lmagan ADMIN uchun majburiy sozlash yo'q.
    assert client.get("/api/me").json()["mfa_setup_required"] is False
    assert client.get("/api/service/upcoming").status_code == 200
