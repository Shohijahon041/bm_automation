"""DTransport API Tool — bm.dtransport.uz API integration."""

from __future__ import annotations

import asyncio
from typing import Any

from . import BaseTool
from ...utils.logger import get_logger

log = get_logger("myai.tools.dtransport")


class DTransportTool(BaseTool):
    """BM API orqali bm.dtransport.uz dan ma'lumot olish."""

    name = "dtransport"
    description = "bm.dtransport.uz API integration (routes, trips, waybills)"

    def _get_client(self):
        from ...api.client import BMClient
        return BMClient()

    @staticmethod
    def _scope_denial(kwargs: dict) -> dict | None:
        """Korxona scope'i bo'lsa faqat o'z yo'nalishi so'raladi."""
        from ..scope import allowed_routes, current_scope, scope_denied
        scope = current_scope()
        if not scope or scope.get("is_admin"):
            return None
        routes = allowed_routes(None)
        rid = str(kwargs.get("route_id") or "").strip()
        if rid and rid in (routes or []):
            return None
        return scope_denied("Bu yo'nalish boshqa korxonaga tegishli")

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "get_route_data": self._get_route_data,
            "get_trips": self._get_trips,
            "get_waybills": self._get_waybills,
            "get_duties": self._get_duties,
            "get_gross_route": self._get_gross_route,
            "get_gross_trip": self._get_gross_trip,
        }
        denial = self._scope_denial(kwargs)
        if denial is not None:
            return denial
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum dtransport action: {action}")
        return await handler(**kwargs)

    async def _get_route_data(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            from ...repositories.gross_repo import GrossRepository
            repo = GrossRepository(client)
            return repo.route(route_id, date, date)
        data = await asyncio.to_thread(_fetch)
        return {"route_id": route_id, "date": date, "data": data}

    async def _get_trips(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            return client.get_trips(
                route_variant_id=route_id,
                from_date=date,
                to_date=date,
            )
        trips = await asyncio.to_thread(_fetch)
        return {"route_id": route_id, "date": date, "trips": trips}

    async def _get_waybills(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            return client.get_waybills(
                route_variant_id=route_id,
                from_date=date,
                to_date=date,
            )
        waybills = await asyncio.to_thread(_fetch)
        return {"route_id": route_id, "date": date, "waybills": waybills}

    async def _get_duties(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            return client.get_duties(
                route_variant_id=route_id,
                date_from=date,
                date_to=date,
            )
        duties = await asyncio.to_thread(_fetch)
        return {"route_id": route_id, "date": date, "duties": duties}

    async def _get_gross_route(self, route_id: str = "", date: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            from ...repositories.gross_repo import GrossRepository
            repo = GrossRepository(client)
            return repo.route(route_id, date, date)
        data = await asyncio.to_thread(_fetch)
        return {"route_id": route_id, "date": date, "gross": data}

    async def _get_gross_trip(self, trip_id: str = "", **kwargs) -> dict:
        def _fetch():
            client = self._get_client()
            client.login()
            from ...repositories.gross_repo import GrossRepository
            repo = GrossRepository(client)
            return repo.trip(trip_id)
        data = await asyncio.to_thread(_fetch)
        return {"trip_id": trip_id, "gross": data}
