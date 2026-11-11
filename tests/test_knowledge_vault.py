"""Bilim bazasi (Obsidian vault) testlari.

Qamrov:
  * `core.knowledge` — yozish/o'qish/qidirish, kategoriya tekshiruvi,
    traversal himoyasi, `overwrite` va `append_note`, hisobot saqlash.
  * Dashboard API — `/api/knowledge/list|note|search|stats` (TestClient).
  * Bot `/vault` — ro'yxat/o'qish va ADMIN bo'lmagan foydalanuvchi uchun yozish
    taqiqi; bo'lim xaritasi (`sections`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from bm_automation.app.core import knowledge as K


@pytest.fixture()
def vault_dir(tmp_path, monkeypatch):
    """Har test uchun alohida vault papkasi."""
    base = tmp_path / "vault"
    monkeypatch.setenv("KNOWLEDGE_VAULT_DIR", str(base))
    return base


# ---------------------------------------------------------------------------
# core.knowledge
# ---------------------------------------------------------------------------

def test_vault_dir_created_with_categories(vault_dir):
    d = K.vault_dir()
    assert d == vault_dir
    for cat in K.CATEGORIES:
        assert (d / cat).is_dir()


def test_write_read_list_search(vault_dir):
    res = K.write_note("KM xatosi", "Xatolar", "sabab: km ikki marta")
    assert res["ok"] is True
    assert res["path"] == "Xatolar/KM-xatosi.md"

    read = K.read_note(res["path"])
    assert read["ok"] is True
    assert "km ikki marta" in read["text"]
    assert read["category"] == "Xatolar"

    listing = K.list_notes()
    assert listing["count"] == 1
    assert listing["counts"]["Xatolar"] == 1
    assert K.list_notes("Xatolar")["count"] == 1
    assert K.list_notes("Hisobotlar")["count"] == 0

    found = K.search_notes("ikki marta")
    assert found["count"] == 1
    assert found["results"][0]["path"] == res["path"]


def test_write_unknown_category_rejected(vault_dir):
    res = K.write_note("qayd", "Boshqa", "matn")
    assert res["ok"] is False
    assert "ruxsat" in res["error"]
    assert K.list_notes()["count"] == 0


def test_write_without_category_goes_to_root(vault_dir):
    res = K.write_note("Erkin qayd", "", "matn")
    assert res["ok"] is True
    assert res["path"] == "Erkin-qayd.md"
    assert res["category"] == ""


@pytest.mark.parametrize("use_tool", [False, True])
def test_write_duplicates_in_same_second_preserve_content(vault_dir, monkeypatch,
                                                         use_tool):
    from bm_automation.app.myai.tools.vault import VaultTool

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 10, 12, 0, 0)

    monkeypatch.setattr(K, "datetime", FrozenDateTime)
    tool = VaultTool()
    results = []
    for body in ("birinchi", "ikkinchi", "uchinchi", "tortinchi"):
        result = (asyncio.run(tool.execute("write", title="Takror",
                                          category="Qoidalar", content=body))
                  if use_tool else K.write_note("Takror", "Qoidalar", body))
        assert result["ok"] is True
        results.append((result["path"], body))
    assert len({path for path, _ in results}) == 4
    assert K.list_notes("Qoidalar")["count"] == 4
    for path, body in results:
        assert f"\n\n{body}\n" in K.read_note(path)["text"]


def test_overwrite_same_path(vault_dir):
    a = K.write_note("Qoida", "Qoidalar", "eski")
    b = K.write_note("Qoida", "Qoidalar", "yangi", overwrite=True,
                     path=a["path"])
    assert b["path"] == a["path"]
    assert "yangi" in K.read_note(a["path"])["text"]
    assert K.list_notes("Qoidalar")["count"] == 1


def test_append_note_creates_then_appends(vault_dir):
    first = K.append_note("Hisobotlar/Kunlik.md", "## 1-kun\n\nmatn",
                          title="Kunlik")
    assert first["ok"] is True
    second = K.append_note("Hisobotlar/Kunlik.md", "## 2-kun\n\nmatn")
    assert second["ok"] is True
    text = K.read_note("Hisobotlar/Kunlik.md")["text"]
    assert "1-kun" in text and "2-kun" in text
    assert text.startswith("# Kunlik")


def test_traversal_blocked(vault_dir):
    with pytest.raises(ValueError):
        K.read_note("../../secret.md")
    with pytest.raises(ValueError):
        K.write_note("x", "", "matn", overwrite=True, path="../outside.md")


def test_traversal_sibling_dir_blocked(vault_dir):
    """Qo'shni `vault2` papkasi prefiks bo'yicha o'tib ketmasligi kerak."""
    sibling = vault_dir.parent / "vault2"
    sibling.mkdir()
    (sibling / "secret.md").write_text("sir", encoding="utf-8")
    with pytest.raises(ValueError):
        K.read_note("../vault2/secret.md")
    with pytest.raises(ValueError):
        K.delete_note("../vault2/secret.md")
    assert (sibling / "secret.md").exists()


