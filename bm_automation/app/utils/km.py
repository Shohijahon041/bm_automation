"""Trip payloadidan km olish — umumiy yordamchi.

BM endpointlari turli davrlarda masofani turlicha nomlagan: ``distanceKm``,
``mileage``, ``gpsOdo`` (GPS odometr), ``routeOdo`` (yo'nalish masofasi).
Waybill qatorlari masofani ``gpsOdo``/``routeOdo`` da beradi; yig'ma
maydonlar (``distanceFactSum`` kabi) har bir reysga qo'shilmasligi uchun
faqat bitta reysga tegishli kalitlar olinadi.
"""

from __future__ import annotations

import json
import re
from typing import Any

TRIP_KM_KEYS = (
    "distanceKm", "distance_km", "mileageKm", "mileage_km", "mileage",
    "distanceFact", "factDistance", "fact_distance", "km", "gpsOdo", "routeOdo",
)


def _loads(raw) -> dict:
    """JSON qatorni obyektga aylantiradi; buzilgan bo'lsa bo'sh dict."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def to_float(value: Any) -> float:
    """API dan kelgan sonni xavfsiz floatga aylantiradi."""
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    raw = str(value).strip().replace(" ", "").replace(",", ".")
    # "123.5 km" kabi obyektlarda faqat son qismi olinadi.
    match = re.search(r"\d+(?:\.\d+)?", raw)
    try:
        return max(float(match.group(0)), 0.0) if match else 0.0
    except (TypeError, ValueError):
        return 0.0


def trip_km(raw: Any) -> float:
    """Trip payloadidan (mavjud bo'lsa) bosib o'tilgan km ni oladi."""
    data = _loads(raw) if isinstance(raw, str) else (raw or {})
    if not isinstance(data, dict):
        return 0.0
    for key in TRIP_KM_KEYS:
        if key in data:
            return to_float(data[key])
    return 0.0
