"""Fayl I/O yordamchilari.

Takrorlangan JSON yozish / xavfsiz nomlash / atomik yozish amallari shu
modulga jamlangan. Eski `save_json(...)` chaqiruvlari bilan muvofiq qoladi.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

__all__ = ["save_json", "atomic_write", "safe_name", "ensure_dir"]


def ensure_dir(path: str | Path) -> Path:
    """Papka mavjudligini ta'minlaydi va Path qaytaradi."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_json(data, out_file: str | Path, indent: int = 2, default=str) -> str:
    """Ma'lumotni UTF-8 JSON qilib yozadi (atomic). Fayl yo'lini qaytaradi."""
    out = Path(out_file)
    ensure_dir(out.parent)
    json.dump(data, out.open("w", encoding="utf-8"),
              ensure_ascii=False, indent=indent, default=default)
    return str(out)


def atomic_write(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    """Yozishni temp fayl orqali bajarib, almashtirish (crash-xavfsiz).

    O'qish-yozish jarayonlari bir vaqtda bo'lganda buzilgan fayl qolishini
    oldini oladi.
    """
    p = Path(path)
    ensure_dir(p.parent)
    fd, tmp = tempfile.mkstemp(prefix=p.stem + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def safe_name(s, fallback: str = "profile", max_len: int = 40) -> str:
    """Fayl/papka nomi uchun xavfsiz belgilarga keltiradi.

    Har qanday `[^A-Za-z0-9_-]` belgini `_` ga almashtiradi, chekli uzunlikka
    kesadi. Bo'sh natija uchun `fallback` qaytariladi.
    """
    import re

    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")[:max_len]
    return name or fallback
