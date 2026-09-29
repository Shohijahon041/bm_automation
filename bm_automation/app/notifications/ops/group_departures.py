"""Haydovchilar guruhiga chiqish (reys) vaqtlari bo'yicha bildirishnoma.

Bot qo'shilgan guruh(ler)ga (`TG_DRIVER_CHAT_ID`) joriy kunda *har bir
reys* bo'yicha eslatma yuboriladi: haydovchi ismi, avtobus raqami va
chiqish (reys) yoki yetib kelish (obed) vaqti.

Manba: joriy kun ``schedules`` (BM duty grafikalar — data'dagi
``shiftGraphId``) + ``duty/graph/times`` API slotlari. Slot turlari:
  - START / BETWEEN  -> "reys chiqishi": departureTime asos olinadi;
  - LUNCH            -> obed (tanaffus): arriveTime (yetib kelish) asos.

Yuborish vaqti: reys chiqishidan (yoki obed yetib kelishidan) ~40
SONIYA KEYIN. 40 soniyalik tekshiruv siklida o'tib ketmaslik uchun oyna
kengligi 45 s. Kun rejasi kuniga bir marta (silent refresh token bilan)
API'dan yuklanib ``state/group_departures.json`` ga saqlanadi; har bir
eslatma holat faylida belgilanadi — restart'da takroriy spam bo'lmaydi.

Holat `state/group_departures.json`:
  {"date": "YYYY-MM-DD", "build_at": "HH:MM",
   "items": [{"hhmm", "kind", "key", "name", "plate", "route"}, ...],
   "sent": ["key|hhmm|kind", ...]}

O'chirish: `GROUP_DEPARTURE_NOTIFY=off` (standart `off` — bot jim).
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html import escape
from pathlib import Path

from ...config.settings import telegram_settings
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.names import short_name
from ..telegram import send_message

STATE_FILE = Path("state") / "group_departures.json"

# "barchasi 40 sekund keyin jo'natilsin, soatga qarab" — 40 soniyalik sikl.
_CHECK_INTERVAL_S = 40.0
# Kunlik reja yuklashdagi parallel so'rovlar (grafik soniga qarab).
_FETCH_WORKERS = 8

_DEPARTURE_TYPES = ("START", "BETWEEN")
_LUNCH_TYPES = ("LUNCH",)

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_LAST_CHECK_AT = 0.0


def enabled() -> bool:
    s = telegram_settings()
    return str(s.get("group_departure_notify", "off")).lower() not in (
        "off", "0", "false")


def _targets() -> list[str]:
    """Eslatma boradigan guruh(ler) — TG_DRIVER_CHAT_ID, aks holda TG_CHAT_ID."""
    tg = telegram_settings()
    for key in ("driver_chat_id", "chat_id"):
        raw = str(tg.get(key) or "").strip()
        ids = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
        if ids:
            return ids
    return []


def _route_chats() -> dict:
    """route_id -> [chat_id] xaritasi (TG_DRIVER_ROUTE_CHATS).

    Format: "route_id:chat1,chat2;route_id:chat3". Bo'sh bo'lsa {}.
    """
    raw = str(telegram_settings().get("driver_route_chats") or "").strip()
    out = {}
    if not raw:
        return out
    for part in raw.split(";"):
        if ":" not in part:
            continue
        rid, _, chats = part.partition(":")
        rid = rid.strip()
        ids = [c.strip() for c in chats.replace(";", ",").split(",") if c.strip()]
        if rid and ids:
            out[rid] = ids
    return out


def _targets_for(route_id: str) -> list[str]:
    """Yo'nalish uchun guruh(lar) — xaritada bo'lsa o'zi, aks holda umumiy."""
    if route_id:
        mapped = _route_chats().get(route_id)
        if mapped:
            return mapped
    return _targets()


def _today() -> str:
    return date.today().isoformat()


def _current_hhmm() -> str:
    return time.strftime("%H:%M", time.localtime())


def _current_sec() -> int:
    """Kunning boshidan o'tgan soniyalar (HH:MM:SS)."""
    t = time.localtime()
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec


def _hhmm(value: str) -> str:
    """Vaqtdan HH:MM ni ajratadi (HH:MM:SS yoki oddiy vaqt)."""
    value = str(value or "").strip()
    if not value:
        return ""
    parts = value[:5].split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return value


def _sec_of_day(value: str) -> int | None:
    """'HH:MM[:SS]' -> kunning boshidan soniya; yaroqsiz bo'lsa None."""
    value = str(value or "").strip()
    if len(value) >= 8:
        value = value[:8]
    parts = value.split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    s = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + s


def _load_schedules(storage, date_str: str) -> list[dict]:
    """Joriy kun grafik qatorlari (haydovchi/avtobus/route nomlari bilan)."""
    ph = storage.db.ph
    return storage.query(
        "SELECT s.route_id, s.driver_id, s.vehicle_id, s.start_time, s.data, "
        " s.graph_name, "
        " d.full_name, v.plate_number, r.name AS route_name "
        "FROM schedules s "
        "LEFT JOIN drivers d ON d.external_id = s.driver_id "
        "LEFT JOIN vehicles v ON v.external_id = s.vehicle_id "
        "LEFT JOIN routes r ON r.external_id = s.route_id "
        f"WHERE s.date = {ph} AND s.driver_id != '' "
        "ORDER BY s.route_id, s.driver_id",
        (date_str,),
    )


def _parse_data(row: dict) -> dict:
    """Grafik data JSON'dan qo'shimcha maydonlar (fallback nom/raqam)."""
    out: dict = {}
    raw = row.get("data") or "{}"
    if isinstance(raw, dict):
        g = raw
    else:
        try:
            g = json.loads(raw)
        except (ValueError, TypeError):
            g = {}
    if not isinstance(g, dict):
        g = {}
    out["shift_graph_id"] = str(g.get("shiftGraphId") or "").strip()
    out["name"] = str(g.get("driverName") or "").strip()
    out["plate"] = str(g.get("plateNum") or "").strip()
    out["graph"] = str(g.get("graphName") or row.get("graph_name") or "").strip()
    return out


def _item_from_row(row: dict) -> dict:
    """Bitta grafik qatoridan eslatma elementi (reis/obed bog'lar)."""
    g = _parse_data(row)
    name = str(row.get("full_name") or "").strip() or g.get("name") \
        or str(row.get("driver_id") or "")
    name = short_name(name)
    plate = str(row.get("plate_number") or "").strip() or g.get("plate") \
        or str(row.get("vehicle_id") or "")
    route = str(row.get("route_name") or "").strip()
    graph = g.get("graph") or str(g.get("shift_graph_id") or "")[:8]
    key = f"{row.get('route_id') or ''}|{graph}"
    return {
        "key": key,
        "route_id": str(row.get("route_id") or ""),
        "name": name,
        "plate": plate,
        "route": route,
    }


def _graph_times(client, shift_graph_id: str) -> list:
    """Ikkala yo'nalish slotlarini birlashtiradi."""
    from ...repositories.duty_repo import DutyRepository

    repo = DutyRepository(client)
    slots = []
    for direction in ("UP", "DOWN"):
        try:
            slots.extend(repo.graph_times(shift_graph_id, direction) or [])
        except Exception:  # noqa: BLE001 - bir yo'nalish to'smasin
            continue
    return slots


def _classify_slots(slots: list) -> list[dict]:
    """Slotlardan {hhmm, sec, kind} hodisalarini ajratadi.

    START/BETWEEN -> "reys" (chiqish vaqti = departureTime),
    LUNCH -> "obed" (tanaffus — vaqt sifatida arriveTime olinadi,
    chunki haydovchi oshxonaga yetib kelgan payt tanaffus boshlanadi).
    ``sec`` = kunning boshidan soniya (40 soniyadan keyin yuborish uchun).
    """
    events: list[dict] = []
    for s in slots or []:
        kind = None
        if s.get("slotType") in _DEPARTURE_TYPES:
            kind = "reys"
        elif s.get("slotType") in _LUNCH_TYPES:
            kind = "obed"
        if not kind:
            continue
        # obed uchun tanaffus yetib kelish vaqtiga tayanaladi.
        raw = s.get("departureTime") if kind == "reys" else (
            s.get("arriveTime") or s.get("departureTime"))
        hhmm = _hhmm(raw)
        sec = _sec_of_day(raw)
        if not hhmm or sec is None:
            continue
        events.append({"hhmm": hhmm, "sec": sec, "kind": kind})
    return events


