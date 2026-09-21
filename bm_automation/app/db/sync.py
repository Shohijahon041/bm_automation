"""BM API → Database sinxronizatsiya (idempotent, tranzaksiyada).

Har bir `sync_*` funksiyasi BM API'dan ma'lumot olib, natural kalit bo'yicha
bazaga yozadi (upsert):

- takroriy yuklash dublikat YARATMAYDI;
- bir xil ma'lumotni qayta yuklash mavjud yozuvni BUZMAYDI (faqat yangilaydi);
- har bir sync bitta tranzaksiyada bajariladi — xatolikda qisman yozuv qolmaydi;
- DB ishlamasa xato ko'tarilmaydi — `SyncResult.error` da qayd qilinadi.
"""

from __future__ import annotations

import traceback as _tb
import uuid
from datetime import date as _date, timedelta
from typing import Callable

from ..api.client import BMClient
from ..config.settings import km_rate_setting
from ..repositories.driver_repo import DRIVERS, DriverRepository
from ..repositories.duty_repo import DutyRepository
from ..repositories.gross_repo import GrossRepository
from ..repositories.route_repo import RouteRepository
from ..repositories.vehicle_repo import VehicleRepository
from ..repositories.waybill_repo import WaybillRepository
from ..utils.km import trip_km
from ..utils.logger import get_logger
from .models import (SyncResult, SyncSource, TripRecord, TripStatus,
                     json_dumps as _json_dumps, json_loads, now_utc)
from .storage import Storage

log = get_logger("bm_automation.db")


# ------------------------------------------------------------ yordamchilar

def _get(data: dict, *keys: str, default: str = "") -> str:
    """dict'dan bir nechta kalit bo'yicha birinchi topilgan qiymatni oladi."""
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _safe_loop(storage: Storage, source: str, body: Callable[[], SyncResult]) -> SyncResult:
    """Funktsiyani xavfsiz bajaradi — xato SyncResult'da qayd qilinadi."""
    try:
        return body()
    except Exception as exc:  # noqa: BLE001 - DB/api xatolari tizimni sindirmaydi
        storage.record_error(source=source, message=str(exc),
                             traceback=_tb.format_exc())
        return SyncResult(entity=source, error=str(exc))


# -------------------------------------------------------------- entity sync

def sync_statuses(storage: Storage) -> SyncResult:
    """trip_statuses jadvalini to'ldiradi (idempotent)."""
    result = SyncResult(entity="trip_statuses", total=len(TripStatus.values()))
    try:
        from .schema import seed_statuses
        result.inserted = seed_statuses(storage.db)
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
    return result


def sync_profiles(storage: Storage, profiles: list | None = None) -> SyncResult:
    """profiles.json → DB (idempotent)."""
    result = SyncResult(entity="profiles")
    if profiles is None:
        from ..core.profiles import all_profiles
        profiles = all_profiles()
    for p in profiles:
        try:
            result.total += 1
            status = storage.save_profile(
                external_id=_get(p, "id", "profileId", "externalId"),
                name=_get(p, "name"),
                route_id=_get(p, "routeVariantId", "routeId"),
                profile_id=_get(p, "profileId"),
                route_name=_get(p, "routeName", "route_name"),
                start1=_get(p, "start1", "startOne", "firstStart"),
                start2=_get(p, "start2", "startTwo", "secondStart"),
                data=p,
            )
            if status == "inserted":
                result.inserted += 1
            else:
                result.updated += 1
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            break
    return result


def sync_routes(storage: Storage, client: BMClient, brutto: bool = True) -> SyncResult:
    """route-variants/tree → DB (idempotent)."""
    result = SyncResult(entity="routes")
    repo = RouteRepository(client)

    def _run() -> SyncResult:
        tree = repo.tree(brutto=brutto)
        with storage.db.transaction():
            def walk(nodes, parent: str = ""):
                for node in nodes or []:
                    result.total += 1
                    status = storage.save_route(
                        external_id=_get(node, "id", "routeVariantId"),
                        name=_get(node, "name"),
                        parent_id=parent,
                        entity_type=_get(node, "entityType", default="NODE"),
                        is_brutto=brutto,
                        data=node,
                    )
                    if status == "inserted":
                        result.inserted += 1
                    else:
                        result.updated += 1
                    walk(node.get("children") or [], parent=_get(node, "id", "routeVariantId"))

            walk(tree if isinstance(tree, list) else [tree])
        return result

    return _safe_loop(storage, "routes", _run)


