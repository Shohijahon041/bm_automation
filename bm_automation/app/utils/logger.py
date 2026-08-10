"""Strukturaviy loglash.

Konsolga yagona formatdagi log chiqaradi:
    YYYY-MM-DD HH:MM:SS | LEVEL | logger_nomi | xabar

`bm_automation.http` loggeri BMClient tomonidan har bir so'rov natijasini
(status, response time, endpoint, success/failure) qayd qilish uchun
ishlatiladi.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


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
    _configured = True


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Nomlangan logger qaytaradi (handlers o'rnatilmagan bo'lsa qo'shadi)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger
