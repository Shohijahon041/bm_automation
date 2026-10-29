"""Search Tool — yo'nalish/haydovchi/avtobus bo'yicha tezkor qidiruv.

Agentlar uchun yagona qidiruv nuqtasi: ism, raqam, TIN, garaj raqami
yoki plita bilan topadi. Data bazadan to'g'ridan-to'g'ri o'qiydi
(PostgreSQL), shuning uchun LLM/browser kerak emas.
"""

from __future__ import annotations

from typing import Any

from . import BaseTool
from ..scope import allowed_routes, current_scope, scope_denied
from ...db.storage import get_storage
from ...utils.logger import get_logger

log = get_logger("myai.tools.search")


class SearchTool(BaseTool):
    """PostgreSQL global qidiruv — routes / drivers / vehicles.

    Korxona scope'i berilgan bo'lsa qidiruv faqat shu korxona
    yo'nalishlari bo'yicha ishlaydi (fail-closed).
    """

    name = "search"
    description = (
        "Global qidiruv: find (nom/raqam/TIN), drivers, routes, vehicles "
        "— haydovchi/yo'nalish/avtobusni tez topish"
    )

    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "find": self._find,
            "routes": self._routes,
            "drivers": self._drivers,
            "vehicles": self._vehicles,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(f"Noma'lum search action: {action}")
        return await handler(**kwargs)

    @staticmethod
    def _storage():
        return get_storage()

    def _scope_routes(self) -> list[str] | None:
        return allowed_routes(self._storage())

    def _scope_sql(self, column: str, params: list) -> str:
        routes = self._scope_routes()
        if routes is None:
            return ""
        if not routes:
            return " AND 1=0"
        params.extend(routes)
        holders = ", ".join("%s" for _ in routes)
        return f" AND {column} IN ({holders})"

    def _foreign_route(self, route_id: str) -> bool:
        routes = self._scope_routes()
        if routes is None:
            return False
        rid = str(route_id or "").strip()
        return bool(rid) and rid not in routes

    async def _find(self, query: str = "", limit: int = 5, **kwargs) -> dict:
        """Global qidiruv — har bir turdan top N ta natija."""
        q = str(query or "").strip()
        if not q:
            return {"query": "", "found": False}
        routes = await self._routes(keyword=q, limit=limit)
        drivers = await self._drivers(keyword=q, limit=limit)
        vehicles = await self._vehicles(keyword=q, limit=limit)
        total = routes["count"] + drivers["count"] + vehicles["count"]
        return {
            "query": q,
            "found": total > 0,
            "count": total,
            "routes": routes["routes"],
            "drivers": drivers["drivers"],
            "vehicles": vehicles["vehicles"],
        }

    async def _routes(self, keyword: str = "", limit: int = 50, **kwargs) -> dict:
        s = self._storage()
        params: list[Any] = []
        where = " WHERE 1=1"
        if str(keyword or "").strip():
            where += " AND (name ILIKE %s OR external_id = %s)"
            params.extend((f"%{keyword}%", keyword))
        where += self._scope_sql("external_id", params)
        rows = s.query(
            "SELECT external_id, name, entity_type, is_brutto, parent_id "
            f"FROM routes{where} ORDER BY name LIMIT %s",
            (*params, int(limit)),
        )
        return {"routes": rows, "count": len(rows)}

    async def _drivers(self, keyword: str = "", route_id: str = "",
                       limit: int = 50, **kwargs) -> dict:
        """Haydovchilar — ism/TIN/telefon bo'yicha qidiruv."""
        if self._foreign_route(route_id):
            return {**scope_denied("Bu yo'nalish boshqa korxonaga tegishli"),
                    "drivers": []}
        s = self._storage()
        where = []
        params: list[Any] = []
        if str(keyword or "").strip():
            where.append(
                "(d.full_name ILIKE %s OR d.tin ILIKE %s OR d.external_id ILIKE %s "
                "OR p.phone ILIKE %s)")
            k = f"%{keyword}%"
            params.extend((k, k, k, k))
        if str(route_id or "").strip():
            where.append("d.route_id = %s")
            params.append(str(route_id))
        where_sql = f" WHERE {' AND '.join(where)}" if where else " WHERE 1=1"
        where_sql += self._scope_sql("d.route_id", params)
        rows = s.query(
            "SELECT d.external_id, d.full_name, d.tin, d.route_id, "
            "COALESCE(r.name, '') AS route_name, "
            "COALESCE(p.phone, '') AS phone, "
            "COALESCE(p.rating, 5) AS rating, "
            "COALESCE(p.blacklisted, 0) AS blacklisted "
            "FROM drivers d "
            "LEFT JOIN routes r ON r.external_id = d.route_id "
            "LEFT JOIN driver_profiles p ON p.driver_id = d.external_id"
            f"{where_sql} ORDER BY d.full_name LIMIT %s",
            (*params, int(limit)),
        )
        return {"drivers": rows, "count": len(rows)}

    async def _vehicles(self, keyword: str = "", route_id: str = "",
                        limit: int = 50, **kwargs) -> dict:
        """Avtobuslar — plita/garaj/model bo'yicha qidiruv."""
        if self._foreign_route(route_id):
            return {**scope_denied("Bu yo'nalish boshqa korxonaga tegishli"),
                    "vehicles": []}
        s = self._storage()
        where = []
        params: list[Any] = []
        if str(keyword or "").strip():
            where.append(
                "(v.plate_number ILIKE %s OR v.garage_number ILIKE %s "
                "OR v.model ILIKE %s OR v.external_id ILIKE %s)")
            k = f"%{keyword}%"
            params.extend((k, k, k, k))
        if str(route_id or "").strip():
            where.append("v.route_id = %s")
            params.append(str(route_id))
        where_sql = f" WHERE {' AND '.join(where)}" if where else " WHERE 1=1"
        where_sql += self._scope_sql("v.route_id", params)
        rows = s.query(
            "SELECT v.external_id, v.plate_number, v.garage_number, v.model, "
            "v.route_id, COALESCE(r.name, '') AS route_name "
            "FROM vehicles v "
            "LEFT JOIN routes r ON r.external_id = v.route_id"
            f"{where_sql} ORDER BY v.plate_number LIMIT %s",
            (*params, int(limit)),
        )
        return {"vehicles": rows, "count": len(rows)}