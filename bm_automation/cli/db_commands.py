"""CLI `db` buyruqlari — ma'lumotlar bazasi bilan ishlash.

Misol:
    python -m bm_automation db init
    python -m bm_automation db status
    python -m bm_automation db sync --route <id> --date YYYY-MM-DD
    python -m bm_automation db trips --status ACCEPTED --limit 20
    python -m bm_automation db errors
    python -m bm_automation db runs
    python -m bm_automation db enable-rls
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
            or "trips" in what or "waybills" in what \
            or "driver_profiles" in what or "work_logs" in what:
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
    if "driver_profiles" in what:
        results.append(db_sync.sync_driver_profiles(
            storage, client, route, date_str, force=args.force))
    if "work_logs" in what:
        frm = args.from_date or date.today().isoformat()
        to = args.to or date.today().isoformat()
        results.append(db_sync.sync_work_logs(storage, frm, to, route))
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


def cmd_db_fill_drivers(args) -> int:
    """Saytdagi barcha haydovchi ma'lumotlari + kunlik kmlarni to'ldiradi.

    Haydovchi profillariga telefon/PINFL/guvohnoma (force) yoziladi va
    waybill trips'idan kunlik km hisobi `driver_work_logs`'ga yig'iladi.
    """
    from datetime import date, timedelta

    from ..app.core.profiles import all_profiles

    storage, ok = _storage_or_fail(args)
    if not ok:
        return 1
    client = BMClient()
    client.login()

    profiles = all_profiles()
    route = (args.route or "").strip()
    if route:
        matched = [p for p in profiles
                   if str(p.get("routeVariantId") or "").strip() == route]
        profiles = matched if matched else [
            {"name": "?", "routeVariantId": route, "profileId": ""}]

    main_access = client.access_token
    main_refresh = client.refresh_token

    def use_main() -> None:
        client.access_token = main_access
        client.refresh_token = main_refresh
        client.session.headers["Authorization"] = f"Bearer {main_access}"

    # davr: --days > 0 -> so'nggi N kun; aks holda trips'dagi barcha tarix
    today = date.today()
    bounds = storage.query(
        "SELECT MIN(date) AS mn, MAX(date) AS mx FROM trips", limit=1)
    all_from = bounds[0]["mn"] if bounds and bounds[0].get("mn") else ""
    all_to = bounds[0]["mx"] if bounds and bounds[0].get("mx") else ""
    if args.days and args.days > 0:
        frm = (today - timedelta(days=args.days - 1)).isoformat()
    else:
        frm = all_from or today.isoformat()
    to = args.date or all_to or today.isoformat()

    print(f"Davr: {frm} … {to}")
    failed = False
    for p in profiles:
        name = str(p.get("name") or "?")
        rid = str(p.get("routeVariantId") or "").strip()
        pid = str(p.get("profileId") or "").strip()
        if not rid:
            print(f"  {name}: routeVariantId yo'q", file=sys.stderr)
            failed = True
            continue
        try:
            if pid:
                client.login_by_profile(pid)
            else:
                use_main()
        except Exception as exc:  # noqa: BLE001 - token xatosi bir profilni to'xtatadi
            print(f"  {name}: token xatosi: {exc}", file=sys.stderr)
            failed = True
            continue
        r1 = db_sync.sync_driver_profiles(
            storage, client, rid, args.date or today.isoformat(), force=True)
        r2 = db_sync.sync_work_logs(storage, frm, to, rid)
        _print_result(r1)
        _print_result(r2)
        if r1.error or r2.error:
            failed = True
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


def cmd_db_enable_rls(args) -> int:
    """Barcha jadvallarda Row Level Security yoqish (policy qo'shmaydi).

    Ilova superuser (BYPASSRLS) bilan ishlaganligi uchun ish buzilmaydi.
    anon/authenticated rollari esa policy yo'qligi sababli 0 qator ko'radi.
    """
    from ..app.db.base import Database
    from ..app.config.settings import db_settings
    from ..app.db.models import TABLES

    db = Database(**db_settings())
    if db.driver != "postgres":
        print("Buyruq faqat PostgreSQL uchun.", file=sys.stderr)
        return 1
    with db.transaction():
        for table in TABLES:
            db.execute(
                f"ALTER TABLE public.\"{table}\" ENABLE ROW LEVEL SECURITY")
    rows = db.query(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND rowsecurity = true")
    enabled = {r["tablename"] for r in rows}
    missing = [t for t in TABLES if t not in enabled]
    if missing:
        print(f"RLS yoqilmadi: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"RLS yoqildi ({len(enabled)} jadval): {', '.join(sorted(enabled))}")
    return 0


def cmd_db_backup(args) -> int:
    """Supabase'ga zaxira nusxa yaratish."""
    from ..app.db.backup import run_backup, get_backup_state
    from ..app.config.settings import backup_enabled, backup_db_settings

    # Backup holatini ko'rish
    if args.status:
        state = get_backup_state()
        print(f"Backup holati: {state['last_status']}")
        print(f"  Oxirgi backup: {state['last_run'] or 'hech qachon'}")
        print(f"  Davomiylik: {state['last_duration']} soniya")
        print(f"  Jadvalar: {state['tables_synced']}")
        print(f"  Qatorlar: {state['rows_synced']}")
        if state['errors']:
            print(f"  Xatoliklar: {len(state['errors'])}")
            for e in state['errors'][-5:]:
                print(f"    - {e}")
        if not backup_enabled():
            print("  [!] Backup yoqilmagan (SUPABASE_BACKUP_DSN ko'rsatilmagan)")
        else:
            dsn = backup_db_settings().get('dsn', '')
            # DSN'dan host ajratib olish
            if '@' in dsn:
                host = dsn.split('@')[1].split('/')[0]
                print(f"  Maqsadli baz: {host}")
        return 0

    # Backup yoqilganligini tekshirish
    if not backup_enabled():
        print("Backup yoqilmagan!", file=sys.stderr)
        print(".env faylida SUPABASE_BACKUP_DSN ni kiriting:", file=sys.stderr)
        print("  SUPABASE_BACKUP_DSN=postgresql://...@...pooler.supabase.com:5432/postgres", file=sys.stderr)
        return 1

    # Backup bajarish
    print("Backup boshlandi...")
    tables = None
    if args.tables:
        tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    result = run_backup(tables=tables)

    if result['ok']:
        print(f"Backup muvaffaqiyatli tugadi!")
        print(f"  Jadvalar: {result['tables_synced']}")
        print(f"  Qatorlar: {result['rows_synced']}")
        print(f"  Davomiylik: {result['duration']} soniya")
        if result['details']:
            print("  Tafsilotlar:")
            for d in result['details']:
                if 'error' in d:
                    print(f"    [X] {d['table']}: {d['error']}")
                elif d['total'] > 0:
                    print(f"    [OK] {d['table']}: +{d['inserted']} yangi, ~{d['updated']} yangilandi")
        return 0
    else:
        print(f"Backup xatolik bilan tugadi: {result.get('error', 'noma\'lum xato')}", file=sys.stderr)
        return 1
