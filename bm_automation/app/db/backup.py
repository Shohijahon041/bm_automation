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

import os
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
# Kalitlar `schema.UNIQUE_KEYS` dagi haqiqiy UNIQUE indeks bilan bir xil
# bo'lishi shart: boshqacha qilsa eski/noto'g'ri ustunga so'rov ketadi
# (`column ... does not exist`) yoki bitta kalitga birlashtirib butun
# jadvalni yo'qotadi.
#
# Eslatma: `bot_users` jadvali emas — Telegram rollar `state/bot_users.json`
# faylida saqlanadi, shuning uchun bu yerda alohida jadval sifatida emas.
# Append-only log jadvallar (errors, notifications, documents) `id` bo'yicha
# nusxalanadi (yangi yozuvlar qo'shiladi, mavjudlari yangilanadi).
_BACKUP_TABLES = {
    "profiles": {"key_cols": ["name", "route_id"], "desc": "Profillar"},
    "routes": {"key_cols": ["external_id"], "desc": "Yo'nalishlar"},
    "vehicles": {"key_cols": ["external_id"], "desc": "Avtobuslar"},
    "drivers": {"key_cols": ["external_id"], "desc": "Haydovchilar"},
    "driver_profiles": {"key_cols": ["driver_id"], "desc": "Haydovchi profillari"},
    "duties": {"key_cols": ["external_id"], "desc": "Ish rejasi"},
    "schedules": {"key_cols": ["date", "route_id", "graph_name", "driver_id"],
                  "desc": "Grafiklar"},
    "waybills": {"key_cols": ["date", "route_id", "plate_number", "direction"],
                 "desc": "Yo'l varaqalari"},
    "trips": {"key_cols": ["date", "route_id", "vehicle_id", "driver_id",
                            "planned_time"], "desc": "Reyslar"},
    "trip_statuses": {"key_cols": ["code"], "desc": "Reys holatlari"},
    "reports": {"key_cols": ["name", "params_hash"], "desc": "Hisobotlar"},
    "driver_fines": {"key_cols": ["driver_id", "date"], "desc": "Jarimalar"},
    "driver_work_logs": {"key_cols": ["date", "driver_id", "vehicle_id"],
                         "desc": "Ish kunlari"},
    "route_daily": {"key_cols": ["date", "route_id", "vehicle_number"], "desc": "Kunlik yo'nalish"},
    "report_runs": {"key_cols": ["run_id"], "desc": "Hisobot ishlari"},
    "automation_runs": {"key_cols": ["run_id"], "desc": "Avtomatik vazifalar"},
    "dispatcher_routes": {"key_cols": ["dispatcher_chat_id", "route_id"], "desc": "Dispetcher yo'nalishlari"},
    "errors": {"key_cols": ["id"], "desc": "Xatolar jurnali"},
    "notifications": {"key_cols": ["id"], "desc": "Xabarlar jurnali"},
    "documents": {"key_cols": ["id"], "desc": "Hujjatlar"},
    "avans": {"key_cols": ["id"], "desc": "Avans to'lovlari"},
    "staff": {"key_cols": ["id"], "desc": "Ma'muriy bo'lim ishchilari"},
    "driver_appeals": {"key_cols": ["id"], "desc": "Taklif va murojaatlar"},
}


def get_backup_state() -> dict:
    """Backup holatini qaytaradi."""
    return dict(_backup_state)


# Paketli upsert uchun parametrlar chegarasi
# (PostgreSQL bir bayonotda 65535 ta parametrgacha qabul qiladi).
# Bitta tranzaksiyadagi maksimal qator: Neon pooler uzoq tranzaksiyani
# (~5 daqiqa) uzib qo'yadi — 150k qatorli `trips` bir tranzaksiyada sig'maydi.
_CHUNK_ROWS = 15000
_MAX_PARAMS = 40000
# Rezerva baza vaqtincha yopiq bo'lsa jadvalni to'xtatmaslik uchun qayta
# urinish oralig'i (daqiqa).
_PROBE_RETRY_MIN = 15
_ERROR_RETRY_MIN = 60


