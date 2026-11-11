"""Vault Tool — Obsidian knowledge vault bilan ishlash.

Agentlar "qoidalar" (invariantlar) va "xatolar" jurnalini markdown
fayllardan o'qiydi; tasdiqlangan xatoni esa omborga yozib qo'yadi.

Vault ildizi: `core.knowledge.vault_dir()` (`KNOWLEDGE_VAULT_DIR` hisobga
olinadi) yoki `base_dir` orqali berilgan maxsus papka — testlarda ishlatiladi.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import BaseTool
from ..scope import is_admin, scope_denied
from ...core import knowledge


def _default_vault_dir() -> Path:
    """Dashboard va agentlar bir xil vault sozlamasidan foydalanadi."""
    return knowledge.vault_dir()


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
        self.base = (Path(base_dir).expanduser() if base_dir
                     else _default_vault_dir()).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    # ── ichki ──────────────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        """Vault ichidagi fayl yo'lini ochish (traversal dan himoya)."""
        return knowledge._resolve(rel, base=self.base)

    def _walk(self) -> list[Path]:
        return knowledge._walk(self.base)

    @staticmethod
    def _title_of(path: Path) -> str:
        return path.stem

    def _linkify(self, text: str) -> str:
        return WIKILINK_RE.sub(r"\1", text)

    # ── actions ────────────────────────────────────────────────────────
    async def execute(self, action: str = "", **kwargs) -> Any:
        if not is_admin():
            return scope_denied("Umumiy bilim bazasi korxona vazifalariga yopiq")
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
        limit = max(1, min(int(limit or 10), 100))
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
        result = knowledge.read_note(path, base=self.base)
        result["found"] = result.pop("ok", False)
        result.setdefault("text", "")
        if result.get("dir"):
            result["dir"] = result["path"]
        return result

    async def _write(self, title: str = "", category: str = "",
                     content: str = "", **kwargs) -> dict:
        """Yangi qoida/xato yozish.

        category — 'Qoidalar' | 'Sozlamalar' | 'Xatolar' (yoki bo'sh).
        title — fayl nomi (slug). content — markdown matn.
        Xato yozilsa Xato-jurnaliga avtomatik havola qo'shilmaydi —
        faqat yangi fayl ochiladi (jurnalni yangilash keyingi ishda).
        """
        result = knowledge.write_note(title, category, content, base=self.base)
        if result.get("ok"):
            result["title"] = str(title).strip()
        return result

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
            try:
                result = await self._read(path=rel)
            except (ValueError, OSError):
                continue
            if result["found"] and not result.get("dir"):
                head = result["text"].strip().splitlines()
                entries.append({
                    "path": result["path"],
                    "title": result["title"],
                    "excerpt": self._linkify(" ".join(head[1:])[:200]),
                })
        return {"count": len(entries), "entries": entries}