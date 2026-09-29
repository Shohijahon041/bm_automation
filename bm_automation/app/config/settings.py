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
        # Guruhga chiqish vaqti eslatmasi (haydovchilar bildirishnomasi):
        # on bo'lsa poll-tsikl `schedules` grafigi bo'yicha, soatga qarab
        # guruh(lar)ga ism/avtobus/chiqish vaqtini yuboradi. Har bir yo'nalish
        # o'z guruhiga bo'linadi: TG_DRIVER_ROUTE_CHATS=route_id:chat_id[;...]
        # (route_id moslari yo'q bo'lsa barchasi TG_DRIVER_CHAT_ID ga boradi).
        "group_departure_notify": os.getenv("GROUP_DEPARTURE_NOTIFY", "off").strip().lower(),
        # Yo'nalish -> guruh(ga) xaritasi: "route_id:chat1,chat2;route_id:chat3"
        "driver_route_chats": os.getenv("TG_DRIVER_ROUTE_CHATS", "").strip(),
        # Role-based access (Transport Operations Bot)
        "allowed_ids": os.getenv("TG_ALLOWED_IDS", "").strip(),
        "strict_access": os.getenv("TG_STRICT_ACCESS", "").strip(),
        "admin_ids": os.getenv("TG_ADMIN_IDS", "").strip(),
        "dispatcher_ids": os.getenv("TG_DISPATCHER_IDS", "").strip(),
        "manager_ids": os.getenv("TG_MANAGER_IDS", "").strip(),
        "driver_ids": os.getenv("TG_DRIVER_IDS", "").strip(),
        "default_role": os.getenv("TG_DEFAULT_ROLE", "viewer").strip().lower(),
        # Ertalabki AI-xulosa (kuniga bir marta)
        "daily_summary": os.getenv("AI_DAILY_SUMMARY", "on").strip().lower(),
        "daily_summary_hour": os.getenv("AI_DAILY_SUMMARY_HOUR", "8").strip(),
        # AI o'z-o'zini rivojlantirish (loglar + takroriy muammolar tahlili)
        "selfreview": os.getenv("AI_SELFREVIEW", "on").strip().lower(),
        "selfreview_hour": os.getenv("AI_SELFREVIEW_HOUR", "9").strip(),
        "selfreview_days": os.getenv("AI_SELFREVIEW_DAYS", "14").strip(),
        # Avtomatik agentlar (masalan: har kuni bo'ladigan tahlil/hisobot xabarlari).
        # "on" bo'lsa poll-tsikl agentlarni o'z jadvallarida chaqiradi;
        # "off" bo'lsa — jim, faqat foydalanuvchi buyruq yozganda javob beradi.
        "auto_agents": os.getenv("AI_AUTO_AGENTS", "off").strip().lower(),
        # Oy oxiri jarima hisoboti (fines_report agentining yagona kill-switchi).
        "fines_report": os.getenv("FINES_REPORT", "on").strip().lower(),
    }


def openrouter_settings() -> dict:
    """OpenRouter (LLM) sozlamalari.

    `OPENROUTER_API_KEY` ko'rsatilgan bo'lsa AI-yordamchi javoblarni LLM
    orqali jonlantiradi; kalit bo'lmasa lokal qoidaviy javob ishlaydi.
    """
    return {
        "api_key": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "model": (os.getenv("OPENROUTER_MODEL", "").strip()
                  or "openai/gpt-4o-mini"),
        "referer": os.getenv("OPENROUTER_HTTP_REFERER", "").strip(),
        "title": os.getenv("OPENROUTER_APP_TITLE", "BM Automation Bot").strip(),
    }


def is_test_env() -> bool:
    return os.getenv("BM_ENV", "test").strip().lower() == "test"


def km_rate_for(route_id: str = "", default: float = 0.0) -> float:
    """1 km narxi (so'm) — yo'nalish/birinlik bo'yicha, global EMSAS.

    Global `KM_RATE` env/sozlama istalmagan: har bir yo'nalish o'z narxiga
    ega bo'ladi. Ustunlik tartibi:

    1. `default` (haydovchi profilidagi shaxsiy `km_rate`, 0 dan katta);
    2. yo'nalish uchun dashboard'dan o'rnatilgan `route_km`
       (haydovchidan qat'i nazar ishlaydi);
    3. kompaniya profilidagi `kmRate` (profiles.json, `routeVariantId` bo'yicha);
    4. aks holda 0.
    """
    if default > 0:
        return default
    if route_id:
        try:
            from ..core.bot_settings import route_km
            routed = route_km(route_id)
            if routed > 0:
                return routed
        except Exception:
            pass
        try:
            from ..core.profiles import all_profiles
            for p in all_profiles():
                if str(p.get("routeVariantId") or "").strip() == route_id:
                    rate = str(p.get("kmRate") or "").strip().replace(",", ".")
                    if rate:
                        return max(float(rate), 0.0)
        except (TypeError, ValueError):
            pass
        except Exception:
            pass
    return default


