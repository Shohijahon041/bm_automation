"""Ma'lumotlar bazasi qatlami — ulanish va SQL abstraksiyasi.

Faqat **PostgreSQL** (Supabase) qo'llab-quvvatlanadi. `psycopg` v3
talab qilinadi: `pip install "psycopg[binary]" psycopg_pool`.

Agar ulanish muvaffaqiyatsiz bo'lsa, `Database.available` `False` bo'ladi
va butun tizim *DB'siz* ishlashda davom etadi (qulab tushmaydi). DB
tiklangach `probe()` orqali avtomatik qayta ulanadi.

`transaction()` konteksti ichida barcha so'rovlar BIR ulanish orqali
bajariladi (thread-local stack) — shuning uchun ko'p-so'rovli tranzaksiyalar
atomik. Tranzaksiyadan tashqarida har bir so'rov o'z ulanishini ochadi.
"""

from __future__ import annotations

import atexit
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from ..utils.logger import get_logger

log = get_logger("bm_automation.db")

_TRUE = "t"
_FALSE = "f"

_local = threading.local()

# Barcha yaratilgan pool'lar — jarayon chiqishida thread xatosiz yopiladi
# (CLI skriptlar: `db fill-drivers` va h.k.).
_ALL_POOLS: list["Database"] = []


