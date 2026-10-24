"""Tenant (kompaniya) konteksti — CompanyResolver.

So'rov/sessiya kontekstida kompaniya holati va yo'nalishlarini aniqlaydi:

- ``company_state(name)`` — `companies` jadvalidagi qator (status, obuna).
- ``company_active(row)`` — kompaniya ishlayaptimi (status + obuna muddati).
  Noma'lum kompaniya (jadvalda yo'q yoki DB o'chiq) — faol hisoblanadi
  (fail-open: soft nazorat, izolyatsiya esa route qamrovi bilan ta'minlanadi).
- ``company_routes(name)`` — kompaniyaning BARCHA yo'nalishlari
  (profiles.json; topilmasa DB `profiles` jadvalidan).
- ``company_id_for_name/route/driver`` — company_id bog'lash uchun.
- ``resolve_company_id(table, values)`` — `Storage` avtomatik to'ldirishi
  uchun yagona qoida (qaysi ustundan qaysi kompaniya).

DB xaritalari (companies/profiles/drivers) TTL bilan keshlanadi; Storage
nusxasi almashsa (testlar, qayta sozlash) kesh avtomatik yangilanadi.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime

_CACHE_TTL_S = 30.0

# companies.status — shu qiymatlar kompaniyani to'xtatilgan hisoblaydi.
# "deleted" ham shu yerda: soft-delete qilingan korxona foydalanuvchilari
# tizimga kira olmaydi (avval bu holat "faol" hisoblanib qolgan edi).
_INACTIVE_STATUSES = {"blocked", "suspended", "inactive", "disabled",
                      "cancelled", "canceled", "expired", "deleted",
                      "archived"}

_LOCK = threading.RLock()
_CACHE: dict = {
    "storage": None,
    "at": 0.0,
    "by_name": {},        # name(lower) -> companies qatori
    "by_id": {},          # company_id -> companies qatori
    "route_to_cid": {},   # routeVariantId -> company_id (profiles jadvali)
    "driver_to_cid": {},  # driver external_id -> company_id (drivers jadvali)
}

# Storage avtomatik company_id to'ldirishi: jadval -> (ustun, tur).
_FILL_MAP: dict[str, tuple[str, str]] = {
    "profiles": ("name", "name"),
    "staff": ("company", "name"),
    "dashboard_users": ("company", "name"),
    "routes": ("external_id", "route"),
    "vehicles": ("route_id", "route"),
    "drivers": ("route_id", "route"),
    "duties": ("route_id", "route"),
    "schedules": ("route_id", "route"),
    "waybills": ("route_id", "route"),
    "route_daily": ("route_id", "route"),
    "trips": ("route_id", "route"),
    "dispatcher_routes": ("route_id", "route"),
    "sms_route_flags": ("route_id", "route"),
    "sms_log": ("route_id", "route"),
    "driver_profiles": ("driver_id", "driver"),
    "driver_work_logs": ("driver_id", "driver"),
    "driver_fines": ("driver_id", "driver"),
    "driver_appeals": ("driver_id", "driver"),
    "avans": ("driver_id", "driver"),
    "documents": ("driver_id", "driver"),
}


def reset() -> None:
    """Keshni tozalash (testlar / kompaniya sozlamalari o'zgargach)."""
    with _LOCK:
        _CACHE.update(storage=None, at=0.0, by_name={}, by_id={},
                      route_to_cid={}, driver_to_cid={})


def _refresh(storage=None) -> None:
    """DB xaritalarini (companies/profiles/drivers) qayta yuklaydi (TTL bilan).

    `storage` berilsa — aynan o'sha nusxa ishlatiladi (Storage ichidagi
    avtomatik to'ldirish global DB'ga bog'lanib qolmasligi uchun); aks holda
    global `get_storage()`.

    Xato bo'lsa eski kesh saqlanadi — soft nazorat app ishlashini to'xtatmaydi.
    """
    from ..db import storage as storage_mod

    st = storage if storage is not None else storage_mod.get_storage()
    now = time.monotonic()
    with _LOCK:
        if _CACHE["storage"] is st and (now - _CACHE["at"]) < _CACHE_TTL_S:
            return
        by_name: dict[str, dict] = {}
        by_id: dict[int, dict] = {}
        route_to_cid: dict[str, int] = {}
        driver_to_cid: dict[str, int] = {}
        try:
            if st.enabled:
                for r in st.query(
                        "SELECT id, name, slug, status, subscription_expire_date "
                        "FROM companies", limit=10000):
                    cid = int(r.get("id") or 0)
                    if not cid:
                        continue
                    row = {"id": cid, "name": str(r.get("name") or ""),
                           "slug": str(r.get("slug") or ""),
                           "status": str(r.get("status") or ""),
                           "subscription_expire_date": str(
                               r.get("subscription_expire_date") or "")}
                    by_id[cid] = row
                    nm = row["name"].strip().lower()
                    if nm:
                        by_name[nm] = row
                for r in st.query(
                        "SELECT route_id, company_id FROM profiles "
                        "WHERE route_id != ''", limit=100000):
                    rid = str(r.get("route_id") or "").strip()
                    cid = int(r.get("company_id") or 0)
                    if rid and cid:
                        route_to_cid.setdefault(rid, cid)
                for r in st.query(
                        "SELECT external_id, route_id, company_id FROM drivers",
                        limit=200000):
                    did = str(r.get("external_id") or "").strip()
                    if not did:
                        continue
                    cid = int(r.get("company_id") or 0)
                    if not cid:
                        cid = route_to_cid.get(
                            str(r.get("route_id") or "").strip(), 0)
                    if cid:
                        driver_to_cid[did] = cid
        except Exception:  # noqa: BLE001 - resolver hech qachon to'xtatmasin
            return
        _CACHE.update(storage=st, at=now, by_name=by_name, by_id=by_id,
                      route_to_cid=route_to_cid, driver_to_cid=driver_to_cid)


def company_state(company_name: str, storage=None) -> dict | None:
    """Kompaniya qatori (status, obuna). Topilmasa None."""
    name = str(company_name or "").strip().lower()
    if not name:
        return None
    _refresh(storage)
    with _LOCK:
        return _CACHE["by_name"].get(name)


def company_active(row: dict | None) -> bool:
    """Kompaniya ishlayaptimi: status bloklanmagan va obuna muddati o'tmagan.

    `row=None` (noma'lum) → True (fail-open).
    """
    if not row:
        return True
    status = str(row.get("status") or "").strip().lower()
    if status in _INACTIVE_STATUSES:
        return False
    raw = str(row.get("subscription_expire_date") or "").strip()
    if raw:
        try:
            expires = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return True
        if expires < date.today():
            return False
    return True


def company_routes(company_name: str, storage=None) -> list[str]:
    """Kompaniyaning barcha yo'nalishlari (routeVariantId ro'yxati).

    Manba: profiles.json (barcha shu nomdagi yozuvlar); u bo'sh bo'lsa
    DB `profiles` jadvalidagi route_id lar (company_id bo'yicha).
    """
    name = str(company_name or "").strip().lower()
    if not name:
        return []
    out: list[str] = []
    try:
        from .profiles import all_profiles
        for p in all_profiles():
            if str(p.get("name") or "").strip().lower() != name:
                continue
            rid = str(p.get("routeVariantId") or "").strip()
            if rid and rid not in out:
                out.append(rid)
    except Exception:  # noqa: BLE001
        pass
    if out:
        return out
    # DB fallback: profiles jadvali (company_id bo'yicha route_id lar)
    state = company_state(company_name, storage=storage)
    if state:
        _refresh(storage)
        with _LOCK:
            for rid, cid in _CACHE["route_to_cid"].items():
                if cid == state["id"] and rid not in out:
                    out.append(rid)
    if out or not state:
        return out
    # Oxirgi fallback: `routes` jadvali — DB `profiles` qatori bo'lmagan
    # tenantlar uchun (masalan DEMO korxona; profiles.name unikal).
    try:
        from ..db import storage as storage_mod

        st = storage if storage is not None else storage_mod.get_storage()
        if st.enabled:
            ph = st.db.ph
            for r in st.query(
                    "SELECT external_id FROM routes WHERE company_id = "
                    f"{ph} AND entity_type = {ph}",
                    (int(state["id"]), "ROUTE"), limit=1000):
                rid = str(r.get("external_id") or "").strip()
                if rid and rid not in out:
                    out.append(rid)
    except Exception:  # noqa: BLE001 - DB yo'q bo'lsa profiles.json natijasi qoladi
        pass
    return out


# ---------------------------------------------------------------------------
# company_id topish
# ---------------------------------------------------------------------------

def company_id_for_name(company_name: str, storage=None) -> int:
    """Kompaniya nomi -> id (companies jadvali). Topilmasa 0."""
    row = company_state(company_name, storage=storage)
    return int(row["id"]) if row else 0


def _resolve_storage(storage):
    if storage is not None:
        return storage
    from ..db import storage as storage_mod
    return storage_mod.get_storage()


def _db_route_to_cid(rid: str, storage) -> int:
    """DB `profiles` jadvalidan route -> company_id (keshda yo'q bo'lsa).

    Kesh TTL ichida qo'shilgan yangi yozuvlar uchun to'g'ridan-to'g'ri
    so'rov — natija keshga memoizatsiya qilinadi.
    """
    st = _resolve_storage(storage)
    try:
        if not st.enabled:
            return 0
        ph = st.db.ph
        rows = st.query(
            f"SELECT company_id FROM profiles WHERE route_id = {ph} "
            f"AND company_id != 0", (rid,), limit=1)
        cid = int((rows[0] if rows else {}).get("company_id") or 0)
        if not cid:
            rows = st.query(
                f"SELECT name FROM profiles WHERE route_id = {ph}",
                (rid,), limit=1)
            if rows:
                cid = company_id_for_name(str(rows[0].get("name") or ""),
                                          storage=storage)
        if cid:
            with _LOCK:
                _CACHE["route_to_cid"][rid] = int(cid)
        return int(cid)
    except Exception:  # noqa: BLE001
        return 0


def company_id_for_route(route_id: str, storage=None) -> int:
    """routeVariantId -> company_id (kesh -> DB -> profiles.json)."""
    rid = str(route_id or "").strip()
    if not rid:
        return 0
    _refresh(storage)
    with _LOCK:
        cid = _CACHE["route_to_cid"].get(rid, 0)
    if cid:
        return int(cid)
    cid = _db_route_to_cid(rid, storage)
    if cid:
        return cid
    try:
        from .profiles import all_profiles
        for p in all_profiles():
            if str(p.get("routeVariantId") or "").strip() != rid:
                continue
            return company_id_for_name(str(p.get("name") or ""), storage=storage)
    except Exception:  # noqa: BLE001
        pass
    return 0


def company_id_for_driver(driver_id: str, storage=None) -> int:
    """Haydovchi external_id -> company_id (kesh -> DB -> route orqali)."""
    did = str(driver_id or "").strip()
    if not did:
        return 0
    _refresh(storage)
    with _LOCK:
        cid = int(_CACHE["driver_to_cid"].get(did, 0))
    if cid:
        return cid
    # Keshda yo'q (masalan, hozir qo'shilgan yozuv) — DB'dan to'g'ridan-to'g'ri.
    st = _resolve_storage(storage)
    try:
        if not st.enabled:
            return 0
        rows = st.query(
            f"SELECT company_id, route_id FROM drivers "
            f"WHERE external_id = {st.db.ph}", (did,), limit=1)
        if not rows:
            return 0
        r = rows[0]
        cid = int(r.get("company_id") or 0)
        if not cid:
            rid = str(r.get("route_id") or "").strip()
            if rid:
                cid = company_id_for_route(rid, storage=storage)
        if cid:
            with _LOCK:
                _CACHE["driver_to_cid"][did] = int(cid)
        return int(cid)
    except Exception:  # noqa: BLE001
        return 0


def resolve_company_id(table: str, values: dict, storage=None) -> int:
    """Jadval + yozuv maydonlaridan company_id ni aniqlaydi (0 = noma'lum)."""
    spec = _FILL_MAP.get(str(table or ""))
    if not spec:
        return 0
    col, kind = spec
    raw = str((values or {}).get(col) or "").strip()
    if not raw:
        return 0
    try:
        if kind == "route":
            return company_id_for_route(raw, storage=storage)
        if kind == "driver":
            return company_id_for_driver(raw, storage=storage)
        return company_id_for_name(raw, storage=storage)
    except Exception:  # noqa: BLE001
        return 0
