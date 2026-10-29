"""Bilim bazasini (Obsidian vault) avtomatik kengaytirish dvigateli.

Vault qo'lda yozilgan qaydlar bilan birga **o'zini o'zi** kengaytiradi:

1. `kod_xaritasi()` — loyiha kodi skanerlanadi va `Hisobotlar/` ichiga
   avtomatik qaydlar yoziladi (modul xaritasi, API endpointlar, bot
   buyruqlari, testlar, env sozlamalari, DB jadvallari).
2. `runtime_snapshot()` — `state/` fayllari va vault statistikasi asosida
   joriy holat qaydi yangilanadi.
3. `log_tahlili()` — `logs/bm.log` dagi ERROR/WARNING lar guruhlanadi va
   xato jurnali yangilanadi.
4. `boshliq_toldirish()` — qaydlardagi yechilmagan `[[havola]]` lar uchun
   bo'sh qayd (stub) ochiladi (graf uzilmaydi).

Agentlar ishlashi davomida `learn_error()` va `learn_task()` orqali yangi
darslar qo'shadi — ya'ni bilim bazasi har doim o'sib boradi.

Boshqarish: `KNOWLEDGE_AUTO=off` (yozishni o'chiradi),
`KNOWLEDGE_AUTO_INTERVAL_MIN` (standart 180 daqiqa).
Avtomatik yaratilgan qaydlar `avto-yaratilgan` belgisiga ega — qo'lda
tahrir keyingi yangilanishda yo'qoladi (nusxasini alohida qaydga ko'chiring).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .knowledge import CATEGORIES, _resolve, _walk, slugify, vault_dir
from .vault_links import autolink_all
from ..utils.io import atomic_write
from ..utils.logger import get_logger

log = get_logger("bm_automation.knowledge_grow")

AUTO_MARK = "<!-- avto-yaratilgan: {key} -->"
AUTO_NOTE_NOTE = (
    "_Bu qayd avtomatik yangilanadi (`knowledge_grow`) — qo'lda tahrir "
    "keyingi kengaytirishda ustidan yoziladi._"
)

# Avtomatik qaydlar: kalit → (kategoriya/nom, sarlavha)
MANAGED = {
    "kod-xaritasi": ("Hisobotlar/Kod-xaritasi", "Kod xaritasi"),
    "api-endpointlar": ("Hisobotlar/API-endpointlar", "API endpointlar"),
    "bot-buyruqlari": ("Hisobotlar/Bot-buyruqlari", "Bot buyruqlari"),
    "testlar": ("Hisobotlar/Testlar", "Testlar"),
    "sozlamalar": ("Hisobotlar/Muhit-sozlamalari", "Muhit sozlamalari"),
    "db-jadvallari": ("Hisobotlar/DB-jadvallari", "DB jadvallari"),
    "holat-snapshoti": ("Hisobotlar/Holat-snapshoti", "Holat snapshoti"),
    "log-tahlili": ("Xatolar/Log-tahlili", "Log tahlili"),
    "avto-xato-jurnali": ("Xatolar/Avto-xato-jurnali", "Avto xato jurnali"),
    "agent-vazifalari": ("Hisobotlar/Agent-vazifalari", "Agent vazifalari"),
    "grafik-holati": ("Hisobotlar/Grafik-holati", "Grafik holati"),
}

MAX_HARVEST_FILES = 4000


def enabled() -> bool:
    """Avtomatik kengaytirish yoqilganmi (standart: on)."""
    raw = (os.environ.get("KNOWLEDGE_AUTO", "on") or "").strip().lower()
    return raw not in ("0", "off", "false", "no", "yo'q")


def interval_sec() -> int:
    """Ikki kengaytirish orasidagi minimal vaqt (sekund)."""
    try:
        minutes = float(os.environ.get("KNOWLEDGE_AUTO_INTERVAL_MIN", "180") or 180)
    except (TypeError, ValueError):
        minutes = 180.0
    return int(max(1.0, minutes) * 60)


def project_root() -> Path:
    """Loyiha ildizi (`bm_automation/app/core/` dan uch pog'ona yuqori)."""
    return Path(__file__).resolve().parents[3]


def _state_dir() -> Path:
    """Holat papkasi (`state/`; testlar `KNOWLEDGE_GROW_STATE_DIR` bilan izolyatsiya qiladi)."""
    env = (os.environ.get("KNOWLEDGE_GROW_STATE_DIR") or "").strip()
    d = Path(env) if env else Path("state")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_file(name: str) -> Path:
    return _state_dir() / name


def _read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    try:
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - holat yozilmasa ish to'xtamasin
        log.debug("Holat yozilmadi (%s): %s", path.name, exc)


# ── qayd yozuvchi ────────────────────────────────────────────────────────


def _upsert(key: str, body_lines: list[str]) -> dict:
    """Avtomatik qaydni yangilaydi (mazmun bir xil bo'lsa tegmaydi).

    Qaytaradi: `{"path": rel, "changed": bool}`.
    """
    rel, title = MANAGED[key]
    base = vault_dir()
    p = _resolve(f"{rel}.md")
    head = [AUTO_MARK.format(key=key), f"# {title}", "", AUTO_NOTE_NOTE, ""]
    new = "\n".join(head + body_lines).rstrip() + "\n"
    old = ""
    if p.exists():
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
    # Avto-bog'lash bloki o'zgarmagan deb hisoblanishi uchun uni olib tashlaymiz.
    old_body = re.sub(
        r"\n?<!-- avto-bog'liq:start -->.*?<!-- avto-bog'liq:end -->\n?",
        "\n", old, flags=re.S).strip()
    if old_body == new.strip():
        return {"path": str(p.relative_to(base)).replace("\\", "/"),
                "changed": False}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new, encoding="utf-8")
    return {"path": str(p.relative_to(base)).replace("\\", "/"), "changed": True}


