"""CLI argument parser va asosiy ishga tushirish nuqtasi.

`python -m bm_automation <buyruq>` → `bm_automation.cli.parser.main()`.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    from .commands import (
        cmd_bot,
        cmd_daily,
        cmd_dashboard,
        cmd_drivers,
        cmd_duty,
        cmd_gross_route,
        cmd_gross_trip,
        cmd_list_reports,
        cmd_login,
        cmd_login_browser,
        cmd_notify_test,
        cmd_profiles,
        cmd_reports,
        cmd_report_types,
        cmd_routes,
        cmd_schedule,
        cmd_summary,
        cmd_waybill,
    )
    from .db_commands import (
        cmd_db_errors,
        cmd_db_init,
        cmd_db_reports,
        cmd_db_runs,
        cmd_db_status,
        cmd_db_sync,
        cmd_db_trips,
    )

    parser = argparse.ArgumentParser(prog="bm_automation", description="bm.dtransport.uz avtomatizatsiyasi")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Login'ni tekshirish")
    p_login.set_defaults(func=cmd_login)

    p_loginb = sub.add_parser("login-browser", help="OneID orqali brauzer login")
    p_loginb.set_defaults(func=cmd_login_browser)
    p_list = sub.add_parser("list-reports", help="Mavjud hisobotlarni ko'rsatish")
    p_list.set_defaults(func=cmd_list_reports)

    p_reports = sub.add_parser("reports", help="Hisobot yaratish")
    p_reports.add_argument("--name", help="Hisobot nomi (ko'rsatilmasa hammasi)")
    p_reports.add_argument("--type", default="DAILY", choices=["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
    p_reports.add_argument("--date", default="", help="Sana YYYY-MM-DD (bo'sh = bugun)")
    p_reports.add_argument("--out", default="reports", help="Chiqish papkasi")
    p_reports.add_argument("--no-json", action="store_true")
    p_reports.add_argument("--no-excel", action="store_true")
    p_reports.set_defaults(func=cmd_reports)

    p_routes = sub.add_parser("routes", help="Brutto yo'nalish daraxtini ko'rsatish")
    p_routes.add_argument("--json", action="store_true", help="JSON ko'rinishida")
    p_routes.set_defaults(func=cmd_routes)

    p_gt = sub.add_parser("gross-trip", help="Brutto trip hisoboti (JSON + Excel)")
    p_gt.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    p_gt.add_argument("--date", default="", help="Sana YYYY-MM-DD (bo'sh = bugun)")
    p_gt.add_argument("--out", default="reports")
    p_gt.set_defaults(func=cmd_gross_trip)

    p_gr = sub.add_parser("gross-route", help="Brutto route hisoboti (JSON + 2 Excel)")
    p_gr.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    p_gr.add_argument("--from", dest="from_date", default="", help="Boshlanish sana YYYY-MM-DD")
    p_gr.add_argument("--to", default="", help="Tugash sana YYYY-MM-DD (max 30 kun, bo'sh = bugun)")
    p_gr.add_argument("--out", default="reports")
    p_gr.set_defaults(func=cmd_gross_route)

    p_wb = sub.add_parser("waybill", help="Waybill (yo'l varaqalari) hisoboti")
    p_wb.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    p_wb.add_argument("--from", dest="from_date", default="", help="Boshlanish sana YYYY-MM-DD")
    p_wb.add_argument("--to", default="", help="Tugash sana YYYY-MM-DD (max 7 kun, bo'sh = bugun)")
    p_wb.add_argument("--plate", default="", help="Davlat raqami bo'yicha filter")
    p_wb.add_argument("--direction", default="", choices=["UP", "DOWN", "BOTH"])
    p_wb.add_argument("--status", default="", choices=["ACCEPTED", "NOT_ACCEPTED", "PENDING_ACCESS", "APPROVED", "REJECTED", "ZERO_MILEAGE"])
    p_wb.add_argument("--out", default="reports")
    p_wb.set_defaults(func=cmd_waybill)

    p_nt = sub.add_parser("notify-test", help="Telegram xabarini sinash")
    p_nt.add_argument("--text", default="Test xabar", help="Yuboriladigan matn")
    p_nt.set_defaults(func=cmd_notify_test)

    p_duty = sub.add_parser("duty", help="Kunlik duty (haydovchi/avtobus/grafik) ma'lumoti")
    p_duty.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    p_duty.add_argument("--date", default="", help="Sana YYYY-MM-DD (bo'sh = bugun)")
    p_duty.add_argument("--out", default="reports")
    p_duty.set_defaults(func=cmd_duty)

    p_dr = sub.add_parser("drivers", help="Haydovchilar kunlik chiqish jadvali (rasm) — faqat shofyorlar uchun")
    p_dr.add_argument("--route", default="", help="routeVariantId (bo'sh = profil yoki birinchi topilgan)")
    p_dr.add_argument("--date", default="", help="Sana YYYY-MM-DD (bo'sh = bugun + offset)")
    p_dr.add_argument("--offset", type=int, default=0, help="--date berilmasa qaysi kun: 0=bugun, 1=ertaga")
    p_dr.add_argument("--out", default="reports")
    p_dr.add_argument("--send", action="store_true", help="Telegram'ga rasm yuborish")
    p_dr.add_argument("--chat", default="", help="Chat ID (bo'sh = TG_DRIVER_CHAT_ID yoki TG_CHAT_ID)")
    p_dr.add_argument("--profile", default="", help="Profil nomi (profiles.json'dan)")
    p_dr.add_argument("--all", action="store_true", help="Barcha profillar uchun")
    p_dr.set_defaults(func=cmd_drivers)

    p_prof = sub.add_parser("profiles", help="Kompaniya profillarini boshqarish")
    prof_sub = p_prof.add_subparsers(dest="action", required=True)
    ps_l = prof_sub.add_parser("list", help="Profillarni ko'rsatish")
    ps_l.set_defaults(func=cmd_profiles)
    ps_sa = prof_sub.add_parser("set-active", help="Aktiv profilni belgilash")
    ps_sa.add_argument("name")
    ps_sa.set_defaults(func=cmd_profiles)
    ps_a = prof_sub.add_parser("add", help="Profil qo'shish/yangilash")
    ps_a.add_argument("name")
    ps_a.add_argument("--route", default="", help="routeVariantId")
    ps_a.add_argument("--profile-id", dest="profile_id", default="", help="OneID profil ID")
    ps_a.add_argument("--route-name", default="", help="Yo'nalish nomi (masalan 10-yo'nalish)")
    ps_a.add_argument("--start1", default="", help="1-chiqish konechkasi nomi")
    ps_a.add_argument("--start2", default="", help="2-chiqish konechkasi nomi")
    ps_a.set_defaults(func=cmd_profiles)

    p_sm = sub.add_parser("summary", help="Oylik/yillik duty jadvalini Excel'ga yig'ish")
    p_sm.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    p_sm.add_argument("--from", dest="from_date", default="", help="Boshlanish sana YYYY-MM-DD (bo'sh = oyning 1-i)")
    p_sm.add_argument("--to", default="", help="Tugash sana YYYY-MM-DD (bo'sh = bugun)")
    p_sm.add_argument("--out", default="reports", help="Kunlik fayllar papkasi")
    p_sm.add_argument("--out-file", default="", help="Chiqish xlsx fayl yo'li (bo'sh = avtomatik)")
    p_sm.add_argument("--offline", action="store_true", help="Faqat saqlangan fayllardan (API'siz)")
    p_sm.set_defaults(func=cmd_summary)

    p_daily = sub.add_parser("daily", help="Kunlik jadval (rasm) + oylik yig'ma (Excel) yuborish")
    p_daily.add_argument("--no-send", action="store_true", help="Yubormasdan faqat yaratish")
    p_daily.add_argument("--chat", default="", help="Telegram chat ID (bo'sh = .env)")
    p_daily.add_argument("--month-offset", type=int, default=0, help="0=joriy oy, 1=o'tgan oy")
    p_daily.add_argument("--offset", type=int, default=1, help="Qaysi kunga jadval: 0=bugun, 1=ertaga (standart)")
    p_daily.add_argument("--attempts", type=int, default=10, help="Hammasi yuborilmaguncha qayta urinishlar (standart 10)")
    p_daily.add_argument("--delay", type=int, default=600, help="Urinishlar orasidagi kutish sekundlarda (standart 600)")
    p_daily.add_argument("--no-fresh-login", action="store_true", help="OneID qayta loginsiz (saqlangan token bilan)")
    p_daily.add_argument("--only", default="", help="Faqat shu nomdagi profil uchun (masalan 'FERGANATEX')")
    p_daily.add_argument("--trigger", default="auto", help="Ish manbai: auto/manual/bot")
    p_daily.set_defaults(func=cmd_daily)

    p_sc = sub.add_parser("schedule", help="Rejalashtirilgan hisobot yuklash (cheksiz sikl)")
    p_sc.add_argument("--route", required=True, help="routeVariantId")
    p_sc.add_argument("--out", default="reports")
    p_sc.add_argument("--notify", action="store_true", help="Telegram'ga yuborish")
    p_sc.add_argument("--no-trip", action="store_true")
    p_sc.add_argument("--no-route", action="store_true")
    p_sc.add_argument("--no-waybill", action="store_true")
    p_sc.add_argument("--no-duty", action="store_true")
    p_sc.add_argument("--offset", type=int, default=1, help="Qaysi kun: 0=bugun, 1=kecha")
    p_sc.add_argument("--time", default="22:00", help="Ishlash vaqti HH:MM")
    p_sc.add_argument("--once", action="store_true", help="Bir marta ishlab chiqish (Task Scheduler uchun)")
    p_sc.set_defaults(func=cmd_schedule)

    p_bot = sub.add_parser("bot", help="Telegram bot: holat (dashboard) + qayta yuborish tugmasi")
    p_bot.add_argument("--once", action="store_true", help="Bitta getUpdates tsikli (sinash)")
    p_bot.set_defaults(func=cmd_bot)

    p_dash = sub.add_parser("dashboard", help="Web dashboard (http)")
    p_dash.add_argument("--host", default="127.0.0.1", help="Server manzili (standart 127.0.0.1)")
    p_dash.add_argument("--port", type=int, default=8080, help="Server porti (standart 8080)")
    p_dash.add_argument("--no-browser", action="store_true", help="Brauzerni ochmaslik")
    p_dash.set_defaults(func=cmd_dashboard)

    p_db = sub.add_parser("db", help="Ma'lumotlar bazasi (PostgreSQL/SQLite)")
    db_sub = p_db.add_subparsers(dest="action", required=True)
    db_i = db_sub.add_parser("init", help="Sxemani yaratish")
    db_i.set_defaults(func=cmd_db_init)
    db_st = db_sub.add_parser("status", help="Jadval holati (qatorlar soni)")
    db_st.set_defaults(func=cmd_db_status)
    db_sync = db_sub.add_parser("sync", help="BM API → DB sinxronizatsiya")
    db_sync.add_argument("--route", default="", help="routeVariantId (bo'sh = birinchi topilgan)")
    db_sync.add_argument("--date", default="", help="Sana YYYY-MM-DD (bo'sh = bugun)")
    db_sync.add_argument("--from", dest="from_date", default="", help="Waybill boshlanish sana")
    db_sync.add_argument("--to", default="", help="Waybill tugash sana")
    db_sync.add_argument("--what", default="statuses,profiles,routes,drivers,vehicles,duties,trips,waybills",
                         help="Nimalarni sync qilish (vergul bilan): statuses,profiles,routes,drivers,vehicles,duties,trips,waybills")
    db_sync.set_defaults(func=cmd_db_sync)
    db_t = db_sub.add_parser("trips", help="Saqlangan trip'lar ro'yxati")
    db_t.add_argument("--date", default="", help="Sana filteri")
    db_t.add_argument("--route", default="", help="routeId filteri")
    db_t.add_argument("--status", default="", choices=["ACCEPTED", "NOT_ACCEPTED", "PENDING_ACCESS", "APPROVED", "REJECTED", "ZERO_MILEAGE"])
    db_t.add_argument("--limit", type=int, default=50)
    db_t.set_defaults(func=cmd_db_trips)
    db_e = db_sub.add_parser("errors", help="Xatoliklar jurnali")
    db_e.add_argument("--limit", type=int, default=20)
    db_e.set_defaults(func=cmd_db_errors)
    db_r = db_sub.add_parser("runs", help="Avtomatik vazifalar tarixi")
    db_r.add_argument("--limit", type=int, default=20)
    db_r.set_defaults(func=cmd_db_runs)
    db_rp = db_sub.add_parser("reports", help="Hisobotlar tarixi")
    db_rp.add_argument("--limit", type=int, default=20)
    db_rp.set_defaults(func=cmd_db_reports)

    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    args.extra = extra
    try:
        return args.func(args)
    except Exception as exc:
        print(f"XATO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
