"""Tokenlarni faylda saqlash va o'qish.

tokens.json formati:
{
  "base_url": "https://bmapi.dtransport.uz",
  "access_token": "...",
  "refresh_token": "...",
  "organization": "Korxona nomi",
  "expires_at": "2026-08-08T...",
  "obtained_at": "..."
}
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.io import atomic_write

TOKENS_FILE = Path(os.getenv("BM_TOKENS_FILE", str(Path(__file__).resolve().parent.parent.parent / "tokens.json")))
TOKENS_DIR = Path(os.getenv("BM_TOKENS_DIR", str(TOKENS_FILE.parent / "tokens")))

# Access tokenning taxminiy yashash vaqti — faqat JWT `exp` olinmasa ishlatiladi.
TOKEN_TTL_HOURS = 12


def _tokens_path(path: str | Path | None = None) -> Path:
    return Path(path) if path else TOKENS_FILE


def _jwt_exp(token: str) -> int:
    """JWT ``exp`` (epoch sekund); token JWT bo'lmasa yoki xato bo'lsa 0.

    Serverdan keladigan tokenning haqiqiy muddati taxminiy TTL'dan farq
    qiladi (amalda ~3 soat) — shuning uchun `expires_at`/`has_valid_access_token`
    uchun aniq manba sifatida ishlatiladi.
    """
    if not token or token.count(".") < 2:
        return 0
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return int(claims.get("exp") or 0)
    except Exception:  # noqa: BLE001 - buzuq token = muddatsiz
        return 0


def company_tokens_path(name: str) -> Path:
    """Kompaniya uchun alohida token fayli (multi-company login)."""
    from ..utils.io import safe_name

    return TOKENS_DIR / f"{safe_name(name)}.json"


def save_tokens(access_token: str, refresh_token: str | None = None, base_url: str = "", organization: str = "", path: str | Path | None = None) -> Path:
    target = _tokens_path(path)
    data = load_tokens(path=path) or {}
    exp = _jwt_exp(access_token)
    expires_at = (datetime.fromtimestamp(exp) if exp
                  else datetime.now() + timedelta(hours=TOKEN_TTL_HOURS))
    data.update(
        {
            "base_url": base_url or data.get("base_url", ""),
            "access_token": access_token,
            "refresh_token": refresh_token if refresh_token else data.get("refresh_token"),
            "organization": organization or data.get("organization", ""),
            "expires_at": expires_at.isoformat(),
            "obtained_at": datetime.now().isoformat(),
        }
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, json.dumps(data, ensure_ascii=False, indent=2))
    return target


def load_tokens(path: str | Path | None = None) -> dict | None:
    target = _tokens_path(path)
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def clear_tokens(path: str | Path | None = None) -> None:
    target = _tokens_path(path)
    if target.exists():
        target.unlink()


def has_valid_access_token(path: str | Path | None = None) -> bool:
    data = load_tokens(path=path)
    if not data or not data.get("access_token"):
        return False
    exp = _jwt_exp(data["access_token"])
    if exp:
        return exp > int(time.time())
    expires_at = data.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now():
                return False
        except ValueError:
            pass
    return True
