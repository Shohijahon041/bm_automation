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
import json
import time
import hashlib
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bm_automation.client import BMClient  # noqa: E402
from bm_automation.app.repositories.duty_repo import DutyRepository  # noqa: E402
from bm_automation.app.notifications.telegram import (  # noqa: E402
    send_document, send_photo, send_message)
from bm_automation.app.utils.tgformat import esc  # noqa: E402

EMOJI = "\U0001F37D\ufe0f"
PROJECT = Path(__file__).resolve().parent
REPORTS = PROJECT / "reports"
STATE_DIR = PROJECT / "state"
_SENT_LOCK = Lock()

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


def _duty_with_retry(client, rid: str, d: datetime.date, label: str,
                     on_wait=None) -> dict:
    """Ertangi kun duty ma'lumotini 404 beruvchi davrda kutib oladi.

    404 (hali e'lon qilinmagan) bo'lsa grafik e'lon qilinguncha
    ``DUTY_RETRY_MINUTES`` oralig'ida **cheksiz** qayta urinadi (22:00
    cheklovi yo'q — bot har doim ishlaydi). Boshqa xatolar darhol
    ko'tariladi. ``on_wait`` — har 404 urinishida chaqiriladigan callback
    (so'rov yuboruvchiga "hali e'lon qilinmagan" holatini bildirish uchun;
    xabar bir marta yuborilishini chaqiruvchi o'zi ta'minlaydi).
    """
    warned = False
    while True:
        try:
            return DutyRepository(client).by_date(rid, d.isoformat())
        except Exception as exc:
            if not _not_found(exc):
                raise
            print(f"[{label}] {d} duty hali e'lon qilinmagan (404) — "
                  f"{DUTY_RETRY_MINUTES} daqiqadan keyin qayta uriniladi...")
            if on_wait:
                try:
                    on_wait()
                except Exception as cb_exc:  # noqa: BLE001
                    print(f"  [{label}] kutish xabari xatosi: {cb_exc}")
            # Grafik kechiksa — kechqurun (DUTY_LATE_WARN_HOUR dan keyin)
            # bog'langan haydovchilarga BIR MARTA eslatma yuboriladi.
            if not warned:
                warned = True
                _warn_if_late(rid, d, label)
            time.sleep(DUTY_RETRY_MINUTES * 60)


# Kun turi -> (B-80 shablon, 10-yo'nalish shablon, sarlavha so'zi)
# ertangi kun duty ma'lumoti odatda kechqurun (20:00-22:00) e'lon qilinadi.
# 18:00 da ishga tushsa 404 qaytaradi — grafik e'lon qilinguncha 15 daqiqada
# qayta urinamiz (hech qanday soat cheklovi yo'q — bot har doim ishlaydi).
DUTY_RETRY_MINUTES = 15

# Grafik kechiksa haydovchilarga eslatma soati (shu soatdan keyin 404 bo'lsa
# bir marta ogohlantiriladi). BM_GRAFIK_LATE_HOUR bilan sozlanadi.
DUTY_LATE_WARN_HOUR = int(os.getenv("BM_GRAFIK_LATE_HOUR", "20"))


