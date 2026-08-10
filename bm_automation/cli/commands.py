"""CLI buyruq funksiyalari (har bir `python -m bm_automation <buyruq>`)."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

from ..app.api.client import BMClient
from ..cli.common import print_tree, resolve_route_id
from ..app.core.profiles import all_profiles, get_profile, load_profiles, set_active, upsert_profile
from ..app.reports.registry import REPORTS
from ..app.services.duty_service import run as run_duty
from ..app.services.gross_service import find_route_ids, route_tree, run_route, run_trip
from ..app.services.report_service import generate_daily_reports, generate_report
from ..app.services.summary_service import run as run_summary
from ..app.services.waybill_service import run as run_waybill


def cmd_login(args) -> int:
    client = BMClient()
    client.login()
    print(f"Login muvaffaqiyatli: {client.config.username}")
    print(f"  Muhit: {client.config.base_url}")
    return 0


def cmd_login_browser(args) -> int:
    from ..app.auth.browser_login import run as browser_run
    return browser_run(args.extra)


def cmd_list_reports(args) -> int:
    for name, spec in sorted(REPORTS.items()):
        excel = " (excel)" if spec.get("excel") else ""
        print(f"  {name:<18} params={spec['params']}{excel}")
    return 0


def cmd_report_types(args) -> int:
    print("Mavjud hisobotlar va parametrlari:")
    return cmd_list_reports(args)


def cmd_reports(args) -> int:
    client = BMClient()
    client.login()
    if args.name:
        result = generate_report(
            client,
            args.name,
            report_type=args.type,
            date_str=args.date,
            out_dir=args.out,
            save_json=not args.no_json,
            save_excel=not args.no_excel,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    results = generate_daily_reports(client, date_str=args.date, out_dir=args.out)
    print(f"\nYaratildi: {len(results)} hisobot")
    return 0


def cmd_routes(args) -> int:
    client = BMClient()
    client.login()
    tree = route_tree(client)
    if args.json:
        print(json.dumps(tree, ensure_ascii=False, indent=2))
    else:
        print_tree(tree)
    return 0


def cmd_gross_trip(args) -> int:
    client = BMClient()
    client.login()
    rid = resolve_route_id(client, args.route)
    result = run_trip(client, rid, date_str=args.date, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_gross_route(args) -> int:
    client = BMClient()
    client.login()
    rid = resolve_route_id(client, args.route)
    result = run_route(client, rid, from_date=args.from_date, to_date=args.to, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_waybill(args) -> int:
    client = BMClient()
    client.login()
    rid = resolve_route_id(client, args.route)
    result = run_waybill(
        client,
        rid,
        from_date=args.from_date,
        to_date=args.to,
        out_dir=args.out,
        plate_num=args.plate,
        direction=args.direction,
        status=args.status,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_notify_test(args) -> int:
    from ..app.notifications.telegram import send_message
    send_message(args.text)
    print("Yuborildi.")
    return 0


def cmd_duty(args) -> int:
    client = BMClient()
    client.login()
    rid = resolve_route_id(client, args.route)
    result = run_duty(client, rid, date_str=args.date, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_drivers(args) -> int:
    from ..app.services.driver_sheet_service import run as run_driver_sheet
    from ..app.services.driver_sheet_service import run_all as run_driver_sheet_all

    client = BMClient()
    client.login()
    date_str = args.date or (date.today() + timedelta(days=args.offset)).isoformat()
    if args.all:
        results = run_driver_sheet_all(
            client,
            date_str=date_str,
            out_dir=args.out,
            send=args.send,
            chat_id=args.chat,
            only=args.profile,
        )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    profile = None
    route = args.route
    if args.profile:
        profile = get_profile(args.profile)
        if not profile:
            print(f"Profil topilmadi: {args.profile}", file=sys.stderr)
            print("Mavjudlar:")
            for p in all_profiles():
                print(f"  {p.get('name')}")
            return 1
        if not route:
            route = str(profile.get("routeVariantId", ""))
        if profile.get("profileId"):
            client.login_by_profile(profile["profileId"])
    rid = resolve_route_id(client, route)
    result = run_driver_sheet(
        client,
        rid,
        date_str=date_str,
        out_dir=args.out,
        send=args.send,
        chat_id=args.chat,
        profile=profile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_profiles(args) -> int:
    if args.action == "list":
        data = load_profiles()
        print(f"Aktiv: {data.get('active', '')}")
        for i, p in enumerate(all_profiles(), 1):
            print(f"  {i}. {p.get('name')}")
            print(f"     profileId={p.get('profileId') or '-'}  route={p.get('routeVariantId') or '-'}")
            print(f"     1-chiqish={p.get('start1') or '-'}  2-chiqish={p.get('start2') or '-'}")
        return 0
    if args.action == "set-active":
        print(f"Aktiv profil: {set_active(args.name)}")
        return 0
    if args.action == "add":
        p = {"name": args.name, "routeVariantId": args.route or ""}
        if args.profile_id:
            p["profileId"] = args.profile_id
        if args.start1:
            p["start1"] = args.start1
        if args.start2:
            p["start2"] = args.start2
        if args.route_name:
            p["routeName"] = args.route_name
        upsert_profile(p)
        print(f"Qo'shildi/yangilandi: {args.name}")
        return 0
    print("Noma'lum amal", file=sys.stderr)
    return 1


def cmd_summary(args) -> int:
    from_date = args.from_date or date.today().replace(day=1).isoformat()
    to_date = args.to or date.today().isoformat()
    client = BMClient()
    client.login()
    rid = resolve_route_id(client, args.route)
    result = run_summary(
        client,
        rid,
        from_date=from_date,
        to_date=to_date,
        out_dir=args.out,
        out_file=args.out_file,
        download_missing=not args.offline,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_daily(args) -> int:
    from ..app.services.daily_service import run_daily

    results = run_daily(send=not args.no_send, chat_id=args.chat or None,
                        month_offset=args.month_offset, sheet_offset=args.offset,
                        fresh_login=not args.no_fresh_login,
                        max_attempts=args.attempts, attempt_delay=args.delay,
                        only=args.only or None, trigger=args.trigger)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def cmd_schedule(args) -> int:
    from ..app.scheduler.schedule import run_scheduler
    run_scheduler(
        time_str=args.time,
        once=args.once,
        route_id=args.route,
        out_dir=args.out,
        notify=args.notify,
        include_trip=not args.no_trip,
        include_route=not args.no_route,
        include_waybill=not args.no_waybill,
        include_duty=not args.no_duty,
        date_offset_days=args.offset,
    )
    return 0


def cmd_bot(args) -> int:
    from ..app.notifications.bot import main as bot_main
    return bot_main(["--once"] if args.once else [])


def cmd_dashboard(args) -> int:
    from ..app.dashboard.server import run
    return run(host=args.host, port=args.port, open_browser=not args.no_browser)

