"""ATTO dev skriptlari uchun kredensiallar (.env dan o'qiladi).

Loyiha ildizidagi `.env` faylida:
    ATTO_LOGIN=...
    ATTO_PASSWORD=...

Kredensiallar topilmasa — interaktiv so'raladi (parol getpass bilan
eko qilinmaydi). Hardcode kredensiallar 2026-09-12 auditidan keyin
olib tashlandi: parol kompromitatsiyaga uchragan deb hisoblanib
almashtirilishi kerak.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

# tools/atto_dev/atto_env.py -> parents[2] = loyiha ildizi
_ENV_CANDIDATES = (
    Path(__file__).resolve().parents[2] / ".env",
    Path(__file__).resolve().parent / ".env",
)


def _load_env_file() -> None:
    """`.env` ni oddiy parser bilan o'qiydi (dotenv bog'liqligisiz)."""
    for env_path in _ENV_CANDIDATES:
        if not env_path.is_file():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def atto_credentials() -> tuple[str, str]:
    """(login, password) qaytaradi; .env'da yo'q bo'lsa interaktiv so'raydi."""
    _load_env_file()
    login = os.getenv("ATTO_LOGIN", "").strip()
    password = os.getenv("ATTO_PASSWORD", "").strip()
    if login and password:
        return login, password
    print("ATTO_LOGIN / ATTO_PASSWORD .env faylida topilmadi.")
    try:
        login = login or input("ATTO login: ").strip()
        password = password or getpass.getpass("ATTO parol: ")
    except (EOFError, KeyboardInterrupt):
        sys.exit("Kredensiallar berilmadi — to'xtatildi.")
    return login, password
