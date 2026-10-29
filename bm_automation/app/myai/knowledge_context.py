"""Agentlar uchun bilim bazasi konteksti (Obsidian vault).

Vazifa qadamidan oldin vault'dan (qoidalar, tasdiqlangan xatolar) eng
tegishli qaydlar tanlanadi va agent promptiga qo'shiladi. Shu bilan
agentlar o'z xatolaridan o'rganadi: reviewer yozgan dars keyingi
vazifada boshqa agentlarga kontekst bo'ladi.

LLM shart emas — tanlov token o'xshashligi bo'yicha deterministik.
O'chirish uchun `MYAI_KNOWLEDGE=off`.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections import Counter
from pathlib import Path

from ..core import vault_links
from ..core.knowledge import _walk, vault_dir
from ..utils.logger import get_logger

log = get_logger("myai.knowledge")

# Prioritet: avval qoidalar va aniqlangan xatolar, keyin sozlamalar.
PREFERRED = ("Qoidalar", "Xatolar", "Sozlamalar")
MAX_EXCERPT = 220
MAX_BRIEF_CHARS = 1400

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_WORD_RE = re.compile(r"[a-zA-Z\u0400-\u04FF0-9][a-zA-Z\u0400-\u04FF0-9_-]{2,}")
_STOP = {
    "uchun", "bilan", "yoki", "ham", "bu", "shu", "unda", "agar", "lekin",
    "har", "bir", "va", "the", "and", "for", "that", "this", "with", "from",
    "ning", "dan", "deb", "kabi", "yana", "faqat", "endi", "keyin", "oldin",
    "ichida", "orqali", "mumkin", "kerak", "shart", "qanday", "nima", "barcha",
    "hammasi", "haqida", "emas", "esa", "edi", "bor", "yoq",
}

# Indeks keshi — vault o'zgarmasa qayta o'qilmaydi.
_CACHE: dict = {"at": 0.0, "index": {}, "text": {}, "cats": {}}


def enabled() -> bool:
    """Bilim bazasi konteksti yoqilganmi (`MYAI_KNOWLEDGE=off` bilan o'chadi)."""
    return (os.environ.get("MYAI_KNOWLEDGE", "on") or "").strip().lower() not in (
        "0", "off", "false", "no", "yo'q")


def clear_cache() -> None:
    """Keshni tozalash (testlar va yozuvdan keyin)."""
    _CACHE.update({"at": 0.0, "index": {}, "text": {}, "cats": {}})


def _tokens(text: str) -> Counter:
    """Matnni vaznli token oqimiga aylantiradi (stop-so'zlar olib tashlanadi)."""
    words = [w for w in _WORD_RE.findall(str(text or "").lower()) if w not in _STOP]
    return Counter(words)


def _plain(text: str) -> str:
    """Qayd matnidan kod bloklari, izohlar va havolalarni tozalash."""
    text = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _WIKILINK_RE.sub(r"\1", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "|", ">", "-", "*", "_")):
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _norm(vec: Counter) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def _cosine(a: Counter, b: Counter) -> float:
    """Ikki token vektori orasidagi kosinus o'xshashligi (0..1)."""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    return dot / (_norm(a) * _norm(b))


def _index() -> dict:
    """Vault indeksi: {stem: {"vec", "text", "category", "path"}} (keshlangan)."""
    ttl = float(os.environ.get("MYAI_KNOWLEDGE_TTL", "60") or 60)
    if _CACHE["index"] and (time.time() - _CACHE["at"]) < ttl:
        return _CACHE["index"]
    base = vault_dir()
    out: dict = {}
    for p in _walk(base):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(base)).replace("\\", "/")
        parts = rel.split("/")
        title = p.stem
        body = _plain(raw)
        vec = _tokens(body)
        for tok, n in _tokens(title).items():
            vec[tok] += n * 3          # sarlavha mazmundan muhimroq
        out[title] = {
            "title": title,
            "path": rel,
            "category": parts[0] if len(parts) > 1 else "",
            "text": body,
            "vec": vec,
        }
    _CACHE.update({"at": time.time(), "index": out})
    return out


PRIMARY_NOTE = "Qoidalar/Asosiy-invariantlar.md"


def relevant_notes(query: str = "", limit: int = 3,
                   categories: tuple[str, ...] | list[str] | None = None) -> list[dict]:
    """So'rovga eng tegishli qaydlar (token o'xshashligi bo'yicha).

    Mos keladigan qayd topilmasa — asosiy invariantlar qaytariladi
    (agent baribir qoidalarni ko'rib ishlashi uchun).
    """
    if not enabled():
        return []
    idx = _index()
    if not idx:
        return []
    cats = tuple(categories) if categories else PREFERRED
    qvec = _tokens(query)
    scored: list[tuple[float, dict]] = []
    for item in idx.values():
        score = _cosine(qvec, item["vec"])
        if item["category"] in cats:
            score *= 1.15
        scored.append((score, item))
    scored.sort(key=lambda x: (-x[0], x[1]["title"].lower()))
    picked = [item for score, item in scored if score > 0.01][:max(1, limit)]
    if not picked:
        picked = [it for it in idx.values() if it["path"] == PRIMARY_NOTE]
    out = []
    for item in picked:
        out.append({
            "title": item["title"],
            "path": item["path"],
            "category": item["category"],
            "excerpt": item["text"][:MAX_EXCERPT],
        })
    return out


def render(notes: list[dict], max_chars: int = MAX_BRIEF_CHARS) -> str:
    """Qaydlarni prompt uchun qisqa matnga aylantiradi."""
    if not notes:
        return ""
    lines = ["Bilim bazasi (Obsidian qoidalar/xatolar) — amal qiling:"]
    used = len(lines[0])
    for n in notes:
        row = f"- [{n.get('path') or n.get('title')}] {n.get('excerpt', '')}"
        if used + len(row) > max_chars:
            break
        lines.append(row)
        used += len(row)
    return "\n".join(lines) if len(lines) > 1 else ""


def brief(query: str = "", limit: int = 3) -> str:
    """Agent promptiga qo'shiladigan bilim bazasi bloki."""
    try:
        return render(relevant_notes(query, limit=limit))
    except Exception as exc:  # noqa: BLE001 - kontekst vazifani to'xtatmasin
        log.debug("Bilim bazasi konteksti yig'ilmadi: %s", exc)
        return ""


def lesson_hint(tool: str, action: str = "", limit: int = 2) -> str:
    """Muvaffaqiyatsiz tool uchun vault'dagi tegishli dars (matn yoki '')."""
    try:
        notes = relevant_notes(f"{tool} {action} xato", limit=limit,
                               categories=("Xatolar",))
        return render(notes, max_chars=600)
    except Exception as exc:  # noqa: BLE001
        log.debug("Vault darsi topilmadi: %s", exc)
        return ""
