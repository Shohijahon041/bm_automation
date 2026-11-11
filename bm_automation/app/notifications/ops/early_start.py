"""Qatnov rejadan sezilarli erta boshlanganda favqulodda ogohlantirish.

Manba — BM'ning o'z bahosi: ``trip-report`` sahifasidagi
``status=NOT_ACCEPTED`` + ``qualityType=SUITABLE_PLAN_TIME_QUALITY`` filtri,
ya'ni waybill qatorida ``brokenQualities`` ichida
``SUITABLE_PLAN_TIME_QUALITY`` borligi. Shu bayroq asosiy aniqlash yo'li:
BM reja vaqtini o'zi hisoblab "erta boshlangan" deb belgilagan qatnovlar
aynan shu ro'yxatga tushadi.

Reja oraliqlari ``shifts/graph/times`` dan olinadi: ``index`` global
(1..totalTrips) va waybill'dagi ``tripNum`` ga to'g'ri keladi. START,
BETWEEN va LUNCH (nonushta/tushlik) slotlari reys hisoblanadi; faqat END va
``index`` yo'q joy egallovchilar o'tkazib yuboriladi. Har bir slot
``arrivalTime``–``departureTime`` oralig'i sifatida ishlatiladi: qatnov
oraliqdan oldin boshlansa — erta, oraliqdan keyin boshlansa — kech.

Bayroq bo'lmasa, reja oraliqi zaxira yo'l sifatida ishlaydi (BM bayrog'i
kechikib kelganda ham ogohlantirish ketadi).

Ogohlantirish uch kanalga boradi (favqulodda holat — guruh sozlamasi
istisno qilinadi):

1. haydovchining o'z korxonasi guruhi — ``GROUP_DEPARTURE_NOTIFY=off``
   yoki guruhda ``/chiqish_off`` bo'lsa ham (yagona istisno);
2. haydovchi telefoni — SMS (kunlik 2 ta limitni egallamaydi:
   ``schedule_date=""``);
3. haydovchi profili — Telegram DM (``driver_profiles.telegram_chat_id``).

Har bir kanal alohida kuzatiladi: muvaffaqiyatli kanal takrorlanmaydi,
faqat xato bo'lgan kanal keyingi siklda qayta uriniladi (3 martagacha).

Holat ``state/early_start.json``: kunlik reja keshi va yuborilgan
kalitlar (``sana|shiftGraphId|tripNum``) — bitta hodisa uchun bitta xabar.
Barcha kanallar muvaffaqiyatsiz bo'lsa, hodisa 3 urinishdan keyin
"yuborilgan" deb belgilanadi (cheksiz spam bo'lmasligi uchun).

Alertdan keyin reys natijasi kuzatiladi (``followup``): BM waybill'ida
``tripStatus`` ``ACCEPTED``/``APPROVED`` bo'lsa guruh + haydovchi profiliga
"Reys qabul qilindi" ketadi. Qatnov tugagach ``early_start_followup_min``
daqiqa o'tib ham qabul qilinmagan bo'lsa — guruh, SMS va profilga
"Reys qabul qilinmadi — haydovchi aybi" eslatmasi yuboriladi.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from ...core import bot_settings
from ...core.profiles import all_profiles
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.logger import get_logger
from ...utils.names import short_name
from ...utils.tgformat import esc

log = get_logger(__name__)

STATE_FILE = Path("state") / "early_start.json"

# Shu oynadan (daqiqa) eski boshlanishlar uchun ogohlantirilmaydi —
# bot uzoq to'xtab qolganda o'tmishdagi reyslar uchun spam bo'lmasin.
_START_WINDOW_MIN = 120
# Reja qatnov boshlanishidan shu daqiqa oldin oldindan yuklanadi: birinchi
# erta qatnovda `shifts/graph/times` kutish vaqti alertni kechiktirmasin.
_PLAN_PREFETCH_MIN = 30
# Bitta reja (grafik) uchun API so'rovlari parallel bajariladi.
_PLAN_WORKERS = 8
# Profillar bo'yicha waybill so'rovlari parallel bajariladi. Har bir profilga
# alohida oqim: sekin kompaniya (masalan AND ECO ConnectTimeout) qolganlarning
# siklini ushlab turmaydi. 0.3 s stagger bir vaqtda urilishini yumshatadi.
_FETCH_WORKERS = 8
_FETCH_STAGGER_S = 0.3
# Bitta sekin profil butun tekshiruvni bloklamasligi uchun timeout:
# javob bermagan profil o'tkazib yuboriladi, keyingi siklda qayta so'raladi.
# Jonli o'lchov: BM waybill javobi odatda 1–3 s, lekin yuklama ostida
# 5–25 s gacha cho'ziladi. 4 s bilan qatnovlar butunlay o'tkazib
# yuborilardi — 10 s real javoblarning aksariyatini qamrab oladi.
_FETCH_TIMEOUT_S = 10.0
# Xato bo'lsa keyingi 5 s siklni kutmasdan darhol bir marta qayta uriniladi.
_FETCH_RETRY_DELAY_S = 0.5
_PLAN_TIMEOUT_S = 8.0
# Muvaffaqiyatsiz urinishlar shu sondan keyin to'xtatiladi.
_MAX_ATTEMPTS = 3
# Eski ``sent`` kalitlarini saqlash muddati (soniya).
_KEEP_SENT_S = 2 * 86400
# Reja slotlaridan faqat END (smena oxiri) chiqariladi — qolganlari, jumladan
# LUNCH (nonushta/tushlik), reys hisoblanadi va global indeksga kiradi.
_SKIP_SLOT_TYPES = {"END"}
# BM'ning "erta boshlangan" bahosi (trip-report qualityType filtri).
_BM_EARLY_QUALITY = "SUITABLE_PLAN_TIME_QUALITY"
# Panel hisoboti keshi: bir necha marta so'ralganda BM API'ga qayta urilmasin.
_REPORT_CACHE_TTL_S = 60.0
_PLAN_CACHE_TTL_S = 600.0
_REPORT_CACHE: dict[tuple, tuple[float, dict]] = {}
_PLAN_CACHE: dict[tuple, tuple[float, dict[str, dict]]] = {}
_REPORT_LOCK = threading.Lock()

_LOCK = threading.Lock()
_PLAN_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0
_LAST_PRUNE_AT = 0.0
_RUNNING = False
_WORKER_STARTED = False
# Client qurilmasa (masalan BM parol o'zgargan) har 5 s login urinilmaydi:
# xato turiga qarab profil shu vaqtgacha o'tkazib yuboriladi.
_CLIENT_FAIL_UNTIL: dict[str, float] = {}
_CLIENT_FAIL_AUTH_S = 600.0
_CLIENT_FAIL_OTHER_S = 60.0


def enabled() -> bool:
    return bot_settings.early_start_notify()


# ------------------------------------------------------------------ holat

def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("date", "")
            data.setdefault("plans", {})
            data.setdefault("sent", {})
            data.setdefault("attempts", {})
            data.setdefault("followup", {})
            data.setdefault("delivered", {})
            return data
    except (OSError, ValueError):
        pass
    return {"date": "", "plans": {}, "sent": {}, "attempts": {},
            "followup": {}, "delivered": {}}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - kuzatuvni buzmaydi
        log.warning("erta qatnov holati saqlanmadi: %s", exc)


def _mutate(fn) -> dict:
    with _LOCK:
        st = _load()
        fn(st)
        _save(st)
        return st


def _reset_if_new_day(state: dict, today: str) -> None:
    if state.get("date") != today:
        state.clear()
        state.update({"date": today, "plans": {}, "sent": {}, "attempts": {},
                      "followup": {}, "delivered": {}})


# ---------------------------------------------------------------- vaqtlar

def _parse_dt(value) -> datetime | None:
    """BM vaqtini (lokal, tz'siz) ``datetime`` ga aylantiradi."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return dt.replace(tzinfo=None)


def _sec_of_day(value) -> int | None:
    """``HH:MM[:SS]`` → kunning boshidan soniya."""
    parts = str(value or "").strip()[:8].split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    sec = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + sec


def _fmt_hhmm(sec: int) -> str:
    sec = int(sec) % 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"


def _delta_sec(actual: int, planned: int) -> int:
    """Kun chegarasini hisobga olgan holda farq (musbat — kech).

    18 soatdan kichik farq kun ichida qoladi; faqat yarim tunga yaqin
    reja/amalda juftligi aylantiriladi. Aks holda kechki reys ertalabki
    rejaga nisbatan "erta" bo'lib ko'rinardi.
    """
    d = actual - planned
    if d > 64800:
        d -= 86400
    elif d < -64800:
        d += 86400
    return d


# ----------------------------------------------------------- BM ma'lumoti

def _fresh_client():
    """Saqlangan token bilan client (brauzer loginsiz)."""
    try:
        from .group_departures import _fresh_client as fresh
        return fresh()
    except Exception:  # noqa: BLE001
        return None


def _profile_client(profile: dict):
    """Kompaniya uchun client: o'z tokeni bo'lsa shu, aks holda admin+profileId.

    Brauzer loginiga tushmaslik uchun ``auto_relogin=False`` — fon oqimi
    5 soniyalik siklini bloklab qo'ymasligi kerak. Kredensial xatosi
    (401/403) takrorlansa BM'ga har 5 s login urinilmaydi.
    """
    name = str(profile.get("name") or "")
    now = time.monotonic()
    if _CLIENT_FAIL_UNTIL.get(name, 0.0) > now:
        return None
    try:
        from ...core.companies import client_for_profile
        client = client_for_profile(profile, auto_relogin=False)
        _CLIENT_FAIL_UNTIL.pop(name, None)
        return client
    except Exception as exc:  # noqa: BLE001 - bitta kompaniya qolganini to'smasin
        text = str(exc)
        auth = "HTTP 401" in text or "HTTP 403" in text
        cooldown = _CLIENT_FAIL_AUTH_S if auth else _CLIENT_FAIL_OTHER_S
        _CLIENT_FAIL_UNTIL[name] = now + cooldown
        log.warning("erta qatnov: %s client xatosi: %s (%.0f daqiqadan keyin qayta urinish)",
                    name, text[:120], cooldown / 60)
        return None


def _flat(row: dict) -> dict:
    data = dict(row or {})
    inner = data.get("data")
    if isinstance(inner, dict):
        data = {**data, **inner}
    return data


def _fetch_rows(today: str, route_ids: list[str] | None = None,
                errors: list | None = None,
                clients_out: dict | None = None) -> list[dict]:
    """Profillar bo'yicha waybill qatorlari (parallel, ixtiyoriy route filtri).

    Har bir profil O'Z tokeni bilan so'raladi (ko'p korxonali rejim).
    ``errors`` berilsa, yuklanmagan profil nomi/xatosi shu ro'yxatga yoziladi —
    hisobot qisman ma'lumot bilan qolganini yashirmaydi. ``clients_out``
    berilsa, routeVariantId -> client xaritasi to'ldiriladi (rejalar uchun).
    """
    from ...repositories.waybill_repo import WaybillRepository

    profiles = [p for p in all_profiles() if p.get("routeVariantId")]
    if route_ids:
        wanted = {str(r).strip() for r in route_ids if str(r).strip()}
        profiles = [p for p in profiles
                    if str(p.get("routeVariantId")) in wanted]
    if not profiles:
        return []

    out: list[dict] = []
    lock = threading.Lock()
    seq = itertools.count()

    def work(profile):
        # Bir vaqtda yuborilgan 7 so'rovdan bittasi osilib qolmasligi uchun
        # kichik bosqichma-bosqich kechikish.
        time.sleep(next(seq) * _FETCH_STAGGER_S)
        rid = str(profile.get("routeVariantId") or "")
        client = _profile_client(profile)
        if client is None:
            if errors is not None:
                with lock:
                    errors.append({"profile": str(profile.get("name") or ""),
                                   "error": "BM tokeni olinmadi"})
            return
        if clients_out is not None:
            with lock:
                clients_out[rid] = client
        try:
            data = WaybillRepository(client).report(
                rid, today, today, timeout=_FETCH_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - bitta profil qolganini to'smasin
            log.warning("erta qatnov: %s waybill xatosi: %s",
                        profile.get("name"), str(exc)[:120])
            if errors is not None:
                with lock:
                    errors.append({"profile": str(profile.get("name") or ""),
                                   "error": str(exc)[:160]})
            return
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            if errors is not None:
                with lock:
                    errors.append({"profile": str(profile.get("name") or ""),
                                   "error": "javob shakli noto'g'ri"})
            return
        flat = [_flat(r) for r in rows if isinstance(r, dict)]
        with lock:
            out.extend(flat)

    with ThreadPoolExecutor(
            max_workers=min(_FETCH_WORKERS, len(profiles))) as pool:
        list(pool.map(work, profiles))
    return out


def _graph_plan(client, shift_graph_id: str) -> dict[str, dict]:
    """Bitta grafik rejasi: global ``index`` → vaqt oralig'i.

    ``shifts/graph/times`` ikkala yo'nalish slotlarini bitta javobda beradi;
    ``index`` global (1..totalTrips) va waybill'dagi ``tripNum`` ga teng.
    START/BETWEEN/LUNCH slotlari reys hisoblanadi (nonushta/tushlik ham),
    faqat END va ``index`` yo'q joy egallovchilar o'tkazib yuboriladi.

    Natija: ``{"arr": soniya, "dep": soniya, "type": "START"}`` — kalit
    ``str(index)``. ``arr`` bo'sh bo'lsa ``dep`` ga tenglashtiriladi.
    """
    from ...repositories.duty_repo import DutyRepository

    data = DutyRepository(client).shift_graph_times(
        shift_graph_id, timeout=_PLAN_TIMEOUT_S, retries=0)
    if not isinstance(data, dict):
        return {}
    plan: dict[str, dict] = {}
    for key in ("upShiftTimes", "downShiftTimes"):
        part = data.get(key)
        if not isinstance(part, list):
            continue
        for slot in part:
            if not isinstance(slot, dict):
                continue
            try:
                index = int(slot.get("index"))
            except (TypeError, ValueError):
                continue
            slot_type = str(slot.get("type") or slot.get("slotType")
                            or "").strip().upper()
            if slot_type in _SKIP_SLOT_TYPES:
                continue
            arr = _sec_of_day(slot.get("arrivalTime"))
            dep = _sec_of_day(slot.get("departureTime"))
            if arr is None and dep is None:
                continue
            plan[str(index)] = {
                "arr": arr if arr is not None else dep,
                "dep": dep if dep is not None else arr,
                "type": slot_type,
            }
    return plan


def _ensure_plans(client, sgids: list[str], state: dict, today: str) -> None:
    """Bugungi rejalar keshi — faqat yetishmayotgan grafiklar uchun so'rov.

    Bir nechta profil oqimi bir vaqtda chaqirishi mumkin: ``_PLAN_LOCK``
    bitta grafik uchun ``shifts/graph/times`` ikki marta so'ralmasligini
    ta'minlaydi (qulf ichida keshi yangilanib qayta tekshiriladi).
    """
    with _PLAN_LOCK:
        fresh = _load()
        _reset_if_new_day(fresh, today)
        # Eski formatdagi (list) keshlar ham qayta so'raladi.
        missing = [s for s in sgids
                   if not isinstance((fresh["plans"] or {}).get(s), dict)]
        if not missing:
            return

        def work(sgid):
            try:
                return sgid, _graph_plan(client, sgid)
            except Exception as exc:  # noqa: BLE001
                log.warning("erta qatnov: %s reja xatosi: %s", sgid, str(exc)[:120])
                return sgid, None

        results = []
        with ThreadPoolExecutor(
                max_workers=min(_PLAN_WORKERS, len(missing))) as pool:
            results = list(pool.map(work, missing))

        def apply(st: dict) -> None:
            _reset_if_new_day(st, today)
            for sgid, plan in results:
                if plan:  # bo'sh/urilmagan reja qayta so'raladi
                    st["plans"][sgid] = plan

        _mutate(apply)


# --------------------------------------------------------------- aniqlash

def _bm_early(row: dict) -> bool:
    """BM qatorida "erta boshlangan" bahosi bormi.

    ``trip-report`` sahifasi ``status=NOT_ACCEPTED`` bilan birga aynan
    ``brokenQualities`` ichida ``SUITABLE_PLAN_TIME_QUALITY`` bo'lgan
    qatorlarni ko'rsatadi — bu BM'ning o'zi hisoblagan baho.
    """
    quals = row.get("brokenQualities")
    if not isinstance(quals, list):
        return False
    return any(str(q).strip().upper() == _BM_EARLY_QUALITY for q in quals)


def _window_delta(actual: int, entry: dict | None) -> float:
    """Reja oralig'i (arr–dep) ga nisbatan daqiqa: manfiy — erta, musbat — kech."""
    if not entry:
        return 0.0
    arr = entry.get("arr")
    if arr is not None:
        before = _delta_sec(actual, int(arr))
        if before < 0:
            return before / 60.0
    dep = entry.get("dep")
    if dep is not None:
        after = _delta_sec(actual, int(dep))
        if after > 0:
            return after / 60.0
    return 0.0


def _plan_label(start_sec, end_sec) -> str:
    """``HH:MM–HH:MM`` reja oralig'i (bir xil bo'lsa — bitta vaqt)."""
    if start_sec is None:
        return ""
    start = _fmt_hhmm(int(start_sec))
    if end_sec is None or int(end_sec) == int(start_sec):
        return start
    return f"{start}–{_fmt_hhmm(int(end_sec))}"


def _candidates(rows: list[dict], plans: dict, now: datetime,
                threshold_min: int, window_min: int = _START_WINDOW_MIN
                ) -> list[dict]:
    """Yaqinda erta boshlangan qatnovlar (dedupe'siz).

    Asosiy shart — BM bayrog'i (``brokenQualities`` contains
    ``SUITABLE_PLAN_TIME_QUALITY``), chunki BM reja vaqtini o'zi hisoblaydi.
    Bayroq bo'lmasa, reja oralig'iga nisbatan erta boshlanish zaxira shart
    bo'lib qoladi (BM bayrog'i kechikib kelganda ham alert ketadi).
    """
    found: list[dict] = []
    seen: set = set()
    for row in rows:
        sgid = str(row.get("shiftGraphId") or "").strip()
        if not sgid:
            continue
        try:
            trip_num = int(row.get("tripNum") or 0)
        except (TypeError, ValueError):
            continue
        if trip_num <= 0:
            continue
        start = _parse_dt(row.get("startTm"))
        if start is None:
            continue
        age_s = (now - start).total_seconds()
        if age_s < 0 or age_s > window_min * 60:
            continue
        actual_sec = start.hour * 3600 + start.minute * 60 + start.second
        plan = plans.get(sgid)
        entry = plan.get(str(trip_num)) if isinstance(plan, dict) else None
        bm = _bm_early(row)
        if bm:
            # BM sahifasidagi filtr bilan bir xil: faqat qabul qilinmagan reys.
            if _status_of(row) not in _NOT_ACCEPTED_STATUSES:
                continue
            source = "bm"
        else:
            if not entry:
                continue
            delta_min = _window_delta(actual_sec, entry)
            if delta_min > -threshold_min:
                continue
            source = "plan"
        planned_sec = int(entry["arr"]) if entry and entry.get("arr") is not None \
            else actual_sec
        planned_end = int(entry["dep"]) if entry and entry.get("dep") is not None \
            else None
        delta_min = _window_delta(actual_sec, entry)
        if bm and entry and entry.get("dep") is not None:
            # BM bayrog'i qo'yilgan reysda "erta" daqiqasi reja ketish
            # vaqtidan hisoblanadi (BM shu taqqoslashdan foydalanadi).
            early_min = max(0.0, (int(entry["dep"]) - actual_sec) / 60.0)
        else:
            early_min = -delta_min if delta_min < 0 else 0.0
        row_date = str(row.get("date") or start.date().isoformat())
        key = f"{row_date}|{sgid}|{trip_num}"
        if key in seen:
            continue
        seen.add(key)
        found.append({
            "key": key,
            "row": row,
            "trip_num": trip_num,
            "planned_sec": planned_sec,
            "planned_end_sec": planned_end,
            "has_plan": bool(entry),
            "actual_sec": actual_sec,
            "early_min": early_min,
            "delta_min": delta_min,
            "source": source,
            "bm": bm,
            "start": start,
        })
    return found


# -------------------------------------------------------------- xabarlar

def _is_bm_late(item: dict) -> bool:
    """BM bayrog'i bor, lekin waybill vaqti reja ketishidan kech."""
    return (bool(item.get("bm"))
            and int(round(float(item.get("early_min") or 0))) < 1
            and float(item.get("delta_min") or 0.0) >= 1)


def _diff_text(item: dict) -> str:
    early = int(round(float(item.get("early_min") or 0)))
    if early >= 1:
        return f"{early} daq erta"
    if _is_bm_late(item):
        return f"BM bayrog'i: {int(round(float(item.get('delta_min') or 0)))} daq kech"
    return "BM: erta boshlangan"


def _texts(item: dict) -> tuple[str, str]:
    """(guruh/DM uchun HTML matn, SMS matn)."""
    row = item["row"]
    name = str(row.get("driverName") or "").strip() or "Haydovchi"
    plate = str(row.get("plateNum") or "").strip() or "?"
    route = str(row.get("routeName") or "").strip()
    planned = _plan_label(item.get("planned_sec"),
                          item.get("planned_end_sec")) \
        if item.get("has_plan") else ""
    actual = _fmt_hhmm(item["actual_sec"])
    diff = _diff_text(item)
    route_part = f" · {esc(route)}" if route else ""
    if _is_bm_late(item):
        head = "⚠️ <b>REJA VAQTIDAN CHETLASHGAN QATNOV</b>"
        tail = ("Qatnov reja ketish vaqtidan kech boshlangan — BM bayrog'i "
                "qo'yilgan. Sababini aniqlash kerak.")
        sms_head = "REJA VAQTI BUZILDI!"
        sms_tail = "Sababini aniqlang."
    else:
        head = "🚨 <b>ERTA QATNOV BOSHLANDI</b>"
        tail = ("Haydovchi orqaga qaytib, qatnovni belgilangan vaqtda "
                "<b>qayta boshlashi</b> kerak.")
        sms_head = "ERTA QATNOV!"
        sms_tail = "Orqaga qaytib qatnovni belgilangan vaqtda qayta boshlang."

    html = (
        head + "\n"
        f"Haydovchi: <b>{esc(name)}</b>\n"
        f"Avtobus: {esc(plate)}{route_part}\n"
        f"Reja: <b>{planned or '—'}</b> · Haqiqiy: <b>{actual}</b> "
        f"({diff})\n"
        + tail
    )
    plan_part = f"Reja {planned}, " if planned else ""
    sms = (
        f"{sms_head} {short_name(name)} {plate}"
        + (f" {route}" if route else "")
        + f". {plan_part}boshlandi {actual} ({diff}). "
        + sms_tail
    )
    return html, sms


def _driver_profile(storage, driver_id: str) -> dict:
    try:
        return storage.find("driver_profiles", driver_id=driver_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def _send_group(route_id: str, text: str) -> str:
    """Korxona guruhiga yuborish: sent / no-target / failed."""
    if not route_id:
        return "no-target"
    try:
        from . import group_departures
        targets = group_departures._targets_for(route_id)
    except Exception:  # noqa: BLE001
        return "failed"
    if not targets:
        return "no-target"
    from ..telegram import send_message

    ok = False
    for chat_id in targets:
        try:
            send_message(text, chat_id=str(chat_id))
            ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("erta qatnov guruhi (%s): %s", chat_id, str(exc)[:120])
    return "sent" if ok else "failed"


def _send_sms(storage, item: dict, profile: dict, sms_text: str) -> str:
    """Haydovchi telefoniga SMS: sent / no-target / failed."""
    from ..sms_notify import _gsm7_sms, _normalize_phone, send_sms

    phone = _normalize_phone(profile.get("phone"))
    if not phone:
        return "no-target"
    row = item["row"]
    payload = send_sms(phone, _gsm7_sms(sms_text))
    try:
        storage.record_sms(
            driver_id=str(row.get("driverId") or ""),
            name=str(row.get("driverName") or ""),
            phone=phone,
            route_id=str(row.get("routeVariantId") or ""),
            route_name=str(row.get("routeName") or ""),
            schedule_date="",  # favqulodda — kunlik 2 ta limitga kirmaydi
            message=_gsm7_sms(sms_text),
            status=str(payload.get("status") or "FAILED"),
            message_id=str(payload.get("message_id") or ""),
            error=str(payload.get("error") or ""),
            send_at=datetime.now().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001 - log yozilmasa ham SMS ketgan
        log.warning("erta qatnov SMS jurnali: %s", str(exc)[:120])
    return "sent" if payload.get("ok") else "failed"


def _send_dm(storage, profile: dict, text: str) -> str:
    """Haydovchi profiliga Telegram DM: sent / no-target / failed."""
    chat = str(profile.get("telegram_chat_id") or "").strip()
    if not chat or not profile.get("notification_enabled"):
        return "no-target"
    ph = storage.db.ph
    try:
        rows = storage.query(
            "SELECT id FROM notifications "
            f"WHERE channel = {ph} AND target = {ph} AND message = {ph} "
            "AND status = 'SENT' LIMIT 1",
            ("telegram", chat, text))
        if rows:
            return "sent"
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..telegram import send_message
        send_message(text, chat_id=chat)
    except Exception as exc:  # noqa: BLE001
        log.warning("erta qatnov DM (%s): %s", chat, str(exc)[:120])
        return "failed"
    try:
        storage.record_notification(
            channel="telegram", target=chat, message=text, status="SENT",
            sent_at=datetime.now().isoformat())
    except Exception:  # noqa: BLE001
        pass
    return "sent"


_CHANNELS = ("group", "sms", "dm")


def _dispatch(storage, item: dict, done: dict | None = None) -> dict:
    """Uch kanalga yuboradi; muvaffaqiyatli kanallarni qayta yubormaydi.

    ``done`` — oldingi siklda muvaffaqiyatli kanallar. Qaytaradi:
    ``results`` (sent/no-target/failed), ``complete`` (barcha kanallar
    yuborilgan yoki manzil yo'q) va ``any_sent``.
    """
    done = done or {}
    row = item["row"]
    route_id = str(row.get("routeVariantId") or "")
    profile = _driver_profile(storage, str(row.get("driverId") or ""))
    html, sms = _texts(item)

    results: dict[str, str] = {}
    if not done.get("group"):
        results["group"] = _send_group(route_id, html)
    if not done.get("sms"):
        results["sms"] = _send_sms(storage, item, profile, sms)
    if not done.get("dm"):
        results["dm"] = _send_dm(storage, profile, html)

    statuses = {ch: "sent" for ch in _CHANNELS if done.get(ch)}
    statuses.update(results)
    complete = all(statuses.get(ch) in ("sent", "no-target")
                   for ch in _CHANNELS)
    any_sent = any(v == "sent" for v in statuses.values())
    lag_s = max(0.0, (datetime.now() - item["start"]).total_seconds())
    log.info("erta qatnov %s: %s (%s, startdan +%.1fs)", item["key"],
             statuses, "to'liq" if complete else "qayta uriniladi", lag_s)
    return {"results": results, "complete": complete, "any_sent": any_sent}


# ------------------------------------------------------- reys natijasi

# BM waybill'da shu statuslar "reys qabul qilindi" deb hisoblanadi.
_FOLLOWUP_ACCEPTED = {"ACCEPTED", "APPROVED"}


def _followup_record(item: dict) -> dict:
    """Alert yuborilgandan keyin natijani kuzatish uchun yengil yozuv."""
    row = item["row"]
    end = _parse_dt(row.get("endTm"))
    return {
        "at": time.time(),
        "planned": int(item.get("planned_sec") or 0),
        "planned_end": int(item["planned_end_sec"])
        if item.get("planned_end_sec") is not None else 0,
        "has_plan": bool(item.get("has_plan")),
        "source": str(item.get("source") or ""),
        "bm": bool(item.get("bm")),
        "actual": int(item["actual_sec"]),
        "early_min": round(float(item["early_min"]), 1),
        "delta_min": round(float(item.get("delta_min") or 0.0), 1),
        "start": item["start"].isoformat(),
        "end": end.isoformat() if end else "",
        "route": str(row.get("routeVariantId") or ""),
        "driver_id": str(row.get("driverId") or ""),
        "name": str(row.get("driverName") or ""),
        "plate": str(row.get("plateNum") or ""),
        "route_name": str(row.get("routeName") or ""),
        "stage": "",
        "tries": 0,
    }


def _followup_item(key: str, rec: dict) -> dict:
    return {
        "key": key,
        "row": {
            "routeVariantId": rec.get("route") or "",
            "driverId": rec.get("driver_id") or "",
            "driverName": rec.get("name") or "",
            "plateNum": rec.get("plate") or "",
            "routeName": rec.get("route_name") or "",
        },
        "planned_sec": int(rec.get("planned") or 0),
        "planned_end_sec": (int(rec.get("planned_end") or 0) or None),
        "has_plan": bool(rec.get("has_plan")),
        "actual_sec": int(rec.get("actual") or 0),
        "early_min": float(rec.get("early_min") or 0),
        "delta_min": float(rec.get("delta_min") or 0),
        "source": str(rec.get("source") or ""),
        "bm": bool(rec.get("bm")),
    }


def _followup_due(rec: dict, now: datetime, minutes: int) -> bool:
    """Qatnov tugagach + kechikish o'tdimi (endTm bo'lmasa — startTm)."""
    end = _parse_dt(rec.get("end")) or _parse_dt(rec.get("start"))
    if end is None:
        return True
    return (now - end).total_seconds() >= minutes * 60


def _followup_texts(item: dict, accepted: bool) -> tuple[str, str]:
    """(guruh/DM HTML, SMS). Qabul qilinmaganda SMS ham yuboriladi."""
    row = item["row"]
    name = str(row.get("driverName") or "").strip() or "Haydovchi"
    plate = str(row.get("plateNum") or "").strip() or "?"
    route = str(row.get("routeName") or "").strip()
    planned = _plan_label(item.get("planned_sec"),
                          item.get("planned_end_sec")) \
        if item.get("has_plan") else ""
    actual = _fmt_hhmm(item["actual_sec"])
    diff = _diff_text(item)
    route_part = f" · {esc(route)}" if route else ""
    head = (
        f"Haydovchi: <b>{esc(name)}</b>\n"
        f"Avtobus: {esc(plate)}{route_part}\n"
        f"Reja: <b>{planned or '—'}</b> · Boshlangan: <b>{actual}</b> "
        f"({diff})\n"
    )
    if accepted:
        html = (
            "✅ <b>REYS QABUL QILINDI</b>\n" + head
            + "BM tizimida reys qabul qilindi — erta boshlanish "
              "ogohlantirishi yopildi."
        )
        sms = ""
        return html, sms
    html = (
        "⚠️ <b>REYS QABUL QILINMADI</b>\n" + head
        + "Reys BM tizimida qabul qilinmagan — <b>haydovchining aybi</b>.\n"
          "Haydovchidan tushuntirish oling va reysni belgilangan tartibda "
          "qayta topshiring."
    )
    plan_part = f"{planned} o'rniga " if planned else ""
    sms = (
        f"REYS QABUL QILINMADI! {short_name(name)} {plate}. "
        f"{plan_part}{actual} ({diff}). Haydovchi aybi."
    )
    return html, sms


def _dispatch_followup(storage, item: dict, accepted: bool) -> bool:
    """Qabul qilindi — guruh+DM; qilinmadi — guruh+SMS+DM (eslatma)."""
    row = item["row"]
    route_id = str(row.get("routeVariantId") or "")
    profile = _driver_profile(storage, str(row.get("driverId") or ""))
    html, sms = _followup_texts(item, accepted)

    results = {"group": _send_group(route_id, html),
               "dm": _send_dm(storage, profile, html)}
    if not accepted:
        results["sms"] = _send_sms(storage, item, profile, sms)
    ok = any(v == "sent" for v in results.values())
    log.info("erta qatnov natijasi %s (%s): %s", item["key"],
             "qabul qilindi" if accepted else "qabul qilinmadi", results)
    return ok


def _track_followups(storage, rows: list[dict], now: datetime) -> int:
    """Kuzatilayotgan alertlar uchun tripStatus natijasini tekshiradi."""
    state = _load()
    bucket = state.get("followup") or {}
    if not bucket:
        return 0
    minutes = bot_settings.early_start_followup_min()
    if minutes <= 0:
        return 0

    status_by_key: dict[str, str] = {}
    for row in rows:
        sgid = str(row.get("shiftGraphId") or "").strip()
        try:
            trip_num = int(row.get("tripNum") or 0)
        except (TypeError, ValueError):
            continue
        start = _parse_dt(row.get("startTm"))
        if not sgid or trip_num <= 0 or start is None:
            continue
        row_date = str(row.get("date") or start.date().isoformat())
        status = str(row.get("tripStatus") or row.get("status") or "")
        status_by_key[f"{row_date}|{sgid}|{trip_num}"] = status.strip().upper()

    sent = 0
    for key, rec in list(bucket.items()):
        if not isinstance(rec, dict) or rec.get("stage"):
            continue
        status = status_by_key.get(key)
        if status is None:
            continue  # qator hali waybill'da yo'q — keyingi sikl
        if status in _FOLLOWUP_ACCEPTED:
            stage = "accepted"
        elif _followup_due(rec, now, minutes):
            stage = "missed"
        else:
            continue
        item = _followup_item(key, rec)
        ok = _dispatch_followup(storage, item, accepted=(stage == "accepted"))
        tries = int(rec.get("tries") or 0) + 1
        final = ok or tries >= _MAX_ATTEMPTS

        def mark(st: dict, _key=key, _stage=stage, _tries=tries,
                 _final=final) -> None:
            rec2 = (st.get("followup") or {}).get(_key)
            if not isinstance(rec2, dict):
                return
            rec2["tries"] = _tries
            if _final:
                rec2["stage"] = _stage
                rec2["stage_at"] = time.time()

        _mutate(mark)
        if ok:
            sent += 1
    return sent


# --------------------------------------------------------------- hisobot

_ACCEPTED_STATUSES = {"ACCEPTED", "APPROVED"}
_NOT_ACCEPTED_STATUSES = {"NOT_ACCEPTED", "PENDING_ACCESS"}


def _parse_date(value) -> date | None:
    """``YYYY-MM-DD`` (bo'sh bo'lsa bugun); noto'g'ri qiymatda ``None``."""
    raw = str(value or "").strip()[:10]
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _status_of(row: dict) -> str:
    """Waybill statusi — noma'lum qiymatni PENDING_ACCESS'ga aylantirmaydi."""
    raw = str(row.get("tripStatus") or row.get("status") or "").strip().upper()
    try:
        from ...db.models import TripStatus
        if raw and not TripStatus.is_valid(raw):
            return raw
        return raw or TripStatus.PENDING_ACCESS.value
    except Exception:  # noqa: BLE001
        return raw


def _cached_plans(client, sgids: list[str], day: str) -> dict[str, dict]:
    """Grafik rejalari — hisobot uchun qisqa muddatli kesh bilan."""
    now = time.time()
    out: dict[str, dict] = {}
    missing: list[str] = []
    with _REPORT_LOCK:
        for sgid in sgids:
            hit = _PLAN_CACHE.get((day, sgid))
            if hit and now - hit[0] <= _PLAN_CACHE_TTL_S:
                out[sgid] = hit[1]
            else:
                missing.append(sgid)
    if not missing:
        return out
    with ThreadPoolExecutor(
            max_workers=min(_PLAN_WORKERS, len(missing))) as pool:
        futures = {pool.submit(_graph_plan, client, sgid): sgid
                   for sgid in missing}
        for future, sgid in futures.items():
            try:
                plan = future.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("erta qatnov hisoboti: %s reja xatosi: %s",
                            sgid, str(exc)[:120])
                continue
            if plan:
                out[sgid] = plan
                with _REPORT_LOCK:
                    _PLAN_CACHE[(day, sgid)] = (now, plan)
    return out


def _report_item(row: dict, plans: dict[str, dict],
                 threshold_min: int, fallback_date: str) -> dict:
    sgid = str(row.get("shiftGraphId") or "").strip()
    try:
        trip_num = int(row.get("tripNum") or 0)
    except (TypeError, ValueError):
        trip_num = 0
    start = _parse_dt(row.get("startTm"))
    status = _status_of(row)
    bm = _bm_early(row)
    item = {
        "date": str(row.get("date") or fallback_date)[:10],
        "route_id": str(row.get("routeVariantId") or ""),
        "route": str(row.get("routeName") or ""),
        "driver_id": str(row.get("driverId") or ""),
        "driver": str(row.get("driverName") or ""),
        "vehicle": str(row.get("plateNum") or ""),
        "trip_num": trip_num,
        "status": status,
        "planned": "",
        "planned_end": "",
        "actual": "",
        "early_min": 0.0,
        "delta_min": 0.0,
        "has_plan": False,
        "bm": bm,
        "source": "bm" if bm else "",
        "category": "no_plan",
    }
    if start is None:
        item["category"] = "not_started"
        return item
    actual_sec = start.hour * 3600 + start.minute * 60 + start.second
    item["actual"] = _fmt_hhmm(actual_sec)
    entry = (plans.get(sgid) or {}).get(str(trip_num)) \
        if sgid and trip_num > 0 else None
    if entry:
        item["has_plan"] = True
        item["planned"] = _fmt_hhmm(int(entry["arr"]))
        item["planned_end"] = _fmt_hhmm(int(entry["dep"]))
        delta_min = _window_delta(actual_sec, entry)
        item["delta_min"] = round(delta_min, 1)
        if bm and entry.get("dep") is not None:
            # BM bayrog'ida "erta" daqiqasi reja ketish vaqtidan hisoblanadi
            # (ogohlantirish oqimi bilan bir xil taqqoslash).
            item["early_min"] = round(
                max(0.0, (int(entry["dep"]) - actual_sec) / 60.0), 1)
        else:
            item["early_min"] = round(-delta_min, 1) if delta_min < 0 else 0.0
        item["source"] = "bm" if bm else "plan"
    # BM bayrog'i faqat BM sahifasidagi filtr bilan bir xil holatda hisobga
    # olinadi (qabul qilinmagan reys); aks holda reja oraliqi hal qiladi.
    if bm and status in _NOT_ACCEPTED_STATUSES:
        item["category"] = "early"
    elif not entry:
        item["category"] = "no_plan"
    elif item["early_min"] >= threshold_min:
        item["category"] = "early"
    elif item["delta_min"] >= threshold_min:
        item["category"] = "late"
    else:
        item["category"] = "ontime"
    return item


def _build_report(rows: list[dict], client, day: str,
                  threshold_min: int,
                  clients_by_route: dict | None = None) -> dict:
    sgids: list[str] = []
    for row in rows:
        sgid = str(row.get("shiftGraphId") or "").strip()
        if sgid and sgid not in sgids:
            sgids.append(sgid)
    plans: dict[str, dict] = {}
    if sgids:
        groups: dict[int, tuple[object, list[str]]] = {}
        for row in rows:
            sgid = str(row.get("shiftGraphId") or "").strip()
            if not sgid:
                continue
            rid = str(row.get("routeVariantId") or "")
            row_client = (clients_by_route or {}).get(rid) or client
            if row_client is None:
                continue
            entry = groups.setdefault(id(row_client), (row_client, []))
            if sgid not in entry[1]:
                entry[1].append(sgid)
        for row_client, group_sgids in groups.values():
            plans.update(_cached_plans(row_client, group_sgids, day))

    items: list[dict] = []
    seen: set[str] = set()
    for idx, row in enumerate(rows):
        item = _report_item(row, plans, threshold_min, day)
        key = "|".join((item["date"], item["route_id"],
                        str(row.get("shiftGraphId") or ""),
                        str(item["trip_num"]), item["actual"],
                        item["driver_id"], item["vehicle"])) or str(idx)
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    totals = {
        "total": 0, "measured": 0, "early": 0, "ontime": 0,
        "late": 0, "no_plan": 0, "not_started": 0,
        "accepted": 0, "not_accepted": 0, "rejected": 0,
        "zero_mileage": 0, "other": 0,
        "early_accepted": 0, "early_not_accepted": 0, "bm_early": 0,
        "avg_early_min": 0.0, "max_early_min": 0.0,
        "completion_rate": 0.0,
    }
    early_sum = 0.0
    early_measured = 0
    early_rows: list[dict] = []
    not_accepted_rows: list[dict] = []
    drivers: dict[str, dict] = {}
    routes: dict[str, dict] = {}

    for item in items:
        totals["total"] += 1
        category = item["category"]
        if category in ("early", "ontime", "late"):
            totals["measured"] += 1
            totals[category] += 1
        elif category in ("no_plan", "not_started"):
            totals[category] += 1

        status = item["status"]
        if status in _ACCEPTED_STATUSES:
            totals["accepted"] += 1
        elif status in _NOT_ACCEPTED_STATUSES:
            totals["not_accepted"] += 1
        elif status == "REJECTED":
            totals["rejected"] += 1
        elif status == "ZERO_MILEAGE":
            totals["zero_mileage"] += 1
        else:
            totals["other"] += 1

        if item["bm"] and status in _NOT_ACCEPTED_STATUSES:
            totals["bm_early"] += 1

        if category == "early":
            early_rows.append(item)
            # BM bayrog'i reja oralig'ida tasdiqlanmasa (early_min=0),
            # o'rtacha/maksimum faqat aniq daqiqalardan hisoblanadi.
            if item["early_min"] >= 1:
                early_sum += item["early_min"]
                early_measured += 1
            totals["max_early_min"] = max(totals["max_early_min"],
                                          item["early_min"])
            if status in _ACCEPTED_STATUSES:
                totals["early_accepted"] += 1
            elif status in _NOT_ACCEPTED_STATUSES:
                totals["early_not_accepted"] += 1
        if status in _NOT_ACCEPTED_STATUSES:
            not_accepted_rows.append(item)

        dkey = item["driver_id"] or item["driver"] or "—"
        dstat = drivers.setdefault(dkey, {
            "id": dkey, "name": item["driver"] or "—",
            "route": item["route"], "trips": 0, "early": 0,
            "late": 0, "not_accepted": 0, "early_sum": 0.0})
        dstat["trips"] += 1
        dstat["route"] = dstat["route"] or item["route"]
        if category == "early":
            dstat["early"] += 1
            if item["early_min"] >= 1:
                dstat["early_sum"] += item["early_min"]
                dstat["early_measured"] = dstat.get("early_measured", 0) + 1
        if category == "late":
            dstat["late"] += 1
        if status in _NOT_ACCEPTED_STATUSES:
            dstat["not_accepted"] += 1

        rkey = item["route_id"] or item["route"] or "—"
        rstat = routes.setdefault(rkey, {
            "id": rkey, "name": item["route"] or rkey,
            "trips": 0, "early": 0, "not_accepted": 0})
        rstat["trips"] += 1
        if category == "early":
            rstat["early"] += 1
        if status in _NOT_ACCEPTED_STATUSES:
            rstat["not_accepted"] += 1

    if early_measured:
        totals["avg_early_min"] = round(early_sum / early_measured, 1)
    if totals["total"]:
        totals["completion_rate"] = round(
            totals["accepted"] / totals["total"] * 100, 1)

    early_rows.sort(key=lambda x: (0 if x["bm"] else 1,
                                   -x["early_min"], x["actual"]))
    not_accepted_rows.sort(key=lambda x: (x["date"], x["actual"]))
    driver_rows = []
    for stat in drivers.values():
        if not (stat["early"] or stat["not_accepted"]):
            continue
        driver_rows.append({
            "id": stat["id"], "name": stat["name"],
            "route": stat["route"], "trips": stat["trips"],
            "early": stat["early"], "late": stat["late"],
            "not_accepted": stat["not_accepted"],
            "avg_early_min": round(stat["early_sum"] / stat.get("early_measured", 0), 1)
            if stat.get("early_measured") else 0.0,
        })
    driver_rows.sort(key=lambda x: (-x["early"], -x["not_accepted"],
                                    x["name"]))
    route_rows = sorted(routes.values(),
                        key=lambda x: (-x["early"], -x["not_accepted"],
                                       x["name"]))

    return {
        "ok": True,
        "date": day,
        "threshold_min": threshold_min,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": ("BM bahosi (SUITABLE_PLAN_TIME_QUALITY) + waybill startTm · "
                   "reja: shifts/graph/times oraliqlari (START/BETWEEN/LUNCH)"),
        "totals": totals,
        "rows": early_rows[:300],
        "not_accepted_rows": not_accepted_rows[:300],
        "drivers": driver_rows[:100],
        "routes": route_rows[:100],
        "note": "" if items else "Bu sana uchun BM waybill qatorlari topilmadi",
    }


def early_start_report(date_str: str = "", route_ids: list[str] | None = None,
                       force: bool = False) -> dict:
    """Bir kunlik ANIQ erta boshlanish tahlili.

    Asosiy manba — BM'ning o'z bahosi (``brokenQualities`` ichida
    ``SUITABLE_PLAN_TIME_QUALITY``, qabul qilinmagan reyslar), ya'ni
    ``trip-report`` sahifasidagi filtr bilan bir xil ro'yxat. Reja
    oraliqlari ``shifts/graph/times`` dan (global ``index`` = ``tripNum``).
    Alert oqimidan farqli ravishda 120 daqiqalik oyna va yuborilganlar
    dedupe'i qo'llanmaydi — kun bo'yicha barcha reyslar hisoblanadi.
    Natija 60 s keshlanadi (``force=True`` — yangilash).
    """
    day = _parse_date(date_str)
    if day is None:
        return {"ok": False,
                "error": "Sana YYYY-MM-DD ko'rinishida bo'lishi kerak"}
    routes = tuple(sorted({str(r).strip() for r in (route_ids or [])
                           if str(r).strip()}))
    threshold = int(bot_settings.early_start_threshold_min() or 1)
    cache_key = (day.isoformat(), routes, threshold)
    now = time.time()
    if not force:
        with _REPORT_LOCK:
            hit = _REPORT_CACHE.get(cache_key)
        if hit and now - hit[0] <= _REPORT_CACHE_TTL_S:
            return hit[1]

    default_client = _fresh_client()
    fetch_errors: list[dict] = []
    clients_by_route: dict[str, object] = {}
    rows = _fetch_rows(day.isoformat(), route_ids=list(routes) or None,
                       errors=fetch_errors, clients_out=clients_by_route)
    if not rows and fetch_errors:
        detail = "; ".join(
            f"{e.get('profile') or '?'}: {e.get('error') or ''}"
            for e in fetch_errors[:3])
        return {"ok": False, "error": "BM ma'lumoti yuklanmadi: " + detail}
    if default_client is None and not clients_by_route and not rows:
        return {"ok": False,
                "error": "BM tokeni yo'q — tizimga qayta kirish kerak (401)"}
    report = _build_report(rows, default_client, day.isoformat(), threshold,
                           clients_by_route)
    report["fetch_errors"] = fetch_errors
    report["partial"] = bool(fetch_errors)
    if fetch_errors and not report.get("note"):
        report["note"] = ("Ba'zi kompaniyalar ma'lumoti yuklanmadi — "
                          "raqamlar to'liq emas")
    with _REPORT_LOCK:
        _REPORT_CACHE[cache_key] = (time.time(), report)
    return report


# ------------------------------------------------------------------ sikl

def _maybe_prune() -> None:
    """Eskirgan ``sent``/``attempts`` kalitlarini siyrak tozalaydi."""
    global _LAST_PRUNE_AT
    now = time.time()
    if now - _LAST_PRUNE_AT < 600:
        return
    _LAST_PRUNE_AT = now
    cutoff = now - _KEEP_SENT_S

    def prune(st: dict) -> None:
        for bucket in ("sent", "attempts", "followup"):
            data = st.get(bucket) or {}
            for k, v in list(data.items()):
                if isinstance(v, dict):
                    ts = v.get("at")
                else:
                    ts = v
                if isinstance(ts, (int, float)) and ts and ts < cutoff:
                    data.pop(k, None)
        active = set(st.get("attempts") or {}) | set(st.get("sent") or {})
        delivered = st.get("delivered") or {}
        for k in list(delivered):
            if k not in active:
                delivered.pop(k, None)

    _mutate(prune)


def _check_rows(storage, rows: list[dict], client, now: datetime) -> dict:
    """Waybill qatorlari → reja → erta boshlanganlar → xabarlar.

    Avval oldingi alertlarning natijasi (``tripStatus``) tekshiriladi,
    keyin yangi erta boshlanishlar uchun ogohlantirish yuboriladi.
    """
    today = now.date().isoformat()
    state = _load()
    if state.get("date") != today:
        state = _mutate(lambda st: _reset_if_new_day(st, today))

    followups = _track_followups(storage, rows, now)

    # Faqat yaqinda boshlangan qatorlar uchun reja kerak (API tejamkorligi).
    recent_sgids: list[str] = []
    for row in rows:
        start = _parse_dt(row.get("startTm"))
        if start is None:
            continue
        age = (now - start).total_seconds()
        if -_PLAN_PREFETCH_MIN * 60 <= age <= _START_WINDOW_MIN * 60:
            sgid = str(row.get("shiftGraphId") or "").strip()
            if sgid and sgid not in recent_sgids:
                recent_sgids.append(sgid)
    if not recent_sgids:
        _maybe_prune()
        return {"checked": len(rows), "alerts": 0, "followups": followups,
                "skipped": "no-recent"}
    if client is not None:  # BM bayrog'i token bo'lmasa ham yetarli
        _ensure_plans(client, recent_sgids, state, today)
    state = _load()

    threshold = bot_settings.early_start_threshold_min()
    found = _candidates(rows, state.get("plans") or {}, now, threshold)
    sent = state.get("sent") or {}
    delivered = state.get("delivered") or {}
    fresh = [it for it in found if it["key"] not in sent]

    alerts = 0
    for item in fresh:
        key = item["key"]
        done = delivered.get(key) or {}
        outcome = _dispatch(storage, item, done)
        results = outcome["results"]
        complete = outcome["complete"]
        any_sent = outcome["any_sent"]

        def mark(st: dict, _key=key, _item=item, _results=results,
                 _complete=complete, _any_sent=any_sent) -> None:
            if _any_sent:
                st.setdefault("followup", {}).setdefault(
                    _key, _followup_record(_item))
            if _complete:
                st["sent"][_key] = time.time()
                st.get("attempts", {}).pop(_key, None)
                st.setdefault("delivered", {}).pop(_key, None)
                return
            bucket = st.setdefault("delivered", {}).setdefault(_key, {})
            for channel, status in _results.items():
                if status == "sent":
                    bucket[channel] = True
            raw = st.setdefault("attempts", {}).get(_key) or 0
            n = int(raw.get("n") or 0) if isinstance(raw, dict) else int(raw)
            n += 1
            if n >= _MAX_ATTEMPTS:
                st["sent"][_key] = time.time()
                st.get("attempts", {}).pop(_key, None)
                st.setdefault("delivered", {}).pop(_key, None)
            else:
                st["attempts"][_key] = {"n": n, "at": time.time()}

        _mutate(mark)
        if any_sent and not done:
            alerts += 1

    _maybe_prune()
    return {"checked": len(rows), "alerts": alerts, "followups": followups,
            "candidates": len(found), "pending": len(fresh)}


def _run_once(now: datetime | None = None) -> dict:
    """Bitta to'liq tekshiruv: barcha profillar → erta boshlanganlar.

    Bu zaxira yo'l (poll-hook/testlar uchun) — sekin profil butun siklini
    ushlab turishi mumkin. Doimiy ishda ``_profile_loop`` ishlaydi.
    """
    if not enabled():
        return {"checked": 0, "alerts": 0, "skipped": "off"}
    storage = get_storage()
    if not storage or not getattr(storage, "enabled", False):
        return {"checked": 0, "alerts": 0, "skipped": "db"}
    now = now or datetime.now()
    rows = _fetch_rows(now.date().isoformat())
    if not rows:
        return {"checked": 0, "alerts": 0, "skipped": "no-rows"}
    client = _fresh_client()
    if client is None:
        return {"checked": len(rows), "alerts": 0, "skipped": "no-token"}
    return _check_rows(storage, rows, client, now)


def _check_profile(profile: dict, now: datetime | None = None) -> dict:
    """Bitta kompaniyaning waybill'ini olib tekshiradi (mustaqil oqim)."""
    from ...repositories.waybill_repo import WaybillRepository

    rid = str(profile.get("routeVariantId") or "")
    if not rid:
        return {"alerts": 0, "skipped": "no-route"}
    storage = get_storage()
    if not storage or not getattr(storage, "enabled", False):
        return {"alerts": 0, "skipped": "db"}
    client = _profile_client(profile)
    if client is None:
        return {"alerts": 0, "skipped": "no-token"}
    now = now or datetime.now()
    today = now.date().isoformat()
    repo = WaybillRepository(client)
    try:
        try:
            data = repo.report(rid, today, today, timeout=_FETCH_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - BM tarmoq xatolari ~10%
            # Keyingi sikl 5 s kutmasin: qisqa tanaffusdan so'ng darhol qayta
            # urinish (favqulodda alert uchun har bir soniya muhim).
            time.sleep(_FETCH_RETRY_DELAY_S)
            data = repo.report(rid, today, today, timeout=_FETCH_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - bitta profil qolganini to'smasin
        log.warning("erta qatnov: %s waybill xatosi: %s",
                    profile.get("name"), str(exc)[:120])
        return {"alerts": 0, "skipped": "fetch-error"}
    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        return {"checked": 0, "alerts": 0, "skipped": "no-rows"}
    flat = [_flat(r) for r in rows if isinstance(r, dict)]
    return _check_rows(storage, flat, client, now)


def _profile_loop(profile: dict, idx: int = 0) -> None:
    """Bitta profil uchun mustaqil sikl: fetch → tekshirish → interval.

    Boshqa kompaniyalar bilan umumiy to'siq yo'q — sekin yoki uzilgan
    profil faqat o'z tekshiruvini kechiktiradi.
    """
    if idx:
        time.sleep(min(idx, 12) * _FETCH_STAGGER_S)
    while True:
        started = time.monotonic()
        if enabled():
            try:
                _check_profile(profile)
            except Exception as exc:  # noqa: BLE001 - oqim to'xtamasligi kerak
                log.warning("erta qatnov (%s) oqimi xatosi: %s",
                            profile.get("name"), str(exc)[:200])
        interval = float(bot_settings.early_start_interval_s())
        time.sleep(max(1.0, interval - (time.monotonic() - started)))


def check_and_send() -> None:
    """Poll-tsikldan chaqiriladi: interval o'tgan bo'lsa fon oqimida ishlaydi."""
    global _LAST_CHECK_AT, _RUNNING
    if _WORKER_STARTED:  # doimiy profil oqimlari ishlayapti — takrorlamaymiz
        return
    if _RUNNING or not enabled():
        return
    now = time.time()
    interval = float(bot_settings.early_start_interval_s())
    if now - _LAST_CHECK_AT < interval:
        return
    _LAST_CHECK_AT = now
    with _LOCK:
        if _RUNNING:
            return
        _RUNNING = True

    def _bg() -> None:
        global _RUNNING
        try:
            res = _run_once()
            if res.get("alerts"):
                print(f"Erta qatnov ogohlantirishlari: {res}")
        except Exception as exc:  # noqa: BLE001 - keyingi sikl qayta urinadi
            log.warning("erta qatnov tekshiruvi xatosi: %s", str(exc)[:200])
        finally:
            _RUNNING = False

    threading.Thread(target=_bg, daemon=True).start()


def start_worker() -> None:
    """Doimiy fon koordinatori (bot ishga tushganda bir marta chaqiriladi).

    Telegram long-poll 50 s davom etadi — poll-tsikldagi tekshiruv shu bilan
    cheklanadi. Koordinator har bir kompaniya uchun alohida ``early-start-*``
    oqimini ochadi (standart interval 5 s); ``check_and_send`` poll-tsiklda
    zaxira sifatida qoladi va doimiy oqim ishlayotganda hech narsa qilmaydi.
    """
    global _WORKER_STARTED
    with _LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
    log.info("erta qatnov oqimi ishga tushdi (interval %ss)",
             bot_settings.early_start_interval_s())
    threading.Thread(target=_worker_loop, name="early-start",
                     daemon=True).start()


def _worker_loop() -> None:
    """Koordinator: profillar uchun oqimlarni ochadi va tirikligini kuzatadi."""
    global _LAST_CHECK_AT
    workers: dict[str, threading.Thread] = {}
    while True:
        try:
            profiles = [p for p in all_profiles() if p.get("routeVariantId")]
            for idx, profile in enumerate(profiles):
                rid = str(profile.get("routeVariantId") or "")
                thread = workers.get(rid)
                if thread is not None and thread.is_alive():
                    continue
                thread = threading.Thread(
                    target=_profile_loop, args=(profile, idx),
                    name=f"early-start-{rid}", daemon=True)
                thread.start()
                workers[rid] = thread
            _LAST_CHECK_AT = time.time()
        except Exception as exc:  # noqa: BLE001 - koordinator to'xtamasligi kerak
            log.warning("erta qatnov koordinatori xatosi: %s", str(exc)[:200])
        time.sleep(30.0)
