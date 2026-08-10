"""Kunlik duty (haydovchi/avtobus/grafik) repository.

Endpoint:
    GET /brutto-management/api/v1/duty?routeVariantId=&date=YYYY-MM-DD
    -> {id, shiftId, date, graphs: [...]}
       graphs[]: graphName (P1/P2...), driverName, timeTableNumber,
                 vehicleId, plateNum, vehicleModel, startTime, endTime,
                 shiftName (DU/SE/CH...), tripCount, hasSecond,
                 secondDriverName, isReplaced.
"""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT

DUTY = f"{BRUTTO_MGMT}/duty"


class DutyRepository:
    def __init__(self, client: BMClient):
        self.client = client

    def by_date(self, route_id: str, date_str: str) -> dict:
        """Bir kunlik duty ma'lumotini qaytaradi (graphs bilan)."""
        return self.client.get(
            DUTY, params={"routeVariantId": route_id, "date": date_str}
        )

    def graph_times(self, shift_graph_id: str, direction: str) -> list:
        """Grafik yo'nalish bo'yicha slotlari (START joylashuvi uchun)."""
        return self.client.get(
            f"{DUTY}/graph/times",
            params={"shiftGraphId": shift_graph_id, "direction": direction},
        )
