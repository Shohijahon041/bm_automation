"""LLM javobidan JSON ajratish — umumiy yordamchi."""

from __future__ import annotations

import json


def extract_json_object(text: str) -> dict | None:
    """Matndan birinchi JSON obyektini ajratib olish.

    Modellar JSON'ni ```json fence ichida, izoh bilan yoki javob oxirida
    kesilgan holda qaytarishi mumkin. Eng yaqin `{` dan oxirgi `}` gacha
    olamiz — shu usul fenced/izohli javoblarni ham qamrab oladi.
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
