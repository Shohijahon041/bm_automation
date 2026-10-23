"""BM'dan haydovchi to'ldirish (jins / PINFL / guvohnoma / rasm) testlari.

BM API'dagi muhim qoidalar bu yerda mustahkamlanadi:
  * ro'yxatda `pinfl`/`phoneNumber` MASKALANGAN — to'liq qiymat faqat
    `GET /drivers/{id}` (detail) da;
  * rasm `GET /drivers/photo?driverId=` da — faqat `Accept: */*` bilan;
    `Accept: application/json` bilan backend `produces=image/*` route'ini
    tanlamay `/drivers/{id}`ga tushib `400` beradi;
  * `sync_driver_profiles` rasmni faqat hali yuklanmagan bo'lsa oladi.
"""

from __future__ import annotations

import pytest

from bm_automation.app.db.storage import storage_for
from tests.sqlite_backend import SQLiteDatabase

JPG = b"\xff\xd8\xff\xe0" + b"fakejpegdata"
PNG = b"\x89PNG\r\n\x1a\n" + b"fakedata"


@pytest.fixture()
def storage(tmp_path):
    db = SQLiteDatabase(str(tmp_path / "test.db"))
    return storage_for(db)


class _PhotoClient:
    """BM client soxtasi — detail (`get`) + rasm (`download`)."""

    def __init__(self, detail, photo=b""):
        self.detail = detail
        self.photo = photo
        self.get_calls: list[tuple] = []
        self.download_calls: list[dict] = []

    def get(self, path, params=None, **kwargs):
        self.get_calls.append((path, params))
        return self.detail

    def download(self, path, out_file=None, params=None, retries=None,
                 headers=None, **kwargs):
        self.download_calls.append({"path": path, "params": params,
                                    "headers": headers})
        return self.photo


def _detail(**over):
    d = {
        "id": "d1", "fullName": "ALIYEV ALI",
        "gender": "MAN", "pinfl": "31704661370044", "phoneNumber": "+998941063195",
        "licenseNumber": "AF1234567", "category": "A, B, C",
        "licenseExpiryDate": "2030-01-01",
        "vehicles": [{"routeId": "r9"}],
    }
    d.update(over)
    return d


# ------------------------------------------------- refresh_driver_from_bm

def test_refresh_driver_from_bm_fills_all_fields(storage, tmp_path, monkeypatch):
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    storage.save_driver(external_id="d1", full_name="ALIYEV", route_id="r1", data={})
    storage.save_driver_profile("d1", km_rate=7.5)

    client = _PhotoClient(_detail(), photo=JPG)
    out = db_sync.refresh_driver_from_bm(storage, client, "d1")

    assert out["ok"] is True and out["photo"] is True
    profile = storage.find("driver_profiles", driver_id="d1")
    assert profile["gender"] == "MAN"
    assert profile["phone"] == "+998941063195"
    assert profile["license_number"] == "AF1234567"
    assert profile["license_category"] == "A, B, C"
    assert profile["license_expiry"] == "2030-01-01"
    # Mavjud shaxsiy km_rate saqlanib qoladi (sync ustiga yozmaydi).
    assert float(profile["km_rate"]) == 7.5
    # PINFL (JSHSHR) -> drivers.tin
    assert storage.find("drivers", external_id="d1")["tin"] == "31704661370044"
    # Rasm saqlandi va biriktirildi
    assert profile["photo_path"].endswith("_photo.jpg")
    # Accept: */* shart — aks holda API 400 beradi
    assert client.download_calls[0]["headers"]["Accept"] == "*/*"
    assert client.download_calls[0]["params"] == {"driverId": "d1"}


def test_refresh_driver_from_bm_without_photo(storage, tmp_path, monkeypatch):
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    storage.save_driver(external_id="d1", full_name="A", route_id="r1", data={})
    client = _PhotoClient(_detail(gender="WOMAN"), photo=b"")
    out = db_sync.refresh_driver_from_bm(storage, client, "d1")
    assert out["ok"] is True and out["photo"] is False
    profile = storage.find("driver_profiles", driver_id="d1")
    assert profile["gender"] == "WOMAN"
    assert not profile["photo_path"]


