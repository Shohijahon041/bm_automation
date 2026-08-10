"""Jadval sxemasi (DDL) — PostgreSQL va SQLite uchun bitta manba.

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
}

# Jadval -> UNIQUE indeks ustunlari (upsert `ON CONFLICT` kalitlari).
UNIQUE_KEYS: dict[str, list[str]] = {
    "profiles": ["name"],
    "routes": ["external_id"],
    "vehicles": ["external_id"],
    "drivers": ["external_id"],
    "duties": ["external_id"],
    "schedules": ["date", "route_id", "graph_name", "driver_id"],
    "waybills": ["date", "route_id", "plate_number", "direction"],
    "trips": ["date", "route_id", "vehicle_id", "driver_id", "planned_time"],
    "trip_statuses": ["code"],
    "reports": ["name", "params_hash"],
    "automation_runs": ["run_id"],
}

# Qo'shimcha (UNIQUE emas) indekslar: jadval -> ustunlar.
EXTRA_INDEXES: dict[str, list[list[str]]] = {
    "duties": [["date", "route_id"]],
    "trips": [["date", "route_id", "status"], ["date", "route_id"]],
    "report_runs": [["run_id"]],
    "errors": [["occurred_at"]],
    "notifications": [["sent_at"]],
}

STATUS_DESCRIPTIONS = {
    "ACCEPTED": "Qabul qilingan",
    "NOT_ACCEPTED": "Qabul qilinmagan",
    "PENDING_ACCESS": "Kutilmoqda",
    "APPROVED": "Tasdiqlangan",
    "REJECTED": "Rad etilgan",
    "ZERO_MILEAGE": "Nol kilometraj",
}


def _id_column(driver: str) -> str:
    if driver == "postgres":
        return "id BIGSERIAL PRIMARY KEY"
    return "id INTEGER PRIMARY KEY AUTOINCREMENT"


def _ts_type(driver: str) -> str:
    return "TIMESTAMPTZ" if driver == "postgres" else "TEXT"


def create_table_sql(driver: str, table: str) -> str:
    cols = [
        _id_column(driver),
        f"created_at {_ts_type(driver)} NOT NULL",
        f"updated_at {_ts_type(driver)} NOT NULL",
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
    try:
        with db.transaction():
            for table in TABLES:
                db.execute(create_table_sql(db.driver, table))
            for table, keys in UNIQUE_KEYS.items():
                db.execute(unique_index_sql(table, keys))
            for table, indexes in EXTRA_INDEXES.items():
                for keys in indexes:
                    db.execute(extra_index_sql(table, keys))
            seed_statuses(db)
        log.info("DB sxemasi tayyor (%s), jadvallar: %d",
                 db.driver, len(TABLES))
        return True
    except Exception as exc:  # noqa: BLE001 - DB ishlamasa automation davom etadi
        log.warning("DB sxemasi yaratilmadi: %s", exc)
        return False
