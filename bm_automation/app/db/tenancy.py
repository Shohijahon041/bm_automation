"""Tenant (ko'p-kompaniyali) bootstrap: kompaniyalar reyestri + backfill.

Phase 0 (SaaS ko'p-tenant poydevori):

1. ``seed_companies`` — `profiles.json`dagi kompaniyalarni `companies`
   jadvaliga yozadi (slug bo'yicha idempotent). Bitta kompaniyaning bir nechta
   yo'nalishi bo'lsa (masalan AND ECO: 4 ta profil) — BITTA kompaniya.
2. ``backfill_company_ids`` — mavjud biznes yozuvlarga `company_id` ni
   avtomatik bog'laydi:
       - yo'nalish jadvallari (trips, waybills, ...) — `route_id` orqali;
       - haydovchiga bog'liq jadvallar (driver_fines, avans, ...) —
         `drivers.route_id` → kompaniya;
       - nom bo'yicha (profiles.name, staff.company, dashboard_users.company).
   Faqat `company_id = 0` yozuvlarga tegadi — idempotent va xavfsiz.

``bootstrap_tenancy`` — ikkovini birga bajaradi; `Storage.enabled` ichidan
(postgres) avtomatik chaqiriladi. Har qanday xato yutiladi — bootstrap
avtomatlashtirishning ishlashiga to'sqinlik qilmasligi shart.
"""

from __future__ import annotations

import hashlib
import re

from ..utils.logger import get_logger

log = get_logger("bm_automation.db.tenancy")

# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
}


