"""Fayl/yozish yordamchilari testlari (tmp_path)."""

import json

from bm_automation.app.utils.io import atomic_write, ensure_dir, safe_name, save_json


def test_safe_name_keeps_alnum_dash_underscore():
    assert safe_name("FERGANATEX") == "FERGANATEX"
    assert safe_name("10-yo'nalish") == "10-yo_nalish"


def test_safe_name_strips_and_limits():
    assert safe_name("!!!") == "profile"  # bo'sh bo'lsa default
    assert len(safe_name("a" * 100)) <= 40


def test_ensure_dir_creates(tmp_path):
    d = tmp_path / "nested" / "dir"
    assert ensure_dir(d) == d
    assert d.is_dir()


def test_save_json_atomic(tmp_path):
    target = tmp_path / "data.json"
    save_json({"a": 1}, target)
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}


def test_atomic_write(tmp_path):
    target = tmp_path / "f.txt"
    atomic_write(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