def _table_columns(db: Database, table: str) -> list[str]:
    """Jadvalning mavjud ustunlari nomlari."""
    if getattr(db, "driver", "") == "postgres":
        rows = db.query(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema = 'public' AND table_name = {db.ph}",
            (table,))
        return [str(r.get("column_name") or "") for r in rows]
    return [str(r.get("name") or "")
            for r in db.query(f"PRAGMA table_info({table})")]


def _source_column_defs(source: Database, table: str) -> list[dict]:
    """Manba jadvalining ustun tavsillari (PostgreSQL katalogidan)."""
    if getattr(source, "driver", "") != "postgres":
        return []
    rows = source.query(
        "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS typ,"
        " pg_get_expr(ad.adbin, ad.adrelid) AS def, a.attnotnull AS notnull"
        " FROM pg_attribute a"
        " JOIN pg_class c ON c.oid = a.attrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum"
        f" WHERE n.nspname = 'public' AND c.relname = {source.ph}"
        "   AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (table,))
    return [{"name": str(r.get("name") or ""),
             "typ": str(r.get("typ") or ""),
             "def": r.get("def"),
             "notnull": bool(r.get("notnull"))} for r in rows]


def _align_columns(target: Database, table: str, src_defs: list[dict]) -> None:
    """Rezerva bazasida yo'q ustunlarni manba turida qo'shadi.

    Sxema yangilanganda ustun yetishmasa eski kodda butun jadval sinxroni
    `column ... does not exist` xatosi bilan tashlab ketilardi.
    """
    if getattr(target, "driver", "") != "postgres" or not src_defs:
        return
    have = set(_table_columns(target, table))
    ddl = []
    for c in src_defs:
        name, typ, default = c["name"], c["typ"], str(c.get("def") or "")
        # `nextval(...)` (id) maqsad bazada o'zi mavjud — ko'chirmaymiz.
        if not name or name in have or not typ or "nextval(" in default:
            continue
        stmt = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "{name}" {typ}'
        if default:
            stmt += f" DEFAULT {default}"
            if c.get("notnull"):
                stmt += " NOT NULL"
        ddl.append(stmt)
    if ddl:
        target.executescript(";".join(ddl))
        log.info("Backup sxema: %s ga %d ustun qo'shildi", table, len(ddl))


def _upsert_sql(table: str, cols: list[str], key_cols: list[str],
                ph: str, nrows: int = 1) -> str:
    """Bitta bayonotli upsert: `INSERT ... ON CONFLICT (kalit) DO UPDATE`.

    Avvalgi staging + anti-join usuli kalitni `IS NOT DISTINCT FROM` bilan
    solishtirardi — bunday shart UNIQUE indeksdan foydalana olmaydi va har
    bo'lakda butun jadval (267 MB, TOAST'li `data` bilan) skanerlanardi.
    Shu sabab 160 ming qatorli `trips` sinxroni soatlab davom etib, hech
    qachon tugamasdi. `ON CONFLICT` konfliktni indeks orqali hal qiladi:
    qator boshiga bitta kalit tekshiruvi.
    """
    upd = [c for c in cols if c not in key_cols and c != "created_at"]
    marks = ", ".join([ph] * len(cols))
    values = ", ".join([f"({marks})"] * max(1, int(nrows)))
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES {values} "
           f"ON CONFLICT ({', '.join(key_cols)})")
    if not upd:
        return sql + " DO NOTHING"
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in upd)
    return sql + f" DO UPDATE SET {sets}"


