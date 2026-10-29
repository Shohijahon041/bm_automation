"""Bilim bazasini avtomatik kengaytirish (`core.knowledge_grow`) testlari.

Qamrov:
  * `grow()` — avtomatik qaydlar (kod/API/bot/test/sozlama/DB/holat/log),
    marker, idempotentlik.
  * `learn_error` / `learn_task` — agent darslari va vazifa hisobotlari.
  * `check_and_grow()` — `KNOWLEDGE_AUTO` va interval gate.
  * `boshliq_toldirish()` — yechilmagan havolalar uchun bo'sh qayd; kod
    ichidagi misollar va avto-bog'lash bloki hisobga olinmaydi.
  * Dashboard API — `/api/knowledge/grow` (GET holat, POST kengaytirish).
"""

from __future__ import annotations

import json

import pytest

from bm_automation.app.core import knowledge as K
from bm_automation.app.core import knowledge_grow as G


@pytest.fixture()
def vault_dir(tmp_path, monkeypatch):
    """Har test uchun alohida vault papkasi (konftest bilan bir xil uslub)."""
    base = tmp_path / "vault"
    monkeypatch.setenv("KNOWLEDGE_VAULT_DIR", str(base))
    return base


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """Kengaytirish holati testlar orasida o'tib ketmasin (konftest bilan bir xil).

    Log ham izolyatsiya qilinadi: haqiqiy `logs/bm.log` o'sib boradi va
    oyna chegarasi siljiganda tahlil natijasi run'lar orasida o'zgarib,
    idempotentlik testini beqaror qilardi.
    """
    monkeypatch.setenv("KNOWLEDGE_GROW_STATE_DIR", str(tmp_path / "grow_state"))
    monkeypatch.setenv("KNOWLEDGE_LOG_PATH", str(tmp_path / "bm.log"))


