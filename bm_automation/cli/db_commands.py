"""CLI `db` buyruqlari — ma'lumotlar bazasi bilan ishlash.

Misol:
    python -m bm_automation db init
    python -m bm_automation db status
    python -m bm_automation db sync --route <id> --date YYYY-MM-DD
    python -m bm_automation db trips --status ACCEPTED --limit 20
    python -m bm_automation db errors
    python -m bm_automation db runs
"""

from __future__ import annotations

import json
import sys
from datetime import date

from ..app.api.client import BMClient
from ..app.db import get_storage
from ..app.db import sync as db_sync
from ..app.repositories.route_repo import find_route_ids
from ..app.services.gross_service import route_tree


def _storage_or_fail(args) -> tuple:
    from ..app.db.storage import get_storage as _get
    storage = _get()
    if not storage.enabled:
        print("DB rejimi o'chirilgan (BM_DB_DRIVER/BM_DB_DSN tekshiring).",
              file=sys.stderr)
        return storage, False
    return storage, True


def _print_result(r) -> None:
    if getattr(r, "error", ""):
        print(f"  XATO {r.entity}: {r.error}")
    else:
        print(f"  {r.entity:<10} inserted={r.inserted} updated={r.updated} "
              f"total={r.total}")


def cmd_db_init(args) -> int:
    from ..app.db import init_db
    from ..app.db.base import Database
    from ..app.config.settings import db_settings

    db = Database(**db_settings())
    ok = init_db(db)
    print(f"Driver: {db.driver} | mavjud: {db.available}")
    if ok:
        print("Sxema tayyor.")
        return 0
    print("Sxema yaratilmadi.", file=sys.stderr)
    return 1


def cmd_db_status(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    print(f"Driver: {storage.db.driver}")
    for table, n in storage.counts().items():
        print(f"  {table:<18} {n}")
    return 0


def cmd_db_sync(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    client = BMClient()
    client.login()

    what = args.what.split(",")
    results = []

    if "statuses" in what:
        results.append(db_sync.sync_statuses(storage))
    if "profiles" in what:
        results.append(db_sync.sync_profiles(storage))
    if "routes" in what:
        results.append(db_sync.sync_routes(storage, client, brutto=True))

    if "drivers" in what or "vehicles" in what or "duties" in what \
            or "trips" in what or "waybills" in what:
        route = args.route
        if not route:
            tree = route_tree(client)
            ids = find_route_ids(tree)
            if not ids:
                print("Yo'nalish topilmadi. --route <id> bering.",
                      file=sys.stderr)
                return 1
            route = ids[0]
            print(f"Avtomatik tanlangan yo'nalish: {route}")
        date_str = args.date or date.today().isoformat()

    if "drivers" in what:
        results.append(db_sync.sync_drivers(storage, client, route, date_str))
    if "vehicles" in what:
        results.append(db_sync.sync_vehicles(storage, client, route, date_str))
    if "duties" in what:
        results.append(db_sync.sync_duties(storage, client, route, date_str))
    if "trips" in what:
        results.append(db_sync.sync_gross_trips(storage, client, route, date_str))
    if "waybills" in what:
        frm = args.from_date or date.today().isoformat()
        to = args.to or date.today().isoformat()
        results.append(db_sync.sync_waybills(storage, client, route, frm, to))

    for r in results:
        _print_result(r)
    failed = [r for r in results if r.error]
    return 1 if failed else 0


def cmd_db_trips(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    rows = storage.trips(date=args.date or "", route_id=args.route or "",
                         status=args.status or "", limit=args.limit)
    for r in rows:
        print(f"  {r['date']} | {r['route_id'][:8]:<8} | {r['vehicle_id'] or '-':<12} "
              f"| {r['driver_id'] or '-':<12} | plan={r['planned_time'] or '-':<8} "
              f"| {r['status']:<14} | {r['source']}")
    print(f"\nJami: {len(rows)}")
    return 0


def cmd_db_errors(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    rows = storage.list_errors(limit=args.limit)
    for r in rows:
        print(f"  {r['occurred_at']} | {r['source']:<12} | {r['message']}")
    print(f"\nJami: {len(rows)}")
    return 0


def cmd_db_runs(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    rows = storage.list_runs(limit=args.limit)
    for r in rows:
        print(f"  {r['started_at']} | {r['run_id']:<12} | {r['trigger']:<8} "
              f"| {r['status']:<6} | {r['sheet_date']} {r['month']}")
    print(f"\nJami: {len(rows)}")
    return 0


def cmd_db_reports(args) -> int:
    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    rows = storage.list_reports(limit=args.limit)
    for r in rows:
        print(f"  {r['name']:<20} | {r['report_type']:<8} | {r['period_date']} "
              f"| {r['file_path']}")
    print(f"\nJami: {len(rows)}")
    return 0
