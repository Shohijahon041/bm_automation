"""`daily_grafik.get_routes` — yangi kompaniya profillardan avtomatik olinishi."""

import daily_grafik as dg

RID = "11111111-2222-3333-4444-555555555555"


def _profile(**kw):
    p = {"name": "YANGI BUS MCHJ", "routeVariantId": RID,
         "routeName": "13-Yo'nalish"}
    p.update(kw)
    return p


def test_get_routes_keeps_static_entries(monkeypatch):
    monkeypatch.setattr(dg, "ROUTES", [("B-80", RID, "ASL", "B-80", 0)])
    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles", lambda: [])
    routes = dg.get_routes()
    assert routes == [("B-80", RID, "ASL", "B-80", 0)]


def test_get_routes_auto_adds_new_company(monkeypatch):
    monkeypatch.setattr(dg, "ROUTES", [])
    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles",
        lambda: [_profile()])
    routes = dg.get_routes()
    assert len(routes) == 1
    label, rid, img_dir, label2, _idx = routes[0]
    assert rid == RID
    assert label == "13-YO'NALISH"
    assert label2 == "13-YO'NALISH"
    assert img_dir == "YANGI_BUS_MCHJ_" + RID[:8]


def test_get_routes_static_wins_and_dedupes(monkeypatch):
    monkeypatch.setattr(dg, "ROUTES", [("STATIC", RID, "STATIC_DIR", "STATIC", 0)])
    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles",
        lambda: [_profile(), {"name": "BOSHQA", "routeVariantId": ""}])
    routes = dg.get_routes()
    assert routes == [("STATIC", RID, "STATIC_DIR", "STATIC", 0)]


def test_get_routes_falls_back_when_profiles_unavailable(monkeypatch):
    monkeypatch.setattr(dg, "ROUTES", [("B-80", RID, "ASL", "B-80", 0)])

    def boom():
        raise RuntimeError("yo'q")

    monkeypatch.setattr(
        "bm_automation.app.core.profiles.all_profiles", boom)
    assert dg.get_routes() == [("B-80", RID, "ASL", "B-80", 0)]
