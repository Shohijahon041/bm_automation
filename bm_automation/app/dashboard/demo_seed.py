"""DEMO korxona va namuna ma'lumotlar (real ma'lumotlardan ajratilgan).

`python -m bm_automation demo-seed` buyrug'i orqali ishlaydi. Alohida
`DEMO KORXONA` yaratadi: 2 yo'nalish, 6 avtobus, 8 haydovchi, kunlik
reyslar/grafiklar/AVTO km qaydlari, bir nechta jarima/murojaat/avans va
`MANAGER` rolli panel foydalanuvchisi.

Idempotent: kompaniya/foydalanuvchi mavjud bo'lsa yangilanadi; demo
oynasidagi (start..end) qatorlar har safar qayta yoziladi. Boshqa
korxonalarning ma'lumotlariga tegmaydi — barcha yozuvlar `DEMO-*`
yo'nalishlari va `company_id` bilan belgilangan.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timedelta

DEMO_COMPANY = "DEMO KORXONA"
DEMO_SLUG = "demo-korxona"
DEMO_USERNAME = "demo"
DEMO_ROLE = "MANAGER"
DEMO_ROUTE_PREFIX = "DEMO-"
DEMO_KM_RATE = 450.0

# Panelda ochiladigan bo'limlar (moliyaviy bo'limlar MANAGER uchun ochiq;
# GPS/Video/SMS/Admin/Sozlamalar demo akkauntga berilmaydi).
DEMO_PAGES: tuple[str, ...] = (
    "overview", "dispatch", "routes", "vehicles", "drivers", "appeals",
    "alerts", "performance", "top-performance", "electricity", "rejects",
    "brutto", "tariffs",
)

_TIMES = ("06:30", "10:00", "14:00", "17:30")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

# (id, nomi, km)
_DEMO_ROUTES: tuple[tuple[str, str, float], ...] = (
    ("DEMO-R01", "DEMO 1-yo'nalish (markaz)", 32.0),
    ("DEMO-R02", "DEMO 2-yo'nalish (vokzal)", 27.0),
)

# (external_id, davlat raqami, model, yo'nalish indeksi)
_DEMO_VEHICLES: tuple[tuple[str, str, str, int], ...] = (
    ("demo-veh-01", "01 DEMO 001", "Isuzu SAZ LE 60", 0),
    ("demo-veh-02", "01 DEMO 002", "Isuzu SAZ LE 60", 0),
    ("demo-veh-03", "01 DEMO 003", "Yutong ZK6850", 0),
    ("demo-veh-04", "01 DEMO 004", "Isuzu SAZ LE 60", 1),
    ("demo-veh-05", "01 DEMO 005", "Yutong ZK6850", 1),
    ("demo-veh-06", "01 DEMO 006", "Isuzu SAZ LE 60", 1),
)

# (external_id, F.I.Sh., yo'nalish indeksi)
_DEMO_DRIVERS: tuple[tuple[str, str, int], ...] = (
    ("demo-drv-01", "Aziz Karimov", 0),
    ("demo-drv-02", "Bekzod Tursunov", 0),
    ("demo-drv-03", "Dilshod Rahimov", 0),
    ("demo-drv-04", "Jasur Yuldashev", 0),
    ("demo-drv-05", "Nodir Sattorov", 1),
    ("demo-drv-06", "Otabek Mirzayev", 1),
    ("demo-drv-07", "Rustam Qodirov", 1),
    ("demo-drv-08", "Sardor Abdullayev", 1),
)


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _shift_time(base: str, minutes: int) -> str:
    hour, minute = (int(x) for x in base.split(":"))
    total = (hour * 60 + minute + int(minutes or 0)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _real_last_trip_date(storage) -> str:
    """Demo bo'lmagan eng oxirgi reys sanasi (global default sana uchun)."""
    ph = storage.db.ph
    rows = storage.query(
        f"SELECT MAX(date) AS d FROM trips WHERE route_id NOT LIKE {ph}",
        (f"{DEMO_ROUTE_PREFIX}%",), limit=1)
    raw = str((rows[0] if rows else {}).get("d") or "").strip()
    return raw[:10] if raw else ""