def _unique_index_cols(target: Database, table: str) -> list[set[str]]:
    """Jadvaldagi to'liq (qismli bo'lmagan) UNIQUE indekslarning ustunlari."""
    if getattr(target, "driver", "") != "postgres":
        return []
    try:
        rows = target.query(
            "SELECT array_agg(a.attname::text ORDER BY k.ord) AS cols"
            " FROM pg_index i"
            " JOIN pg_class c ON c.oid = i.indrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY"
            " AS k(attnum, ord)"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            " AND a.attnum = k.attnum"
            " WHERE n.nspname = 'public' AND c.relname = " + target.ph +
            "   AND i.indisunique AND i.indpred IS NULL"
            "   AND i.indexprs IS NULL"
            " GROUP BY i.indexrelid",
            (table,))
    except Exception:  # noqa: BLE001 - katalog o'qilmasa ON CONFLICT yo'li
        return []
    return [set(r.get("cols") or []) for r in rows]


def _has_conflict_target(target: Database, table: str,
                         key_cols: list[str]) -> bool:
    """`ON CONFLICT (kalit)` shu jadvalda ishlaydimi.

    `driver_fines` kabi jadvallarda UNIQUE indeks yo'q — PostgreSQL
    "there is no unique or exclusion constraint matching the ON CONFLICT
    specification" bilan butun jadvalni zaxiradan tashlab ketardi.
    """
    keys = set(key_cols)
    return any(cols == keys for cols in _unique_index_cols(target, table))


def _plain_upsert_sql(table: str, cols: list[str], key_cols: list[str],
                      ph: str, nrows: int) -> tuple[str, str]:
    """UNIQUE indekssiz jadval uchun `UPDATE` + `INSERT` juftligi.

    Ikkala bayonot ham bitta yuborishda (`VALUES` ro'yxati) ishlaydi va
    kalit bo'yicha (`=`) solishtiradi — qator-baqator so'rov yuborilmaydi.
    """
    col_list = ", ".join(cols)
    marks = ", ".join([ph] * len(cols))
    values = ", ".join([f"({marks})"] * max(1, int(nrows)))
    alias = f"(VALUES {values}) AS v({col_list})"
    match = " AND ".join(
        f"t.{k} IS NOT DISTINCT FROM v.{k}" for k in key_cols)
    upd_cols = [c for c in cols if c not in key_cols and c != "created_at"]
    upd = ""
    if upd_cols:
        sets = ", ".join(f"{c} = v.{c}" for c in upd_cols)
        upd = (f"UPDATE {table} AS t SET {sets} FROM {alias}"
               f" WHERE {match} RETURNING 1")
    ins = (f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM {alias}"
           f" WHERE NOT EXISTS (SELECT 1 FROM {table} AS t WHERE {match})"
           " RETURNING 1")
    return upd, ins


