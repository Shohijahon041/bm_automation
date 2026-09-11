"""Ertangi kunga haydovchi chiqishini rejalashtirish.

Admin haydovchi ismini yozadi — tizim haydovchining BUGUNGI grafigidan
keyingi grafikni aniqlab, ERTANGI rejaga qo'shadi (avtobus ham davom
etadi). Grafik raqami yozilsa — o'sha grafikka qo'yiladi.

Grafiklar `schedules` jadvalidagi `graph_name` (masalan: ``P1``..``P10``).
"Keyingi grafik" = bugungi grafikdan keyingisi (P5 → P6, P10 → P1).

Holat ``state/schedule_plan.json`` faylida (bot restart'da ham yo'qolmaydi):
    {"dates": {"YYYY-MM-DD": [entry, ...]}}

Flow holati ``state/planning_flow.json`` da (interaktiv dialog davomi).

Funksiyalar:
  - `add(driver_id, name, ...)`  — rejaga yozish
  - `add_from_text(text, ...)`   — tabiiy matndan (chat / AI) qo'shish
  - `plan_text()`                — ertangi reja matni
  - `start/current/input/cancel` — interaktiv flow
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.names import short_name

STATE_FILE = Path("state") / "schedule_plan.json"
FLOW_FILE = Path("state") / "planning_flow.json"

_PLAN: dict[str, list[dict]] = {}
_FLOW: dict[int, dict] = {}
_LOCK = threading.Lock()

_FLOW_STEPS = {"driver", "graph"}


# ------------------------------------------------------------------- holat

def target_date() -> str:
    """Reja sanasi — ertangi kun."""
    return (date.today() + timedelta(days=1)).isoformat()


def _load() -> None:
    if _PLAN:
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        dates = data.get("dates") if isinstance(data, dict) else None
        if isinstance(dates, dict):
            for k, v in dates.items():
                if isinstance(v, list):
                    _PLAN[k] = v
    except Exception:  # noqa: BLE001
        pass


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(
            {"dates": _PLAN}, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Reja holati saqlanmadi: {exc}")


def entries(ds: str | None = None) -> list[dict]:
    """Berilgan sana (standart: ertangi kun) uchun reja yozuvlari."""
    _load()
    ds = ds or target_date()
    return _PLAN.setdefault(ds, [])


def clear(ds: str | None = None) -> int:
    """Rejani tozalaydi; o'chirilgan yozuvlar sonini qaytaradi."""
    _load()
    ds = ds or target_date()
    with _LOCK:
        n = len(_PLAN.pop(ds, []))
        _save()
        return n


def remove(driver_id: str, ds: str | None = None) -> bool:
    _load()
    ds = ds or target_date()
    with _LOCK:
        lst = _PLAN.get(ds, [])
        rest = [e for e in lst if str(e.get("driver_id")) != str(driver_id)]
        if len(rest) == len(lst):
            return False
        _PLAN[ds] = rest
        _save()
        return True


# --------------------------------------------------------- haydovchi qidirish

def find_driver(name: str) -> list[dict]:
    """Ism bo'yicha haydovchilar (substring, case-insensitive)."""
    name = (name or "").strip().lower()
    if not name:
        return []
    rows = get_storage().query(
        "SELECT external_id, full_name, route_id FROM drivers")
    out = []
    for r in rows:
        full = str(r.get("full_name") or "").strip()
        low = full.lower()
        if name in low or low in name:
            out.append({
                "driver_id": str(r.get("external_id") or ""),
                "name": short_name(full),
                "route_id": str(r.get("route_id") or ""),
            })
    out.sort(key=lambda d: len(d["name"]))
    return out


def driver_name(driver_id: str) -> str:
    ph = get_storage().db.ph
    for r in get_storage().query(
            "SELECT external_id, full_name FROM drivers "
            f"WHERE external_id = {ph}", (driver_id,)):
        if str(r.get("external_id")) == str(driver_id):
            return short_name(str(r.get("full_name") or driver_id))
    return driver_id


