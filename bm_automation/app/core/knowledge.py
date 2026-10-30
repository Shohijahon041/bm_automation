"""Bilim bazasi (Obsidian vault) — markdown qaydlar ustida sinxron API.

Vault ildizi: loyiha ildizidagi `knowledge_vault/` (yoki `KNOWLEDGE_VAULT_DIR`
env o'zgaruvchisi). Papka oddiy markdown fayllardan iborat — Obsidian ilovasi
uni vault sifatida ochadi, agentlar (`myai/tools/vault.py`), dashboard
"Bilim bazasi" sahifasi va bot `/vault` buyrug'i esa shu modul orqali ishlaydi.

Kategoriyalar: Qoidalar, Sozlamalar, Xatolar, Hisobotlar.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from ..utils.logger import get_logger

log = get_logger("bm_automation.knowledge")

CATEGORIES = ("Qoidalar", "Sozlamalar", "Xatolar", "Hisobotlar")
MAX_NOTE_BYTES = 400_000


def vault_dir() -> Path:
    """Vault papkasi (env bilan qayta belgilash mumkin; papka yaratiladi)."""
    env = (os.environ.get("KNOWLEDGE_VAULT_DIR") or "").strip()
    if env:
        base = Path(env).expanduser()
    else:
        base = Path(__file__).resolve().parents[3] / "knowledge_vault"
    base.mkdir(parents=True, exist_ok=True)
    for cat in CATEGORIES:
        (base / cat).mkdir(exist_ok=True)
    return base


def _resolve(rel: str) -> Path:
    """Vault ichidagi yo'lni ochish (tashqariga chiqishdan himoya).

    Tekshiruv prefiks bo'yicha EMAS: `../vault2/x.md` kabi qo'shni papka
    `/vault` prefiksi bilan boshlanib ketardi. Shuning uchun haqiqiy
    ota-papkalar zanjiri tekshiriladi.
    """
    base = vault_dir().resolve()
    rel = (rel or "").strip().strip("/\\").replace("\\", "/")
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"Vault tashqarisiga chiqish mumkin emas: {rel}")
    return p


def slugify(title: str) -> str:
    """Sarlavhadan xavfsiz fayl nomi (`[[wikilink]]` bilan ishlaydigan)."""
    slug = re.sub(r"[^\w\u0400-\u04FF-]+", "-", str(title or "").strip())
    return slug.strip("-") or "qayd"


def _meta(p: Path, base: Path) -> dict:
    st = p.stat()
    rel = str(p.relative_to(base)).replace("\\", "/")
    parts = rel.split("/")
    return {
        "path": rel,
        "title": p.stem,
        "category": parts[0] if len(parts) > 1 else "",
        "updated": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "size": st.st_size,
    }


def _walk(base: Path | None = None) -> list[Path]:
    base = base or vault_dir()
    return sorted(
        p for p in base.rglob("*.md")
        if p.is_file() and not any(
            part.startswith(".") for part in p.relative_to(base).parts)
    )


def _find_by_stem(name: str) -> Path | None:
    """Obsidian uslubi: `[[Qayd-nomi]]` havolasini papka ichidan topadi.

    Faqat yagona moslik bo'lsa qaytaradi — ikki xil papkada bir xil nom
    bo'lsa, noaniqlikni yechish uchun to'liq yo'l talab qilinadi.
    """
    stem = str(name or "").strip()
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    matches = [p for p in _walk() if p.stem.lower() == stem.lower()]
    return matches[0] if len(matches) == 1 else None


def list_notes(category: str = "") -> dict:
    """Qaydlar ro'yxati (kategoriya bo'yicha; `category` bo'sh = hammasi)."""
    base = vault_dir()
    cat = str(category or "").strip()
    files = []
    for p in _walk(base):
        item = _meta(p, base)
        if cat and item["category"].lower() != cat.lower():
            continue
        files.append(item)
    files.sort(key=lambda x: x["updated"], reverse=True)
    counts = {}
    for item in files:
        counts[item["category"] or "—"] = counts.get(item["category"] or "—", 0) + 1
    return {"ok": True, "count": len(files), "files": files,
            "categories": list(CATEGORIES), "counts": counts}


def search_notes(keyword: str = "", limit: int = 20) -> dict:
    """Matn bo'yicha qidirish (sarlavha + tarkib)."""
    base = vault_dir()
    kw = str(keyword or "").strip().lower()
    limit = max(1, min(int(limit or 20), 100))
    results = []
    for p in _walk(base):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hay = (text + "\n" + p.stem).lower()
        if kw and kw not in hay:
            continue
        item = _meta(p, base)
        needle = hay.find(kw) if kw else 0
        snippet = text[max(0, needle - 60): needle + 140] if kw else text[:160]
        item["snippet"] = " ".join(snippet.split())
        results.append(item)
        if len(results) >= limit:
            break
    return {"ok": True, "keyword": kw, "count": len(results), "results": results}


