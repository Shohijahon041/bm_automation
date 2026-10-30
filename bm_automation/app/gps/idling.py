"""Bekorchi ishlash (idling) nazorati: dvigatel yoniq, lekin harakat yo'q.

Har bir pozitsiya uchun (listener'dan, geofence/speed bilan bir yo'lakay):
  1. dvigatel yoniq (ignition=1) va tezlik ~0 bo'lsa — "idling" epizodi ochiladi
  2. epizod `IDLE_MIN_S` dan uzoq davom etsa → IDLING hodisasi (bir marta)
  3. harakat boshlansa yoki dvigatel o'chsa — epizod yopiladi
  4. gps_alerts'ga yoziladi + Telegram'ga yuboriladi (sozlangan bo'lsa)

Telegram xatosi hech qachon oqimni buzmaydi (fail-safe).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import config, storage

log = logging.getLogger(__name__)

# imei -> {"since": epoch|None, "alerted": bool}
_state: dict[str, dict] = {}
_lock = threading.Lock()


def _epoch(ts: datetime | None) -> float:
    """datetime (yoki None) → epoch sekund."""
    if ts is None:
        return time.time()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def check_position(imei: str, lat: float, lng: float, speed: int,
                   ignition: int, ts: datetime | None = None) -> None:
    """Bitta pozitsiyada idling epizodini kuzatadi."""
    idle = bool(ignition) and int(speed or 0) <= config.IDLE_MAX_SPEED_KMH
    now = _epoch(ts)
    with _lock:
        st = _state.setdefault(imei, {"since": None, "alerted": False})
        if not idle:
            st["since"] = None
            st["alerted"] = False
            return
        if st["since"] is None:
            st["since"] = now
            st["alerted"] = False
            return
        duration = now - st["since"]
        if duration < config.IDLE_MIN_S or st["alerted"]:
            return
        st["alerted"] = True
        since = st["since"]
    _raise_idle(imei, lat, lng, speed, since, now)


def _raise_idle(imei: str, lat: float, lng: float, speed: int,
                since: float, now: float) -> None:
    mins = int((now - since) / 60)
    vehicle_id = ""
    try:
        from .geofence import _vehicle_for

        vehicle_id = _vehicle_for(imei)
    except Exception:  # noqa: BLE001
        pass

    nm = vehicle_id or imei
    msg = (
        f"🅿️ <b>Bekorchi ishlash (idling)</b>\n"
        f"Avtobus: <b>{nm}</b>\n"
        f"Davomiylik: <b>{mins} daqiqa</b> (dvigatel yoniq)\n"
        f"Tezlik: {int(speed or 0)} km/h"
        f"\nJoy: {lat:.5f}, {lng:.5f}"
    )

    try:
        storage.insert_alert(
            imei=imei,
            vehicle_id=vehicle_id,
            kind="idling",
            fence_id=0,
            message=f"{mins} daqiqa bekorchi ish",
        )
    except Exception:  # noqa: BLE001
        log.exception("idling: alert yozilmadi (imei=%s)", imei)

    if config.IDLE_TELEGRAM:
        try:
            from ..notifications.telegram import send_message

            ok = send_message(msg)
            if not ok:
                log.warning("idling: telegram yuborilmadi (configured emas?)")
        except Exception:  # noqa: BLE001
            log.exception("idling: telegram xatosi (davom etamiz)")

    log.info("idling: imei=%s davomiylik=%s daq", imei, mins)