def _warn_if_late(rid: str, d: datetime.date, label: str) -> None:
    """Ertangi grafik e'lon qilinmagan bo'lsa kechqurun haydovchilarga xabar.

    Faqat shu soatdan (DUTY_LATE_WARN_HOUR) keyin va kuniga bir marta
    (holat fayli bilan) yuboriladi — spam bo'lmaydi. Grafik e'lon
    qilingandan keyin qayta eslatilmaydi.
    """
    try:
        import json
        from pathlib import Path as _P
        state_file = _P("state") / "grafik_late_warn.json"
        key = f"{rid}|{d.isoformat()}"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if state.get(key):
            return  # bu kun uchun allaqachon ogohlantirilgan
        now = datetime.datetime.now()
        if now.hour < DUTY_LATE_WARN_HOUR:
            return  # hali kechqurun bo'lmadi
        from bm_automation.app.db.storage import get_storage
        st = get_storage()
        if not st.enabled:
            return
        ph = st.db.ph
        rows = st.query(
            "SELECT p.telegram_chat_id, p.notification_enabled, "
            "p.blacklisted, d.full_name "
            "FROM schedules s "
            "JOIN driver_profiles p ON p.driver_id = s.driver_id "
            "LEFT JOIN drivers d ON d.external_id = s.driver_id "
            f"WHERE s.route_id = {ph} AND s.date = {ph} "
            f"AND s.driver_id != ''",
            (rid, d.isoformat()), limit=100)
        sent = 0
        seen = set()
        for r in rows or []:
            tcid = str(r.get("telegram_chat_id") or "").strip()
            if not tcid or tcid in seen:
                continue
            if not r.get("notification_enabled") or r.get("blacklisted"):
                continue
            seen.add(tcid)
            try:
                name = r.get("full_name") or "Haydovchi"
                send_message(
                    f"⏳ <b>Eslatma</b>\n\n"
                    f"{esc(label)} yo'nalishi bo'yicha "
                    f"{d:%d.%m.%Y} (ertaga) kun grafigi hali "
                    f"e'lon qilinmagan.\nGrafik e'lon qilinganda "
                    f"avtomatik yuboriladi.",
                    chat_id=tcid)
                sent += 1
            except Exception:
                pass
        state[key] = now.strftime("%Y-%m-%d %H:%M")
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass
        print(f"  [{label}] kechikish eslatmasi: sent={sent}")
    except Exception as exc:
        print(f"  [{label}] kechikish eslatmasi xatolik: {exc}")

# Rasmni har safar yangidan yaratish (saytdagi o'zgarishlar aks etishi uchun).
# 0 = eski fayl bor bo'lsa qayta yaratilmaydi (tezroq, lekin eskirgan rasm
# yuborilishi mumkin). BM_GRAFIK_REFRESH_IMAGE=0 bilan o'chiriladi.
REFRESH_IMAGE = (os.getenv("BM_GRAFIK_REFRESH_IMAGE", "1").strip() not in
                 ("0", "false", "no"))

