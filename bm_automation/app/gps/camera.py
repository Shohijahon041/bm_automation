"""Kamera GPS ↔ Teltonika GPS solishtirmasi (yordamchi tahlil).

4G kamera (JT808/JT1078) o'z GPS joyini ham yuboradi, lekin **xarita, trek
va nazorat faqat Teltonika FMB920 dan olinadi**. Kamera GPS faqat YORDAMCHI:

  * kamera qaysi avtobusda ekanini aniqlash/taklif qilish (yaqinlik bo'yicha);
  * bog'langan avtobusda kamera GPS Teltonika'dan uzoqlashsa — o'rnatish
    yoki bog'lash xatosi haqida ogohlantirish (``drift``).

Bu modul hech narsa YOZMAYDI — faqat mavjud jadvallarni o'qiydi.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from ..db.storage import get_storage
from . import config, storage

log = logging.getLogger(__name__)

# status → o'zbekcha izoh (UI va Telegram uchun)
STATUS_TEXT = {
    "ok": "GPS mos",
    "drift": "GPS farqi katta — o'rnatish/bog'lashni tekshiring",
    "suggest": "Yaqin avtobus topildi — bog'lash tavsiya etiladi",
    "unbound": "Avtobusga bog'lanmagan",
    "no_camera_gps": "Kamera GPS yubormayapti",
    "no_teltonika": "Avtobusda Teltonika GPS yo'q",
    "no_position": "Joy ma'lum emas",
}


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Ikki nuqta orasidagi masofa (metr)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def _latlng(row: dict):
    lat, lng = row.get("last_lat"), row.get("last_lng")
    if lat is None or lng is None:
        return None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


def _age_s(row: dict) -> float | None:
    ls = row.get("last_seen")
    if not ls:
        return None
    try:
        ts = ls if isinstance(ls, datetime) else datetime.fromisoformat(
            str(ls).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:  # noqa: BLE001
        return None


def _teltonika_rows() -> list:
    """Haqiqiy GPS qurilmalari (kind='teltonika'), joyi ma'lum bo'lganlari."""
    try:
        rows = storage.teltonika_only(storage.list_devices())
    except Exception:  # noqa: BLE001
        log.exception("camera: Teltonika qurilmalari olinmadi")
        return []
    return [r for r in rows if _latlng(r)]


def assist(threshold_m: int | None = None) -> list[dict]:
    """Har bir kamera uchun Teltonika bilan solishtirma natijasi.

    Qaytadi: ro'yxat (kamera bo'yicha), har birida:

    ``phone``, ``vehicle_id``, ``plate``, ``last_seen_s``, ``online``,
    ``lat``/``lng`` (kamera joyi), ``tel_lat``/``tel_lng`` (Teltonika joyi),
    ``dist_m`` (ikonkalar orasi), ``status`` (yuqoridagi kalitlar),
    ``status_text``, ``suggest_vehicle_id``/``suggest_plate`` (bog'lanmagan
    kamera yaqin avtobus topilsa).
    """
    thr = int(threshold_m if threshold_m is not None
              else config.GPS_CAMERA_MATCH_M)
    try:
        cams = storage.list_camera_devices()
    except Exception:  # noqa: BLE001
        log.exception("camera: kameralar olinmadi")
        return []
    trows = _teltonika_rows()

    # Bog'langan avtobus → uning Teltonika qurilmasi
    by_vehicle: dict[str, dict] = {}
    for t in trows:
        vid = str(t.get("vehicle_id") or "")
        if vid and vid not in by_vehicle:
            by_vehicle[vid] = t

    out: list[dict] = []
    for c in cams:
        phone = str(c.get("imei") or "")
        vid = str(c.get("vehicle_id") or "")
        cll = _latlng(c)
        age = _age_s(c)
        rec = {
            "phone": phone,
            "name": str(c.get("name") or ""),
            "vehicle_id": vid,
            "plate": str(c.get("plate_number") or ""),
            "online": bool(c.get("online")),
            "last_seen_s": age,
            "lat": cll[0] if cll else None,
            "lng": cll[1] if cll else None,
            "tel_lat": None,
            "tel_lng": None,
            "dist_m": None,
            "suggest_vehicle_id": "",
            "suggest_plate": "",
            "suggest_dist_m": None,
        }
        t = by_vehicle.get(vid) if vid else None
        if t:
            rec["tel_lat"], rec["tel_lng"] = _latlng(t)

        if not cll:
            status = "no_camera_gps" if vid else "no_position"
        elif vid and t:
            dist = _haversine(cll[0], cll[1], rec["tel_lat"], rec["tel_lng"])
            rec["dist_m"] = round(dist, 1)
            status = "ok" if dist <= thr else "drift"
        elif vid and not t:
            status = "no_teltonika"
        else:
            # Bog'lanmagan: eng yaqin Teltonika qurilmasini qidiramiz.
            best, best_d = None, None
            for cand in trows:
                cll2 = _latlng(cand)
                d = _haversine(cll[0], cll[1], cll2[0], cll2[1])
                if best_d is None or d < best_d:
                    best, best_d = cand, d
            if best is not None and best_d is not None and best_d <= thr:
                rec["suggest_vehicle_id"] = str(best.get("vehicle_id") or "")
                rec["suggest_plate"] = str(best.get("plate_number") or "")
                rec["suggest_dist_m"] = round(best_d, 1)
                rec["tel_lat"], rec["tel_lng"] = _latlng(best)
                status = "suggest"
            else:
                status = "unbound"

        rec["status"] = status
        rec["status_text"] = STATUS_TEXT.get(status, status)
        out.append(rec)

    out.sort(key=lambda r: r["phone"])
    return out
