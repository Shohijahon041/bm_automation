"""Saqlash (storage) qatlami вЂ” idempotent upsert va voqea jurnallari.

`Storage` barcha 14 jadval bilan ishlaydi:

- **Sync jadvallari** (profiles, routes, vehicles, drivers, duties, schedules,
  waybills, trips, reports, trip_statuses) вЂ” natural kalit bo'yicha upsert,
  dublikat yaratilmaydi, qayta yuklash mavjud yozuvni buzmaydi.
- **Voqea jurnallari** (report_runs, errors, notifications, automation_runs) вЂ”
  append-only.

Agar DB ishlamasa (`get_storage()` `enabled=False`) barcha metodlar no-op
bo'lib, automation normal ishlashda davom etadi.
"""

from __future__ import annotations

import time
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


_err_dedup: dict[str, float] = {}
_err_dedup_lock = __import__("threading").RLock()


class Storage:
    """Jadvallar bilan idempotent ishlash uchun yagona kirish nuqtasi."""
    def __init__(self, db: Database | None = None, enabled: bool | None = None):
        if db is None:
            db = Database(**db_settings())
        self.db = db
        self._forced_enabled = enabled  # None = auto (init_db + o'z-o'zini tiklash)
        self._schema_ready = False
        if not self.enabled:
            log.info("DB rejimi o'chirilgan вЂ” barcha DB yozuvlar o'tkazib yuboriladi.")

    @property
    def enabled(self) -> bool:
        """DB hozir mavjudmi.

        Dastlabki ulanish muvaffaqiyatsiz bo'lsa ham, har bir tekshiruvda
        qayta urinish (``probe``, backoff) bajariladi вЂ” Supabase qayta tiklangach
        xizmatni qayta ishga tushirmasdan yozish davom etadi. Sxema bir marta
        yaratiladi; DB tiklangach idempotent ``init_db`` qayta bajariladi.
        """
        if self._forced_enabled is not None:
            return self._forced_enabled and self.db.available
        if not self.db.available:
            self.db.probe()
        if not self.db.available:
            return False
        if not self._schema_ready:
            if not init_db(self.db):
                return False
            self._schema_ready = True
        return True

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

    def bulk_upsert(self, table: str, key_cols: list[str],
                    rows: list[dict], chunk: int = 200) -> tuple[int, int]:
        """Ko'p yozuvli upsert вЂ” (inserted, updated) sonini qaytaradi.

        Har bir chunk BIR ko'p-qatorli `INSERT ... ON CONFLICT DO UPDATE`
        bilan bajariladi (PostgreSQL). Row-by-row (`upsert`) bilan 522
        qatorli waybill hisoboti ~1000 ta so'rov talab qilardi вЂ” bu
        Supabase `statement_timeout`'iga uchiradi; batch bilan 3-4 so'rov.
        SQLite (faqat test) row-by-row ishlaydi вЂ” natijalar bir xil.
        """
        if not self.enabled or not rows:
            return 0, 0
        # Bitta INSERT ichida ON CONFLICT dublikat kalitni qabul qilmaydi;
        # row-by-row upsert semantikasi вЂ” oxirgi qiymat yutadi.
        seen = {}
        for r in rows:
            k = tuple(str(r.get(c) or "") for c in key_cols)
            seen[k] = r
        rows = list(seen.values())
        if getattr(self.db, "driver", "") != "postgres":
            inserted = updated = 0
            for r in rows:
                status = self.upsert(table, key_cols, r)
                if status == "inserted":
                    inserted += 1
                else:
                    updated += 1
            return inserted, updated

        now = now_utc()
        cols = ["created_at", "updated_at"] + [
            c for c in rows[0] if c not in ("created_at", "updated_at")
        ]
        col_sql = ", ".join(cols)
        ph = self.db.ph
        ph_sql = ", ".join([ph] * len(cols))
        update_cols = [c for c in cols if c not in key_cols and c != "created_at"]
        sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        inserted = updated = 0
        for start in range(0, len(rows), chunk):
            part = rows[start:start + chunk]
            params = []
            for r in part:
                row = {"created_at": now, "updated_at": now, **r}
                params.extend(row[c] for c in cols)
            tuples = ", ".join(f"({ph_sql})" for _ in part)
            sql = (
                f"INSERT INTO {table} ({col_sql}) VALUES {tuples} "
                f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET {sets} "
                f"RETURNING (xmax = 0) AS is_new"
            )
            for out in self.db.query(sql, tuple(params)):
                if out.get("is_new"):
                    inserted += 1
                else:
                    updated += 1
        return inserted, updated

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

    def save_driver_profile(self, driver_id: str, **fields) -> str:
        """Haydovchining ichki kadr/ish-haqi profilini saqlaydi.

        Faqat ruxsat etilgan ustunlarni qabul qilamiz. Bu API payload'i SQL
        ustun nomini tanlashiga yo'l qo'ymaydi va BM sinxron ma'lumotidan
        alohida ishlaydi.
        """
        allowed = {
            "phone", "passport_number", "passport_issued_by", "passport_expiry",
            "license_number", "license_category", "license_expiry",
            "passport_front_path", "passport_back_path", "license_front_path",
            "license_back_path", "photo_path", "rating", "blacklisted",
            "blacklist_reason", "km_rate", "notification_enabled",
            "notification_target", "notes", "data",
        }
        values = {"driver_id": str(driver_id or "")}
        for key in allowed:
            if key not in fields:
                continue
            value = fields[key]
            if key in {"rating", "km_rate"}:
                try:
                    values[key] = float(value or 0)
                except (TypeError, ValueError):
                    values[key] = 0.0
            elif key in {"blacklisted", "notification_enabled"}:
                values[key] = 1 if value else 0
            elif key == "data":
                values[key] = json_dumps(value)
            else:
                values[key] = str(value or "")
        existing = self.find("driver_profiles", driver_id=str(driver_id or "")) or {}
        defaults = {
            "phone": "", "passport_number": "", "passport_issued_by": "",
            "passport_expiry": "", "license_number": "", "license_category": "",
            "license_expiry": "", "passport_front_path": "", "passport_back_path": "",
            "license_front_path": "", "license_back_path": "", "photo_path": "",
            "rating": 5.0,
            "blacklisted": 0, "blacklist_reason": "", "km_rate": 0.0,
            "notification_enabled": 0, "notification_target": "",
            "telegram_chat_id": "", "notes": "", "data": "{}",
        }
        for key, default in defaults.items():
            values.setdefault(key, existing.get(key, default))
        return self.upsert("driver_profiles", ["driver_id"], values)

    def find_driver_by_telegram(self, chat_id: int) -> dict | None:
        """Telegram chat_id bo'yicha haydovchi profilini qaytaradi."""
        if not self.enabled:
            return None
        try:
            rows = self.query(
                "SELECT * FROM driver_profiles WHERE telegram_chat_id = "
                f"{self.db.ph}", (str(chat_id),), limit=1)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_driver_by_notification(self, phone_or_id: str) -> dict | None:
        """notification_target bo'yicha haydovchi profilini qaytaradi."""
        if not self.enabled or not phone_or_id:
            return None
        try:
            rows = self.query(
                "SELECT * FROM driver_profiles WHERE notification_target = "
                f"{self.db.ph}", (str(phone_or_id),), limit=1)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_driver_by_jshshr(self, jshshr: str) -> dict | None:
        """JSHSHR (TIN/PINFL) bo'yicha haydovchini qaytaradi."""
        if not self.enabled or not jshshr:
            return None
        try:
            rows = self.query(
                "SELECT * FROM drivers WHERE tin = "
                f"{self.db.ph}", (str(jshshr).strip(),), limit=1)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_driver_by_license(self, license_number: str) -> dict | None:
        """Haydovchilik guvohnomasi raqami bo'yicha qidiradi."""
        if not self.enabled or not license_number:
            return None
        try:
            rows = self.query(
                "SELECT dp.* FROM driver_profiles dp "
                "WHERE dp.license_number = "
                f"{self.db.ph}", (str(license_number).strip(),), limit=1)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_driver_by_passport(self, passport_number: str) -> dict | None:
        """Pasport raqami bo'yicha qidiradi."""
        if not self.enabled or not passport_number:
            return None
        try:
            rows = self.query(
                "SELECT dp.* FROM driver_profiles dp "
                "WHERE dp.passport_number = "
                f"{self.db.ph}", (str(passport_number).strip(),), limit=1)
            return rows[0] if rows else None
        except Exception:  # noqa: BLE001
            pass
        return None

    def find_driver_by_phone(self, phone: str) -> dict | None:
        """Telefon raqami bo'yicha qidiradi (notification_target yoki phone)."""
        if not self.enabled or not phone:
            return None
        clean = str(phone).strip().lstrip("+")
        for variant in (clean, f"998{clean}" if len(clean) == 9 else "",
                        clean[-9:] if len(clean) > 9 else ""):
            if not variant:
                continue
            try:
                rows = self.query(
                    "SELECT * FROM driver_profiles "
                    "WHERE notification_target = " + self.db.ph +
                    " OR phone = " + self.db.ph,
                    (variant, variant), limit=1)
                if rows:
                    return rows[0]
            except Exception:  # noqa: BLE001
                pass
        return None

    def resolve_driver_by_identifier(self, identifier: str) -> str:
        """Birlashtirilgan haydovchi qidiruv: JSHSHR в†’ Guvohnoma в†’ Pasport в†’ Tel.

        Muvaffaqiyatli topilgan driver_id qaytaradi, topilmasa bo'sh qator.
        Qidiruv tartibi: JSHSHR (1-o'rin) в†’ Haydovchilik guvohnomasi (2)
        в†’ Pasport (3) в†’ Telefon raqami (4).
        """
        identifier = (identifier or "").strip()
        if not identifier:
            return ""
        clean = identifier.replace(" ", "").replace("-", "")
        # 1. JSHSHR (faqat raqam, 14 ta)
        if clean.isdigit() and len(clean) >= 10:
            row = self.find_driver_by_jshshr(clean)
            if row:
                return str(row.get("external_id") or "")
        # 2. Haydovchilik guvohnomasi
        row = self.find_driver_by_license(identifier)
        if row:
            return str(row.get("driver_id") or "")
        # 3. Pasport
        row = self.find_driver_by_passport(identifier)
        if row:
            return str(row.get("driver_id") or "")
        # 4. Telefon raqami
        row = self.find_driver_by_phone(identifier)
        if row:
            return str(row.get("driver_id") or "")
        return ""

    def resolve_driver_profile(self, identifier: str) -> dict | None:
        """Birlashtirilgan qidiruv вЂ” to'liq profil qaytaradi (driver_id + profile)."""
        identifier = (identifier or "").strip()
        if not identifier:
            return None
        clean = identifier.replace(" ", "").replace("-", "")
        # 1. JSHSHR
        if clean.isdigit() and len(clean) >= 10:
            row = self.find_driver_by_jshshr(clean)
            if row:
                did = str(row.get("external_id") or "")
                profile = self.find("driver_profiles", driver_id=did) or {}
                return {"driver_id": did, "full_name": row.get("full_name", ""),
                        "source": "jshshr", **profile}
        # 2. Guvohnoma
        row = self.find_driver_by_license(identifier)
        if row:
            did = str(row.get("driver_id") or "")
            d = self.find("drivers", external_id=did) or {}
            return {"driver_id": did, "full_name": d.get("full_name", ""),
                    "source": "guvohnoma", **row}
        # 3. Pasport
        row = self.find_driver_by_passport(identifier)
        if row:
            did = str(row.get("driver_id") or "")
            d = self.find("drivers", external_id=did) or {}
            return {"driver_id": did, "full_name": d.get("full_name", ""),
                    "source": "pasport", **row}
        # 4. Telefon
        row = self.find_driver_by_phone(identifier)
        if row:
            did = str(row.get("driver_id") or "")
            d = self.find("drivers", external_id=did) or {}
            return {"driver_id": did, "full_name": d.get("full_name", ""),
                    "source": "telefon", **row}
        return None

    def link_driver_telegram(self, driver_id: str, chat_id: int) -> None:
        """Haydovchiga Telegram chat_id bog'laydi + bildirishnomani yoqadi."""
        if not self.enabled:
            return
        try:
            self._exec(
                "UPDATE driver_profiles SET telegram_chat_id = "
                f"{self.db.ph}, notification_enabled = 1 "
                "WHERE driver_id = " f"{self.db.ph}",
                (str(chat_id), driver_id),
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------- dispatcher routes

    def assign_route_to_dispatcher(self, chat_id: int, route_id: str,
                                   route_name: str = "", company: str = "",
                                   phone: str = "") -> str:
        """Dispetcherga bir yo'nalish biriktiradi (idempotent upsert)."""
        return self.upsert("dispatcher_routes",
                           ["dispatcher_chat_id", "route_id"], {
            "dispatcher_chat_id": str(chat_id),
            "route_id": str(route_id or ""),
            "route_name": str(route_name or ""),
            "company": str(company or ""),
            "phone": str(phone or ""),
            "data": "{}",
        })

    def unassign_route_from_dispatcher(self, chat_id: int, route_id: str) -> bool:
        """Dispetcherdan bitta yo'nalishni olib tashlaydi."""
        if not self.enabled:
            return False
        try:
            return self.db.execute(
                "DELETE FROM dispatcher_routes WHERE dispatcher_chat_id = "
                f"{self.db.ph} AND route_id = {self.db.ph}",
                (str(chat_id), str(route_id)),
            ) > 0
        except Exception:  # noqa: BLE001
            return False

    def set_dispatcher_routes(self, chat_id: int, routes: list[dict]) -> list[dict]:
        """Dispetcher yo'nalishlari to'plamini almashtiradi (avvalgisini o'chiradi).

        `routes` вЂ” [{"route_id": ..., "route_name": ..., "company": ...}, ...].
        Qaytadi: yangi biriktirilgan ro'yxat.
        """
        if not self.enabled:
            return routes
        chat = str(chat_id)
        try:
            with self.db.transaction():
                self.db.execute(
                    "DELETE FROM dispatcher_routes WHERE dispatcher_chat_id = "
                    f"{self.db.ph}", (chat,))
                for r in routes or []:
                    self.assign_route_to_dispatcher(
                        chat_id,
                        str(r.get("route_id") or ""),
                        str(r.get("route_name") or ""),
                        str(r.get("company") or ""),
                        str(r.get("phone") or ""),
                    )
        except Exception:  # noqa: BLE001
            pass
        return routes

    def dispatcher_routes(self, chat_id: int | None = None) -> list[dict]:
        """Dispetcher yo'nalishlari ro'yxati. chat_id berilsa вЂ” bitta
        dispetcher uchun, aks holda hammasi."""
        if not self.enabled:
            return []
        try:
            if chat_id is not None:
                rows = self.query(
                    "SELECT route_id, route_name, company, phone "
                    "FROM dispatcher_routes "
                    "WHERE dispatcher_chat_id = " + self.db.ph +
                    " ORDER BY route_name", (str(chat_id),))
            else:
                rows = self.query(
                    "SELECT dispatcher_chat_id, route_id, route_name, "
                    "company, phone "
                    "FROM dispatcher_routes "
                    "ORDER BY dispatcher_chat_id, route_name")
            return rows
        except Exception:  # noqa: BLE001
            return []

    def has_dispatcher_routes(self, chat_id: int) -> bool:
        """chat_id ga yo'nalish biriktirilganmi (dashboard'dan)."""
        if not self.enabled:
            return False
        try:
            rows = self.query(
                "SELECT 1 AS ok FROM dispatcher_routes WHERE dispatcher_chat_id = "
                f"{self.db.ph} LIMIT 1", (str(chat_id),))
            return bool(rows)
        except Exception:  # noqa: BLE001
            return False

    def dispatcher_phone(self, route_id: str) -> str:
        """Yo'nalishga biriktirilgan dispetcherning telefoni (SMS uchun)."""
        if not self.enabled:
            return ""
        try:
            rows = self.query(
                "SELECT phone FROM dispatcher_routes WHERE route_id = "
                f"{self.db.ph} AND phone != '' "
                "ORDER BY dispatcher_chat_id LIMIT 1", (str(route_id),))
            return str((rows[0] or {}).get("phone") or "") if rows else ""
        except Exception:  # noqa: BLE001
            return ""

    def save_driver_work_log(self, date: str, driver_id: str, vehicle_id: str = "",
                             distance_km: float = 0, trip_count: int = 0,
                             note: str = "", distance_plan: float = 0,
                             trip_plan: int = 0, working_day: int = 0,
                             trip_passed: int = 0, trip_approved: int = 0) -> str:
        try:
            distance = max(float(distance_km or 0), 0.0)
        except (TypeError, ValueError):
            distance = 0.0
        try:
            trips = max(int(trip_count or 0), 0)
        except (TypeError, ValueError):
            trips = 0
        try:
            d_plan = max(float(distance_plan or 0), 0.0)
        except (TypeError, ValueError):
            d_plan = 0.0
        try:
            t_plan = max(int(trip_plan or 0), 0)
        except (TypeError, ValueError):
            t_plan = 0
        try:
            wd = max(int(working_day or 0), 0)
        except (TypeError, ValueError):
            wd = 0
        try:
            passed = max(int(trip_passed or 0), 0)
        except (TypeError, ValueError):
            passed = 0
        try:
            approved = max(int(trip_approved or 0), 0)
        except (TypeError, ValueError):
            approved = 0
        return self.upsert("driver_work_logs", ["date", "driver_id", "vehicle_id"], {
            "date": str(date or ""), "driver_id": str(driver_id or ""),
            "vehicle_id": str(vehicle_id or ""), "distance_km": distance,
            "trip_count": trips, "note": str(note or ""),
            "distance_plan": d_plan, "trip_plan": t_plan,
            "working_day": wd, "trip_passed": passed, "trip_approved": approved,
        })

    def delete_driver_work_log(self, date: str, driver_id: str,
                               vehicle_id: str = "") -> bool:
        """Kunlik km qaydini o'chiradi (noto'g'ri kiritilgan bo'lsa).

        Natural kalit: (date, driver_id, vehicle_id) вЂ” shu kalitdagi
        yagona qayd o'chiriladi."""
        if not self.enabled:
            return False
        return self._exec(
            "DELETE FROM driver_work_logs WHERE date = " + self.db.ph
            + " AND driver_id = " + self.db.ph
            + " AND vehicle_id = " + self.db.ph,
            (str(date or ""), str(driver_id or ""), str(vehicle_id or "")),
        ) == 1

    def add_driver_fine(self, driver_id: str, date: str, amount: float,
                        reason: str = "", status: str = "ACTIVE") -> bool:
        try:
            value = max(float(amount or 0), 0.0)
        except (TypeError, ValueError):
            value = 0.0
        return self.insert("driver_fines", {
            "driver_id": str(driver_id or ""), "date": str(date or ""),
            "amount": value, "reason": str(reason or ""),
            "status": str(status or "ACTIVE").upper(),
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

    def delete_missing(self, table: str, key_cols: list[str],
                       route_id: str, from_date: str, to_date: str,
                       keep: set, extra_cond: str = "",
                       extra_params: tuple = ()) -> int:
        """Replace-sync uchun: diapazondagi `keep`'da yo'q yozuvlarni o'chiradi.

        Faqat shu (route_id, date) oralig'idagi qatorlar tekshiriladi;
        `key_cols` to'liq natural kalitni beradi (date/route_id kiradi).
        `extra_cond` (masalan `source='WAYBILL'`) o'chirishni toraytiradi.
        """
        if not self.enabled:
            return 0
        ph = self.db.ph
        extra = f" AND {extra_cond}" if extra_cond else ""
        rows = self.query(
            f"SELECT * FROM {table} WHERE route_id={ph} "
            f"AND date >= {ph} AND date <= {ph}{extra}",
            (route_id, from_date, to_date) + tuple(extra_params),
        )
        doomed = [
            r for r in rows
            if tuple(str(r.get(c) or "") for c in key_cols) not in keep
        ]
        if not doomed:
            return 0
        if getattr(self.db, "driver", "") != "postgres":
            # SQLite (faqat test): qator-qator o'chirish.
            deleted = 0
            for r in doomed:
                cond = " AND ".join(f"{c}={ph}" for c in key_cols)
                self._exec(
                    f"DELETE FROM {table} WHERE {cond}",
                    tuple(str(r.get(c) or "") for c in key_cols),
                )
                deleted += 1
            return deleted
        # PostgreSQL: BIR ko'p-kalitli DELETE (unnest orqali) вЂ” 522 kalit
        # uchun 522 so'rov o'rniga 1 so'rov, statement timeout bo'lmaydi.
        # Har bir ustun o'z text[] array'ida bitta parametr sifatida uzatiladi;
        # unnest() satrlarni ustunlar bo'yicha biriktirib qaytaradi.
        by_col = [[str(r.get(c) or "") for r in doomed] for c in key_cols]
        arrays = ", ".join(f"{ph}::text[]" for _ in key_cols)
        key_sql = ", ".join(key_cols)
        sql = (
            f"DELETE FROM {table} WHERE ({key_sql}) IN "
            f"(SELECT * FROM unnest({arrays}))"
        )
        return max(self._exec(sql, tuple(by_col)), 0)

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

    # Bir xil xato (source+message) shu vaqt ichida takrorlanmasin.
    # Monitor/error-markazini "flood" qilmaslik uchun (mavjud xato qayta
    # qayd etilmaydi вЂ” yangi xatolar esa darhol ko'rinadi).
    # 0 bo'lsa dedup o'chirilgan.
    error_dedup_minutes: int = 30

    def record_error(self, source: str = "", message: str = "",
                     traceback: str = "", context: dict | None = None) -> bool:
        source = str(source or "")
        message = str(message or "")
        window = self.error_dedup_minutes
        if window and window > 0:
            key = f"{source}:::{message}"
            now_ts = time.time()
            with _err_dedup_lock:
                ts = _err_dedup.get(key)
                if ts is not None and (now_ts - ts) < window * 60:
                    return False  # yaqinda bir xil xato qayd etilgan вЂ” skip
                _err_dedup[key] = now_ts
                if len(_err_dedup) > 10000:  # eskirgan yozuvlarni tozalash
                    cutoff = now_ts - 60 * 60 * 6
                    for k in [k for k, v in _err_dedup.items() if v < cutoff]:
                        _err_dedup.pop(k, None)
        return self.insert("errors", {
            "source": source,
            "message": message,
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

    def record_sms(self, driver_id: str = "", name: str = "",
                   phone: str = "", route_id: str = "", route_name: str = "",
                   schedule_date: str = "", message: str = "",
                   status: str = "PENDING", message_id: str = "",
                   error: str = "", send_at: str = "") -> bool:
        """Haydovchiga yuborilgan SMS natijasini `sms_log`ga yozadi."""
        return self.insert("sms_log", {
            "driver_id": str(driver_id or ""),
            "name": str(name or ""),
            "phone": str(phone or ""),
            "route_id": str(route_id or ""),
            "route_name": str(route_name or ""),
            "schedule_date": str(schedule_date or ""),
            "message": str(message or ""),
            "status": str(status or "PENDING"),
            "message_id": str(message_id or ""),
            "error": str(error or ""),
            "send_at": str(send_at or now_utc()),
        })

    def sms_log(self, limit: int = 200, status: str = "") -> list:
        """SMS jurnalini oxirgi yozuvdan boshlab qaytaradi."""
        if not self.enabled:
            return []
        where = ""
        params: tuple = ()
        if status:
            where = f" WHERE status = {self.db.ph}"
            params = (status,)
        return self.query(
            f"SELECT * FROM sms_log{where} "
            f"ORDER BY id DESC LIMIT {max(int(limit or 200), 1)}",
            params,
        )

    def sms_log_counts(self) -> dict:
        """SMS statuslari bo'yicha umumiy hisob (dashboard statistikasi)."""
        if not self.enabled:
            return {"delivered": 0, "sent": 0, "failed": 0,
                    "pending": 0, "total": 0}
        rows = self.query(
            "SELECT status, COUNT(*) AS n FROM sms_log GROUP BY status")
        counts: dict[str, int] = {}
        total = 0
        for r in rows:
            st = (r.get("status") or "UNKNOWN").upper()
            counts[st] = int(r.get("n") or 0)
            total += counts[st]
        delivered = counts.get("DELIVERED", 0)
        sent = counts.get("SENT", 0) + counts.get("PROCESSED", 0)
        return {"delivered": delivered, "sent": sent,
                "failed": counts.get("FAILED", 0),
                "pending": counts.get("PENDING", 0), "total": total}

    def sms_route_enabled(self, route_id: str) -> bool:
        """Yo'nalish uchun SMS faolmi (standart: faol)."""
        if not self.enabled:
            return True
        row = self.find("sms_route_flags", route_id=str(route_id or ""))
        if not row:
            return True
        return int(row.get("enabled") or 0) != 0

    def sms_route_list(self) -> list:
        """Barcha markalangan yo'nalishlar + faollik holati (dashboard)."""
        if not self.enabled:
            return []
        return self.query("SELECT route_id, enabled, updated_at "
                          "FROM sms_route_flags ORDER BY route_id")

    def sms_route_set(self, route_id: str, enabled: bool) -> bool:
        """Yo'nalish SMS flagini o'rnatadi (idempotent upsert)."""
        if not self.enabled:
            return False
        self.upsert("sms_route_flags", ["route_id"], {
            "route_id": str(route_id or ""),
            "enabled": 1 if enabled else 0,
        })
        return True

    def sms_route_set_all(self, enabled: bool) -> int:
        """Barcha markalangan yo'nalishlarni bitta holatga o'rnatadi."""
        if not self.enabled:
            return 0
        now = now_utc()
        val = 1 if enabled else 0
        return self._exec(
            f"UPDATE sms_route_flags SET enabled = {self.db.ph}, "
            f"updated_at = {self.db.ph} WHERE enabled <> {self.db.ph}",
            (val, now, val),
        )

    # ------------------------------------------------------ avans (ma'muriy)

    def record_avans(self, driver_id: str = "", name: str = "",
                     route_id: str = "", route_name: str = "",
                     amount: float = 0, pay_date: str = "",
                     note: str = "") -> int:
        """Yangi avans to'lovini yozadi; yangi id qaytaradi (0 = xato)."""
        if not self.enabled:
            return 0
        row = {
            "driver_id": str(driver_id or ""),
            "name": str(name or ""),
            "route_id": str(route_id or ""),
            "route_name": str(route_name or ""),
            "amount": round(float(amount or 0), 2),
            "pay_date": str(pay_date or ""),
            "note": str(note or ""),
        }
        if self.insert("avans", row):
            rows = self.query("SELECT id FROM avans ORDER BY id DESC LIMIT 1")
            if rows:
                return int(rows[0]["id"])
        return 0

    def avans_list(self, driver_id: str = "", route_id: str = "",
                   month: str = "", limit: int = 500) -> list:
        """Avans to'lovlari ro'yxati (eng oxirgisi birinchi)."""
        if not self.enabled:
            return []
        where = []
        params: list = []
        if driver_id:
            where.append(f"driver_id = {self.db.ph}")
            params.append(driver_id)
        if route_id:
            where.append(f"route_id = {self.db.ph}")
            params.append(route_id)
        if month:  # YYYY-MM
            where.append(f"LEFT(pay_date, 7) = {self.db.ph}")
            params.append(month)
        sql = ("SELECT * FROM avans"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY pay_date DESC, id DESC LIMIT "
               + str(max(int(limit or 500), 1)))
        return self.query(sql, tuple(params))

    def avans_total(self, driver_id: str = "", route_id: str = "",
                    month: str = "") -> float:
        """Berilgan filtr bo'yicha avans summasi yig'indisi."""
        if not self.enabled:
            return 0.0
        where = []
        params: list = []
        if driver_id:
            where.append(f"driver_id = {self.db.ph}")
            params.append(driver_id)
        if route_id:
            where.append(f"route_id = {self.db.ph}")
            params.append(route_id)
        if month:
            where.append(f"LEFT(pay_date, 7) = {self.db.ph}")
            params.append(month)
        sql = ("SELECT COALESCE(SUM(amount), 0) AS s FROM avans"
               + (" WHERE " + " AND ".join(where) if where else ""))
        rows = self.query(sql, tuple(params))
        return float((rows[0] or {}).get("s") or 0)

    def avans_by_driver(self, month: str = "") -> dict:
        """Har bir haydovchiga berilgan avans summasini (driver_id -> summa)."""
        if not self.enabled:
            return {}
        where = []
        params: list = []
        if month:
            where.append(f"LEFT(pay_date, 7) = {self.db.ph}")
            params.append(month)
        sql = ("SELECT driver_id, COALESCE(SUM(amount), 0) AS s FROM avans"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " GROUP BY driver_id")
        return {str(r.get("driver_id") or ""): float(r.get("s") or 0)
                for r in self.query(sql, tuple(params))}

    def fines_list(self, driver_id: str = "", month: str = "",
                   limit: int = 500) -> list:
        """Haydovchi jarimalari ro'yxati (eng oxirgisi birinchi)."""
        if not self.enabled:
            return []
        where = []
        params: list = []
        if driver_id:
            where.append(f"driver_id = {self.db.ph}")
            params.append(driver_id)
        if month:  # YYYY-MM
            where.append(f"substr(date, 1, 7) = {self.db.ph}")
            params.append(month)
        sql = ("SELECT * FROM driver_fines"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY date DESC, id DESC LIMIT "
               + str(max(int(limit or 500), 1)))
        return self.query(sql, tuple(params))

    def fines_total(self, driver_id: str = "", month: str = "") -> float:
        """Berilgan filtr bo'yicha jarima summasi yig'indisi."""
        if not self.enabled:
            return 0.0
        where = []
        params: list = []
        if driver_id:
            where.append(f"driver_id = {self.db.ph}")
            params.append(driver_id)
        if month:
            where.append(f"substr(date, 1, 7) = {self.db.ph}")
            params.append(month)
        sql = ("SELECT COALESCE(SUM(amount), 0) AS s FROM driver_fines"
               + (" WHERE " + " AND ".join(where) if where else ""))
        rows = self.query(sql, tuple(params))
        return float((rows[0] or {}).get("s") or 0)

    def delete_avans(self, row_id: int) -> bool:
        """Avans yozuvini o'chiradi (noto'g'ri kiritilgan bo'lsa)."""
        if not self.enabled:
            return False
        return self._exec(
            f"DELETE FROM avans WHERE id = {self.db.ph}", (int(row_id),)
        ) == 1

    def delete_fine(self, row_id: int) -> bool:
        """Haydovchi jirimasi yozuvini o'chiradi (noto'g'ri kiritilsa)."""
        if not self.enabled:
            return False
        return self._exec(
            f"DELETE FROM driver_fines WHERE id = {self.db.ph}", (int(row_id),)
        ) == 1

    # --------------------------------------------------------- staff (xodimlar)

    def staff_add(self, name: str, position: str = "", company: str = "",
                  salary_type: str = "oylik", rate: float = 0,
                  days: int = 0, note: str = "") -> int:
        """Ma'muriy bo'lim ishchisini qo'shadi; yangi id qaytaradi (0 = xato)."""
        if not self.enabled:
            return 0
        row = {
            "name": str(name or "").strip() or None,
            "position": str(position or "").strip(),
            "company": str(company or "").strip(),
            "salary_type": str(salary_type or "oylik").strip(),
            "rate": round(float(rate or 0), 2),
            "days": max(int(days or 0), 0),
            "note": str(note or "").strip(),
        }
        if row.get("name") is None:
            return 0
        if self.insert("staff", row):
            rows = self.query("SELECT id FROM staff ORDER BY id DESC LIMIT 1")
            if rows:
                return int(rows[0]["id"])
        return 0

    def staff_update(self, row_id: int, name: str = "", position: str = "",
                     company: str = "", salary_type: str = "oylik",
                     rate: float = 0, days: int = 0, note: str = "") -> bool:
        """Mavjud xodim ma'lumotlarini yangilaydi."""
        if not self.enabled:
            return False
        sets = []
        params: list = []
        if name is not None:
            nm = str(name or "").strip()
            if nm:
                sets.append(f"name = {self.db.ph}")
                params.append(nm)
        if position is not None:
            sets.append(f"position = {self.db.ph}")
            params.append(str(position or "").strip())
        if company is not None:
            sets.append(f"company = {self.db.ph}")
            params.append(str(company or "").strip())
        if salary_type is not None:
            sets.append(f"salary_type = {self.db.ph}")
            params.append(str(salary_type or "oylik").strip())
        if rate is not None:
            sets.append(f"rate = {self.db.ph}")
            params.append(round(float(rate or 0), 2))
        if days is not None:
            sets.append(f"days = {self.db.ph}")
            params.append(max(int(days or 0), 0))
        if note is not None:
            sets.append(f"note = {self.db.ph}")
            params.append(str(note or "").strip())
        if not sets:
            return False
        sets.append(f"updated_at = {self.db.ph}")
        params.append(now_utc())
        params.append(int(row_id))
        return self._exec(
            f"UPDATE staff SET {', '.join(sets)} WHERE id = {self.db.ph}",
            tuple(params),
        ) == 1

    def staff_list(self, company: str = "", position: str = "") -> list:
        """Xodimlar ro'yxati (nomi bo'yicha tartiblangan)."""
        if not self.enabled:
            return []
        where = []
        params: list = []
        if company:
            where.append(f"company = {self.db.ph}")
            params.append(company)
        if position:
            where.append(f"position = {self.db.ph}")
            params.append(position)
        sql = ("SELECT * FROM staff"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY company, position, name")
        return self.query(sql, tuple(params))

    def delete_staff(self, row_id: int) -> bool:
        """Xodim yozuvini o'chiradi."""
        if not self.enabled:
            return False
        return self._exec(
            f"DELETE FROM staff WHERE id = {self.db.ph}", (int(row_id),)
        ) == 1

    def update_sms_status(self, row_id: int, status: str = "",
                          message_id: str = "", error: str = "") -> bool:
        """Mavjud `sms_log` yozuvining holatini yangilaydi (retry uchun)."""
        if not self.enabled:
            return False
        now = now_utc()
        return self._exec(
            f"UPDATE sms_log SET status = {self.db.ph}, "
            f"message_id = {self.db.ph}, error = {self.db.ph}, "
            f"send_at = {self.db.ph}, updated_at = {self.db.ph} "
            f"WHERE id = {self.db.ph}",
            (str(status or ""), str(message_id or ""), str(error or ""),
             now, now, int(row_id)),
        ) == 1

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
_STORAGE_LOCK = __import__("threading").Lock()


def get_storage() -> Storage:
    """Yagona (singleton) Storage. DB ishlamasa o'chirilgan Storage qaytaradi."""
    global _NULL_STORAGE
    if _NULL_STORAGE is not None:
        return _NULL_STORAGE
    with _STORAGE_LOCK:
        if _NULL_STORAGE is None:
            _NULL_STORAGE = Storage()
        return _NULL_STORAGE


def reset_storage() -> None:
    """Singleton'ni qayta yaratish (testlar uchun)."""
    global _NULL_STORAGE
    with _STORAGE_LOCK:
        _NULL_STORAGE = None


def storage_for(db: Database) -> Storage:
    """Berilgan Database bilan yangi Storage (testlar / maxsus foydalanish)."""
    return Storage(db=db)
