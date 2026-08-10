"""Saqlash (storage) qatlami — idempotent upsert va voqea jurnallari.

`Storage` barcha 14 jadval bilan ishlaydi:

- **Sync jadvallari** (profiles, routes, vehicles, drivers, duties, schedules,
  waybills, trips, reports, trip_statuses) — natural kalit bo'yicha upsert,
  dublikat yaratilmaydi, qayta yuklash mavjud yozuvni buzmaydi.
- **Voqea jurnallari** (report_runs, errors, notifications, automation_runs) —
  append-only.

Agar DB ishlamasa (`get_storage()` `enabled=False`) barcha metodlar no-op
bo'lib, automation normal ishlashda davom etadi.
"""

from __future__ import annotations

from typing import Any

from ..config.settings import db_settings
from ..utils.logger import get_logger
from .base import Database, DatabaseError
from .models import (SyncResult, TripRecord, json_dumps, json_loads,
                     now_utc, params_hash)
from .schema import init_db

log = get_logger("bm_automation.db")


def _safe_default(kind: str) -> Any:
    """No-op holatda qaytariladigan qiymat (DB o'chiq bo'lganda)."""
    if kind == "int":
        return 0
    if kind == "bool":
        return False
    if kind == "list":
        return []
    if kind == "dict":
        return {}
    if kind == "str":
        return ""
    return None


