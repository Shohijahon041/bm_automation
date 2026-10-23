"""JSHSHR (PINFL) unikal haydovchi identifikatsiyasi testlari.

Qoidalar:
  * JSHSHR shaxsning barqaror unikal identifikatori — pasport yoki
    guvohnoma almashsa ham o'zgarmaydi, shuning uchun bitta JSHSHR → bitta
    `drivers` qatori;
  * BM ba'zan bir shaxsni ikki id ostida qaytaradi (turli yo'nalishlar) —
    bunday qatorlar eng boy (eng ko'p bog'liq ma'lumotga ega) qator ustida
    birlashtiriladi;
  * BM ro'yxat (list) payload'ida PINFL maskalanadi → `tin` hech qachon
    bo'sh qiymat bilan o'chirilmaydi (clobber).
"""

from __future__ import annotations

import pytest

from bm_automation.app.db.driver_identity import (
    TIN_UNIQUE_INDEX, driver_aliases, ensure_drivers_tin_unique,
    merge_drivers_by_alias, merge_drivers_by_tin)
from bm_automation.app.db.storage import Storage, storage_for
from tests.sqlite_backend import SQLiteDatabase

TIN = "31704661370044"


@pytest.fixture()
def storage(tmp_path):
    return storage_for(SQLiteDatabase(str(tmp_path / "tin.db")))


def _raw_storage(tmp_path):
    """Indeks o'chirilgan Storage — testlar uchun dublikat yaratish."""
    return Storage(db=SQLiteDatabase(str(tmp_path / "raw.db")), enabled=True)


# ------------------------------------------------------------- save_driver

def test_save_driver_merges_same_tin_into_one_row(storage):
    storage.save_driver(external_id="a", tin=TIN, full_name="ALIYEV",
                        route_id="r1", data={})
    storage.save_driver(external_id="b", tin=TIN, full_name="ALIYEV",
                        route_id="r2", data={})
    rows = storage.query("SELECT external_id, tin FROM drivers")
    assert len(rows) == 1, rows
    assert rows[0]["external_id"] == "a"          # birinchi (kanonik) qoladi
    # Ikkinchi BM id alias sifatida saqlanadi
    assert "b" in driver_aliases(storage.find("drivers", external_id="a"))


def test_save_driver_keeps_tin_when_upstream_has_none(storage):
    """List payload'ida `tin` yo'q — mavjud JSHSHR o'chirilmasin."""
    storage.save_driver(external_id="a", tin=TIN, full_name="A",
                        route_id="r1", data={})
    storage.save_driver(external_id="a", full_name="A", route_id="r1",
                        data={"id": "a"})
    assert storage.find("drivers", external_id="a")["tin"] == TIN


def test_save_driver_keeps_full_name_when_upstream_empty(storage):
    storage.save_driver(external_id="a", full_name="ALIYEV", route_id="r1",
                        data={})
    storage.save_driver(external_id="a", full_name="", route_id="", data={})
    row = storage.find("drivers", external_id="a")
    assert row["full_name"] == "ALIYEV"


def test_different_tin_stays_separate(storage):
    storage.save_driver(external_id="a", tin=TIN, data={})
    storage.save_driver(external_id="b", tin="30112925160013", data={})
    assert len(storage.query("SELECT external_id FROM drivers")) == 2


# -------------------------------------------------------- canonical_driver_id

def test_canonical_driver_id_resolves_by_tin(storage):
    storage.save_driver(external_id="a", tin=TIN, data={})
    assert storage.canonical_driver_id("b", TIN) == "a"


def test_canonical_driver_id_resolves_alias(storage):
    storage.save_driver(external_id="a", tin=TIN, data={})
    storage.save_driver_alias("a", "b")
    assert storage.canonical_driver_id("b") == "a"


def test_canonical_driver_id_unknown_passthrough(storage):
    assert storage.canonical_driver_id("zz") == "zz"


# ------------------------------------------------------------ merge_by_tin

