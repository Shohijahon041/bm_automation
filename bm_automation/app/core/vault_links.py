"""Bilim bazasi qaydlarini avtomatik bog'lash (Obsidian grafigi uchun).

Har bir qayd oxiriga `<!-- avto-bog'liq:start -->` ... `end` markerlari
orasida "Bog'liq qaydlar" bloki yoziladi. Blok:

- `[[Bilim-bazasi]]` — barcha qaydlar ildiz indeksga ulanadi (graf uzilmaydi);
- mazmuni bo'yicha eng yaqin 4 ta qayd — token kosinus o'xshashligi.

Faqat blok qayta yoziladi; qayd matni, sarlavhalar va kod bloklari
o'zgartirilmaydi. `write_note`/`append_note` dan avtomatik chaqiriladi,
mavjud qaydlar uchun `python -m bm_automation vault-links` (bir marta yoki
timer bilan).
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path

from .knowledge import vault_dir, _walk
from ..utils.logger import get_logger

log = get_logger("bm_automation.vault_links")

ROOT_STEM = "Bilim-bazasi"
MARK_START = "<!-- avto-bog'liq:start -->"
MARK_END = "<!-- avto-bog'liq:end -->"
_BLOCK_RE = re.compile(
    r"\n?<!-- avto-bog'liq:start -->.*?<!-- avto-bog'liq:end -->\n?",
    re.DOTALL,
)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_RE = re.compile(r"`[^`\n]*`")
_WORD_RE = re.compile(r"[a-zA-Z\u0400-\u04FF][a-zA-Z\u0400-\u04FF0-9_-]{2,}")

_STOP = {
    "uchun", "bilan", "yoki", "hamda", "ham", "bu", "shu", "unda", "agar",
    "lekin", "ammo", "har", "bir", "ikki", "uch", "va", "the", "and", "for",
    "that", "this", "with", "from", "are", "was", "not", "you", "all", "dan",
    "ning", "ga", "da", "ni", "bo", "deb", "kabi", "yana", "faqat", "endi",
    "keyin", "oldin", "ichida", "ustida", "orqali", "sifatida", "mumkin",
    "kerak", "shart", "bo", "lsa", "lgan", "lish", "ish", "qilish", "bo'ladi",
    "boladi", "qanday", "nima", "nima", "nega", "qaysi", "barcha", "hammasi",
    "haqida", "yo'q", "yoq", "bor", "edi", "em", "emas", "esa", "chun", "ki",
}


def _plain(text: str) -> str:
    """Kod bloklari va inline kodni olib tashlaydi (o'xshashlik uchun)."""
    text = _FENCE_RE.sub(" ", str(text or ""))
    text = _INLINE_RE.sub(" ", text)
    text = re.sub(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]", r" \1 ", text)
    return text


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(_plain(text).lower()) if w not in _STOP]


