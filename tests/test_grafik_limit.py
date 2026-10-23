"""Grafik yuborish kunlik limiti (har bir korxona uchun 20 marta)."""

import json
from pathlib import Path

import pytest

from bm_automation.app.notifications.ops import grafik_limit as gl


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(gl, "STATE_FILE", tmp_path / "grafik_limit.json")
    profiles = [
        {"name": "ALPHA", "routeVariantId": "r1,r2"},
        {"name": "BETA", "routeVariantId": "r3"},
    ]
    monkeypatch.setattr(gl, "all_profiles", lambda: profiles)
    return gl


def _counts(gl):
    return json.loads(gl.STATE_FILE.read_text(encoding="utf-8"))["counts"]


def test_multi_route_company_counts_once(env):
    res = env.reserve(["r1", "r2"])
    assert sorted(res["allowed"]) == ["r1", "r2"]
    assert res["blocked"] == []
    assert res["remaining"] == {"ALPHA": gl.DAILY_LIMIT - 1}
    assert _counts(env) == {"ALPHA": 1}


def test_daily_limit_blocks_after_20_requests(env):
    for i in range(gl.DAILY_LIMIT):
        res = env.reserve(["r3"])
        assert res["allowed"] == ["r3"]
        assert res["remaining"] == {"BETA": gl.DAILY_LIMIT - i - 1}
    res = env.reserve(["r3"])
    assert res["allowed"] == []
    assert res["blocked"] == [{"company": "BETA", "count": gl.DAILY_LIMIT,
                               "limit": gl.DAILY_LIMIT}]
    assert _counts(env) == {"BETA": gl.DAILY_LIMIT}


def test_partial_blocking_keeps_other_company(env):
    for _ in range(gl.DAILY_LIMIT):
        env.reserve(["r1", "r2"])
    res = env.reserve(["r1", "r2", "r3"])
    assert res["allowed"] == ["r3"]
    assert [b["company"] for b in res["blocked"]] == ["ALPHA"]
    assert res["remaining"] == {"BETA": gl.DAILY_LIMIT - 1}


def test_day_change_resets_counts(env, monkeypatch):
    monkeypatch.setattr(gl, "_today", lambda: "2026-10-19")
    for _ in range(gl.DAILY_LIMIT):
        env.reserve(["r3"])
    assert env.reserve(["r3"])["allowed"] == []

    monkeypatch.setattr(gl, "_today", lambda: "2026-10-20")
    res = env.reserve(["r3"])
    assert res["allowed"] == ["r3"]
    assert _counts(env) == {"BETA": 1}


def test_all_routes_counts_each_company_once(env, monkeypatch):
    monkeypatch.setattr(gl, "_all_route_ids", lambda: ["r1", "r2", "r3"])
    res = env.reserve(None)
    assert sorted(res["allowed"]) == ["r1", "r2", "r3"]
    assert _counts(env) == {"ALPHA": 1, "BETA": 1}


def test_unknown_route_uses_route_id_as_company(env):
    res = env.reserve(["noma-lum"])
    assert res["allowed"] == ["noma-lum"]
    assert _counts(env) == {"noma-lum": 1}


def test_save_is_atomic_and_cleans_temp(tmp_path):
    from bm_automation.app.exporters import sheet_image

    class _Fig:
        def savefig(self, path, **kwargs):
            Path(path).write_bytes(b"png-bytes")

    out = tmp_path / "sub" / "sheet.png"
    sheet_image._save_fig(_Fig(), str(out))
    assert out.read_bytes() == b"png-bytes"
    assert list(out.parent.iterdir()) == [out]