def _pg_sync(target: Database, table: str, cols: list[str],
             key_cols: list[str], rows: list[dict]) -> tuple[int, int]:
    """PostgreSQL: paketli upsert (bitta tranzaksiya, bir necha bayonot).

    Har bir bayonot `_MAX_PARAMS` dan oshmaydi (PostgreSQL chegarasi
    65535 parametr). Qatorlar kalit indeksi bo'yicha yangilanadi —
    jadval to'liq skanerlanmaydi.
    """
    ph = target.ph
    seq = ""
    if "id" in key_cols:
        got = target.query(
            f"SELECT pg_get_serial_sequence({ph}, 'id') AS seq", (table,))
        seq = str((got[0] or {}).get("seq") or "") if got else ""
    conflict = _has_conflict_target(target, table, key_cols)
    if not conflict:
        log.info("Backup %s: UNIQUE indeks yo'q — UPDATE+INSERT usulida",
                 table)
    batch = max(1, _MAX_PARAMS // max(len(cols), 1))
    inserted = updated = 0
    with target.transaction():
        # Pooler "idle in transaction" limiti (5 daqiqa) backup'ni uzmasin.
        target.execute("SET LOCAL idle_in_transaction_session_timeout = 0")
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            params = tuple(r.get(c) for r in chunk for c in cols)
            if not conflict:
                upd_sql, ins_sql = _plain_upsert_sql(
                    table, cols, key_cols, ph, len(chunk))
                if upd_sql:
                    updated += len(target.query(upd_sql, params) or [])
                inserted += len(target.query(ins_sql, params) or [])
                continue
            # Bayonot bo'lak hajmiga moslanadi (bir qator — bir parametr
            # to'plami): aks holda placeholder va parametr soni mos kelmaydi.
            sql = _upsert_sql(table, cols, key_cols, ph, len(chunk))
            returning = "DO UPDATE" in sql
            if returning:
                # `xmax = 0` — yangi qo'shilgan qator (bulk_upsert usuli).
                sql += " RETURNING (xmax = 0) AS is_new"
            if returning:
                got = target.query(sql, params)
                new = sum(1 for r in got if r.get("is_new"))
                inserted += new
                updated += len(chunk) - new
            else:
                target.execute(sql, params)
                updated += len(chunk)
        if seq:
            # Yangi qatorlar mavjud id'larga tushmasligi uchun sequence
            # faqat yuqoriga ko'tariladi.
            target.execute(
                f"SELECT setval({ph}::regclass, GREATEST("
                f"(SELECT COALESCE(MAX(id), 0) FROM {table}), "
                f"(SELECT last_value FROM {seq})))", (seq,))
    return inserted, updated


def _row_sync(target: Database, table: str, cols: list[str],
              key_cols: list[str], rows: list[dict]) -> tuple[int, int]:
    """Qatorma-qator upsert (SQLite — faqat testlar uchun)."""
    ph = target.ph
    cond = " AND ".join(f"{k} = {ph}" for k in key_cols)
    upd = [c for c in cols if c not in key_cols and c != "created_at"]
    inserted = updated = 0
    for data in rows:
        keys = tuple(data.get(k) for k in key_cols)
        if target.query(f"SELECT 1 FROM {table} WHERE {cond}", keys):
            if upd:
                sets = ", ".join(f"{c} = {ph}" for c in upd)
                target.execute(
                    f"UPDATE {table} SET {sets} WHERE {cond}",
                    tuple([data.get(c) for c in upd] + list(keys)))
                updated += 1
        else:
            target.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ("
                + ", ".join([ph] * len(cols)) + ")",
                tuple(data.get(c) for c in cols))
            inserted += 1
    return inserted, updated


def _row_chunks(rows: list[dict],
                key_cols: list[str]) -> list[list[dict]]:
    """Katta jadvalni kichik tranzaksiyalarga bo'laklaydi.

    `date` kaliti bo'lsa oy bo'yicha (tabiiy chegara), aks holda
    `_CHUNK_ROWS` qatordan bo'linadi.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        prefix = str(r.get("date") or "")[:7] if "date" in key_cols else ""
        groups.setdefault(prefix, []).append(r)
    chunks: list[list[dict]] = []
    for prefix in sorted(groups):
        items = groups[prefix]
        for i in range(0, len(items), _CHUNK_ROWS):
            chunks.append(items[i:i + _CHUNK_ROWS])
    return chunks


_WM_TABLE = "_bk_watermarks"


def _ensure_watermark_table(target: Database) -> None:
    """Incremental sinxron uchun watermark jadvali (idempotent)."""
    stamp = ("TIMESTAMPTZ" if getattr(target, "driver", "") == "postgres"
             else "TEXT")
    target.executescript(
        f"CREATE TABLE IF NOT EXISTS {_WM_TABLE} ("
        f"table_name TEXT PRIMARY KEY, watermark {stamp}, "
        f"synced_at {stamp}, rows_last INTEGER DEFAULT 0)")


def _load_watermark(target: Database, table: str):
    """Oxirgi muvaffaqiyatli sinxronning `updated_at` chegarasi."""
    try:
        _ensure_watermark_table(target)
        rows = target.query(
            f"SELECT watermark FROM {_WM_TABLE} WHERE table_name = {target.ph}",
            (table,))
        if rows:
            return (rows[0] or {}).get("watermark")
    except Exception:  # noqa: BLE001 - watermark ixtiyoriy tezlashtirish
        return None
    return None


def _save_watermark(target: Database, table: str, watermark,
                    rows: int) -> None:
    """Watermark'ni faqat muvaffaqiyatli sinxrondan keyin yangilaydi."""
    if watermark is None:
        return
    try:
        _ensure_watermark_table(target)
        ph = target.ph
        if getattr(target, "driver", "") == "postgres":
            target.execute(
                f"INSERT INTO {_WM_TABLE}"
                " (table_name, watermark, synced_at, rows_last)"
                f" VALUES ({ph}, {ph}, NOW(), {ph})"
                " ON CONFLICT (table_name) DO UPDATE SET"
                " watermark = EXCLUDED.watermark, synced_at = NOW(),"
                " rows_last = EXCLUDED.rows_last",
                (table, watermark, rows))
        else:
            target.execute(
                f"INSERT OR REPLACE INTO {_WM_TABLE}"
                " (table_name, watermark, synced_at, rows_last)"
                f" VALUES ({ph}, {ph}, {ph}, {ph})",
                (table, watermark, "", rows))
    except Exception:  # noqa: BLE001 - watermark yozilmasa ham zaxira ishlaydi
        log.debug("Backup watermark yozilmadi (%s)", table)


def _sync_table(
    source: Database,
    target: Database,
    table: str,
    key_cols: list[str],
) -> tuple[int, int]:
    """Bitta jadvalni manba bazadan maqsadli bazaga sinxronlashtiradi.

    Sinxron incremental: oxirgi muvaffaqiyatli yurishning `updated_at`
    chegarasidan keyingi qatorlargina o'qiladi (`watermark`). Aks holda
    har 6 soatda 160 ming qatorli `trips` (160 MB `data`) qaytadan
    yuborilib, zaxira hech qachon tugamasdi.
    """
    watermark = _load_watermark(target, table)
    sql = f"SELECT * FROM {table}"
    params = None
    if watermark is not None and "updated_at" in _table_columns(source, table):
        # `>=`: chegara vaqtidagi qatorlar ham qayta tekshiriladi —
        # upsert idempotent, lekin o'zgarish tushib qolmaydi.
        sql += f" WHERE updated_at >= {source.ph}"
        params = (watermark,)
    rows = source.query(sql, params)
    if not rows:
        return 0, 0
    rows = [dict(r) for r in rows if hasattr(r, "keys")]
    if not rows:
        return 0, 0
    received = len(rows)

    try:
        # Rezerva bazasi eski sxemada qolib ketgan bo'lsa ustunlarni qo'shish
        _align_columns(target, table, _source_column_defs(source, table))
        tgt_cols = set(_table_columns(target, table))
        if not tgt_cols:
            raise DatabaseError(f"{table}: maqsad bazada jadval topilmadi")
        cols = [c for c in rows[0] if c in tgt_cols]
        missing = [k for k in key_cols if k not in cols]
        if missing:
            raise DatabaseError(
                f"{table}: kalit ustun bazada yo'q: {', '.join(missing)}")
        if "id" not in key_cols:
            cols = [c for c in cols if c != "id"]
        # Takroriy kalit — oxirgi qator g'alaba qiladi (eski xatti-harakat)
        dedup: dict[tuple, dict] = {}
        for r in rows:
            dedup[tuple(r.get(k) for k in key_cols)] = r
        rows = list(dedup.values())

        stamps = [r.get("updated_at") for r in rows
                  if r.get("updated_at") is not None]
        try:
            new_watermark = max(stamps) if stamps else None
        except TypeError:
            new_watermark = max(str(s) for s in stamps)

        if getattr(target, "driver", "") == "postgres":
            inserted = updated = 0
            chunks = _row_chunks(rows, key_cols)
            for n, chunk in enumerate(chunks, 1):
                started = time.time()
                # Uzilgan ulanish — qayta uriniladi (pool yangi ulanish beradi).
                for attempt in (1, 2):
                    try:
                        i, u = _pg_sync(
                            target, table, cols, key_cols, chunk)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == 2:
                            raise
                        log.warning(
                            "Backup %s: bo'lak qayta uriniladi (%s)",
                            table, exc)
                        time.sleep(3)
                inserted += i
                updated += u
                log.info("Backup %s: bo'lak %d/%d — %d qator, %.1fs",
                         table, n, len(chunks), len(chunk),
                         time.time() - started)
            _save_watermark(target, table, new_watermark, received)
            return inserted, updated
        result = _row_sync(target, table, cols, key_cols, rows)
        _save_watermark(target, table, new_watermark, received)
        return result
    except DatabaseError as exc:
        log.warning("Backup xatosi (%s): %s", table, exc)
        raise


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

    Rezerva (maqsadli) baza vaqtincha yopiq bo'lsa yoki run xato bersa,
    jadval to'xtamaydi — qisqa oralikda qayta uriniladi. Faqat muvaffaqiyatli
    run'dan keyin asosiy intervalga qaytiladi.
    """
    if not backup_enabled():
        return
    # Tashqi jadval (systemd timer) ishlatilsa ilova ichida takrorlanmaydi —
    # dashboard qayta ishga tushganda uzilib qolgan run qolmaydi.
    if os.getenv("AUTO_BACKUP_IN_APP", "on").strip().lower() in (
            "off", "0", "false", "no"):
        log.info("Auto-backup ilova ichida o'chirilgan (AUTO_BACKUP_IN_APP)")
        return

    def _probe() -> bool:
        try:
            import psycopg
            target = backup_db_settings()
            if not target.get("dsn"):
                return False
            conn = psycopg.connect(target["dsn"], connect_timeout=8)
            conn.close()
            return True
        except Exception as exc:  # noqa: BLE001 - uzilish jadvalni o'ldirmaydi
            log.warning(
                "Auto-backup: rezerva baza hozircha yetib bo'lmadi (%s) - "
                "%d daqiqadan keyin qayta uriniladi.",
                type(exc).__name__, _PROBE_RETRY_MIN)
            return False

    def _do_backup():
        interval = backup_interval_hours() * 3600
        try:
            if not _probe():
                interval = _PROBE_RETRY_MIN * 60
                return
            res = run_backup() or {}
            # "jarayonda" - boshqa run hali ishlayapti, bu xato emas.
            if not res.get("ok") and "jarayonda" not in str(res.get("error") or ""):
                interval = _ERROR_RETRY_MIN * 60
        except Exception as exc:  # noqa: BLE001
            log.error("Auto-backup xatosi: %s", exc)
            interval = _ERROR_RETRY_MIN * 60
        finally:
            _schedule_next(interval)

    def _schedule_next(interval: float):
        global _auto_backup_timer
        _auto_backup_timer = threading.Timer(interval, _do_backup)
        _auto_backup_timer.daemon = True
        _auto_backup_timer.start()

    # Birinchi backup ni 5 daqiqadan keyin bajarish (server ishga tushgandan keyin)
    _schedule_next(300)
    log.info(
        "Auto-backup yoqildi: har %d soatda (muvaffaqiyatsiz run — %d daqiqadan keyin)",
        backup_interval_hours(), _ERROR_RETRY_MIN,
    )


def stop_auto_backup():
    """Avtomatik backup ni to'xtatadi."""
    global _auto_backup_timer
    if _auto_backup_timer:
        _auto_backup_timer.cancel()
        _auto_backup_timer = None
        log.info("Auto-backup to'xtatildi")