def _fresh_client():
    """Saqlangan token bilan client; brauzer loginsiz (bloklanmasligi uchun).

    Token yo'q yoki refresh ham ishlamasa ``None`` qaytariladi — reja
    yuklanmaydi va keyingi 40 soniyalik siklda qayta uriniladi.
    """
    try:
        from ...api.client import BMAuthError, BMClient
    except Exception:  # noqa: BLE001
        return None
    try:
        client = BMClient()
        if not client.load_tokens_from_file():
            return None
        if client._token_remaining(client.access_token) > 60:
            return client
        try:
            client._refresh()
            return client
        except BMAuthError:
            return None
    except Exception:  # noqa: BLE001 - bir sikl o'tkazib yuboriladi
        return None


def _build_plan(storage, date_str: str) -> dict:
    """Kunlik reys/obed rejasini DB schedules + API graph/times'dan yig'adi."""
    rows = _load_schedules(storage, date_str)
    if not rows:
        return {"ok": True, "items": []}
    client = _fresh_client()
    if client is None:
        return {"ok": False, "items": []}

    # shiftGraphId -> grafik uchun reja elementi (haydovchi/avtobus/route).
    infos: dict[str, dict] = {}
    for row in rows:
        sgid = _parse_data(row).get("shift_graph_id")
        if not sgid:
            continue
        infos.setdefault(sgid, _item_from_row(row))

    done = 0
    events_by_graph: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, max(1, len(infos)))) as pool:
        futures = {pool.submit(_graph_times, client, sgid): sgid for sgid in infos}
        for fut in futures:
            sgid = futures[fut]
            try:
                events = _classify_slots(fut.result())
            except Exception:  # noqa: BLE001
                continue
            if events:
                events_by_graph[sgid] = events
                done += 1

    # Barcha grafiklar API'ga chiqa olmasa — reja saqlanmaydi, qayta urinish.
    if not events_by_graph:
        return {"ok": done > 0, "items": []}

    items: list[dict] = []
    seen: set[str] = set()
    for sgid, info in infos.items():
        for ev in events_by_graph.get(sgid, []):
            sent_key = f"{info['key']}|{ev['hhmm']}|{ev['kind']}"
            if sent_key in seen:
                continue
            seen.add(sent_key)
            items.append({
                "hhmm": ev["hhmm"],
                "sec": ev["sec"],
                "kind": ev["kind"],
                "key": info["key"],
                "route_id": info.get("route_id") or "",
                "name": info["name"],
                "plate": info["plate"],
                "route": info["route"],
            })
    items.sort(key=lambda it: (it["hhmm"], it["kind"]))
    return {"ok": True, "items": items}


def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"date": "", "build_at": "", "items": [], "sent": []}


def _plan_for(storage) -> dict:
    """Joriy kun uchun reja — kerak bo'lsa kuniga bir marta API'dan yuklanadi."""
    state = _load_state()
    today = _today()
    if state.get("date") == today and isinstance(state.get("items", []), list):
        return state
    result = _build_plan(storage, today)
    if not result.get("ok"):
        return state  # reja saqlanmaydi — keyingi siklda qayta uriniladi
    state = {"date": today, "build_at": _current_hhmm(),
             "items": result.get("items", []), "sent": []}
    atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    return state


