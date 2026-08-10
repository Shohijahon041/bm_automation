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
from typing import Callable

from ..api.client import BMClient
from ..repositories.driver_repo import DriverRepository
from ..repositories.duty_repo import DutyRepository
from ..repositories.gross_repo import GrossRepository
from ..repositories.route_repo import RouteRepository
from ..repositories.vehicle_repo import VehicleRepository
from ..repositories.waybill_repo import WaybillRepository
from ..utils.logger import get_logger
from .models import SyncResult, SyncSource, TripRecord, TripStatus, now_utc
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
        duty = repo.by_date(route_id, date_str) or {}
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
            for g in graphs:
                key = (date, route_id, _get(g, "graphName"), _get(g, "driverId"))
                if key in seen:
                    continue
                seen.add(key)
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


def sync_waybills(storage: Storage, client: BMClient, route_id: str,
                  from_date: str, to_date: str) -> SyncResult:
    """Waybill hisoboti → waybills + trips (status bilan, idempotent).

    Har bir waybill qatori bitta HAQIQIY qatnov. `plateNumber` → `vehicles`
    jadvali orqali `vehicle_id`ga bog'lanadi; vaqtlar `startTm`/`endTm`.
    `ZERO_MILEAGE` qatnovlarga trips'da yozilmaydi (haqiqiy qatnov emas) —
    faqat waybills jadvalida qayd qilinadi.
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
        with storage.db.transaction():
            for row in rows or []:
                result.total += 1
                date = _get(row, "date", "dutyDate", "reportDate")
                plate = _get(row, "plateNumber", "plateNum", "vehiclePlate")
                status = TripStatus.normalize(
                    _get(row, "status", "tripStatus", "acceptStatus"))
                if not date or not plate:
                    continue
                vehicle_id = _get(row, "vehicleId") or plate_veh.get(plate, "")
                wb = storage.save_waybill(
                    date=date, route_id=route_id, plate_number=plate,
                    direction=_get(row, "direction", "routeDirection"),
                    vehicle_id=vehicle_id,
                    driver_id=_get(row, "driverId"),
                    status=status, data=row,
                )
                if wb == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1

                if status == TripStatus.ZERO_MILEAGE.value:
                    continue

                trip = TripRecord(
                    date=date, route_id=route_id,
                    vehicle_id=vehicle_id,
                    driver_id=_get(row, "driverId"),
                    planned_time=_get(row, "startTm", "startTime",
                                      "planTime", "plannedTime"),
                    actual_time=_get(row, "endTm", "endTime", "actualTime"),
                    status=status,
                    source=SyncSource.WAYBILL.value,
                    data=row,
                )
                result.total += 1
                ts = storage.save_trip(trip)
                if ts == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1
        return result

    return _safe_loop(storage, "waybills", _run)


def sync_gross_trips(storage: Storage, client: BMClient, route_id: str,
                     date_str: str) -> SyncResult:
    """Gross trip hisoboti → trips (idempotent)."""
    result = SyncResult(entity="trips")
    repo = GrossRepository(client)

    def _run() -> SyncResult:
        data = repo.trip(route_id, date_str)
        rows = _waybill_rows(data)
        with storage.db.transaction():
            for row in rows or []:
                result.total += 1
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
                status = storage.save_trip(trip)
                if status == "inserted":
                    result.inserted += 1
                else:
                    result.updated += 1
        return result

    return _safe_loop(storage, "trips", _run)


# ------------------------------------------------------------ yig'ma sync

def sync_route_day(storage: Storage, client: BMClient, route_id: str,
                   date_str: str) -> list[SyncResult]:
    """Bitta yo'nalish+sana uchun barcha sync'lar (idempotent)."""
    results = [
        sync_drivers(storage, client, route_id, date_str),
        sync_vehicles(storage, client, route_id, date_str),
        sync_duties(storage, client, route_id, date_str),
        sync_gross_trips(storage, client, route_id, date_str),
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
        "vehicles": sync_vehicles,
        "duties": sync_duties,
        "waybills": sync_waybills,
        "trips": sync_gross_trips,
    }
