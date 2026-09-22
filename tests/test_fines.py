"""Jarimalarni o'chirish (delete_fine) va admin jurnali testlari.

Storage darajasida: `fines_list` / `fines_total` / `delete_fine`.
Admin darajasida: `fines_data` (filterlar) va `fine_remove`.
"""

import pytest

from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase

from bm_automation.app.dashboard import admin_service


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "test-fines.db"))
    return storage_for(db)


def test_add_and_list_fines(storage):
    assert storage.add_driver_fine("d1", "2026-09-01", 50000, "kechikish")
    assert storage.add_driver_fine("d1", "2026-09-05", 30000, "tezlik")
    assert storage.add_driver_fine(
        "d2", "2026-09-02", 10000, "signal", status="CANCELLED")

    rows = storage.fines_list()
    assert len(rows) == 3

    only_d1 = storage.fines_list(driver_id="d1")
    assert len(only_d1) == 2

    month_rows = storage.fines_list(month="2026-09")
    assert len(month_rows) == 3


def test_fines_total(storage):
    storage.add_driver_fine("d1", "2026-09-01", 50000, "kechikish")
    storage.add_driver_fine("d1", "2026-09-05", 30000, "tezlik")
    storage.add_driver_fine("d2", "2026-08-01", 10000, "xato")

    assert storage.fines_total(driver_id="d1") == 80000
    assert storage.fines_total(month="2026-09") == 80000
    assert storage.fines_total(driver_id="d2") == 10000


def test_delete_fine(storage):
    storage.add_driver_fine("d1", "2026-09-01", 50000, "kechikish")
    rows = storage.fines_list()
    assert len(rows) == 1
    fine_id = rows[0]["id"]

    assert storage.delete_fine(fine_id) is True
    assert storage.fines_list() == []

    # bir marta o'chirilgan id yana o'chirilmaydi (false)
    assert storage.delete_fine(fine_id) is False


def test_fines_data_admin(storage):
    storage.add_driver_fine("d1", "2026-09-01", 50000, "kechikish")
    storage.add_driver_fine("d2", "2026-09-02", 30000, "tezlik")

    data = admin_service.fines_data(storage)
    assert data["count"] == 2
    assert data["total"] == 80000

    only_d1 = admin_service.fines_data(storage, driver_id="d1")
    assert only_d1["count"] == 1
    assert only_d1["total"] == 50000

    sept = admin_service.fines_data(storage, month="2026-09")
    assert sept["count"] == 2


def test_fine_remove_ok_and_missing(storage):
    storage.add_driver_fine("d1", "2026-09-01", 50000, "kechikish")
    rid = storage.fines_list()[0]["id"]

    res = admin_service.fine_remove(storage, rid)
    assert res["ok"] is True
    assert storage.fines_list() == []

    res2 = admin_service.fine_remove(storage, rid)
    assert res2["ok"] is False