def _py_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        parts = p.relative_to(root).parts
        if any(x in ("__pycache__", ".git", ".venv", "venv", "node_modules")
               for x in parts):
            continue
        out.append(p)
        if len(out) >= MAX_HARVEST_FILES:
            break
    return out


def _first_doc_line(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.match(r'\s*(?:"""|\'\'\')\s*(.+?)\s*(?:"""|\'\'\')?\s*$', text)
    return m.group(1).strip().strip('"\'') if m else ""


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 1. kod xaritasi ──────────────────────────────────────────────────────


def kod_xaritasi() -> dict:
    """Loyiha kodini skanerlab `Hisobotlar/Kod-xaritasi` qaydini yangilaydi."""
    root = project_root()
    pkg = root / "bm_automation"
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    total_py = total_loc = 0
    groups: dict[str, dict] = {}
    for p in _py_files(pkg):
        rel = p.relative_to(pkg).as_posix()
        top = rel.split("/")[0] if "/" in rel else "(ildiz)"
        grp = groups.setdefault(top, {"files": 0, "loc": 0, "items": []})
        loc = _count_lines(p)
        grp["files"] += 1
        grp["loc"] += loc
        total_py += 1
        total_loc += loc
        if rel.count("/") <= 1:
            doc = _first_doc_line(p)
            grp["items"].append(f"- `{rel}` ({loc} qator)" + (f" — {doc}" if doc else ""))
    lines += [f"Jami: **{total_py}** python fayl, **{total_loc}** qator.", ""]
    for top in sorted(groups, key=lambda k: -groups[k]["loc"]):
        g = groups[top]
        lines.append(f"## {top} ({g['files']} fayl, {g['loc']} qator)")
        lines.append("")
        lines += sorted(g["items"]) or ["_(ichki modullar)_"]
        lines.append("")
    tests = _py_files(root / "tests") if (root / "tests").is_dir() else []
    lines += ["## Testlar papkasi", "",
              f"- `tests/` — {len(tests)} fayl, "
              f"{sum(_count_lines(p) for p in tests)} qator.", ""]
    return _upsert("kod-xaritasi", lines)


_ROUTE_RE = re.compile(
    r'@router\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']')


def api_xaritasi() -> dict:
    """Dashboard/routerlardagi HTTP endpointlarni yig'adi."""
    root = project_root() / "bm_automation"
    found: dict[str, list[str]] = {}
    for p in _py_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = _ROUTE_RE.findall(text)
        if not hits:
            continue
        rel = p.relative_to(project_root()).as_posix()
        found.setdefault(rel, [])
        for method, path in hits:
            found[rel].append(f"`{method.upper():6}` `{path}`")
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    total = sum(len(v) for v in found.values())
    lines.append(f"Jami **{total}** endpoint, {len(found)} modulda.")
    lines.append("")
    for rel in sorted(found):
        lines.append(f"## `{rel}`")
        lines.append("")
        lines += [f"- {x}" for x in sorted(set(found[rel]))]
        lines.append("")
    return _upsert("api-endpointlar", lines)


_CMD_RE = re.compile(r'["\'](/[a-z][a-z0-9_]{1,24})["\']')
# Dispatcher buyruqni avval bo'sh so'zga aylantiradi (`cmd = ...lstrip("/")`)
# va `cmd == "today"` ko'rinishida taqoslaydi — shuning uchun asosiy manba
# shu taqoslashlar, slash shakli esa foydalanuvchi ko'radigan nom.
_CMD_EQ_RE = re.compile(r'\bcmd\s*==\s*["\']([a-z][a-z0-9_]{1,24})["\']')
_CMD_IN_RE = re.compile(r'\bcmd\s+in\s*\(([^)]{1,200})\)')
_QUOTED_RE = re.compile(r'["\']([a-z][a-z0-9_]{1,24})["\']')


def bot_buyruqlari() -> dict:
    """Telegram bot buyruqlarini (dispatcher va literal matnlardan) yig'adi."""
    root = project_root() / "bm_automation"
    per_file: dict[str, set[str]] = {}
    for p in _py_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cmds = {m.lstrip("/") for m in _CMD_RE.findall(text)}
        cmds |= set(_CMD_EQ_RE.findall(text))
        for group in _CMD_IN_RE.findall(text):
            cmds |= set(_QUOTED_RE.findall(group))
        cmds = {c for c in cmds if c and c.islower()}
        if cmds:
            per_file[p.relative_to(project_root()).as_posix()] = cmds
    all_cmds = sorted({c for v in per_file.values() for c in v})
    lines: list[str] = [f"_yangilandi: {_now()}_", "",
                        f"Jami **{len(all_cmds)}** buyruq topildi "
                        "(slash shaklda foydalanuvchi yozadi).", ""]
    lines.append("## Buyruqlar")
    lines.append("")
    lines += [f"- `/{c}`" for c in all_cmds]
    lines.append("")
    lines.append("Matn ko'rinishidagi menyu tugmalari "
                 "`dispatch.py::MENU_MAP` da (masalan `📊 dashboard`).")
    lines.append("")
    for rel in sorted(per_file):
        lines.append(f"### `{rel}`")
        lines.append("")
        lines += [f"- `/{c}`" for c in sorted(per_file[rel])]
        lines.append("")
    return _upsert("bot-buyruqlari", lines)


# ── 2. testlar, sozlamalar, DB jadvallari ────────────────────────────────

_TEST_DEF_RE = re.compile(r"^\s*(?:async )?def (test_[A-Za-z0-9_]+)", re.M)


def testlar_xaritasi() -> dict:
    """`tests/` papkasini skanerlab test xaritasini yangilaydi."""
    tdir = project_root() / "tests"
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    if not tdir.is_dir():
        lines.append("_testlar papkasi topilmadi_")
        return _upsert("testlar", lines)
    items: list[tuple[str, int, int]] = []
    total_tests = 0
    for p in _py_files(tdir):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = len(_TEST_DEF_RE.findall(text))
        rel = p.relative_to(project_root()).as_posix()
        items.append((rel, n, _count_lines(p)))
        if not p.name.startswith("test_"):
            continue
        total_tests += n
    lines.append(f"Jami **{total_tests}** test funksiyasi, "
                 f"{len(items)} fayl.")
    lines.append("")
    for rel, n, loc in sorted(items, key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{rel}` — {n} test, {loc} qator")
    lines.append("")
    lines.append("Ishga tushirish: `python -m pytest tests/ -q` "
                 "(CI: Python 3.12, `BM_DB_DRIVER=sqlite`, `BM_TEST_MODE=1`).")
    return _upsert("testlar", lines)


_ENV_RE = re.compile(
    r'os\.(?:environ\.get|getenv)\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']\s*(?:,\s*([^)\n]+))?')


def sozlamalar_xaritasi() -> dict:
    """Koddagi env o'zgaruvchilarni (standart qiymati bilan) yig'adi."""
    root = project_root() / "bm_automation"
    found: dict[str, str] = {}
    for p in _py_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, default in _ENV_RE.findall(text):
            d = (default or "").strip().strip('"\'')
            found.setdefault(name, d)
    lines: list[str] = [f"_yangilandi: {_now()}_", "",
                        f"Jami **{len(found)}** env o'zgaruvchi.", ""]
    lines.append("| O'ZGARUVCHI | STANDART |")
    lines.append("| --- | --- |")
    for name in sorted(found):
        d = found[name]
        d = d if d and d not in ("''", '""') else "_(majburiy/bo'sh)_"
        if len(d) > 60:
            d = d[:57] + "..."
        lines.append(f"| `{name}` | {d} |")
    lines.append("")
    lines.append("Sirli qiymatlar `.env` da saqlanadi (git'ga tushmaydi).")
    return _upsert("sozlamalar", lines)

def db_xaritasi() -> dict:
    """`app/db/schema.py` dan jadval va ustunlarni yig'adi (haqiqiy sxema)."""
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    try:
        from ..db.schema import TABLE_COLUMNS, TENANT_TABLES, TABLES
    except Exception as exc:  # noqa: BLE001 - sxema import qilinmasa qayd bo'sh qolmasin
        lines.append(f"_sxema o'qilmadi: {exc}_")
        return _upsert("db-jadvallari", lines)
    names = list(TABLES)
    lines.append(f"Jami **{len(names)}** jadval.")
    lines.append("")
    lines += [f"- Ko'p korxonali (`company_id`) jadvallar: "
              f"{len(TENANT_TABLES)}", ""]
    for name in names:
        cols = list(TABLE_COLUMNS.get(name) or [])
        tag = " · `company_id`" if name in TENANT_TABLES else ""
        lines.append(f"## `{name}` ({len(cols)} ustun){tag}")
        lines.append("")
        lines += [f"- `{c}` {t}" for c, t in cols] or ["_(ustun topilmadi)_"]
        lines.append("")
    return _upsert("db-jadvallari", lines)


# ── 3. runtime snapshot ───────────────────────────────────────────────


def _json_summary(p: Path) -> str:
    """Holat JSON faylidan qisqa izoh (o'lcham/son)."""
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(data, dict):
        for key in ("users", "groups", "items", "history", "records"):
            v = data.get(key)
            if isinstance(v, (list, dict)):
                return f"{key}: {len(v)}"
        return f"kalitlar: {len(data)}"
    if isinstance(data, list):
        return f"yozuvlar: {len(data)}"
    return ""


def runtime_snapshot() -> dict:
    """`state/` fayllari va vault statistikasidan joriy holat qaydi."""
    base = vault_dir()
    files = _walk(base)
    size = sum(f.stat().st_size for f in files)
    cats: dict[str, int] = {}
    for f in files:
        parts = f.relative_to(base).parts
        key = parts[0] if len(parts) > 1 else "(ildiz)"
        cats[key] = cats.get(key, 0) + 1
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    lines.append(f"- Vault: **{len(files)} qayd**, {size} bayt")
    for k in sorted(cats):
        lines.append(f"  - {k}: {cats[k]}")
    lines.append("")
    sdir = _state_dir()
    rows = []
    for p in sorted(sdir.glob("*.json"))[:60]:
        try:
            st = p.stat()
        except OSError:
            continue
        stamp = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        note = _json_summary(p)
        rows.append(f"| `{p.name}` | {st.st_size} | {stamp} | {note or '—'} |")
    lines.append(f"## Holat fayllari ({len(rows)})")
    lines.append("")
    if rows:
        lines += ["| FAYL | BAYT | O'ZGARGAN | ICHIDA |",
                  "| --- | --- | --- | --- |"] + rows
    else:
        lines.append("_state/ papkasi bo'sh_")
    lines.append("")
    lines.append(f"Kengaytirish vaqti: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return _upsert("holat-snapshoti", lines)


# ── 4. log tahlili ────────────────────────────────────────────────────

_LOG_LINE_RE = re.compile(
    r"(?:ERROR|CRITICAL|WARNING|WARN)\b[^\w]*(.*)", re.I)
_NUM_RE = re.compile(r"\b\d+\b")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.I)


def _signature(text: str) -> str:
    """Xato matnini barqaror imzoga keltiradi (sonlar/ID olib tashlanadi)."""
    s = _HEX_RE.sub("<id>", str(text or ""))
    s = _NUM_RE.sub("N", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:160]


def log_tahlili(log_path: Path | None = None, max_bytes: int = 400_000) -> dict:
    """Log faylidagi ERROR/WARNING larni guruhlab xato qaydini yangilaydi.

    `KNOWLEDGE_LOG_PATH` — boshqa log faylini ko'rsatish (testlar uchun ham).
    """
    env = (os.environ.get("KNOWLEDGE_LOG_PATH") or "").strip()
    if log_path:
        p = Path(log_path)
    elif env:
        p = Path(env)
    else:
        p = project_root() / "logs" / "bm.log"
    counts: dict[str, int] = {}
    sample: dict[str, str] = {}
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    if not p.is_file():
        lines.append(f"_log topilmadi: {p.name}_")
        return _upsert("log-tahlili", lines)
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            text = fh.read()
    except OSError as exc:
        lines.append(f"_log o'qilmadi: {exc}_")
        return _upsert("log-tahlili", lines)
    for raw in text.splitlines():
        if not _LOG_LINE_RE.search(raw):
            continue
        m = _LOG_LINE_RE.search(raw)
        sig = _signature(m.group(1) if m else raw)
        if not sig:
            continue
        counts[sig] = counts.get(sig, 0) + 1
        sample.setdefault(sig, raw.strip()[:240])
    lines.append(f"Manba: `{p}` (oxirgi {max_bytes // 1000} KB)")
    lines.append("")
    if not counts:
        lines.append("Oxirgi bo'lakda ERROR/WARNING topilmadi ✅")
        return _upsert("log-tahlili", lines)
    lines.append(f"Jami **{sum(counts.values())}** muammo satri, "
                 f"{len(counts)} xil imzo.")
    lines.append("")
    top = sorted(counts.items(), key=lambda x: -x[1])[:15]
    for sig, n in top:
        lines.append(f"### {n}× — `{sig[:110]}`")
        lines.append("")
        lines.append(f"> {sample.get(sig, '')}")
        lines.append("")
    lines.append("Tuzatish topilgach qoidani `Qoidalar/` yoki "
                 "`Xatolar/` qaydiga yozing — agentlar keyingi vazifada o'qiydi.")
    return _upsert("log-tahlili", lines)


# ── 5. bo'shliqlarni to'ldirish (stub qaydlar) ───────────────────────────


def _body_links() -> tuple[dict[str, dict], list[dict]]:
    """Qaydlarning **matnidagi** havolalari (avto-bog'lash blokisiz).

    Avto-bog'lash bloki hisoblanmaydi: u o'zi mavjud qaydlarga havola
    qiladi, shuning uchun uni hisobga olsak, o'chirilgan qayd uchun
    ochilgan bo'sh qayd qayta-qayta tirilardi.

    Qaytaradi: (qaydlar xaritasi, yechilmagan havolalar).
    """
    from .knowledge import _wikilinks
    from .vault_links import _BLOCK_RE

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
        parts = rel.split("/")
        targets = []
        for raw in _wikilinks(_BLOCK_RE.sub(" ", text)):
            target = raw.split("|")[0].split("#")[0].strip()
            if target:
                targets.append(target)
        notes[p.stem] = {"category": parts[0] if len(parts) > 1 else "",
                         "targets": targets}
        by_path[rel.lower()] = p.stem
        by_stem.setdefault(p.stem.lower(), []).append(p.stem)
    unresolved: list[dict] = []
    for stem, item in notes.items():
        for target in item["targets"]:
            key = target.strip().strip("/").lower()
            if by_path.get(key) or by_path.get(key + ".md"):
                continue
            hits = by_stem.get(key, [])
            if len(hits) == 1 and hits[0] != stem:
                continue
            unresolved.append({"from": stem, "target": target})
    return notes, unresolved


def boshliq_toldirish(limit: int = 5) -> dict:
    """Yechilmagan `[[havola]]` lar uchun bo'sh qayd ochadi (graf uzilmasin)."""
    try:
        notes, unresolved = _body_links()
    except Exception as exc:  # noqa: BLE001
        log.debug("Havolalar o'qilmadi: %s", exc)
        return {"created": [], "count": 0}
    if not unresolved:
        return {"created": [], "count": 0}
    created: list[str] = []
    seen: set[str] = set()
    for item in unresolved[:max(1, limit) * 4]:
        target = str(item.get("target") or "").strip()
        if not target or target in seen:
            continue
        seen.add(target)
        holder = notes.get(str(item.get("from") or "")) or {}
        cat = (str(target).split("/")[0] if "/" in str(target)
               else holder.get("category") or "")
        if cat not in CATEGORIES:
            cat = holder.get("category") or ""
        if cat not in CATEGORIES:
            cat = "Qoidalar"
        if cat not in CATEGORIES:
            cat = "Qoidalar"
        slug = slugify(target.split("/")[-1])
        rel = f"{cat}/{slug}.md"
        p = _resolve(rel)
        if p.exists():
            continue
        refs = sorted({u["from"] for u in unresolved
                       if u.get("target") == target})
        body = [
            f"<!-- avto-yaratilgan: boshliq-{slug} -->",
            f"# {slug}",
            "",
            AUTO_NOTE_NOTE,
            "",
            "Bu qayd boshqa qaydlardagi havola uchun avtomatik ochildi — "
            "mazmuni hali to'ldirilmagan.",
            "",
            "## Kim havola qilgan",
            "",
        ] + [f"- [[{r}]]" for r in refs[:10]] + [
            "",
            "## Nima yozish kerak",
            "",
            "- [ ] Muammo/qoidaning aniq tavsifi",
            "- [ ] Sabab (ildiz)",
            "- [ ] Tuzatish yoki tekshirish usuli",
            "",
        ]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(body), encoding="utf-8")
        created.append(rel)
        if len(created) >= max(1, limit):
            break
    return {"created": created, "count": len(created)}


# ── 6. agentlardan o'rganish (runtime darslar) ──────────────────────

ERRORS_STATE = "knowledge_errors.json"
TASKS_STATE = "knowledge_tasks.json"
MAX_ERROR_KEYS = 300
MAX_TASKS = 200


def learn_error(tool: str = "", action: str = "", error: str = "",
                agent: str = "", lesson: str = "") -> dict:
    """Muvaffaqiyatsiz amalni xotiraga yozadi (takrorlar sanaladi).

    Xato imzosi bo'yicha guruhlanadi — bir xil xato yuz marta takrorlansa
    ham jurnalda bitta yozuv bo'ladi, faqat `count` o'sadi.
    """
    sig = _signature(f"{tool} {action} {error}") or "noma'lum-xato"
    path = _state_file(ERRORS_STATE)
    data = _read_json(path, {})
    rec = data.get(sig) or {}
    rec["count"] = int(rec.get("count", 0)) + 1
    rec["tool"] = tool or rec.get("tool", "")
    rec["action"] = action or rec.get("action", "")
    rec["agent"] = agent or rec.get("agent", "")
    rec["example"] = str(error or rec.get("example", ""))[:400]
    if lesson:
        rec["lesson"] = str(lesson)[:400]
    rec.setdefault("first", _now())
    rec["last"] = _now()
    data[sig] = rec
    if len(data) > MAX_ERROR_KEYS:
        keep = sorted(data.items(), key=lambda x: x[1].get("last", ""),
                      reverse=True)[:MAX_ERROR_KEYS]
        data = dict(keep)
    _write_json(path, data)
    try:
        return _render_error_journal(data)
    except Exception as exc:  # noqa: BLE001 - jurnal xatosi darsni to'xtatmasin
        log.debug("Xato jurnali yangilanmadi: %s", exc)
        return {"path": "", "changed": False}


def _render_error_journal(data: dict | None = None) -> dict:
    """`Xatolar/Avto-xato-jurnali` qaydini holatdan qayta quradi."""
    data = data if data is not None else _read_json(
        _state_file(ERRORS_STATE), {})
    items = sorted(data.items(), key=lambda x: (-int(x[1].get("count", 0)),
                                               x[0]))
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    if not items:
        lines.append("_Hali xato qayd etilmagan ✅_")
        return _upsert("avto-xato-jurnali", lines)
    lines.append(f"Jami **{len(items)}** xil xato, "
                 f"{sum(int(v.get('count', 0)) for _, v in items)} takror.")
    lines.append("")
    for sig, rec in items:
        lines.append(f"## {rec.get('count', 1)}× — `{sig[:110]}`")
        lines.append("")
        if rec.get("tool"):
            lines.append(f"- Amal: `{rec.get('tool')}` "
                         f"{rec.get('action') or ''}")
        if rec.get("agent"):
            lines.append(f"- Agent: {rec.get('agent')}")
        lines.append(f"- Birinchi: {rec.get('first', '—')} · "
                     f"Oxirgi: {rec.get('last', '—')}")
        if rec.get("example"):
            lines.append("")
            lines.append(f"> {rec['example']}")
        if rec.get("lesson"):
            lines.append("")
            lines.append(f"**Dars:** {rec['lesson']}")
        lines.append("")
    return _upsert("avto-xato-jurnali", lines)


def learn_task(task_id: str = "", request: str = "", status: str = "",
               agents: list[str] | None = None,
               summary: str = "") -> dict:
    """Bajarilgan agent vazifasini hisobot qaydiga qo'shadi."""
    if not (task_id or request):
        return {"path": "", "changed": False}
    path = _state_file(TASKS_STATE)
    data = _read_json(path, [])
    data.insert(0, {
        "task_id": str(task_id),
        "request": str(request)[:300],
        "status": str(status),
        "agents": list(agents or [])[:12],
        "summary": str(summary)[:600],
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _write_json(path, data[:MAX_TASKS])
    return _render_task_journal(data[:MAX_TASKS])


def _render_task_journal(data: list | None = None) -> dict:
    """`Hisobotlar/Agent-vazifalari` qaydini holatdan qayta quradi."""
    data = data if data is not None else _read_json(_state_file(TASKS_STATE), [])
    lines: list[str] = [f"_yangilandi: {_now()}_", ""]
    if not data:
        lines.append("_Hali vazifa qayd etilmagan_")
        return _upsert("agent-vazifalari", lines)
    ok = sum(1 for r in data if str(r.get("status")) == "completed")
    lines += [f"Jami **{len(data)}** vazifa (muvaffaqiyatli: {ok}).", ""]
    for rec in data[:80]:
        lines.append(f"### {rec.get('at', '')} · `{rec.get('status') or '—'}`")
        lines.append("")
        lines.append(f"- Vazifa: {rec.get('request') or '—'}")
        if rec.get("task_id"):
            lines.append(f"- ID: `{rec['task_id']}`")
        if rec.get("agents"):
            lines.append(f"- Agentlar: {', '.join(rec['agents'])}")
        if rec.get("summary"):
            lines.append("")
            lines.append(f"> {rec['summary']}")
        lines.append("")
    return _upsert("agent-vazifalari", lines)


# ── 7. grafik holati ─────────────────────────────────────────────────────


def grafik_holati() -> dict:
    """`Hisobotlar/Grafik-holati` — grafik salomatligi va markazlari.

    O'zini hisobdan chiqaradi: qayd yozilganda raqamlar o'zgarib, har
    kengaytirishda qayta yozilaverardi (idempotentlik buzilardi).
    """
    from .knowledge import graph_status

    st = graph_status(exclude={"Grafik-holati"}, include_autolinks=False)
    auto_only = set(st["auto_only_list"])
    real_isolated = [s for s in st["isolated_list"] if s not in auto_only]
    real_count = st["isolated"] - st["auto_only"]
    ok = st["unresolved"] == 0 and real_count == 0
    holat = "sog'lom ✅" if ok else "e'tibor talab qiladi ⚠️"
    lines: list[str] = [
        f"**Holat:** {holat}",
        "",
        f"- Qaydlar: **{st['notes']}** · matn havolalari: **{st['edges']}**",
        f"- Uzilgan havolalar: **{st['unresolved']}**",
        f"- Yetim qaydlar (matnda havolasiz): **{st['isolated']}**"
        + (f" — shundan {st['auto_only']} tasi faqat avto-blok bilan ulangan"
           if st["auto_only"] else ""),
        f"- O'rtacha daraja: {st['avg_degree']} · zichlik: {st['density']}",
        "",
        "## Eng bog'langan qaydlar",
        "",
    ]
    for hub in st["hubs"]:
        lines.append(
            f"- [[{hub['id']}]] — {hub['degree']} havola "
            f"({hub['links']} → / {hub['backlinks']} ←), {hub['category']}")
    if st["unresolved_list"]:
        lines += ["", "## Uzilgan havolalar", ""]
        lines += [f"- [[{u['from']}]] → `{u['target']}`"
                  for u in st["unresolved_list"]]
    if real_isolated:
        lines += ["", "## Yetim qaydlar", ""]
        lines += [f"- [[{stem}]]" for stem in real_isolated]
    if auto_only:
        lines += ["", "## Faqat avto-havola bilan ulangan", ""]
        lines += [f"- [[{stem}]]" for stem in sorted(auto_only)]
    res = _upsert("grafik-holati", lines)
    if res.get("changed"):
        # Qayd oxirgi yozilgani uchun uning "Bog'liq qaydlar" bloki hali yo'q.
        try:
            from .vault_links import autolink_written
            autolink_written(_resolve(f"{MANAGED['grafik-holati'][0]}.md"))
        except Exception as exc:  # noqa: BLE001 - hisobot yozilgani muhim
            log.debug("Grafik-holati bog'lanishi yangilanmadi: %s", exc)
    return res


# ── 8. to'liq kengaytirish ───────────────────────────────────────────

GROW_STATE = "knowledge_grow.json"


def grow(force: bool = False, gaps: bool = True) -> dict:
    """Vault'ni to'liq kengaytiradi (idempotent — o'zgarish bo'lmasa tegmaydi)."""
    started = _now()
    steps: dict[str, dict] = {}
    errors: list[str] = []
    for name, fn in (("kod", kod_xaritasi), ("api", api_xaritasi),
                     ("bot", bot_buyruqlari), ("testlar", testlar_xaritasi),
                     ("sozlamalar", sozlamalar_xaritasi),
                     ("db", db_xaritasi), ("holat", runtime_snapshot),
                     ("log", log_tahlili),
                     ("xato-jurnali", _render_error_journal),
                     ("vazifalar", _render_task_journal)):
        try:
            steps[name] = fn()
        except Exception as exc:  # noqa: BLE001 - bitta qadam qolganini to'xtatmasin
            errors.append(f"{name}: {exc}")
            log.warning("Kengaytirish qadami xatosi (%s): %s", name, exc)
    changed = sorted(k for k, v in steps.items()
                     if isinstance(v, dict) and v.get("changed"))
    if changed or gaps:
        # Avval mavjud avto-bloklar yangilanadi (o'chirilgan qaydlarga
        # qolgan havolalar yo'qoladi), keyin bo'shliqlar to'ldiriladi.
        try:
            autolink_all()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"autolink: {exc}")
            log.warning("Avto-bog'lash xatosi: %s", exc)
    if gaps:
        try:
            steps["boshliq"] = boshliq_toldirish()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"boshliq: {exc}")
            log.warning("Bo'shliq to'ldirish xatosi: %s", exc)
        if isinstance(steps.get("boshliq"), dict) and steps["boshliq"].get("created"):
            changed.append("boshliq")
            try:
                autolink_all()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"autolink: {exc}")
    # Grafik holati oxirida: avto-bloklar va bo'shliqlar to'ldirilgandan keyin
    # hisoblanadi — aks holda raqamlar o'sha yurishning yakuniy holatiga mos
    # kelmaydi (masalan, hali yaratilmagan qaydga havola "uzilgan" bo'lib
    # ko'rinadi).
    try:
        steps["grafik"] = grafik_holati()
        if steps["grafik"].get("changed") and "grafik" not in changed:
            changed.append("grafik")
            changed.sort()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"grafik: {exc}")
        log.warning("Kengaytirish qadami xatosi (grafik): %s", exc)
    stats = _vault_counts()
    state = _read_json(_state_file(GROW_STATE), {})
    state.update({
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runs": int(state.get("runs", 0)) + 1,
        "changed": changed,
        "notes": stats["count"],
        "errors": errors[-5:],
    })
    hist = state.get("history") or []
    if not isinstance(hist, list):
        hist = []
    state["history"] = ([{"at": state["last_run"], "notes": stats["count"],
                         "changed": len(changed)}] + hist)[:30]
    _write_json(_state_file(GROW_STATE), state)
    log.info("Bilim bazasi kengaytirildi: %d qayd (%s yangilandi)",
             stats["count"], len(changed))
    return {"ok": True, "started": started, "notes": stats["count"],
            "changed": changed, "steps": {k: bool(v.get("changed"))
                                          for k, v in steps.items()
                                          if isinstance(v, dict)},
            "errors": errors}


def _vault_counts() -> dict:
    files = _walk()
    return {"count": len(files),
            "bytes": sum(f.stat().st_size for f in files)}


def check_and_grow() -> dict:
    """Interval o'tgan bo'lsa kengaytiradi (bot poll-tsikli uchun)."""
    if not enabled():
        return {"ok": True, "skipped": "KNOWLEDGE_AUTO=off"}
    state = _read_json(_state_file(GROW_STATE), {})
    last = str(state.get("last_run") or "")
    if last:
        try:
            elapsed = (datetime.now()
                       - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            elapsed = interval_sec() + 1
        if elapsed < interval_sec():
            return {"ok": True, "skipped": "interval",
                    "next_in_sec": int(interval_sec() - elapsed)}
    return grow()


def status() -> dict:
    """Panel/bot uchun kengaytirish holati."""
    state = _read_json(_state_file(GROW_STATE), {})
    stats = _vault_counts()
    next_in = 0
    last = str(state.get("last_run") or "")
    if last:
        try:
            elapsed = (datetime.now()
                       - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
            next_in = max(0, int(interval_sec() - elapsed))
        except ValueError:
            next_in = 0
    return {"ok": True, "enabled": enabled(),
            "interval_min": interval_sec() // 60,
            "last_run": last, "runs": int(state.get("runs", 0)),
            "changed": state.get("changed") or [],
            "errors": state.get("errors") or [],
            "notes": stats["count"], "bytes": stats["bytes"],
            "next_in_sec": next_in,
            "history": state.get("history") or [],
            "managed": [rel for rel, _ in MANAGED.values()]}
