"""DB qatlami modellari: enums, dataclass'lar va vaqt yordamchilari.

Har bir saqlanadigan obyekt `id`, `created_at`, `updated_at` maydonlariga ega.
`TripStatus` — trip holati enum'i, `trip_statuses` jadvaliga seed qilinadi.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ..utils.km import trip_km


def now_utc() -> str:
    """ISO-8601 UTC timestamp (saqlash uchun, mikrosekund aniqlikda)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def json_dumps(data: Any) -> str:
    """dict/list ni JSON qatorga aylantiradi (non-serializable xavfsiz)."""
    if data is None:
        return "{}"
    return json.dumps(data, ensure_ascii=False, default=str)


def json_loads(raw: str | None) -> Any:
    """JSON qatorni obyektga aylantiradi; buzilgan bo'lsa dict qaytaradi."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def params_hash(params: dict | None) -> str:
    """Hisobot parametrlari uchun barqaror kalit (idempotent dublikat uchun)."""
    payload = json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class TripStatus(str, Enum):
    """Trip holati — BM waybill filtri bilan sinxron qiymatlar."""

    ACCEPTED = "ACCEPTED"
    NOT_ACCEPTED = "NOT_ACCEPTED"
    PENDING_ACCESS = "PENDING_ACCESS"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ZERO_MILEAGE = "ZERO_MILEAGE"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]

    @classmethod
    def is_valid(cls, value: str | None) -> bool:
        return value in cls.values()

    @classmethod
    def normalize(cls, value: str | None) -> str:
        """Noma'lum qiymatni xavfsiz normallashtiradi (PENDING_ACCESS sukut)."""
        if not value:
            return cls.PENDING_ACCESS.value
        return value if value in cls.values() else cls.PENDING_ACCESS.value


class SyncSource(str, Enum):
    """Trip `source` maydoni — ma'lumot qaysi API'dan kelgan."""

    DUTY = "DUTY"
    GROSS_TRIP = "GROSS_TRIP"
    WAYBILL = "WAYBILL"
    MANUAL = "MANUAL"


@dataclass
class TripRecord:
    """Trip — idempotent upsert uchun tayyor dataclass."""

    date: str
    route_id: str
    vehicle_id: str = ""
    driver_id: str = ""
    planned_time: str = ""
    actual_time: str = ""
    status: str = TripStatus.PENDING_ACCESS.value
    source: str = SyncSource.DUTY.value
    last_synced_at: str = field(default_factory=now_utc)
    data: dict = field(default_factory=dict)

    def unique_key(self) -> tuple:
        """Qaysi (date, route, vehicle, driver, planned_time) — dublikat kaliti."""
        return (self.date, self.route_id, self.vehicle_id,
                self.driver_id, self.planned_time or "")

    def to_row(self) -> dict:
        return {
            "date": self.date,
            "route_id": self.route_id,
            "vehicle_id": self.vehicle_id,
            "driver_id": self.driver_id,
            "planned_time": self.planned_time or "",
            "actual_time": self.actual_time or "",
            "status": TripStatus.normalize(self.status),
            "source": self.source,
            "last_synced_at": self.last_synced_at,
            "distance_km": round(trip_km(self.data), 2),
            "data": json_dumps(self.data),
        }


@dataclass
class SyncResult:
    """Bitta sync natijasi — CLI/log uchun."""

    entity: str
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    total: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "entity": self.entity,
            "inserted": self.inserted,
            "updated": self.updated,
            "deleted": self.deleted,
            "total": self.total,
            "error": self.error,
        }


# Jadval ro'yxati (schema.py bilan mos) — CLI/status uchun.
TABLES = [
    "profiles",
    "routes",
    "vehicles",
    "drivers",
    "driver_profiles",
    "driver_work_logs",
    "driver_fines",
    "duties",
    "schedules",
    "waybills",
    "trips",
    "route_daily",
    "trip_statuses",
    "reports",
    "report_runs",
    "errors",
    "notifications",
    "automation_runs",
    "dispatcher_routes",
    "documents",
    "sms_log",
    "sms_route_flags",
    "avans",
    "staff",
    "driver_appeals",
    "dashboard_users",
]
