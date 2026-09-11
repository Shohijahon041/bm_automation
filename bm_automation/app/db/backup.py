"""Zaxira (backup) moduli — mahalliy PostgreSQL → Supabase sinxronizatsiya.

Loyiha asosiy ma'lumotlar bazasini (mahalliy PostgreSQL) Supabase'ga
zaxira sifatida sinxronlashtiradi. Har bir jadval alohida sinxronlashtiriladi:

- Natural kalit bo'yicha upsert (dublikat yaratilmaydi)
- Faqat o'zgarishlar sinxronlashtiriladi (yangilangan yozuvlar)
- Xatolikda boshqa jadvallar to'xtamaydi
- Har bir backup operatsiyasi log qilinadi

Foydalanish:
    python -m bm_automation db backup          # Bitta marta backup
    python -m bm_automation db backup --status  # Backup holatini ko'rish
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from typing import Any

from ..config.settings import backup_db_settings, backup_enabled, backup_interval_hours
from ..utils.logger import get_logger
from .base import Database, DatabaseError

log = get_logger("bm_automation.backup")

# Backup holati (xotira ichida)
_backup_state = {
    "last_run": None,
    "last_duration": 0,
    "last_status": "never",
    "tables_synced": 0,
    "rows_synced": 0,
    "errors": [],
    "running": False,
}

# Backup jarayoni uchun lock
_backup_lock = threading.Lock()

# Sinxronlashtiriladigan jadvallar (natural kalit bilan)
#
# Eslatma: `bot_users` jadvali emas — Telegram rollar `state/bot_users.json`
# faylida saqlanadi, shuning uchun bu yerda alohida jadval sifatida emas.
# Append-only log jadvallar (errors, notifications, documents) `id` bo'yicha
# nusxalanadi (yangi yozuvlar qo'shiladi, mavjudlari yangilanadi).
_BACKUP_TABLES = {
    "profiles": {"key_cols": ["external_id"], "desc": "Profillar"},
    "routes": {"key_cols": ["external_id"], "desc": "Yo'nalishlar"},
    "vehicles": {"key_cols": ["external_id"], "desc": "Avtobuslar"},
    "drivers": {"key_cols": ["external_id"], "desc": "Haydovchilar"},
    "driver_profiles": {"key_cols": ["driver_id"], "desc": "Haydovchi profillari"},
    "duties": {"key_cols": ["external_id"], "desc": "Ish rejasi"},
    "schedules": {"key_cols": ["external_id"], "desc": "Grafiklar"},
    "waybills": {"key_cols": ["external_id"], "desc": "Yo'l varaqalari"},
    "trips": {"key_cols": ["external_id"], "desc": "Reyslar"},
    "trip_statuses": {"key_cols": ["trip_id", "status"], "desc": "Reys holatlari"},
    "reports": {"key_cols": ["external_id"], "desc": "Hisobotlar"},
    "driver_fines": {"key_cols": ["driver_id", "date"], "desc": "Jarimalar"},
    "driver_work_logs": {"key_cols": ["driver_id", "date"], "desc": "Ish kunlari"},
    "route_daily": {"key_cols": ["date", "route_id", "vehicle_number"], "desc": "Kunlik yo'nalish"},
    "report_runs": {"key_cols": ["run_id"], "desc": "Hisobot ishlari"},
    "automation_runs": {"key_cols": ["run_id"], "desc": "Avtomatik vazifalar"},
    "dispatcher_routes": {"key_cols": ["dispatcher_chat_id", "route_id"], "desc": "Dispetcher yo'nalishlari"},
    "errors": {"key_cols": ["id"], "desc": "Xatolar jurnali"},
    "notifications": {"key_cols": ["id"], "desc": "Xabarlar jurnali"},
    "documents": {"key_cols": ["id"], "desc": "Hujjatlar"},
    "avans": {"key_cols": ["id"], "desc": "Avans to'lovlari"},
    "staff": {"key_cols": ["id"], "desc": "Ma'muriy bo'lim ishchilari"},
}


def get_backup_state() -> dict:
    """Backup holatini qaytaradi."""
    return dict(_backup_state)


def _sync_table(
    source: Database,
    target: Database,
    table: str,
    key_cols: list[str],
) -> tuple[int, int]:
    """Bitta jadvalni manba bazadan maqsadli bazaga sinxronlashtiradi.

    (inserted, updated) sonlarini qaytaradi.
    """
    inserted = 0
    updated = 0

    try:
        # Manba bazadan barcha yozuvlarni olish
        rows = source.fetch_all(f"SELECT * FROM {table}")
        if not rows:
            return 0, 0

        for row in rows:
            # Row dict ga aylantirish
            if hasattr(row, "keys"):
                data = dict(row)
            else:
                continue

            # Natural kalit bo'yicha mavjudligini tekshirish
            key_conditions = " AND ".join(
                f"{k} = {target.ph}" for k in key_cols
            )
            key_values = tuple(data.get(k) for k in key_cols)

            existing = target.fetch_one(
                f"SELECT 1 FROM {table} WHERE {key_conditions}",
                key_values,
            )

            if existing:
                # Yangilash
                update_cols = [c for c in data if c not in key_cols and c != "created_at"]
                if update_cols:
                    sets = ", ".join(f"{c} = {target.ph}" for c in update_cols)
                    vals = [data.get(c) for c in update_cols]
                    vals.extend(key_values)
                    target.execute(
                        f"UPDATE {table} SET {sets} WHERE {key_conditions}",
                        tuple(vals),
                    )
                    updated += 1
            else:
                # Qo'shish
                cols = list(data.keys())
                ph = target.ph
                placeholders = ", ".join([ph] * len(cols))
                col_names = ", ".join(cols)
                vals = [data.get(c) for c in cols]
                target.execute(
                    f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
                    tuple(vals),
                )
                inserted += 1

    except DatabaseError as exc:
        log.warning("Backup xatosi (%s): %s", table, exc)
        raise

    return inserted, updated


def run_backup(tables: list[str] | None = None) -> dict:
    """Backup operatsiyasini bajaradi.

    Args:
        tables: Qaysi jadvallarni sinxronlashtirish (None = barchasi)

    Returns:
        Backup natijasi dict
    """
    if not backup_enabled():
        return {"ok": False, "error": "Backup yoqilmagan (SUPABASE_BACKUP_DSN ko'rsatilmagan)"}

    if _backup_state["running"]:
        return {"ok": False, "error": "Backup allaqachon jarayonda"}

    with _backup_lock:
        _backup_state["running"] = True
        _backup_state["errors"] = []

    start_time = time.time()
    result = {
        "ok": True,
        "tables_synced": 0,
        "rows_synced": 0,
        "duration": 0,
        "details": [],
    }

    try:
        # Manba bazani ochish (mahalliy PostgreSQL)
        from .storage import get_storage
        source_storage = get_storage()
        if not source_storage.enabled:
            return {"ok": False, "error": "Manba DB mavjud emas"}

        source_db = source_storage.db

        # Rezerv (maqsadli) bazani ochish (Supabase/Neon)
        target_db = Database(**backup_db_settings())
        if not target_db.available:
            return {"ok": False,
                    "error": "Rezerv baza yetib bo'lmadi (kvota/ulanish bloklangan)"}

        # Sxemani yaratish (idempotent)
        from .schema import init_db
        init_db(target_db)

        # Jadval ro'yxatini aniqlash
        tables_to_sync = tables or list(_BACKUP_TABLES.keys())

        for table_name in tables_to_sync:
            if table_name not in _BACKUP_TABLES:
                continue

            table_info = _BACKUP_TABLES[table_name]
            try:
                inserted, updated = _sync_table(
                    source_db,
                    target_db,
                    table_name,
                    table_info["key_cols"],
                )
                total = inserted + updated
                result["rows_synced"] += total
                result["tables_synced"] += 1
                result["details"].append({
                    "table": table_name,
                    "desc": table_info["desc"],
                    "inserted": inserted,
                    "updated": updated,
                    "total": total,
                })
                if total > 0:
                    log.info(
                        "Backup %s: +%d yangi, ~%d yangilandi",
                        table_name, inserted, updated,
                    )
            except Exception as exc:
                error_msg = f"{table_name}: {exc}"
                result["details"].append({
                    "table": table_name,
                    "error": str(exc),
                })
                _backup_state["errors"].append(error_msg)
                log.warning("Backup xatosi: %s", error_msg)

        # Backup metadata yozish
        try:
            meta = {
                "backup_at": datetime.now(timezone.utc).isoformat(),
                "tables_synced": result["tables_synced"],
                "rows_synced": result["rows_synced"],
                "duration_seconds": round(time.time() - start_time, 1),
                "status": "OK" if not _backup_state["errors"] else "PARTIAL",
            }
            target_db.execute(
                "INSERT INTO automation_runs (run_id, started_at, finished_at, status, summary) "
                f"VALUES ({target_db.ph}, {target_db.ph}, {target_db.ph}, {target_db.ph}, {target_db.ph})",
                (
                    f"backup_{int(time.time())}",
                    meta["backup_at"],
                    meta["backup_at"],
                    meta["status"],
                    str(meta),
                ),
            )
        except Exception:
            pass  # Metadata yozish xatosi backup ni buzmidi

        # Yakuniy natija
        duration = round(time.time() - start_time, 1)
        result["duration"] = duration
        result["ok"] = not bool(_backup_state["errors"])

        # Holatni yangilash
        _backup_state["last_run"] = datetime.now(timezone.utc).isoformat()
        _backup_state["last_duration"] = duration
        _backup_state["last_status"] = "OK" if result["ok"] else "PARTIAL"
        _backup_state["tables_synced"] = result["tables_synced"]
        _backup_state["rows_synced"] = result["rows_synced"]

        log.info(
            "Backup tugadi: %d jadval, %d qator, %.1f soniya",
            result["tables_synced"],
            result["rows_synced"],
            duration,
        )

    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        _backup_state["last_status"] = "ERROR"
        _backup_state["errors"].append(str(exc))
        log.error("Backup xatosi: %s", exc)

    finally:
        _backup_state["running"] = False

    return result


# Auto-backup uchun timer
_auto_backup_timer: threading.Timer | None = None


def start_auto_backup():
    """Avtomatik backup ni ishga tushiradi (interval bilan).

    Rezerv (maqsadli) baza hozircha yetib bo'lmasa (masalan Neon kvotasi
    bloklangan), doimiy xato qilmaslik uchun jadval ishga tushmaydi — faqat
    ogohlantirish yoziladi va keyinroq (Neon yechilganda) qo'lda ishga
    tushirish mumkin.
    """
    if not backup_enabled():
        return

    try:
        import psycopg
        target = backup_db_settings()
        if not target.get("dsn"):
            return
        c = psycopg.connect(target["dsn"], connect_timeout=8)
        c.close()
    except Exception as exc:
        log.warning(
            "Auto-backup ishga tushirilmadi: rezerv baza yetib bo'lmadi (%s). "
            "Neon/kvota yechilganda qayta uriniladi.",
            type(exc).__name__,
        )
        return

    def _do_backup():
        try:
            run_backup()
        except Exception as exc:
            log.error("Auto-backup xatosi: %s", exc)
        finally:
            # Keyingi backup uchun schedule qilish
            interval = backup_interval_hours() * 3600
            _schedule_next(interval)

    def _schedule_next(interval: float):
        global _auto_backup_timer
        _auto_backup_timer = threading.Timer(interval, _do_backup)
        _auto_backup_timer.daemon = True
        _auto_backup_timer.start()

    # Birinchi backup ni 5 daqiqadan keyin bajarish (server ishga tushgandan keyin)
    _schedule_next(300)
    log.info(
        "Auto-backup yoqildi: har %d soatda",
        backup_interval_hours(),
    )


def stop_auto_backup():
    """Avtomatik backup ni to'xtatadi."""
    global _auto_backup_timer
    if _auto_backup_timer:
        _auto_backup_timer.cancel()
        _auto_backup_timer = None
        log.info("Auto-backup to'xtatildi")