def _close_all_pools() -> None:
    for db in _ALL_POOLS:
        pool = getattr(db, "_pg_pool", None)
        if pool is not None and not getattr(pool, "closed", True):
            try:
                pool.close(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass


atexit.register(_close_all_pools)


class DatabaseError(RuntimeError):
    """Baza bilan bog'liq xatolar (ulanish, so'rov, tranzaksiya)."""


class Database:
    """Ulanish/so'rov abstraksiyasi — PostgreSQL (Supabase) uchun.

    `psycopg_pool` ulanishlar bazasi ishlatiladi — har bir so'rov uchun
    Supabase'ga yangi TLS ulanish ochilmaydi (~1s) va o'nlab so'rovli
    sahifalar sekinlashmaydi. Pool thread-xavfsiz: bir vaqtda ko'pi bilan
    ``_PG_POOL_SIZE`` ulanish ochiq turadi, qolgan so'rovlar bo'sh
    ulanishni kutadi.
    """

    _PG_POOL_SIZE = 8  # Ko'p foydalanuvchi uchun oshirildi (dashboard + bot + sync)

    def __init__(self, driver: str = "postgres", dsn: str = ""):
        # driver "auto"/"sqlite" qiymatlari tarixiy moslik uchun qabul
        # qilinadi, lekin app faqat postgres bilan ishlaydi.
        self.driver = "postgres"
        self.dsn = dsn
        # Pooler endpointlari (Supabase port 6543, Neon pooler, boshqa
        # serverless PG) prepared statementlarni qo'llamaydi. Psycopg
        # odatda 5-marta takrorlangan so'rovdan keyin avtomatik prepare
        # qiladi — pooler manzilida uni butunlay o'chiramiz.
        _dsn_lower = dsn.lower()
        self._disable_prepared_statements = (
            "pooler.supabase.com" in _dsn_lower
            or "-pooler." in _dsn_lower
            or "pooler.neon.tech" in _dsn_lower
        )
        self._pg_pool = None
        self.available = True
        self._init_failure: str | None = None
        self._failed_at = 0.0
        _ALL_POOLS.append(self)
        self._verify()
        if not self.available:
            self._failed_at = time.time()

    # ------------------------------------------------------------------ setup

    def _verify(self) -> None:
        try:
            self._ensure_postgres()
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
        kwargs = {"connect_timeout": 5}
        if self._disable_prepared_statements:
            kwargs["prepare_threshold"] = None
        conn = psycopg.connect(self.dsn, **kwargs)
        conn.close()

    # ----------------------------------------------------------- connections

    def _postgres_pool(self):
        """Ulanishlar bazasi (bir marta yaratiladi, keyin qayta ishlatiladi).

        Supabase'ga har so'rovda yangi ulanish ochish ~1s, har round-trip
        esa ~160ms turadi. Pool ulanishni issiq ushlab turadi, lekin har
        `putconn`da ochiq tranzaksiya `rollback` qilinsa — bu ham round-trip
        bo'ladi va so'rov narxi 3-4 barobar oshadi. Shuning uchun ulanishlar
        `autocommit=True` bilan ishlaydi (har so'rov o'z tranzaksiyasi) va
        ko'p so'rovli tranzaksiya (`transaction()`) kerak bo'lganda
        avtocommit vaqtincha o'chiriladi.
        """
        if self._pg_pool is None or getattr(self._pg_pool, "closed", False):
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
            kwargs = {
                "connect_timeout": 10,
                "row_factory": dict_row,
                "autocommit": True,
            }
            if self._disable_prepared_statements:
                kwargs["prepare_threshold"] = None
            self._pg_pool = ConnectionPool(
                self.dsn, min_size=1, max_size=self._PG_POOL_SIZE,
                timeout=10, max_waiting=50, reconnect_timeout=self._RETRY_AFTER,
                max_idle=300.0, max_lifetime=3600.0,
                name="bm_db", kwargs=kwargs,
                open=True,  # psycop_pool >= 3.2 deprecation: ochiq holatni aniq ko'rsatish
            )
        return self._pg_pool

    def _postgres_connect(self):
        return self._postgres_pool().getconn()

    def connect(self):
        """Pool'dan yangi ulanish oladi.

        DB o'chiq bo'lsa qayta ulanishga urinadi (``probe``, backoff bilan) —
        vaqtinchalik uzilishdan so'ng xizmat qayta ishga tushirilmay turib
        o'z-o'zidan tiklanishi uchun. Hali ham mavjud emas bo'lsa
        `DatabaseError` ko'tariladi.
        """
        if not self.available and not self.probe():
            raise DatabaseError(f"DB mavjud emas: {self._init_failure}")
        try:
            return self._postgres_connect()
        except Exception as exc:  # noqa: BLE001
            self.available = False
            self._init_failure = str(exc)
            self._failed_at = time.time()
            raise DatabaseError(f"ulanish xatosi: {exc}") from exc

    _RETRY_AFTER = 30.0  # sekund — muvaffaqiyatsiz ulanishdan keyin qayta urinish

    def probe(self) -> bool:
        """DB tiklangani uchun qayta ulanishga urinadi (backoff bilan).

        Qayta urinish ``_RETRY_AFTER`` dan tez-tez bajarilmaydi — DB uzoq
        muddat o'chiq bo'lsa xizmat ishlashda davom etadi (no-op rejim).
        """
        if self.available:
            return True
        if time.time() - self._failed_at < self._RETRY_AFTER:
            return False
        try:
            self._ensure_postgres()
            # DB tiklangan — eski (buzilgan) pool'ni tashlab, keyingi
            # so'rovda yangi pool ochiladi.
            pool = getattr(self, "_pg_pool", None)
            if pool is not None and not getattr(pool, "closed", True):
                try:
                    pool.close(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
                self._pg_pool = None
            self.available = True
            self._init_failure = None
            self._failed_at = 0.0
            return True
        except Exception as exc:  # noqa: BLE001
            self._failed_at = time.time()
            self._init_failure = str(exc)
            return False

    def _current_conn(self):
        """Tranzaksiya ichida bo'lsa shu ulanishni qaytaradi."""
        stack = getattr(_local, "conns", None)
        return stack[-1] if stack else None

    @property
    def ph(self) -> str:
        """So'rov placeholder'i (psycopg `%s`)."""
        return "%s"

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

    def executescript(self, script: str) -> None:
        """Ko'p bayonotli DDL-skriptni BIR round-tripda bajaradi.

        Supabase session pooler'da har bir so'rov ~165 ms turadi; schema
        init 60 dan ortiq DDL bayonotini ketma-ket yuborsa ishga tushirish
        ~11 s cho'ziladi. `;` bilan ajratilgan skript bir `execute` orqali
        yuboriladi (prepare_threshold=None — multi-statement xavfsiz).
        """
        conn = self._current_conn()
        if conn is not None:
            conn.execute(script)
            return
        conn = self.connect()
        try:
            conn.execute(script)
            conn.commit()
        finally:
            self._close(conn)

    def _close(self, conn) -> None:
        """Postgres ulanishini pool'ga qaytaradi.

        Buzilgan ulanishda ham putconn'ni bajarish muhim: rollback xatosi
        putconn'ni o'tkazib yuborsa, pool ulanishni "qarzga" hisoblab
        qoladi va bir necha xatodan keyin butunlay tugaydi
        ("couldn't get a connection after 10.00 sec").
        """
        try:
            pool = getattr(self, "_pg_pool", None)
            if pool is None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - allaqachon buzilgan
                    pass
                return
            try:
                # Pool faqat bo'sh (IDLE) ulanishni qabul qiladi —
                # ochiq tranzaksiya qolgan bo'lsa rollback qilamiz.
                if getattr(conn.info, "transaction_status", 0) != 0:
                    conn.rollback()
                # Autocommit pool sozlamasi bilan mos kelishi kerak,
                # aks holda keyingi so'rov tranzaksiyaga tushib qoladi.
                if not getattr(conn, "autocommit", True):
                    conn.autocommit = True
            except Exception:  # noqa: BLE001 - buzilgan ulanish, putconn hal qiladi
                pass
            try:
                pool.putconn(conn)
            except Exception:  # noqa: BLE001 - pool xatosi xizmatni buzmaydi
                # Buzilgan ulanishni tashlab yuborish — pool hisobi buzilmaydi.
                try:
                    pool.putconn(conn, close=True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _params(params) -> tuple:
        if params is None:
            return ()
        if isinstance(params, dict):
            return tuple(params[k] for k in sorted(params))
        return tuple(params)

    def retry_operation(self, func, max_retries=3, delay=2.0):
        """DB operatsiyasini timeout/xato bo'lsa qayta urinadi.

        Neon pooler endpointlari uzoq davom etgan operatsiyalarda
        (masalan, OneID login 1-3 daqiqa) connection timeout bo'lishi
        mumkin. Bu metod muvaffaqiyatsiz urinishdan keyin qayta urinadi.
        """
        last_exc = None
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < max_retries - 1:
                    log.warning("DB operatsiya xatosi (urinish %d/%d): %s — %s soniyadan keyin qayta urinish",
                                attempt + 1, max_retries, exc, delay)
                    time.sleep(delay)
                    # Pool'dagi buzilgan ulanishlarni tozalash
                    self.probe()
        raise last_exc

    # ----------------------------------------------------------- transactions

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Tranzaksiya konteksti.

        Muvaffaqiyatda `COMMIT`, xatolikda `ROLLBACK`. Ichidagi barcha
        so'rovlar bitta ulanishda bajariladi (thread-local stack) — shu
        sababli `execute`/`query` ham shu ulanishdan foydalanadi.
        """
        conn = self.connect()
        conn.autocommit = False
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
            # Pool'ga qaytarishdan oldin autocommit tiklanadi — shunda
            # ulanish IDLE bo'ladi va `putconn` rollback qilmaydi.
            try:
                conn.autocommit = True
            except Exception:  # noqa: BLE001
                pass
            # MUHIM: `conn.close()` emas — psycopg_pool ulanishini faqat
            # putconn() qaytaradi; close() uni "qarzda" qoldirib yuboradi
            # va bir necha tranzaksiyadan keyin pool tugaydi.
            self._close(conn)

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
