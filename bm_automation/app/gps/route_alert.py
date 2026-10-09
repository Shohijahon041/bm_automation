"""Yo'nalish yo'lagi nazorati: avtobus marshrutdan chiqsa ogohlantirish.

Yo'nalish (route) uchun DB'da saqlangan polyline ("yo'lak") bo'ladi. Har bir
pozitsiya (listener'dan, geofence/speed bilan bir yo'lakay) yo'lakdan
`tolerance_m` metrdan uzoqlashsa → `route_deviation` hodisasi:
  1. yo'lak eng yaqin segmentigacha masofa hisoblanadi
  2. debounce: shu (imei, route) uchun cooldown oralig'ida bitta alert
  3. gps_alerts'ga yoziladi + Telegram'ga yuboriladi (sozlangan bo'lsa)

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

# (imei, route_id) -> oxirgi alert epoch (debounce)
_last_alert: dict[tuple[str, str], float] = {}
_lock = threading.Lock()

# imei -> (route_id, epoch) cache (bog'lash kam o'zgaradi)
_route_cache: dict[str, tuple[str, float]] = {}
_ROUTE_TTL_S = 60.0


# ------------------------------------------------------------ geometriya

def point_segment_distance_m(plat: float, plng: float, alat: float,
                             alng: float, blat: float, blng: float) -> float:
    """Nuqtadan (a→b) segmentgacha masofa (metr, lokal tekis proyeksiya)."""
    kx = math.cos(math.radians(plat)) * 111320.0
    ky = 111320.0
    ax = (alng - plng) * kx
    ay = (alat - plat) * ky
    bx = (blng - plng) * kx
    by = (blat - plat) * ky
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(ax, ay)
    t = -(ax * dx + ay * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(ax + t * dx, ay + t * dy)


def point_to_polyline_m(lat: float, lng: float, points: list) -> float:
    """Nuqtadan polyline'gacha eng qisqa masofa (metr)."""
    pts = _clean(points)
    if not pts:
        return float("inf")
    if len(pts) == 1:
        return point_segment_distance_m(
            lat, lng, pts[0][0], pts[0][1], pts[0][0], pts[0][1])
    best = float("inf")
    for i in range(1, len(pts)):
        d = point_segment_distance_m(
            lat, lng, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        if d < best:
            best = d
    return best


def _clean(points: list) -> list:
    out = []
    for p in points or []:
        try:
            la, ln = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append([la, ln])
    return out


def simplify_path(points: list, tolerance_m: float) -> list:
    """Douglas–Peucker: yo'lakni tolerantlik ichida soddalashtiradi."""
    pts = _clean(points)
    if len(pts) <= 2:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        md, idx = -1.0, -1
        for i in range(a + 1, b):
            d = point_segment_distance_m(
                pts[i][0], pts[i][1],
                pts[a][0], pts[a][1], pts[b][0], pts[b][1])
            if d > md:
                md, idx = d, i
        if md > tolerance_m and idx > a:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [pts[i] for i in range(len(pts)) if keep[i]]


# --------------------------------------------------------------- holat

def _route_for(imei: str) -> str:
    now = time.time()
    cached = _route_cache.get(imei)
    if cached and now - cached[1] < _ROUTE_TTL_S:
        return cached[0]
    rid = ""
    try:
        rows = storage._st().db.query(
            "SELECT v.route_id AS route_id FROM gps_devices d "
            "JOIN vehicles v ON v.external_id = d.vehicle_id "
            "WHERE d.imei = %s", (imei,), limit=1,
        )
        if rows:
            rid = str(rows[0].get("route_id") or "")
    except Exception:  # noqa: BLE001
        pass
    _route_cache[imei] = (rid, now)
    return rid


def check_position(imei: str, lat: float, lng: float, speed: int = 0,
                   ts: datetime | None = None) -> None:
    """Bitta pozitsiyani yo'nalish yo'lagi bilan tekshiradi."""
    try:
        route_id = _route_for(imei)
    except Exception:  # noqa: BLE001
        log.exception("route_alert: route aniqlanmadi (imei=%s)", imei)
        return
    if not route_id:
        return
    try:
        path = storage.get_route_path(route_id)
    except Exception:  # noqa: BLE001
        log.exception("route_alert: yo'lak olinmadi (route=%s)", route_id)
        return
    if not path or not path.get("enabled"):
        return
    pts = _clean(path.get("points") or [])
    if len(pts) < 2:
        return
    dist = point_to_polyline_m(lat, lng, pts)
    tol = float(path.get("tolerance_m") or config.ROUTE_DEVIATION_TOLERANCE_M)
    if dist <= tol:
        return
    _raise_deviation(imei, route_id, path, dist, tol, lat, lng, speed, ts)


def _raise_deviation(imei: str, route_id: str, path: dict, dist: float,
                     tol: float, lat: float, lng: float, speed: int,
                     ts: datetime | None) -> None:
    key = (imei, route_id)
    now = time.time()
    with _lock:
        if now - _last_alert.get(key, 0.0) < config.ROUTE_DEVIATION_COOLDOWN_S:
            return
        _last_alert[key] = now

    vehicle_id = ""
    try:
        from .geofence import _vehicle_for

        vehicle_id = _vehicle_for(imei)
    except Exception:  # noqa: BLE001
        pass

    name = str(path.get("name") or route_id or "")
    msg = (
        f"🛣 <b>Marshrutdan chiqish</b>\n"
        f"Yo'nalish: <b>{name}</b>\n"
        f"Qurilma: <code>{imei}</code>"
        + (f"\nAvtobus: <b>{vehicle_id}</b>" if vehicle_id else "")
        + f"\nMasofa: {dist:.0f} m (yo'lak {tol:.0f} m)"
        + f"\nTezlik: {speed} km/h"
        + f"\nJoy: {lat:.5f}, {lng:.5f}"
    )

    try:
        storage.insert_alert(
            imei=imei,
            vehicle_id=vehicle_id,
            kind="route_deviation",
            fence_id=0,
            message=f"{name}: {dist:.0f} m",
        )
    except Exception:  # noqa: BLE001
        log.exception("route_alert: alert yozilmadi (imei=%s)", imei)

    if config.ROUTE_DEVIATION_TELEGRAM:
        try:
            from ..notifications.telegram import send_message

            ok = send_message(msg)
            if not ok:
                log.warning("route_alert: telegram yuborilmadi")
        except Exception:  # noqa: BLE001
            log.exception("route_alert: telegram xatosi (davom etamiz)")

    log.info("route_deviation: imei=%s route=%s dist=%.0fm",
             imei, route_id, dist)


# ---------------------------------------------------------- o'rganish

def learn_route_path(route_id: str, date_from: str = "", date_to: str = "",
                     imei: str = "", simplify_m: int | None = None) -> list:
    """Avtobus harakat tarixidan yo'lakni o'rganadi (eng boy kun).

    Har bir (qurilma, kun) uchun track olinadi, soddalashtiriladi va eng
    ko'p nuqtali track yo'lak sifatida qaytariladi.
    """
    tol = simplify_m if simplify_m is not None else config.ROUTE_LEARN_SIMPLIFY_M
    best: list = []
    try:
        devs = storage.list_devices()
    except Exception:  # noqa: BLE001
        log.exception("route_alert: qurilmalar olinmadi")
        return best
    for d in devs:
        if str(d.get("route_id") or "") != route_id:
            continue
        if imei and d.get("imei") != imei:
            continue
        try:
            pts = storage.positions(
                imei=str(d.get("imei") or ""), date_from=date_from,
                date_to=date_to, limit=50000)
        except Exception:  # noqa: BLE001
            log.exception("route_alert: pozitsiyalar olinmadi (%s)", d.get("imei"))
            continue
        by_day: dict[str, list] = {}
        for p in pts:
            by_day.setdefault(str(p.get("ts") or "")[:10], []).append(p)
        for seq in by_day.values():
            coords = [[p.get("lat"), p.get("lng")] for p in seq
                      if p.get("lat") is not None and p.get("lng") is not None]
            if len(coords) < 2:
                continue
            simp = simplify_path(coords, tol)
            if len(simp) > len(best):
                best = simp
    return best
