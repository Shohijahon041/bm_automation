"""Avtomatik bog'lash (`core.vault_links`) testlari.

Qamrov:
  * `write_note`/`append_note` yozgandan keyin blok avtomatik qo'shilishi;
  * ildiz indeksga havola va mazmun bo'yicha o'xshash qaydlar;
  * idempotentlik, eski blokni almashtirish, o'ziga havola yo'qligi;
  * ildiz qaydning o'ziga blok yozilmasligi;
  * `vault-links --dry-run` hech narsani yozmasligi;
  * CLI buyrug'i ro'yxatga qo'shilgani.
"""

from __future__ import annotations

import pytest

from bm_automation.app.core import knowledge as K
from bm_automation.app.core import vault_links as V


@pytest.fixture()
def vault_dir(tmp_path):
    base = tmp_path / "knowledge_vault"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _body(path) -> str:
    return path.read_text(encoding="utf-8")


def _blocks(text: str) -> int:
    return text.count(V.MARK_START)


def test_write_note_gets_root_link(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi\n\nindeks")
    res = K.write_note("Qayd", "Qoidalar", "matn")
    text = K.read_note(res["path"])["text"]
    assert V.MARK_START in text and V.MARK_END in text
    assert "## Bog'liq qaydlar" in text
    assert f"[[{V.ROOT_STEM}]]" in text
    assert _blocks(text) == 1


def test_root_note_has_no_block(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi\n\nindeks")
    text = _body(vault_dir / "Bilim-bazasi.md")
    assert V.MARK_START not in text


def test_related_notes_by_content(vault_dir):
    K.write_note("Kamera oqimi", "Sozlamalar",
                 "jt1078 kamera rtsp ffmpeg oqim protokol kanal")
    K.write_note("Kamera xatosi", "Xatolar",
                 "jt1078 kamera oqim ffmpeg protokol kanal uzilish xato")
    K.write_note("Oylik hisobot", "Hisobotlar",
                 "excel oylik hisobot jadval fayl yig'ma")

    index = V.build_index()
    related = V.related_notes("Kamera-oqimi", index, limit=1)
    assert related == ["Kamera-xatosi"]


def test_no_self_link(vault_dir):
    K.write_note("Yakka qayd", "Qoidalar", "butunlay boshqa matn zebra")
    text = _body(vault_dir / "Qoidalar" / "Yakka-qayd.md")
    assert "[[Yakka-qayd]]" not in text


def test_apply_is_idempotent(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    K.write_note("Qayd", "Qoidalar", "matn bir")
    path = vault_dir / "Qoidalar" / "Qayd.md"
    before = _body(path)
    assert V.apply_to_note(path) is False
    assert _body(path) == before

    res = V.autolink_all()
    assert res["ok"] is True
    assert res["changed_count"] == 0


def test_stale_block_replaced(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    K.write_note("Qayd", "Qoidalar", "matn")
    path = vault_dir / "Qoidalar" / "Qayd.md"
    stale = _body(path).replace(
        V.MARK_START, V.MARK_START + "\n- [[Eski-qayd]]", 1)
    path.write_text(stale, encoding="utf-8")

    assert V.apply_to_note(path) is True
    text = _body(path)
    assert "[[Eski-qayd]]" not in text
    assert _blocks(text) == 1


def test_no_root_still_links(vault_dir):
    K.write_note("Birinchi", "Qoidalar", "bir xil matn alfa")
    K.write_note("Ikkinchi", "Xatolar", "bir xil matn alfa")
    text = _body(vault_dir / "Qoidalar" / "Birinchi.md")
    assert V.MARK_START in text
    assert f"[[{V.ROOT_STEM}]]" not in text
    assert "[[Ikkinchi]]" in text


def test_append_note_keeps_single_block(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    K.append_note("Hisobotlar/Kunlik.md", "birinchi kun", title="Kunlik")
    K.append_note("Hisobotlar/Kunlik.md", "ikkinchi kun")
    text = _body(vault_dir / "Hisobotlar" / "Kunlik.md")
    assert "birinchi kun" in text and "ikkinchi kun" in text
    assert _blocks(text) == 1
    assert text.count("## Bog'liq qaydlar") == 1


def test_write_note_meta_size_includes_block(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    res = K.write_note("Qayd", "Qoidalar", "qisqa")
    assert res["size"] == (vault_dir / res["path"]).stat().st_size
    assert res["size"] > len("qisqa")


def test_dry_run_writes_nothing(vault_dir):
    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    K.write_note("A", "Qoidalar", "umumiy so'z alfa")
    path = vault_dir / "Qoidalar" / "A.md"
    path.write_text("# A\n\numumiy so'z alfa\n", encoding="utf-8")
    before = _body(path)

    res = V.autolink_all(dry_run=True)
    assert res["dry_run"] is True
    assert res["changed_count"] >= 1
    assert "Qoidalar/A.md" in res["changed"]
    assert _body(path) == before


def test_myai_vault_tool_write_autolinks(tmp_path):
    import asyncio

    from bm_automation.app.myai.tools.vault import VaultTool

    base = tmp_path / "agent_vault"
    (base / "Qoidalar").mkdir(parents=True)
    (base / "Bilim-bazasi.md").write_text("# Bilim bazasi", encoding="utf-8")

    asyncio.run(VaultTool(base_dir=base).execute(
        action="write", title="Agent qaydi", category="Qoidalar", content="matn"))
    text = (base / "Qoidalar" / "Agent-qaydi.md").read_text(encoding="utf-8")
    assert V.MARK_START in text
    assert f"[[{V.ROOT_STEM}]]" in text


def test_cli_command_registered(vault_dir):
    from bm_automation.cli.parser import build_parser

    args = build_parser().parse_args(["vault-links", "--dry-run", "--limit", "2"])
    assert args.func.__name__ == "cmd_vault_links"
    assert args.dry_run is True and args.limit == 2

    K.write_note("Bilim bazasi", "", "# Bilim bazasi")
    assert args.func(args) == 0
