"""Konechka nomlarini aniqlash: profil + waybill zaxira yo'li."""

from bm_automation.app.services import driver_sheet_service as dss

_QOQON = "Qoqon yo'li"
_SIL = "Sil kassaliklar shifoxonasi (Konechka)"

_DUTY = {"graphs": [{
    "shiftGraphId": "g1", "graphName": "P1", "driverName": "TEST",
    "startTime": "06:05:00", "endTime": "19:48:00",
}]}


class _FakeRepo:
    def __init__(self, client):
        pass

    def report(self, route_id, from_date, to_date):
        if from_date == "2026-10-20":
            return []
        if from_date == "2026-10-19":
            return [
                {"direction": "UP", "startName": _QOQON},
                {"direction": "UP", "startName": _QOQON},
                {"direction": "DOWN", "startName": _SIL},
            ]
        return []


def test_konechka_names_keeps_target_date(monkeypatch):
    class Repo(_FakeRepo):
        def report(self, route_id, from_date, to_date):
            return [{"direction": "UP", "startName": "Bugungi"},
                    {"direction": "DOWN", "startName": "Bugungi 2"}]

    monkeypatch.setattr(dss, "WaybillRepository", Repo)
    names = dss.konechka_names(object(), "r", "2026-10-20")
    assert names == {"UP": "Bugungi", "DOWN": "Bugungi 2"}


def test_konechka_names_looks_back_when_target_empty(monkeypatch):
    monkeypatch.setattr(dss, "WaybillRepository", _FakeRepo)
    names = dss.konechka_names(object(), "r", "2026-10-20")
    assert names == {"UP": _QOQON, "DOWN": _SIL}


def test_profile_without_starts_uses_route_name_and_waybills(monkeypatch):
    monkeypatch.setattr(dss, "WaybillRepository", _FakeRepo)
    monkeypatch.setattr(dss, "graph_start_direction", lambda *a, **k: "UP")
    data = dss.build_rows(
        object(), "r", "2026-10-20", duty_data=_DUTY,
        profile={"routeName": "12-Yo'nalish", "start1": None, "start2": None})
    assert data["routeName"] == "12-Yo'nalish"
    assert data["konechka"] == {"UP": _QOQON, "DOWN": _SIL}


def test_profile_starts_take_precedence(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("profil to'liq bo'lsa waybill so'ralmasin")

    monkeypatch.setattr(dss, "konechka_names", _boom)
    monkeypatch.setattr(dss, "graph_start_direction", lambda *a, **k: "UP")
    data = dss.build_rows(
        object(), "r", "2026-10-20", duty_data=_DUTY,
        profile={"routeName": "12-Yo'nalish", "start1": _QOQON, "start2": _SIL})
    assert data["konechka"] == {"UP": _QOQON, "DOWN": _SIL}


def test_profile_partial_starts_fills_missing_side(monkeypatch):
    monkeypatch.setattr(dss, "konechka_names",
                        lambda *a, **k: {"UP": "Avto", "DOWN": "Avto 2"})
    monkeypatch.setattr(dss, "graph_start_direction", lambda *a, **k: "UP")
    data = dss.build_rows(
        object(), "r", "2026-10-20", duty_data=_DUTY,
        profile={"routeName": "12-Yo'nalish", "start1": _QOQON})
    assert data["konechka"] == {"UP": _QOQON, "DOWN": "Avto 2"}


def test_konechka_names_no_data_returns_blank(monkeypatch):
    """Waybill bo'lmasa boshqa yo'nalishning standart nomlari chiqmasin."""
    class _EmptyRepo:
        def __init__(self, client):
            pass

        def report(self, route_id, from_date, to_date):
            return []

    monkeypatch.setattr(dss, "WaybillRepository", _EmptyRepo)
    assert dss.konechka_names(object(), "r", "2026-10-20") == {
        "UP": "", "DOWN": ""}


def test_detect_route_starts_maps_directions(monkeypatch):
    monkeypatch.setattr(dss, "WaybillRepository", _FakeRepo)
    assert dss.detect_route_starts(object(), "r", "2026-10-20") == {
        "start1": _QOQON, "start2": _SIL}


def test_detect_route_starts_partial(monkeypatch):
    class _UpOnly(_FakeRepo):
        def report(self, route_id, from_date, to_date):
            return [{"direction": "UP", "startName": _QOQON}]

    monkeypatch.setattr(dss, "WaybillRepository", _UpOnly)
    starts = dss.detect_route_starts(object(), "r", "2026-10-20")
    assert starts == {"start1": _QOQON}


def test_waybill_details_reads_route_name(monkeypatch):
    """Yo'nalish nomi ham waybill'dan, konechkalar bilan bir o'qishda olinadi."""
    class _Repo(_FakeRepo):
        def report(self, route_id, from_date, to_date):
            return [
                {"direction": "UP", "startName": _QOQON, "routeName": "12-сонли"},
                {"direction": "DOWN", "startName": _SIL, "routeName": "12-сонли"},
                {"direction": "UP", "startName": _QOQON, "routeName": "boshqa"},
            ]

    monkeypatch.setattr(dss, "WaybillRepository", _Repo)
    assert dss.waybill_details(object(), "r", "2026-10-20") == {
        "start1": _QOQON, "start2": _SIL, "routeName": "12-сонли"}


def test_waybill_details_empty_without_waybills(monkeypatch):
    class _EmptyRepo:
        def __init__(self, client):
            pass

        def report(self, route_id, from_date, to_date):
            return []

    monkeypatch.setattr(dss, "WaybillRepository", _EmptyRepo)
    assert dss.waybill_details(object(), "r", "2026-10-20") == {}


def test_short_route_name_strips_suffixes():
    assert dss.short_route_name("12-сонли") == "12"
    assert dss.short_route_name("10-yo'nalish") == "10"
    assert dss.short_route_name("12-Yo'nalish") == "12"
    assert dss.short_route_name("B-80") == "B-80"
    assert dss.short_route_name("ШИ-4") == "ШИ-4"
    assert dss.short_route_name("") == ""


def test_detect_profile_fields_returns_short_route_name(monkeypatch):
    """Avto-to'ldirishda rasmiy nom ("12-сонли") qisqa ko'rinishda saqlanadi."""
    monkeypatch.delenv("BM_TEST_MODE", raising=False)
    monkeypatch.setattr(
        dss, "waybill_details",
        lambda *a, **k: {"routeName": "12-сонли", "start1": _QOQON})
    from bm_automation.app.core import companies as companies_mod
    monkeypatch.setattr(companies_mod, "client_for_profile",
                        lambda *a, **k: object())
    assert dss.detect_profile_fields({"routeVariantId": "r1"}) == {
        "routeName": "12", "start1": _QOQON}
