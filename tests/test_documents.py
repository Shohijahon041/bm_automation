"""Xujjatlar bo'limi: avto-to'ldirish, haydovchiga bog'lash testlari."""

import pytest

from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.db.storage import storage_for
from bm_automation.app.documents import manager as manager_mod
from bm_automation.app.documents.engine import (
    autofill_field_values, company_context, driver_autofill_context,
)
from bm_automation.app.documents.manager import get_manager
from tests.sqlite_backend import SQLiteDatabase


def _fresh_manager():
    manager_mod._manager = None
    return get_manager()


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    db = SQLiteDatabase(str(tmp_path / "st.db"))
    st = storage_for(db)
    monkeypatch.setattr("bm_automation.app.db.storage.get_storage",
                        lambda: st)
    monkeypatch.setattr(manager_mod, "get_storage", lambda: st)
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE",
                        tmp_path / "profiles.json")
    profiles_mod.save_profiles({
        "active": "XTEST",
        "profiles": [
            {"name": "XTEST", "routeVariantId": "r1",
             "routeName": "10-yo'nalish"},
            {"name": "YTEST", "routeVariantId": "r2",
             "routeName": "20-yo'nalish"},
        ],
    })
    st.save_route(external_id="r1", name="10-yo'nalish", entity_type="ROUTE")
    st.save_route(external_id="r2", name="20-yo'nalish", entity_type="ROUTE")
    st.save_driver("d1", full_name="Ali Valiyev",
                   tin="30101001234567", route_id="r1")
    st.save_driver("d2", full_name="Botir Qosimov", route_id="r2")
    st.save_driver_profile(driver_id="d1", phone="+998901234567",
                           passport_number="AB1234567",
                           license_number="LZ0011223")
    return st


def test_company_context_active_profile(storage, monkeypatch):
    monkeypatch.setenv("COMPANY_STIR", "123456789")
    ctx = company_context()
    assert ctx["name"] == "XTEST"
    assert ctx["stir"] == "123456789"


def test_manager_autofill_data(storage):
    """DocumentManager.autofill_data: /api/documents/autofill javobi —
    `field_values` xaritasi (frontend live-preview uchun)."""
    res = _fresh_manager().autofill_data("d1")
    assert res["ok"] is True
    assert res["driver_id"] == "d1"
    fv = res["field_values"]
    assert fv["haydovchi_fio"] == "Ali Valiyev"
    assert fv["haydovchi_tin"] == "30101001234567"
    assert fv["yo_nishoni"] == "10-yo'nalish"
    # Haydovchisiz so'rov ham ishlaydi (faqat kompaniya/sana).
    base = _fresh_manager().autofill_data("")
    assert base["ok"] is True
    assert "haydovchi_fio" not in base["field_values"]


def test_driver_autofill_context(storage):
    ctx = driver_autofill_context("d1")
    assert ctx["full_name"] == "Ali Valiyev"
    assert ctx["tin"] == "30101001234567"
    assert ctx["phone"] == "+998901234567"
    assert ctx["passport_number"] == "AB1234567"
    assert ctx["route_name"] == "10-yo'nalish"
    assert ctx["company_name"] == "XTEST"
    assert ctx["position"] == "Haydovchi"
    assert ctx["today"]
    # Yo'nalish o'z profil kompaniyasini oladi.
    assert driver_autofill_context("d2")["company_name"] == "YTEST"
    # Bo'sh driver_id — faqat kompaniya/sana (haydovchi bo'limi bo'sh).
    base = driver_autofill_context("")
    assert base["full_name"] == ""
    assert base["company_name"] == "XTEST"
    assert base["today"]


def test_autofill_field_values_mehnat(storage):
    ctx = driver_autofill_context("d1")
    vals = autofill_field_values("mehnat_shartnomasi", ctx)
    assert vals["haydovchi_fio"] == "Ali Valiyev"
    assert vals["haydovchi_tin"] == "30101001234567"
    assert vals["ish_boshlash_sana"] == ctx["today"]
    assert vals["lavozimi"] == "Haydovchi"
    # STIR env'da yo'q — majburiy maydon qoladi, xatolik to'ldirilmaydi.
    assert "tashkilot_stir" not in vals


def test_autofill_field_values_dalolatnoma(storage):
    ctx = driver_autofill_context("d1")
    vals = autofill_field_values("dalolatnoma", ctx)
    assert vals["haydovchi_fio"] == "Ali Valiyev"
    assert vals["dalolatnoma_sana"] == ctx["today"]
    assert vals["yo_nishoni"] == "10-yo'nalish"
    assert vals["lavozimi"] == "Haydovchi"
    # Raqamlar mapping yo'q — qoldiriladi.
    assert "dalolatnoma_raqami" not in vals


def test_create_document_autofill_and_link(storage, monkeypatch):
    monkeypatch.setenv("COMPANY_STIR", "123456789")
    res = _fresh_manager().create_document("mehnat_shartnomasi", {},
                                           driver_id="d1")
    assert res["ok"] is True, res
    docs = get_manager().list_documents()
    assert len(docs) == 1
    doc = docs[0]
    assert doc["driver_id"] == "d1"
    fields = doc["fields"]
    assert fields["haydovchi_fio"] == "Ali Valiyev"
    assert fields["tashkilot_nomi"] == "XTEST"
    assert fields["tashkilot_stir"] == "123456789"
    assert fields["ish_boshlash_sana"]
    assert "Ali Valiyev" in doc["body_text"]
    # Qo'lda berilgan qiymat avto-to'ldirishdan ustun turadi,
    # bo'sh majburiy maydonlar esa haydovchi bo'yicha to'ldiriladi.
    assert get_manager().delete_document(doc["id"])["ok"]
    res2 = get_manager().create_document(
        "mehnat_shartnomasi", {"haydovchi_fio": "Z"},
        driver_id="d1")
    assert res2["ok"] is True, res2
    d2 = get_manager().list_documents()[0]
    assert d2["fields"]["haydovchi_fio"] == "Z"
    assert d2["fields"]["tashkilot_stir"] == "123456789"


def test_update_document_relink_and_autofill(storage, monkeypatch):
    monkeypatch.setenv("COMPANY_STIR", "9")
    mgr = _fresh_manager()
    assert mgr.create_document("mehnat_shartnomasi", {}, driver_id="d1")["ok"]
    doc = mgr.list_documents()[0]
    doc_id = doc["id"]
    assert mgr.update_document(doc_id, fields={}, driver_id="d2")["ok"]
    got = mgr.get_document(doc_id)
    assert got["driver_id"] == "d2"
    assert got["fields"]["haydovchi_fio"] == "Botir Qosimov"
    # Bog'langan haydovchi bo'yicha body qayta ekranga chiqariladi.
    assert "Botir Qosimov" in got["body_text"]
    # Bog'lanish olib tashlansa fields qoladi, driver_id bo'shaydi.
    assert mgr.update_document(doc_id, fields={}, driver_id="")["ok"]
    assert mgr.get_document(doc_id)["driver_id"] == ""


def test_get_manager_singleton(storage):
    manager_mod._manager = None
    a = get_manager()
    b = get_manager()
    assert a is b