def test_merge_drivers_by_tin_prefers_richest(tmp_path):
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "a", "full_name": "A", "tin": TIN, "route_id": "r1",
        "data": "{}"})
    st.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "B", "tin": TIN, "route_id": "r2",
        "data": "{}"})
    # b boyroq: telefoni + rasmi bor
    st.upsert("driver_profiles", ["driver_id"], {
        "driver_id": "b", "phone": "+998901112233",
        "photo_path": "data/driver_photos/b_photo.jpg", "rating": 5.0,
        "blacklisted": 0, "km_rate": 0.0, "notification_enabled": 0})

    report = merge_drivers_by_tin(st)
    assert report["groups"] == 1 and report["merged_rows"] == 1
    rows = st.query("SELECT external_id FROM drivers")
    assert [r["external_id"] for r in rows] == ["b"]


def test_merge_drivers_by_tin_repoints_references(tmp_path):
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "a", "full_name": "A", "tin": TIN, "route_id": "r1",
        "data": "{}"})
    st.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "B", "tin": TIN, "route_id": "r2",
        "data": "{}"})
    st.upsert("driver_work_logs", ["date", "driver_id", "vehicle_id"], {
        "date": "2026-10-16", "driver_id": "a", "vehicle_id": "v1",
        "distance_km": 0.0, "trip_count": 0, "note": ""})
    st.upsert("driver_profiles", ["driver_id"], {
        "driver_id": "b", "phone": "+998901112233",
        "photo_path": "p.jpg", "rating": 5.0, "blacklisted": 0, "km_rate": 0.0,
        "notification_enabled": 0})

    merge_drivers_by_tin(st)
    logs = st.query("SELECT driver_id FROM driver_work_logs")
    assert [r["driver_id"] for r in logs] == ["b"]
    assert not st.find("drivers", external_id="a")


def test_merge_drivers_by_tin_is_idempotent(tmp_path):
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "a", "full_name": "A", "tin": TIN, "route_id": "r1",
        "data": "{}"})
    st.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "B", "tin": TIN, "route_id": "r2",
        "data": "{}"})
    first = merge_drivers_by_tin(st)
    second = merge_drivers_by_tin(st)
    assert first["merged_rows"] == 1
    assert second["merged_rows"] == 0
    assert len(st.query("SELECT external_id FROM drivers")) == 1


def test_merge_ignores_empty_tin(tmp_path):
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "a", "full_name": "A", "tin": "", "route_id": "r1",
        "data": "{}"})
    st.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "B", "tin": "", "route_id": "r2",
        "data": "{}"})
    report = merge_drivers_by_tin(st)
    assert report["groups"] == 0
    assert len(st.query("SELECT external_id FROM drivers")) == 2


# -------------------------------------------------------- unique index

def test_ensure_tin_unique_creates_index(storage):
    storage.save_driver(external_id="a", tin=TIN, data={})
    assert ensure_drivers_tin_unique(storage) is True
    rows = storage.query(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (TIN_UNIQUE_INDEX,))
    assert rows, "unikal indeks yaratilmadi"


def test_ensure_tin_unique_merges_then_indexes(tmp_path):
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "a", "full_name": "A", "tin": TIN, "route_id": "r1",
        "data": "{}"})
    st.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "B", "tin": TIN, "route_id": "r2",
        "data": "{}"})
    assert ensure_drivers_tin_unique(st) is True
    assert len(st.query("SELECT external_id FROM drivers")) == 1
    rows = st.query(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (TIN_UNIQUE_INDEX,))
    assert rows


# ------------------------------------------------------------ _full_pinfl

def test_full_pinfl_accepts_only_14_digit():
    from bm_automation.app.db.sync import _full_pinfl
    assert _full_pinfl(TIN) == TIN
    assert _full_pinfl(f"{TIN} ") == TIN
    assert _full_pinfl("3***********44") == ""   # maskalangan (list)
    assert _full_pinfl("325") == ""
    assert _full_pinfl("") is ""


def test_sync_drivers_does_not_store_masked_pinfl(tmp_path, monkeypatch):
    """List payload'ida maskalangan `pinfl` → `tin` ga yozilmaydi."""
    from bm_automation.app.db import sync as db_sync

    class _Repo:
        def __init__(self, client):
            pass

        def by_route_variant(self, route_variant_id, date_str, search=""):
            return [{"id": "d1", "fullName": "ALIYEV",
                     "pinfl": "3***********44"}]

    monkeypatch.setattr(db_sync, "DriverRepository", _Repo)
    st = _raw_storage(tmp_path)
    st.upsert("drivers", ["external_id"], {
        "external_id": "d1", "full_name": "ALIYEV", "tin": TIN,
        "route_id": "r1", "data": "{}"})
    r = db_sync.sync_drivers(st, None, "r1", "2026-10-16")
    assert not r.error
    assert st.find("drivers", external_id="d1")["tin"] == TIN


