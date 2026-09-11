"""Kunlik avtomatik vazifa: haydovchilar jadvali + oylik yig'ma yuborish.

Haydovchilar jadvali bir kun oldin tayyorlanadi: bugun 8 bo'lsa, ertangi
(9-kun) grafigi bugun yuboriladi. Har kuni soat 20:00 da barcha profillar
uchun:
  - haydovchilar ertangi kun chiqish jadvali (rasm) -> Telegram
  - oy boshidan bugungacha oylik yig'ma Excel -> Telegram

Hammasi yuborilmaguncha qayta-qayta uriniladi (default 10 marta, oralig'i 10
daqiqa). Xatolik bo'lsa Telegram'ga xabar ham yuboriladi.

Windows Task Scheduler orqali ishga tushiriladi:
    python -m bm_automation daily

--offset 0 bilan bugungi, --offset 1 bilan ertangi (standart) kun yuboriladi.
--no-send bilan faqat yaratib tekshirish mumkin.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from ..api.client import BMClient
from ..core.profiles import all_profiles
from ..core.state import record_run
from ..db.sync import AutomationLogger
from ..notifications.telegram import (resend_keyboard, send_document,
                                      send_message)
from ..services.driver_sheet_service import run as run_sheet
from ..services.summary_service import run as run_summary
from ..utils.io import safe_name


def _auth_error(results: list) -> bool:
    """401/avtorizatsiya xatosi borligini aniqlaydi."""
    return any(
        "avtorizatsiya" in str(r.get(key, "")) or "HTTP 401" in str(r.get(key, ""))
        for r in results
        for key in ("error", "monthly_error")
    )


def _has_failures(results: list) -> bool:
    """Biron element muvaffaqiyatsiz/jo'natilmagan bo'lsa True."""
    if not results:
        return True
    for r in results:
        if (r.get("error") or r.get("monthly_error")
                or r.get("driver_notification_error") or r.get("driver_sms_error")):
            return True
        if r.get("image") and not r.get("sent"):
            return True
        if r.get("monthly_excel") and not r.get("monthly_sent"):
            return True
    return False


def _fresh_login(retries: int = 3) -> bool:
    """OneID orqali yangi login. Birinchi muvaffaqiyatda qaytadi."""
    from ..auth.browser_login import browser_login

    for attempt in range(1, retries + 1):
        try:
            print(f"OneID login urinishi {attempt}/{retries}...")
            browser_login(headless=True, timeout=300)
            print("OneID login muvaffaqiyatli.")
            return True
        except Exception as exc:
            print(f"OneID login xatosi ({attempt}): {exc}")
            if attempt < retries:
                time.sleep(15 * attempt)
    return False


def _notify_summary(results: list, chat_id: str | None, sheet_date: date) -> None:
    """Bajarilish xulosasini Telegram'ga yuboradi (xatoliklar bilan)."""
    lines = [f"Kunlik hisobot | {sheet_date:%d.%m.%Y}"]
    any_error = False
    for r in results:
        name = r.get("profile", "?")
        parts = []
        if r.get("sent"):
            parts.append("jadval ✓")
        elif r.get("error"):
            parts.append(f"jadval XATO: {r['error']}")
        if r.get("monthly_sent"):
            parts.append("oylik ✓")
        elif r.get("monthly_error"):
            parts.append(f"oylik XATO: {r['monthly_error']}")
        if r.get("driver_notifications"):
            n = r["driver_notifications"]
            try:
                from ..notifications.driver_schedule import _delivery_summary
                parts.append("\n" + _delivery_summary(n).replace("\n", "\n  "))
            except Exception:  # noqa: BLE001
                parts.append(f"haydovchi eslatma: {n.get('sent', 0)} yuborildi, "
                             f"{n.get('failed', 0)} xato")
        elif r.get("driver_notification_error"):
            parts.append(f"eslatma XATO: {r['driver_notification_error']}")
        if r.get("driver_sms"):
            sms = r["driver_sms"]
            parts.append(f"SMS: {sms.get('sent', 0)} yuborildi, "
                         f"{sms.get('failed', 0)} xato, "
                         f"{sms.get('invalid', 0)} raqamsiz, "
                         f"{sms.get('removed', 0)} chiqarilgan")
        elif r.get("driver_sms_error"):
            parts.append(f"SMS XATO: {r['driver_sms_error']}")
        if not parts:
            parts.append("ma'lumot yo'q")
        status = "; ".join(parts)
        lines.append(f"  {name}: {status}")
        if r.get("error") or r.get("monthly_error") or r.get("driver_notification_error") \
                or r.get("driver_sms_error"):
            any_error = True
    try:
        send_message("\n".join(lines), chat_id=chat_id,
                     reply_markup=resend_keyboard(None))
    except Exception as exc:
        print(f"Telegram xulosa yuborilmadi: {exc}")
    if any_error:
        print("Xulosa: ba'zi elementlar yuborilmadi (yuqoridagi log'ga qarang).")
    else:
        print("Xulosa: barchasi muvaffaqiyatli yuborildi.")


