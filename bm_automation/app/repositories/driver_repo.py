"""Haydovchi (driver) repository — qidiruv va ID yechish."""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT
from ..utils.text import upper_norm

DRIVERS = f"{BRUTTO_MGMT}/drivers"

_NAME_KEYS = (
    "fullName", "name", "driverName", "firstName", "lastName",
    "middleName", "secondName",
)


class DriverRepository:
    def __init__(self, client: BMClient):
        self.client = client

    def by_route_variant(self, route_variant_id: str, date_str: str,
                         search: str = "") -> list:
        """Yo'nalish bo'yicha haydovchilar ro'yxati (sahifalangan)."""
        try:
            data = self.client.get(
                f"{DRIVERS}/by-route-variant/{route_variant_id}",
                params={"dutyDate": date_str, "page": 0, "size": 200, "search": search},
            )
        except Exception:
            return []
        items, _ = self.client._extract_items(data)
        if not isinstance(items, list):
            items = data if isinstance(data, list) else []
        return items

    def resolve_driver_id(self, route_variant_id: str, date_str: str,
                          driver_name: str) -> str:
        """Haydovchi nomini API ro'yxatidan topib ID qaytaradi.

        Aniq mos, so'ngra so'z-to'plami, so'ngra substring mosligi bilan
        izlanadi. Topilmasa bo'sh qator qaytaradi.
        """
        name = upper_norm(driver_name)
        if not name:
            return ""
        items = self.by_route_variant(route_variant_id, date_str)
        words = set(name.split())
        for d in items:
            for cand in self._candidates(d):
                if cand == name:
                    return self._id_of(d)
        for d in items:
            for cand in self._candidates(d):
                if words and words.issubset(set(cand.split())):
                    return self._id_of(d)
        for d in items:
            for cand in self._candidates(d):
                if len(name) >= 3 and (name in cand or cand in name):
                    return self._id_of(d)
        return ""

    @staticmethod
    def _candidates(d: dict) -> list[str]:
        return [upper_norm(d.get(k) or "") for k in _NAME_KEYS if upper_norm(d.get(k) or "")]

    @staticmethod
    def _id_of(d: dict) -> str:
        return str(d.get("id") or d.get("driverId") or "")
