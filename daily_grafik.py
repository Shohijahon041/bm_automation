"""Kunlik avtomatik vazifa: ertangi kun grafiklarini real BM ma'lumoti bilan
to'ldirib Telegram'ga yuborish (B-80, 10-yo'nalish, AND ECO yo'nalishlari).

Kun turi avtomatik aniqlanadi:
  Dush-Chor-Pay-Jum -> ISH KUNI
  Shanba           -> SHANBA
  Yakshanba        -> YAKSHANBA

Windows Task Scheduler orqali har kuni 18:00 da ishga tushiriladi:
    python -X utf8 daily_grafik.py
"""

from __future__ import annotations

import sys
import re
import os
import time
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bm_automation.client import BMClient  # noqa: E402
from bm_automation.app.repositories.duty_repo import DutyRepository  # noqa: E402
from bm_automation.app.notifications.telegram import send_document, send_photo, send_message  # noqa: E402

EMOJI = "\U0001F37D\ufe0f"
PROJECT = Path(__file__).resolve().parent
REPORTS = PROJECT / "reports"

def _not_found(exc) -> bool:
    """404'da 'ma'lumot yo'q' holatini aniqlaydi (xato emas).

    Duty/grafik endpoint'i uchun HAR QANDAY HTTP 404 ``hali e'lon
    qilinmagan`` degani — jismoniy xato emas. BM javob tanasidagi
    ``code``/``message`` turli shaklda bo'lishi mumkin (bo'sh, ``404``,
    ``DUTY_NOT_FOUND`` va h.k.), shuning uchun faqat status 404 bo'lsa
    ``not found`` deb hisoblaymiz. Boshqa xatolar (tarmoq/timeout, 401,
    5xx) o'sha yerda ko'tariladi.
    """
    from bm_automation.app.api.client import BMApiError

    return isinstance(exc, BMApiError) and exc.status == 404


def _duty_with_retry(client, rid: str, d: datetime.date, label: str) -> dict:
    """Ertangi kun duty ma'lumotini 404 beruvchi davrda kutib oladi.

    404 (hali e'lon qilinmagan) bo'lsa grafik e'lon qilinguncha
    ``DUTY_RETRY_MINUTES`` oralig'ida **cheksiz** qayta urinadi (22:00
    cheklovi yo'q — bot har doim ishlaydi). Boshqa xatolar darhol
    ko'tariladi.
    """
    last = None
    while True:
        try:
            return DutyRepository(client).by_date(rid, d.isoformat())
        except Exception as exc:
            if not _not_found(exc):
                raise
            last = exc
            print(f"[{label}] {d} duty hali e'lon qilinmagan (404) — "
                  f"{DUTY_RETRY_MINUTES} daqiqadan keyin qayta uriniladi...")
            time.sleep(DUTY_RETRY_MINUTES * 60)


# Kun turi -> (B-80 shablon, 10-yo'nalish shablon, sarlavha so'zi)
# ertangi kun duty ma'lumoti odatda kechqurun (20:00-22:00) e'lon qilinadi.
# 18:00 da ishga tushsa 404 qaytaradi — grafik e'lon qilinguncha 15 daqiqada
# qayta urinamiz (hech qanday soat cheklovi yo'q — bot har doim ishlaydi).
DUTY_RETRY_MINUTES = 15

# Rasmni har safar yangidan yaratish (saytdagi o'zgarishlar aks etishi uchun).
# 0 = eski fayl bor bo'lsa qayta yaratilmaydi (tezroq, lekin eskirgan rasm
# yuborilishi mumkin). BM_GRAFIK_REFRESH_IMAGE=0 bilan o'chiriladi.
REFRESH_IMAGE = (os.getenv("BM_GRAFIK_REFRESH_IMAGE", "1").strip() not in
                 ("0", "false", "no"))

TEMPLATES = {
    "ISH": (
        r"D:\ASL SUNDAY B-80\GRAFIK ASL SUNDAY B-80\ISH Kuni V3 Grafik.xlsx",
        r"D:\Betonamax 10\GRAFIK 10 Yo'nalishi\ish kuni.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B11\B-11_GRAFIK_ISH KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B7\B-7_GRAFIK_ISH KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_E17\E-17_GRAFIK_ISH KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_D4\D-4_GRAFIK_ISH KUNI.xlsx",
        "ISH KUNI",
    ),
    "SHANBA": (
        r"D:\ASL SUNDAY B-80\GRAFIK ASL SUNDAY B-80\SHANBA KUNI GRAFIK.xlsx",
        r"D:\Betonamax 10\GRAFIK 10 Yo'nalishi\shanba kuni.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B11\B-11_GRAFIK_SHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B7\B-7_GRAFIK_SHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_E17\E-17_GRAFIK_SHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_D4\D-4_GRAFIK_SHANBA KUNI.xlsx",
        "SHANBA",
    ),
    "YAKSHANBA": (
        r"D:\ASL SUNDAY B-80\GRAFIK ASL SUNDAY B-80\Yakshanba Kungi Grafik B-80.xlsx",
        r"D:\Betonamax 10\GRAFIK 10 Yo'nalishi\yakshanba kuni.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B11\B-11_GRAFIK_YAKSHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_B7\B-7_GRAFIK_YAKSHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_E17\E-17_GRAFIK_YAKSHANBA KUNI.xlsx",
        r"D:\AND ECO TRANSPORT\AND_ECO_D4\D-4_GRAFIK_YAKSHANBA KUNI.xlsx",
        "YAKSHANBA",
    ),
}