def test_delete_note(vault_dir):
    res = K.write_note("O'chiriladigan", "Xatolar", "matn")
    gone = K.delete_note(res["path"])
    assert gone["ok"] is True
    assert gone["path"] == res["path"]
    assert K.list_notes()["count"] == 0
    assert K.delete_note(res["path"])["ok"] is False


def test_delete_note_rejects_non_md_and_dirs(vault_dir):
    K.vault_dir()
    (vault_dir / "Qoidalar" / "rasm.png").write_bytes(b"x")
    assert K.delete_note("Qoidalar/rasm.png")["ok"] is False
    assert K.delete_note("Qoidalar")["ok"] is False
    assert (vault_dir / "Qoidalar").is_dir()
    assert K.delete_note("")["ok"] is False


def test_read_missing_file(vault_dir):
    res = K.read_note("Qoidalar/yoq.md")
    assert res["ok"] is False
    assert "topilmadi" in res["error"]


def test_read_note_by_basename(vault_dir):
    """Obsidian `[[Qayd]]` havolasi — papka ichidan nom bo'yicha topiladi."""
    K.write_note("Asosiy invariantlar", "Qoidalar", "matn")
    res = K.read_note("Asosiy-invariantlar.md")
    assert res["ok"] is True
    assert res["path"] == "Qoidalar/Asosiy-invariantlar.md"


def test_read_note_ambiguous_basename_not_resolved(vault_dir):
    K.write_note("Bir xil", "Qoidalar", "a")
    K.write_note("Bir xil", "Xatolar", "b")
    assert K.read_note("Bir-xil.md")["ok"] is False


def test_save_report_period_in_title(vault_dir):
    res = K.save_report("Oylik-natijalar", "natijalar", period="2026-09")
    assert res["ok"] is True
    assert res["category"] == "Hisobotlar"
    assert "2026-09" in res["title"]
    assert "saqlangan" in K.read_note(res["path"])["text"]


def test_stats(vault_dir):
    K.write_note("bir", "Qoidalar", "matn")
    K.write_note("ikki", "Xatolar", "matn")
    st = K.stats()
    assert st["count"] == 2
    assert st["bytes"] > 0
    assert st["dir"].endswith("vault")


def test_index_note_lists_categories(vault_dir):
    K.write_note("bir", "Qoidalar", "matn")
    text = K.index_note()
    assert "# Bilim bazasi" in text
    assert "[[Qoidalar/bir]]" in text


def test_vault_tool_uses_configured_path_and_explicit_override(vault_dir, tmp_path):
    from bm_automation.app.myai.tools.vault import VaultTool

    tool = VaultTool()
    assert tool.base == vault_dir.resolve()
    created = asyncio.run(tool.execute("write", title="Sozlangan", content="matn"))
    assert K.read_note(created["path"])["ok"] is True

    other = tmp_path / "explicit"
    explicit = VaultTool(base_dir=other)
    created = asyncio.run(explicit.execute("write", title="Maxsus", content="matn"))
    assert (other / created["path"]).is_file()
    assert asyncio.run(explicit.execute("read", path=created["path"]))["found"]
    assert asyncio.run(explicit.execute("list"))["count"] == 1
    assert K.list_notes()["count"] == 1
    assert K.read_note(created["path"])["ok"] is False


