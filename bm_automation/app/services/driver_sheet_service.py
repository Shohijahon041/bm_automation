"""Haydovchilar uchun kunlik jadval rasmini yaratish xizmati.

Yo'nalish: har grafik (P1..Pn) qaysi konechkadan chiqishini grafik vaqtlari
va waybill ma'lumotlaridan aniqlaydi, ikkita konechka bo'yicha guruhlab,
bitta rangli rasmga tushiradi (chizish `app.exporters.sheet_image` da).

Bu xizmat faqat haydovchilarga jadvalni rasm qilib yuborish uchun
ishlatiladi va asosiy hisobot jarayonlariga ta'sir qilmaydi.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ..api.client import BMClient
from ..exporters.sheet_image import make_sheet_image
from ..repositories.duty_repo import DutyRepository
from ..repositories.waybill_repo import WaybillRepository
from ..utils.io import atomic_write, ensure_dir, safe_name
from ..utils.text import normalize_uz

__all__ = [
    "DEFAULT_KONECHKA",
    "build_rows",
    "detect_profile_fields",
    "detect_profile_route_name",
    "detect_profile_starts",
    "detect_route_starts",
    "graph_start_direction",
    "konechka_names",
    "run",
    "run_all",
    "short_route_name",
    "waybill_details",
]

# Eski chaqiruvchilar uchun qoldirilgan; yangi oqimda ishlatilmaydi —
# aks holda yangi yo'nalishga boshqa firmaning konechka nomlari tushadi.
DEFAULT_KONECHKA = {
    "UP": "Temiryo'l kesishmasi-10",
    "DOWN": "FERGANA TEX SERVIS YURIY",
}

_ROUTE_SUFFIX = re.compile(
    r"[\s\-–—]*(?:сонли|сон|sonli|son)(?=\s|$).*$"
    r"|[\s\-–—]*(?:йўналиши|йўналиш|yo['’ʻ‘`]?nalishi|yo['’ʻ‘`]?nalish)(?=\s|$).*$",
    re.IGNORECASE,
)

SENT_STATE = Path("state") / "daily_sheet_sent.json"
_SENT_LOCK = threading.Lock()
_SENT_KEEP_DAYS = 14


def _sent_state() -> dict:
    """`state/daily_sheet_sent.json` — "<route_id>|<sana>" -> [yuborilgan guruhlar]."""
    try:
        if SENT_STATE.exists():
            data = json.loads(SENT_STATE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _prune_sent(state: dict) -> dict:
    cutoff = (date.today() - timedelta(days=_SENT_KEEP_DAYS)).isoformat()
    return {k: v for k, v in state.items() if str(k).split("|")[-1] >= cutoff}


def _mark_sent(route_id: str, date_str: str, targets: list[str]) -> None:
    """(yo'nalish, kun) uchun yuborilgan guruhlarni eslab qoladi."""
    with _SENT_LOCK:
        state = _prune_sent(_sent_state())
        key = f"{route_id}|{date_str}"
        cur = state.get(key) or []
        if isinstance(cur, str):
            cur = [cur]
        state[key] = sorted({str(x) for x in cur} | {str(t) for t in targets})
        try:
            atomic_write(SENT_STATE, json.dumps(state, ensure_ascii=False, indent=2))
        except Exception:
            pass


def _pending_targets(route_id: str, date_str: str, targets: list[str]) -> list[str]:
    """Allaqachon yuborilgan guruhlarni chiqarib tashlaydi (retryda takror yubormaslik)."""
    done = _sent_state().get(f"{route_id}|{date_str}") or []
    if isinstance(done, str):
        done = [done]
    done = {str(x) for x in done}
    return [t for t in targets if str(t) not in done]


def _norm_time(t: str) -> str:
    """Vaqtni HH:MM formatiga tekislaydi (string taqqoslash uchun)."""
    t = (t or "").strip()
    if ":" in t:
        parts = t.split(":", 1)
        return f"{int(parts[0]):02d}:{parts[1][:2]}"
    return t


def graph_start_direction(client: BMClient, shift_graph_id: str, start_time: str) -> str | None:
    """Grafik qaysi yo'nalishda (konechkadan) boshlanishini aniqlaydi."""
    repo = DutyRepository(client)
    for direction in ("UP", "DOWN"):
        try:
            slots = repo.graph_times(shift_graph_id, direction)
        except Exception as exc:
            print(f"  [graph_start_direction] {direction} xatolik: {exc}")
            continue
        if not slots:
            continue
        first = slots[0]
        if first.get("slotType") == "START":
            return direction
        if first.get("departureTime") == start_time:
            return direction
    return None


def _waybill_rows(client: BMClient, route_id: str, date_str: str) -> list:
    """Waybill qatorlari: avval so'ralgan kun, bo'lmasa 7 kun orqaga.

    Ertangi kun uchun waybill hali yaratilmagan bo'lishi mumkin — shunda
    oldingi kunlardan ma'lumot qidiriladi. Topilmasa bo'sh ro'yxat.
    """
    try:
        target = date.fromisoformat(date_str)
    except ValueError:
        target = date.today()
    repo = WaybillRepository(client)
    for offset in range(8):
        day = (target - timedelta(days=offset)).isoformat()
        try:
            data = repo.report(route_id, day, day)
        except Exception as exc:
            print(f"  [waybill_rows] xatolik ({day}): {exc}")
            continue
        if data:
            return data
    return []


def short_route_name(value) -> str:
    """Yo'nalish nomini qisqa ko'rinishga keltiradi: "12-сонли" -> "12"."""
    text = str(value or "").strip()
    if not text:
        return ""
    short = _ROUTE_SUFFIX.sub("", text).strip(" -–—")
    return short or text


def waybill_details(client: BMClient, route_id: str, date_str: str = "") -> dict:
    """Waybill'dan konechka nomlari va yo'nalish nomini bir o'qishda oladi.

    Qaytaradi faqat topilgan maydonlarni:
    ``{"start1": ..., "start2": ..., "routeName": ...}``.
    """
    data = _waybill_rows(client, route_id, date_str or date.today().isoformat())
    counts = {"UP": Counter(), "DOWN": Counter()}
    route_names: Counter = Counter()
    for wb in data or []:
        d = wb.get("direction")
        sn = wb.get("startName")
        if d in counts and sn:
            counts[d][normalize_uz(sn)] += 1
        rn = wb.get("routeName")
        if rn:
            route_names[normalize_uz(rn)] += 1
    out = {}
    for d, key in (("UP", "start1"), ("DOWN", "start2")):
        if counts[d]:
            out[key] = counts[d].most_common(1)[0][0]
    if route_names:
        out["routeName"] = route_names.most_common(1)[0][0]
    return out


def konechka_names(client: BMClient, route_id: str, date_str: str) -> dict:
    """Waybill'lardan har yo'nalish uchun eng ko'p uchragan startName (konechka).

    Ma'lumot topilmasa nom bo'sh qoladi: boshqa yo'nalishning standart
    nomlari ko'rsatilmaydi.
    """
    details = waybill_details(client, route_id, date_str)
    return {"UP": details.get("start1") or "",
            "DOWN": details.get("start2") or ""}


def detect_route_starts(client: BMClient, route_id: str, date_str: str = "") -> dict:
    """Waybill'lardan `start1`/`start2` (1-/2-chiqish konechkalari) ni aniqlaydi."""
    details = waybill_details(client, route_id, date_str)
    return {key: details[key] for key in ("start1", "start2") if details.get(key)}


def detect_profile_fields(profile: dict, date_str: str = "") -> dict:
    """Profil yo'nalishi bo'yicha waybill'dan nomlar (best-effort).

    Qaytaradi ``{"routeName", "start1", "start2"}`` dan topilganlari.
    Yangi kompaniya qo'shilganda va "BM'dan aniqlash" tugmasida ishlatiladi:
    client qurilmasa yoki waybill topilmasa bo'sh dict. Testlarda tarmoqqa
    chiqmaydi.
    """
    import os

    if os.getenv("BM_TEST_MODE"):
        return {}
    from ..core.companies import client_for_profile

    route_id = ""
    for part in str(profile.get("routeVariantId") or "").replace(",", " ").split():
        route_id = part
        break
    if not route_id:
        return {}
    client = client_for_profile(profile, auto_relogin=False)
    found = waybill_details(client, route_id, date_str)
    if found.get("routeName"):
        found["routeName"] = short_route_name(found["routeName"])
    return found


def detect_profile_starts(profile: dict, date_str: str = "") -> dict:
    """Profil yo'nalishi bo'yicha konechka nomlarini waybill'dan oladi."""
    details = detect_profile_fields(profile, date_str)
    return {key: details[key] for key in ("start1", "start2") if details.get(key)}


def detect_profile_route_name(profile: dict, date_str: str = "") -> str:
    """Profil yo'nalishi bo'yicha BM'dagi rasmiy yo'nalish nomini oladi."""
    return str(detect_profile_fields(profile, date_str).get("routeName") or "")


def build_rows(client: BMClient, route_id: str, date_str: str,
               profile: dict | None = None,
               duty_data: dict | None = None) -> dict:
    """Duty + grafik yo'nalishlari + konechka nomlarini yig'adi.

    duty_data berilgan bo'lsa duty API ga qayta so'rov yubormaydi.
    profile berilsa konechka nomlari va yo'nalish nomi profil'dan olinadi
    (start1 = 1-chiqish, start2 = 2-chiqish). Qaytaradi:
    {date, routeName, konechka: {dir: name}, groups: {dir: [rows]}}
    """
    duty = duty_data if duty_data is not None else DutyRepository(client).by_date(route_id, date_str)
    if not duty:
        raise ValueError(f"{date_str} uchun duty ma'lumoti topilmadi")
    if profile:
        names = {
            "UP": profile.get("start1") or "",
            "DOWN": profile.get("start2") or "",
        }
        route_name = profile.get("routeName") or "Yo'nalish"
        if not (names["UP"] and names["DOWN"]):
            auto = konechka_names(client, route_id, date_str)
            names["UP"] = names["UP"] or auto["UP"]
            names["DOWN"] = names["DOWN"] or auto["DOWN"]
    else:
        route_name = "Yo'nalish"
        names = konechka_names(client, route_id, date_str)

    groups = {"UP": [], "DOWN": []}

    # Har grafikning yo'nalishini PARALLEL aniqlaymiz: har biri 1-2 API
    # so'rovi bo'lgani uchun ketma-ket yig'ilishi (16 grafik ~30 so'rov)
    # rasm generatsiyasini 30-60 soniyaga cho'zardi.
    graphs = duty.get("graphs") or []

    def _direction_of(g: dict) -> str:
        try:
            direction = graph_start_direction(
                client, g.get("shiftGraphId") or "", g.get("startTime") or "")
        except Exception as exc:  # noqa: BLE001 - bitta grafik to'smaydi
            print(f"  [build_rows] graph_times xatolik: {exc}")
            direction = None
        return direction or "UP"  # aniqlanmagan bo'lsa shartli

    from concurrent.futures import ThreadPoolExecutor
    if graphs:
        with ThreadPoolExecutor(max_workers=min(8, len(graphs))) as pool:
            directions = list(pool.map(_direction_of, graphs))
    else:
        directions = []

    best = {}  # (direction, graph, shiftGraphId) -> qator indeksi
    for g, direction in zip(graphs, directions):
        shift_graph_id = g.get("shiftGraphId") or ""
        driver = g.get("driverName") or ""
        if g.get("hasSecond") and g.get("secondDriverName"):
            driver = f"{driver} / {g.get('secondDriverName')}"
        row = {
            "graph": g.get("graphName") or "?",
            "driver": driver,
            "driver_id": g.get("driverId") or "",
            "second_driver_id": (g.get("secondDriverId") or "")
                                 if g.get("hasSecond") else "",
            "bus": g.get("plateNum") or "",
            "start": g.get("startTime") or "",
            "end": g.get("endTime") or "",
            "shift": g.get("shiftName") or "",
        }
        key = (direction, row["graph"], shift_graph_id)
        idx = best.get(key)
        if idx is None:
            best[key] = len(groups[direction])
            groups[direction].append(row)
        elif row["start"] and _norm_time(row["start"]) < _norm_time(groups[direction][idx]["start"]):
            groups[direction][idx] = row
    for d in groups:
        groups[d].sort(key=lambda r: _norm_time(r["start"]))
    # Grafikdagi barcha haydovchilar (ID + ism) — Telegram bog'langan
    # profillarni jadval postida ko'rsatish uchun.
    _seen: set[str] = set()
    _drivers: list[dict] = []
    for _dkey in ("UP", "DOWN"):
        for _r in groups[_dkey]:
            for _did in (_r.get("driver_id"), _r.get("second_driver_id")):
                _did = str(_did or "").strip()
                if _did and _did not in _seen:
                    _seen.add(_did)
                    _drivers.append({"id": _did, "name": _r["driver"]})
    return {
        "date": date_str,
        "routeName": route_name,
        "konechka": names,
        "groups": groups,
        "drivers": _drivers,
    }


def run(
    client: BMClient,
    route_id: str,
    date_str: str = "",
    out_dir: str = "reports",
    send: bool = False,
    chat_id: str | None = None,
    profile: dict | None = None,
    duty_data: dict | None = None,
) -> dict:
    """Jadval rasmini yaratadi (ixtiyoriy Telegram'ga yuboradi)."""
    date_str = date_str or date.today().isoformat()
    data = build_rows(client, route_id, date_str, profile=profile, duty_data=duty_data)
    base = Path(out_dir)
    ensure_dir(base)
    img = base / f"driver-sheet_{route_id[:8]}_{date_str.replace('-', '')}.png"
    make_sheet_image(data, str(img))

    result = {"date": date_str, "image": str(img), "groups": {
        "UP": [r["graph"] for r in data["groups"]["UP"]],
        "DOWN": [r["graph"] for r in data["groups"]["DOWN"]],
    }, "konechka": data["konechka"], "drivers": data["drivers"],
        # Shaxsiy grafik kartasi uchun to'liq qatorlar (Grafikim tugmasi).
        "rows": data["groups"], "route_name": data["routeName"]}

    if send:
        from ..notifications.ops.grafik_sms import bot_targets
        from ..notifications.telegram import resend_keyboard, send_photo

        d = date.fromisoformat(date_str)
        caption = (f"🚌 {data['routeName']} | {d:%d.%m.%Y} | "
                   f"Kunlik chiqish jadvali (grafik × haydovchi × avtobus)")
        # Guruhlarga hech qachon yuborilmaydi — bot ichida qoladi: tugma
        # so'rovida so'rov chatiga, avtomatik oqimda shu yo'nalish egasi
        # kompaniya chatlariga (qoida: grafik_sms.bot_targets).
        targets = bot_targets(route_id, chat_id)
        manual = bool(chat_id) and not str(chat_id).startswith("-")
        if targets:
            # Avtomatik yuborishda (so'rov chatisiz) retry takror yubormaydi.
            pending = (targets if manual
                       else _pending_targets(route_id, date_str, targets))
            for t in pending:
                send_photo(str(img), caption=caption, chat_id=t,
                           reply_markup=resend_keyboard(profile.get("name") if profile else None))
            if pending and not manual:
                _mark_sent(route_id, date_str, pending)
            result["sent"] = True
            result["sent_to"] = targets
            if not pending:
                result["already_sent"] = True
        else:
            # Yuboriladigan chat yo'q — xato emas, retry ham kerak emas.
            result["sent"] = False
            result["no_group"] = True
    return result


def run_all(
    client: BMClient,
    date_str: str = "",
    out_dir: str = "reports",
    send: bool = False,
    chat_id: str | None = None,
    only: str = "",
) -> list:
    """Barcha sozlangan kompaniyalar (profillar) uchun jadval yaratadi.

    Har profil uchun zarur bo'lsa get-token-by-profile orqali token almashtiriladi.
    """
    from ..core.profiles import all_profiles

    date_str = date_str or date.today().isoformat()
    profiles = all_profiles()
    if only:
        profiles = [p for p in profiles if only.lower() in str(p.get("name", "")).lower()]
    if not profiles:
        raise ValueError("profiles.json'da kompaniyalar sozlanmagan. 'profiles add' yoki login-browser ishlating.")

    results = []
    for p in profiles:
        pid = str(p.get("profileId", "") or "").strip()
        route_id = str(p.get("routeVariantId", "") or "").strip()
        if not route_id:
            print(f"  [{p.get('name')}] routeVariantId ko'rsatilmagan, o'tkazib yuborildi.")
            continue
        try:
            if pid:
                # 401/403 bo'lsa login_for_profile asosiy tokenga qaytadi.
                client.login_for_profile(pid, fallback_to_main=True)
            res = run(
                client,
                route_id,
                date_str=date_str,
                out_dir=str(Path(out_dir) / safe_name(p.get("name", "profile"))),
                send=send,
                chat_id=chat_id,
                profile=p,
            )
            res["profile"] = p.get("name")
            results.append(res)
            print(f"  OK [{p.get('name')}]: {res['image']}" + (" (yuborildi)" if send else ""))
        except Exception as exc:
            results.append({"profile": p.get("name"), "error": str(exc)})
            print(f"  XATO [{p.get('name')}]: {exc}")
    return results