ROUTES = [
    ("B-80", "fe319a0a-95c5-45f3-8f90-1f3ed8c4e375",
     r"ASL_SUNDAY_MCHJ", "B-80", 0),
    ("10-YO'NALISH", "dfbfbe00-38a2-4ecc-8f3b-15b790308cbc",
     r"FERGANATEX", "10-YO'NALISH", 1),
    ("B-11", "c26557e6-7c0c-431f-a6fb-bda5bf5c4621",
     r"AND_ECO_B11", "B-11", 2),
    ("B-7", "752d6dc5-2311-4657-952b-578ee225ac98",
     r"AND_ECO_B7", "B-7", 3),
    ("E-17", "ba0a3939-9b7e-4a1a-8347-e42c8dfabc0b",
     r"AND_ECO_E17", "E-17", 4),
    ("D-4", "591a9148-b3ca-4b8c-9a88-b5c9b23c762d",
     r"AND_ECO_D4", "D-4", 5),
]


def day_type(d: datetime.date) -> str:
    if d.weekday() == 5:
        return "SHANBA"
    if d.weekday() == 6:
        return "YAKSHANBA"
    return "ISH"


def strip_digits(s: str) -> str:
    return re.sub(r"\d+$", "", s).strip()


def find_blocks(ws):
    """Blok satrlarini topadi. Qaytaradi: [(row, pnum), ...]."""
    blocks = []
    for r in range(1, ws.max_row + 1):
        e = ws.cell(row=r, column=5).value
        if isinstance(e, str) and e.strip().upper().startswith("P"):
            blocks.append((r, e.strip().upper()))
    return blocks


def find_blocks_yak_b80(ws):
    """B-80 yakshanba: A ustunda 'Якшанба куни' + F'da blok raqami."""
    blocks = []
    for r in range(1, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        f = ws.cell(row=r, column=6).value
        if isinstance(a, str) and "Якшанба" in a:
            num = str(f or "").strip()
            if num.isdigit():
                blocks.append((r, f"P{num}"))
    return blocks


def fill_grafik(src, out, prefix, title_word, newdate, by_graph,
                blocks_mode="E", date_col=1):
    import openpyxl
    from openpyxl.styles import Font
    from bm_automation.app.exporters.excel_cells import set_cell

    wb = openpyxl.load_workbook(src, data_only=False)
    ws = wb.worksheets[0]
    title = f"{prefix} GRAFIKI - {title_word} - {newdate:%d.%m.%Y}"

    if blocks_mode == "E":
        blocks = find_blocks(ws)
    else:
        blocks = find_blocks_yak_b80(ws)

    filled = 0
    for r, pnum in blocks:
        info = by_graph.get(pnum) or {}
        set_cell(ws, r, 1, strip_digits(info.get("driver", "")),
                 Font(name="Times New Roman", size=14, bold=True))
        set_cell(ws, r, 6, info.get("plate", ""),
                 Font(name="Times New Roman", size=18, bold=True))
        set_cell(ws, max(r - 1, 1), date_col, newdate)
        if info.get("driver"):
            filled += 1

    set_cell(ws, 1, 1, title)

    replaced = 0
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and "\u25cf" in c.value:
                c.value = c.value.replace("\u25cf", EMOJI)
                replaced += 1

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out))
    return title, len(blocks), filled, replaced


