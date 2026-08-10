"""Yo'nalish daraxti repository."""

from __future__ import annotations

from ..api.client import BMClient
from ..config.settings import ROUTE_MGMT


def find_route_ids(nodes: list, name: str | None = None) -> list:
    """Daraxtdan yo'nalish (ROUTE) id'larini yig'adi; name berilsa filter qiladi."""
    ids = []

    def walk(node):
        children = node.get("children") or []
        if not children and node.get("entityType") == "ROUTE":
            if name is None or (node.get("name") or "").strip() == name:
                ids.append(node["id"])
        for c in children:
            walk(c)

    for n in nodes:
        walk(n)
    return ids


class RouteRepository:
    """route-variants/tree orqali region->tashkilot->park->yo'nalish."""

    def __init__(self, client: BMClient):
        self.client = client

    def tree(self, brutto: bool = True) -> list:
        data = self.client.get(
            f"{ROUTE_MGMT}/route-variants/tree",
            params={"isBrutto": "true" if brutto else "false"},
        )
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data if isinstance(data, list) else []

    def find_route_ids(self, nodes: list, name: str | None = None) -> list:
        return find_route_ids(nodes, name=name)