def _read(rel: str) -> str:
    return (K.vault_dir() / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# grow(): avtomatik qaydlar
# ---------------------------------------------------------------------------

def test_grow_creates_managed_notes(vault_dir):
    res = G.grow()
    assert res["ok"] is True
    assert not res["errors"]

    for key in ("kod-xaritasi", "api-endpointlar", "bot-buyruqlari", "testlar",
                "sozlamalar", "db-jadvallari", "holat-snapshoti", "log-tahlili",
                "avto-xato-jurnali", "agent-vazifalari", "grafik-holati"):
        rel = f"{G.MANAGED[key][0]}.md"
        text = _read(rel)
        assert G.AUTO_MARK.format(key=key) in text, rel
        assert G.AUTO_NOTE_NOTE in text, rel
    assert res["notes"] >= len(G.MANAGED)


def test_grow_harvests_real_project_data(vault_dir):
    G.grow()
    kod = _read("Hisobotlar/Kod-xaritasi.md")
    assert "python fayl" in kod

    api = _read("Hisobotlar/API-endpointlar.md")
    assert "/api/knowledge" in api          # routerlar skanerlanadi

    db = _read("Hisobotlar/DB-jadvallari.md")
    assert "`companies`" in db              # sxemadan jadvallar

    sozlama = _read("Hisobotlar/Muhit-sozlamalari.md")
    assert "KNOWLEDGE_AUTO" in sozlama

    testlar = _read("Hisobotlar/Testlar.md")
    assert "test_knowledge_grow.py" in testlar


def test_grow_is_idempotent(vault_dir):
    G.grow()
    ikkinchi = G.grow()
    # Faqat holat snapshoti (vaqt belgisi) har safar yangilanadi.
    assert set(ikkinchi["changed"]) <= {"holat"}


def test_grow_links_new_notes(vault_dir):
    """Yangi avtomatik qayd grafga ulanadi (avto-bog'lash bloki)."""
    G.grow()
    assert "<!-- avto-bog'liq:start -->" in _read("Hisobotlar/Kod-xaritasi.md")
    g = K.graph()
    assert any(e["target"] == "Kod-xaritasi" for e in g["edges"])


def test_grafik_holati_note_and_stability(vault_dir):
    """`Grafik-holati` qaydi salomatlikni yozadi va o'zini sanab cheksiz
    o'zgarmaydi (ikkinchi kengaytirishda qayta yozilmaydi)."""
    K.write_note("Alfa", "Qoidalar", "matn\n\n[[Beta]]")
    K.write_note("Beta", "Qoidalar", "matn")
    G.grow()
    text = _read("Hisobotlar/Grafik-holati.md")
    assert "**Holat:**" in text
    assert "Eng bog'langan qaydlar" in text
    assert "[[Alfa]]" in text
    # Grafik-holati o'zini sanamaydi (aks holda har safar raqam o'zgaradi).
    assert "[[Grafik-holati]]" not in text

    ikkinchi = G.grow()
    assert "grafik" not in ikkinchi["changed"]


# ---------------------------------------------------------------------------
# Agentlardan o'rganish
# ---------------------------------------------------------------------------

def test_learn_error_groups_repeats(vault_dir):
    G.learn_error("browser", "get_routes", "401 Unauthorized")
    G.learn_error("browser", "get_routes", "401 Unauthorized")
    jurnal = _read("Xatolar/Avto-xato-jurnali.md")
    assert jurnal.count("401 Unauthorized") == 1     # takror birlashtiriladi
    assert "## 2×" in jurnal
    assert "browser" in jurnal

    state = json.loads((G._state_dir() / G.ERRORS_STATE).read_text(encoding="utf-8"))
    assert sum(int(v["count"]) for v in state.values()) == 2


def test_learn_error_keeps_lesson(vault_dir):
    G.learn_error("db", "rows", "ulanish yo'q", agent="analytics",
                  lesson="DB ishlamasa BM API'ga tushing")
    jurnal = _read("Xatolar/Avto-xato-jurnali.md")
    assert "analytics" in jurnal
    assert "BM API'ga tushing" in jurnal


def test_learn_task_writes_report(vault_dir):
    G.learn_task("t-1", "B-80 yo'nalishini tekshir", "completed",
                 agents=["browser", "analytics"], summary="Reyslar to'g'ri")
    hisobot = _read("Hisobotlar/Agent-vazifalari.md")
    assert "B-80 yo'nalishini tekshir" in hisobot
    assert "browser, analytics" in hisobot
    assert "muvaffaqiyatli: 1" in hisobot


def test_learn_task_without_request_skipped(vault_dir):
    res = G.learn_task("", "", "completed")
    assert res.get("changed") is False


# ---------------------------------------------------------------------------
# check_and_grow(): sozlama va interval
# ---------------------------------------------------------------------------

def test_check_and_grow_disabled(vault_dir, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_AUTO", "off")
    assert G.enabled() is False
    res = G.check_and_grow()
    assert res["skipped"] and "off" in res["skipped"]
    assert not (K.vault_dir() / "Hisobotlar" / "Kod-xaritasi.md").exists()


def test_check_and_grow_respects_interval(vault_dir):
    first = G.check_and_grow()
    assert first["ok"] is True and "skipped" not in first
    second = G.check_and_grow()
    assert second["skipped"] == "interval"
    assert second["next_in_sec"] > 0


def test_status_reports_state(vault_dir):
    G.grow()
    st = G.status()
    assert st["ok"] is True and st["enabled"] is True
    assert st["runs"] == 1 and st["notes"] >= len(G.MANAGED)
    assert st["last_run"]
    assert "Hisobotlar/Kod-xaritasi" in st["managed"]


# ---------------------------------------------------------------------------
# boshliq_toldirish(): bo'shliqlarni to'ldirish
# ---------------------------------------------------------------------------

def test_gap_fill_creates_stub_for_missing_link(vault_dir):
    K.write_note("Qoida bir", "Qoidalar", "matn\n\n[[Yoq-qayd]] ga qarang")
    res = G.boshliq_toldirish()
    assert res["created"] == ["Qoidalar/Yoq-qayd.md"]
    stub = _read("Qoidalar/Yoq-qayd.md")
    assert "avto-yaratilgan: boshliq-Yoq-qayd" in stub
    assert "[[Qoida-bir]]" in stub          # kim havola qilgani ko'rsatiladi


def test_gap_fill_ignores_code_examples(vault_dir):
    """Kod bloki/ichi koddagi misol havola uchun bo'sh qayd ochilmaydi."""
    K.write_note("Qoida bir", "Qoidalar",
                 "Misollar:\n\n```\n[[Misol-qayd]]\n```\n\n"
                 "Satr ichida `[[Yana-misol]]` ko'rinishida yoziladi.")
    res = G.boshliq_toldirish()
    assert res["created"] == []
    assert not (K.vault_dir() / "Qoidalar" / "Misol-qayd.md").exists()


def test_gap_fill_ignores_autolink_block(vault_dir):
    """Faqat avto-bog'lash blokidagi havola stub yaratmaydi (qayta tirilmasin)."""
    res = K.write_note("Qoida bir", "Qoidalar", "matni bor")
    path = K.vault_dir() / res["path"]
    path.write_text(
        "# Qoida bir\n\nmatni bor\n\n"
        "<!-- avto-bog'liq:start -->\n## Bog'liq qaydlar\n\n"
        "- [[Ochirilgan-qayd]]\n<!-- avto-bog'liq:end -->\n",
        encoding="utf-8")
    assert G.boshliq_toldirish()["created"] == []
    # Grafik esa bu havolani yechilmagan deb ko'rsatadi.
    assert any(u["target"] == "Ochirilgan-qayd" for u in K.graph()["unresolved"])


def test_gap_fill_uses_target_path_category(vault_dir):
    """`[[Sozlamalar/Yangi-qayd]]` so'zma-so'z yo'l bo'yicha ochiladi."""
    K.write_note("Qoida bir", "Qoidalar", "matn\n\n[[Xatolar/Yangi-hodisa]]")
    created = G.boshliq_toldirish()["created"]
    assert created == ["Xatolar/Yangi-hodisa.md"]


def test_gap_fill_skips_existing_and_respects_limit(vault_dir):
    K.write_note("Bor qayd", "Xatolar", "matn")
    K.write_note("Qoida bir", "Qoidalar",
                 "[[Xatolar/Bor-qayd]]\n[[A-bir]]\n[[B-ikki]]\n[[C-uch]]")
    created = G.boshliq_toldirish(limit=2)["created"]
    assert created == ["Qoidalar/A-bir.md", "Qoidalar/B-ikki.md"]


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

def test_api_grow_status_and_run(vault_dir):
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import app

    client = TestClient(app)
    st = client.get("/api/knowledge/grow")
    assert st.status_code == 200
    assert st.json()["enabled"] is True

    before = client.get("/api/knowledge/list").json()["count"]
    r = client.post("/api/knowledge/grow")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["notes"] > before
    assert (K.vault_dir() / "Hisobotlar" / "Kod-xaritasi.md").is_file()


def test_api_grow_requires_super(monkeypatch):
    """`_require_super` egasi bo'lmagan administratorni to'sadi."""
    from bm_automation.app.dashboard.routers import knowledge as router
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        router._require_super({"is_admin": True, "is_super": False})
    assert exc.value.status_code == 403