def test_refresh_driver_from_bm_not_found(storage):
    from bm_automation.app.api.client import BMApiError
    from bm_automation.app.db import sync as db_sync

    class _Missing:
        def get(self, path, params=None, **kwargs):
            raise BMApiError(404, code="NOT_FOUND", message="yo'q")

        def download(self, *a, **k):
            raise AssertionError("rasm so'ralmasligi kerak")

    out = db_sync.refresh_driver_from_bm(storage, _Missing(), "nope")
    assert out["ok"] is False
    assert out["error"]


def test_save_driver_photo_bytes_detects_format(tmp_path, monkeypatch):
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    assert db_sync.save_driver_photo_bytes("d/1", JPG).endswith("_photo.jpg")
    assert db_sync.save_driver_photo_bytes("d1", PNG).endswith("_photo.png")
    # Rasm emas — saqlanmaydi
    assert db_sync.save_driver_photo_bytes("d1", b"notanimage") == ""


# --------------------------------------------------- sync_driver_profiles

def test_sync_driver_profiles_fills_gender_and_photo(storage, tmp_path, monkeypatch):
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    storage.save_driver(
        external_id="drv1", full_name="ALIYEV", route_id="r1",
        data={"id": "drv1", "fullName": "ALIYEV"})

    client = _PhotoClient(_detail(id="drv1"), photo=JPG)
    r = db_sync.sync_driver_profiles(storage, client, "r1", "2026-08-10", force=True)
    assert not r.error
    p = storage.find("driver_profiles", driver_id="drv1")
    assert p["gender"] == "MAN"
    assert p["photo_path"].endswith("_photo.jpg")


def test_sync_driver_profiles_skips_existing_photo(storage, tmp_path, monkeypatch):
    """Rasm allaqachon bo'lsa qayta yuklanmaydi (og'ir so'rov tejash)."""
    from bm_automation.app.db import sync as db_sync
    monkeypatch.setattr(db_sync, "PHOTO_DIR", tmp_path / "photos")
    storage.save_driver(
        external_id="drv1", full_name="ALIYEV", route_id="r1",
        data={"id": "drv1", "licenseNumber": "X", "category": "B",
              "phoneNumber": "+99890", "pinfl": "325"})
    storage.save_driver_profile("drv1", phone="+99890", license_number="X",
                                photo_path="data/driver_photos/existing.jpg")

    class _Complete:
        def get(self, *a, **k):
            raise AssertionError("detail so'ralmasligi kerak")

        def download(self, *a, **k):
            raise AssertionError("rasm qayta yuklanmasligi kerak")

    r = db_sync.sync_driver_profiles(storage, _Complete(), "r1", "2026-08-10")
    assert not r.error
    p = storage.find("driver_profiles", driver_id="drv1")
    assert p["photo_path"] == "data/driver_photos/existing.jpg"
    assert p["phone"] == "+99890"


# ------------------------------------------------------- DriverRepository

def test_driver_repo_directory_detail_and_photo():
    from bm_automation.app.repositories.driver_repo import DriverRepository

    class _C:
        def list_all(self, path, page_size=100, **kwargs):
            self.list_path = path
            return [{"id": "d1"}, {"id": "d2"}]

        def get(self, path, params=None, **kwargs):
            return {"id": "d1", "pinfl": "31704661370044", "phoneNumber": "+99894"}

        def download(self, path, out_file=None, params=None, headers=None,
                     retries=None, **kwargs):
            self.dl = {"path": path, "params": params, "headers": headers}
            return JPG

    c = _C()
    repo = DriverRepository(c)
    assert repo.all_drivers() == [{"id": "d1"}, {"id": "d2"}]
    assert c.list_path.endswith("/drivers")
    assert repo.by_id("d1")["pinfl"] == "31704661370044"
    assert repo.photo_bytes("d1") == JPG
    assert c.dl["headers"]["Accept"] == "*/*"
    assert c.dl["params"] == {"driverId": "d1"}
