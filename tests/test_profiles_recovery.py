"""profiles.json buzilishi/yo'qolishida tiklanish testlari.

2026-11-07 hodisasi: VDS'da profiles.json o'qilmaydigan holatga tushgach
`load_profiles` jimgina DEFAULT (1 kompaniya) qaytargan, keyingi yozuv esa
6 kompaniyani almashtirib yuborgan. Endi zaxiradan tiklash va buzilgan
nusxani saqlash majburiy.
"""

import json

import pytest

from bm_automation.app.core import profiles as profiles_mod


@pytest.fixture
def prof_file(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles_mod, "PROFILES_FILE", path)
    return path


def _profiles(*names):
    return {"active": names[0], "profiles": [
        {"name": n, "routeVariantId": f"r-{i}", "routeName": n}
        for i, n in enumerate(names)
    ]}


def test_missing_file_restores_latest_backup(prof_file):
    backup = profiles_mod.PROFILES_FILE.with_name(
        profiles_mod.PROFILES_FILE.name + ".bak_20260101_000000")
    backup.write_text(json.dumps(_profiles("A", "B", "C")), encoding="utf-8")

    data = profiles_mod.load_profiles()

    assert [p["name"] for p in data["profiles"]] == ["A", "B", "C"]
    assert prof_file.exists()
    assert [p["name"] for p in json.loads(prof_file.read_text(encoding="utf-8"))["profiles"]] == ["A", "B", "C"]


def test_corrupt_file_restores_backup_and_keeps_corrupt_copy(prof_file):
    backup = profiles_mod.PROFILES_FILE.with_name(
        profiles_mod.PROFILES_FILE.name + ".bak_20260101_000000")
    backup.write_text(json.dumps(_profiles("A", "B")), encoding="utf-8")
    prof_file.write_text("{buzilgan", encoding="utf-8")

    data = profiles_mod.load_profiles()

    assert [p["name"] for p in data["profiles"]] == ["A", "B"]
    assert list(prof_file.parent.glob("profiles.json.corrupt_*"))


def test_corrupt_file_without_backup_uses_last_good_cache(prof_file):
    profiles_mod.save_profiles(_profiles("A", "B"))
    prof_file.write_text("", encoding="utf-8")

    data = profiles_mod.load_profiles()

    assert [p["name"] for p in data["profiles"]] == ["A", "B"]


def test_missing_file_without_backup_does_not_create_defaults(prof_file):
    data = profiles_mod.load_profiles()

    assert data["profiles"] == profiles_mod.DEFAULT_PROFILES["profiles"]
    assert not prof_file.exists()


def test_save_over_corrupt_file_backs_up_last_good_state(prof_file):
    profiles_mod.save_profiles(_profiles("A", "B", "C"))
    prof_file.write_text("buzilgan", encoding="utf-8")

    profiles_mod.save_profiles(_profiles("A"))

    backups = list(prof_file.parent.glob("profiles.json.bak_*"))
    assert backups
    contents = [json.loads(b.read_text(encoding="utf-8")) for b in backups]
    assert any(len(c["profiles"]) == 3 for c in contents)
    assert list(prof_file.parent.glob("profiles.json.corrupt_*"))
