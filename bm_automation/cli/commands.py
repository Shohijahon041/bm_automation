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


def cmd_demo_seed(args) -> int:
    """DEMO korxona + namuna ma'lumotlarni yaratish (idempotent)."""
    from ..app.dashboard.demo_seed import seed_demo

    try:
        res = seed_demo(
            username=args.username,
            password=args.password or "",
            days=args.days,
            company_name=args.company or "",
            end_date=args.end_date or "",
        )
    except (ValueError, RuntimeError) as exc:
        print(f"XATO: {exc}", file=sys.stderr)
        return 1

    w = res["window"]
    c = res["counts"]
    print(f"DEMO korxona tayyor: {res['company']} (#{res['company_id']})")
    print(f"  Login:  {res['username']}")
    if res["password"]:
        print(f"  Parol:  {res['password']}   (faqat bir marta ko'rsatiladi)")
    elif res["user_created"]:
        print("  Parol:  --password bilan berilgan")
    else:
        print("  Parol:  o'zgartirilmadi (mavjud akkaunt)")
    print(f"  Rol:    {res['role']}")
    print("  Yo'nalishlar: " + ", ".join(
        f"{r['id']} ({r['km']:g} km)" for r in res["routes"]))
    print(f"  Oyna:   {w['from']} .. {w['to']} ({w['days']} kun)")
    print(f"  Yozuvlar: {c['trips']} reys, {c['route_daily']} kunlik, "
          f"{c['schedules']} grafik, {c['work_logs']} AVTO qayd, "
          f"{c['drivers']} haydovchi, {c['vehicles']} avtobus")
    print(f"  Qo'shimcha: {c['fines']} jarima, {c['appeals']} murojaat, "
          f"{c['avans']} avans")
    for msg in res["warnings"]:
        print(f"  OGOHLANTIRISH: {msg}")
    print("  Eslatma: panel allaqachon ishlab turgan bo'lsa, tenant keshi "
          "30 soniyada yangilanadi.")
    return 0


def cmd_keepalive(args) -> int:
    """Tokenlarni tirik saqlash: refresh rotatsiyasi + tekshiruv so'rovi.

    12 soatlik `bm-token-refresh` timer uchun. Xato bo'lsa adminlarga
    Telegram ogohlantirish yuboriladi (qo'lda qayta login zarurligi
    haqida) va 1 qaytariladi.
    """
    client = BMClient()
    try:
        result = client.keepalive()
    except Exception as exc:  # noqa: BLE001 - timer uchun yutamiz, xabar beramiz
        print(f"XATO: {exc}", file=sys.stderr)
        _notify_keepalive_error(exc)
        return 1
    label = (
        "refresh yangilandi"
        if result == "refreshed"
        else "to'liq qayta login bajarildi"
    )
    print(f"OK: tokenlar tirik ({label})")
    return 0


def _notify_keepalive_error(exc: Exception) -> None:
    """Keep-alive xatosi haqida adminlarga Telegram ogohlantirish (best-effort)."""
    try:
        from ..app.config.settings import telegram_settings
        from ..app.notifications.telegram import send_message

        s = telegram_settings()
        targets = [
            tok for tok in str(s.get("admin_ids") or "").replace(",", " ").replace(";", " ").split()
            if tok.strip().isdigit()
        ]
        if not targets and str(s.get("chat_id") or "").strip():
            targets = [str(s.get("chat_id")).strip()]
        text = (
            "⚠️ <b>BM token keep-alive muammosi</b>\n"
            f"Sana: {date.today().isoformat()}\n"
            "Token yangilanmadi — BM API (jadval/brutto) ishlamay qolishi mumkin.\n"
            f"Sabab: {exc}"
        )
        for chat in targets[:5]:
            send_message(text, chat_id=chat)
    except Exception:  # noqa: BLE001 - ogohlantirish muvaffaqiyatsizligi asosiy natijani o'zgartirmaydi
        pass


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
            client.login_for_profile(profile["profileId"], fallback_to_main=True)
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
    flags = []
    if args.once:
        flags.append("--once")
    if getattr(args, "watchdog", False):
        flags.append("--watchdog")
    return bot_main(flags or [])