def sync_drivers(storage: Storage, client: BMClient, route_id: str,
                 date_str: str) -> SyncResult:
    """Haydovchilar ro'yxati → DB (idempotent)."""
    result = SyncResult(entity="drivers")
    repo = DriverRepository(client)

    def _run() -> SyncResult:
        items = repo.by_route_variant(route_id, date_str)
        with storage.db.transaction():
            for d in items or []:
                result.total += 1
                status = storage.save_driver(
                    external_id=_get(d, "id", "driverId", "driverIdValue"),
                    full_name=_get(d, "fullName", "name", "driverName"),
                    tin=_get(d, "tin", "tinNumber"),
                    route_id=route_id,
                    data=d,
                )
                if status == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1
        return result

    return _safe_loop(storage, "drivers", _run)


def sync_driver_profiles(storage: Storage, client: BMClient, route_id: str,
                         date_str: str, force: bool = False) -> SyncResult:
    """Haydovchi profillarini saytdagi to'liq ma'lumot bilan to'ldiradi.

    Ro'yxat API'da telefon/PINFL maskalangan; to'liq qiymat uchun har bir
    haydovchining detail'i (`GET /drivers/{id}`) so'raladi. Passport BM
    API'da yo'q — u maydon o'zgarishsiz qoladi.

    ``force=False`` bo'lsa profilida telefon va guvohnoma allaqachon bor
    haydovchilar uchun detail so'ralmaydi (kunlik sync tezroq ishlaydi).
    """
    result = SyncResult(entity="driver_profiles")

    def _run() -> SyncResult:
        drivers = storage.query(
            f"SELECT external_id, data FROM drivers WHERE route_id={storage.db.ph}",
            (route_id,), limit=10000)
        if not drivers:
            result.error = "drivers jadvali bo'sh — avval sync_drivers ishga tushiring"
            return result
        rate = km_rate_setting()
        failures: list[str] = []
        with storage.db.transaction():
            for d in drivers:
                driver_id = str(d.get("external_id") or "").strip()
                if not driver_id:
                    continue
                result.total += 1
                payload = json_loads(d.get("data")) or {}
                if not isinstance(payload, dict):
                    payload = {}
                existing = storage.find("driver_profiles", driver_id=driver_id) or {}
                need_detail = force or not (existing.get("phone")
                                            or existing.get("license_number"))
                detail = None
                if need_detail:
                    try:
                        detail = client.get(f"{DRIVERS}/{driver_id}")
                    except Exception as exc:  # noqa: BLE001 - biri buzilsa davom etamiz
                        if _not_found(exc):
                            detail = None  # haydovchi mavjud emas — xato emas
                        else:
                            failures.append(f"{driver_id}")  # jamlab qayd etamiz
                    if not isinstance(detail, dict):
                        detail = None
                src = detail if detail is not None else payload
                # Faqat yangi profil uchun global km_rate; mavjud profilingiz
                # shaxsiy km_rate saqlanib qoladi (sync uni HECH QACHON
                # ustiga yozmaydi).
                existing_rate = existing.get("km_rate")
                if existing_rate and float(existing_rate) > 0:
                    use_rate = float(existing_rate)
                else:
                    use_rate = rate
                fields = {
                    "license_number": _get(src, "licenseNumber"),
                    "license_category": _get(src, "category"),
                    "license_expiry": _get(src, "licenseExpiryDate"),
                    "km_rate": use_rate,
                    "data": detail or payload,
                }
                # To'liq telefon faqat detail'dan; skip holatida eski (to'liq)
                # qiymat saqlanib qoladi va maskalangan ro'yxat qiymati bilan
                # ustiga yozilmaydi.
                if detail is not None or force:
                    fields["phone"] = _get(src, "phoneNumber")
                status = storage.save_driver_profile(
                    driver_id=driver_id, **fields)
                if status == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1
                pinfl = _get(detail, "pinfl") if detail is not None else ""
                if pinfl:
                    storage.save_driver(
                        external_id=driver_id,
                        full_name=_get(detail, "fullName", "name", "driverName"),
                        tin=pinfl, route_id=route_id, data=payload)
        # Alohida haydovchi detallaridagi xatolar har biri uchun alohida emas,
        # birlashtirilib BIR qator qilib qayd etiladi (errors jadvalini
        # "flood" qilmaslik uchun).
        if failures:
            storage.record_error(
                source="sync.driver_profiles",
                message=(f"{route_id}: {len(failures)} ta haydovchi detali "
                         f"olib bo'lmadi ({', '.join(failures[:10])})"
                         + ("..." if len(failures) > 10 else "")),
                context={"route_id": route_id, "failed": failures[:50],
                         "count": len(failures)})
        return result

    return _safe_loop(storage, "driver_profiles", _run)