def resolve_end_date(storage, end_date: str = "") -> str:
    """Demo oynasining oxirgi sanasi.

    Aniq berilmagan bo'lsa — real reyslarning oxirgi sanasi (global
    `_last_active_date` siljib ketmasligi uchun), u bo'lmasa bugun.
    """
    raw = str(end_date or "").strip()
    if raw:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError("--end-date YYYY-MM-DD formatida bo'lishi kerak") from exc
    return _real_last_trip_date(storage) or date.today().isoformat()


def _ensure_company(storage, company_name: str) -> tuple[int, dict, bool]:
    """Kompaniyani topadi yoki yaratadi (mavjud statusni o'zgartirmaydi)."""
    from ..db.tenancy import slugify

    slug = slugify(company_name)
    row = storage.find("companies", slug=slug)
    if row and str(row.get("name") or "").strip().lower() != company_name.lower():
        raise ValueError(
            f"'{slug}' slug boshqa korxonaga band: {row.get('name')}")
    created = False
    if not row:
        storage.insert("companies", {
            "name": company_name,
            "slug": slug,
            "subscription_plan": "demo",
            "subscription_expire_date": "",
            "status": "active",
            "max_users": 2,
            "max_drivers": 50,
            "max_routes": 10,
            "features": _js({"demo": True}),
            "notes": "Namuna (demo) korxona — demo-seed buyrug'i orqali yaratilgan",
        })
        row = storage.find("companies", slug=slug)
        created = True
    if not row:
        raise RuntimeError("companies jadvaliga yozib bo'lmadi")
    return int(row["id"]), row, created


def _seed_catalog(storage, cid: int) -> None:
    """Yo'nalishlar, avtobuslar, haydovchilar va profillari.

    `profiles` jadvaliga yozilmaydi: unda `name` unikal (production sxemasi)
    — bitta korxonaning bir nechta yo'nalishini saqlab bo'lmaydi. Tenant
    yo'nalishlari `routes` jadvalidan olinadi (company_routes fallback).
    """
    for rid, rname, _km in _DEMO_ROUTES:
        storage.upsert("routes", ["external_id"], {
            "external_id": rid,
            "name": rname,
            "parent_id": "",
            "entity_type": "ROUTE",
            "is_brutto": "t",
            "data": _js({"demo": True}),
            "company_id": cid,
        })

    for vid, plate, model, ri in _DEMO_VEHICLES:
        storage.upsert("vehicles", ["external_id"], {
            "external_id": vid,
            "plate_number": plate,
            "garage_number": plate,
            "model": model,
            "route_id": _DEMO_ROUTES[ri][0],
            "data": _js({"demo": True, "route_name": _DEMO_ROUTES[ri][1]}),
            "company_id": cid,
        })

    for i, (did, name, ri) in enumerate(_DEMO_DRIVERS):
        storage.upsert("drivers", ["external_id"], {
            "external_id": did,
            "full_name": name,
            "tin": "",
            "route_id": _DEMO_ROUTES[ri][0],
            "data": _js({"demo": True}),
            "company_id": cid,
        })
        storage.upsert("driver_profiles", ["driver_id"], {
            "driver_id": did,
            "phone": f"+99890000{i + 1:04d}",
            "passport_number": f"DEMO-P-{i + 1:03d}",
            "passport_issued_by": "DEMO IIB",
            "passport_expiry": "2032-12-31",
            "license_number": f"DEMO-L-{i + 1:03d}",
            "license_category": "D",
            "license_expiry": "2031-12-31",
            "gender": "Erkak",
            "rating": round(4.3 + (i % 6) * 0.1, 1),
            "blacklisted": 0,
            "km_rate": DEMO_KM_RATE,
            "notification_enabled": 0,
            "notes": "Demo haydovchi",
            "data": _js({"demo": True}),
            "company_id": cid,
        })


def _purge_window(storage) -> None:
    """Barcha eski demo qatorlarini o'chiradi (oyna qayta yoziladi).

    Demo ma'lumot doim aynan `start..end` oynasida qoladi — qayta
    ishga tushirilganda tarix to'planib ketmaydi.
    """
    ph = storage.db.ph
    route_ids = [r[0] for r in _DEMO_ROUTES]
    driver_ids = [d[0] for d in _DEMO_DRIVERS]
    rph = ", ".join([ph] * len(route_ids))
    dph = ", ".join([ph] * len(driver_ids))
    for table in ("trips", "route_daily", "schedules"):
        storage.db.execute(
            f"DELETE FROM {table} WHERE route_id IN ({rph})",
            tuple(route_ids))
    storage.db.execute(
        f"DELETE FROM driver_work_logs WHERE driver_id IN ({dph})",
        tuple(driver_ids))


