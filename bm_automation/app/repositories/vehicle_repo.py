"""Avtobus (vehicle) repository — qidiruv va ID yechish."""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT
from ..utils.text import upper_norm

VEHICLES = f"{BRUTTO_MGMT}/vehicles"


class VehicleRepository:
    def __init__(self, client: BMClient):
        self.client = client

    def by_route_variant(self, route_variant_id: str, date_str: str) -> list:
        """Yo'nalish bo'yicha avtobuslar ro'yxati."""
        try:
            data = self.client.get(
                f"{VEHICLES}/by-route-variant/{route_variant_id}",
                params={"dutyDate": date_str},
            )
        except Exception:
            return []
        items, _ = self.client._extract_items(data)
        if not isinstance(items, list):
            items = data if isinstance(data, list) else []
        return items

    def resolve_vehicle_id(self, route_variant_id: str, date_str: str,
                           plate: str, garage: str = "") -> str:
        """Avtobus raqami/garaj raqami bo'yicha ID qaytaradi.

        Aniq mos (raqam yoki garaj), so'ngra substring mosligi. Topilmasa
        bo'sh qator qaytaradi.
        """
        plate = upper_norm(plate)
        garage = upper_norm(garage)
        if not plate and not garage:
            return ""
        items = self.by_route_variant(route_variant_id, date_str)
        for v in items:
            p = upper_norm(v.get("plateNumber") or v.get("plateNum") or "")
            g = upper_norm(v.get("garageNumber") or "")
            if (plate and p == plate) or (garage and g == garage):
                return self._id_of(v)
        for v in items:
            p = upper_norm(v.get("plateNumber") or v.get("plateNum") or "")
            if plate and len(plate) >= 3 and (plate in p or p in plate):
                return self._id_of(v)
        return ""

    @staticmethod
    def _id_of(v: dict) -> str:
        return str(v.get("id") or v.get("vehicleId") or "")
