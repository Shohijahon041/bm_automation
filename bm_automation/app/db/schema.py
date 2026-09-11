"""Jadval sxemasi (DDL) — PostgreSQL (Supabase) uchun yagona manba.

Barcha jadvallar `id`, `created_at`, `updated_at` maydonlariga ega.
Upsert idempotentligi UNIQUE indekslar orqali ta'minlanadi (`ON CONFLICT`).

Jadvallar:
    profiles, routes, vehicles, drivers, duties, schedules, waybills, trips,
    trip_statuses, reports, report_runs, errors, notifications, automation_runs.
"""

from __future__ import annotations

from ..utils.logger import get_logger
from .base import Database
from .models import TABLES, TripStatus, now_utc

log = get_logger("bm_automation.db")

# Jadval: (ustun, tur) — `id/created_at/updated_at` avtomatik qo'shiladi.
TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "profiles": [
        ("external_id", "TEXT NOT NULL DEFAULT ''"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("profile_id", "TEXT NOT NULL DEFAULT ''"),
        ("route_name", "TEXT NOT NULL DEFAULT ''"),
        ("start1", "TEXT NOT NULL DEFAULT ''"),
        ("start2", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "routes": [
        ("external_id", "TEXT NOT NULL"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("parent_id", "TEXT NOT NULL DEFAULT ''"),
        ("entity_type", "TEXT NOT NULL DEFAULT ''"),
        ("is_brutto", "TEXT NOT NULL DEFAULT 't'"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "vehicles": [
        ("external_id", "TEXT NOT NULL"),
        ("plate_number", "TEXT NOT NULL DEFAULT ''"),
        ("garage_number", "TEXT NOT NULL DEFAULT ''"),
        ("model", "TEXT NOT NULL DEFAULT ''"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "drivers": [
        ("external_id", "TEXT NOT NULL"),
        ("full_name", "TEXT NOT NULL DEFAULT ''"),
        ("tin", "TEXT NOT NULL DEFAULT ''"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    # Driver-specific personnel data is intentionally kept apart from the
    # upstream ``drivers`` table. BM sync may refresh that table at any time;
    # it must never overwrite passport, pay-rate, or notification settings.
    "driver_profiles": [
        ("driver_id", "TEXT NOT NULL"),
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("passport_number", "TEXT NOT NULL DEFAULT ''"),
        ("passport_issued_by", "TEXT NOT NULL DEFAULT ''"),
        ("passport_expiry", "TEXT NOT NULL DEFAULT ''"),
        ("license_number", "TEXT NOT NULL DEFAULT ''"),
        ("license_category", "TEXT NOT NULL DEFAULT ''"),
        ("license_expiry", "TEXT NOT NULL DEFAULT ''"),
        ("passport_front_path", "TEXT NOT NULL DEFAULT ''"),
        ("passport_back_path", "TEXT NOT NULL DEFAULT ''"),
        ("license_front_path", "TEXT NOT NULL DEFAULT ''"),
        ("license_back_path", "TEXT NOT NULL DEFAULT ''"),
        # Haydovchi rasmi — dashboard/bot orqali yuklanadi (BM API'da foto yo'q).
        ("photo_path", "TEXT NOT NULL DEFAULT ''"),
        ("rating", "REAL NOT NULL DEFAULT 5"),
        ("blacklisted", "INTEGER NOT NULL DEFAULT 0"),
        ("blacklist_reason", "TEXT NOT NULL DEFAULT ''"),
        ("km_rate", "REAL NOT NULL DEFAULT 0"),
        ("notification_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("notification_target", "TEXT NOT NULL DEFAULT ''"),
        ("telegram_chat_id", "TEXT NOT NULL DEFAULT ''"),
        ("notes", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    # Manual daily odometer corrections. Automated trip counts remain the
    # source for trips; these records supplement distance/odometer data.
    "driver_work_logs": [
        ("date", "TEXT NOT NULL"),
        ("driver_id", "TEXT NOT NULL"),
        ("vehicle_id", "TEXT NOT NULL DEFAULT ''"),
        ("distance_km", "REAL NOT NULL DEFAULT 0"),
        ("trip_count", "INTEGER NOT NULL DEFAULT 0"),
        ("note", "TEXT NOT NULL DEFAULT ''"),
    ],
    "driver_fines": [
        ("driver_id", "TEXT NOT NULL"),
        ("date", "TEXT NOT NULL"),
        ("amount", "REAL NOT NULL DEFAULT 0"),
        ("reason", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
    ],
    "duties": [
        ("external_id", "TEXT NOT NULL"),
        ("date", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("shift_id", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "schedules": [
        ("date", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("graph_name", "TEXT NOT NULL DEFAULT ''"),
        ("driver_id", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_id", "TEXT NOT NULL DEFAULT ''"),
        ("shift_name", "TEXT NOT NULL DEFAULT ''"),
        ("start_time", "TEXT NOT NULL DEFAULT ''"),
        ("end_time", "TEXT NOT NULL DEFAULT ''"),
        ("trip_count", "INTEGER NOT NULL DEFAULT 0"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "waybills": [
        ("date", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_id", "TEXT NOT NULL DEFAULT ''"),
        ("driver_id", "TEXT NOT NULL DEFAULT ''"),
        ("plate_number", "TEXT NOT NULL DEFAULT ''"),
        ("direction", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    # Gross route hisoboti (brutto-route) — bitta (sana, avtobus) uchun
    # saytdagi rasmiy hisob-kitob. Haydovchi oyligi km shu manbadan olinadi
    # (`gross/route` `distanceFact`), odo km emas. Per-bus-day yagona
    # haydovchiga tegishli bo'lgani uchun butun kun km shu haydovchiga yoziladi.
    "route_daily": [
        ("date", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_id", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_number", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_brand", "TEXT NOT NULL DEFAULT ''"),
        ("shift_name", "TEXT NOT NULL DEFAULT ''"),
        ("working_day", "INTEGER NOT NULL DEFAULT 0"),
        ("trip_plan", "INTEGER NOT NULL DEFAULT 0"),
        ("trip_fact", "INTEGER NOT NULL DEFAULT 0"),
        ("trip_passed", "INTEGER NOT NULL DEFAULT 0"),
        ("trip_approved", "INTEGER NOT NULL DEFAULT 0"),
        ("distance_plan", "REAL NOT NULL DEFAULT 0"),
        ("distance_fact", "REAL NOT NULL DEFAULT 0"),
        ("distance_fact_extra", "REAL NOT NULL DEFAULT 0"),
        ("last_synced_at", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "trips": [
        ("date", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("vehicle_id", "TEXT NOT NULL DEFAULT ''"),
        ("driver_id", "TEXT NOT NULL DEFAULT ''"),
        ("planned_time", "TEXT NOT NULL DEFAULT ''"),
        ("actual_time", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'PENDING_ACCESS'"),
        ("source", "TEXT NOT NULL DEFAULT 'DUTY'"),
        ("last_synced_at", "TEXT NOT NULL DEFAULT ''"),
        # Saqlash paytida data'dan hisoblanadi — dashboard/sync to'liq JSON
        # (har bir trip ~21KB) o'tkazmasdan tez summa oladi.
        ("distance_km", "REAL NOT NULL DEFAULT 0"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "trip_statuses": [
        ("code", "TEXT NOT NULL"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("description", "TEXT NOT NULL DEFAULT ''"),
    ],
    "reports": [
        ("name", "TEXT NOT NULL"),
        ("report_type", "TEXT NOT NULL DEFAULT ''"),
        ("period_date", "TEXT NOT NULL DEFAULT ''"),
        ("params", "TEXT NOT NULL DEFAULT '{}'"),
        ("params_hash", "TEXT NOT NULL DEFAULT ''"),
        ("file_path", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "report_runs": [
        ("run_id", "TEXT NOT NULL DEFAULT ''"),
        ("report_name", "TEXT NOT NULL DEFAULT ''"),
        ("started_at", "TEXT NOT NULL DEFAULT ''"),
        ("finished_at", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT ''"),
        ("error", "TEXT NOT NULL DEFAULT ''"),
        ("output_file", "TEXT NOT NULL DEFAULT ''"),
    ],
    "errors": [
        ("source", "TEXT NOT NULL DEFAULT ''"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("traceback", "TEXT NOT NULL DEFAULT ''"),
        ("context", "TEXT NOT NULL DEFAULT '{}'"),
        ("occurred_at", "TEXT NOT NULL DEFAULT ''"),
    ],
    "notifications": [
        ("channel", "TEXT NOT NULL DEFAULT ''"),
        ("target", "TEXT NOT NULL DEFAULT ''"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT ''"),
        ("sent_at", "TEXT NOT NULL DEFAULT ''"),
    ],
    "automation_runs": [
        ("run_id", "TEXT NOT NULL"),
        ("trigger", "TEXT NOT NULL DEFAULT ''"),
        ("started_at", "TEXT NOT NULL DEFAULT ''"),
        ("finished_at", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT ''"),
        ("summary", "TEXT NOT NULL DEFAULT '{}'"),
        ("sheet_date", "TEXT NOT NULL DEFAULT ''"),
        ("month", "TEXT NOT NULL DEFAULT ''"),
    ],
    # Dispetcherga biriktirilgan yo'nalishlar. Dispetcher Telegram
    # chat_id bilan aniqlanadi (bot_users.json ROLE=DISPATCHER).
    "dispatcher_routes": [
        ("dispatcher_chat_id", "TEXT NOT NULL"),
        ("route_id", "TEXT NOT NULL"),
        ("route_name", "TEXT NOT NULL DEFAULT ''"),
        ("company", "TEXT NOT NULL DEFAULT ''"),
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("data", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    # Xujjatlar bo'limi — shablonlar asosida yaratilgan hujjatlar.
    "documents": [
        ("title", "TEXT NOT NULL DEFAULT ''"),
        ("template_key", "TEXT NOT NULL DEFAULT ''"),
        ("category", "TEXT NOT NULL DEFAULT ''"),
        ("driver_id", "TEXT NOT NULL DEFAULT ''"),
        ("fields", "TEXT NOT NULL DEFAULT '{}'"),
        ("body_text", "TEXT NOT NULL DEFAULT ''"),
        ("body_html", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'draft'"),
        ("attachments", "TEXT NOT NULL DEFAULT '[]'"),
    ],
    # Haydovchilarga SMS xabarnomalar jurnali (Android SMS Gateway).
    "sms_log": [
        ("driver_id", "TEXT NOT NULL DEFAULT ''"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("route_name", "TEXT NOT NULL DEFAULT ''"),
        ("schedule_date", "TEXT NOT NULL DEFAULT ''"),
        ("message", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'PENDING'"),
        ("message_id", "TEXT NOT NULL DEFAULT ''"),
        ("error", "TEXT NOT NULL DEFAULT ''"),
        ("send_at", "TEXT NOT NULL DEFAULT ''"),
    ],
    # Yo'nalish bo'yicha SMS faollik flagi (dashboard orqali o'chirish/yoqish).
    # Yo'nalish ro'yxatdan chiqarilsa yozuv ham o'chirilgandek: berilmagan
    # yo'nalishlar DEFAULT bo'yicha FAOL hisoblanadi.
    "sms_route_flags": [
        ("route_id", "TEXT NOT NULL"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ],
    # Avans to'lovlari (ma'muriy bo'lim) — haydovchiga berilgan avans.
    "avans": [
        ("driver_id", "TEXT NOT NULL"),
        ("name", "TEXT NOT NULL DEFAULT ''"),
        ("route_id", "TEXT NOT NULL DEFAULT ''"),
        ("route_name", "TEXT NOT NULL DEFAULT ''"),
        ("amount", "REAL NOT NULL DEFAULT 0"),
        ("pay_date", "TEXT NOT NULL DEFAULT ''"),
        ("note", "TEXT NOT NULL DEFAULT ''"),
    ],
    "staff": [
        ("name", "TEXT NOT NULL"),
        ("position", "TEXT NOT NULL DEFAULT ''"),
        ("company", "TEXT NOT NULL DEFAULT ''"),
        ("salary_type", "TEXT NOT NULL DEFAULT 'oylik'"),
        ("rate", "REAL NOT NULL DEFAULT 0"),
        ("days", "INTEGER NOT NULL DEFAULT 0"),
        ("note", "TEXT NOT NULL DEFAULT ''"),
    ],
}

# Jadval -> UNIQUE indeks ustunlari (upsert `ON CONFLICT` kalitlari).
UNIQUE_KEYS: dict[str, list[str]] = {
    "profiles": ["name"],
    "routes": ["external_id"],
    "vehicles": ["external_id"],
    "drivers": ["external_id"],
    "driver_profiles": ["driver_id"],
    "driver_work_logs": ["date", "driver_id", "vehicle_id"],
    "duties": ["external_id"],
    "schedules": ["date", "route_id", "graph_name", "driver_id"],
    "waybills": ["date", "route_id", "plate_number", "direction"],
    "trips": ["date", "route_id", "vehicle_id", "driver_id", "planned_time"],
    "route_daily": ["date", "route_id", "vehicle_number"],
    "trip_statuses": ["code"],
    "reports": ["name", "params_hash"],
    "automation_runs": ["run_id"],
    "dispatcher_routes": ["dispatcher_chat_id", "route_id"],
    "sms_route_flags": ["route_id"],
}

# Qo'shimcha (UNIQUE emas) indekslar: jadval -> ustunlar.
EXTRA_INDEXES: dict[str, list[list[str]]] = {
    "duties": [["date", "route_id"]],
    "trips": [["date", "route_id", "status"], ["date", "route_id"], ["driver_id"]],
    "route_daily": [["date", "route_id"], ["date", "vehicle_id"]],
    "driver_work_logs": [["driver_id", "date"]],
    "driver_fines": [["driver_id", "date", "status"]],
    "report_runs": [["run_id"]],
    "errors": [["occurred_at"]],
    "notifications": [["sent_at"]],
    "schedules": [["vehicle_id", "date"]],
    "driver_profiles": [["driver_id"]],
    "dispatcher_routes": [["dispatcher_chat_id", "route_id"]],
    "sms_log": [["send_at"], ["status"]],
    "avans": [["driver_id"], ["pay_date"]],
    "staff": [["company"], ["position"]],
}

STATUS_DESCRIPTIONS = {
    "ACCEPTED": "Qabul qilingan",
    "NOT_ACCEPTED": "Qabul qilinmagan",
    "PENDING_ACCESS": "Kutilmoqda",
    "APPROVED": "Tasdiqlangan",
    "REJECTED": "Rad etilgan",
    "ZERO_MILEAGE": "Nol kilometraj",
}


def create_table_sql(table: str) -> str:
    cols = [
        "id BIGSERIAL PRIMARY KEY",
        "created_at TIMESTAMPTZ NOT NULL",
        "updated_at TIMESTAMPTZ NOT NULL",
    ]
    cols.extend(f"{c} {t}" for c, t in TABLE_COLUMNS[table])
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n  "
        + ",\n  ".join(cols)
        + "\n)"
    )


def unique_index_sql(table: str, keys: list[str]) -> str:
    name = f"uq_{table}_{'_'.join(keys)}"
    cols = ", ".join(keys)
    return (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {name} "
        f"ON {table} ({cols})"
    )


def extra_index_sql(table: str, keys: list[str]) -> str:
    name = f"ix_{table}_{'_'.join(keys)}"
    cols = ", ".join(keys)
    return (
        f"CREATE INDEX IF NOT EXISTS {name} "
        f"ON {table} ({cols})"
    )


# Eski bazalarni yangilash uchun idempotent migratsiyalar.
_MIGRATIONS: list[str] = [
    "ALTER TABLE trips ADD COLUMN IF NOT EXISTS "
    "distance_km REAL NOT NULL DEFAULT 0",
    "ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS "
    "photo_path TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE driver_profiles ADD COLUMN IF NOT EXISTS "
    "telegram_chat_id TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_driver_profiles_tg_chat "
    "ON driver_profiles (telegram_chat_id) "
    "WHERE telegram_chat_id != ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS "
    "driver_id TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_documents_driver "
    "ON documents (driver_id) "
    "WHERE driver_id != ''",
    "ALTER TABLE dispatcher_routes ADD COLUMN IF NOT EXISTS "
    "phone TEXT NOT NULL DEFAULT ''",
]


def seed_statuses(db: Database) -> int:
    """TripStatus jadvalini alti status bilan to'ldiradi (idempotent)."""
    count = 0
    now = now_utc()
    for status in TripStatus.values():
        sql = (
            f"INSERT INTO trip_statuses (code, name, description, created_at, updated_at) "
            f"VALUES ({db.ph}, {db.ph}, {db.ph}, {db.ph}, {db.ph}) "
            f"ON CONFLICT (code) DO UPDATE SET "
            f"name=EXCLUDED.name, description=EXCLUDED.description, updated_at=EXCLUDED.updated_at"
        )
        count += db.execute(
            sql,
            (status, status, STATUS_DESCRIPTIONS.get(status, ""), now, now),
        )
    return count


def init_db(db: Database) -> bool:
    """Barcha jadvallarni yaratadi va statuslarni seed qiladi. Tranzaksiyada."""
    if not db.available:
        return False
    if db.driver != "postgres":
        # SQLite faqat testlarda ishlatiladi — sxemani test-backend
        # (tests/sqlite_backend.py) o'zi yaratadi.
        return True
    try:
        with db.transaction():
            # DDL bayonotlari BIR skriptga yig'iladi — Supabase session
            # pooler'da har so'rov ~165 ms: 60+ bayonot ketma-ket yuborilsa
            # init ~11 s turadi, skript bilan ~0.5 s.
            script = ";\n".join(
                [create_table_sql(t) for t in TABLES]
                # Supabase xavfsizligi: yangi jadvallar ham RLS bilan yaratiladi
                # (policy yo'q — anon/authenticated 0 qator ko'radi).
                + [f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY'
                   for t in TABLES]
                # Eski bazalarda (migratsiya) yangi ustunlar yo'q bo'lishi mumkin.
                + _MIGRATIONS
                + [unique_index_sql(t, keys) for t, keys in UNIQUE_KEYS.items()]
                + [extra_index_sql(t, keys)
                   for t, indexes in EXTRA_INDEXES.items() for keys in indexes]
            )
            db.executescript(script)
            seed_statuses(db)
        log.info("DB sxemasi tayyor (%s), jadvallar: %d",
                 db.driver, len(TABLES))
        # MyAI jadvallarini yaratish (async — bloklamaslik uchun)
        try:
            import threading as _t
            def _init_myai():
                try:
                    from ..myai.state import init_myai_schema
                    init_myai_schema()
                except Exception:
                    pass
            _t.Thread(target=_init_myai, daemon=True).start()
        except Exception:
            pass
        return True
    except Exception as exc:  # noqa: BLE001 - DB ishlamasa automation davom etadi
        log.warning("DB sxemasi yaratilmadi: %s", exc)
        return False
