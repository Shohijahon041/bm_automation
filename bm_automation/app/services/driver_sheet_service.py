"""Haydovchilar uchun kunlik jadval rasmini yaratish xizmati.

Yo'nalish: har grafik (P1..Pn) qaysi konechkadan chiqishini grafik vaqtlari
va waybill ma'lumotlaridan aniqlaydi, ikkita konechka bo'yicha guruhlab,
bitta rangli rasmga tushiradi (chizish `app.exporters.sheet_image` da).

Bu xizmat faqat haydovchilarga jadvalni rasm qilib yuborish uchun
ishlatiladi va asosiy hisobot jarayonlariga ta'sir qilmaydi.
"""

from __future__ import annotations

import json
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
    "graph_start_direction",
    "konechka_names",
    "run",
    "run_all",
]

DEFAULT_KONECHKA = {
    "UP": "Temiryo'l kesishmasi-10",
    "DOWN": "FERGANA TEX SERVIS YURIY",
}

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


def konechka_names(client: BMClient, route_id: str, date_str: str) -> dict:
    """Waybill'lardan har yo'nalish uchun eng ko'p uchragan startName (konechka)."""
    names = dict(DEFAULT_KONECHKA)
    try:
        data = WaybillRepository(client).report(route_id, date_str, date_str)
    except Exception as exc:
        print(f"  [konechka_names] waybill xatolik: {exc}")
        return names
    counts = {"UP": Counter(), "DOWN": Counter()}
    for wb in data or []:
        d = wb.get("direction")
        sn = wb.get("startName")
        if d in counts and sn:
            counts[d][normalize_uz(sn)] += 1
    for d in ("UP", "DOWN"):
        if counts[d]:
            names[d] = counts[d].most_common(1)[0][0]
    return names


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
    if profile and (profile.get("start1") or profile.get("start2")):
        names = {
            "UP": profile.get("start1") or DEFAULT_KONECHKA["UP"],
            "DOWN": profile.get("start2") or DEFAULT_KONECHKA["DOWN"],
        }
        route_name = profile.get("routeName") or "Yo'nalish"
    else:
        route_name = "10-yo'nalish"
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
        from ..config.settings import telegram_settings
        from ..notifications.telegram import resend_keyboard, send_photo

        d = date.fromisoformat(date_str)
        caption = (f"🚌 {data['routeName']} | {d:%d.%m.%Y} | "
                   f"Kunlik chiqish jadvali (grafik × haydovchi × avtobus)")
        # Yo'nalishga biriktirilgan guruhlarga faqat o'sha yo'nalish grafigi yuborilsin.
        targets = []
        try:
            from ..notifications.ops.group_departures import _route_targets
            targets = _route_targets(route_id, chat_id)
        except Exception:
            # Fallback: eski logika (agarda import xatosi bo'lsa)
            s = telegram_settings()
            targets = [chat_id] if chat_id else []
            if not targets:
                t = s.get("driver_chat_id") or s.get("chat_id") or ""
                targets = [x.strip() for x in str(t).replace(";", ",").split(",") if x.strip()]
        if targets:
            # Avtomatik yuborishda (chat_id berilmagan) retry takror yubormaydi.
            pending = targets if chat_id else _pending_targets(route_id, date_str, targets)
            for t in pending:
                send_photo(str(img), caption=caption, chat_id=t,
                           reply_markup=resend_keyboard(profile.get("name") if profile else None))
            if pending and not chat_id:
                _mark_sent(route_id, date_str, pending)
            result["sent"] = True
            result["sent_to"] = targets
            if not pending:
                result["already_sent"] = True
        else:
            # Yo'nalishga guruh biriktirilmagan — xato emas, retry ham kerak emas.
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
                client.login_by_profile(pid)
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
