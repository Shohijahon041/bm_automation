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

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from ..utils.io import atomic_write

TOKENS_FILE = Path(os.getenv("BM_TOKENS_FILE", str(Path(__file__).resolve().parent.parent.parent / "tokens.json")))
TOKENS_DIR = Path(os.getenv("BM_TOKENS_DIR", str(TOKENS_FILE.parent / "tokens")))

# Access tokenning taxminiy yashash vaqti (aniq muddat serverdan kelmaydi).
TOKEN_TTL_HOURS = 12


def _tokens_path(path: str | Path | None = None) -> Path:
    return Path(path) if path else TOKENS_FILE


def company_tokens_path(name: str) -> Path:
    """Kompaniya uchun alohida token fayli (multi-company login)."""
    from ..utils.io import safe_name

    return TOKENS_DIR / f"{safe_name(name)}.json"


def save_tokens(access_token: str, refresh_token: str | None = None, base_url: str = "", organization: str = "", path: str | Path | None = None) -> Path:
    target = _tokens_path(path)
    data = load_tokens(path=path) or {}
    data.update(
        {
            "base_url": base_url or data.get("base_url", ""),
            "access_token": access_token,
            "refresh_token": refresh_token if refresh_token else data.get("refresh_token"),
            "organization": organization or data.get("organization", ""),
            "obtained_at": datetime.now().isoformat(timespec="seconds"),
            "expires_at": (datetime.now() + timedelta(hours=TOKEN_TTL_HOURS)).isoformat(timespec="seconds"),
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
    expires_at = data.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now():
                return False
        except ValueError:
            pass
    return True