def slugify(name: str) -> str:
    """Kompaniya nomidan barqaror slug (URL/tenant kaliti).

    ``"AND ECO TRANSPORT MCHJ"`` → ``"and-eco-transport-mchj"``.
    Kirill a→a ... ў→o transliteratsiya qilinadi; bo'sh natijada
    nom xeshidan ``company-xxxxxxxx`` qaytariladi.
    """
    s = str(name or "").strip().lower()
    for ch in ("'", "\u2019", "\u2018", "`", "\u02bb"):
        s = s.replace(ch, "")
    s = "".join(_TRANSLIT.get(ch, ch) for ch in s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        digest = hashlib.sha1(str(name or "").encode("utf-8")).hexdigest()[:8]
        s = f"company-{digest}"
    return s


# ---------------------------------------------------------------------------
# Kompaniya kalitlari
# ---------------------------------------------------------------------------

# Yo'nalish ustuni bo'yicha bog'lanadigan jadvallar: (jadval, ustun).
_ROUTE_TABLES: tuple[tuple[str, str], ...] = (
    ("routes", "external_id"),
    ("vehicles", "route_id"),
    ("drivers", "route_id"),
    ("duties", "route_id"),
    ("schedules", "route_id"),
    ("waybills", "route_id"),
    ("route_daily", "route_id"),
    ("trips", "route_id"),
    ("dispatcher_routes", "route_id"),
    ("sms_route_flags", "route_id"),
    ("sms_log", "route_id"),
)

# driver_id orqali (drivers.route_id → kompaniya) bog'lanadigan jadvallar.
_DRIVER_TABLES: tuple[str, ...] = (
    "driver_profiles",
    "driver_work_logs",
    "driver_fines",
    "driver_appeals",
    "avans",
    "documents",
)

# Nom bo'yicha bog'lanadigan jadvallar: (jadval, ustun).
_NAME_TABLES: tuple[tuple[str, str], ...] = (
    ("profiles", "name"),
    ("staff", "company"),
    ("dashboard_users", "company"),
)


def _company_rows(profiles: list[dict]) -> list[tuple[str, str]]:
    """Profillar → takrorlanmagan (nomi, slug) kompaniyalar ro'yxati.

    Bitta kompaniyaning bir nechta yo'nalishi bitta qatorga birlashadi.
    """
    out: dict[str, str] = {}  # slug -> name (birinchi uchragan nom)
    for p in profiles or []:
        name = str((p or {}).get("name") or "").strip()
        if not name:
            continue
        slug = slugify(name)
        out.setdefault(slug, name)
    return [(name, slug) for slug, name in out.items()]


def _route_map(profiles: list[dict],
               company_ids: dict[str, int]) -> dict[int, list[str]]:
    """company_id -> [routeVariantId, ...] (profillar bo'yicha, takrorsiz)."""
    out: dict[int, list[str]] = {}
    seen: dict[int, set[str]] = {}
    for p in profiles or []:
        name = str((p or {}).get("name") or "").strip()
        if not name:
            continue
        cid = company_ids.get(slugify(name))
        if not cid:
            continue
        rid = str((p or {}).get("routeVariantId")
                  or (p or {}).get("routeId") or "").strip()
        if not rid:
            continue
        bucket = seen.setdefault(int(cid), set())
        if rid in bucket:
            continue
        bucket.add(rid)
        out.setdefault(int(cid), []).append(rid)
    return out


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed_companies(storage, profiles: list[dict] | None = None) -> dict[str, int]:
    """`companies` jadvalini profillar asosida to'ldiradi (find-then-insert).

    `upsert` ATAYLAB ishlatilmaydi: ON CONFLICT DO UPDATE `subscription_plan`,
    `status`, `logo` kabi qo'lda tahrirlanadigan maydonlarni har ishga
    tushishda boshlang'ich qiymatga qaytarib qo'yardi.

    Qaytadi: {slug: company_id}.
    """
    if profiles is None:
        from ..core.profiles import load_profiles
        profiles = load_profiles().get("profiles") or []
    ids: dict[str, int] = {}
    for name, slug in _company_rows(profiles):
        row = storage.find("companies", slug=slug)
        if not row:
            storage.insert("companies", {"name": name, "slug": slug})
            row = storage.find("companies", slug=slug)
        if row:
            ids[slug] = int(row["id"])
    return ids


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def backfill_company_ids(storage, profiles: list[dict] | None = None,
                         company_ids: dict[str, int] | None = None) -> dict[str, int]:
    """Mavjud yozuvlarga `company_id` ni bog'laydi (faqat company_id=0).

    Qaytadi: {jadval: yangilangan qatorlar soni} + {"companies": N}.
    """
    if profiles is None:
        from ..core.profiles import load_profiles
        profiles = load_profiles().get("profiles") or []
    if company_ids is None:
        company_ids = seed_companies(storage, profiles)

    ph = storage.db.ph
    counts: dict[str, int] = {}
    routes_by_company = _route_map(profiles, company_ids)

    # 1) Yo'nalish jadvallari: route_id IN (kompaniya yo'nalishlari).
    for table, col in _ROUTE_TABLES:
        updated = 0
        for cid, rids in routes_by_company.items():
            marks = ", ".join([ph] * len(rids))
            updated += storage._exec(
                f"UPDATE {table} SET company_id = {ph} "
                f"WHERE company_id = 0 AND {col} IN ({marks})",
                (cid, *rids))
        if updated:
            counts[table] = updated

    # 2) Haydovchiga bog'liq jadvallar: driver_id IN (shu kompaniya
    #    haydovchilari). Haydovchilar `drivers.route_id` orqali aniqlanadi —
    #    bu `drivers.company_id` hozir to'lgan-to'lmaganiga bog'liq emas.
    for table in _DRIVER_TABLES:
        updated = 0
        for cid, rids in routes_by_company.items():
            marks = ", ".join([ph] * len(rids))
            updated += storage._exec(
                f"UPDATE {table} SET company_id = {ph} "
                f"WHERE company_id = 0 AND driver_id IN "
                f"(SELECT external_id FROM drivers "
                f"WHERE route_id IN ({marks}))",
                (cid, *rids))
        if updated:
            counts[table] = updated

    # 3) Nom bo'yicha (profiles.name, staff.company, dashboard_users.company).
    #    dashboard_users.company='' (ADMIN) — tizim darajasi, tegilmaydi.
    name_map: dict[str, int] = {}
    for name, slug in _company_rows(profiles):
        cid = company_ids.get(slug)
        if cid:
            name_map[name.strip().lower()] = int(cid)
    for table, col in _NAME_TABLES:
        updated = 0
        for name_lower, cid in name_map.items():
            updated += storage._exec(
                f"UPDATE {table} SET company_id = {ph} "
                f"WHERE company_id = 0 AND lower({col}) = {ph}",
                (cid, name_lower))
        if updated:
            counts[table] = updated

    counts["companies"] = len(company_ids)
    return counts


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_tenancy(storage, profiles: list[dict] | None = None) -> dict[str, int]:
    """Kompaniyalar seed + company_id backfill. Hech qachon xato tashlamaydi."""
    try:
        if not storage.enabled:
            return {}
        if profiles is None:
            from ..core.profiles import load_profiles
            profiles = load_profiles().get("profiles") or []
        counts = backfill_company_ids(storage, profiles)
        log.info("Tenant bootstrap: %d kompaniya, yangilangan: %s",
                 counts.get("companies", 0),
                 {k: v for k, v in counts.items() if k != "companies"} or "—")
        return counts
    except Exception as exc:  # noqa: BLE001 - bootstrap app ishlashini to'xtatmasin
        log.warning("Tenant bootstrap bajarilmadi: %s", exc)
        return {}
