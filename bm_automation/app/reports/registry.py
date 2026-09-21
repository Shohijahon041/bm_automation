"""Operativ hisobotlar reyestri (endpoint va parametr tavsifi).

Hisobot nomi -> {yo'l, excel yo'li, talab qilinadigan parametrlar}
Qo'shimcha parametrlar: type (DAILY/WEEKLY/MONTHLY/YEARLY), date (YYYY-MM-DD),
regionId, orgId, hour.
"""

from __future__ import annotations

from ..config.settings import OPERATIVE_REPORT

REPORTS = {
    "bus-statistic": {
        "url": f"{OPERATIVE_REPORT}/report/bus/statistic",
        "excel": None,
        "params": ["type"],
    },
    "bus-region": {
        "url": f"{OPERATIVE_REPORT}/report/bus/region",
        "excel": f"{OPERATIVE_REPORT}/report/bus/region/excel",
        "params": ["type", "regionId"],
    },
    "bus-org": {
        "url": f"{OPERATIVE_REPORT}/report/bus/org",
        "excel": f"{OPERATIVE_REPORT}/report/bus/org/excel",
        "params": ["type", "regionId", "orgId"],
    },
    "bus-atv": {
        "url": f"{OPERATIVE_REPORT}/report/bus/atv",
        "excel": f"{OPERATIVE_REPORT}/report/bus/atv/excel",
        "params": ["type", "orgId"],
    },
    "route-statistic": {
        "url": f"{OPERATIVE_REPORT}/report/route/statistic",
        "excel": None,
        "params": ["type", "date"],
    },
    "route-region": {
        "url": f"{OPERATIVE_REPORT}/report/route/region",
        "excel": f"{OPERATIVE_REPORT}/report/route/region/excel",
        "params": ["type", "date", "regionId"],
    },
    "route-org": {
        "url": f"{OPERATIVE_REPORT}/report/route/org",
        "excel": f"{OPERATIVE_REPORT}/report/route/org/excel",
        "params": ["type", "date", "regionId"],
    },
    "distance-region": {
        "url": f"{OPERATIVE_REPORT}/report/distance/region",
        "excel": f"{OPERATIVE_REPORT}/report/distance/region/excel",
        "params": ["type", "date", "regionId"],
    },
    "distance-org": {
        "url": f"{OPERATIVE_REPORT}/report/distance/org",
        "excel": f"{OPERATIVE_REPORT}/report/distance/org/excel",
        "params": ["type", "date", "regionId"],
    },
    "trip-region": {
        "url": f"{OPERATIVE_REPORT}/report/trip/region",
        "excel": f"{OPERATIVE_REPORT}/report/trip/region/excel",
        "params": ["type", "date", "regionId"],
    },
    "trip-org": {
        "url": f"{OPERATIVE_REPORT}/report/trip/org",
        "excel": f"{OPERATIVE_REPORT}/report/trip/org/excel",
        "params": ["type", "date", "regionId"],
    },
    "gps-statistics": {
        "url": f"{OPERATIVE_REPORT}/gps-statistics",
        "excel": f"{OPERATIVE_REPORT}/gps-statistics/download-excel",
        "params": ["type", "date"],
    },
    "gps-signal-report": {
        "url": f"{OPERATIVE_REPORT}/gps-signal/report",
        "excel": f"{OPERATIVE_REPORT}/gps-signal/report/excel",
        "params": ["type", "date"],
    },
    "pflow-by-vehicle": {
        "url": f"{OPERATIVE_REPORT}/pflow-analyze/by-vehicle",
        "excel": f"{OPERATIVE_REPORT}/pflow-analyze/by-vehicle/download-excel",
        "params": ["type", "date"],
    },
    "pflow-by-trip": {
        "url": f"{OPERATIVE_REPORT}/pflow-analyze/by-trip",
        "excel": f"{OPERATIVE_REPORT}/pflow-analyze/by-trip/download-excel",
        "params": ["type", "date"],
    },
}

VALID_TYPES = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")


def report_names() -> list[str]:
    return sorted(REPORTS)


def has_excel(name: str) -> bool:
    return bool(REPORTS.get(name, {}).get("excel"))