def _process_route(route_tuple, client, d, profiles_map, chat_id, title_word,
                   retry_until_published: bool = True):
    """Bitta yo'nalish uchun Excel + rasmni parallel bajaradi."""
    from bm_automation.app.services.export_service import build_export
    from bm_automation.app.services.driver_sheet_service import run as run_sheet

    out_prefix, rid, img_dir, label, _tpl_idx = route_tuple
    date_str = d.isoformat()
    results = []

    try:
        if retry_until_published:
            # Ertangi kun — e'lon qilinmaguncha kutamiz (faqat kelajak kun).
            duty = _duty_with_retry(client, rid, d, label)
        else:
            try:
                duty = DutyRepository(client).by_date(rid, d.isoformat())
            except Exception as exc:
                if not _not_found(exc):
                    raise
                print(f"[{label}] {d} kun grafigi topilmadi (404) — "
                      f"yuborilmadi: {exc}")
                send_message(f"🚏 {label}: {d:%d.%m.%Y} kun uchun grafik "
                             f"e'lon qilinmagan — Excel yuborilmadi.",
                             chat_id=chat_id)
                return results
    except Exception as exc:
        print(f"[{label}] {d} kun grafigi e'lon qilinmagan: {exc}")
        send_message(f"🚏 {label}: {d:%d.%m.%Y} kun grafigi hali e'lon "
                     f"qilinmagan — Excel yuborilmadi.", chat_id=chat_id)
        return results

    graphs = duty.get("graphs") or []
    print(f"[{label}] duty grafiklar: {len(graphs)}")

    # Grafika tayyor — telefon raqami yozilgan haydovchilarga SMS yuboriladi
    # (Android SMS Gateway). Xato bo'lsa grafik yuborish to'xtamaydi.
    try:
        from bm_automation.app.db.storage import get_storage
        from bm_automation.app.notifications.sms_notify import send_sms_for_graphs
        sms_res = send_sms_for_graphs(get_storage(), graphs, rid, date_str,
                                      route_name=label)
        extra = []
        if not sms_res.get("errors") and sms_res.get("sent") == 0 \
                and sms_res.get("skipped") == 0:
            extra.append("(SMS sozlanmagan)")
        print(f"  [{label}] SMS: sent={sms_res['sent']} skipped={sms_res['skipped']} "
              f"failed={sms_res['failed']} invalid={sms_res['invalid']} "
              f"removed={sms_res['removed']} "
              f"{' '.join(extra)}")
    except Exception as exc:
        print(f"  [{label}] SMS xatolik: {exc}")

    excel_file = None
    try:
        prof = profiles_map.get(rid) or {}
        excel_file = build_export(
            client, rid, date_str,
            out_dir=REPORTS, profile=prof,
            duty_data=duty,
            mech_name=prof.get("mechName") or "",
            dispatcher_name=prof.get("dispatcherName") or "",
        )
        print(f"  [{label}] Excel tayyor: {excel_file.name}")
        desc = f"🚏 {label}"
        if "ASL" in (profiles_map.get(rid) or {}).get("name", ""):
            desc = "🚏 ASL SUNDAY B-80 yo'nalishi"
        elif "FERGANATEX" in (profiles_map.get(rid) or {}).get("name", ""):
            desc = "🚏 10-YO'NALISH (FERGANATEX)"
        send_document(str(excel_file),
                      caption=f"{excel_file.stem}\n{desc} — {title_word} grafigi",
                      chat_id=chat_id)
        print(f"  [{label}] Excel yuborildi: {excel_file.name}")
        results.append(("excel", label, True))
    except Exception as exc:
        print(f"  [{label}] export xatolik: {exc}")
        send_message(f"🚏 {label}: Excel yaratishda xatolik — {exc}",
                     chat_id=chat_id)
        results.append(("excel", label, False))

    img = REPORTS / img_dir / f"driver-sheet_{rid[:8]}_{d.strftime('%Y%m%d')}.png"
    try:
        # HAR DOIM qayta generatsiya: saytda ma'lumot o'zgargan bo'lsa
        # (haydovchi/avtobus almashtirilgan bo'lsa) eski rasm noto'g'ri
        # bo'lib qoladi. Keshdan foydalanish faqat --no-refresh rejimida.
        if REFRESH_IMAGE:
            res = run_sheet(client, rid, date_str=date_str,
                            out_dir=str(REPORTS / img_dir),
                            send=False, profile=profiles_map.get(rid),
                            duty_data=duty)
            img = Path(res["image"])
            print(f"  [{label}] rasm yangidan yaratildi: {img.name}")
        elif not img.exists():
            res = run_sheet(client, rid, date_str=date_str,
                            out_dir=str(REPORTS / img_dir),
                            send=False, profile=profiles_map.get(rid),
                            duty_data=duty)
            img = Path(res["image"])
            print(f"  [{label}] rasm yaratildi: {img.name}")
        if img.exists():
            desc = (f"🚌 {label} | {d:%d.%m.%Y} | Kunlik chiqish jadvali "
                    f"(grafik × haydovchi × avtobus)")
            send_photo(str(img), caption=desc, chat_id=chat_id)
            print(f"  [{label}] rasm yuborildi: {img.name}")
            results.append(("image", label, True))
        else:
            print(f"  [{label}] rasm topilmadi: {img.name}")
            results.append(("image", label, False))
    except Exception as exc:
        print(f"  [{label}] rasm xatolik: {exc}")
        try:
            send_message(f"🚏 {label}: rasm yuborishda xatolik — {exc}",
                         chat_id=chat_id)
        except Exception:
            pass
        results.append(("image", label, False))

    return results


