"""Yo'nalish daraxtidan route id'larini topish testlari."""

from bm_automation.app.repositories.route_repo import find_route_ids


def _tree():
    return [
        {
            "entityType": "REGION",
            "name": "Farg'ona",
            "children": [
                {
                    "entityType": "ORGANIZATION",
                    "name": "FERGANATEX",
                    "children": [
                        {
                            "entityType": "PARK",
                            "name": "Park 1",
                            "children": [
                                {
                                    "entityType": "ROUTE",
                                    "id": "abc",
                                    "name": "10-yo'nalish",
                                    "children": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {"entityType": "ROUTE", "id": "zzz", "name": "yagona", "children": []},
    ]


def test_find_route_ids_all():
    ids = find_route_ids(_tree())
    assert ids == ["abc", "zzz"]


def test_find_route_ids_filter_by_name():
    assert find_route_ids(_tree(), name="10-yo'nalish") == ["abc"]
    assert find_route_ids(_tree(), name="yo'q") == []


def test_find_route_ids_empty():
    assert find_route_ids([]) == []
