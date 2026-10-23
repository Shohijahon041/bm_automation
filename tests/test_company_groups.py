"""Firma -> Telegram guruh bog'lash (profiles.json `company_groups`) testlari."""

import pytest

from bm_automation.app.core import profiles as profiles_mod
from bm_automation.app.notifications.ops import group_departures


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE",
                        tmp_path / "profiles.json")


def test_set_and_get_company_groups(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})

    assert profiles_mod.company_group_ids("A") == []
    ids = profiles_mod.set_company_group_ids("A", ["-1001", "-1002", "-1003"])
    assert ids == ["-1001", "-1002", "-1003"]
    assert profiles_mod.company_group_ids("A") == ["-1001", "-1002", "-1003"]
    # nom katta/kichik harfga bog'liq emas
    assert profiles_mod.company_group_ids("a") == ["-1001", "-1002", "-1003"]


def test_set_groups_filters_invalid_and_dedupes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})
    ids = profiles_mod.set_company_group_ids("A", ["-1001", "-1001", "abc",
                                                   "", "1002", None])
    assert ids == ["-1001", "1002"]


def test_clear_groups(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})
    profiles_mod.set_company_group_ids("A", "-1001")
    assert profiles_mod.company_group_ids("A") == ["-1001"]
    assert profiles_mod.set_company_group_ids("A", "") == []
    assert profiles_mod.company_group_ids("A") == []


def test_company_group_map(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"},
        {"name": "B", "routeVariantId": "rB"}]})
    profiles_mod.set_company_group_ids("A", "-1001")
    profiles_mod.set_company_group_ids("B", "-2002")
    assert profiles_mod.company_group_map() == {
        "A": ["-1001"], "B": ["-2002"]}


def test_add_and_remove_company_group(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})
    assert profiles_mod.add_company_group("A", "-1001") == ["-1001"]
    # takroriy qo'shish — dublikat yo'q
    assert profiles_mod.add_company_group("A", "-1001") == ["-1001"]
    assert profiles_mod.add_company_group("A", "-1002") == ["-1001", "-1002"]
    # barcha firmalardan o'chirish
    assert profiles_mod.remove_company_group("-1001") == 1
    assert profiles_mod.company_group_ids("A") == ["-1002"]


def test_group_rebind_removes_from_other_company(monkeypatch, tmp_path):
    """Bitta guruh ikki firmaga bog'lanmaydi — yangi firma eski bog'lanishni oladi."""
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"},
        {"name": "B", "routeVariantId": "rB"}]})
    profiles_mod.set_company_group_ids("A", ["-1001", "-1002"])
    profiles_mod.set_company_group_ids("B", ["-2002"])

    assert profiles_mod.add_company_group("B", "-1001") == ["-2002", "-1001"]
    assert profiles_mod.company_group_ids("A") == ["-1002"]
    assert profiles_mod.company_group_ids("B") == ["-2002", "-1001"]
    assert profiles_mod.company_group_map() == {
        "A": ["-1002"], "B": ["-2002", "-1001"]}


def test_set_groups_replaces_other_company_binding(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"},
        {"name": "B", "routeVariantId": "rB"}]})
    profiles_mod.set_company_group_ids("A", ["-1001", "-1002"])
    profiles_mod.set_company_group_ids("B", ["-1002", "-3003"])

    assert profiles_mod.company_group_ids("A") == ["-1001"]
    assert profiles_mod.company_group_ids("B") == ["-1002", "-3003"]


def test_add_company_group_unknown_company_raises(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})
    with pytest.raises(ValueError):
        profiles_mod.add_company_group("YO'Q FIRMA", "-1001")
    assert profiles_mod.company_group_map() == {}


def test_delete_profile_cascades_groups(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"},
        {"name": "B", "routeVariantId": "rB"}]})
    profiles_mod.set_company_group_ids("A", "-1001")
    profiles_mod.set_company_group_ids("B", "-1002")
    assert profiles_mod.delete_profile("A") is True
    assert profiles_mod.company_group_ids("A") == []
    assert profiles_mod.company_group_ids("B") == ["-1002"]


def test_profile_route_chats_maps_all_routes_of_company(monkeypatch, tmp_path):
    """Bir xil nomli bir necha yo'nalish — hammasi bitta guruhga."""
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "AND ECO TRANSPORT MCHJ", "profiles": [
        {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "rV1"},
        {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "rV2"},
        {"name": "ASL SUNDAY MCHJ", "routeVariantId": "rB80"},
    ]})
    profiles_mod.set_company_group_ids("AND ECO TRANSPORT MCHJ",
                                       ["-1001", "-1002"])
    profiles_mod.set_company_group_ids("ASL SUNDAY MCHJ", "-1009")

    out = group_departures._profile_route_chats()
    assert out["rV1"] == ["-1001", "-1002"]
    assert out["rV2"] == ["-1001", "-1002"]
    assert out["rB80"] == ["-1009"]
    assert "rOTHER" not in out


def test_targets_for_known_route_does_not_fall_back(monkeypatch, tmp_path):
    """Xaritada yo'q yo'nalish umumiy guruhga oqmaydi (boshqa firma himoyasi)."""
    _isolate(monkeypatch, tmp_path)
    profiles_mod.save_profiles({"active": "AND ECO TRANSPORT MCHJ", "profiles": [
        {"name": "AND ECO TRANSPORT MCHJ", "routeVariantId": "rV1"},
    ]})
    profiles_mod.set_company_group_ids("AND ECO TRANSPORT MCHJ", "-1001")
    monkeypatch.setattr(group_departures, "telegram_settings",
                        lambda: {"driver_chat_id": "-2000", "chat_id": "-2000"})

    assert group_departures._targets_for("rV1") == ["-1001"]
    # B-80 kabi boshqa firma yo'nalishi — bo'sh, -2000 ga tushmaydi.
    assert group_departures._targets_for("rB80") == []
    # Umumiy guruh faqat yo'nalishsiz hodisalar uchun qoladi.
    assert group_departures._targets_for("") == ["-2000"]


def test_chat_enabled_default_on_for_bound_group(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(group_departures, "STATE_FILE", tmp_path / "gd.json")
    profiles_mod.save_profiles({"active": "A", "profiles": [
        {"name": "A", "routeVariantId": "rA"}]})
    profiles_mod.set_company_group_ids("A", "-1001")
    monkeypatch.setattr(group_departures, "enabled", lambda: False)

    # Firmaga bog'langan guruh — global off bo'lsa ham yoqilgan.
    assert group_departures.chat_enabled("-1001") is True
    # Boshqa guruh — global holatga bo'ysunadi.
    assert group_departures.chat_enabled("-9999") is False
    # Aniq /chiqish_off ustun turadi.
    group_departures.set_chat_reminders("-1001", False)
    assert group_departures.chat_enabled("-1001") is False
