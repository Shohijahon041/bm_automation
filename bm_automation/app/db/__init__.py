"""Ma'lumotlar bazasi qatlami.

Faqat PostgreSQL (Supabase) qo'llab-quvvatlanadi.
Barcha 14 jadval: profiles, routes, vehicles, drivers, duties, schedules,
waybills, trips, trip_statuses, reports, report_runs, errors, notifications,
automation_runs.

Asosiy kirish nuqtalari:
- `get_storage()` — yagona `Storage` (DB ishlamasa o'chirilgan, no-op).
- `storage_for(db)` — berilgan Database bilan yangi Storage (testlar).
- `AutomationLogger` — avtomatik vazifalar jurnali (best-effort).
- `sync_*` — BM API → DB idempotent sinxronizatsiya.
"""

from .base import Database, DatabaseError  # noqa: F401
from .models import (  # noqa: F401
    SyncResult,
    SyncSource,
    TABLES,
    TripRecord,
    TripStatus,
    json_dumps,
    json_loads,
    now_utc,
    params_hash,
)
from .schema import init_db  # noqa: F401
from .storage import (  # noqa: F401
    Storage,
    get_storage,
    reset_storage,
    storage_for,
)
from .sync import AutomationLogger  # noqa: F401

__all__ = [
    "AutomationLogger",
    "Database",
    "DatabaseError",
    "Storage",
    "SyncResult",
    "SyncSource",
    "TABLES",
    "TripRecord",
    "TripStatus",
    "get_storage",
    "init_db",
    "json_dumps",
    "json_loads",
    "now_utc",
    "params_hash",
    "reset_storage",
    "storage_for",
]