# ------------------------------------------------------------ grafik mantiqi

def _graph_number(graph: str) -> int | None:
    m = re.search(r"(\d+)", graph or "")
    return int(m.group(1)) if m else None


def _sorted_graphs(route_id: str) -> list[str]:
    ph = get_storage().db.ph
    rows = get_storage().query(
        "SELECT DISTINCT graph_name FROM schedules "
        f"WHERE route_id = {ph} AND graph_name != ''", (route_id,))
    names = {str(r.get("graph_name") or "") for r in rows}
    names.discard("")
    return sorted(names, key=lambda g: (_graph_number(g) or 10**9, g))


def _graph_label(route_id: str, n: int) -> str:
    for g in _sorted_graphs(route_id):
        if _graph_number(g) == n:
            return g
    return f"P{n}"


def _next_graph(graph: str, graphs: list[str]) -> str:
    if graphs and graph in graphs:
        return graphs[(graphs.index(graph) + 1) % len(graphs)]
    n = _graph_number(graph)
    return f"P{n + 1}" if n is not None else (graph or "P1")


def today_schedule(driver_id: str) -> list[dict]:
    """Haydovchining bugungi grafik yozuvlari (schedules)."""
    ph = get_storage().db.ph
    return get_storage().query(
        "SELECT route_id, graph_name, vehicle_id, shift_name FROM schedules "
        f"WHERE driver_id = {ph} AND date = {ph} AND graph_name != ''",
        (str(driver_id), date.today().isoformat()))


def _best_today(driver_id: str) -> dict | None:
    rows = today_schedule(driver_id)
    if not rows:
        return None
    return max(rows, key=lambda r: _graph_number(r.get("graph_name")) or 0)


def _taken(route_id: str, graph: str, ds: str) -> bool:
    for e in entries(ds):
        if (str(e.get("route_id")) == str(route_id)
                and str(e.get("graph_name")) == graph):
            return True
    ph = get_storage().db.ph
    return bool(get_storage().query(
        "SELECT 1 FROM schedules "
        f"WHERE date = {ph} AND route_id = {ph} AND graph_name = {ph} "
        "LIMIT 1", (ds, str(route_id), graph)))


def _route_name(route_id: str) -> str:
    if not route_id:
        return "-"
    ph = get_storage().db.ph
    rows = get_storage().query(
        f"SELECT external_id, name FROM routes WHERE external_id = {ph}",
        (route_id,))
    for r in rows:
        if str(r.get("external_id")) == str(route_id):
            return str(r.get("name") or route_id)
    return route_id


def _vehicle_name(vehicle_id: str) -> str:
    if not vehicle_id:
        return ""
    ph = get_storage().db.ph
    rows = get_storage().query(
        "SELECT external_id, plate_number FROM vehicles "
        f"WHERE external_id = {ph}", (vehicle_id,))
    for r in rows:
        if str(r.get("external_id")) == str(vehicle_id):
            return str(r.get("plate_number") or vehicle_id)
    return vehicle_id


# ------------------------------------------------------------------ qo'shish

def auto_plan(driver_id: str) -> dict:
    """Haydovchining bugungi grafigidan keyingi grafikni hisoblaydi.

    Qaytaradi: {route_id, graph_name, vehicle_id}. Bugun grafikda bo'lmasa
    ValueError — grafik raqamini so'raymiz.
    """
    best = _best_today(driver_id)
    if not best:
        raise ValueError(
            "👨‍✈️ Bu haydovchi bugun grafikda yo'q — grafik raqamini yozing.")
    route_id = str(best.get("route_id") or "")
    graph = str(best.get("graph_name") or "")
    vehicle_id = str(best.get("vehicle_id") or "")
    graphs = _sorted_graphs(route_id)
    ds = target_date()
    nxt = _next_graph(graph, graphs)
    guard = 0
    while _taken(route_id, nxt, ds) and guard < max(len(graphs), 12):
        nxt = _next_graph(nxt, graphs)
        guard += 1
    return {"route_id": route_id, "graph_name": nxt,
            "vehicle_id": vehicle_id}


