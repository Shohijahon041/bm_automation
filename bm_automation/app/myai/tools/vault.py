"""Vault Tool — Obsidian knowledge vault bilan ishlash.

Agentlar "qoidalar" (invariantlar) va "xatolar" jurnalini markdown
fayllardan o'qiydi; tasdiqlangan xatoni esa omborga yozib qo'yadi.

Vault ildizi: loyiha ildizidagi `knowledge_vault/` (yoki `base_dir`
parametri orqali berilgan maxsus papka — testlarda ishlatiladi).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import BaseTool
from ...utils.logger import get_logger

log = get_logger("myai.tools.vault")


def _default_vault_dir() -> Path:
    """Loyiha ildizidagi `knowledge_vault` papkasini topish.

    tool.py joylashuvi: <root>/bm_automation/app/myai/tools/vault.py
    → parents[3] = <root>/bm_automation, parents[4] = <root>.
    """
    here = Path(__file__).resolve().parents[4]
    for cand in (
            here / "knowledge_vault",
            here / "bm_automation" / "knowledge_vault",
    ):
        if cand.is_dir():
            return cand
    return here / "knowledge_vault"


# [[wikilink]] ni oddiy matnga (yoki matn ichida qoldirib) o'qiymiz.
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


class VaultTool(BaseTool):
    """Obsidian vault — qoidalar/xatolar ombori.

    Actions:
      - search   keyword [limit]           — fayllarni qidirish
      - read     path                       — fayl matnini o'qish
      - write    title, category, content   — xato/qoida qo'shish
      - list     [category]                 — barcha fayllar ro'yxati
      - index                              — Xato-jurnali matni (va boshqa
                                              indekslar) — qidiruv yordami
    """

    name = "vault"
    description = (
        "Obsidian knowledge vault: qoidalar/invariantlar va tasdiqlangan "
        "xatolar jurnali. search / read / write / list."
    )

    def __init__(self, base_dir: str | Path | None = None):
        self.base = Path(base_dir) if base_dir else _default_vault_dir()
        self.base.mkdir(parents=True, exist_ok=True)

    # ── ichki ──────────────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        """Vault ichidagi fayl yo'lini ochish (traversal dan himoya)."""
        rel = (rel or "").strip("/\\").replace("\\", "/")
        p = (self.base / rel).resolve()
        if not str(p).startswith(str(self.base.resolve())):
            raise ValueError(f"Vault tashqarisiga chiqish mumkin emas: {rel}")
        return p

    def _walk(self) -> list[Path]:
        return sorted(
            p for p in self.base.rglob("*.md")
            if p.is_file() and not any(
                part.startswith(".") for part in p.relative_to(self.base).parts)
        )

    @staticmethod
    def _title_of(path: Path) -> str:
        return path.stem

    def _linkify(self, text: str) -> str:
        return WIKILINK_RE.sub(r"\1", text)

    # ── actions ────────────────────────────────────────────────────────
    async def execute(self, action: str = "", **kwargs) -> Any:
        actions = {
            "search": self._search,
            "read": self._read,
            "write": self._write,
            "list": self._list,
            "index": self._index,
        }
        handler = actions.get(action)
        if handler is None:
            raise ValueError(
                f"Noma'lum vault action: {action!r} "
                f"(mavjud: {', '.join(actions)})")
        return await handler(**kwargs)

    async def _search(self, keyword: str = "", limit: int = 10,
                      **kwargs) -> dict:
        """Fayllarni contenido bo'yicha qidirish.

        [[wikilink]] nomlari ham kalit sifatida ishlaydi.
        """
        kw = str(keyword or "").strip().lower()
        limit = max(1, int(limit or 10))
        results: list[dict] = []
        for p in self._walk():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hay = (text + "\n" + p.stem).lower()
            if kw and kw not in hay:
                continue
            head = text.strip().splitlines()
            title_line = next(
                (l for l in head if l.startswith("#")), "") or p.stem
            results.append({
                "path": str(p.relative_to(self.base)).replace("\\", "/"),
                "title": self._title_of(p),
                "heading": title_line.lstrip("# ").strip(),
                "excerpt": self._linkify(
                    " ".join(head[1:])[:180]) if head[1:] else "",
            })
            if len(results) >= limit:
                break
        return {"keyword": kw, "found": bool(results), "count": len(results),
                "results": results}

    async def _read(self, path: str = "", **kwargs) -> dict:
        """Fayl matnini o'qish (markdown)."""
        if not path:
            return {"found": False, "error": "path talab qilinadi",
                    "text": ""}
        p = self._resolve(str(path))
        if p.suffix.lower() != ".md" and p.is_dir():
            # Qulaylik: katalog → README-ish matn
            files = sorted(x.name for x in p.glob("*.md"))
            return {"found": True, "path": str(p), "text": "",
                    "files": files, "dir": str(p.relative_to(self.base))}
        if not p.exists():
            return {"found": False, "error": f"Fayl topilmadi: {path}",
                    "text": ""}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"found": False, "error": str(exc), "text": ""}
        return {"found": True, "path": str(p.relative_to(self.base))
                .replace("\\", "/"), "title": self._title_of(p),
                "text": text}

    async def _write(self, title: str = "", category: str = "",
                     content: str = "", **kwargs) -> dict:
        """Yangi qoida/xato yozish.

        category — 'Qoidalar' | 'Sozlamalar' | 'Xatolar' (yoki bo'sh).
        title — fayl nomi (slug). content — markdown matn.
        Xato yozilsa Xato-jurnaliga avtomatik havola qo'shilmaydi —
        faqat yangi fayl ochiladi (jurnalni yangilash keyingi ishda).
        """
        title = str(title or "").strip()
        if not title:
            return {"ok": False, "error": "title talab qilinadi"}
        category = str(category or "").strip("/ \\")
        valid_cats = {"Qoidalar", "Sozlamalar", "Xatolar"}
        if category and category not in valid_cats:
            return {"ok": False,
                    "error": f"category {category} ruxsat etilmagan "
                             f"(mavjud: {', '.join(sorted(valid_cats))})"}
        slug = re.sub(r"[^\w\u0400-\u04FF-]+", "-", title).strip("-") or title
        rel = f"{category}/{slug}.md" if category else f"{slug}.md"
        p = self._resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            timestamp = __import__("time").strftime("%Y%m%d-%H%M%S")
            p = self._resolve(f"{rel.replace('.md', '')}-{timestamp}.md")
        body = f"# {title}\n\n{str(content or '').strip()}\n"
        p.write_text(body, encoding="utf-8")
        log.info("Vault: yozildi %s", p.relative_to(self.base))
        return {"ok": True, "path": str(p.relative_to(self.base))
                .replace("\\", "/"), "title": title}

    async def _list(self, category: str = "", **kwargs) -> dict:
        """Barcha fayllar ro'yxati (kategoriyaga qarab)."""
        files = []
        for p in self._walk():
            rel = str(p.relative_to(self.base)).replace("\\", "/")
            if category and not rel.lower().startswith(
                    str(category).lower() + "/"):
                continue
            files.append({"path": rel, "title": self._title_of(p)})
        return {"count": len(files), "files": files}

    async def _index(self, **kwargs) -> dict:
        """Xato-jurnali va asosiy qoidalar indeksi (qidiruv yordami)."""
        notable = ["Xatolar/Xato-jurnali.md",
                   "Qoidalar/Asosiy-invariantlar.md",
                   "Qoidalar/Tekshirish-qoidalari.md"]
        entries = []
        for rel in notable:
            p = self._resolve(rel)
            if p.exists():
                head = p.read_text(encoding="utf-8",
                                   errors="replace").strip().splitlines()
                entries.append({
                    "path": rel,
                    "title": self._title_of(p),
                    "excerpt": self._linkify(" ".join(head[1:])[:200]),
                })
        return {"count": len(entries), "entries": entries}