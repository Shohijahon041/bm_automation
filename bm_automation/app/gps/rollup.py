"""Kunlik rollup: gps_positions → gps_daily (arzon tarix).

Har intervalda oxirgi N kun qayta hisoblanadi (idempotent upsert):
masofa (haversine), maks/ortacha tezlik, nuqtalar, harakat vaqti.
Rollup qilingan kunlar retention tozalashidan keyin ham qoladi —
tarix uchun gps_daily yetarli, siqiq pozitsiyalar eskiygach o'chadi.

Fail-safe: hech qachon exception otmaydi (fon tredi).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from ..db.storage import get_storage
from . import config

log = logging.getLogger(__name__)


def _st():
    return get_storage()


def _haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def rollup_day(day: datetime) -> int:
    """Bitta UTC kun uchun yig'amani hisoblab gps_daily'ga yozadi.

    day — kun boshlanishi (UTC). Qaytaradi: yozilgan qurilmalar soni.
    """
    st = _st()
    if not st.enabled:
        return 0
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    rows = st.db.query(
        """
        SELECT imei, ts, lat, lng, speed, movement
        FROM gps_positions
        WHERE ts >= %s AND ts < %s
        ORDER BY imei, ts ASC
        """,
        (start, end),
    )
    by_imei: dict[str, list] = {}
    for r in rows:
        by_imei.setdefault(str(r["imei"]), []).append(r)

    n = 0
    for imei, pts in by_imei.items():
        dist = 0.0
        for a, b in zip(pts, pts[1:]):
            dist += _haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        speeds = [int(r.get("speed") or 0) for r in pts]
        moving = sum(
            1 for r in pts
            if int(r.get("speed") or 0) > 3 or int(r.get("movement") or 0) == 1
        )
        st.db.execute(
            """
            INSERT INTO gps_daily
                (imei, day, points, distance_m, max_speed, avg_speed,
                 moving_points, first_ts, last_ts)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (imei, day) DO UPDATE SET
                points = EXCLUDED.points,
                distance_m = EXCLUDED.distance_m,
                max_speed = EXCLUDED.max_speed,
                avg_speed = EXCLUDED.avg_speed,
                moving_points = EXCLUDED.moving_points,
                first_ts = EXCLUDED.first_ts,
                last_ts = EXCLUDED.last_ts
            """,
            (
                imei, start.date(), len(pts), round(dist, 1),
                max(speeds) if speeds else 0,
                round(sum(speeds) / len(speeds), 1) if speeds else 0,
                moving, pts[0]["ts"], pts[-1]["ts"],
            ),
        )
        n += 1
    return n


def rollup_recent(days: int | None = None) -> int:
    """Oxirgi N kunni qayta hisoblaydi (standart: config.ROLLUP_DAYS)."""
    n_days = days or config.ROLLUP_DAYS
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                               microsecond=0)
    total = 0
    for i in range(n_days):
        try:
            total += rollup_day(today - timedelta(days=i))
        except Exception:  # noqa: BLE001
            log.exception("rollup: kun xatosi (%s)", today - timedelta(days=i))
    return total


def start_monitor() -> None:
    """Fon tredi: davriy rollup (standart: har 30 daqiqada)."""
    if not config.ROLLUP_ENABLED:
        log.info("rollup o'chirilgan (GPS_ROLLUP_ENABLED=0)")
        return

    def _loop() -> None:
        # birinchi hisobkitob boshlang'ich yuklamaga to'siq bo'lmasin
        time.sleep(20)
        while True:
            try:
                n = rollup_recent()
                if n:
                    log.info("rollup: %s qurilma/kun yozildi", n)
            except Exception:  # noqa: BLE001
                log.exception("rollup monitor xatosi")
            time.sleep(config.ROLLUP_CHECK_S)

    threading.Thread(target=_loop, daemon=True, name="bm-gps-rollup").start()
