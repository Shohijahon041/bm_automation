"""Eski fayl va yozuvlarni aylanma tozalash.

- `reports/` papkasidagi fayllar `REPORTS_RETENTION_DAYS` (standart 30) dan
  eski bo'lsa o'chiriladi (`_exports_pending.json` o'zi hech qachon
  o'chirilmaydi);
- `_exports_pending.json` dagi `exp:` yozuvlari: fayl yo'qolgan yoki
  `EXPORTS_RETENTION_DAYS` (standart 7) dan eski bo'lsa o'chiriladi.

Bot poll-tsikli kuniga bir marta `run_once()` chaqiradi; qo'lda:
`python -m bm_automation cleanup [--dry-run]`.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

REPORTS_DIR = Path("reports")
EXPORTS_STORE = Path("reports") / "_exports_pending.json"
LOGS_DIR = Path("logs")

REPORTS_RETENTION_DAYS = 30
EXPORTS_RETENTION_DAYS = 7
LOGS_RETENTION_DAYS = 30

# Kuniga bir marta cheklovi (in-memory, restart'da nollanadi).
_LAST_RUN_AT = 0.0
_INTERVAL_S = 6 * 3600.0


def _retention(env: str, fallback: int) -> int:
    try:
        return max(1, int(os.getenv(env, "")))
    except (TypeError, ValueError):
        return fallback


def _older_than(path: Path, days: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > days * 86400
    except OSError:
        return False


def prune_reports(max_age_days: int = REPORTS_RETENTION_DAYS,
                  dry_run: bool = False) -> int:
    """`reports/` da eskirgan fayllarni o'chiradi; nechtasi uchirilgani qaytaradi."""
    if not REPORTS_DIR.exists():
        return 0
    removed = 0
    for p in sorted(REPORTS_DIR.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if p.is_file():
            if p == EXPORTS_STORE:
                continue
            if not _older_than(p, max_age_days):
                continue
            if not dry_run:
                try:
                    p.unlink()
                except OSError:
                    continue
            removed += 1
        elif p.is_dir() and not dry_run:
            try:
                p.rmdir()  # faqat bo'sh katalog
            except OSError:
                pass
    return removed


def prune_pending_exports(max_age_days: int = EXPORTS_RETENTION_DAYS,
                          dry_run: bool = False) -> int:
    """Eski `exp:` yozuvlarini `_exports_pending.json` dan o'chiradi."""
    try:
        data = json.loads(EXPORTS_STORE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    stale = [k for k, rec in data.items()
             if not _export_fresh(rec, max_age_days)]
    if not stale:
        return 0
    for k in stale:
        data.pop(k, None)
    if not dry_run:
        try:
            EXPORTS_STORE.parent.mkdir(parents=True, exist_ok=True)
            EXPORTS_STORE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return 0
    return len(stale)


def _export_fresh(rec: dict, max_age_days: int) -> bool:
    path = str(rec.get("path") or "")
    if not path:
        return False
    f = Path(path)
    return f.is_file() and not _older_than(f, max_age_days)


def prune_logs(max_age_days: int = LOGS_RETENTION_DAYS,
               dry_run: bool = False) -> int:
    """`logs/` dagi eski log fayllarni o'chiradi (aylanma fayllar ham).

    Faqat log kengaytmalari (.log, .out, .err, .txt) tozalanadi — boshqa
    fayllar (masalan foydalanuvchi qo'ygandek) tegilmaydi. Joriy yozilayotgan
    `bm.log` ham qari bo'lsa o'chirmaymiz: RotatingFileHandler o'zi aylantiradi
    (bm.log.1...bm.log.5 aylanma fayllar o'chiriladi).
    """
    if not LOGS_DIR.exists():
        return 0
    removed = 0
    for p in LOGS_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".log", ".out", ".err", ".txt"):
            continue
        if p.name == "bm.log":  # faol log — rotatsiya o'zi boshqaradi
            continue
        if not _older_than(p, max_age_days):
            continue
        if not dry_run:
            try:
                p.unlink()
            except OSError:
                continue
        removed += 1
    return removed


def run_once() -> str:
    """Kuniga bir marta bajariladigan tozalash (poll-tsikl uchun)."""
    global _LAST_RUN_AT
    if time.time() - _LAST_RUN_AT < _INTERVAL_S:
        return ""
    _LAST_RUN_AT = time.time()
    files = prune_reports(
        _retention("BM_REPORTS_RETENTION_DAYS", REPORTS_RETENTION_DAYS))
    exports = prune_pending_exports(
        _retention("BM_EXPORTS_RETENTION_DAYS", EXPORTS_RETENTION_DAYS))
    logs = prune_logs(
        _retention("BM_LOGS_RETENTION_DAYS", LOGS_RETENTION_DAYS))
    if files or exports or logs:
        return (f"🧹 Tozalandi: {files} eski hisobot fayli, "
                f"{exports} eski export yozuvi, {logs} eski log fayli.")
    return ""