# Grafik tayyor bo'lganda bog'langan haydovchilarga o'z shaxsiy grafik
# kartasi (o'z grafigi ajratilgan) avtomatik yuborilsinmi.
# BM_GRAFIK_AUTO_NOTIFY=0 bilan o'chiriladi.
AUTO_NOTIFY = (os.getenv("BM_GRAFIK_AUTO_NOTIFY", "1").strip() not in
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


def _send_personal_cards(res: dict, rid: str, d: datetime.date, label: str,
                         admin_chat_id: str | None,
                         only_driver_ids: set | None = None) -> None:
    """Bog'langan haydovchilarga o'z shaxsiy grafik kartasini yuboradi.

    Har haydovchi faqat O'Z grafigi, vaqti va avtobusi ko'rinadigan
    karta oladi (o'z qatori ajratib ko'rsatilgan). Kartalar
    `reports/<img_dir>/personal/` keshida saqlanadi — haydovchi keyin
    "Grafikim" tugmasi orqali ham qayta ko'rishi mumkin.

    ``only_driver_ids`` berilsa faqat shu haydovchilarga yuboriladi
    (grafik yangilanishida faqat o'zgargan haydovchilarga xabar uchun);
    ``admin_chat_id`` berilmasa admin xulosasi yuborilmaydi.
    """
    if not isinstance(res, dict):
        return
    rows_by_dir = res.get("rows") or {}
    route_name = res.get("route_name") or label
    date_str = res.get("date") or d.isoformat()
    drivers_meta = {r.get("id"): r for r in (res.get("drivers") or [])}

    try:
        from bm_automation.app.db.storage import get_storage
        from bm_automation.app.exporters.sheet_image import (
            make_personal_sheet_image)
        from bm_automation.app.notifications.telegram import send_photo as _sp
        st = get_storage()
        if not st.enabled:
            return
        out_dir = REPORTS / "personal"
        out_dir.mkdir(parents=True, exist_ok=True)
        sent = skipped = failed = 0
        seen: set[str] = set()
        for direction, rows in rows_by_dir.items():
            for row in (rows or []):
                for did in (row.get("driver_id"), row.get("second_driver_id")):
                    did = str(did or "").strip()
                    if not did or did in seen:
                        continue
                    seen.add(did)
                    if only_driver_ids is not None \
                            and did not in only_driver_ids:
                        continue  # yangilanishda faqat o'zgarganlarga
                    prof = st.find("driver_profiles", driver_id=did) or {}
                    tcid = str(prof.get("telegram_chat_id") or "").strip()
                    if not tcid or not prof.get("notification_enabled") \
                            or prof.get("blacklisted"):
                        continue
                    # Ikkinchi haydovchi bo'lsa ismini birlashtiramiz.
                    meta = drivers_meta.get(did) or {}
                    display = dict(row)
                    if row.get("second_driver_id") == did and \
                            row.get("driver_id") and \
                            row.get("driver_id") != did:
                        display["driver"] = meta.get("name") or row.get("driver")
                    png = out_dir / f"personal_{rid[:8]}_{d:%Y%m%d}_{did[:8]}.png"
                    try:
                        if REFRESH_IMAGE or not png.exists():
                            # Haydovchi rasmi + oylik km statistikasi.
                            photo = str(prof.get("photo_path") or "").strip()
                            km_stats = None
                            try:
                                ph = st.db.ph
                                agg = st.query(
                                    "SELECT COALESCE(SUM(distance_plan),0) AS plan_km, "
                                    "COALESCE(SUM(distance_km),0) AS fact_km, "
                                    "COALESCE(SUM(trip_plan),0) AS plan_trips, "
                                    "COALESCE(SUM(trip_count),0) AS fact_trips "
                                    "FROM driver_work_logs WHERE driver_id = " + ph +
                                    " AND substr(date,1,7) = " + ph,
                                    (did, date_str[:7]), limit=1)
                                if agg:
                                    km_stats = {
                                        "plan_km": float(agg[0].get("plan_km") or 0),
                                        "fact_km": float(agg[0].get("fact_km") or 0),
                                        "plan_trips": int(agg[0].get("plan_trips") or 0),
                                        "fact_trips": int(agg[0].get("fact_trips") or 0),
                                    }
                            except Exception:
                                km_stats = None
                            make_personal_sheet_image(
                                display, str(png), date_str=date_str,
                                route_name=route_name,
                                photo_path=photo, km_stats=km_stats)
                        bus_no = str(row.get("bus") or "-").strip()
                        dphone = (st.dispatcher_phone(str(rid))
                                  if rid else "")
                        cap_bits = [f"🚌 <b>{label}</b>",
                                    f"Grafik {row.get('graph') or '-'}",
                                    f"🚍 Avtobus: <b>{bus_no}</b>"]
                        if dphone:
                            cap_bits.append(f"💬 Dispetcher tel: "
                                            f"<code>{dphone}</code>")
                        caption = (f"🗓 <b>SIZNING GRAFIKINGIZ</b> — "
                                   f"{d:%d.%m.%Y}\n" + " · ".join(cap_bits))
                        _sp(str(png), caption=caption, chat_id=tcid)
                        sent += 1
                        print(f"  [{label}] shaxsiy karta yuborildi: "
                              f"{meta.get('name') or did[:8]}")
                    except Exception as exc:
                        failed += 1
                        print(f"  [{label}] shaxsiy karta yuborilmadi "
                              f"({did[:8]}): {exc}")
        if sent or failed:
            print(f"  [{label}] shaxsiy kartalar: sent={sent} failed={failed}")
            if admin_chat_id:
                try:
                    send_message(f"👤 {label}: shaxsiy grafik kartalar — "
                                 f"yuborildi: {sent}, xato: {failed}",
                                 chat_id=admin_chat_id)
                except Exception:
                    pass
    except Exception as exc:
        print(f"  [{label}] shaxsiy kartalar xatolik: {exc}")


def _route_targets(rid: str, chat_id) -> list[str]:
    """Yo'nalish yuboriladigan guruh(lar)i ro'yxati.

    `TG_DRIVER_ROUTE_CHATS` (``route_id:chat1,chat2;route_id:chat3``) —
    har yo'nalish FAQAT o'z guruhiga yuboriladi. Xaritada bo'lmagan
    yo'nalish uchun:
      - ``chat_id`` jo'natuvchi guruh bo'lsa — shu guruhga;
      - lekin u BOSHQA yo'nalishga biriktirilgan (nomlangan) guruh bo'lsa
        — [] qaytadi: boshqa yo'nalish grafigi o'sha guruhga yuborilmaydi;
      - ``chat_id`` bo'lmasa — env'dagi umumiy guruh(lar), biriktirilgan
        guruhlar chiqarib tashlanadi.
    """
    from bm_automation.app.notifications.ops.group_departures import _route_targets
    return _route_targets(rid, chat_id)


def _grafik_sent_state() -> dict:
    """Yuborilgan grafiklar holati (`state/grafik_sent.json`).

    Kalitlar:
      ``<route_id>|<YYYY-MM-DD>``      -> True  (Excel + rasm shu kunga ketdi);
      ``notfound|<route_id>|<date>``   -> True  ("e'lon qilinmagan" eslatmasi bir marta);
      ``error|<route_id>|<date>``      -> True  ("yuborilmadi" xabar etak bir marta).

    `state` katalogi mavjud bo'lmasa bo'sh dict — hech narsa bloklanmaydi.
    """
    if not STATE_DIR.exists():
        return {}
    try:
        sf = STATE_DIR / "grafik_sent.json"
        if sf.exists():
            return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_grafik_sent(state: dict) -> None:
    from bm_automation.app.utils.io import atomic_write
    with _SENT_LOCK:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write(STATE_DIR / "grafik_sent.json",
                         json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"  [!] grafik holat saqlanmadi: {exc}")


def _grafik_was_sent(rid: str, date_str: str) -> bool:
    """Shu (yo'nalish, kun) Excel+rasmi allaqachon yuborilganmi?"""
    key = f"{rid}|{date_str}"
    return bool(_grafik_sent_state().get(key))


def _grafik_sent_info(rid: str, date_str: str) -> dict | None:
    """Yuborilgan grafik yozuvi (hash + haydovchi hash'lari) yoki None.

    Eski formatdagi (``True``) yozuvlar uchun None — chaqiruvchi buni
    "hash hali yo'q" deb qabul qiladi (baseline keyingi tekshiruvda
    yoziladi)."""
    entry = _grafik_sent_state().get(f"{rid}|{date_str}")
    return entry if isinstance(entry, dict) else None


def _duty_fingerprint(duty: dict) -> tuple[str, dict[str, str]]:
    """Duty ma'lumotidan barqaror fingerprint (umumiy + haydovchi bo'yicha).

    Qaytaradi: ``(total_hash, {driver_id: row_hash})``. Har grafik qatori
    ko'rinadigan maydonlaridan (grafik/haydovchi(lar)/davlat raqami/vaqt/
    shift) sha1 olinadi; jami hash — saralangan qator hash'laridan.
    Grafik e'lon qilingandan keyin haydovchi/avtobus/vaqt o'zgarsa hash
    ham o'zgaradi — `grafik_update` agenti shunga qarab qayta yuboradi.
    (Safar soni kirmaydi: kun davomida u o'zgarib turishi mumkin.)
    """
    rows = []
    per_driver: dict[str, str] = {}
    for g in (duty.get("graphs") or []):
        has_second = bool(g.get("hasSecond"))
        item = {
            "g": g.get("graphName") or "",
            "d": g.get("driverName") or "",
            "d2": (g.get("secondDriverName") or "") if has_second else "",
            "did": str(g.get("driverId") or ""),
            "did2": str(g.get("secondDriverId") or "") if has_second else "",
            "p": g.get("plateNum") or "",
            "st": g.get("startTime") or "",
            "en": g.get("endTime") or "",
            "sh": g.get("shiftName") or "",
        }
        row_hash = hashlib.sha1(
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            .encode("utf-8")).hexdigest()[:16]
        rows.append((item["g"], item["st"], row_hash))
        for did in (item["did"], item["did2"]):
            if did:
                per_driver[did] = row_hash
    rows.sort()
    total = hashlib.sha1(
        "|".join(h for _g, _st, h in rows).encode("utf-8")).hexdigest()[:16]
    return total, per_driver


def _grafik_notify_once(rid: str, date_str: str, kind: str) -> bool:
    """Bu (yo'nalish, kun) uchun `kind` eslatma hali yuborilmagan bo'lsa True."""
    key = f"{kind}|{rid}|{date_str}"
    state = _grafik_sent_state()
    if state.get(key):
        return False
    state[key] = True
    _save_grafik_sent(state)
    return True


def _mark_grafik_sent(rid: str, date_str: str, hash: str | None = None,
                      drivers: dict | None = None) -> None:
    """Excel+rasm shu (yo'nalish, kun) uchun yuborilgan deb belgilaydi.

    ``hash``/``drivers`` berilsa yozuv dict ko'rinishida saqlanadi —
    keyinroq `grafik_update` agenti ma'lumot o'zgarganini aniqlab qayta
    yuborishi uchun. Aks holda eski True formati (moslik uchun)."""
    state = _grafik_sent_state()
    if hash:
        state[f"{rid}|{date_str}"] = {
            "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "hash": hash,
            "drivers": drivers or {},
        }
    else:
        state[f"{rid}|{date_str}"] = True
    _save_grafik_sent(state)


# Bir vaqtning o'zida bir xil (yo'nalish, kun) uchun faqat BITTA oqim
# ishlaydi: tugma bir necha marta bosilsa ham Excel/rasm/kartalar takror
# yuborilmaydi (404 kutilayotgan paytda ham). Holat jarayon ichida:
#   _INFLIGHT      — hozir ishlayotgan/kutayotgan yo'nalishlar;
#   _WAIT_NOTIFIED — "hali e'lon qilinmagan" xabari yuborilganlar.
_INFLIGHT_LOCK = Lock()
_INFLIGHT: dict[str, str] = {}
_WAIT_NOTIFIED: set[str] = set()


def _claim_route(rid: str, date_str: str, label: str) -> bool:
    """(yo'nalish, kun) oqimini band qiladi. Allaqachon band bo'lsa False."""
    key = f"{rid}|{date_str}"
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return False
        _INFLIGHT[key] = label
        return True


def _release_route(rid: str, date_str: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(f"{rid}|{date_str}", None)


def _mark_wait_notified(rid: str, date_str: str) -> bool:
    """Kutish xabari birinchi marta yuborilayotgan bo'lsa True."""
    key = f"{rid}|{date_str}"
    with _INFLIGHT_LOCK:
        if key in _WAIT_NOTIFIED:
            return False
        _WAIT_NOTIFIED.add(key)
        return True


def _process_route(route_tuple, client, d, profiles_map, chat_id, title_word,
                   retry_until_published: bool = True):
    """Bitta yo'nalish: takrorlanishni to'sib, ishni himoyalangan bajaradi.

    - grafik allaqachon yuborilgan bo'lsa -> ("already", label, True);
    - shu (yo'nalish, kun) boshqa oqimda kutilyapti/ishlayapti bo'lsa
      -> ("waiting", label, True) — yangi oqim ochilmaydi (takror yuborish
      bo'lmaydi: kechqurun grafik e'lon qilinganda ham bittadan ketadi).
    """
    _out_prefix, rid, _img_dir, label, _tpl_idx = route_tuple
    date_str = d.isoformat()

    # Kuniga bir marta: tayyor grafik takroriy yuborilmaydi.
    if _grafik_was_sent(rid, date_str):
        print(f"  [{label}] {date_str} grafigi bugun allaqachon yuborilgan "
              f"— takrorlanmaydi")
        return [("already", label, True)]

    if not _claim_route(rid, date_str, label):
        print(f"  [{label}] {date_str} — boshqa so'rov allaqachon ishlamoqda "
              f"(tayyor bo'lishi bilan yuboriladi), takroriy oqim ochilmadi")
        return [("waiting", label, True)]

    try:
        return _process_route_run(route_tuple, client, d, profiles_map,
                                  chat_id, title_word, retry_until_published)
    finally:
        _release_route(rid, date_str)


def _process_route_run(route_tuple, client, d, profiles_map, chat_id,
                       title_word, retry_until_published: bool = True):
    """Bitta yo'nalish uchun Excel + rasmni parallel bajaradi (claim ostida)."""
    from bm_automation.app.services.export_service import build_export
    from bm_automation.app.services.driver_sheet_service import run as run_sheet

    out_prefix, rid, img_dir, label, _tpl_idx = route_tuple
    date_str = d.isoformat()
    results = []
    targets = _route_targets(rid, chat_id)

    def on_wait():
        """404 — grafik hali e'lon qilinmaganini so'rov yuboruvchiga bildiradi."""
        if not chat_id or not _mark_wait_notified(rid, date_str):
            return
        send_message(
            f"⏳ <b>{esc(label)}</b>: {d:%d.%m.%Y} kun grafigi hali e'lon "
            f"qilinmagan.\nGrafik e'lon qilinishi bilanoq avtomatik "
            f"yuboriladi (har {DUTY_RETRY_MINUTES} daqiqada tekshiriladi).",
            chat_id=chat_id)

    try:
        if retry_until_published:
            # Ertangi kun — e'lon qilinmaguncha kutamiz (faqat kelajak kun).
            duty = _duty_with_retry(client, rid, d, label, on_wait=on_wait)
        else:
            try:
                duty = DutyRepository(client).by_date(rid, d.isoformat())
            except Exception as exc:
                if not _not_found(exc):
                    raise
                print(f"[{label}] {d} kun grafigi topilmadi (404) — "
                      f"yuborilmadi: {exc}")
                if _grafik_notify_once(rid, date_str, "notfound"):
                    for t in targets:
                        send_message(f"🚏 {label}: {d:%d.%m.%Y} kun uchun "
                                     f"grafik e'lon qilinmagan — "
                                     f"Excel yuborilmadi.", chat_id=t)
                return [("notfound", label, True)]
    except Exception as exc:
        print(f"[{label}] {d} kun grafigi e'lon qilinmagan: {exc}")
        if _grafik_notify_once(rid, date_str, "notfound"):
            for t in targets:
                send_message(f"🚏 {label}: {d:%d.%m.%Y} kun grafigi hali e'lon "
                             f"qilinmagan — Excel yuborilmadi.", chat_id=t)
        found = _not_found(exc)
        return [("notfound" if found else "error", label, found)]

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
        for t in targets:
            send_document(str(excel_file),
                          caption=f"{excel_file.stem}\n{desc} — {title_word} grafigi",
                          chat_id=t)
        if targets:
            print(f"  [{label}] Excel yuborildi: {excel_file.name}")
        else:
            print(f"  [{label}] Excel tayyor, yuboriladigan guruh tanlanmadi")
        results.append(("excel", label, True))
    except Exception as exc:
        print(f"  [{label}] export xatolik: {exc}")
        for t in targets:
            send_message(f"🚏 {label}: Excel yaratishda xatolik — {exc}",
                         chat_id=t)
        results.append(("excel", label, False))

    img = REPORTS / img_dir / f"driver-sheet_{rid[:8]}_{d.strftime('%Y%m%d')}.png"
    res = None
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
            # Faqat jadval rasmi yuboriladi — haydovchi rasmlari albomga
            # qo'shilmaydi. Bog'langan haydovchi o'z grafigini haydovchi
            # kartasidagi "🗓 Grafikim" tugmasi orqali oladi.
            for t in targets:
                send_photo(str(img), caption=desc, chat_id=t)
            if targets:
                print(f"  [{label}] rasm yuborildi: {img.name}")
            else:
                print(f"  [{label}] rasm tayyor, yuboriladigan guruh tanlanmadi")
            results.append(("image", label, True))
            # Grafik tayyor — bog'langan haydovchilarga o'z shaxsiy grafik
            # kartasi avtomatik yuboriladi (xohlaganda o'chiriladi).
            if AUTO_NOTIFY and res:
                _send_personal_cards(res, rid, d, label, chat_id)
        else:
            print(f"  [{label}] rasm topilmadi: {img.name}")
            results.append(("image", label, False))
    except Exception as exc:
        print(f"  [{label}] rasm xatolik: {exc}")
        for t in targets:
            send_message(f"🚏 {label}: rasm yuborishda xatolik — {exc}",
                         chat_id=t)
        results.append(("image", label, False))

    # Kuniga bir marta yuborilishi uchun muvaffaqiyatli jo'natishni belgilaymiz.
    # Yozuvga duty fingerprint ham qo'shiladi — `grafik_update` agenti keyin
    # ma'lumot o'zgarganini shu hash orqali aniqlaydi.
    if targets and ("excel", label, True) in results \
            and ("image", label, True) in results:
        try:
            total_hash, driver_hashes = _duty_fingerprint(duty)
            _mark_grafik_sent(rid, date_str, hash=total_hash,
                              drivers=driver_hashes)
        except Exception as exc:
            print(f"  [{label}] fingerprint saqlanmadi: {exc}")
            _mark_grafik_sent(rid, date_str)

    return results


def _summarize_results(results) -> dict:
    """Oqim natijalarini yo'nalish bo'yicha holat guruhlariga yig'adi."""
    out = {"sent": [], "already": [], "waiting": [],
           "notfound": [], "error": []}
    seen = {k: set() for k in out}
    flags: dict[str, dict] = {}

    def _add(kind: str, label: str) -> None:
        if label not in seen[kind]:
            seen[kind].add(label)
            out[kind].append(label)

    for kind, label, ok in results:
        if kind in ("excel", "image"):
            st = flags.setdefault(label, {})
            st[kind] = st.get(kind, True) and ok
        elif kind in out:
            _add(kind, label)
        elif not ok:
            # Kutilmagan tur (masalan yo'nalish darajasidagi xatolik).
            _add("error", label)
    for label, st in flags.items():
        _add("sent" if (st.get("excel", True) and st.get("image", True))
             else "error", label)
    return out


def _summary_text(summary: dict, d: datetime.date, day_word: str) -> str:
    """So'rov yuboruvchiga ko'rinadigan xulosa matni."""
    lines = [f"📋 <b>GRAFIK XULOSASI</b> — {esc(day_word)} {d:%d.%m.%Y}"]
    if summary.get("sent"):
        lines.append("✅ Yuborildi: " + ", ".join(summary["sent"]))
    if summary.get("already"):
        lines.append("📤 Allaqachon yuborilgan: "
                     + ", ".join(summary["already"]))
    if summary.get("waiting"):
        lines.append("⏳ Jarayonda — tayyor bo'lishi bilan avtomatik "
                     "yuboriladi: " + ", ".join(summary["waiting"]))
    if summary.get("notfound"):
        lines.append("🚏 E'lon qilinmagan — yuborilmadi: "
                     + ", ".join(summary["notfound"]))
    if summary.get("error"):
        lines.append("❌ Xatolik: " + ", ".join(summary["error"]))
    if len(lines) == 1:
        lines.append("Ma'lumot yo'q")
    return "\n".join(lines)


def _send_summary(chat_id, results, d: datetime.date, day_word: str) -> None:
    """So'rov yuboruvchiga ish natijasi xulosasini yuboradi (chat bo'lsa)."""
    if not chat_id:
        return
    send_message(_summary_text(_summarize_results(results), d, day_word),
                 chat_id=chat_id)


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

    # So'rov yuboruvchiga aniq xulosa: nima yuborildi, nima kutilyapti,
    # nima o'tkazib yuborildi — "jimgina tugash" holati bo'lmasin.
    try:
        _send_summary(chat_id, all_results, d, day_label)
    except Exception as exc:
        print(f"  xulosa yuborilmadi: {exc}")

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
