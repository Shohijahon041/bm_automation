"""Operatsion monitoring — disk, ma'lumotlar bazasi va zaxira yangiligi.

Botning poll tsiklidan davriy chaqiriladi (`check_and_send`); muammo
bo'lsa ADMIN'larga Telegram ogohlantirishi yuboriladi (alerts throttle
bilan). Bu modul hech qanday holat faylini yozmaydi — faqat o'qiydi.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard.monitor")

CHECK_INTERVAL_S = 10 * 60

_last_check_at = 0.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def disk_warn_percent() -> float:
    return min(max(_env_float("BM_DISK_WARN_PERCENT", 85.0), 50.0), 99.0)


def backup_max_age_hours() -> float:
    return max(_env_float("BM_BACKUP_MAX_AGE_H", 36.0), 1.0)


def disk_path() -> str:
    """Kuzatiladigan bo'lim: VDS'da `/opt/bm`, lokalda joriy katalog."""
    raw = os.getenv("BM_DISK_PATH", "").strip()
    if raw:
        return raw
    if os.path.isdir("/opt/bm"):
        return "/opt/bm"
    return os.getcwd()


def check_disk(path: str | None = None) -> dict:
    target = path or disk_path()
    try:
        usage = shutil.disk_usage(target)
    except OSError as exc:
        return {"ok": False, "path": target,
                "error": type(exc).__name__, "percent": 0.0, "free_gb": 0.0}
    percent = (usage.used / usage.total * 100) if usage.total else 0.0
    return {
        "ok": percent < disk_warn_percent(),
        "path": target,
        "percent": round(percent, 1),
        "free_gb": round(usage.free / (1024 ** 3), 2),
    }


def check_db() -> dict:
    """`SELECT 1` bilan haqiqiy ulanishni tekshiradi (xato matni sir saqlaydi)."""
    try:
        from ..db.storage import get_storage
        storage = get_storage()
        if not storage.enabled:
            return {"ok": True, "skipped": True, "error": ""}
        rows = storage.db.query("SELECT 1 AS ok")
        if not rows:
            return {"ok": False, "skipped": False, "error": "bo'sh javob"}
        return {"ok": True, "skipped": False, "error": ""}
    except Exception as exc:  # noqa: BLE001 - DSN/xabar ochilmasin
        return {"ok": False, "skipped": False, "error": type(exc).__name__}


def _state_file() -> Path:
    return Path("state") / "backup_state.json"


def backup_freshness(now: datetime | None = None) -> dict:
    """Oxirgi muvaffaqiyatli backup yoshini soatlarda qaytaradi."""
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "skipped": True, "last_run": "", "age_hours": 0.0}
    last_run = str((data or {}).get("last_run") or "")
    status = str((data or {}).get("last_status") or "").upper()
    if not last_run or status not in ("OK", "PARTIAL"):
        # Hali muvaffaqiyatli run yozilmagan — hukm chiqarmaymiz.
        return {"ok": True, "skipped": True, "last_run": last_run,
                "age_hours": 0.0}
    try:
        moment = datetime.fromisoformat(last_run)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        age = (now - moment).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return {"ok": True, "skipped": True, "last_run": last_run,
                "age_hours": 0.0}
    return {
        "ok": age <= backup_max_age_hours(),
        "skipped": False,
        "last_run": last_run,
        "age_hours": round(age, 1),
    }


def run_checks(*, notify: bool = True, now: datetime | None = None) -> dict:
    """Barcha tekshiruvlar; `notify=True` bo'lsa muammolar haqida xabar beradi."""
    disk = check_disk()
    db = check_db()
    backup = backup_freshness(now=now)
    result = {"ok": True, "disk": disk, "db": db, "backup": backup}
    if notify:
        from . import alerts
        if not disk.get("ok"):
            try:
                alerts.notify_disk_low(
                    float(disk.get("percent") or 0), str(disk.get("path") or ""),
                    float(disk.get("free_gb") or 0))
            except Exception as exc:  # noqa: BLE001
                log.warning("Disk ogohlantirishi yuborilmadi: %s", exc)
        if not db.get("ok"):
            try:
                alerts.notify_db_down(str(db.get("error") or ""))
            except Exception as exc:  # noqa: BLE001
                log.warning("DB ogohlantirishi yuborilmadi: %s", exc)
        if not backup.get("ok"):
            try:
                alerts.notify_backup_stale(
                    float(backup.get("age_hours") or 0),
                    str(backup.get("last_run") or ""))
            except Exception as exc:  # noqa: BLE001
                log.warning("Backup ogohlantirishi yuborilmadi: %s", exc)
    result["ok"] = bool(disk.get("ok") and db.get("ok") and backup.get("ok"))
    return result


def check_and_send(force: bool = False) -> dict:
    """Davriy chaqiruv (bot poll tsikli); interval ichida qayta ishlamaydi."""
    global _last_check_at
    now = time.monotonic()
    if not force and _last_check_at and now - _last_check_at < CHECK_INTERVAL_S:
        return {"skipped": True}
    _last_check_at = now
    result = run_checks(notify=True)
    if not result.get("ok"):
        log.warning("Monitoring muammo topdi: %s", {
            "disk": result["disk"].get("ok"),
            "db": result["db"].get("ok"),
            "backup": result["backup"].get("ok"),
        })
    return result


def reset_state() -> None:
    """Testlar uchun interval hisoblagichini tozalaydi."""
    global _last_check_at
    _last_check_at = 0.0