def sync_vehicles(storage: Storage, client: BMClient, route_id: str,
                  date_str: str) -> SyncResult:
    """Avtobuslar ro'yxati → DB (idempotent)."""
    result = SyncResult(entity="vehicles")
    repo = VehicleRepository(client)

    def _run() -> SyncResult:
        items = repo.by_route_variant(route_id, date_str)
        with storage.db.transaction():
            for v in items or []:
                result.total += 1
                status = storage.save_vehicle(
                    external_id=_get(v, "id", "vehicleId"),
                    plate_number=_get(v, "plateNumber", "plateNum"),
                    garage_number=_get(v, "garageNumber", "garage"),
                    model=_get(v, "vehicleModel", "model", "vehicleType"),
                    route_id=route_id,
                    data=v,
                )
                if status == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1
        return result

    return _safe_loop(storage, "vehicles", _run)


_NOT_FOUND_CODES = {"TRIP_NOT_FOUND", "DUTY_NOT_FOUND", "NOT_FOUND"}


def _not_found(exc) -> bool:
    """404'da 'ma'lumot yo'q' holatini aniqlaydi (xato emas).

    BM API bugungi sana uchun hali yaratilmagan hisobotlarda 404 qaytaradi
    (masalan `gross/trip` -> TRIP_NOT_FOUND, `duty` -> DUTY_NOT_FOUND).
    Bu dastur xatosi emas — oddiygina o'sha sana uchun ma'lumot yo'q.
    """
    from ..api.client import BMApiError

    if not isinstance(exc, BMApiError) or exc.status != 404:
        return False
    code = str(exc.code or "").upper()
    message = str(exc.message or "").upper()
    if not code and not message:
        return True  # 404 — yo'q ob'ekt, xato emas
    for tok in (code, message):
        if tok in _NOT_FOUND_CODES or tok.endswith("_NOT_FOUND"):
            return True
    return False


def sync_duties(storage: Storage, client: BMClient, route_id: str,
                date_str: str) -> SyncResult:
    """Duty (graphs) → duties + schedules (idempotent).

    Reyslar (trips) duty'dan YARATILMAYDI — ular haqiqiy bajarilgan qatnovlar
    sifatida `sync_waybills` orqali keladi. Duty grafiklar `schedules`
    jadvalida reja sifatida saqlanadi (trip_count = rejadagi qatnovlar soni).
    """
    result = SyncResult(entity="duties")
    repo = DutyRepository(client)

    def _run() -> SyncResult:
        try:
            duty = repo.by_date(route_id, date_str) or {}
        except Exception as exc:
            if _not_found(exc):
                return result  # o'sha sana uchun duty yo'q — xato emas
            raise
        graphs = duty.get("graphs") or []
        duty_id = _get(duty, "id", "dutyId")
        date = _get(duty, "date") or date_str
        driver_ids = {}
        for g in graphs:
            nm = _get(g, "driverName")
            did = _get(g, "driverId")
            if nm and did:
                driver_ids[nm] = did
            nm2 = _get(g, "secondDriverName")
            did2 = _get(g, "secondDriverId")
            if nm2 and did2:
                driver_ids[nm2] = did2

        with storage.db.transaction():
            result.total += 1
            status = storage.save_duty(
                external_id=duty_id or f"{date}|{route_id}",
                date=date, route_id=route_id,
                shift_id=_get(duty, "shiftId"),
                data=duty,
            )
            if status == "inserted":
                result.inserted += 1
            else:
                result.updated += 1

            seen = set()
            key_cols = ["date", "route_id", "graph_name", "driver_id"]
            keep = set()
            for g in graphs:
                key = (date, route_id, _get(g, "graphName"), _get(g, "driverId"))
                if key in seen:
                    continue
                seen.add(key)
                keep.add(key)
                storage.save_schedule(
                    date=date, route_id=route_id,
                    graph_name=_get(g, "graphName"),
                    driver_id=_get(g, "driverId"),
                    vehicle_id=_get(g, "vehicleId", "vehicle"),
                    shift_name=_get(g, "shiftName"),
                    start_time=_get(g, "startTime"),
                    end_time=_get(g, "endTime"),
                    trip_count=int(_get(g, "tripCount", default="0") or 0),
                    data=g,
                )
            # Replace-sync: kunning eski duty grafiklari (haydovchi almashtirilgan
            # bo'lsa ham) xotirada qolmasligi uchun hozirgi duty'da yo'q qatorlar
            # o'chiriladi. Aks holda reja (SUM(trip_count)) sun'iy ko'payadi.
            result.deleted += storage.delete_missing(
                "schedules", key_cols, route_id, date, date, keep)
        return result

    return _safe_loop(storage, "duties", _run)