def db_settings() -> dict:
    """Ma'lumotlar bazasi sozlamalari (PostgreSQL — Supabase/Neon/local).

    SUPABASE_DB_DSN / NEON_DB_DSN / BM_DB_DSN — birinchisi topilgandan
    ishlatiladi (ustunlik tartibida).
    Remote DSN bo'lsa (localhost emas), SSL avtomatik qo'shiladi.
    """
    dsn = (os.getenv("SUPABASE_DB_DSN", "").strip()
           or os.getenv("NEON_DB_DSN", "").strip()
           or os.getenv("SUPABASE_DATABASE_URL", "").strip()
           or os.getenv("BM_DB_DSN", "").strip())
    # Remote DB (localhost emas) uchun SSL avtomatik qo'shiladi
    # agar DSN'da allaqachon `sslmode=` bo'lmasa.
    is_remote = dsn and "localhost" not in dsn and "127.0.0.1" not in dsn
    if is_remote and "sslmode=" not in dsn.lower():
        sslmode = os.getenv("SUPABASE_DB_SSLMODE", "require").strip() or "require"
        dsn += "&" if "?" in dsn else "?"
        dsn += f"sslmode={sslmode}"
    return {
        "driver": "postgres",
        "dsn": dsn,
    }


def backup_db_settings() -> dict:
    """Zaxira (backup) ma'lumotlar bazasi sozlamalari.

    SUPABASE_BACKUP_DSN: Supabase Postgres connection string zaxira uchun.
    NEON_RESERVE_DSN: Neon'ni rezerv (zaxira) sifatida ishlatish uchun —
    `SUPABASE_BACKUP_DSN` berilmaganda ishlatiladi. Asosiy DB (hozir lokal)
    bilan birgalikda shu rezerv bazaga zaxira nusxa yaratiladi.
    """
    dsn = (os.getenv("SUPABASE_BACKUP_DSN", "").strip()
           or os.getenv("NEON_RESERVE_DSN", "").strip())
    if not dsn:
        return {}
    if "sslmode=" not in dsn.lower():
        sslmode = os.getenv("SUPABASE_DB_SSLMODE", "require").strip() or "require"
        dsn += "&" if "?" in dsn else "?"
        dsn += f"sslmode={sslmode}"
    return {
        "driver": "postgres",
        "dsn": dsn,
    }


def backup_enabled() -> bool:
    """Zaxira tizimi yoqilganmi."""
    return bool(os.getenv("SUPABASE_BACKUP_DSN", "").strip()
                or os.getenv("NEON_RESERVE_DSN", "").strip())


def backup_interval_hours() -> int:
    """Zaxira orasidagi vaqt (soat). Odatda 6 soat."""
    try:
        return max(int(os.getenv("BACKUP_INTERVAL_HOURS", "6").strip()), 1)
    except (TypeError, ValueError):
        return 6


def sms_gateway_settings() -> dict:
    """Android SMS Gateway (capcom6) sozlamalari — haydovchilarga SMS.

    O'z telefon+SIM karta orqali ishlaydigan lokal tarmoq shlyuzi (uchinchi
    tomon pullik SMS API emas). Login/parol `.env` dan o'qiladi, kodga
    yozilmaydi:
        ANDROID_SMS_GATEWAY_LOGIN / _PASSWORD / _URL
    """
    return {
        "login": os.getenv("ANDROID_SMS_GATEWAY_LOGIN", "").strip(),
        "password": os.getenv("ANDROID_SMS_GATEWAY_PASSWORD", "").strip(),
        "url": os.getenv("ANDROID_SMS_GATEWAY_URL", "").strip(),
    }


def sms_gateway_configured() -> bool:
    """SMS shlyuzi sozlanganmi (haydovchilarga SMS yuborish mumkinmi)."""
    s = sms_gateway_settings()
    return bool(s.get("password") and s.get("url"))
