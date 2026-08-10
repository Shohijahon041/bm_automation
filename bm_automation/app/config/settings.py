"""Markazlashgan konfiguratsiya.

Barcha env o'zgaruvchilar, URL konstantalar va sozlamalar yagona nuqtadan
olinadi. Eski `bm_automation.config` moduli shu modulni qayta eksport qiladi.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

PROD_BASE_URL = "https://bmapi.dtransport.uz"
TEST_BASE_URL = "https://testapi-bm.dtransport.uz"

USER_MGMT = "/user-management/api/v1"
BRUTTO_MGMT = "/brutto-management/api/v1"
NON_BRUTTO_MGMT = "/non-brutto-management/api/v1"
ROUTE_MGMT = "/route-management/api/v1"
NOTIFICATION = "/notification/api/v1"
ONLINE_DISPATCH = "/online-dispatch/api/v1"
OPERATIVE_REPORT = "/operative-report/api/v1"

# Telegram Bot API
TG_API = "https://api.telegram.org/bot{token}/{method}"


@dataclass
class Config:
    base_url: str
    username: str
    password: str
    organization: str = ""


def get_config() -> Config:
    env = os.getenv("BM_ENV", "test").strip().lower()
    base_url = TEST_BASE_URL if env == "test" else PROD_BASE_URL
    username = os.getenv("BM_USERNAME", "").strip()
    password = os.getenv("BM_PASSWORD", "").strip()
    if not username or not password:
        raise ValueError(
            "BM_USERNAME/BM_PASSWORD .env faylida ko'rsatilmagan. "
            ".env.example dan nusxa oling."
        )
    return Config(
        base_url=base_url,
        username=username,
        password=password,
        organization=os.getenv("BM_ORGANIZATION", "").strip(),
    )


def telegram_settings() -> dict:
    return {
        "token": os.getenv("TG_BOT_TOKEN", "").strip(),
        "chat_id": os.getenv("TG_CHAT_ID", "").strip(),
        "driver_chat_id": os.getenv("TG_DRIVER_CHAT_ID", "").strip(),
        # Role-based access (Transport Operations Bot)
        "admin_ids": os.getenv("TG_ADMIN_IDS", "").strip(),
        "dispatcher_ids": os.getenv("TG_DISPATCHER_IDS", "").strip(),
        "default_role": os.getenv("TG_DEFAULT_ROLE", "viewer").strip().lower(),
    }


def is_test_env() -> bool:
    return os.getenv("BM_ENV", "test").strip().lower() == "test"


def db_settings() -> dict:
    """Ma'lumotlar bazasi sozlamalari.

    BM_DB_DRIVER: auto | postgres | sqlite (auto = BM_DB_DSN bo'lsa postgres,
    aks holda sqlite).
    BM_DB_DSN: PostgreSQL ulanish qatori, masalan
        postgresql://user:pass@localhost:5432/bm_automation
    BM_DB_PATH: SQLite fayl yo'li (standart data/bm_automation.db).
    """
    driver = os.getenv("BM_DB_DRIVER", "auto").strip().lower()
    dsn = os.getenv("BM_DB_DSN", "").strip()
    path = os.getenv("BM_DB_PATH", "").strip() or "data/bm_automation.db"
    return {
        "driver": driver,
        "dsn": dsn,
        "path": path,
    }
