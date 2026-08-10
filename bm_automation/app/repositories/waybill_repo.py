"""Waybill (yo'l varaqalari) repository.

Frontend'dan qaytarilgan endpointlar:
    POST /brutto-management/api/v1/waybill            - hisobot ma'lumoti (JSON)
    GET  /brutto-management/api/v1/waybill/download   - Excel
    GET  /brutto-management/api/v1/waybill/stat/plan  - reja statistikasi
    POST /brutto-management/api/v1/waybill/v2         - temp variant

Izoh: max 7 kunlik oraliq. Eski sanalarda backend DUTY_NOT_FOUND (404)
qaytaradi — u holda hisobot yaratishning imkoni yo'q.
"""

from __future__ import annotations

from datetime import date

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT

WAYBILL = f"{BRUTTO_MGMT}/waybill"

MAX_RANGE_DAYS = 7

# Direction / status qiymatlari (frontend filterlari)
DIRECTIONS = ["UP", "DOWN", "BOTH"]
STATUSES = [
    "ACCEPTED", "NOT_ACCEPTED", "PENDING_ACCESS",
    "APPROVED", "REJECTED", "ZERO_MILEAGE",
]


class WaybillRepository:
    MAX_RANGE_DAYS = MAX_RANGE_DAYS
    DIRECTIONS = DIRECTIONS
    STATUSES = STATUSES

    def __init__(self, client: BMClient):
        self.client = client

    def validate_range(self, from_date: str, to_date: str) -> None:
        f = date.fromisoformat(from_date)
        t = date.fromisoformat(to_date)
        if f > t:
            raise ValueError("from > to")
        if (t - f).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"Oraliq {MAX_RANGE_DAYS} kundan oshmasligi kerak")

    def report(self, route_id: str, from_date: str, to_date: str) -> list:
        """Waybill hisobotini JSON ko'rinishida qaytaradi."""
        self.validate_range(from_date, to_date)
        return self.client.post(
            WAYBILL,
            json={"routeVariantId": route_id, "from": from_date, "to": to_date},
        )

    def download(self, route_id: str, from_date: str, to_date: str,
                 out_file: str, plate_num: str | None = None,
                 direction: str | None = None, status: str | None = None) -> str:
        """Waybill hisobotini Excel qilib yuklab oladi."""
        self.validate_range(from_date, to_date)
        if direction and direction not in DIRECTIONS:
            raise ValueError(f"direction: {', '.join(DIRECTIONS)}")
        if status and status not in STATUSES:
            raise ValueError(f"status: {', '.join(STATUSES)}")
        params = {
            "routeVariantId": route_id,
            "from": from_date,
            "to": to_date,
        }
        if plate_num:
            params["plateNum"] = plate_num
        if direction:
            params["direction"] = direction
        if status:
            params["status"] = status
        return self.client.download(f"{WAYBILL}/download", out_file, params=params)

    def stat_plan(self, route_id: str, from_date: str, to_date: str):
        """Reja statistikasi: totalTrip, forwardTrip, backwardTrip, vehicles."""
        self.validate_range(from_date, to_date)
        return self.client.get(
            f"{WAYBILL}/stat/plan",
            params={"routeVariantId": route_id, "from": from_date, "to": to_date},
        )
