"""Avtobus texnik xizmat muddatini hisoblash (dashboard va bot uchun umumiy).

`vehicle_service_logs` yozuvidagi `next_service_date` / `next_service_km`
asosida holat aniqlanadi:

- ``overdue`` — muddat o'tib ketgan,
- ``due`` — `days_ahead`/`km_ahead` ichida yaqinlashgan,
- ``ok`` — muddat uzoq,
- ``unknown`` — keyingi xizmat belgilanmagan.
"""

from __future__ import annotations

from datetime import date

SERVICE_TYPES = (
    "Moy va filtrlar", "Tormoz tizimi", "Shina balansi", "Dvigatel ta'miri",
    "Elektr jihozlari", "Konditsioner", "Kuzov ishlari", "Texnik ko'rik",
    "Boshqa",
)

DEFAULT_DAYS_AHEAD = 30
DEFAULT_KM_AHEAD = 500


def due_state(row: dict, days_ahead: int = DEFAULT_DAYS_AHEAD,
              km_ahead: int = DEFAULT_KM_AHEAD,
              today: date | None = None) -> tuple[str, int, int]:
    """-> (state, days_left, km_left)."""
    today = today or date.today()
    state = "unknown"
    days_left = 0
    km_left = 0
    next_date = str((row or {}).get("next_service_date") or "")
    if next_date:
        try:
            days_left = (date.fromisoformat(next_date) - today).days
            state = "overdue" if days_left < 0 else (
                "due" if days_left <= days_ahead else "ok")
        except ValueError:
            pass
    next_km = int((row or {}).get("next_service_km") or 0)
    odometer = int((row or {}).get("odometer") or 0)
    if next_km > 0 and odometer > 0:
        km_left = next_km - odometer
        if km_left < 0:
            state = "overdue"
        elif km_left <= km_ahead and state != "overdue":
            state = "due"
    return state, days_left, km_left


def collect_due(days_ahead: int = DEFAULT_DAYS_AHEAD,
                km_ahead: int = DEFAULT_KM_AHEAD,
                route_ids: list[str] | None = None,
                today: date | None = None) -> list[dict]:
    """Muddati yaqin/o'tgan avtobuslar ro'yxati (avtobus ma'lumotlari bilan)."""
    from ..db.storage import get_storage
    storage = get_storage()
    latest = storage.latest_vehicle_service_logs(route_ids)
    if not latest:
        return []
    vehicles = {str(v.get("external_id") or ""): v
                for v in storage.query("SELECT * FROM vehicles")}
    routes = {str(r.get("external_id") or ""): str(r.get("name") or "")
              for r in storage.query("SELECT external_id, name FROM routes")}
    today = today or date.today()
    items = []
    for row in latest:
        state, days_left, km_left = due_state(row, days_ahead, km_ahead, today)
        if state not in ("due", "overdue"):
            continue
        vid = str(row.get("vehicle_id") or "")
        vehicle = vehicles.get(vid) or {}
        route_id = str(vehicle.get("route_id") or row.get("route_id") or "")
        items.append({
            "vehicle_id": vid,
            "plate_number": str(vehicle.get("plate_number") or vid),
            "garage_number": str(vehicle.get("garage_number") or ""),
            "model": str(vehicle.get("model") or ""),
            "route_id": route_id,
            "route_name": routes.get(route_id, ""),
            "service_date": str(row.get("service_date") or ""),
            "service_type": str(row.get("service_type") or ""),
            "odometer": int(row.get("odometer") or 0),
            "next_service_date": str(row.get("next_service_date") or ""),
            "next_service_km": int(row.get("next_service_km") or 0),
            "state": state,
            "days_left": days_left,
            "km_left": km_left,
        })
    items.sort(key=lambda x: (
        0 if x["state"] == "overdue" else 1,
        x["days_left"] if x["next_service_date"] else 1_000_000,
        x["km_left"]))
    return items