def read_note(path: str) -> dict:
    """Bitta qaydni o'qish."""
    if not str(path or "").strip():
        return {"ok": False, "error": "path talab qilinadi"}
    p = _resolve(str(path))
    if not p.exists() and "/" not in str(path) and "\\" not in str(path):
        alt = _find_by_stem(str(path))
        if alt is not None:
            p = alt
    if p.is_dir():
        return {"ok": True, "path": str(p.name), "text": "",
                "files": sorted(x.name for x in p.glob("*.md")), "dir": True}
    if not p.exists():
        return {"ok": False, "error": f"Fayl topilmadi: {path}"}
    if p.suffix.lower() != ".md":
        return {"ok": False, "error": "faqat .md fayllar"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    meta = _meta(p, vault_dir())
    meta.update({"ok": True, "text": text})
    return meta


def write_note(title: str, category: str = "", content: str = "",
               overwrite: bool = False, path: str = "") -> dict:
    """Yangi qayd yozadi (yoki `overwrite` bilan mavjudini almashtiradi).

    `path` berilsa — aynan shu fayl yoziladi (mavjudini tahrirlash uchun).
    """
    title = str(title or "").strip()
    if not title:
        return {"ok": False, "error": "Sarlavha bo'sh"}
    cat = str(category or "").strip().strip("/\\")
    if cat and cat not in CATEGORIES:
        return {"ok": False,
                "error": f"Kategoriya ruxsat etilmagan: {cat} "
                         f"(mavjud: {', '.join(CATEGORIES)})"}
    body = str(content or "")
    if len(body.encode("utf-8")) > MAX_NOTE_BYTES:
        return {"ok": False, "error": "Matn juda katta (400 KB dan oshdi)"}
    rel = str(path or "").strip().strip("/\\").replace("\\", "/")
    if rel:
        if not rel.lower().endswith(".md"):
            rel += ".md"
    else:
        rel = f"{cat}/{slugify(title)}.md" if cat else f"{slugify(title)}.md"
    p = _resolve(rel)
    if p.exists() and not overwrite:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel = rel[:-3] + f"-{stamp}.md"
        p = _resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = body if body.lstrip().startswith("#") else f"# {title}\n\n{body.strip()}\n"
    p.write_text(text, encoding="utf-8")
    log.info("Bilim bazasi: yozildi %s", rel)
    from .vault_links import autolink_written
    autolink_written(p)
    meta = _meta(p, vault_dir())
    meta.update({"ok": True})
    return meta


def delete_note(path: str) -> dict:
    """Qaydni o'chiradi (faqat vault ichidagi `.md` fayl)."""
    rel = str(path or "").strip()
    if not rel:
        return {"ok": False, "error": "path talab qilinadi"}
    p = _resolve(rel)
    if not p.exists():
        return {"ok": False, "error": f"Fayl topilmadi: {path}"}
    if p.is_dir() or p.suffix.lower() != ".md":
        return {"ok": False, "error": "faqat .md fayllarni o'chirish mumkin"}
    try:
        p.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    rel_path = str(p.relative_to(vault_dir())).replace("\\", "/")
    log.info("Bilim bazasi: o'chirildi %s", rel_path)
    return {"ok": True, "path": rel_path}


def append_note(path: str, text: str, title: str = "") -> dict:
    """Mavjud qaydga matn qo'shadi (yo'q bo'lsa yaratadi)."""
    p = _resolve(str(path or ""))
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "Qo'shiladigan matn bo'sh"}
    if p.exists():
        old = p.read_text(encoding="utf-8", errors="replace").rstrip() + "\n\n"
        p.write_text(old + body + "\n", encoding="utf-8")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        head = title or p.stem
        p.write_text(f"# {head}\n\n{body}\n", encoding="utf-8")
    from .vault_links import autolink_written
    autolink_written(p)
    meta = _meta(p, vault_dir())
    meta.update({"ok": True})
    return meta


def stats() -> dict:
    """Vault statistikasi (dashboard sarlavhasi uchun)."""
    base = vault_dir()
    files = _walk(base)
    total = sum(f.stat().st_size for f in files)
    return {"ok": True, "dir": str(base), "count": len(files),
            "bytes": total, "categories": list(CATEGORIES)}


def save_report(name: str, text: str, category: str = "Hisobotlar",
                period: str = "") -> dict:
    """Hisobotni vault'ga markdown sifatida saqlaydi (avtomatik).

    Fayl nomi: `<period>/<name>.md` ko'rinishida emas — kategoriya ichida
    `<name>` + sana qo'shiladi, shunda tarix saqlanadi.
    """
    title = str(name or "hisobot").strip()
    if period:
        title = f"{title} {period}".strip()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = f"{str(text or '').strip()}\n\n_saqlangan: {stamp}_\n"
    return write_note(title=title, category=category, content=body)


_WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")
# Kod bloki/ichi kod ichidagi misollar havola hisoblanmaydi: hujjatlar
# `[[Qayd-nomi]]` ni namuna sifatida yozadi va u yechilmagan havola
# bo'lib qolsa, avtomatik kengaytirish keraksiz bo'sh qayd ochardi.
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _wikilinks(text: str) -> list[str]:
    """Matndagi `[[havola]]` nishonlari (alias/#sarlavha olib tashlanadi).

    Kod bloklari va inline kod ichidagi havolalar e'tiborga olinmaydi —
    ular misol, haqiqiy bog'lanish emas.
    """
    body = _CODE_BLOCK_RE.sub(" ", str(text or ""))
    body = _INLINE_CODE_RE.sub(" ", body)
    out: list[str] = []
    for raw in _WIKILINK_RE.findall(body):
        target = raw.split("|")[0].split("#")[0].strip()
        if target:
            out.append(target)
    return out


def graph() -> dict:
    """Obsidian grafigi: tugunlar (qaydlar) va qirralar (`[[havolalar]]`).

    Havola nishoni avval to'liq yo'l, keyin yagona fayl nomi sifatida
    aniqlanadi. Yechilmagan havolalar qirra bo'lmaydi — `unresolved`da
    qaytadi (uzilgan grafik ko'rinmasligi uchun).
    """
    base = vault_dir()
    notes: dict[str, dict] = {}
    by_path: dict[str, str] = {}
    by_stem: dict[str, list[str]] = {}
    for p in _walk(base):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(base)).replace("\\", "/")
        notes[p.stem] = {**_meta(p, base), "id": p.stem,
                         "links": 0, "backlinks": 0}
        by_path[rel.lower()] = p.stem
        by_stem.setdefault(p.stem.lower(), []).append(p.stem)
        notes[p.stem]["_targets"] = _wikilinks(text)

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    unresolved: list[dict] = []
    for stem, item in notes.items():
        for target in item.pop("_targets", []):
            key = target.strip().strip("/").lower()
            other = by_path.get(key) or by_path.get(key + ".md")
            if not other:
                hits = by_stem.get(key, [])
                other = hits[0] if len(hits) == 1 else ""
            if not other:
                unresolved.append({"from": stem, "target": target})
                continue
            if other == stem or (stem, other) in seen:
                continue
            seen.add((stem, other))
            edges.append({"source": stem, "target": other})

    for e in edges:
        notes[e["source"]]["links"] += 1
        notes[e["target"]]["backlinks"] += 1
    return {"ok": True, "count": len(notes), "edge_count": len(edges),
            "nodes": list(notes.values()), "edges": edges,
            "unresolved": unresolved,
            "categories": list(CATEGORIES)}


