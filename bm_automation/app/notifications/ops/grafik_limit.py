"""Grafik yuborishning kunlik limiti — har bir korxona uchun 20 marta.

Hisob `state/grafik_limit.json` da saqlanadi: sana + korxona nomi bo'yicha
nechta so'rov qabul qilingani. Kun almashganda avtomatik nolga tushadi.

Bir so'rov kompaniyaning bir nechta yo'nalishini yuborsa ham hisob 1 marta
oshadi (foydalanuvchi nuqtai nazaridan bu bitta "grafik yuborish").
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from ...core.profiles import all_profiles
from ...utils.io import atomic_write

DAILY_LIMIT = 20
STATE_FILE = Path("state") / "grafik_limit.json"

_LOCK = threading.Lock()


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - fayl yo'q/buzuk bo'lsa yangi hisob
        return {}


def _save(day: str, counts: dict) -> None:
    try:
        atomic_write(STATE_FILE, json.dumps(
            {"date": day, "counts": counts}, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - limit saqlanmasa yuborish to'xtamaydi
        print(f"Grafik limiti saqlanmadi: {exc}")


def _route_company_map() -> dict[str, str]:
    """routeVariantId -> kompaniya nomi (profiles.json bo'yicha)."""
    out: dict[str, str] = {}
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        raw = str(p.get("routeVariantId") or "").strip()
        for rid in raw.replace(",", " ").split():
            if rid and rid not in out:
                out[rid] = name or rid
    return out


def _all_route_ids() -> list[str]:
    try:
        import daily_grafik
        return [str(r[1]).strip() for r in daily_grafik.get_routes()
                if str(r[1]).strip()]
    except Exception:  # noqa: BLE001
        return []


def reserve(route_ids: list[str] | None) -> dict:
    """So'rovni hisobga oladi va ruxsat etilgan yo'nalishlarni qaytaradi.

    ``route_ids=None`` — barcha yo'nalishlar (ADMIN).
    Qaytadi::

        {"allowed": [route_id, ...],
         "blocked": [{"company": str, "count": int, "limit": int}, ...],
         "remaining": {company: qolgan_soni}}
    """
    if route_ids:
        rids = [str(r).strip() for r in route_ids if str(r).strip()]
    else:
        rids = _all_route_ids()

    companies = _route_company_map()
    grouped: dict[str, list[str]] = {}
    for rid in rids:
        grouped.setdefault(companies.get(rid, rid), []).append(rid)

    with _LOCK:
        data = _load()
        day = _today()
        counts = (dict(data.get("counts") or {})
                  if str(data.get("date") or "") == day else {})
        allowed: list[str] = []
        blocked: list[dict] = []
        remaining: dict[str, int] = {}
        for company, company_routes in grouped.items():
            try:
                used = int(counts.get(company) or 0)
            except (TypeError, ValueError):
                used = 0
            if used >= DAILY_LIMIT:
                blocked.append({"company": company, "count": used,
                                "limit": DAILY_LIMIT})
                continue
            counts[company] = used + 1
            allowed.extend(company_routes)
            remaining[company] = DAILY_LIMIT - used - 1
        _save(day, counts)

    return {"allowed": allowed, "blocked": blocked, "remaining": remaining}
