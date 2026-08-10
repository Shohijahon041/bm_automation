"""Ma'lumotlar bazasi qatlami — ulanish va SQL abstraksiyasi.

Ikkala backend qo'llab-quvvatlanadi:

- **PostgreSQL** (standart, ishlab chiqarish) — `psycopg` v3. Kerak:
  `pip install "psycopg[binary]"`.
- **SQLite** (kichik/local rejim) — standart `sqlite3` kutubxonasi. Hech qanday
  qo'shimcha bog'liqlik talab qilmaydi.

Agar sozlangan backend mavjud bo'lmasa yoki ulanish muvaffaqiyatsiz bo'lsa,
`Database.available` `False` bo'ladi va butun tizim *DB'siz* ishlashda davom
etadi (qulab tushmaydi).

`transaction()` konteksti ichida barcha so'rovlar BIR ulanish orqali bajariladi
(thread-local stack) — shuning uchun ko'p-so'rovli tranzaksiyalar atomik.
Tranzaksiyadan tashqarida har bir so'rov o'z ulanishini ochadi.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from ..utils.logger import get_logger

log = get_logger("bm_automation.db")

_TRUE = "t"
_FALSE = "f"

_local = threading.local()


class DatabaseError(RuntimeError):
    """Baza bilan bog'liq xatolar (ulanish, so'rov, tranzaksiya)."""


def _dialect(driver: str, dsn: str) -> str:
    if driver == "postgres":
        return "postgres"
    if driver == "sqlite":
        return "sqlite"
    # auto: DSN bor bo'lsa postgres, aks holda sqlite
    return "postgres" if dsn else "sqlite"


class Database:
    """Ulanish/so'rov abstraksiyasi.

    Obyekt ulanishlarni saqlamaydi (tranzaksiya stack'idan tashqari) — bu
    `fork`/thread xavfsizligini ta'minlaydi.
    """

    def __init__(self, driver: str = "sqlite", dsn: str = "", path: str = ""):
        self.driver = _dialect(driver, dsn)
        self.dsn = dsn
        self.path = path
        self.available = True
        self._init_failure: str | None = None
        self._verify()

    # ------------------------------------------------------------------ setup

    def _verify(self) -> None:
        try:
            if self.driver == "postgres":
                self._ensure_postgres()
            else:
                self.driver = "sqlite"
                if self.path:
                    import os
                    os.makedirs(os.path.dirname(os.path.abspath(self.path)),
                                exist_ok=True)
                conn = self._sqlite_connect()
                conn.close()
        except Exception as exc:  # noqa: BLE001 - ishga tushishga xalaqit bermaymiz
            self.available = False
            self._init_failure = str(exc)
            log.warning("DB mavjud emas (%s): %s — DB'siz davom etiladi.",
                        self.driver, exc)

    def _ensure_postgres(self) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - faqat psycopg yo'q muhit
            raise DatabaseError(
                "PostgreSQL uchun 'psycopg[binary]' o'rnatilmagan: "
                "pip install \"psycopg[binary]\""
            ) from exc
        conn = psycopg.connect(self.dsn, connect_timeout=5)
        conn.close()

    # ----------------------------------------------------------- connections

    def _postgres_connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=10)

    def _sqlite_connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def connect(self):
        if not self.available:
            raise DatabaseError(f"DB mavjud emas: {self._init_failure}")
        try:
            if self.driver == "postgres":
                return self._postgres_connect()
            return self._sqlite_connect()
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self._init_failure = str(exc)
            raise DatabaseError(f"ulanish xatosi: {exc}") from exc

    def _current_conn(self):
        """Tranzaksiya ichida bo'lsa shu ulanishni qaytaradi."""
        stack = getattr(_local, "conns", None)
        return stack[-1] if stack else None

    @property
    def ph(self) -> str:
        """So'rov placeholder'i (psycopg `%s`, sqlite `?`)."""
        return "%s" if self.driver == "postgres" else "?"

    # ------------------------------------------------------------------ exec

    def _fetch(self, conn, sql: str, params: tuple) -> list[dict[str, Any]]:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def _execute(self, conn, sql: str, params: tuple) -> int:
        cur = conn.execute(sql, params)
        return max(cur.rowcount or 0, 0)

    def query(self, sql: str, params: tuple | list | dict | None = None,
              limit: int | None = None) -> list[dict[str, Any]]:
        """SELECT so'rovi — dict qatorlar ro'yxatini qaytaradi."""
        if not self.available:
            raise DatabaseError("DB mavjud emas")
        sql = sql.strip().rstrip(";")
        if limit is not None:
            sql = f"{sql} LIMIT {int(limit)}"
        params = self._params(params)
        conn = self._current_conn()
        if conn is not None:
            return self._fetch(conn, sql, params)
        conn = self.connect()
        try:
            return self._fetch(conn, sql, params)
        finally:
            self._close(conn)

    def execute(self, sql: str, params: tuple | list | dict | None = None) -> int:
        """INSERT/UPDATE/DELETE so'rovi — ta'sirlangan qatorlar soni."""
        if not self.available:
            raise DatabaseError("DB mavjud emas")
        params = self._params(params)
        conn = self._current_conn()
        if conn is not None:
            return self._execute(conn, sql, params)
        conn = self.connect()
        try:
            count = self._execute(conn, sql, params)
            conn.commit()
            return count
        finally:
            self._close(conn)

    @staticmethod
    def _close(conn) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _params(params) -> tuple:
        if params is None:
            return ()
        if isinstance(params, dict):
            return tuple(params[k] for k in sorted(params))
        return tuple(params)

    # ----------------------------------------------------------- transactions

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Tranzaksiya konteksti.

        Muvaffaqiyatda `COMMIT`, xatolikda `ROLLBACK`. Ichidagi barcha
        so'rovlar bitta ulanishda bajariladi (thread-local stack) — shu
        sababli `execute`/`query` ham shu ulanishdan foydalanadi.
        """
        if not self.available:
            raise DatabaseError("DB mavjud emas")
        conn = self.connect()
        stack = getattr(_local, "conns", None)
        if stack is None:
            stack = []
            _local.conns = stack
        stack.append(conn)
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            stack.pop()
            if not stack:
                _local.conns = []
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- encoding

    @staticmethod
    def encode_bool(value: Any) -> str:
        return _TRUE if value else _FALSE

    @staticmethod
    def decode_bool(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "t", "true", "yes")


def row_to_str(value: Any, default: str = "") -> str:
    """DB qiymatini qatorda xavfsiz aylantirish (NULL -> bo'sh)."""
    if value is None:
        return default
    return str(value)
