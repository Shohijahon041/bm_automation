"""Geofence nazorati: qurilma doira zonadan chiqib ketsa ogohlantirish.

Har bir pozitsiya uchun (listener'dan):
  1. qurilmaga tegishli yoqilgan geofence'lar olinadi
  2. haversine masofa radiusdan katta bo'lsa → EXIT hodisasi
  3. debounce: oxirgi alertdan keyin min interval bo'lmasa yozmaymiz
  4. gps_alerts'ga yoziladi + Telegram'ga yuboriladi (sozlangan bo'lsa)

Telegram xatosi hech qachon oqimni buzmaydi (fail-safe).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone

from . import config, storage

log = logging.getLogger(__name__)

# (imei, fence_id) -> oxirgi alert epoch (debounce)
_last_alert: dict[tuple[str, int], float] = {}
_lock = threading.Lock()

# zonada bo'lmagan qurilmalar uchun negative-cache (har pozitsiyada DB'ga
# so'rov ketmasligi uchun) — (imei) -> oxirgi tekshiruv epoch
_last_check: dict[str, float] = {}
_CHECK_COOLDOWN_S = 5.0  # bir qurilma uchun geofence so'rovi oralig'i


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# vehicle_id cache (bog'lash kam o'zgaradi): imei -> (vehicle_id, epoch)
_vehicle_cache: dict[str, tuple[str, float]] = {}
_VEHICLE_TTL_S = 60.0


def _vehicle_for(imei: str) -> str:
    now = time.time()
    cached = _vehicle_cache.get(imei)
    if cached and now - cached[1] < _VEHICLE_TTL_S:
        return cached[0]
    vid = ""
    try:
        rows = storage._st().db.query(
            "SELECT vehicle_id FROM gps_devices WHERE imei = %s", (imei,),
            limit=1,
        )
        if rows:
            vid = str(rows[0].get("vehicle_id") or "")
    except Exception:  # noqa: BLE001
        pass
    _vehicle_cache[imei] = (vid, now)
    return vid


def _fences_for(imei: str, vehicle_id: str) -> list:
    """Qurilmaga tegishli yoqilgan geofence'lar (umumiy + shu vehicle)."""
    now = time.time()
    with _lock:
        if now - _last_check.get(imei, 0.0) < _CHECK_COOLDOWN_S:
            return []
        _last_check[imei] = now
    fences = storage.list_geofences(only_enabled=True)
    return [
        f for f in fences
        if not str(f.get("vehicle_id") or "") or f.get("vehicle_id") == vehicle_id
    ]


def check_position(imei: str, lat: float, lng: float,
                   ts: datetime | None = None) -> None:
    """Bitta pozitsiyani barcha tegishli geofence'lar bilan tekshiradi."""
    try:
        vehicle_id = _vehicle_for(imei)
        fences = _fences_for(imei, vehicle_id)
    except Exception:  # noqa: BLE001
        log.exception("geofence: fence'lar olinmadi (imei=%s)", imei)
        return
    if not fences:
        return
    for f in fences:
        try:
            dist = haversine_m(lat, lng, float(f["lat"]), float(f["lng"]))
            radius = float(f.get("radius_m") or 0)
            if dist <= radius:
                continue  # zona ichida — OK
            _raise_exit(imei, vehicle_id, f, dist, lat, lng, ts)
        except Exception:  # noqa: BLE001
            log.exception("geofence: fence %s tekshiruvi xatosi", f.get("id"))


def _raise_exit(imei: str, vehicle_id: str, fence: dict, dist: float,
                lat: float, lng: float, ts: datetime | None) -> None:
    fence_id = int(fence["id"])
    key = (imei, fence_id)
    now = time.time()
    with _lock:
        last = _last_alert.get(key, 0.0)
        if now - last < config.GEOFENCE_ALERT_COOLDOWN_S:
            return
        _last_alert[key] = now

    when = (ts or datetime.now(timezone.utc))
    msg = (
        f"🚨 <b>Geofence: zonadan chiqdi</b>\n"
        f"Zona: <b>{fence.get('name')}</b>\n"
        f"Qurilma: <code>{imei}</code>"
        + (f"\nAvtobus: <b>{vehicle_id}</b>" if vehicle_id else "")
        + f"\nMasofa: {dist:.0f} m (radius {fence.get('radius_m')} m)"
        + f"\nJoy: {lat:.5f}, {lng:.5f}"
    )

    try:
        storage.insert_alert(
            imei=imei,
            vehicle_id=vehicle_id,
            kind="geofence_exit",
            fence_id=fence_id,
            message=str(fence.get("name") or ""),
        )
    except Exception:  # noqa: BLE001
        log.exception("geofence: alert yozilmadi (imei=%s)", imei)

    if config.GEOFENCE_TELEGRAM:
        try:
            from ..notifications.telegram import send_message

            ok = send_message(msg)
            if not ok:
                log.warning("geofence: telegram yuborilmadi (configured emas?)")
        except Exception:  # noqa: BLE001
            log.exception("geofence: telegram xatosi (davom etamiz)")

    log.info(
        "geofence EXIT: imei=%s fence=%s dist=%.0fm",
        imei, fence_id, dist,
    )