def graph_status(g: dict | None = None, exclude: set[str] | None = None,
                 include_autolinks: bool = True) -> dict:
    """Grafik salomatligi: yetim qaydlar, uzilgan havolalar, markazlar.

    `include_autolinks=True` (panel) — Obsidian grafigidagi barcha qirralar
    (matn + avtomatik "Bog'liq qaydlar" bloki). `False` — faqat matn
    havolalari; bu rejim **barqaror** (avto-bloklar keyin qo'shilsa ham
    raqamlar o'zgarmaydi), shuning uchun `Grafik-holati` qaydi shu rejimda.

    `isolated` — hech qanday havolasi yo'q qaydlar; `orphans` — matnida
    havolasi yo'q va hech kim matnda havola qilmagan qaydlar; `auto_only` —
    shulardan faqat avto-blok orqali ulanganlari (avtomatik hisobotlar).

    `exclude` — hisobdan chiqariladigan qaydlar (masalan, `Grafik-holati`
    qaydi o'zini sanamasligi uchun).
    """
    from .vault_links import _BLOCK_RE

    skip = {str(x) for x in (exclude or ())}
    data = g if isinstance(g, dict) else graph()
    nodes = [n for n in (data.get("nodes") or []) if n.get("id") not in skip]
    full_edges = [e for e in (data.get("edges") or [])
                  if e.get("source") not in skip
                  and e.get("target") not in skip]
    def _target_stem(raw: str) -> str:
        return str(raw or "").strip().strip("/").rsplit("/", 1)[-1].removesuffix(".md")

    unresolved = [u for u in (data.get("unresolved") or [])
                  if u.get("from") not in skip
                  and _target_stem(u.get("target")) not in skip]

    # Matn havolalari (avto-bog'lash bloki hisobga olinmaydi).
    base = vault_dir()
    body_out: dict[str, list[str]] = {}
    stems: dict[str, list[str]] = {}
    paths: dict[str, str] = {}
    for p in _walk(base):
        if p.stem in skip:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(base)).replace("\\", "/")
        body_out[p.stem] = _wikilinks(_BLOCK_RE.sub(" ", text))
        stems.setdefault(p.stem.lower(), []).append(p.stem)
        paths[rel.lower()] = p.stem
    body_in = {stem: 0 for stem in body_out}
    body_degree = {stem: 0 for stem in body_out}
    body_pairs: set[tuple[str, str]] = set()
    for stem, targets in body_out.items():
        for target in targets:
            key = target.strip().strip("/").lower()
            other = paths.get(key) or paths.get(key + ".md") or ""
            if not other:
                hits = stems.get(key, [])
                other = hits[0] if len(hits) == 1 else ""
            if not other or other == stem:
                continue
            body_pairs.add((stem, other))
            body_in[other] += 1
            body_degree[stem] += 1
            body_degree[other] += 1
    orphans = sorted(s for s in body_out
                     if not body_out[s] and not body_in.get(s))

    if include_autolinks:
        edges = full_edges
        isolated = sorted(n["id"] for n in nodes
                          if not any(e.get("source") == n["id"]
                                     or e.get("target") == n["id"]
                                     for e in edges))
        iso_set = set(isolated)
        auto_only = [s for s in orphans if s not in iso_set]
    else:
        edges = [{"source": s, "target": t} for s, t in sorted(body_pairs)]
        isolated = sorted(s for s in body_out if body_degree.get(s, 0) == 0)
        # Avto-blok tufayli grafda ulangan, lekin matnda havolasi yo'qlar.
        linked_anywhere = {e.get("source") for e in full_edges} | {
            e.get("target") for e in full_edges}
        auto_only = [s for s in isolated if s in linked_anywhere]

    out_deg = {n["id"]: 0 for n in nodes}
    in_deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        src, dst = e.get("source"), e.get("target")
        if src in out_deg and dst in in_deg:
            out_deg[src] += 1
            in_deg[dst] += 1
    degree = {nid: out_deg[nid] + in_deg[nid] for nid in out_deg}

    cat_of = {n["id"]: (n.get("category") or "—") for n in nodes}
    categories: dict[str, dict] = {}
    for n in nodes:
        rec = categories.setdefault(cat_of[n["id"]],
                                    {"notes": 0, "internal_edges": 0})
        rec["notes"] += 1
    for e in edges:
        a, b = cat_of.get(e.get("source")), cat_of.get(e.get("target"))
        if a and a == b:
            categories[a]["internal_edges"] += 1

    hubs = sorted(nodes, key=lambda n: (-(degree[n["id"]]), n["id"]))[:5]
    count = len(nodes)
    edge_count = len(edges)
    return {
        "ok": True,
        "notes": count,
        "edges": edge_count,
        "body_edges": len(body_pairs),
        "auto_edges": max(0, len(full_edges) - len(body_pairs)),
        "unresolved": len(unresolved),
        "unresolved_list": unresolved[:10],
        "isolated": len(isolated),
        "isolated_list": isolated[:10],
        "orphans": len(orphans),
        "orphan_list": orphans[:10],
        "auto_only": len(auto_only),
        "auto_only_list": auto_only[:10],
        "hubs": [{"id": n["id"], "title": n.get("title") or n["id"],
                  "path": n.get("path") or "", "category": cat_of[n["id"]],
                  "links": out_deg[n["id"]],
                  "backlinks": in_deg[n["id"]],
                  "degree": degree[n["id"]]} for n in hubs],
        "categories": categories,
        "avg_degree": round(2 * edge_count / count, 2) if count else 0.0,
        "density": (round(2 * edge_count / (count * (count - 1)), 4)
                    if count > 1 else 0.0),
        "health": ("sog'lom"
                   if not unresolved
                   and len(isolated) - len(auto_only) <= 0 else "e'tibor"),
    }


def index_note() -> str:
    """Vault indeksi (MOC) — Obsidian'da kirish nuqtasi bo'lib xizmat qiladi."""
    base = vault_dir()
    lines = ["# Bilim bazasi", "",
             "BM Automation loyihasining bilim bazasi: qoidalar, sozlamalar, "
             "xato jurnali va hisobotlar.", ""]
    for cat in CATEGORIES:
        items = sorted((base / cat).glob("*.md")) if (base / cat).is_dir() else []
        lines.append(f"## {cat}")
        if not items:
            lines.append("_hali bo'sh_")
        for p in items:
            lines.append(f"- [[{cat}/{p.stem}]]")
        lines.append("")
    return "\n".join(lines)
