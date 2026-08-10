"""Brutto (toshuvchi) hisobotlar repository.

Endpoints frontend bundle'dan qaytarilgan:
    GET /brutto-management/api/v1/gross/trip            - trip hisoboti (JSON)
    GET /brutto-management/api/v1/gross/trip/download   - trip Excel
    GET /brutto-management/api/v1/gross/route           - route hisoboti (JSON)
    GET /brutto-management/api/v1/gross/route/download  - route Excel (avtobus bo'yicha)
    GET /brutto-management/api/v1/gross/route/overall/download - route jamlama Excel
    GET /brutto-management/api/v1/gross/finance         - finance (ROLE ruxsati kerak)
    GET /brutto-management/api/v1/gross/finance/download
"""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import BRUTTO_MGMT

GROSS = f"{BRUTTO_MGMT}/gross"

MONTH_ENUM = {
    "01": "JANUARY", "02": "FEBRUARY", "03": "MARCH", "04": "APRIL",
    "05": "MAY", "06": "JUNE", "07": "JULY", "08": "AUGUST",
    "09": "SEPTEMBER", "10": "OCTOBER", "11": "NOVEMBER", "12": "DECEMBER",
}


class GrossRepository:
    MONTH_ENUM = MONTH_ENUM

    def __init__(self, client: BMClient):
        self.client = client

    def trip(self, route_id: str, date_str: str):
        """Trip hisobotini JSON ko'rinishida qaytaradi."""
        return self.client.get(
            f"{GROSS}/trip", params={"routeVariantId": route_id, "date": date_str}
        )

    def trip_download(self, route_id: str, date_str: str, out_file: str) -> str:
        """Trip hisobotini Excel qilib yuklab oladi."""
        return self.client.download(
            f"{GROSS}/trip/download", out_file,
            params={"routeVariantId": route_id, "date": date_str},
        )

    def route(self, route_id: str, from_date: str, to_date: str):
        """Route hisobotini JSON ko'rinishida qaytaradi (max 30 kun)."""
        return self.client.get(
            f"{GROSS}/route",
            params={"routeVariantId": route_id, "from": from_date, "to": to_date},
        )

    def route_download(self, route_id: str, from_date: str, to_date: str,
                       out_file: str) -> str:
        """Route hisobotini avtobus bo'yicha Excel qilib yuklab oladi."""
        return self.client.download(
            f"{GROSS}/route/download", out_file,
            params={"routeVariantId": route_id, "from": from_date, "to": to_date},
        )

    def route_overall_download(self, route_id: str, from_date: str,
                               to_date: str, out_file: str) -> str:
        """Route hisobotini jamlama (overall) Excel qilib yuklab oladi."""
        return self.client.download(
            f"{GROSS}/route/overall/download", out_file,
            params={"routeVariantId": route_id, "from": from_date, "to": to_date},
        )

    def finance(self, route_id: str, year: int, month: str):
        """Finance hisobotini JSON qaytaradi. month: '01'..'12' yoki enum nomi."""
        month_enum = MONTH_ENUM.get(str(month), str(month).upper())
        return self.client.get(
            f"{GROSS}/finance",
            params={"routeVariantId": route_id, "year": str(year), "month": month_enum},
        )

    def finance_download(self, route_id: str, year: int, month: str,
                         out_file: str) -> str:
        month_enum = MONTH_ENUM.get(str(month), str(month).upper())
        return self.client.download(
            f"{GROSS}/finance/download", out_file,
            params={"routeVariantId": route_id, "year": str(year), "month": month_enum},
        )