def _due_items(plan: dict, now_sec: int) -> dict[str, list[dict]]:
    """Chiqish (reys) yoki yetib kelish (obed) vaqtidan ~40 SONIYA KEYIN.

    Haydovchilar ketish vaqtidan 40 soniya keyin ogohlantiriladi — xabar
    ketish bo'lgandan so'ng guruhga boradi. 40 soniyalik tekshiruv siklida
    o'tib ketmaslik uchun oyna kengligi 45 s.

    Qaytish: chiqish daqiqasi (HH:MM) -> o'sha daqiqadagi elementlar.
    """
    by_hhmm: dict[str, list[dict]] = {}
    for it in plan.get("items", []):
        trigger = (it.get("sec") or 0) + 40  # asosiydan 40 soniya keyin
        if 0 <= now_sec - trigger <= 45:
            by_hhmm.setdefault(it.get("hhmm") or "", []).append(it)
    return by_hhmm


def _build_message(kind: str, hhmm: str, items: list[dict]) -> str:
    if kind == "obed":
        lines = [f"🍽 <b>OBED VAQTI</b> (tanaffus) · {hhmm}", ""]
    else:
        lines = [f"🚌 <b>CHIQISH VAQTI</b> · {hhmm}", ""]
    for it in items:
        line = f"• <b>{escape(it['name'])}</b> — {escape(it['plate'])}"
        if it.get("route"):
            line += f" ({escape(it['route'])})"
        lines.append(line)
    if kind == "obed":
        lines.extend(["", "😋 Yoqimli ishtaha!"])
    else:
        lines.extend(["", "✅ Belgilangan vaqtda yo'lga chiqing!"])
    return "\n".join(lines)


def _send_once() -> None:
    tg_all = _targets()
    if not tg_all:
        return
    storage = get_storage()
    if not storage or not getattr(storage, "enabled", False):
        return

    plan = _plan_for(storage)
    if not plan.get("items"):
        return

    hhmm = _current_hhmm()
    now_sec = _current_sec()
    due = _due_items(plan, now_sec)
    if not due:
        return

    state = plan
    already = set(state.get("sent", []))
    sent_keys = []
    send_plan: list[tuple[str, str]] = []  # (chat_id, text)
    for dep_hhmm, items in due.items():
        # Chiqish daqiqasi ichida reys/obed bo'yicha ajratamiz.
        by_kind: dict[str, list[dict]] = {}
        for it in items:
            by_kind.setdefault(it.get("kind") or "reys", []).append(it)
        for kind, k_items in by_kind.items():
            # YO'nalish bo'yicha alohida: har bir yo'nalish o'z guruhiga boradi.
            by_route: dict[str, list[dict]] = {}
            for it in k_items:
                rid = str(it.get("route_id") or "")
                if not rid:
                    rid = "__all__"
                by_route.setdefault(rid, []).append(it)
            for rid, ritems in by_route.items():
                chat_ids = _targets_for(None if rid == "__all__" else rid)
                if not chat_ids:
                    chat_ids = tg_all
                fresh = [it for it in ritems
                         if f"{state['date']}|{it['key']}|{it['hhmm']}|{it['kind']}"
                         not in already]
                if not fresh:
                    continue
                text = _build_message(kind, dep_hhmm, fresh)
                for cid in chat_ids:
                    send_plan.append((str(cid), text))
                sent_keys.extend(
                    f"{state['date']}|{it['key']}|{it['hhmm']}|{it['kind']}"
                    for it in fresh)
    if not send_plan:
        return

    for cid, text in send_plan:
        try:
            send_message(text, chat_id=cid)
        except Exception as exc:  # noqa: BLE001 - qolgan guruhlar yuboriladi
            print(f"Chiqish vaqti eslatmasi yuborilmadi [{cid}]: {exc}")

    state["sent"] = sorted(already | set(sent_keys))
    atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))


def check_and_send() -> None:
    """40 soniyalik tekshiruvni fon thread'da ishga tushiradi (idempotent).

    Asosiy poll thread bloklanmaydi; bir vaqtda bitta tekshiruv ishlaydi.
    """
    global _LAST_CHECK_AT, _THREAD
    if not enabled():
        return
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_send_once, daemon=True)
        _THREAD.start()