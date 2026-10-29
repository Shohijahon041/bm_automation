"""Texnik bo'limlardagi tuzatmalar uchun regresiya testlari.

Yopilgan xatolar:
1. Dashboard: hash'dagi sahifa auth yuklangandan keyin tiklanmayapti —
   deep link, F5 (refresh) va login'dan keyin har doim "Umumiy" ochiladi.
2. Dashboard: `video` / `video-grid` sahifalari o'lik (bo'sq oq sahifa),
   video allaqachon GPS iframe ichidagi tabga ko'chirilgan.
3. GPS UI: `.jt-head` qatori tor ekranda buziladi — holat matni har belgi
   qatorga bo'linib, tugmalar toshib ketadi.
4. Backup: `Database`da `fetch_all`/`fetch_one` yo'q (bori — `query()`),
   shuning uchun zaxira nusxalash butunlay ishlamaydi.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_WEB = Path("bm_automation/app/dashboard/web/index.html")
_GPS_UI = Path("bm_automation/app/gps/gps_ui.html")
_BACKUP = Path("bm_automation/app/db/backup.py")


def _web() -> str:
    return _WEB.read_text(encoding="utf-8")


# --------------------------------------------------- 1) hash / navigatsiya

def test_hash_page_applied_after_auth():
    """Auth'dan keyin hash sahifasi qayta qo'llanadi (deep link / F5 / login)."""
    html = _web()
    assert "function applyHashPage()" in html, "applyHashPage() ta'riflanmagan"
    m = re.search(r"if \(await refreshMe\(\)\) \{(.*?)\} else", html, re.S)
    assert m, "auth bootstrap topilmadi"
    assert "applyHashPage()" in m.group(1), \
        "auth'dan keyin hash sahifasi tiklanmayapti — chuqur havola va F5 buziladi"
    login = re.search(r"async function tryLogin\(\) \{(.*?)\n\}", html, re.S)
    assert login and "startDashboard();" in login.group(1) \
        and "applyHashPage();" in login.group(1), \
        "login oqimida dashboard/hash sahifasi tiklanmayapti"


def test_hashchange_uses_apply_hash_page():
    html = _web()
    assert "window.addEventListener('hashchange',()=>{ if (me) applyHashPage(); });" in html


# --------------------------------------------------- 2) o'lik "video" sahifasi

def test_page_meta_has_no_dead_video_section():
    """`video`/`video-grid` PAGE_META'da bo'lmasin — bo'sh (oq) sahifa chiqmasin."""
    html = _web()
    m = re.search(r"const PAGE_META\s*=\s*\{(.*?)\n\};", html, re.S)
    assert m, "PAGE_META topilmadi"
    keys = set(re.findall(r"^\s*'?([\w-]+)'?\s*:\s*\{", m.group(1), re.M))
    assert "gps" in keys
    assert "video" not in keys
    assert "video-grid" not in keys


def test_no_dead_video_frame_or_init():
    """O'lik `#video-frame` CSS va `initVideoFrame` qoldig'i tozalanadi."""
    html = _web()
    assert "initVideoFrame" not in html
    assert "#video-frame" not in html


def test_video_section_key_kept_for_server_parity():
    """`video` kaliti serverda `/api/video` uchun kerak — olib tashlanmaydi."""
    html = _web()
    m = re.search(r"const PAGE_ROLES\s*=\s*\{(.*?)\};", html, re.S)
    assert m, "PAGE_ROLES topilmadi"
    keys = set(re.findall(r"'?([\w-]+)'?\s*:\s*\d+", m.group(1)))
    assert "video" in keys


# --------------------------------------------- hash → sahifa xaritasi (node)

