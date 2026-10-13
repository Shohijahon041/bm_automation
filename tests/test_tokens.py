"""Token fayli va JWT ``exp`` orqali muddat aniqlash testlari.

Token muddati `expires_at` maydoniga taxminiy TTL bilan yozilardi (12 soat),
lekin haqiqiy JWT `exp` undan ancha qisqa (~3 soat). Shuning uchun
`save_tokens`/`has_valid_access_token` endi JWT `exp` ni ustun qo'yadi.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta

from bm_automation.app.core.tokens import (
    _jwt_exp,
    has_valid_access_token,
    save_tokens,
)


def _jwt(exp: int) -> str:
    def seg(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


def test_jwt_exp_reads_claim():
    exp = int(time.time()) + 3600
    assert _jwt_exp(_jwt(exp)) == exp


def test_jwt_exp_non_jwt_token():
    assert _jwt_exp("opaque-token") == 0
    assert _jwt_exp("") == 0


def test_save_tokens_uses_jwt_exp(tmp_path):
    path = tmp_path / "tokens.json"
    exp = int(time.time()) + 3 * 3600
    save_tokens(_jwt(exp), "refresh", path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(data["expires_at"]) == datetime.fromtimestamp(exp)


def test_save_tokens_falls_back_to_ttl(tmp_path):
    path = tmp_path / "tokens.json"
    save_tokens("opaque-token", "refresh", path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    delta = datetime.fromisoformat(data["expires_at"]) - datetime.now()
    assert timedelta(hours=11) < delta <= timedelta(hours=12)


def test_has_valid_access_token_expired_jwt_overrides_optimistic_expires_at(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": _jwt(int(time.time()) - 10),
        "expires_at": (datetime.now() + timedelta(hours=12)).isoformat(),
    }), encoding="utf-8")
    assert has_valid_access_token(path=path) is False


def test_has_valid_access_token_true_when_jwt_valid(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": _jwt(int(time.time()) + 3600),
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")
    assert has_valid_access_token(path=path) is True


def test_has_valid_access_token_falls_back_to_expires_at(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({
        "access_token": "opaque",
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")
    assert has_valid_access_token(path=path) is True

    path.write_text(json.dumps({
        "access_token": "opaque",
        "expires_at": (datetime.now() - timedelta(hours=1)).isoformat(),
    }), encoding="utf-8")
    assert has_valid_access_token(path=path) is False