def add(driver_id: str, name: str, graph: str | None = None,
        route_id: str = "", vehicle_id: str = "",
        by: int = 0) -> tuple[bool, str]:
    """Rejaga haydovchi yozadi.

    `graph` berilsa o'sha grafik (raqam yoki to'liq nom, masalan "5"/"P5"),
    berilmasa bugungi grafikdan keyingisi avtomatik aniqlanadi.

    Qaytaradi: (muvaffaqiyatmi, xabar).
    """
    driver_id = str(driver_id or "").strip()
    if not driver_id:
        return False, "Haydovchi topilmadi."
    name = name or driver_name(driver_id)

    if graph:
        g_raw = str(graph).upper().strip()
        m = re.search(r"(\d+)", g_raw.replace("ГРАФИК", "").replace("GRAFIK", ""))
        if not m:
            return False, f"Grafik raqami topilmadi: {graph}"
        n = int(m.group(1))
        if route_id:
            g = _graph_label(route_id, n)
        else:
            g = f"P{n}"
    else:
        try:
            info = auto_plan(driver_id)
        except ValueError as exc:
            return False, str(exc)
        route_id = info["route_id"]
        g = info["graph_name"]
        vehicle_id = info["vehicle_id"]

    ds = target_date()
    entry = {
        "driver_id": driver_id,
        "name": name,
        "route_id": route_id,
        "graph_name": g,
        "vehicle_id": vehicle_id,
        "by": int(by or 0),
        "at": date.today().isoformat(),
    }

    warn = ""
    # Shu (yo'nalish, grafik) bandmi?
    if route_id and g:
        for e in entries(ds):
            if (str(e.get("route_id")) == str(route_id)
                    and str(e.get("graph_name")) == g
                    and str(e.get("driver_id")) != driver_id):
                warn = (f"\n\n⚠️ {esc_name(e.get('name'))} allaqachon "
                        f"shu grafikda (P raqami {g}).")
                break

    with _LOCK:
        lst = entries(ds)
        replaced = any(str(e.get("driver_id")) == driver_id for e in lst)
        lst = [e for e in lst if str(e.get("driver_id")) != driver_id]
        lst.append(entry)
        _PLAN[ds] = lst
        _save()

    route_txt = esc_name(_route_name(route_id)) if route_id else "—"
    veh = _vehicle_name(vehicle_id)
    veh_txt = f" · 🚌 {esc_name(veh)}" if veh else ""
    act = "yangilandi" if replaced else "qo'shildi"
    return True, (
        f"✅ <b>{esc_name(name)}</b> ertangi rejaga {act}.\n\n"
        f"🛣 {route_txt}\n"
        f"📊 Grafik: <b>{esc_name(g)}</b>{veh_txt}\n"
        f"📅 Sana: {esc_name(ds)}"
        f"{warn}")


def esc_name(value) -> str:
    from ...utils.tgformat import esc
    return esc(value)


