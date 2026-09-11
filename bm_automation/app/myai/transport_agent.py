"""Transport Agent — ma'lumotlarni normalizatsiya qiladi."""

from __future__ import annotations

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.transport_agent")


class TransportAgent(BaseAgent):
    """Transport Agent — xom ma'lumotlarni tushunarli formatga o'giradi."""

    name = AgentType.TRANSPORT
    description = "Ma'lumotlarni normalizatsiya qiladi"

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            action = context.params.get("action", "normalize")
            prev = context.previous_results
            raw_data = (
                prev.get("browser") or prev.get("driver")
                or prev.get("schedule") or prev.get("attendance") or {}
            )

            if action == "normalize":
                result = await self._normalize(raw_data)
            elif action == "extract_vehicles":
                result = await self._extract_vehicles(raw_data)
            else:
                result = raw_data

            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:  # noqa: BLE001
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    async def _normalize(self, raw_data: dict) -> dict:
        """Xom ma'lumotlarni normalizatsiya qilish."""
        # Check if data comes from DB fallback
        method = ""
        if isinstance(raw_data, dict):
            method = raw_data.get("method", "")
            if isinstance(raw_data.get("data"), dict) and "data" in raw_data:
                raw_data = raw_data["data"]

        # Driver agent payload — haydovchi ma'lumotlari
        if isinstance(raw_data, dict) and self._looks_like_driver(raw_data):
            return await self._normalize_driver(raw_data)

        if method == "db" or self._looks_like_db(raw_data):
            return await self._normalize_db(raw_data)

        # Attendance agent payload
        if isinstance(raw_data, dict) and "attendance_rate" in raw_data:
            return await self._normalize_attendance(raw_data)

        # API format: data.dates.vehicles
        data = raw_data.get("data", raw_data) if isinstance(raw_data, dict) else raw_data
        vehicles = []
        if isinstance(data, dict) and "dates" in data:
            for date_key, date_info in data["dates"].items():
                for v in date_info.get("vehicles", []):
                    vehicles.append({
                        "bus_number": v.get("vehicleNumber", ""),
                        "route": v.get("routeName", ""),
                        "driver": v.get("driverName", ""),
                        "shift": v.get("shiftName", ""),
                        "status": "active" if v.get("tripFact", 0) > 0 else "inactive",
                        "trips": v.get("tripFact", 0),
                        "km": v.get("distanceFact", 0),
                        "working_day": v.get("workingDay", 0),
                    })
        elif isinstance(data, dict) and "vehicles" in data:
            # Already has vehicles (from DB)
            vehicles = data["vehicles"]
        elif isinstance(data, dict) and "summary" in data:
            # DB route_summary format
            return await self._normalize_db(data)

        total = len(vehicles)
        active = sum(1 for v in vehicles if v["status"] == "active")

        return {
            "vehicles": vehicles,
            "summary": {
                "total_buses": total,
                "active_buses": active,
                "inactive_buses": total - active,
                "total_km": round(sum(v["km"] for v in vehicles), 2),
                "total_trips": sum(v["trips"] for v in vehicles),
                "units": "buses",
            },
        }

    @staticmethod
    def _looks_like_db(data: dict) -> bool:
        """DB summary strukturasini aniqlash."""
        if not isinstance(data, dict):
            return False
        if "routes" in data or "vehicles" in data:
            return True
        s = data.get("summary", {})
        return isinstance(s, dict) and (
            "total_buses" in s or "accepted" in s or "count" in s
        )

    @staticmethod
    def _looks_like_driver(data: dict) -> bool:
        """Haydovchi agent payload strukturasini aniqlash."""
        if not isinstance(data, dict):
            return False
        if isinstance(data.get("matches"), list):
            return True
        if "driver" in data and any(k in data for k in ("trips", "schedule", "work")):
            return True
        return False

    async def _normalize_driver(self, data: dict) -> dict:
        """Haydovchi payload'ni normalizatsiya qilish."""
        matches = data.get("matches") or []
        driver = data.get("driver") or (matches[0] if matches else {})
        trips_data = data.get("trips") or {}
        trips = trips_data.get("trips") or []
        trip_summary = trips_data.get("summary") or {}
        sched = data.get("schedule") or {}
        schedules = sched.get("schedules") or sched.get("data") or []
        work = data.get("work") or {}

        vehicles = []
        for t in trips:
            vehicles.append({
                "bus_number": t.get("plate", ""),
                "route": t.get("route_name", t.get("routeName", "")) or "Noma'lum",
                "route_name": t.get("route_name", ""),
                "driver": (driver.get("full_name", "") if isinstance(driver, dict)
                           else ""),
                "status": "active" if t.get("status") == "ACCEPTED" else "inactive",
                "trips": t.get("trip_num", 1) or 1,
                "km": t.get("distance_km", 0) or 0,
                "plate": t.get("plate", ""),
            })

        return {
            "vehicles": vehicles,
            "summary": {
                "total_trips": trip_summary.get("total_trips", len(trips)),
                "accepted": trip_summary.get("accepted",
                                             trip_summary.get("total_trips", 0)),
                "not_accepted": trip_summary.get("not_accepted", 0),
                "total_km": trip_summary.get("total_km", 0),
                "completion_rate": trip_summary.get("completion_rate", 0),
                "total_vehicles": 0,
                "units": "trips",
            },
            "driver_data": {
                "driver": driver,
                "matches": matches,
                "found": bool(driver) or bool(matches),
                "trips_count": trip_summary.get("total_trips", len(trips)),
                "schedule_count": len(schedules),
                "work_log_days": work.get("work_logs_count", 0),
                "full_name": (driver.get("full_name") if isinstance(driver, dict)
                              else data.get("query", "")),
            },
            "route_breakdown": [],
        }

    async def _normalize_attendance(self, data: dict) -> dict:
        """Ishga chiqish ma'lumotlarini normalizatsiya qilish."""
        total = data.get("total_drivers", 0)
        present = data.get("present", 0)
        vehicles = []
        for s in data.get("scheduled") or []:
            fact = int(s.get("trip_fact", 0) or 0)
            vehicles.append({
                "bus_number": s.get("vehicle_number", ""),
                "route": s.get("route_name", "") or "Noma'lum",
                "route_name": s.get("route_name", ""),
                "driver": "",
                "status": "active" if fact > 0 else "inactive",
                "trips": fact,
                "km": s.get("distance_fact", 0) or 0,
            })
        for s in data.get("absent_list") or []:
            vehicles.append({
                "bus_number": s.get("vehicle_number", ""),
                "route": s.get("route_name", "") or "Noma'lum",
                "route_name": s.get("route_name", ""),
                "driver": "",
                "status": "inactive",
                "trips": 0,
                "km": 0,
            })
        return {
            "vehicles": vehicles,
            "summary": {
                "total_buses": total,
                "active_buses": present,
                "inactive_buses": total - present,
                "total_km": round(sum(
                    float(v.get("km", 0) or 0) for v in vehicles), 2),
                "total_trips": sum(v.get("trips", 0) for v in vehicles),
                "units": "buses",
            },
            "attendance_data": {
                "attendance_rate": data.get("attendance_rate", 0),
                "present": present,
                "absent": data.get("absent", 0),
                "scheduled_count": len(data.get("scheduled") or []),
                "absent_list": data.get("absent_list") or [],
            },
            "route_breakdown": [],
        }

    async def _normalize_db(self, data: dict) -> dict:
        """DB formatdan normalizatsiya."""
        # data could be {routes: [...], summary: {...}} or {vehicles: [...], summary: {...}}
        if "routes" in data:
            # All routes summary from DB
            routes = data["routes"]
            total = sum(r.get("total_trips", r.get("total_buses", 0)) for r in routes)
            accepted = sum(r.get("accepted", 0) for r in routes)
            total_km = sum(r.get("total_km", 0) for r in routes)
            total_vehicles = sum(r.get("total_vehicles", 0) for r in routes)
            vehicles = []
            for r in routes:
                route_trips = r.get("total_trips", r.get("total_buses", 0))
                vehicles.append({
                    "bus_number": "",
                    "route": r.get("route_name", ""),
                    "route_total": r.get("total_vehicles", route_trips),
                    "route_trips": route_trips,
                    "route_accepted": r.get("accepted", 0),
                    "route_vehicles": r.get("total_vehicles", 0),
                    "driver": "",
                    "shift": "",
                    "status": "active" if r.get("accepted", 0) > 0 else "inactive",
                    "trips": route_trips,
                    "km": r.get("total_km", 0),
                    "working_day": 0,
                })
            return {
                "vehicles": vehicles,
                "summary": {
                    "total_trips": total,
                    "accepted": accepted,
                    "not_accepted": total - accepted,
                    "total_km": round(total_km, 2),
                    "completion_rate": round(accepted / total * 100, 1) if total else 0,
                    "total_vehicles": total_vehicles,
                    "total_buses": total_vehicles,
                    "units": "trips",
                },
                "route_breakdown": routes,
            }
        elif "vehicles" in data:
            # Single route summary
            vehicles = data["vehicles"]
            summary = data.get("summary", {})
            total = summary.get("total_trips", 0)
            accepted = summary.get("accepted", 0)
            vehicle_count = summary.get("total_vehicles", 0)
            for v in vehicles:
                v["route_trips"] = total
                v["route_accepted"] = accepted
                v["route_vehicles"] = vehicle_count
            return {
                "vehicles": vehicles,
                "summary": {
                    "total_trips": total,
                    "accepted": accepted,
                    "not_accepted": summary.get("not_accepted", 0),
                    "total_km": summary.get("total_km", 0),
                    "completion_rate": summary.get("completion_rate", 0),
                    "total_vehicles": vehicle_count,
                    "total_buses": vehicle_count,
                    "units": summary.get("units", "trips"),
                },
            }
        return {"vehicles": [], "summary": {"total_trips": 0, "accepted": 0, "not_accepted": 0, "total_km": 0, "completion_rate": 0, "total_vehicles": 0, "total_buses": 0}}

    async def _extract_vehicles(self, raw_data: dict) -> dict:
        """Faqat avtobuslar ro'yxatini ajratib olish."""
        normalized = await self._normalize(raw_data)
        return {"vehicles": normalized["vehicles"]}
