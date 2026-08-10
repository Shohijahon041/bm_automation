"""Rejalashtirilgan hisobot yuklash.

Har kuni soat HH:MM da belgilangan hisobotlarni yuklab oladi,
ixtiyoriy ravishda Telegram'ga yuboradi. Uzoq muddatli ishlashi uchun
Windows Task Scheduler yoki cron bilan ishga tushiriladi:
    python -m bm_automation schedule --route ID --time 22:00
(cheksiz sikl; time o'tgach har kuni qayta ishlaydi)
"""

from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta

from ..api.client import BMClient
from ..config.settings import telegram_settings
from ..services.duty_service import run as run_duty
from ..services.gross_service import run_route, run_trip
from ..services.waybill_service import run as run_waybill
from ..utils.time import last_working_day


def run_daily_job(
    route_id: str,
    out_dir: str = "reports",
    notify: bool = False,
    include_trip: bool = True,
    include_route: bool = True,
    include_waybill: bool = True,
    include_duty: bool = True,
    date_offset_days: int = 1,
) -> list:
    """Kunlik hisobotlarni yig'adi. date_offset: 0=bugun, 1=kecha va h.k."""
    client = BMClient()
    client.login()
    today = date.today()
    report_date = last_working_day(today - timedelta(days=date_offset_days))
    ds = report_date.isoformat()

    results = []
    if include_trip:
        r = run_trip(client, route_id, date_str=ds, out_dir=out_dir)
        r["kind"] = "gross-trip"
        results.append(r)
    if include_route:
        r = run_route(client, route_id, from_date=ds, to_date=ds, out_dir=out_dir)
        r["kind"] = "gross-route"
        results.append(r)
    if include_waybill:
        r = run_waybill(client, route_id, from_date=ds, to_date=ds, out_dir=out_dir)
        r["kind"] = "waybill"
        results.append(r)
    if include_duty:
        r = run_duty(client, route_id, date_str=ds, out_dir=out_dir)
        r["kind"] = "duty"
        results.append(r)

    if notify:
        from ..notifications.telegram import send_report_summary
        for r in results:
            try:
                send_report_summary(r)
            except Exception as exc:
                print(f"Telegram xatosi ({r.get('kind')}): {exc}")

    return results


def run_scheduler(time_str: str = "22:00", once: bool = False, interval_seconds: int = 60, **job_kwargs) -> None:
    """time_str dan boshlab har kuni ishlaydigan sikl.

    once=True bo'lsa bir marta ishlab chiqadi (Task Scheduler uchun).
    """
    if once:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Bir martalik ishga tushirildi.")
        run_daily_job(**job_kwargs)
        print("Bajarildi.")
        return
    hh, mm = map(int, time_str.split(":"))
    next_run = None
    while True:
        now = datetime.now()
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if next_run is None or candidate != next_run:
            next_run = candidate
        wait = (next_run - now).total_seconds()
        print(f"[{now:%Y-%m-%d %H:%M:%S}] Keyingi ishlash: {next_run:%Y-%m-%d %H:%M:%S} ({(wait/60):.0f} min)")
        time.sleep(min(interval_seconds, max(wait, 0)))
        if wait <= interval_seconds:
            try:
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Hisobot yuklanmoqda...")
                run_daily_job(**job_kwargs)
                print("Bajarildi.")
            except Exception as exc:
                print(f"Xato: {exc}")
            next_run = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bm_automation schedule", description="Rejalashtirilgan hisobot yuklash")
    parser.add_argument("--route", required=True, help="routeVariantId")
    parser.add_argument("--out", default="reports")
    parser.add_argument("--notify", action="store_true", help="Telegram'ga yuborish")
    parser.add_argument("--no-trip", action="store_true")
    parser.add_argument("--no-route", action="store_true")
    parser.add_argument("--no-waybill", action="store_true")
    parser.add_argument("--no-duty", action="store_true")
    parser.add_argument("--offset", type=int, default=1, help="Qaysi kuni: 0=bugun, 1=kecha")
    parser.add_argument("--time", default="22:00", help="Ishlash vaqti HH:MM")
    parser.add_argument("--once", action="store_true", help="Bir marta ishlab chiqish (Task Scheduler uchun)")
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
