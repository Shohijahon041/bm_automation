"""Tezlik nazorati: qurilma belgilangan tezlikdan oshsa ogohlantirish.

Har bir pozitsiya uchun (listener'dan, geofence bilan bir yo'lakay):
  1. tezlik threshold'dan katta bo'lsa → OVERSPEED hodisasi
  2. debounce: bir qurilma uchun cooldown oralig'ida bitta alert
  3. gps_alerts'ga yoziladi + Telegram'ga yuboriladi (sozlangan bo'lsa)

Telegram xatosi hech qachon oqimni buzmaydi (fail-safe).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import config, storage

log = logging.getLogger(__name__)

# imei -> oxirgi alert epoch (debounce)
_last_alert: dict[str, float] = {}
_lock = threading.Lock()

# qisqa intervalda tezlik sakragan qurilmaga spam qilmaslik uchun
# oxirgi tekshiruvdan keyin threshold'ga yaqin bo'lsa ham kutamiz
_last_speed: dict[str, tuple[int, float]] = {}


def check_position(imei: str, lat: float, lng: float, speed: int,
                   ts: datetime | None = None) -> None:
    """Bitta pozitsiyaning tezligini tekshiradi (threshold > 0 bo'lsa)."""
    limit = config.SPEED_LIMIT_KMH
    if limit <= 0 or speed <= limit:
        return
    now = time.time()
    with _lock:
        last = _last_alert.get(imei, 0.0)
        if now - last < config.SPEED_ALERT_COOLDOWN_S:
            return
        _last_alert[imei] = now

    vehicle_id = ""
    try:
        from .geofence import _vehicle_for

        vehicle_id = _vehicle_for(imei)
    except Exception:  # noqa: BLE001
        pass

    when = ts or datetime.now(timezone.utc)
    nm = vehicle_id or imei
    msg = (
        f"⚡️ <b>Tezlik chegarasi oshdi</b>\n"
        f"Avtobus: <b>{nm}</b>\n"
        f"Tezlik: <b>{speed} km/h</b> (chegara {limit} km/h)\n"
        f"Joy: {lat:.5f}, {lng:.5f}"
    )

    try:
        storage.insert_alert(
            imei=imei,
            vehicle_id=vehicle_id,
            kind="overspeed",
            fence_id=0,
            message=f"{speed} km/h (chegara {limit})",
        )
    except Exception:  # noqa: BLE001
        log.exception("speed: alert yozilmadi (imei=%s)", imei)

    if config.SPEED_TELEGRAM:
        try:
            from ..notifications.telegram import send_message

            ok = send_message(msg)
            if not ok:
                log.warning("speed: telegram yuborilmadi (configured emas?)")
        except Exception:  # noqa: BLE001
            log.exception("speed: telegram xatosi (davom etamiz)")

    log.info("overspeed: imei=%s speed=%s (limit %s)", imei, speed, limit)