@pytest.mark.parametrize("path", ["../vault2/secret.md", "..\\vault2\\secret.md",
                                  "../../secret.md"])
def test_vault_tool_blocks_traversal(vault_dir, path):
    from bm_automation.app.myai.tools.vault import VaultTool

    sibling = vault_dir.parent / "vault2"
    sibling.mkdir()
    secret = sibling / "secret.md"
    secret.write_text("sir", encoding="utf-8")
    with pytest.raises(ValueError):
        asyncio.run(VaultTool().execute("read", path=path))
    assert secret.read_text(encoding="utf-8") == "sir"


@pytest.mark.parametrize("action", ["read", "write", "append", "delete"])
def test_absolute_paths_rejected(vault_dir, action):
    path = str(vault_dir.parent / "secret.md")
    with pytest.raises(ValueError):
        if action == "write":
            K.write_note("x", content="matn", path=path, overwrite=True)
        elif action == "append":
            K.append_note(path, "matn")
        else:
            getattr(K, f"{action}_note")(path)


def test_non_markdown_reads_and_writes_rejected(vault_dir):
    from bm_automation.app.myai.tools.vault import VaultTool

    K.vault_dir()
    target = vault_dir / "secret.txt"
    target.write_text("sir", encoding="utf-8")
    assert K.read_note("secret.txt")["ok"] is False
    assert K.write_note("x", path="secret.txt", overwrite=True)["ok"] is False
    assert K.append_note("secret.txt", "matn")["ok"] is False
    assert K.append_note("new.txt", "matn")["ok"] is False
    assert K.append_note("Qoidalar", "matn")["ok"] is False
    result = asyncio.run(VaultTool().execute("read", path="secret.txt"))
    assert result["found"] is False and result["text"] == ""
    assert target.read_text(encoding="utf-8") == "sir"
    assert not (vault_dir / "new.txt").exists()


