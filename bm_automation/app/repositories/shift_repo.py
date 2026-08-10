"""Shift (smena) repository."""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT

SHIFTS = f"{BRUTTO_MGMT}/shifts"


class ShiftRepository:
    def __init__(self, client: BMClient):
        self.client = client

    def by_date(self, route_variant_id: str, date_str: str) -> dict | None:
        """Kun uchun shift ma'lumoti: {id, name, graphicList}."""
        return self.client.get(
            f"{SHIFTS}/by-date",
            params={"routeVariantId": route_variant_id, "date": date_str},
        )

    def graph_export(self, shift_id: str) -> bytes:
        """Shift grafik export faylini (xlsx) qaytaradi (raw bytes)."""
        resp = self.client.session.get(
            self.client.config.base_url + f"{SHIFTS}/graph/export",
            params={"shiftId": shift_id},
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Export yuklanmadi: {resp.status_code} {resp.text[:200]}"
            )
        return resp.content