def cmd_dashboard(args) -> int:
    import webbrowser
    import uvicorn

    host = args.host
    port = args.port
    url = f"http://{host}:{port}/"

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    print(f"Dashboard (FastAPI): {url}")
    print("To'xtatish: Ctrl+C")
    uvicorn.run(
        "bm_automation.app.dashboard.fastapi_app:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
    return 0


def cmd_openapi(args) -> int:
    """FastAPI OpenAPI (Swagger) schema'sini JSON faylga yozish.

    Frontend uchun API kontraktini generatsiya qiladi:
      python -m bm_automation openapi [-o web/openapi.json]
    """
    import json
    import sys
    from pathlib import Path

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    from ..app.dashboard.fastapi_app import app

    out = Path(args.output)
    schema = app.openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    n_paths = len(schema.get("paths", {}))
    n_ops = sum(
        1
        for ops in schema.get("paths", {}).values()
        for m in ops
        if m in ("get", "post", "put", "patch", "delete")
    )
    print(f"OpenAPI schema: {out} ({n_paths} yo'l, {n_ops} operatsiya)")
    return 0


def cmd_insights(args) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    from ..app.notifications.ops.self_review import build_text
    print(build_text(days=args.days or None, html=args.html))
    return 0


def cmd_cleanup(args) -> int:
    from ..app.utils import cleanup

    files = cleanup.prune_reports(
        max_age_days=args.reports_days, dry_run=args.dry_run)
    exports = cleanup.prune_pending_exports(
        max_age_days=args.exports_days, dry_run=args.dry_run)
    logs = cleanup.prune_logs(
        max_age_days=args.logs_days, dry_run=args.dry_run)
    mode = "Sinov (dry-run): " if args.dry_run else ""
    print(f"{mode}eski fayllar: {files} ta, eski export yozuvlari: {exports} ta, "
          f"eski log fayllari: {logs} ta")
    return 0


def cmd_vault_links(args) -> int:
    from ..app.core.vault_links import autolink_all

    res = autolink_all(dry_run=args.dry_run, limit=max(1, args.limit))
    mode = "Sinov (dry-run): " if res["dry_run"] else ""
    print(f"{mode}{res['total']} qayd tekshirildi, "
          f"{res['changed_count']} ta yangilandi")
    for rel in res["changed"]:
        print(f"  ~ {rel}")
    return 0


def cmd_vault_grow(args) -> int:
    """Bilim bazasini kengaytiradi (kod xaritasi, holat, log, bo'shliqlar)."""
    from ..app.core.knowledge_grow import grow

    res = grow(gaps=not args.no_gaps)
    print(f"Bilim bazasi kengaytirildi: {res['notes']} qayd, "
          f"{len(res['changed'])} ta qayd yangilandi")
    for key, changed in res["steps"].items():
        print(f"  {'~' if changed else '='} {key}")
    for err in res["errors"]:
        print(f"  ! {err}")
    return 0


def cmd_vault_status(args) -> int:
    """Kengaytirish holati (qaydlar soni, interval, oxirgi ishga tushish)."""
    from ..app.core.knowledge_grow import status

    st = status()
    state = "yoqilgan" if st["enabled"] else "o'chirilgan (KNOWLEDGE_AUTO=off)"
    print(f"Avtomatik kengaytirish: {state}")
    print(f"Interval: {st['interval_min']} daqiqa, "
          f"ishga tushishlar: {st['runs']}")
    print(f"Qaydlar: {st['notes']} ta, {st['bytes']} bayt")
    print(f"Oxirgi: {st['last_run'] or '—'}")
    if st["next_in_sec"]:
        print(f"Keyingisi: {st['next_in_sec'] // 60} daqiqadan keyin")
    for item in st["history"][:5]:
        print(f"  {item.get('at', '')} — {item.get('notes', 0)} qayd, "
              f"{item.get('changed', 0)} yangilandi")
    return 0

