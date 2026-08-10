"""CLI uchun umumiy yordamchi funksiyalar."""

from __future__ import annotations

from ..app.api.client import BMClient


def print_tree(nodes, depth=0):
    for node in nodes:
        kind = node.get("entityType", "")
        marker = f" [{node['id']}]" if kind == "ROUTE" else ""
        print(f"{'  ' * depth}{node.get('name', '')}{marker}")
        print_tree(node.get("children") or [], depth + 1)


def resolve_route_id(client: BMClient, route_id: str) -> str:
    """--route berilmasa daraxtdan birinchi yo'nalishni topadi."""
    from ..app.repositories.route_repo import find_route_ids, route_tree

    if route_id:
        return route_id
    tree = route_tree(client)
    ids = find_route_ids(tree)
    if not ids:
        raise ValueError("Yo'nalish topilmadi. --route <id> bering yoki 'routes' buyrug'idan foydalaning.")
    print(f"Avtomatik tanlangan yo'nalish: {ids[0]}")
    return ids[0]
