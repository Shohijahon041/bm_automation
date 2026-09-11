"""Testlar uchun SQLite backend (app paketida SQLite yo'q).

Ishlab chiqarish kodi faqat PostgreSQL (Supabase) bilan ishlaydi. Testlar
haqiqiy Supabase'ga tegmasligi uchun `Storage` bilan ishlaydigan yengil
SQLite dublikatidan foydalanadi. Sxema `schema.TABLE_COLUMNS` dan avtomatik
yaratiladi — app sxemasi o'zgarsa testlar ham xabardor bo'ladi.

Bu fayl faqat `tests/` katalogida — ishlab chiqarish kodiga kirmaydi.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from bm_automation.app.db.models import TABLES
from bm_automation.app.db.schema import EXTRA_INDEXES, TABLE_COLUMNS, UNIQUE_KEYS


class SQLiteDatabase:
    """`app.db.base.Database` bilan API-mos test dublikati."""

    driver = "sqlite"
    ph = "?"
    _RETRY_AFTER = 30.0

    def __init__(self, path: str):
        self.path = str(path)
        self.available = True
        self._init_failure: str | None = None
        self._failed_at = 0.0
        self._local = threading.local()
        self._conn = sqlite3.connect(self.path, timeout=30,
                                     check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    # ---------------------------------------------------------------- schema

    def _create_schema(self) -> None:
        for table in TABLES:
            cols = [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "created_at TEXT NOT NULL",
                "updated_at TEXT NOT NULL",
            ]
            cols.extend(f"{c} {t}" for c, t in TABLE_COLUMNS[table])
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)})")
        for table, keys in UNIQUE_KEYS.items():
            self._conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_{'_'.join(keys)} "
                f"ON {table} ({', '.join(keys)})")
        for table, indexes in EXTRA_INDEXES.items():
            for keys in indexes:
                self._conn.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_{'_'.join(keys)} "
                    f"ON {table} ({', '.join(keys)})")
        from bm_automation.app.db.schema import seed_statuses
        seed_statuses(self)

    # ------------------------------------------------------------- lifecycle

    def probe(self) -> bool:
        if self.available:
            return True
        if time.time() - self._failed_at < self._RETRY_AFTER:
            return False
        self.available = True
        self._failed_at = 0.0
        return True

    def _current_conn(self) -> sqlite3.Connection | None:
        stack = getattr(self._local, "conns", None)
        return stack[-1] if stack else None

    # ------------------------------------------------------------------ exec

    def query(self, sql: str, params: tuple | list | dict | None = None,
              limit: int | None = None) -> list[dict[str, Any]]:
        sql = sql.strip().rstrip(";")
        if limit is not None:
            sql = f"{sql} LIMIT {int(limit)}"
        cur = self._conn.execute(sql, self._params(params))
        return [dict(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: tuple | list | dict | None = None) -> int:
        cur = self._conn.execute(sql, self._params(params))
        return max(cur.rowcount or 0, 0)

    def _in_tx(self) -> bool:
        return bool(getattr(self._local, "conns", None))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """BEGIN/COMMIT/ROLLBACK — Storage `db.transaction()` bilan mos."""
        stack = getattr(self._local, "conns", None)
        if stack:
            raise RuntimeError("ichma-ich tranzaksiya SQLite'da qo'llanmaydi")
        self._local.conns = [True]
        self._conn.execute("BEGIN")
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._local.conns = []

    @staticmethod
    def _params(params) -> tuple:
        if params is None:
            return ()
        if isinstance(params, dict):
            return tuple(params[k] for k in sorted(params))
        return tuple(params)