class Storage:
    """Jadvallar bilan idempotent ishlash uchun yagona kirish nuqtasi."""

    def __init__(self, db: Database | None = None, enabled: bool | None = None):
        if db is None:
            db = Database(**db_settings())
        self.db = db
        self.enabled = init_db(db) if enabled is None else (enabled and db.available)
        if not self.enabled:
            log.info("DB rejimi o'chirilgan — barcha DB yozuvlar o'tkazib yuboriladi.")

    # ------------------------------------------------------------ low level

    def _exec(self, sql: str, params: tuple) -> int:
        try:
            return self.db.execute(sql, params)
        except DatabaseError as exc:
            self._warn(exc)
            return 0

    def _warn(self, exc: Exception) -> None:
        if not getattr(self, "_warned", False):
            self._warned = True
            log.warning("DB yozuv xatosi (keyingilari ham o'tkazib yuboriladi): %s", exc)

    def upsert(self, table: str, key_cols: list[str], values: dict) -> str:
        """Bitta yozuvni natural kalit bo'yicha upsert qiladi.

        "inserted" yoki "updated" qaytaradi (SyncResult hisobi uchun).
        """
        if not self.enabled:
            return "updated"
        now = now_utc()
        row = {"created_at": now, "updated_at": now, **values}
        cols = list(row)
        ph = self.db.ph

        ins = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join([ph] * len(cols))}) "
            f"ON CONFLICT ({', '.join(key_cols)}) DO NOTHING"
        )
        if self._exec(ins, tuple(row[c] for c in cols)) == 1:
            return "inserted"

        update_cols = [c for c in cols if c not in key_cols and c != "created_at"]
        if not update_cols:
            return "updated"
        sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        upd = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join([ph] * len(cols))}) "
            f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET {sets}"
        )
        self._exec(upd, tuple(row[c] for c in cols))
        return "updated"

    def insert(self, table: str, values: dict) -> bool:
        """Append-only jadvalga yozuv qo'shadi (voqea jurnallari)."""
        if not self.enabled:
            return False
        now = now_utc()
        row = {"created_at": now, "updated_at": now, **values}
        cols = list(row)
        ph = self.db.ph
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join([ph] * len(cols))})"
        )
        return self._exec(sql, tuple(row[c] for c in cols)) == 1

    def find(self, table: str, **criteria) -> dict | None:
        if not self.enabled:
            return None
        where = " AND ".join(f"{k}={self.db.ph}" for k in criteria)
        rows = self.query(
            f"SELECT * FROM {table} WHERE {where}", tuple(criteria.values()), limit=1)
        return rows[0] if rows else None

    def query(self, sql: str, params: tuple = (), limit: int | None = None) -> list:
        if not self.enabled:
            return []
        try:
            return self.db.query(sql, params, limit=limit)
        except DatabaseError as exc:
            self._warn(exc)
            return []

    # ------------------------------------------------------------ sync tables

    def save_profile(self, external_id: str = "", name: str = "",
                     route_id: str = "", profile_id: str = "",
                     route_name: str = "", start1: str = "", start2: str = "",
                     data: dict | None = None) -> str:
        return self.upsert("profiles", ["name"], {
            "external_id": str(external_id or ""),
            "name": str(name or ""),
            "route_id": str(route_id or ""),
            "profile_id": str(profile_id or ""),
            "route_name": str(route_name or ""),
            "start1": str(start1 or ""),
            "start2": str(start2 or ""),
            "data": json_dumps(data),
        })

    def save_route(self, external_id: str, name: str = "", parent_id: str = "",
                   entity_type: str = "", is_brutto: bool = True,
                   data: dict | None = None) -> str:
        return self.upsert("routes", ["external_id"], {
            "external_id": str(external_id or ""),
            "name": str(name or ""),
            "parent_id": str(parent_id or ""),
            "entity_type": str(entity_type or ""),
            "is_brutto": "t" if is_brutto else "f",
            "data": json_dumps(data),
        })

    def save_vehicle(self, external_id: str, plate_number: str = "",
                     garage_number: str = "", model: str = "",
                     route_id: str = "", data: dict | None = None) -> str:
        return self.upsert("vehicles", ["external_id"], {
            "external_id": str(external_id or ""),
            "plate_number": str(plate_number or ""),
            "garage_number": str(garage_number or ""),
            "model": str(model or ""),
            "route_id": str(route_id or ""),
            "data": json_dumps(data),
        })

    def save_driver(self, external_id: str, full_name: str = "",
                    tin: str = "", route_id: str = "",
                    data: dict | None = None) -> str:
        return self.upsert("drivers", ["external_id"], {
            "external_id": str(external_id or ""),
            "full_name": str(full_name or ""),
            "tin": str(tin or ""),
            "route_id": str(route_id or ""),
            "data": json_dumps(data),
        })

    def save_duty(self, external_id: str, date: str, route_id: str = "",
                  shift_id: str = "", data: dict | None = None) -> str:
        return self.upsert("duties", ["external_id"], {
            "external_id": str(external_id or ""),
            "date": str(date or ""),
            "route_id": str(route_id or ""),
            "shift_id": str(shift_id or ""),
            "data": json_dumps(data),
        })

    def save_schedule(self, date: str, route_id: str, graph_name: str = "",
                      driver_id: str = "", vehicle_id: str = "",
                      shift_name: str = "", start_time: str = "",
                      end_time: str = "", trip_count: int = 0,
                      data: dict | None = None) -> str:
        return self.upsert(
            "schedules",
            ["date", "route_id", "graph_name", "driver_id"],
            {
                "date": str(date or ""),
                "route_id": str(route_id or ""),
                "graph_name": str(graph_name or ""),
                "driver_id": str(driver_id or ""),
                "vehicle_id": str(vehicle_id or ""),
                "shift_name": str(shift_name or ""),
                "start_time": str(start_time or ""),
                "end_time": str(end_time or ""),
                "trip_count": int(trip_count or 0),
                "data": json_dumps(data),
            },
        )

    def save_waybill(self, date: str, route_id: str, plate_number: str,
                     direction: str = "", vehicle_id: str = "",
                     driver_id: str = "", status: str = "",
                     data: dict | None = None) -> str:
        return self.upsert(
            "waybills",
            ["date", "route_id", "plate_number", "direction"],
            {
                "date": str(date or ""),
                "route_id": str(route_id or ""),
                "plate_number": str(plate_number or ""),
                "direction": str(direction or ""),
                "vehicle_id": str(vehicle_id or ""),
                "driver_id": str(driver_id or ""),
                "status": str(status or ""),
                "data": json_dumps(data),
            },
        )

    def save_trip(self, trip: TripRecord) -> str:
        row = trip.to_row()
        return self.upsert(
            "trips",
            ["date", "route_id", "vehicle_id", "driver_id", "planned_time"],
            row,
        )

    def save_report(self, name: str, report_type: str = "",
                    period_date: str = "", params: dict | None = None,
                    file_path: str = "", data: dict | None = None) -> str:
        p = params or {}
        return self.upsert("reports", ["name", "params_hash"], {
            "name": str(name or ""),
            "report_type": str(report_type or ""),
            "period_date": str(period_date or ""),
            "params": json_dumps(p),
            "params_hash": params_hash(p),
            "file_path": str(file_path or ""),
            "data": json_dumps(data),
        })

    # -------------------------------------------------------- event journals

    def record_report_run(self, run_id: str = "", report_name: str = "",
                          started_at: str = "", finished_at: str = "",
                          status: str = "", error: str = "",
                          output_file: str = "") -> bool:
        return self.insert("report_runs", {
            "run_id": str(run_id or ""),
            "report_name": str(report_name or ""),
            "started_at": str(started_at or ""),
            "finished_at": str(finished_at or ""),
            "status": str(status or ""),
            "error": str(error or ""),
            "output_file": str(output_file or ""),
        })

    def record_error(self, source: str = "", message: str = "",
                     traceback: str = "", context: dict | None = None) -> bool:
        return self.insert("errors", {
            "source": str(source or ""),
            "message": str(message or ""),
            "traceback": str(traceback or ""),
            "context": json_dumps(context),
            "occurred_at": now_utc(),
        })

    def record_notification(self, channel: str = "", target: str = "",
                            message: str = "", status: str = "",
                            sent_at: str = "") -> bool:
        return self.insert("notifications", {
            "channel": str(channel or ""),
            "target": str(target or ""),
            "message": str(message or ""),
            "status": str(status or ""),
            "sent_at": str(sent_at or now_utc()),
        })

    def record_automation_run(self, run_id: str, trigger: str = "",
                              started_at: str = "", status: str = "STARTED",
                              summary: dict | None = None,
                              sheet_date: str = "", month: str = "") -> str:
        self.upsert("automation_runs", ["run_id"], {
            "run_id": str(run_id or ""),
            "trigger": str(trigger or ""),
            "started_at": str(started_at or now_utc()),
            "finished_at": "",
            "status": str(status or "STARTED"),
            "summary": json_dumps(summary),
            "sheet_date": str(sheet_date or ""),
            "month": str(month or ""),
        })
        return str(run_id or "")

    def finish_automation_run(self, run_id: str, status: str = "OK",
                              summary: dict | None = None,
                              finished_at: str = "") -> None:
        self._exec(
            f"UPDATE automation_runs SET status={self.db.ph}, "
            f"summary={self.db.ph}, finished_at={self.db.ph}, "
            f"updated_at={self.db.ph} WHERE run_id={self.db.ph}",
            (status, json_dumps(summary), finished_at or now_utc(), now_utc(), run_id),
        )

    # --------------------------------------------------------------- queries

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("profiles", "routes", "vehicles", "drivers", "duties",
                      "schedules", "waybills", "trips", "trip_statuses",
                      "reports", "report_runs", "errors", "notifications",
                      "automation_runs"):
            out[table] = self.query(f"SELECT COUNT(*) AS n FROM {table}", limit=1)[0]["n"] if self.enabled else 0
        return out

    def trips(self, date: str = "", route_id: str = "", status: str = "",
              limit: int = 50) -> list:
        where, params = [], []
        for col, val in (("date", date), ("route_id", route_id), ("status", status)):
            if val:
                where.append(f"{col}={self.db.ph}")
                params.append(val)
        sql = f"SELECT * FROM trips"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY date DESC, planned_time ASC"
        return self.query(sql, tuple(params), limit=limit)

    def list_errors(self, limit: int = 20) -> list:
        return self.query("SELECT * FROM errors ORDER BY occurred_at DESC", limit=limit)

    def list_runs(self, limit: int = 20) -> list:
        return self.query(
            "SELECT * FROM automation_runs ORDER BY started_at DESC", limit=limit)

    def list_reports(self, limit: int = 20) -> list:
        return self.query("SELECT * FROM reports ORDER BY updated_at DESC", limit=limit)


_NULL_STORAGE: Storage | None = None


def get_storage() -> Storage:
    """Yagona (singleton) Storage. DB ishlamasa o'chirilgan Storage qaytaradi."""
    global _NULL_STORAGE
    if _NULL_STORAGE is None:
        _NULL_STORAGE = Storage()
    return _NULL_STORAGE


def reset_storage() -> None:
    """Singleton'ni qayta yaratish (testlar uchun)."""
    global _NULL_STORAGE
    _NULL_STORAGE = None


def storage_for(db: Database) -> Storage:
    """Berilgan Database bilan yangi Storage (testlar / maxsus foydalanish)."""
    return Storage(db=db)