def _month_start(month_offset: int) -> date:
    today = date.today()
    month_start = today.replace(day=1)
    if month_offset:
        m = month_start.month - month_offset
        y = month_start.year
        if m < 1:
            m += 12
            y -= 1
        month_start = date(y, m, 1)
    return month_start


def run_daily(send: bool = True, chat_id: str | None = None, month_offset: int = 0,
              sheet_offset: int = 1, fresh_login: bool = True,
              max_attempts: int = 10, attempt_delay: int = 600,
              only: str | list[str] | None = None, routes: list[str] | None = None,
              trigger: str = "auto") -> list:
    """Barcha profillar uchun kunlik vazifani bajaradi.

    Hammasi yuborilmaguncha (max_attempts marta) qayta urinadi. Har urinishda
    yangi OneID login olinadi. Yakunda Telegram'ga xulosa xabari yuboriladi.
    """
    today = date.today()
    sheet_date = today + timedelta(days=sheet_offset)
    month_start = _month_start(month_offset)

    logger = AutomationLogger()
    run_id = logger.start(
        trigger=trigger,
        sheet_date=sheet_date.isoformat(),
        month=f"{month_start:%Y-%m-%d}..{today:%Y-%m-%d}",
    )

    results = []
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            wait = attempt_delay
            print(f"\n[{time.strftime('%H:%M:%S')}] {wait//60} daqiqa kutib, "
                  f"qayta urinish {attempt}/{max_attempts}...")
            time.sleep(wait)
        if fresh_login and not _fresh_login():
            print("Yangi login olinmadi. Saqlangan tokenlar bilan uriniladi.")
        try:
            results = run_daily_once(send=send, chat_id=chat_id,
                                     month_offset=month_offset, sheet_offset=sheet_offset,
                                     only=only, routes=routes)
        except Exception as exc:
            results = [{"error": str(exc)}]
        if not _has_failures(results):
            break

    logger.finish(run_id, results)
    for r in results:
        for key in ("error", "monthly_error"):
            if r.get(key):
                logger.error(source="daily", exc=str(r[key]),
                             context={"profile": r.get("profile")})

    if send:
        record_run(results, sheet_date=sheet_date.isoformat(),
                   month=f"{month_start:%Y-%m-%d}..{today:%Y-%m-%d}", trigger=trigger)
        _notify_summary(results, chat_id, sheet_date)
    return results


def _matches_only(only, name: str) -> bool:
    """`only` filtriga to'g'ri keladimi.

    `only` — bitta nom (str) yoki nomlar ro'yxati (list). Ro'yxat bo'lsa,
    ulardan biri kompaniya nomiga mos tushsa True. Masalan dispetcherga
    biriktirilgan yo'nalish(lar) bo'yicha bir nechta kompaniyani qayta
    yuborish uchun.
    """
    if not only:
        return True
    name_lower = str(name or "").lower()
    if isinstance(only, (list, tuple, set)):
        return any(o and str(o).lower() in name_lower for o in only)
    return str(only).lower() in name_lower