def _build_rows(cid: int, start: str, end: str) -> dict:
    """Reys, kunlik, grafik va AVTO km qatorlarini yasaydi."""
    days = (datetime.strptime(end, "%Y-%m-%d").date()
            - datetime.strptime(start, "%Y-%m-%d").date()).days + 1
    trips: list[dict] = []
    daily: list[dict] = []
    schedules: list[dict] = []
    logs: list[dict] = []

    for di in range(days):
        day = (datetime.strptime(start, "%Y-%m-%d").date()
               + timedelta(days=di)).isoformat()
        for vi, (vid, plate, model, ri) in enumerate(_DEMO_VEHICLES):
            rid, rname, km = _DEMO_ROUTES[ri]
            pool = [d for d in _DEMO_DRIVERS if d[2] == ri]
            slot = sum(1 for v in _DEMO_VEHICLES[:vi] if v[3] == ri)
            did = pool[(di + slot) % len(pool)][0]
            pending = di == days - 1 and vi in (1, 4)
            code = (di * 6 + vi) % 60
            accepted = 0
            dist_total = 0.0

            for ti, planned in enumerate(_TIMES):
                if pending:
                    status, actual, dist = "PENDING_ACCESS", "", 0.0
                elif code in (5, 37):
                    status, actual, dist = "REJECTED", "", 0.0
                elif code == 11:
                    status, actual, dist = "NOT_ACCEPTED", "", 0.0
                elif code == 23:
                    status = "ZERO_MILEAGE"
                    actual = _shift_time(planned, 6 + (di + vi + ti) % 5)
                    dist = 0.0
                else:
                    status = "ACCEPTED"
                    actual = _shift_time(planned, 3 + (di * 2 + vi + ti) % 7)
                    dist = round(
                        km * (1 + (((di + vi + ti) % 5) - 2) * 0.01), 2)
                    accepted += 1
                    dist_total += dist
                trips.append({
                    "date": day,
                    "route_id": rid,
                    "vehicle_id": vid,
                    "driver_id": did,
                    "planned_time": planned,
                    "actual_time": actual,
                    "status": status,
                    "source": "DEMO",
                    "last_synced_at": "",
                    "distance_km": dist,
                    "data": _js({"demo": True, "route_name": rname}),
                    "company_id": cid,
                })

            working_day = 0 if pending else 1
            daily.append({
                "date": day,
                "route_id": rid,
                "vehicle_id": vid,
                "vehicle_number": plate,
                "vehicle_brand": model,
                "shift_name": "1-smena",
                "working_day": working_day,
                "trip_plan": len(_TIMES),
                "trip_fact": accepted,
                "trip_passed": accepted,
                "trip_approved": accepted,
                "distance_plan": round(km * len(_TIMES), 2),
                "distance_fact": round(dist_total, 2),
                "distance_fact_extra": 0.0,
                "last_synced_at": "",
                "data": _js({"demo": True}),
                "company_id": cid,
            })
            schedules.append({
                "date": day,
                "route_id": rid,
                "graph_name": f"DEMO-{ri + 1}",
                "driver_id": did,
                "vehicle_id": vid,
                "shift_name": "1-smena",
                "start_time": _TIMES[0],
                "end_time": _TIMES[-1],
                "trip_count": len(_TIMES),
                "data": _js({"demo": True}),
                "company_id": cid,
            })
            logs.append({
                "date": day,
                "driver_id": did,
                "vehicle_id": vid,
                "distance_km": round(dist_total, 2),
                "trip_count": accepted,
                "note": "AVTO",
                "distance_plan": round(km * len(_TIMES), 2),
                "trip_plan": len(_TIMES),
                "working_day": working_day,
                "trip_passed": accepted,
                "trip_approved": accepted,
                "company_id": cid,
            })

    return {"trips": trips, "route_daily": daily,
            "schedules": schedules, "driver_work_logs": logs}


