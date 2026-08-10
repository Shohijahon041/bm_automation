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

# Access tokenning taxminiy yashash vaqti (aniq muddat serverdan kelmaydi).
TOKEN_TTL_HOURS = 12


def save_tokens(access_token: str, refresh_token: str | None = None, base_url: str = "", organization: str = "") -> Path:
    data = load_tokens() or {}
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
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(TOKENS_FILE, json.dumps(data, ensure_ascii=False, indent=2))
    return TOKENS_FILE


def load_tokens() -> dict | None:
    if not TOKENS_FILE.exists():
        return None
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def clear_tokens() -> None:
    if TOKENS_FILE.exists():
        TOKENS_FILE.unlink()


def has_valid_access_token() -> bool:
    data = load_tokens()
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