def _title_of(path: Path, text: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else path.stem


def _note_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_index(base: Path | None = None) -> dict[str, Counter]:
    """Har bir qayd uchun vaznli token chastotasi (sarlavha 4x)."""
    index: dict[str, Counter] = {}
    for p in _walk(base or vault_dir()):
        text = _BLOCK_RE.sub(" ", _note_text(p))
        body = Counter(_tokens(text))
        head = Counter(_tokens(_title_of(p, text) + " " + p.stem))
        for tok, n in head.items():
            body[tok] += n * 4
        index[p.stem] = body
    return index


def _norm(vec: Counter) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def _similarity(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    return dot / (_norm(a) * _norm(b))


def related_notes(stem: str, index: dict[str, Counter] | None = None,
                  limit: int = 4, min_score: float = 0.03) -> list[str]:
    """Berilgan qaydga eng yaqin qaydlar (o'zi va ildizdan tashqari)."""
    idx = index or build_index()
    own = idx.get(stem)
    if not own:
        return []
    scored = [
        (_similarity(own, vec), other)
        for other, vec in idx.items()
        if other != stem and other != ROOT_STEM
    ]
    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    return [name for score, name in scored if score >= min_score][:limit]


def render_block(stem: str, related: list[str], has_root: bool) -> str:
    lines = [MARK_START, "## Bog'liq qaydlar", ""]
    if has_root and stem != ROOT_STEM:
        lines.append(f"- [[{ROOT_STEM}]] — bosh indeks")
    for name in related:
        lines.append(f"- [[{name}]]")
    lines += ["", MARK_END]
    return "\n".join(lines)


def apply_to_note(path: Path, index: dict[str, Counter] | None = None,
                  limit: int = 4) -> bool:
    """Bitta qaydning avtomatik bog'lanish blokini yangilaydi.

    Qaytaradi: `True` — fayl o'zgartirildi, `False` — o'zgarish yo'q.
    """
    if path.stem == ROOT_STEM or path.suffix.lower() != ".md":
        return False
    try:
        before = path.stat()
    except OSError:
        return False
    old = _note_text(path)
    if not old:
        return False
    idx = index or build_index()
    related = related_notes(path.stem, idx, limit=limit)
    has_root = ROOT_STEM in idx and path.stem != ROOT_STEM
    block = render_block(path.stem, related, has_root)

    cleaned = _BLOCK_RE.sub("", old).rstrip()
    new = f"{cleaned}\n\n{block}\n"
    if new == old:
        return False
    tmp = path.with_name(path.name + ".autolink.tmp")
    try:
        tmp.write_text(new, encoding="utf-8")
        # Parallel yozuv (bot/dashboard) faylni o'zgartirgan bo'lsa — tegmaymiz.
        now = path.stat()
        if (now.st_mtime_ns, now.st_size) != (before.st_mtime_ns, before.st_size):
            return False
        os.replace(tmp, path)
    except OSError as exc:
        # Windows/OneDrive/Obsidian faylni ushlab turgan bo'lishi mumkin —
        # bog'lash bloki keyingi o'tishda yangilanadi (butun vault
        # yangilanishi to'xtamasligi kerak).
        log.debug("Bog'lash bloki yozilmadi (%s): %s", path.name, exc)
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return True


def autolink_all(dry_run: bool = False, limit: int = 4,
                 base: Path | None = None) -> dict:
    """Butun vault'ni qayta bog'laydi (idempotent).

    `dry_run=True` — hech narsa yozilmaydi, faqat hisobot qaytadi.
    `base` — maxsus vault papkasi (standart: `knowledge.vault_dir()`).
    """
    base = Path(base) if base else vault_dir()
    index = build_index(base)
    changed: list[str] = []
    skipped: list[str] = []
    for p in _walk(base):
        if p.stem == ROOT_STEM:
            skipped.append(_rel(p, base))
            continue
        if dry_run:
            old = _note_text(p)
            related = related_notes(p.stem, index, limit=limit)
            block = render_block(p.stem, related, ROOT_STEM in index)
            cleaned = _BLOCK_RE.sub("", old).rstrip()
            if f"{cleaned}\n\n{block}\n" != old:
                changed.append(_rel(p, base))
        elif apply_to_note(p, index=index, limit=limit):
            changed.append(_rel(p, base))
    return {"ok": True, "dry_run": bool(dry_run), "total": len(index),
            "changed": changed, "changed_count": len(changed),
            "skipped": skipped}


def _rel(p: Path, base: Path) -> str:
    return str(p.relative_to(base)).replace("\\", "/")


def autolink_written(path: Path, base: Path | None = None) -> None:
    """Yozuvdan keyin butun vault bog'lanishini yangilaydi (best-effort).

    Yangi qayd boshqa qaydlarning "bog'liq" ro'yxatiga ham kirishi uchun
    to'liq o'tish kerak. Vault kichik (o'nlab fayl) va o'zgarmagan qaydlar
    qayta yozilmaydi — faqat ro'yxati o'zgargan fayllar yangilanadi.
    """
    try:
        autolink_all(base=base)
    except Exception as exc:  # noqa: BLE001 - bog'lash yozuvni to'xtatmasin
        log.warning("Avto-bog'lash xatosi (%s): %s", path.name, exc)
