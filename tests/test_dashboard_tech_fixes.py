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
