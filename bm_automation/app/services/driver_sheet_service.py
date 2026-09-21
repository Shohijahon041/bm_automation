"""Haydovchilar uchun kunlik jadval rasmini yaratish xizmati.

Yo'nalish: har grafik (P1..Pn) qaysi konechkadan chiqishini grafik vaqtlari
va waybill ma'lumotlaridan aniqlaydi, ikkita konechka bo'yicha guruhlab,
bitta rangli rasmga tushiradi (chizish `app.exporters.sheet_image` da).

Bu xizmat faqat haydovchilarga jadvalni rasm qilib yuborish uchun
ishlatiladi va asosiy hisobot jarayonlariga ta'sir qilmaydi.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from ..api.client import BMClient
from ..exporters.sheet_image import make_sheet_image
from ..repositories.duty_repo import DutyRepository
from ..repositories.waybill_repo import WaybillRepository
from ..utils.io import ensure_dir, safe_name
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
    return {
        "date": date_str,
        "routeName": route_name,
        "konechka": names,
        "groups": groups,
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
    }, "konechka": data["konechka"]}

    if send:
        from ..config.settings import telegram_settings
        from ..notifications.telegram import resend_keyboard, send_photo

        d = date.fromisoformat(date_str)
        caption = (f"🚌 {data['routeName']} | {d:%d.%m.%Y} | "
                   f"Kunlik chiqish jadvali (grafik × haydovchi × avtobus)")
        target = chat_id or telegram_settings().get("driver_chat_id") or None
        send_photo(str(img), caption=caption, chat_id=target,
                   reply_markup=resend_keyboard(profile.get("name") if profile else None))
        result["sent"] = True
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