def _parse_text(text: str) -> tuple[str, str | None]:
    """Matndan (haydovchi ismi, grafik raqami) ni ajratadi.

    Grafik raqami quyidagicha berilishi mumkin:
      "Yuldashov 5", "Yuldashov P5", "Yuldashov 5-grafik",
      "Yuldashovni 2-grafikaga yoz", "grafik 5 Yuldashov"
    Raqam topilmasa butun matn ism deb olinadi.
    """
    t = text or ""
    # Kirish/fe'l so'zlarni olib tashlaymiz
    t = re.sub(
        r"\b(rejalashtir|rejaga\s+yoz|rejaga\s+qo'sh|yoz|qo'sh|kirit"
        r"|ertangi\s+kunga|ertaga|ertangi|ertalab)\b",
        " ", t, flags=re.IGNORECASE)
    t = re.sub(r"(grafikka|grafikaga|grafigiga|grafika|график)", " ", t,
               flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()

    m = re.search(r"([pг])?\s*(\d{1,2})", t, flags=re.IGNORECASE)
    graph = None
    name_part = t
    if m:
        graph = f"P{int(m.group(2))}"
        name_part = (t[:m.start()] + t[m.end():]).strip(" -—–.,;:")
        name_part = re.sub(r"\s+", " ", name_part).strip()

    # Uzbek egalik qo'shimchalari ("Yuldashovni" → "Yuldashov")
    name_part = re.sub(r"(ni|si|ni)\s*$", "", name_part, flags=re.IGNORECASE)
    name_part = name_part.strip()
    return name_part or text.strip(), graph


def add_from_text(text: str, by: int = 0) -> str:
    """Tabiiy matndan rejaga qo'shadi (chat / AI uchun).

    Misollar:
      "Yuldashov"                      → bugungi grafikdan keyingisi
      "Yuldashov 5" / "Yuldashov P5"   → 5-grafik
    """
    name_part, graph = _parse_text(text)
    cands = find_driver(name_part)
    if not cands:
        return ("❌ Haydovchi topilmadi. To'liq ism yozing "
                "(masalan: Yuldashov).")
    if len(cands) > 1:
        names = ",\n".join(f"• {esc_name(c['name'])}" for c in cands[:6])
        return (f"🔎 Bir nechta moslik topildi. To'liq ism yozing:\n{names}")
    d = cands[0]
    ok, msg = add(d["driver_id"], d["name"], graph=graph,
                  route_id=d.get("route_id") or "", by=by)
    return msg


# ----------------------------------------------------------------- reja matni

def plan_text(ds: str | None = None) -> str:
    """Ertangi reja matni (HTML)."""
    ds = ds or target_date()
    lst = entries(ds)
    if not lst:
        return (f"📝 <b>ERTANGI REJA</b>\n\n"
                f"📅 {esc_name(ds)}\n"
                f"Reja bo'sh. Haydovchi ismini yozing — keyingi grafikka "
                f"qo'shaman (yoki <b>/plan</b>).")
    # Yo'nalish → grafik → haydovchi guruhlash
    groups: dict[str, list[dict]] = {}
    for e in lst:
        rid = str(e.get("route_id") or "")
        groups.setdefault(rid, []).append(e)
    for rid in groups:
        groups[rid].sort(key=lambda e: _graph_number(e.get("graph_name")) or 0)

    parts = [f"📝 <b>ERTANGI REJA</b>", f"📅 {esc_name(ds)}", ""]
    total = 0
    for rid, items in groups.items():
        rname = _route_name(rid) if rid else "—"
        parts.append(f"🛣 <b>{esc_name(rname)}</b>")
        for e in items:
            veh = _vehicle_name(e.get("vehicle_id"))
            veh_txt = f" 🚌 {esc_name(veh)}" if veh else ""
            parts.append(f"  {esc_name(e.get('graph_name'))} · "
                         f"{esc_name(e.get('name'))}{veh_txt}")
        parts.append("")
        total += len(items)
    parts.append(f"Jami: <b>{total}</b> haydovchi")
    return "\n".join(parts)


def plan_text_plain(ds: str | None = None) -> str:
    """LLM konteksti uchun tagsiz reja matni."""
    from ...utils.tgformat import esc as _e
    ds = ds or target_date()
    lst = entries(ds)
    if not lst:
        return f"{ds} uchun ertangi reja bo'sh."
    out = [f"{ds} uchun ertangi reja:"]
    groups: dict[str, list[dict]] = {}
    for e in lst:
        groups.setdefault(str(e.get("route_id") or ""), []).append(e)
    for rid, items in groups.items():
        out.append(f"- {_route_name(rid) if rid else '-'}:")
        for e in items:
            out.append(f"  {e.get('graph_name')} -> {e.get('name')}")
    return "\n".join(out)


# ----------------------------------------------------------- interaktiv flow

def flow_start(chat_id: int) -> str:
    """Rejalashtirish dialogini boshlaydi. Prompt qaytaradi."""
    _load_flow()
    with _LOCK:
        _FLOW[chat_id] = {"step": "driver", "data": {}}
        _save_flow()
    return ("👨‍✈️ Haydovchi ismini yozing (masalan: <b>Yuldashov</b>).\n"
            "Grafik raqamini yozmoqchi bo'lsangiz: <b>Ism 5</b>")


def flow_current(chat_id: int) -> dict | None:
    _load_flow()
    return _FLOW.get(chat_id)


def flow_cancel(chat_id: int) -> bool:
    _load_flow()
    with _LOCK:
        ok = _FLOW.pop(chat_id, None) is not None
        if ok:
            _save_flow()
        return ok


def flow_input(chat_id: int, value: str) -> str:
    """Dialog davomi. Yakunlansa qo'shishni bajarib, xabar qaytaradi."""
    _load_flow()
    with _LOCK:
        st = _FLOW.get(chat_id)
        if not st:
            return "Rejalashtirish boshlanmagan. <b>/plan</b> yozing."
        step = st["step"]

    if step == "driver":
        name_part, graph = _parse_text(value)
        cands = find_driver(name_part)
        if not cands:
            return ("❌ Haydovchi topilmadi. To'liq ism yozing "
                    "(masalan: Yuldashov).")
        if len(cands) > 1:
            names = ",\n".join(f"• {esc_name(c['name'])}" for c in cands[:6])
            return (f"🔎 Bir nechta moslik topildi. To'liq ism yozing:\n"
                    f"{names}")
        d = cands[0]
        with _LOCK:
            st = _FLOW.get(chat_id)
            if not st:
                return "Rejalashtirish boshlanmagan. <b>/plan</b> yozing."
            st.setdefault("data", {}).update({
                "driver_id": d["driver_id"], "name": d["name"],
                "route_id": d.get("route_id") or ""})
            st["step"] = "graph"
            _save_flow()
        if graph:
            # "Ism 5" — grafik ham berilgan, darhol yakunlaymiz
            _, msg = add(d["driver_id"], d["name"], graph=graph,
                         route_id=d.get("route_id") or "", by=chat_id)
            return msg
        return ("📊 Grafik raqamini yozing (masalan: <b>5</b>) yoki "
                "<b>-</b> — bugungi grafikdan keyingisi avtomatik "
                "aniqlanadi.")
    elif step == "graph":
        raw = (value or "").strip()
        if raw in ("-", "0", "auto", "avto"):
            graph = None
        else:
            graph = raw
        with _LOCK:
            st = _FLOW.get(chat_id)
            if not st:
                return "Rejalashtirish boshlanmagan. <b>/plan</b> yozing."
            did = st.get("data", {}).get("driver_id", "")
            nm = st.get("data", {}).get("name", "")
            rid = st.get("data", {}).get("route_id", "")
            st["step"] = "done"
            _save_flow()
        _, msg = add(did, nm, graph=graph, route_id=rid, by=chat_id)
        return msg
    return "Rejalashtirish boshlanmagan."


def _load_flow() -> None:
    if _FLOW:
        return
    try:
        with open(FLOW_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("chats"), dict):
            for k, v in data["chats"].items():
                try:
                    _FLOW[int(k)] = v
                except (TypeError, ValueError):
                    continue
    except Exception:  # noqa: BLE001
        pass


def _save_flow() -> None:
    try:
        FLOW_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(FLOW_FILE, json.dumps(
            {"chats": _FLOW}, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Reja flow holati saqlanmadi: {exc}")