def _symlink(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink yaratish mavjud emas: {type(exc).__name__}")


@pytest.mark.parametrize("directory", [False, True])
def test_outside_symlinks_excluded_from_discovery(vault_dir, directory):
    from bm_automation.app.myai import knowledge_context as kc
    from bm_automation.app.myai.tools.vault import VaultTool

    K.vault_dir()
    outside = vault_dir.parent / "vault2"
    outside.mkdir()
    secret = outside / "Asosiy-invariantlar.md"
    secret.write_text("# Sir\n\ntenantmaxfiy\n", encoding="utf-8")
    link = vault_dir / "Qoidalar" / ("external" if directory else secret.name)
    _symlink(link, outside if directory else secret)
    relative = (link / secret.name if directory else link).relative_to(vault_dir)
    tool = VaultTool()

    assert K.list_notes()["count"] == 0
    assert K.search_notes("tenantmaxfiy")["count"] == 0
    assert K.stats()["count"] == 0
    assert K.graph()["count"] == 0
    assert K.read_note("Qoidalar")["files"] == []
    assert K.read_note("Asosiy-invariantlar")["ok"] is False
    assert "Asosiy-invariantlar" not in K.index_note()
    assert asyncio.run(tool.execute("list"))["count"] == 0
    assert asyncio.run(tool.execute("search", keyword="tenantmaxfiy"))["count"] == 0
    assert asyncio.run(tool.execute("index"))["count"] == 0
    assert asyncio.run(tool.execute("read", path="Qoidalar"))["files"] == []
    for read in (K.read_note, lambda p: asyncio.run(tool.execute("read", path=p))):
        with pytest.raises(ValueError):
            read(relative.as_posix())
    with pytest.raises(ValueError):
        K.write_note("sir", path=relative.as_posix(), overwrite=True)
    with pytest.raises(ValueError):
        K.append_note(relative.as_posix(), "yangi")
    with pytest.raises(ValueError):
        K.delete_note(relative.as_posix())
    kc.clear_cache()
    try:
        assert kc.relevant_notes("tenantmaxfiy") == []
    finally:
        kc.clear_cache()
    assert secret.read_text(encoding="utf-8") == "# Sir\n\ntenantmaxfiy\n"


def test_symlink_cannot_disguise_non_markdown_file(vault_dir):
    from bm_automation.app.myai.tools.vault import VaultTool

    K.vault_dir()
    secret = vault_dir / "secret.txt"
    secret.write_text("sir", encoding="utf-8")
    _symlink(vault_dir / "alias.md", secret)
    assert K.list_notes()["count"] == 0
    assert K.read_note("alias.md")["ok"] is False
    assert K.write_note("x", path="alias.md", overwrite=True)["ok"] is False
    assert K.append_note("alias.md", "matn")["ok"] is False
    assert asyncio.run(VaultTool().execute("read", path="alias.md"))["found"] is False
    assert secret.read_text(encoding="utf-8") == "sir"


# ---------------------------------------------------------------------------
# Dashboard API
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from bm_automation.app.dashboard.fastapi_app import app
    return TestClient(app)


def test_api_list_and_read(client, vault_dir):
    K.write_note("Qoida bir", "Qoidalar", "invariant matni")
    r = client.get("/api/knowledge/list")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1

    r2 = client.get("/api/knowledge/note",
                    params={"path": "Qoidalar/Qoida-bir.md"})
    assert r2.status_code == 200
    assert "invariant matni" in r2.json()["text"]


def test_api_search_and_stats(client, vault_dir):
    K.write_note("GPS xatosi", "Xatolar", "tracker jim qoldi")
    r = client.get("/api/knowledge/search", params={"q": "tracker"})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    st = client.get("/api/knowledge/stats").json()
    assert st["count"] == 1


def _post(client, payload, path="/api/knowledge/note"):
    """POST endpoint'i uchun rate-limit'ni tozalab yuboradi (1 sek/endpoint)."""
    from bm_automation.app.dashboard import deps
    deps._last_post_at.clear()
    return client.post(path, json=payload)


def test_api_post_note(client, vault_dir):
    r = _post(client, {"title": "Yangi qoida", "category": "Qoidalar",
                       "content": "matn"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["path"] == "Qoidalar/Yangi-qoida.md"

    # tahrirlash (overwrite + path) — yangi fayl yaratilmaydi
    r2 = _post(client, {"title": "Yangi qoida", "category": "Qoidalar",
                        "content": "# Yangilandi\n\nyangi matn",
                        "overwrite": True, "path": data["path"]})
    assert r2.json()["ok"] is True
    assert K.list_notes("Qoidalar")["count"] == 1
    assert "yangilandi" in K.read_note(data["path"])["text"].lower()


def test_api_post_rejects_bad_category(client, vault_dir):
    r = _post(client, {"title": "x", "category": "Boshqa", "content": "y"})
    assert r.json()["ok"] is False


def test_api_delete_note(client, vault_dir):
    K.write_note("Vaqtinchalik", "Hisobotlar", "matn")
    url = "/api/knowledge/delete"
    r = _post(client, {"path": "Hisobotlar/Vaqtinchalik.md"}, url)
    assert r.json()["ok"] is True
    assert K.list_notes()["count"] == 0
    assert _post(client, {"path": "Hisobotlar/Vaqtinchalik.md"}, url).json()["ok"] is False
    assert _post(client, {"path": "../../secret.md"}, url).json()["ok"] is False


@pytest.mark.parametrize("endpoint", ["note", "delete"])
@pytest.mark.parametrize("body", ["null", "[]", '"matn"', "1", "true"])
def test_api_rejects_non_object_json(client, vault_dir, endpoint, body):
    from bm_automation.app.dashboard import deps

    deps._last_post_at.clear()
    response = client.post(f"/api/knowledge/{endpoint}", content=body,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert K.list_notes()["count"] == 0


@pytest.mark.parametrize("endpoint", ["note", "delete"])
def test_api_rejects_invalid_json(client, vault_dir, endpoint):
    from bm_automation.app.dashboard import deps

    deps._last_post_at.clear()
    response = client.post(f"/api/knowledge/{endpoint}", content="{",
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 400


@pytest.mark.parametrize("overwrite", ["false", "true", 0, 1, None, [], {}])
def test_api_overwrite_requires_boolean(client, vault_dir, overwrite):
    created = K.write_note("Asl", content="eski matn")
    response = _post(client, {"title": "Asl", "path": created["path"],
                              "content": "yangi matn", "overwrite": overwrite})
    assert response.status_code == 422
    assert "eski matn" in K.read_note(created["path"])["text"]
    assert K.list_notes()["count"] == 1


@pytest.mark.parametrize("field,value", [("title", {}), ("category", []),
                                         ("content", False), ("path", 123)])
def test_api_note_fields_require_strings(client, vault_dir, field, value):
    payload = {"title": "Yangi", "content": "matn", field: value}
    assert _post(client, payload).status_code == 422
    assert K.list_notes()["count"] == 0


def test_api_false_overwrite_preserves_existing_note(client, vault_dir):
    created = K.write_note("Asl", content="eski matn")
    response = _post(client, {"title": "Asl", "path": created["path"],
                              "content": "yangi matn", "overwrite": False})
    assert response.json()["ok"] is True
    assert response.json()["path"] != created["path"]
    assert "eski matn" in K.read_note(created["path"])["text"]


@pytest.mark.parametrize("action", ["read", "write", "delete"])
@pytest.mark.parametrize("error_type", [OSError, ValueError, RuntimeError])
def test_api_note_errors_hide_internal_details(client, vault_dir, monkeypatch,
                                               action, error_type):
    def fail(*args, **kwargs):
        raise error_type("C:/private/internal-path secret-detail")

    monkeypatch.setattr(K, f"{action}_note", fail)
    if action == "read":
        response = client.get("/api/knowledge/note", params={"path": "qayd.md"})
    else:
        endpoint = "note" if action == "write" else "delete"
        response = _post(client, {"title": "Qayd", "path": "qayd.md"},
                          f"/api/knowledge/{endpoint}")
    assert response.json()["ok"] is False
    assert "internal-path" not in response.text
    assert "secret-detail" not in response.text


@pytest.mark.parametrize("action,method", [("read", "read_text"), ("delete", "unlink")])
def test_core_note_errors_hide_internal_details(vault_dir, monkeypatch, action, method):
    created = K.write_note("Qayd", content="matn")
    original = getattr(Path, method)
    target = vault_dir / created["path"]

    def fail(path, *args, **kwargs):
        if path == target:
            raise OSError("private internal-path secret-detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, fail)
    result = getattr(K, f"{action}_note")(created["path"])
    assert result["ok"] is False
    assert "internal-path" not in result["error"]
    assert "secret-detail" not in result["error"]


def test_api_requires_super_admin():
    """`/api/knowledge` — ADMIN (4) + `is_super` shart."""
    from fastapi import HTTPException
    from bm_automation.app.dashboard import deps
    from bm_automation.app.dashboard.routers.knowledge import _require_super
    assert deps._GET_ROLE_MIN["/api/knowledge"] == 4
    assert deps._POST_ROLE_MIN["/api/knowledge"] == 4
    assert deps.page_for_path("/api/knowledge/list") == "knowledge"

    _require_super({"is_admin": True, "is_super": True})  # egasi — ruxsat
    for scope in ({"is_admin": True, "is_super": False},
                  {"is_admin": False, "is_super": False}, {}):
        with pytest.raises(HTTPException) as exc:
            _require_super(scope)
        assert exc.value.status_code == 403


def test_scope_super_flag(monkeypatch):
    """Sessiya scope'ida `is_super` faqat egasi uchun True bo'ladi."""
    from bm_automation.app.dashboard import deps
    monkeypatch.setenv("DASHBOARD_ADMIN_USER", "egasi")
    deps.invalidate_super_cache()
    assert deps.is_super_username("egasi") is True
    assert deps.is_super_username("boshqa_admin") is False
    deps.invalidate_super_cache()


# ---------------------------------------------------------------------------
# Obsidian grafigi (tugun/qirra)
# ---------------------------------------------------------------------------

def test_graph_nodes_edges_and_unresolved(vault_dir):
    """Tugunlar = qaydlar, qirralar = `[[havolalar]]`; yechilmagani tashlanadi."""
    K.write_note("Bilim bazasi", "", "# Bilim bazasi\n\nindeks")
    K.write_note("Alfa", "Qoidalar", "alfa matni")
    K.write_note("Beta", "Xatolar", "beta matni")
    (vault_dir / "Qoidalar" / "Alfa.md").write_text(
        "# Alfa\n\n[[Beta]] va [[Yo'q-qayd]]\n", encoding="utf-8")

    g = K.graph()
    assert g["ok"] is True and g["count"] == 3
    assert {n["id"] for n in g["nodes"]} == {"Alfa", "Beta", "Bilim-bazasi"}
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("Alfa", "Beta") in pairs
    assert all(e["source"] != e["target"] for e in g["edges"])
    assert g["unresolved"] == [{"from": "Alfa", "target": "Yo'q-qayd"}]

    alfa = next(n for n in g["nodes"] if n["id"] == "Alfa")
    assert alfa["category"] == "Qoidalar"
    assert alfa["path"] == "Qoidalar/Alfa.md"
    assert alfa["links"] == 1
    beta = next(n for n in g["nodes"] if n["id"] == "Beta")
    assert beta["backlinks"] == 1


def test_graph_link_resolution_by_path(vault_dir):
    """`[[Xatolar/Beta]]` to'liq yo'l bilan ham aniqlanadi."""
    K.write_note("Alfa", "Qoidalar", "alfa matni")
    K.write_note("Beta", "Xatolar", "beta matni")
    (vault_dir / "Qoidalar" / "Alfa.md").write_text(
        "# Alfa\n\n[[Xatolar/Beta|alias]]\n", encoding="utf-8")
    g = K.graph()
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("Alfa", "Beta") in pairs
    assert g["unresolved"] == []


def test_graph_preserves_duplicate_titles_and_path_links(vault_dir):
    K.vault_dir()
    (vault_dir / "Kirish.md").write_text(
        "[[Qoidalar/Bir-xil]] [[Xatolar/Bir-xil]] [[Bir-xil]]", encoding="utf-8")
    (vault_dir / "Qoidalar/Bir-xil.md").write_text(
        "[[Xatolar/Bir-xil]] [[Xatolar/Bir-xil]]", encoding="utf-8")
    (vault_dir / "Xatolar/Bir-xil.md").write_text("matn", encoding="utf-8")

    graph = K.graph()
    assert graph["count"] == 3
    assert {n["id"] for n in graph["nodes"]} == {
        "Kirish", "Qoidalar/Bir-xil", "Xatolar/Bir-xil"}
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        ("Kirish", "Qoidalar/Bir-xil"), ("Kirish", "Xatolar/Bir-xil"),
        ("Qoidalar/Bir-xil", "Xatolar/Bir-xil")}
    assert graph["unresolved"] == [{"from": "Kirish", "target": "Bir-xil"}]
    for include_autolinks in (False, True):
        status = K.graph_status(graph, include_autolinks=include_autolinks)
        assert status["notes"] == 3 and status["edges"] == 3
        assert status["body_edges"] == 3 and status["isolated"] == 0
        assert len(status["hubs"]) == 3
    assert K.graph_status(graph, exclude={"Bir-xil"})["notes"] == 1
    assert K.graph_status(graph, exclude={"Qoidalar/Bir-xil"})["notes"] == 2


def test_graph_status_reports_health(vault_dir):
    """Holat: tugun/qirra, yetim, uzilgan havolalar va markazlar."""
    K.write_note("Bilim bazasi", "", "# Bilim bazasi\n\n[[Alfa]]")
    K.write_note("Alfa", "Qoidalar", "matn")
    K.write_note("Beta", "Xatolar", "matn")
    K.write_note("Yolgiz", "Sozlamalar", "matn")
    (vault_dir / "Bilim-bazasi.md").write_text(
        "# Bilim bazasi\n\n[[Alfa]]\n", encoding="utf-8")
    (vault_dir / "Qoidalar" / "Alfa.md").write_text(
        "# Alfa\n\n[[Beta]] va [[Yo'q-qayd]]\n", encoding="utf-8")
    (vault_dir / "Xatolar" / "Beta.md").write_text(
        "# Beta\n\nmatn\n", encoding="utf-8")
    (vault_dir / "Sozlamalar" / "Yolgiz.md").write_text(
        "# Yolgiz\n\nmatn\n", encoding="utf-8")

    st = K.graph_status()
    assert st["ok"] is True and st["notes"] == 4
    assert st["edges"] == 2          # Bilim-bazasi→Alfa, Alfa→Beta
    assert st["body_edges"] == 2
    assert st["unresolved"] == 1
    assert st["isolated_list"] == ["Yolgiz"]
    assert st["orphans"] == 1 and st["auto_only"] == 0
    assert st["health"] == "e'tibor"
    assert st["hubs"][0]["id"] == "Alfa"
    assert st["hubs"][0]["degree"] == 2
    assert st["categories"]["Qoidalar"]["notes"] == 1
    assert st["avg_degree"] == 1.0
    assert st["density"] == round(2 * 2 / (4 * 3), 4)


def test_graph_status_ignores_autolink_block(vault_dir):
    """Avto-bog'lash bloki matn havolasi hisoblanmaydi (barqaror rejim)."""
    K.write_note("Alfa", "Qoidalar", "matn")
    K.write_note("Beta", "Xatolar", "matn")
    (vault_dir / "Qoidalar" / "Alfa.md").write_text(
        "# Alfa\n\n<!-- avto-bog'liq:start -->\n- [[Beta]]\n"
        "<!-- avto-bog'liq:end -->\n", encoding="utf-8")

    g = K.graph()
    pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("Alfa", "Beta") in pairs

    st = K.graph_status(g)
    assert st["edges"] == 1 and st["body_edges"] == 0
    assert st["orphans"] == 2
    assert st["auto_only"] == 2      # faqat avto-blok bilan ulangan
    assert st["isolated"] == 0

    body = K.graph_status(g, include_autolinks=False)
    assert body["edges"] == 0
    assert body["isolated"] == 2     # barqaror rejim: matn havolasi yo'q
    assert body["auto_only"] == 2    # ikkisi ham avto-blok bilan grafda
    assert body["health"] == "sog'lom"


def test_api_graph(client, vault_dir):
    """`/api/knowledge/graph` tugun/qirra va holatni qaytaradi (super)."""
    K.write_note("Bir", "Qoidalar", "matn")
    K.write_note("Ikki", "Xatolar", "matn")
    (vault_dir / "Qoidalar" / "Bir.md").write_text(
        "# Bir\n\n[[Ikki]]\n", encoding="utf-8")
    (vault_dir / "Xatolar" / "Ikki.md").write_text(
        "# Ikki\n\nmatn\n", encoding="utf-8")
    r = client.get("/api/knowledge/graph")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["count"] == 2
    assert ("Bir", "Ikki") in {(e["source"], e["target"]) for e in body["edges"]}
    assert body["status"]["notes"] == 2
    assert body["status"]["edges"] == 1
    assert body["status"]["health"] == "sog'lom"


# ---------------------------------------------------------------------------
# Bo'limlar va bot
# ---------------------------------------------------------------------------

def test_sections_include_knowledge():
    from bm_automation.app.core.sections import (SECTION_KEYS, SECTION_LABELS,
                                                 clean_sections,
                                                 section_for_command)
    assert "knowledge" in SECTION_KEYS
    assert SECTION_LABELS["knowledge"] == "Bilim bazasi"
    assert section_for_command("/vault") == "knowledge"
    pages, err = clean_sections(["knowledge"])
    assert pages == ["knowledge"] and err == ""


def _bot_env(monkeypatch, role):
    from bm_automation.app.notifications.ops import dispatch
    from bm_automation.app.notifications.ops.roles import Role

    sent: list[str] = []
    monkeypatch.setattr(dispatch, "resolve_role", lambda chat_id: role)
    monkeypatch.setattr(dispatch, "reply",
                        lambda chat_id, text, **kw: sent.append(str(text)))
    monkeypatch.setattr(dispatch, "section_allowed",
                        lambda *a, **kw: True, raising=False)
    # Bot tomonidagi "faqat egasi" tekshiruvi test holatida ruxsatli.
    monkeypatch.setattr(dispatch, "is_super_chat", lambda *a, **kw: True)
    return dispatch, sent


def test_bot_vault_lists_notes(monkeypatch, vault_dir):
    from bm_automation.app.notifications.ops.roles import Role
    K.write_note("Qoida bir", "Qoidalar", "matn")
    dispatch, sent = _bot_env(monkeypatch, Role.ADMIN)
    dispatch.handle_message(555, "/vault")
    assert sent
    assert "BILIM BAZASI" in sent[0]
    assert "Qoidalar/Qoida-bir.md" in sent[0]


def test_bot_vault_reads_path(monkeypatch, vault_dir):
    from bm_automation.app.notifications.ops.roles import Role
    K.write_note("Qoida bir", "Qoidalar", "invariant matni")
    dispatch, sent = _bot_env(monkeypatch, Role.ADMIN)
    dispatch.handle_message(555, "/vault Qoidalar/Qoida-bir.md")
    assert any("invariant matni" in s for s in sent)


def test_bot_vault_search(monkeypatch, vault_dir):
    from bm_automation.app.notifications.ops.roles import Role
    K.write_note("GPS xatosi", "Xatolar", "tracker jim qoldi")
    dispatch, sent = _bot_env(monkeypatch, Role.ADMIN)
    dispatch.handle_message(555, "/vault qidir tracker")
    joined = "\n".join(sent)
    assert "Xatolar/GPS-xatosi.md" in joined


def test_bot_vault_write_admin_only(monkeypatch, vault_dir):
    from bm_automation.app.notifications.ops.roles import Role
    dispatch, sent = _bot_env(monkeypatch, Role.MANAGER)
    dispatch.handle_message(555, "/vault yoz Qoidalar|Yangi|matn")
    assert sent  # MANAGER — umuman ko'rmaydi
    assert K.list_notes("Qoidalar")["count"] == 0

    dispatch2, sent2 = _bot_env(monkeypatch, Role.ADMIN)
    dispatch2.handle_message(555, "/vault yoz Qoidalar|Yangi|matn")
    assert K.list_notes("Qoidalar")["count"] == 1


def test_bot_vault_viewer_and_manager_denied(monkeypatch, vault_dir):
    from bm_automation.app.notifications.ops.roles import Role
    K.write_note("Qoida bir", "Qoidalar", "matn")
    for role in (Role.VIEWER, Role.DISPATCHER, Role.MANAGER):
        dispatch, sent = _bot_env(monkeypatch, role)
        dispatch.handle_message(555, "/vault")
        assert sent and not any("BILIM BAZASI" in s for s in sent), role


def test_bot_vault_admin_without_super_denied(monkeypatch, vault_dir):
    """ADMIN ham, lekin egasi emas — bilim bazasini ko'ra olmaydi."""
    from bm_automation.app.notifications.ops import dispatch
    from bm_automation.app.notifications.ops.roles import Role
    K.write_note("Qoida bir", "Qoidalar", "matn")
    _, sent = _bot_env(monkeypatch, Role.ADMIN)
    monkeypatch.setattr(dispatch, "is_super_chat", lambda *a, **kw: False)
    dispatch.handle_message(555, "/vault")
    assert sent and not any("BILIM BAZASI" in s for s in sent)


def test_is_super_chat_owner_ids(monkeypatch):
    """`TG_OWNER_IDS` env chat_id egasi sifatida qabul qilinadi."""
    from bm_automation.app.notifications.ops.roles import is_super_chat
    monkeypatch.setenv("TG_OWNER_IDS", "777, 888")
    assert is_super_chat(777) is True
    assert is_super_chat(888) is True
    assert is_super_chat(999) is False
    assert is_super_chat(0) is False