def main(chat_id: str | None = None,
         route_ids: list[str] | None = None,
         day_offset: int = 1):
    """Grafik yuborish.

    route_ids — faqat shu route_id'lar uchun yuboradi (bo'sa = hammasi).
    day_offset — qaysi kun grafigi (0=bugun, 1=ertaga, -1=kechagi).
    Parallel: barcha yo'nalishlar bir vaqtda (Excel + rasm).
    """
    today = datetime.date.today()
    d = today + datetime.timedelta(days=day_offset)  # tanlangan kun
    dt = day_type(d)
    title_word = TEMPLATES[dt][-1]
    day_label = {1: "Ertaga", 0: "Bugun", -1: "Kechagi"}.get(
        day_offset, f"{d:%d.%m.%Y}")
    print(f"Bugun: {today:%d.%m.%Y} | {day_label}: {d:%d.%m.%Y} "
          f"({d:%A}) | {title_word}")
    # Infinite qayta urinish faqat kelajak kuni uchun (grafik e'lon
    # qilinmaguncha kutamiz). O'tgan/bugungi kun uchun 404 — shunchaki
    # o'tkazib yuboriladi.
    retry_until_published = d > today

    active_routes = ROUTES
    if route_ids:
        active_routes = [r for r in ROUTES if r[1] in route_ids]
        if not active_routes:
            send_message("⚠️ Sizga tegishli yo'nalish grafigi topilmadi.",
                         chat_id=chat_id)
            return 0

    client = BMClient()
    client.login()

    from bm_automation.app.core.profiles import all_profiles
    profiles_map = {p.get("routeVariantId", "").strip(): p
                    for p in all_profiles()}

    t0 = time.monotonic()

    max_workers = min(len(active_routes), 6)
    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_route, route, client, d,
                        profiles_map, chat_id, title_word,
                        retry_until_published): route
            for route in active_routes
        }
        for future in as_completed(futures):
            route = futures[future]
            try:
                res = future.result()
                all_results.extend(res)
            except Exception as exc:
                print(f"  [{route[3]}] kutilmagan xatolik: {exc}")
                all_results.append(("route", route[3], False))

    elapsed = time.monotonic() - t0
    ok_count = sum(1 for _, _, ok in all_results if ok)
    total = len(all_results)
    print(f"\nJami: {ok_count}/{total} muvaffaqiyatli ({elapsed:.1f}s)")

    try:
        send_stats(client, today, chat_id,
                   route_ids=[r[1] for r in active_routes])
    except Exception as exc:
        print(f"  statistika yuborilmadi: {exc}")

    return 0


def _stats_one_profile(p, ds, chat_id, stats_date):
    """Bitta profil uchun statistikani yuboradi (thread-safe)."""
    from bm_automation.app.services.stats_service import run as stats_run
    from bm_automation.app.core.companies import client_for_profile

    rid = str(p.get("routeVariantId", "") or "").strip()
    if not rid:
        return
    try:
        c = client_for_profile(p)
        res = stats_run(c, rid, ds, ds, title_word=stats_date.strftime("%A"))
        send_message(res["text"], chat_id=chat_id)
        print(f"  OK [{p.get('name')}]: statistika yuborildi")
    except Exception as exc:
        print(f"  XATO [{p.get('name')}] statistika: {exc}")


def send_stats(client, stats_date: datetime.date, chat_id: str | None = None,
               route_ids: list[str] | None = None):
    """Har yo'nalish bo'yicha byBus umumiy statistikani Telegram'ga yuboradi.

    Har kompaniya uchun o'z clienti ishlatiladi (o'z kredensiallari bo'lsa
    alohida token; bo'lmasa admin tokeni + login_by_profile).
    route_ids — faqat shu route_id'lar uchun (bo'sa = hammasi).
    Parallel bajariladi.
    """
    from bm_automation.app.core.profiles import all_profiles

    ds = stats_date.isoformat()
    print(f"\nStatistika (byBus) {ds}:")

    profiles = []
    for p in all_profiles():
        rid = str(p.get("routeVariantId", "") or "").strip()
        if not rid:
            continue
        if route_ids and rid not in route_ids:
            continue
        profiles.append(p)

    if not profiles:
        return

    with ThreadPoolExecutor(max_workers=min(len(profiles), 4)) as pool:
        futures = [
            pool.submit(_stats_one_profile, p, ds, chat_id, stats_date)
            for p in profiles
        ]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                print(f"  statistika xatolik: {exc}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        try:
            send_message("Grafik yuborishda xatolik:\n" + traceback.format_exc())
        except Exception:
            pass
        raise