def _hash_probe(tmp_path, hash_value: str) -> dict:
    """`_hashPageAndTab()` ni node'da ishga tushirib natijani qaytaradi."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node mavjud emas")
    html = _web()
    meta = re.search(r"const PAGE_META\s*=\s*\{.*?\n\};", html, re.S)
    nav = re.search(
        r"const GPS_TAB_KEYS\s*=\s*\{.*?\};\s*function _hashPageAndTab\(\)\{.*?\n\}",
        html, re.S,
    )
    assert meta and nav, "JS bloklari topilmadi"
    script = (
        "globalThis.location = {hash: process.argv[2] || ''};\n"
        + meta.group(0) + "\n"
        + nav.group(0) + "\n"
        + "console.log(JSON.stringify(_hashPageAndTab()));"
    )
    path = tmp_path / "hash_probe.js"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run([node, str(path), hash_value],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@pytest.mark.parametrize("hash_value,expected", [
    ("", {"page": "overview", "tab": None}),
    ("#sms", {"page": "sms", "tab": None}),
    ("#documents", {"page": "documents", "tab": None}),
    ("#gps", {"page": "gps", "tab": "gps"}),
    ("#gps-video", {"page": "gps", "tab": "video"}),
    ("#gps-setka", {"page": "gps", "tab": "setka"}),
    ("#video", {"page": "gps", "tab": "video"}),
    ("#video-grid", {"page": "gps", "tab": "setka"}),
    ("#yoq-bolim", {"page": "overview", "tab": None}),
])
def test_hash_page_and_tab(hash_value, expected, tmp_path):
    assert _hash_probe(tmp_path, hash_value) == expected


# --------------------------------------------------- 3) GPS UI joylashuvi

def test_gps_ui_jt_head_wraps_on_narrow():
    """`.jt-head` qatori tor ekranda buzilmasin (matn har belgi qatorga bo'lmasin)."""
    src = _GPS_UI.read_text(encoding="utf-8")
    m = re.search(r"\.jt-head\{([^}]*)\}", src)
    assert m, ".jt-head CSS topilmadi"
    assert "flex-wrap:wrap" in m.group(1), ".jt-head qatori qaytmaydi — tugmalar toshadi"
    m2 = re.search(r"\.jt-head \.grow\{([^}]*)\}", src)
    assert m2, ".jt-head .grow CSS topilmadi"
    assert "white-space:nowrap" in m2.group(1), \
        "holat matni har belgi qatorga bo'linib ketadi"
    assert ".jt-actions{" in src, ".jt-actions konteyneri yo'q"


def test_gps_ui_camera_buttons_grouped():
    """Kamera ro'yxati qatoridagi tugmalar `.jt-actions` ichida bo'lishi kerak."""
    src = _GPS_UI.read_text(encoding="utf-8")
    block = re.search(r"Ro'yxatdagi kameralar(.*?)cam-grid", src, re.S)
    assert block, "kamera ro'yxati bloki topilmadi"
    assert "jt-actions" in block.group(1)


# --------------------------------------------------- 4) backup DB API

def test_database_api_has_no_fetch_all():
    from bm_automation.app.db.base import Database
    assert hasattr(Database, "query")
    assert not hasattr(Database, "fetch_all")
    assert not hasattr(Database, "fetch_one")


def test_backup_module_uses_query_api():
    src = _BACKUP.read_text(encoding="utf-8")
    assert not re.search(r"\.fetch_(all|one)\(", src), \
        "Database'da fetch_all/fetch_one yo'q — query() ishlatilishi kerak"
    assert "source.query(" in src
    assert "target.query(" in src


def test_sync_table_upserts_rows(tmp_path):
    """`_sync_table` haqiqiy sintax bilan ishlaydi (avval butunlay xato berardi)."""
    from bm_automation.app.db import backup
    from tests.sqlite_backend import SQLiteDatabase

    src = SQLiteDatabase(str(tmp_path / "src.db"))
    dst = SQLiteDatabase(str(tmp_path / "dst.db"))

    src.execute(
        "INSERT INTO routes (external_id, name, parent_id, entity_type, is_brutto,"
        " data, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("r-1", "Yo'nalish 1", "", "route", "t", "{}",
         "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )

    inserted, updated = backup._sync_table(src, dst, "routes", ["external_id"])
    assert (inserted, updated) == (1, 0)

    rows = dst.query("SELECT external_id, name FROM routes")
    assert rows == [{"external_id": "r-1", "name": "Yo'nalish 1"}]

    # O'zgarish — yangilanish (insert emas)
    src.execute("UPDATE routes SET name = ? WHERE external_id = ?",
                ("Yo'nalish 1 (yangilangan)", "r-1"))
    inserted, updated = backup._sync_table(src, dst, "routes", ["external_id"])
    assert (inserted, updated) == (0, 1)
    rows = dst.query("SELECT name FROM routes WHERE external_id = ?", ("r-1",))
    assert rows == [{"name": "Yo'nalish 1 (yangilangan)"}]


def test_sync_table_never_writes_target_id(tmp_path):
    """`id` kalit emasligiga qaramay yozilmasdi — boshqa qator raqami ustiga
    chiqardi (Neon'da duties/driver_work_logs/route_daily shu tufayl har
    run'da `duplicate key ... pkey` xato bermoqda edi).
    """
    from bm_automation.app.db import backup
    from tests.sqlite_backend import SQLiteDatabase

    src = SQLiteDatabase(str(tmp_path / "src.db"))
    dst = SQLiteDatabase(str(tmp_path / "dst.db"))
    now = "2026-01-01 00:00:00"
    ins = (
        "INSERT INTO routes (id, external_id, name, parent_id, entity_type,"
        " is_brutto, data, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")

    dst.execute(ins, (1, "r-1", "Eski", "", "route", "t", "{}", now, now))
    # dst'da id=2 allaqachon boshqa qator tomonidan egallangan
    dst.execute(ins, (2, "r-2", "Boshqa", "", "route", "t", "{}", now, now))
    # manbadagi r-1 qatorining id'si dst'da r-2 ga tegishli
    src.execute(ins, (2, "r-1", "Yangi", "", "route", "t", "{}", now, now))

    inserted, updated = backup._sync_table(src, dst, "routes", ["external_id"])
    assert (inserted, updated) == (0, 1)

    rows = dst.query("SELECT id, external_id, name FROM routes ORDER BY id")
    assert rows == [
        {"id": 1, "external_id": "r-1", "name": "Yangi"},
        {"id": 2, "external_id": "r-2", "name": "Boshqa"},
    ]


def test_upsert_sql_is_single_statement_on_conflict():
    """Upsert — bitta `ON CONFLICT` bayonoti, staging jadval yo'q.

    Avvalgi anti-join (`IS NOT DISTINCT FROM`) kalit indeksidan foydalana
    olmay, har bo'lakda butun `trips` jadvalini skanerlardi: zaxira
    soatlab davom etib, hech qachon tugamasdi.
    """
    from bm_automation.app.db.backup import _upsert_sql

    cols = ["date", "route_id", "distance_km", "data"]
    sql = _upsert_sql("trips", cols, ["date", "route_id"], "%s")

    head = sql[len("INSERT INTO trips ("):]
    inserted_cols = {c.strip() for c in head[:head.index(")")].split(",")}
    assert inserted_cols == set(cols)
    assert "ON CONFLICT (date, route_id)" in sql
    assert "DO UPDATE SET" in sql
    assert "distance_km = EXCLUDED.distance_km" in sql
    assert "EXCLUDED.date" not in sql          # kalit yangilanmaydi
    assert "IS NOT DISTINCT FROM" not in sql
    assert "SELECT" not in sql                 # jadval skanerlanmaydi

    # Faqat kalit + `created_at` bo'lsa — DO NOTHING.
    assert _upsert_sql("t", ["a", "created_at"], ["a"], "?").endswith(
        "DO NOTHING")


def test_auto_backup_can_be_disabled_in_app(monkeypatch):
    """Tashqi systemd timer ishlatilganda ilova ichida takroriy run bo'lmasin."""
    from bm_automation.app.db import backup

    monkeypatch.setenv("NEON_RESERVE_DSN", "postgresql://u:p@h/db")
    monkeypatch.setenv("AUTO_BACKUP_IN_APP", "off")
    backup._auto_backup_timer = None
    backup.start_auto_backup()
    assert backup._auto_backup_timer is None


def test_row_chunks_split_large_tables_by_month_and_size():
    """Neon pooler uzoq tranzaksiyani uzadi — `trips` bo'laklarga bo'linadi."""
    from bm_automation.app.db.backup import _CHUNK_ROWS, _row_chunks

    rows = [{"date": f"2026-{m:02d}-05", "driver_id": f"d{i}"}
            for m in (1, 2) for i in range(3)]
    chunks = _row_chunks(rows, ["date", "driver_id"])
    assert [len(c) for c in chunks] == [3, 3]
    assert {c[0]["date"][:7] for c in chunks} == {"2026-01", "2026-02"}

    many = [{"external_id": str(i)} for i in range(_CHUNK_ROWS + 1)]
    assert [len(p) for p in _row_chunks(many, ["external_id"])] == [
        _CHUNK_ROWS, 1]


def test_pg_sync_uses_on_conflict_and_bumps_sequence():
    """`_pg_sync` paketli `ON CONFLICT` bajaradi; `id` kalit bo'lsa
    sequence yuqoriga ko'tariladi."""
    from contextlib import contextmanager

    from bm_automation.app.db.backup import _pg_sync

    seen: list[str] = []

    class FakeDB:
        driver = "postgres"
        ph = "%s"

        @contextmanager
        def transaction(self):
            yield

        def executescript(self, sql):
            seen.append(sql)

        def execute(self, sql, params=()):
            seen.append(sql)
            return 0

        def query(self, sql, params=None):
            seen.append(sql)
            if "pg_get_serial_sequence" in sql:
                return [{"seq": "trips_id_seq"}]
            if "pg_index" in sql:
                # ON CONFLICT faqat haqiqiy UNIQUE indeksda ishlaydi.
                return [{"cols": ["date", "route_id"]}, {"cols": ["id"]}]
            if "RETURNING" in sql:
                return [{"is_new": True}, {"is_new": False}]
            return []

    _pg_sync(FakeDB(), "trips",
             ["date", "route_id", "distance_km"], ["date", "route_id"],
             [{"date": "2026-01-01", "route_id": "r", "distance_km": 1},
              {"date": "2026-01-02", "route_id": "r", "distance_km": 2}])

    joined = "\n".join(seen)
    assert "ON CONFLICT (date, route_id) DO UPDATE" in joined
    assert "RETURNING (xmax = 0) AS is_new" in joined
    # Uzoq tranzaksiya limiti o'chirilgan, jadval skanerlanmaydi.
    assert "SET LOCAL idle_in_transaction_session_timeout = 0" in joined
    assert "enable_nestloop" not in joined
    assert "CREATE TEMP TABLE" not in joined
    assert "ANALYZE" not in joined

    seen.clear()
    inserted, updated = _pg_sync(
        FakeDB(), "trips", ["id", "date"], ["id"],
        [{"id": 5, "date": "2026-01-01"}, {"id": 6, "date": "2026-01-02"}])
    joined = "\n".join(seen)
    assert "ON CONFLICT (id) DO UPDATE" in joined
    assert "setval" in joined
    assert (inserted, updated) == (1, 1)


def test_pg_sync_falls_back_when_no_unique_index():
    """`driver_fines` da UNIQUE indeks yo'q — `ON CONFLICT` xato berib,
    jadval butunlay zaxiradan tushib qolardi. Bunday jadval UPDATE+INSERT
    juftligi bilan yoziladi (qator-baqator so'rovsiz)."""
    from contextlib import contextmanager

    from bm_automation.app.db.backup import _pg_sync

    seen: list[str] = []

    class FakeDB:
        driver = "postgres"
        ph = "%s"

        @contextmanager
        def transaction(self):
            yield

        def executescript(self, sql):
            seen.append(sql)

        def execute(self, sql, params=()):
            seen.append(sql)
            return 0

        def query(self, sql, params=None):
            seen.append(sql)
            if "pg_index" in sql:
                return [{"cols": ["driver_id", "date", "status"]}]
            if "RETURNING" in sql:
                return [{"?column?": 1}]
            return []

    inserted, updated = _pg_sync(
        FakeDB(), "driver_fines", ["driver_id", "date", "amount"],
        ["driver_id", "date"],
        [{"driver_id": "d1", "date": "2026-09-01", "amount": 400000}])

    joined = "\n".join(seen)
    assert "ON CONFLICT" not in joined
    assert "UPDATE driver_fines AS t SET amount = v.amount" in joined
    assert "INSERT INTO driver_fines" in joined
    assert "NOT EXISTS (SELECT 1 FROM driver_fines AS t" in joined
    assert (inserted, updated) == (1, 1)


def test_sync_table_second_run_is_incremental(tmp_path):
    """Ikkinchi yurish watermark'dan keyingi qatorlargina o'qiydi."""
    from bm_automation.app.db import backup
    from tests.sqlite_backend import SQLiteDatabase

    src = SQLiteDatabase(str(tmp_path / "src.db"))
    dst = SQLiteDatabase(str(tmp_path / "dst.db"))
    ins = ("INSERT INTO routes (id, external_id, name, parent_id, entity_type,"
           " is_brutto, data, created_at, updated_at)"
           " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")

    src.execute(ins, (1, "r-1", "Birinchi", "", "route", "t", "{}",
                      "2026-01-01 00:00:00", "2026-01-01 00:00:00"))
    assert backup._sync_table(src, dst, "routes", ["external_id"]) == (1, 0)
    assert backup._load_watermark(dst, "routes") is not None

    src.execute(ins, (2, "r-2", "Ikkinchi", "", "route", "t", "{}",
                      "2026-01-02 00:00:00", "2026-01-02 00:00:00"))
    inserted, updated = backup._sync_table(src, dst, "routes",
                                           ["external_id"])
    assert inserted == 1
    assert updated <= 1                      # chegara qatori qayta yozilgan
    assert dst.query("SELECT count(*) AS c FROM routes")[0]["c"] == 2


def test_backup_keys_match_schema_and_unique_index():
    """`_BACKUP_TABLES` kalitlari mavjud ustunlar va haqiqiy UNIQUE indeks
    bilan mos bo'lishi shart — eskirgan kalit (`external_id`, `trip_id`)
    `column ... does not exist` bilan butun jadvalni tashlab ketardi.
    """
    from bm_automation.app.db.backup import _BACKUP_TABLES
    from bm_automation.app.db.schema import TABLE_COLUMNS, UNIQUE_KEYS

    for table, info in _BACKUP_TABLES.items():
        if table not in TABLE_COLUMNS:
            continue
        have = {"id", "created_at", "updated_at"}
        have.update(c for c, _ in TABLE_COLUMNS[table])
        missing = [k for k in info["key_cols"] if k not in have]
        assert not missing, f"{table}: kalit ustun bazada yo'q: {missing}"
        if table in UNIQUE_KEYS:
            assert info["key_cols"] == UNIQUE_KEYS[table], (
                f"{table}: backup kaliti UNIQUE indeksdan farq qiladi: "
                f"{info['key_cols']} != {UNIQUE_KEYS[table]}")