def _seed_once(storage, cid: int, start: str, end: str) -> dict:
    """Jarima, murojaat va avanslarni faqat bir marta yozadi."""
    ph = storage.db.ph
    out = {"fines": 0, "appeals": 0, "avans": 0}

    has = storage.query(
        f"SELECT id FROM driver_fines WHERE driver_id LIKE {ph}",
        ("demo-drv-%",), limit=1)
    if not has:
        for did, offset, amount, reason in (
                ("demo-drv-01", 10, 50000, "Yo'l harakati qoidasi (demo)"),
                ("demo-drv-05", 5, 30000, "Jadvalga kechikish (demo)")):
            pay_date = (datetime.strptime(end, "%Y-%m-%d").date()
                        - timedelta(days=offset)).isoformat()
            pay_date = max(pay_date, start)
            storage.insert("driver_fines", {
                "driver_id": did,
                "date": pay_date,
                "amount": amount,
                "reason": reason,
                "status": "ACTIVE",
                "company_id": cid,
            })
            out["fines"] += 1

    has = storage.query(
        f"SELECT id FROM driver_appeals WHERE driver_id LIKE {ph}",
        ("demo-drv-%",), limit=1)
    if not has:
        for did, title, text, status in (
                ("demo-drv-02", "Oylik hisob-kitob",
                 "Demo murojaat: oylik hisob-kitobini tekshirishni so'rayman.",
                 "YANGI"),
                ("demo-drv-06", "Grafik o'zgarishi",
                 "Demo murojaat: dam olish kunini almashtirish so'rovi.",
                 "JAVOB_YOZILDI")):
            storage.insert("driver_appeals", {
                "driver_id": did,
                "title": title,
                "text": text,
                "status": status,
                "reply": "Demo javob." if status == "JAVOB_YOZILDI" else "",
                "replied_at": "",
                "company_id": cid,
            })
            out["appeals"] += 1

    has = storage.query(
        f"SELECT id FROM avans WHERE driver_id LIKE {ph}",
        ("demo-drv-%",), limit=1)
    if not has:
        pay_date = max(
            (datetime.strptime(end, "%Y-%m-%d").date()
             - timedelta(days=3)).isoformat(), start)
        for did, name, ri, amount in (
                ("demo-drv-03", "Dilshod Rahimov", 0, 300000),
                ("demo-drv-07", "Rustam Qodirov", 1, 250000)):
            rid, rname, _km = _DEMO_ROUTES[ri]
            storage.insert("avans", {
                "driver_id": did,
                "name": name,
                "route_id": rid,
                "route_name": rname,
                "amount": amount,
                "pay_date": pay_date,
                "note": "Demo avans",
                "company_id": cid,
            })
            out["avans"] += 1

    return out


def _merge_user_data(raw, pages: tuple[str, ...]) -> str:
    """Mavjud `data` JSON'ini saqlab, `pages`/`demo` ni yangilaydi."""
    try:
        data = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["pages"] = list(pages)
    data["demo"] = True
    return _js(data)


def _ensure_user(storage, cid: int, company_name: str,
                 username: str, password: str) -> tuple[bool, str]:
    """Demo foydalanuvchisini yaratadi/yangilaydi.

    Qaytadi: (yangi_yaratildi, generatsiya_qilingan_parol). Parol faqat
    yangi akkauntda va `--password` berilmaganda generatsiya qilinadi.
    """
    from .deps import hash_password, password_error

    if password:
        err = password_error(password, username)
        if err:
            raise ValueError(err)

    row = storage.dashboard_user_get(username=username)
    if row:
        owner = str(row.get("company") or "").strip()
        if owner and owner.lower() != company_name.lower():
            raise ValueError(
                f"'{username}' logini '{owner}' korxonasiga tegishli. "
                "Boshqa --username tanlang.")
        data = _merge_user_data(row.get("data"), DEMO_PAGES)
        kwargs = {"role": DEMO_ROLE, "active": 1, "data": data}
        if password:
            digest, salt = hash_password(password)
            kwargs.update(salt=salt, password_hash=digest)
        storage.dashboard_user_update(int(row["id"]), **kwargs)
        _force_company(storage, int(row["id"]), cid, company_name)
        return False, ""

    generated = ""
    if not password:
        generated = password = secrets.token_urlsafe(9)
    digest, salt = hash_password(password)
    row_id = storage.dashboard_user_add(
        username, digest, salt, DEMO_ROLE, company_name, 1,
        _merge_user_data("{}", DEMO_PAGES))
    if not row_id:
        raise RuntimeError("demo foydalanuvchisini yaratib bo'lmadi")
    _force_company(storage, row_id, cid, company_name)
    return True, generated