def run_daily_once(send: bool = True, chat_id: str | None = None, month_offset: int = 0,
                   sheet_offset: int = 1, only: str | list[str] | None = None,
                   routes: list[str] | None = None) -> list:
    """Barcha profillar uchun kunlik vazifani bajaradi (bir martalik).

    `only` — bitta kompaniya nomi (str) yoki nomlar ro'yxati (list).
    `routes` — aniq route_id'lar ro'yxati; berilsa faqat shu yo'nalishlar
    bajariladi (masalan dispetcherga biriktirilgan yo'nalishlar bo'yicha).
    Berilmasa barcha profillar bajariladi.
    """
    client = BMClient()
    client.login()
    main_access = client.access_token
    main_refresh = client.refresh_token

    def use_main():
        client.access_token = main_access
        client.refresh_token = main_refresh
        client.session.headers["Authorization"] = f"Bearer {main_access}"

    today = date.today()
    sheet_date = today + timedelta(days=sheet_offset)
    month_start = _month_start(month_offset)

    if routes:
        routes = [str(r or "").strip() for r in routes if str(r or "").strip()]

    results = []
    for p in all_profiles():
        name = p.get("name")
        if not _matches_only(only, name):
            continue
        route = str(p.get("routeVariantId", "") or "").strip()
        if routes:
            p_rids = [t.strip() for t in route.replace(",", " ").split() if t.strip()]
            if not any(r in routes for r in p_rids):
                continue
        pid = str(p.get("profileId", "") or "").strip()
        entry = {"profile": name}
        base = str(Path("reports") / safe_name(name))
        if not route:
            entry["error"] = "routeVariantId ko'rsatilmagan"
            print(f"  XATO [{name}]: routeVariantId ko'rsatilmagan")
            results.append(entry)
            continue

        # Profil tokeni (kompaniya). FERGANATEX'da profileId bo'sh -> asosiy token.
        try:
            if pid:
                client.login_by_profile(pid)   # faqat xotirada, tokens.json buzilmaydi
            else:
                use_main()
        except Exception as exc:
            entry["error"] = f"kompaniya tokeni olinmadi: {exc}"
            print(f"  XATO [{name}] token: {exc}")
            results.append(entry)
            continue

        # 1) Ertangi kun jadvali (rasm)
        try:
            sheet = run_sheet(client, route, date_str=sheet_date.isoformat(),
                              out_dir=base, send=send, chat_id=chat_id, profile=p)
            entry["image"] = sheet["image"]
            entry["sent"] = sheet.get("sent", False)
            print(f"  OK [{name}]: jadval {sheet['image']}")
        except Exception as exc:
            entry["error"] = str(exc)
            print(f"  XATO [{name}] jadval: {exc}")

        # Ertangi grafik tayyor bo'lgach, shaxsiy Telegram chat ID kiritilgan
        # haydovchilarga o'z smenasi yuboriladi. DB jurnalidagi tekshiruv
        # daily task qayta uringanda bir xil xabarni qayta yubormaydi.
        if send and not entry.get("error"):
            try:
                from ..db.storage import get_storage
                from ..db.sync import sync_duties
                from ..notifications.driver_schedule import send_driver_schedule_notifications

                storage = get_storage()
                synced = sync_duties(storage, client, route, sheet_date.isoformat())
                if synced.error:
                    raise RuntimeError(synced.error)
                notifications = send_driver_schedule_notifications(
                    storage, route, sheet_date.isoformat())
                entry["driver_notifications"] = notifications
                if notifications["failed"]:
                    entry["driver_notification_error"] = "; ".join(notifications["errors"])
            except Exception as exc:
                entry["driver_notification_error"] = str(exc)
                print(f"  XATO [{name}] haydovchi eslatmasi: {exc}")

        # Ertangi grafik tayyor bo'lgach, telefon raqami yozilgan
        # haydovchilarga SMS xabarnoma yuboriladi (Android SMS Gateway).
        # Xato bo'lsa jarayon to'xtamaydi — faqat yozuv qayd etiladi.
        if send and not entry.get("error"):
            try:
                from ..notifications.sms_notify import send_driver_schedule_sms
                sms = send_driver_schedule_sms(
                    storage, sheet_date.isoformat(), route_id=route)
                entry["driver_sms"] = sms
                if sms.get("errors"):
                    entry["driver_sms_error"] = "; ".join(sms["errors"][:5])
            except Exception as exc:
                entry["driver_sms_error"] = str(exc)
                print(f"  XATO [{name}] haydovchi SMS: {exc}")

        # 2) Oylik yig'ma (Excel) — ma'lumot bo'lsagina
        try:
            sm = run_summary(client, route, from_date=month_start.isoformat(),
                             to_date=today.isoformat(), out_dir=base,
                             download_missing=True)
            entry["monthly_excel"] = sm["excel_file"]
            if sm.get("days_with_duty", 0) == 0:
                entry["monthly_error"] = "Oylik ma'lumot topilmadi (0 kun)"
                print(f"  XATO [{name}] oylik: ma'lumot topilmadi (0 kun)")
            else:
                if send:
                    caption = (f"📊 {name} | Oylik yig'ma hisobot "
                               f"({month_start:%d.%m.%Y} - {today:%d.%m.%Y})")
                    send_document(sm["excel_file"], caption=caption, chat_id=chat_id,
                                  reply_markup=resend_keyboard(name))
                    entry["monthly_sent"] = True
                print(f"  OK [{name}]: oylik {sm['excel_file']}")
        except Exception as exc:
            entry["monthly_error"] = str(exc)
            print(f"  XATO [{name}] oylik: {exc}")

        results.append(entry)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bm_automation daily",
                                     description="Kunlik jadval + oylik yig'ma yuborish")
    parser.add_argument("--no-send", action="store_true", help="Yubormasdan faqat yaratish")
    parser.add_argument("--chat", default="", help="Telegram chat ID (bo'sh = .env)")
    parser.add_argument("--month-offset", type=int, default=0,
                        help="0=joriy oy, 1=o'tgan oy")
    parser.add_argument("--offset", type=int, default=1,
                        help="Qaysi kunga jadval: 0=bugun, 1=ertaga (standart)")
    parser.add_argument("--attempts", type=int, default=10,
                        help="Qayta urinishlar soni (hammasi yuborilmaguncha)")
    parser.add_argument("--delay", type=int, default=600,
                        help="Urinishlar orasidagi kutish (sekund)")
    parser.add_argument("--no-fresh-login", action="store_true",
                        help="OneID qayta loginsiz (saqlangan token bilan)")
    parser.add_argument("--only", default="", help="Faqat shu profil uchun")
    parser.add_argument("--trigger", default="auto", help="Ish manbai: auto/manual/bot")
    args = parser.parse_args(argv)
    results = run_daily(send=not args.no_send,
                        chat_id=args.chat or None,
                        month_offset=args.month_offset,
                        sheet_offset=args.offset,
                        fresh_login=not args.no_fresh_login,
                        max_attempts=args.attempts,
                        attempt_delay=args.delay,
                        only=args.only or None,
                        trigger=args.trigger)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
