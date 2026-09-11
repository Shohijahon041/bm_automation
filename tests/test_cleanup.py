"""Eski fayl/yozuvlarni aylanma tozalash (`app/utils/cleanup`) testlari."""

import json
import os
from pathlib import Path

import pytest

from bm_automation.app.utils import cleanup


def _touch(path: Path, age_days: float = 0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if age_days:
        ts = path.stat().st_mtime - age_days * 86400
        os.utime(path, (ts, ts))
    return path


@pytest.fixture()
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cleanup, "EXPORTS_STORE", tmp_path / "reports" / "_exports_pending.json")
    return tmp_path / "reports"


def test_prune_reports_keeps_recent_and_store(reports):
    recent = _touch(reports / "today.xlsx")
    _touch(reports / "old.png", age_days=40)
    store = _touch(reports / "_exports_pending.json", age_days=90)

    assert cleanup.prune_reports() == 1
    assert recent.exists()
    assert store.exists()  # xizmat fayli o'chirilmaydi
    assert not (reports / "old.png").exists()


def test_prune_reports_dry_run(reports):
    _touch(reports / "old.png", age_days=40)
    assert cleanup.prune_reports(dry_run=True) == 1
    assert (reports / "old.png").exists()


def test_prune_reports_empty_dir_removed(reports):
    _touch(reports / "sub" / "old.png", age_days=40)
    assert cleanup.prune_reports() == 1
    assert not (reports / "sub").exists()


def test_prune_pending_exports(reports):
    fresh = _touch(reports / "fresh.xlsx")
    missing = "reports/yuq_missing.xlsx"
    old = _touch(reports / "old.xlsx", age_days=10)
    cleanup.EXPORTS_STORE.write_text(json.dumps({
        "exp1": {"path": str(fresh), "caption": "yangi"},
        "exp2": {"path": missing, "caption": "fayl yo'q"},
        "exp3": {"path": str(old), "caption": "eski"},
    }), encoding="utf-8")

    assert cleanup.prune_pending_exports() == 2  # exp2 + exp3
    data = json.loads(cleanup.EXPORTS_STORE.read_text(encoding="utf-8"))
    assert set(data) == {"exp1"}


def test_prune_pending_exports_missing_file(reports):
    reports.mkdir(parents=True, exist_ok=True)
    cleanup.EXPORTS_STORE.write_text(json.dumps({
        "expA": {"path": "reports/yoq.xlsx", "caption": "x"},
    }), encoding="utf-8")
    assert cleanup.prune_pending_exports() == 1
    assert json.loads(cleanup.EXPORTS_STORE.read_text(encoding="utf-8")) == {}


def test_prune_pending_exports_empty_or_bad(reports):
    reports.mkdir(parents=True, exist_ok=True)
    cleanup.EXPORTS_STORE.write_text("not json", encoding="utf-8")
    assert cleanup.prune_pending_exports() == 0
    cleanup.EXPORTS_STORE.write_text("[]", encoding="utf-8")
    assert cleanup.prune_pending_exports() == 0


def test_run_once_interval_gate(reports):
    _touch(reports / "old.png", age_days=40)
    cleanup._LAST_RUN_AT = 0.0
    msg = cleanup.run_once()
    assert msg and "1 eski hisobot fayli" in msg
    assert not (reports / "old.png").exists()

    # interval cheklovi — ikkinchi chaqiriq hech narsa qilmaydi
    _touch(reports / "old2.png", age_days=40)
    assert cleanup.run_once() == ""
    assert (reports / "old2.png").exists()


# ------------------------------------------------------------------ logs

def test_prune_logs_removes_old_and_keeps_active(reports, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(cleanup, "LOGS_DIR", logs)
    _touch(logs / "old_run.log", age_days=40)
    _touch(logs / "backend2.err", age_days=40)
    _touch(logs / "bm.log", age_days=90)   # faol log — rotatsiya o'zi boshqaradi
    _touch(logs / "keep.csv", age_days=40)  # log kengaytmasi emas
    _touch(logs / "fresh.log")              # yangi

    assert cleanup.prune_logs() == 2  # old_run.log + backend2.err
    assert not (logs / "old_run.log").exists()
    assert not (logs / "backend2.err").exists()
    assert (logs / "bm.log").exists()
    assert (logs / "keep.csv").exists()
    assert (logs / "fresh.log").exists()


def test_prune_logs_dry_run(reports, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setattr(cleanup, "LOGS_DIR", logs)
    _touch(logs / "old.log", age_days=40)
    assert cleanup.prune_logs(dry_run=True) == 1
    assert (logs / "old.log").exists()


def test_prune_logs_missing_dir(reports, tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "LOGS_DIR", tmp_path / "yoq_papka")
    assert cleanup.prune_logs() == 0