def _force_company(storage, row_id: int, cid: int, company_name: str) -> None:
    """`company`/`company_id` ni aniq yozadi (keshga bog'liq bo'lmasligi uchun)."""
    ph = storage.db.ph
    storage.db.execute(
        f"UPDATE dashboard_users SET company = {ph}, company_id = {ph} "
        f"WHERE id = {ph}",
        (str(company_name or "").strip(), int(cid), int(row_id)))


def _check_username(storage, username: str, company_name: str) -> None:
    """Login boshqa korxona akkauntiga tegishli bo'lsa — yozishdan oldin xato."""
    row = storage.dashboard_user_get(username=username)
    if not row:
        return
    owner = str(row.get("company") or "").strip()
    if owner and owner.lower() != company_name.lower():
        raise ValueError(
            f"'{username}' logini '{owner}' korxonasiga tegishli. "
            "Boshqa --username tanlang.")


def seed_demo(storage=None, *, username: str = DEMO_USERNAME,
              password: str = "", days: int = 21,
              company_name: str = DEMO_COMPANY,
              end_date: str = "") -> dict:
    """Demo korxonani va namuna ma'lumotlarni yaratadi (idempotent)."""
    if storage is None:
        from ..db.storage import get_storage
        storage = get_storage()
    if not storage.enabled:
        raise RuntimeError("DB rejimi o'chiq — demo ma'lumot yozilmaydi")

    company_name = str(company_name or "").strip() or DEMO_COMPANY
    username = str(username or "").strip().lower() or DEMO_USERNAME
    if not _USERNAME_RE.match(username):
        raise ValueError(
            "Login 2-32 belgi: kichik harflar, raqamlar, '.', '_', '-'")
    try:
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("--days butun son bo'lishi kerak") from exc
    if not 1 <= days <= 365:
        raise ValueError("--days 1..365 oralig'ida bo'lishi kerak")
    if password:
        from .deps import password_error
        err = password_error(password, username)
        if err:
            raise ValueError(err)

    end = resolve_end_date(storage, end_date)
    _check_username(storage, username, company_name)
    cid, company_row, company_created = _ensure_company(
        storage, company_name)

    from ..core import tenant
    from . import deps
    tenant.reset()
    deps.invalidate_user_access(username)

    _seed_catalog(storage, cid)

    start = (datetime.strptime(end, "%Y-%m-%d").date()
             - timedelta(days=days - 1)).isoformat()

    _purge_window(storage)
    rows = _build_rows(cid, start, end)
    for table, key_cols in (
            ("trips", ["date", "route_id", "vehicle_id", "driver_id",
                       "planned_time"]),
            ("route_daily", ["date", "route_id", "vehicle_number"]),
            ("schedules", ["date", "route_id", "graph_name", "driver_id"]),
            ("driver_work_logs", ["date", "driver_id", "vehicle_id"])):
        storage.bulk_upsert(table, key_cols, rows[table])

    extras = _seed_once(storage, cid, start, end)
    user_created, generated = _ensure_user(
        storage, cid, company_name, username, password)

    tenant.reset()
    deps.invalidate_user_access(username)

    warnings: list[str] = []
    real_end = _real_last_trip_date(storage)
    if real_end and end > real_end:
        warnings.append(
            f"Demo oxiri ({end}) real reyslardan ({real_end}) keyin — "
            "panelning standart sanasi demo kuniga siljiydi.")
    if not tenant.company_active(company_row):
        warnings.append(
            "Kompaniya bloklangan yoki obunasi tugagan — login ishlamaydi.")

    return {
        "company": company_name,
        "company_id": cid,
        "company_created": company_created,
        "company_active": tenant.company_active(company_row),
        "username": username,
        "role": DEMO_ROLE,
        "pages": list(DEMO_PAGES),
        "password": generated,
        "user_created": user_created,
        "routes": [{"id": r[0], "name": r[1], "km": r[2]}
                   for r in _DEMO_ROUTES],
        "window": {"from": start, "to": end, "days": days},
        "counts": {
            "drivers": len(_DEMO_DRIVERS),
            "vehicles": len(_DEMO_VEHICLES),
            "trips": len(rows["trips"]),
            "route_daily": len(rows["route_daily"]),
            "schedules": len(rows["schedules"]),
            "work_logs": len(rows["driver_work_logs"]),
            **extras,
        },
        "warnings": warnings,
    }
