"""Strukturaviy loglash.

Konsolga yagona formatdagi log chiqaradi:
    YYYY-MM-DD HH:MM:SS | LEVEL | logger_nomi | xabar

`bm_automation.http` loggeri BMClient tomonidan har bir so'rov natijasini
(status, response time, endpoint, success/failure) qayd qilish uchun
ishlatiladi.

Loglar faylga ham yoziladi (`logs/bm.log`, `BM_LOG_FILE` bilan o'zgartirish
mumkin). Fayl aylanadigan (rotating) — 1 MB × 5 nusxa. `tail()` funksiyasi
AI o'z-o'zini rivojlantirish tahlili uchun log faylining oxirgi satrlarini
qaytaradi.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

FILE_LOG = Path(os.getenv("BM_LOG_FILE", "logs/bm.log"))

_configured = False
_file_handler: RotatingFileHandler | None | bool = None


def _get_file_handler() -> RotatingFileHandler | None:
    """Fayl log handlerni yaratadi (muvaffaqiyatsiz bo'lsa False — qayta urinishsiz)."""
    global _file_handler
    if _file_handler is None:
        try:
            FILE_LOG.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                str(FILE_LOG), maxBytes=1_000_000, backupCount=5,
                encoding="utf-8")
            handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
            _file_handler = handler
        except Exception:
            _file_handler = False
    return _file_handler if _file_handler else None


def setup_logging(level: int = logging.INFO) -> None:
    """Ildiz loggerini konfiguratsiya qiladi (bir marta)."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    fh = _get_file_handler()
    if fh:
        root.addHandler(fh)
    _configured = True


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Nomlangan logger qaytaradi (handlers o'rnatilmagan bo'lsa qo'shadi)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(handler)
        fh = _get_file_handler()
        if fh:
            logger.addHandler(fh)
        logger.setLevel(level)
        logger.propagate = False
    return logger


def tail(n: int = 200) -> str:
    """Log faylining oxirgi `n` satrini qaytaradi (bo'sh bo'lsa '')."""
    try:
        lines = FILE_LOG.read_text(encoding="utf-8",
                                   errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-n:])