def _waybill_rows(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "waybills", "list", "items", "rows"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


# Bitta waybill qatori to'liq holda ~90KB — points/waybillStations ichida
# GPS trayektoriya bor. `data` ustuniga yozilganda kunlik sync ~16MB ga
# yetadi va Supabase statement_timeout (2min) ga uchiradi. Dashboard faqat
# ustun qiymatlarini ishlatadi — trayektoriya va sifat ro'yxatlari saqlanmaydi.
_TRIM_KEYS = (
    "points", "planPoints", "tempPoints", "waybillStations", "tempStations",
    "tempRoute", "tmpRouteInfo", "tempRouteType", "brokenQualities",
    "poorQualities",
)


def _trim_row(row) -> dict:
    """Hisobot qatoridan katta GPS maydonlarini olib tashlaydi."""
    if not isinstance(row, dict):
        return {}
    return {k: v for k, v in row.items() if k not in _TRIM_KEYS}


def sync_waybills(storage: Storage, client: BMClient, route_id: str,
                  from_date: str, to_date: str) -> SyncResult:
    """Waybill hisoboti → waybills + trips (status bilan, idempotent).

    Har bir waybill qatori bitta HAQIQIY qatnov. `plateNumber` → `vehicles`
    jadvali orqali `vehicle_id`ga bog'lanadi; vaqtlar `startTm`/`endTm`.
    `ZERO_MILEAGE` (nollik masofa) qatnovlar ham trips jadvaliga yoziladi —
    `jami qatnovlar` saytdagidek nolliklarni ham o'z ichiga oladi.
    """
    result = SyncResult(entity="waybills")
    repo = WaybillRepository(client)

    def _run() -> SyncResult:
        data = repo.report(route_id, from_date, to_date)
        rows = _waybill_rows(data)
        plate_veh = {
            str(v.get("plate_number") or "").strip(): str(v.get("external_id") or "")
            for v in storage.query("SELECT external_id, plate_number FROM vehicles")
        }
        trip_keys: set[tuple] = set()
        wb_keys: set[tuple] = set()
        wb_rows: list[dict] = []
        trip_rows: list[dict] = []
        for row in rows or []:
            result.total += 1
            row = _trim_row(row)
            date = _get(row, "date", "dutyDate", "reportDate")
            plate = _get(row, "plateNumber", "plateNum", "vehiclePlate")
            status = TripStatus.normalize(
                _get(row, "status", "tripStatus", "acceptStatus"))
            if not date or not plate:
                continue
            vehicle_id = _get(row, "vehicleId") or plate_veh.get(plate, "")
            direction = _get(row, "direction", "routeDirection")
            driver_id = _get(row, "driverId")
            planned_time = _get(row, "startTm", "startTime",
                                "planTime", "plannedTime")
            wb_keys.add((date, route_id, plate, direction))
            trip_keys.add((date, route_id, vehicle_id, driver_id,
                           planned_time or ""))
            wb_rows.append({
                "date": str(date),
                "route_id": str(route_id),
                "plate_number": str(plate),
                "direction": str(direction),
                "vehicle_id": str(vehicle_id),
                "driver_id": str(driver_id),
                "status": str(status),
                "data": _json_dumps(row),
            })
            trip = TripRecord(
                date=date, route_id=route_id,
                vehicle_id=vehicle_id,
                driver_id=driver_id,
                planned_time=planned_time,
                actual_time=_get(row, "endTm", "endTime", "actualTime"),
                status=status,
                source=SyncSource.WAYBILL.value,
                data=row,
            )
            trip_rows.append(trip.to_row())
            result.total += 1
        with storage.db.transaction():
            wb_inserted, wb_updated = storage.bulk_upsert(
                "waybills", ["date", "route_id", "plate_number", "direction"],
                wb_rows)
            tr_inserted, tr_updated = storage.bulk_upsert(
                "trips", ["date", "route_id", "vehicle_id", "driver_id",
                          "planned_time"],
                trip_rows)
            result.inserted += wb_inserted + tr_inserted
            result.updated += wb_updated + tr_updated
            # Replace: hisobotda endi yo'q (eski) WAYBILL qatorlarini o'chirish
            result.deleted += storage.delete_missing(
                "trips", ["date", "route_id", "vehicle_id", "driver_id",
                          "planned_time"],
                route_id, from_date, to_date, trip_keys,
                extra_cond=f"source={storage.db.ph}",
                extra_params=(SyncSource.WAYBILL.value,))
            result.deleted += storage.delete_missing(
                "waybills", ["date", "route_id", "plate_number", "direction"],
                route_id, from_date, to_date, wb_keys)
        return result

    return _safe_loop(storage, "waybills", _run)


def sync_work_logs(storage: Storage, from_date: str = "", to_date: str = "",
                   route_id: str = "") -> SyncResult:
    """Waybill trips'idan kunlik km hisobini `driver_work_logs`'ga yig'adi.

    Km manbasi ustuvorligi:
      1. `route_daily` (brutto-route `distanceFact`) — saytdagi rasmiy
         hisob-kitob. Bitta (sana, avtobus) kuniga eng ko'p reys bajargan
         haydovchiga to'liq yoziladi (bir avtobusni ikki haydovchi
         boshqarsa km ikki marta hisoblanmaydi);
      2. `route_daily`'da yo'q (sana, avtobus) uchun waybill trips'larining
         km yig'indisi — faqat amalda bajarilgan (ACCEPTED/APPROVED)
         qatnovlar (qabul qilinmagan/bekor/nollik reyslar ish haqqiga kirmaydi).

    `trip_count` shunga mos: route_daily `tripFact` yoki trips soni.
    Natija (sana, haydovchi, avtobus) bo'yicha bitta qatorga yig'iladi va
    `note='AVTO'` bilan saqlanadi. Qo'lda kiritilgan (dlog) qaydlar
    o'zgarmaydi — agar shu (sana, haydovchi, avtobus) uchun qo'lda qayd
    bo'lsa, AVTO qator qo'shilmaydi. Hisobotda yo'q bo'lib qolgan eski
    AVTO qatorlar o'chiriladi (idempotent).

    Waybill'da bor-u `drivers` jadvalida yo'q haydovchilar (masalan
    vaqtincha o'rinbosar) avtomatik ro'yxatga olinadi — aks holda
    ularning ish haqi hisobga kirmaydi.
    """
    result = SyncResult(entity="driver_work_logs")
    ph = storage.db.ph

    clauses = [f"source = {ph}", f"driver_id != {ph}"]
    params: list = [SyncSource.WAYBILL.value, ""]
    # Trips so'rovi yo'nalish bo'yicha (route_id trips'da bor).
    trip_scope: list[str] = []
    trip_bind: list = []
    # driver_work_logs'da route_id ustuni yo'q — u haydovchi ro'yxati orqali.
    log_scope: list[str] = []
    log_bind: list = []
    if from_date:
        trip_scope.append(f"date >= {ph}")
        log_scope.append(f"date >= {ph}")
        trip_bind.append(from_date)
        log_bind.append(from_date)
    if to_date:
        trip_scope.append(f"date <= {ph}")
        log_scope.append(f"date <= {ph}")
        trip_bind.append(to_date)
        log_bind.append(to_date)
    if route_id:
        trip_scope.append(f"route_id = {ph}")
        trip_bind.append(route_id)
        log_scope.append(f"driver_id IN (SELECT external_id FROM drivers "
                         f"WHERE route_id = {ph})")
        log_bind.append(route_id)
    rows = storage.query(
        "SELECT date, driver_id, vehicle_id, distance_km, status, data FROM trips "
        f"WHERE {' AND '.join(clauses + trip_scope)}",
        tuple(params + trip_bind), limit=50000)

    done_statuses = {TripStatus.ACCEPTED.value, TripStatus.APPROVED.value}
    agg: dict[tuple, dict] = {}
    driver_names: dict[str, str] = {}
    for r in rows:
        key = (str(r.get("date") or ""), str(r.get("driver_id") or ""),
               str(r.get("vehicle_id") or ""))
        if not key[0] or not key[1]:
            continue
        if key[1] not in driver_names:
            d = json_loads(r.get("data")) if r.get("data") else None
            if isinstance(d, dict):
                nm = _get(d, "driverName", "driver_name", "fullName", "name")
                if nm:
                    driver_names[key[1]] = nm
        a = agg.setdefault(key, {"km": 0.0, "cnt": 0})
        a["cnt"] += 1
        if str(r.get("status") or "") in done_statuses:
            a["km"] += float(r.get("distance_km") or 0)

    # Noma'lum haydovchilarni ro'yxatdan o'tkazamiz (waybill'da bor, drivers'da yo'q).
    if route_id and driver_names:
        known = {str(x.get("external_id") or "") for x in storage.query(
            f"SELECT external_id FROM drivers WHERE route_id = {ph}",
            (route_id,), limit=50000)}
        for did, nm in driver_names.items():
            if did in known:
                continue
            storage.save_driver(external_id=did, full_name=nm,
                                route_id=route_id, data={})
            known.add(did)

    # Brutto-route hisob-kitobi — rasmiy km manbasi (ustuvor).
    rd_scope = []
    rd_bind: list = []
    if from_date:
        rd_scope.append(f"date >= {ph}")
        rd_bind.append(from_date)
    if to_date:
        rd_scope.append(f"date <= {ph}")
        rd_bind.append(to_date)
    if route_id:
        rd_scope.append(f"route_id = {ph}")
        rd_bind.append(route_id)
    rd_sql = ("SELECT date, route_id, vehicle_id, vehicle_number, "
              "trip_fact, distance_fact, trip_plan, distance_plan, "
              "working_day, trip_passed, trip_approved FROM route_daily"
              + (f" WHERE {' AND '.join(rd_scope)}" if rd_scope else ""))
    rd_rows = storage.query(rd_sql, tuple(rd_bind), limit=50000)
    rd_by_vd = {(str(r.get("date") or ""), str(r.get("vehicle_id") or "")): r
                for r in rd_rows if r.get("vehicle_id")}
    # Bitta (sana, avtobus) kuniga eng ko'p reys bajargan haydovchi — asosiy.
    main_by_vd: dict[tuple, tuple] = {}
    for key, a in agg.items():
        if not key[2]:
            continue
        vd = (key[0], key[2])
        if vd not in main_by_vd or a["cnt"] > agg[main_by_vd[vd]]["cnt"]:
            main_by_vd[vd] = key

    scope_sql = f" AND {' AND '.join(log_scope)}" if log_scope else ""
    scope_params = tuple(log_bind) if log_bind else ()
    # Eski AVTO qatorlar (endi hisobotda yo'q) — o'chiriladi
    avto_rows = storage.query(
        "SELECT date, driver_id, vehicle_id FROM driver_work_logs "
        f"WHERE note = {ph}{scope_sql}",
        ("AVTO",) + scope_params, limit=50000)
    existing_manual = set()
    if agg:
        manual_rows = storage.query(
            "SELECT date, driver_id, vehicle_id FROM driver_work_logs "
            f"WHERE note != {ph}{scope_sql}",
            ("AVTO",) + scope_params, limit=50000)
        existing_manual = {
            (str(r.get("date") or ""), str(r.get("driver_id") or ""),
             str(r.get("vehicle_id") or "")) for r in manual_rows}

    def _km_cnt(key: tuple) -> tuple[float, int]:
        """(date, driver, vehicle) uchun hisob-kitob km/reyslar."""
        vd = (key[0], key[2])
        rd = rd_by_vd.get(vd)
        if rd is not None and main_by_vd.get(vd) == key:
            return float(rd.get("distance_fact") or 0), int(rd.get("trip_fact") or 0)
        a = agg[key]
        return float(a["km"]), int(a["cnt"])

    def _rd_row(key: tuple) -> dict | None:
        """(date, driver, vehicle) asosiy haydovchiga tegishli route_daily satori."""
        vd = (key[0], key[2])
        if rd_by_vd.get(vd) is not None and main_by_vd.get(vd) == key:
            return rd_by_vd[vd]
        return None

    with storage.db.transaction():
        for r in avto_rows:
            key = (str(r.get("date") or ""), str(r.get("driver_id") or ""),
                   str(r.get("vehicle_id") or ""))
            if key not in agg:
                storage.db.execute(
                    f"DELETE FROM driver_work_logs WHERE date = {ph} "
                    f"AND driver_id = {ph} AND vehicle_id = {ph}",
                    key)
                result.deleted += 1
        for key, a in agg.items():
            if key in existing_manual:
                continue  # qo'lda qayd mavjud — AVTO ustiga yozilmaydi
            km, cnt = _km_cnt(key)
            rd = _rd_row(key)
            status = storage.save_driver_work_log(
                key[0], key[1], key[2], km, cnt, note="AVTO",
                distance_plan=float(rd.get("distance_plan") or 0) if rd else 0.0,
                trip_plan=int(rd.get("trip_plan") or 0) if rd else 0,
                working_day=int(rd.get("working_day") or 0) if rd else 0,
                trip_passed=int(rd.get("trip_passed") or 0) if rd else 0,
                trip_approved=int(rd.get("trip_approved") or 0) if rd else 0)
            if status == "inserted":
                result.inserted += 1
            else:
                result.updated += 1
            result.total += 1
    return result


def sync_gross_trips(storage: Storage, client: BMClient, route_id: str,
                     date_str: str) -> SyncResult:
    """Gross trip hisoboti → trips (idempotent)."""
    result = SyncResult(entity="trips")
    repo = GrossRepository(client)

    def _run() -> SyncResult:
        try:
            data = repo.trip(route_id, date_str)
        except Exception as exc:
            if _not_found(exc):
                return result  # o'sha sana uchun trip yo'q — xato emas
            raise
        rows = _waybill_rows(data)
        trip_rows: list[dict] = []
        for row in rows or []:
            result.total += 1
            row = _trim_row(row)
            trip = TripRecord(
                date=_get(row, "date", "dutyDate", "reportDate") or date_str,
                route_id=route_id,
                vehicle_id=_get(row, "vehicleId"),
                driver_id=_get(row, "driverId"),
                planned_time=_get(row, "startTime", "planTime"),
                actual_time=_get(row, "endTime", "actualTime"),
                status=TripStatus.normalize(_get(row, "status", "tripStatus")),
                source=SyncSource.GROSS_TRIP.value,
                data=row,
            )
            if not (trip.driver_id or trip.vehicle_id or trip.planned_time):
                continue
            trip_rows.append(trip.to_row())
        with storage.db.transaction():
            inserted, updated = storage.bulk_upsert(
                "trips", ["date", "route_id", "vehicle_id", "driver_id",
                          "planned_time"],
                trip_rows)
            result.inserted += inserted
            result.updated += updated
        return result

    return _safe_loop(storage, "trips", _run)


# ------------------------------------------------------------ yig'ma sync

def _date_ranges(from_date: str, to_date: str,
                 max_days: int = 30) -> list[tuple[str, str]]:
    """Diapazonni `gross/route` (maks 30 kun) uchun bo'laklarga bo'ladi."""
    frm = _date.fromisoformat(from_date)
    last = _date.fromisoformat(to_date)
    out = []
    while frm <= last:
        end = min(frm + _date_timedelta(max_days - 1), last)
        out.append((frm.isoformat(), end.isoformat()))
        frm = end + _date_timedelta(1)
    return out


def _date_timedelta(days: int) -> timedelta:
    return timedelta(days=days)


def sync_route_daily(storage: Storage, client: BMClient, route_id: str,
                     from_date: str, to_date: str) -> SyncResult:
    """Gross route hisoboti (`brutto-route`) → route_daily (idempotent).

    Saytdagi rasmiy hisob-kitob: har bir (sana, avtobus) uchun
    `distanceFact` (km), `tripFact` (reyslar), `workingDay`. Haydovchi
    oyligi (`driver_work_logs` AVTO) shu km asosida hisoblanadi, shuning
    uchun bu endpoint hisob-kitoblarning yagona manbasi. API maksimal
    30 kunlik diapazon qabul qiladi — kattaroq davr avtomatik bo'linadi.
    """
    result = SyncResult(entity="route_daily")
    repo = GrossRepository(client)

    def _run() -> SyncResult:
        plate_veh = {
            str(v.get("plate_number") or "").strip(): str(v.get("external_id") or "")
            for v in storage.query("SELECT external_id, plate_number FROM vehicles")
        }
        rows: list[dict] = []
        for frm, last in _date_ranges(from_date, to_date):
            try:
                data = repo.route(route_id, frm, last)
            except Exception as exc:
                if _not_found(exc):
                    continue  # o'sha davr uchun hisobot yo'q — xato emas
                raise
            for ds, info in (data.get("dates") or {}).items():
                for v in info.get("vehicles") or []:
                    plate = _get(v, "vehicleNumber", "vehiclePlate")
                    if not ds or not plate:
                        continue
                    result.total += 1
                    rows.append({
                        "date": str(ds),
                        "route_id": str(route_id),
                        "vehicle_id": plate_veh.get(plate, ""),
                        "vehicle_number": str(plate),
                        "vehicle_brand": _get(v, "vehicleBrand"),
                        "shift_name": _get(v, "shiftName"),
                        "working_day": int(_get(v, "workingDay", default="0") or 0),
                        "trip_plan": int(_get(v, "tripPlan", default="0") or 0),
                        "trip_fact": int(_get(v, "tripFact", default="0") or 0),
                        "trip_passed": int(_get(v, "tripFactPassed", default="0") or 0),
                        "trip_approved": int(_get(v, "tripFactApproved", default="0") or 0),
                        "distance_plan": float(_get(v, "distancePlan", default="0") or 0),
                        "distance_fact": float(_get(v, "distanceFact", default="0") or 0),
                        "distance_fact_extra": float(
                            _get(v, "distanceFactExtra", default="0") or 0),
                        "last_synced_at": now_utc(),
                        "data": _json_dumps(v),
                    })
        with storage.db.transaction():
            inserted, updated = storage.bulk_upsert(
                "route_daily", ["date", "route_id", "vehicle_number"], rows)
            result.inserted += inserted
            result.updated += updated
        return result

    return _safe_loop(storage, "route_daily", _run)


def sync_route_day(storage: Storage, client: BMClient, route_id: str,
                   date_str: str) -> list[SyncResult]:
    """Bitta yo'nalish+sana uchun barcha sync'lar (idempotent)."""
    results = [
        sync_drivers(storage, client, route_id, date_str),
        sync_driver_profiles(storage, client, route_id, date_str),
        sync_vehicles(storage, client, route_id, date_str),
        sync_duties(storage, client, route_id, date_str),
        sync_gross_trips(storage, client, route_id, date_str),
        sync_route_daily(storage, client, route_id, date_str, date_str),
    ]
    return results


def sync_all_profiles(storage: Storage, client: BMClient,
                      date_str: str) -> list[SyncResult]:
    """Barcha profillar (kompaniyalar) bo'yicha sync.

    Har profil o'z tokeni bilan ishlaydi: `profileId` bo'lsa
    `login_by_profile` (xotirada), bo'lmasa asosiy token. Bitta kompaniya
    xatosida butun sync to'xtamaydi — xato `SyncResult`'da qayd etiladi.
    """
    from ..core.profiles import all_profiles

    profiles = all_profiles()
    results: list[SyncResult] = []
    if not profiles:
        return [SyncResult(entity="profiles", error="profiles.json bo'sh")]

    main_access = client.access_token
    main_refresh = client.refresh_token

    def use_main() -> None:
        client.access_token = main_access
        client.refresh_token = main_refresh
        client.session.headers["Authorization"] = f"Bearer {main_access}"

    for p in profiles:
        name = str(p.get("name") or "?")
        rid = str(p.get("routeVariantId") or "").strip()
        pid = str(p.get("profileId") or "").strip()
        if not rid:
            results.append(SyncResult(
                entity=f"profiles/{name}", error="routeVariantId ko'rsatilmagan"))
            continue
        try:
            if pid:
                client.login_by_profile(pid)
            else:
                use_main()
        except Exception as exc:  # noqa: BLE001 - token xatosi bir profilni to'xtatadi
            results.append(SyncResult(
                entity=f"profiles/{name}", error=f"token: {exc}"))
            storage.record_error(source="sync.profiles", message=f"{name}: {exc}")
            continue
        try:
            results += sync_route_day(storage, client, rid, date_str)
            results.append(sync_waybills(storage, client, rid, date_str, date_str))
            results.append(sync_work_logs(storage, date_str, date_str, rid))
        except Exception as exc:  # noqa: BLE001 - API xatosi bir profilni to'xtatadi
            results.append(SyncResult(entity=f"profiles/{name}", error=str(exc)))
            storage.record_error(source="sync.profiles", message=f"{name}: {exc}")
    return results


# --------------------------------------------------------- AutomationLogger

class AutomationLogger:
    """Avtomatik vazifalar (daily/schedule/bot) uchun DB jurnali.

    Barcha metodlar best-effort: DB ishlamasa hech qanday xato ko'tarilmaydi.
    """

    def __init__(self, storage: Storage | None = None):
        self.storage = storage if storage is not None else Storage()

    @property
    def enabled(self) -> bool:
        return self.storage.enabled

    def start(self, trigger: str = "auto", sheet_date: str = "",
              month: str = "") -> str:
        run_id = uuid.uuid4().hex[:12]
        self.storage.record_automation_run(
            run_id=run_id, trigger=trigger, started_at=now_utc(),
            status="STARTED", sheet_date=sheet_date, month=month,
        )
        return run_id

    def finish(self, run_id: str, results: list | None = None) -> str:
        results = results or []
        status = "OK" if not self._has_failures(results) else "ERROR"
        self.storage.finish_automation_run(
            run_id, status=status, summary={"results": results},
        )
        return status

    @staticmethod
    def _has_failures(results: list) -> bool:
        for r in results:
            if not isinstance(r, dict):
                continue
            if r.get("error") or r.get("monthly_error"):
                return True
            if r.get("image") and not r.get("sent"):
                return True
            if r.get("monthly_excel") and not r.get("monthly_sent"):
                return True
        return False

    def error(self, source: str, exc: BaseException | str,
              context: dict | None = None) -> None:
        message = str(exc)
        tb = _tb.format_exc() if isinstance(exc, BaseException) else ""
        self.storage.record_error(
            source=source, message=message, traceback=tb, context=context)

    def notification(self, channel: str = "telegram", target: str = "",
                     message: str = "", status: str = "SENT") -> None:
        self.storage.record_notification(
            channel=channel, target=target, message=message, status=status)

    def report_run(self, report_name: str, status: str = "OK",
                   error: str = "", output_file: str = "") -> None:
        self.storage.record_report_run(
            run_id=uuid.uuid4().hex[:12], report_name=report_name,
            started_at=now_utc(), finished_at=now_utc(),
            status=status, error=error, output_file=output_file,
        )


# CLI'da umumiy kirish nuqtasi
def make_syncer(storage: Storage, client: BMClient) -> dict[str, Callable]:
    return {
        "statuses": sync_statuses,
        "profiles": sync_profiles,
        "routes": sync_routes,
        "drivers": sync_drivers,
        "driver_profiles": sync_driver_profiles,
        "vehicles": sync_vehicles,
        "duties": sync_duties,
        "waybills": sync_waybills,
        "work_logs": sync_work_logs,
        "trips": sync_gross_trips,
        "route_daily": sync_route_daily,
    }
