"""Kunlik masofa hisoboti: gps_daily asosida Telegram'ga yuboriladi.

Ertalabki belgilangan vaqtda (standart: 07:00 Toshkent vaqti) fon tredi
kechagi/to'liq oxirgi kun yig'amalarini hisoblab, bitta hisobot yuboradi:

    🚌 Kunlik yurish hisoboti — 2026-09-24
    1. 01.240-AAB — 128.4 km · maks 82 km/h
    2. ...

Fail-safe: hech qachon exception otmaydi (fon tredi), Telegram
sozlanmagan bo'lsa hisobot faqat log'da qoladi.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from . import config, storage

log = logging.getLogger(__name__)

_TASHKENT = "Asia/Tashkent"


def _now_local() -> datetime:
    if ZoneInfo:
        return datetime.now(ZoneInfo(_TASHKENT))
    return datetime.now(timezone.utc) + timedelta(hours=5)


def _seconds_until_next(hour: int) -> float:
    now = _now_local()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def build_report(day=None) -> str:
    """Bir kun uchun hisobot matni (Telegram HTML). day: date | None = kecha."""
    d = day or (_now_local().date() - timedelta(days=1))
    rows = sorted(
        storage.daily(date_from=d.isoformat(), date_to=d.isoformat()),
        key=lambda r: float(r.get("distance_m") or 0),
        reverse=True,
    )
    head = f"🚌 <b>Kunlik yurish hisoboti — {d.isoformat()}</b>\n"
    if not rows:
        return head + "Ma'lumot yo'q — qurilmalar oqim yubormagan."
    lines = []
    for i, r in enumerate(rows, 1):
        nm = r.get("imei")
        try:
            rows_db = storage.list_devices()
        except Exception:  # noqa: BLE001
            rows_db = []
        for dv in rows_db:
            if dv.get("imei") == r.get("imei"):
                nm = dv.get("plate_number") or dv.get("name") or nm
                break
        km = float(r.get("distance_m") or 0) / 1000.0
        lines.append(
            f"{i}. <b>{nm}</b> — {km:.1f} km · "
            f"maks {int(r.get('max_speed') or 0)} km/h · "
            f"{int(r.get('points') or 0)} nuqta"
        )
    return head + "\n".join(lines)


def send_daily_report(day=None) -> bool:
    """Hisobotni Telegram'ga yuboradi. Yuborilgan bo'lsa True."""
    text = build_report(day)
    try:
        from ..notifications.telegram import send_message

        ok = send_message(text)
        if not ok:
            log.warning("report: telegram yuborilmadi (bot sozlanmagan?)")
        else:
            log.info("report: kunlik hisobot yuborildi")
        return bool(ok)
    except Exception:  # noqa: BLE001
        log.exception("report: telegram xatosi")
        return False


def start_monitor() -> None:
    """Fon tredi: har kuni REPORT_HOUR'da kechagi kun hisobotini yuboradi."""
    if not config.REPORT_ENABLED:
        log.info("report o'chirilgan (GPS_REPORT_ENABLED=0)")
        return

    def _loop() -> None:
        # birinchi sikl — ertaga ertalab; xohlaganda tekshirish uchun
        # delay kichik boshlanadi (dashbord startiga to'siq bo'lmasin)
        time.sleep(15)
        while True:
            try:
                wait_s = _seconds_until_next(config.REPORT_HOUR)
                log.info("report: keyingi kunlik hisobot %.0f soatdan keyin", wait_s / 3600)
                time.sleep(max(60.0, wait_s))
                send_daily_report()
            except Exception:  # noqa: BLE001
                log.exception("report monitor xatosi")
                time.sleep(600)

    threading.Thread(target=_loop, daemon=True, name="bm-gps-report").start()