# ---------------------------------------------- refresh canonicalisation

def test_refresh_driver_canonicalizes_by_pinfl(storage, tmp_path, monkeypatch):
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    storage.save_driver(external_id="a", tin=TIN, full_name="A",
                        route_id="r1", data={})

    class _Client:
        def get(self, path, params=None, **kwargs):
            return {"id": "b", "fullName": "A", "pinfl": TIN,
                    "gender": "MAN", "phoneNumber": "+99890"}

        def download(self, *a, **k):
            return b""

    out = db_sync.refresh_driver_from_bm(storage, _Client(), "b",
                                         with_photo=False)
    assert out["ok"] is True
    assert out["driver_id"] == "a"          # kanonik id qaytadi
    assert storage.find("driver_profiles", driver_id="a")
    assert not storage.find("driver_profiles", driver_id="b")
    assert "b" in driver_aliases(storage.find("drivers", external_id="a"))


# --------------------------------------------------- alias canonicalisation

def test_save_driver_maps_alias_to_canonical_row(storage):
    """Maskalangan (tin='') alias id kelganda yangi qator yaratilmaydi."""
    storage.save_driver(external_id="a", tin=TIN, full_name="A"
                        , route_id="r1", data={})
    storage.save_driver(external_id="b", tin=TIN, full_name="A",
                        route_id="r2", data={})          # b -> alias
    # Endi BM ro'yxatidan faqat `b` (tin maskalangan) keladi:
    storage.save_driver(external_id="b", full_name="A", route_id="r2",
                        data={"x": 1})
    rows = storage.query("SELECT external_id, tin FROM drivers")
    assert len(rows) == 1, rows
    assert rows[0]["external_id"] == "a"
    assert rows[0]["tin"] == TIN


def test_merge_drivers_by_alias_folds_recreated_row(storage):
    """Eski kod bilan yaratilgan alias qatori keyinchalik birlashtiriladi."""
    storage.save_driver(external_id="a", tin=TIN, full_name="A",
                        route_id="r1", data={})
    storage.save_driver_alias("a", "b")
    # Eski xatti-harakat: `b` alohida qator bo'lib qayta yaratildi (tin='').
    storage.upsert("drivers", ["external_id"], {
        "external_id": "b", "full_name": "A", "tin": "", "route_id": "r2",
        "data": "{}"})
    assert len(storage.query("SELECT external_id FROM drivers")) == 2
    report = merge_drivers_by_alias(storage)
    assert report["folded"] == 1
    rows = storage.query("SELECT external_id FROM drivers")
    assert [r["external_id"] for r in rows] == ["a"]


def test_save_driver_tin_conflict_falls_back_to_holder(storage, monkeypatch):
    """`uq_drivers_tin` konflikti butun yozuvni buzmaydi — kanonikka qo'shiladi.

    Ikki yagona sinxron tranzaksiyasi bir xil JSHSHR bilan urinishi mumkin:
    konflikt faqat shu qatorni bekor qiladi va ma'lumot yo'qotmaydi.
    """
    storage.save_driver(external_id="a", tin=TIN, full_name="A",
                        route_id="r1", data={})
    real_upsert = storage.upsert

    def fake_upsert(table, keys, vals):
        if table == "drivers" and str(vals.get("external_id")) == "b":
            raise RuntimeError(
                'duplicate key value violates unique constraint "uq_drivers_tin"')
        return real_upsert(table, keys, vals)

    monkeypatch.setattr(storage, "upsert", fake_upsert)
    values = {"external_id": "b", "full_name": "B", "tin": TIN,
              "route_id": "r2", "data": "{}"}
    status = storage._upsert_driver_row(values, tin=TIN, orig="b", eid="b")
    monkeypatch.setattr(storage, "upsert", real_upsert)
    assert status in ("inserted", "updated")
    rows = storage.query("SELECT external_id, tin FROM drivers")
    assert [r["external_id"] for r in rows] == ["a"]
    assert rows[0]["tin"] == TIN
    assert "b" in driver_aliases(storage.find("drivers", external_id="a"))


