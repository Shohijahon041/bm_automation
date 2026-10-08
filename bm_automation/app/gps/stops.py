"""To'xtashlarni aniqlash — traektoriyadan "pauza" epizodlari.

Pozitsiyalar ketma-ketligidan bir joyda uzoq turgan epizodlar ajratiladi:
ketma-ket nuqtalar radius ichida qolsa guruh bo'ladi, davomiyligi
min_duration_s dan katta bo'lsa — to'xtash. Katta tanaffus (qurilma jim,
gap > _GAP_MAX_S) guruhni bo'ladi. Harakat nuqtalari (tezlik
_MOVE_SPEED_KMH dan yuqori) guruhga qo'shilmaydi — lekin radius/tanaffus
tekshiruvi ular uchun ham ishlaydi (shovqin bitta to'xtashni yo'qotmaydi).

`detect_stops` — sof funksiya (testlar uchun); `stops_for` — DB'dan
pozitsiya o'qib, geofence nomlari bilan belgilaydi.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import config, storage
from .geofence import haversine_m

log = logging.getLogger(__name__)

_GAP_MAX_S = 1500.0    # nuqtalar orasidagi tanaffus: undan katta — guruh bo'linadi
_MOVE_SPEED_KMH = 15   # shu tezlikdan yuqori nuqta "harakat" hisoblanadi


def _ts_of(v) -> datetime:
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def detect_stops(positions: list, radius_m: int | None = None,
                 min_duration_s: int | None = None) -> list[dict]:
    """Pozitsiyalardan to'xtash epizodlarini ajratadi (ts bo'yicha tartibda).

    Qaytaradi: [{start, end, duration_s, lat, lng, points, max_speed,
    ignition_off}, ...] — start/end ISO satr.
    """
    radius = float(radius_m if radius_m is not None else config.STOPS_RADIUS_M)
    min_s = float(
        min_duration_s if min_duration_s is not None
        else config.STOPS_MIN_DURATION_S
    )
    out: list[dict] = []
    run: list[dict] = []
    center: tuple[float, float] | None = None

    for p in positions:
        try:
            lat, lng = float(p["lat"]), float(p["lng"])
            ts = _ts_of(p["ts"])
        except Exception:  # noqa: BLE001
            continue
        if run and center is not None:
            gap = (ts - run[-1]["_ts"]).total_seconds()
            dist = haversine_m(lat, lng, center[0], center[1])
            if gap > _GAP_MAX_S or dist > radius:
                _close_run(run, out, min_s)
                run, center = [], None
        if int(p.get("speed") or 0) > _MOVE_SPEED_KMH:
            continue  # harakat nuqtasi — guruhga kirmaydi (chegara tekshirildi)
        if not run:
            center = (lat, lng)
        run.append({**p, "_lat": lat, "_lng": lng, "_ts": ts})

    _close_run(run, out, min_s)
    return out


def _close_run(run: list[dict], out: list[dict], min_s: float) -> None:
    """Guruhni tekshirib, to'xtash bo'lsa out'ga qo'shadi."""
    if len(run) < 2:
        return
    dur = (run[-1]["_ts"] - run[0]["_ts"]).total_seconds()
    if dur < min_s:
        return
    speeds = [int(r.get("speed") or 0) for r in run]
    lat = round(sum(r["_lat"] for r in run) / len(run), 6)
    lng = round(sum(r["_lng"] for r in run) / len(run), 6)
    ign_off = sum(1 for r in run if int(r.get("ignition") or 0) == 0)
    out.append({
        "start": run[0]["_ts"].isoformat(),
        "end": run[-1]["_ts"].isoformat(),
        "duration_s": int(dur),
        "lat": lat,
        "lng": lng,
        "points": len(run),
        "max_speed": max(speeds or [0]),
        "ignition_off": ign_off * 2 >= len(run),  # ko'p qismida dvigatel o'chgan
    })


def stops_for(imei: str, date_from: str = "", date_to: str = "",
              min_s: int | None = None, radius_m: int | None = None,
              limit: int = 20000) -> list[dict]:
    """Qurilmaning davr bo'yicha to'xtashlari (geofence nomi bilan)."""
    pts = storage.positions(imei=imei, date_from=date_from,
                            date_to=date_to, limit=limit)
    stops = detect_stops(pts, radius_m=radius_m, min_duration_s=min_s)
    _label_fences(stops, imei)
    return stops


def _label_fences(stops: list[dict], imei: str) -> None:
    """To'xtash markazi geofence ichida bo'lsa — zona nomini yozadi."""
    if not stops:
        return
    try:
        vid = storage._vehicle_id_for(imei)
        fences = [
            f for f in storage.list_geofences(only_enabled=True)
            if not str(f.get("vehicle_id") or "")
            or str(f.get("vehicle_id")) == vid
        ]
    except Exception:  # noqa: BLE001
        log.exception("stops: geofence'lar olinmadi (imei=%s)", imei)
        return
    for s in stops:
        best: tuple[str, float] | None = None
        for f in fences:
            try:
                d = haversine_m(s["lat"], s["lng"],
                                float(f["lat"]), float(f["lng"]))
            except Exception:  # noqa: BLE001
                continue
            if d <= float(f.get("radius_m") or 0) and (
                    best is None or d < best[1]):
                best = (str(f.get("name") or ""), d)
        if best:
            s["fence"] = best[0]
