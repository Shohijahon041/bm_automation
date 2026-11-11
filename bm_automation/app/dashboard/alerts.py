"""Xavfsizlik/operatsion ogohlantirishlar (Telegram).

Ketma-ket login xatolari va backup nosozligi ADMIN'larga Telegram orqali
yuboriladi. Yuborish fon oqimida — HTTP so'rov javobi kutmaydi. Bir turdagi
ogohlantirish `_THROTTLE_S` ichida faqat bir marta ketadi (log spam bo'lmasin).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard.alerts")

# Tur bo'yicha jimjitlik oynasi (soniya).
_THROTTLE_S = {"login": 10 * 60, "backup": 30 * 60,
               "disk": 6 * 3600, "db": 30 * 60, "backup_stale": 6 * 3600}

# Login xatolari oynasi: `LOGIN_ALERT_THRESHOLD` marta `LOGIN_ALERT_WINDOW_S`
# ichida bo'lsa bitta ogohlantirish yuboriladi.
LOGIN_ALERT_THRESHOLD = 5
LOGIN_ALERT_WINDOW_S = 10 * 60

_LOCK = threading.Lock()
_last_sent: dict[str, float] = {}
_fail_times: list[float] = []
# Testlarda almashtiriladi (haqiqiy Telegram o'rniga).
_sender: Callable[[str], bool] | None = None

# FastAPI `TestClient` so'rovlarining client host'i — haqiqiy foydalanuvchi
# emas. Bunday urinishlar hisoblanmaydi: aks holda testlar/lokal tekshiruv
# ADMIN'larga "login xatolari" ogohlantirishini yuborib yuboradi.
_TEST_CLIENT_HOSTS = frozenset({"testclient", "testserver"})


def _is_test_origin(ip: str) -> bool:
    return str(ip or "").strip().lower() in _TEST_CLIENT_HOSTS


def admin_targets() -> list[str]:
    """`TG_ADMIN_IDS` (vergul/probel bilan) → chat_id ro'yxati."""
    raw = os.getenv("TG_ADMIN_IDS", "").replace(";", ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _telegram_send(text: str) -> bool:
    """Barcha ADMIN'larga yuboradi; bittasi ishlamasa qolganlari davom etadi."""
    targets = admin_targets()
    if not targets:
        return False
    try:
        from ..notifications.telegram import send_message
    except Exception:  # noqa: BLE001 - import xatosi ogohlantirishni to'xtatmaydi
        log.warning("Ogohlantirish yuborilmadi: telegram moduli yuklanmadi")
        return False
    sent = False
    for chat_id in targets:
        try:
            send_message(text, chat_id=chat_id)
            sent = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Ogohlantirish yuborilmadi (%s): %s", chat_id, exc)
    return sent


def send_admin_text(text: str) -> bool:
    """Admin'larga tayyor matnni yuboradi (eslatma agentlari uchun)."""
    return _telegram_send(text)


def _dispatch(kind: str, text: str) -> bool:
    """Throttle tekshiruvi + fon oqimida yuborish."""
    now = time.monotonic()
    with _LOCK:
        window = _THROTTLE_S.get(kind, 10 * 60)
        last = _last_sent.get(kind, 0.0)
        if last and now - last < window:
            return False
        _last_sent[kind] = now
    # Test rejimida tashqi tarmoqqa chiqilmaydi (yuboruvchi almashtirilmasa).
    if _sender is None and os.getenv("BM_TEST_MODE") == "1":
        return False
    sender = _sender or _telegram_send

    def _run() -> None:
        try:
            sender(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ogohlantirish yuborish xatosi: %s", exc)

    threading.Thread(target=_run, daemon=True, name=f"bm-alert-{kind}").start()
    return True


def notify_login_failure(username: str, ip: str, detail: str) -> bool:
    """Ketma-ket login xatolari chegaradan oshsa ADMIN'larga xabar beradi."""
    if _is_test_origin(ip):
        # In-process TestClient (testlar, lokal tekshiruv) — real tahdid emas.
        return False
    now = time.monotonic()
    with _LOCK:
        _fail_times[:] = [t for t in _fail_times if now - t < LOGIN_ALERT_WINDOW_S]
        _fail_times.append(now)
        count = len(_fail_times)
    if count < LOGIN_ALERT_THRESHOLD:
        return False
    text = (
        "BM OGOHLANTIRISH: login xatolari\n"
        f"Oxirgi {LOGIN_ALERT_WINDOW_S // 60} daqiqada {count} marta xato.\n"
        f"Oxirgi urinish: login={username or '?'} ip={ip or '?'} sabab={detail or '?'}\n"
        "Sabab bo'lmasa fail2ban/nginx jurnalini tekshiring."
    )
    return _dispatch("login", text)


def notify_backup_failure(status: str, error: str) -> bool:
    """Backup to'liq bo'lmasa (PARTIAL/ERROR) ADMIN'larga xabar beradi."""
    text = (
        "BM OGOHLANTIRISH: zaxira nusxa\n"
        f"Holat: {status or '?'}\n"
        f"Xato: {(error or 'noma lum')[:500]}\n"
        "Ma'lumotlar bazasi/disk holatini tekshiring."
    )
    return _dispatch("backup", text)


def notify_disk_low(percent: float, path: str, free_gb: float) -> bool:
    """Disk to'lib borayotganda ADMIN'larga xabar beradi."""
    text = (
        "BM OGOHLANTIRISH: disk joyi\n"
        f"Bo'lim: {path or '?'} — {percent:.0f}% band, "
        f"bo'sh {free_gb:.1f} GB.\n"
        "Eski log/backup/segment fayllarni tozalang yoki diskni kengaytiring."
    )
    return _dispatch("disk", text)


def notify_db_down(error: str) -> bool:
    """Ma'lumotlar bazasiga ulanish uzilganda xabar beradi."""
    text = (
        "BM OGOHLANTIRISH: ma'lumotlar bazasi\n"
        f"Ulanish xatosi: {(error or 'noma lum')[:300]}\n"
        "Dashboard PostgreSQL holatini tekshiring (systemctl status postgresql)."
    )
    return _dispatch("db", text)


def notify_backup_stale(age_hours: float, last_run: str) -> bool:
    """Backup uzoq vaqt muvaffaqiyatli bajarilmaganda xabar beradi."""
    text = (
        "BM OGOHLANTIRISH: zaxira eskirgan\n"
        f"Oxirgi muvaffaqiyatli nusxa: {last_run or '?'} "
        f"({age_hours:.1f} soat oldin).\n"
        "Backup jadvali (timer/interval) va rezerva bazani tekshiring."
    )
    return _dispatch("backup_stale", text)


def reset_state() -> None:
    """Testlar uchun: throttle va hisoblagichlarni tozalaydi."""
    with _LOCK:
        _last_sent.clear()
        _fail_times.clear()


def set_sender(sender: Callable[[str], bool] | None) -> None:
    """Testlar uchun: yuboruvchini almashtiradi (None = haqiqiy Telegram)."""
    global _sender
    _sender = sender
