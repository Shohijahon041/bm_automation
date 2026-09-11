"""AI o'z-o'zini rivojlantirish (self-review) — loglar va takroriy muammolar tahlili.

Bot poll-tsikli har iteratsiyada `check_and_send()` chaqiradi; u kuniga bir
marta (soat `AI_SELFREVIEW_HOUR`, standart 9) log/xatolar manbasini tahlil
qiladi va ADMIN/DISPATCHER'ga tavsiyalar yuboradi. Qo'lda ko'rish: `/insights`,
CLI: `python -m bm_automation insights`.

Tahlil manbalari:
- DB `errors` jadvali (xatoliklar jurnali) — takroriy xatolar, hafta kuni
  bo'yicha taqsimot ("har kuni takrorlanadigan holatlar");
- `automation_runs` — muvaffaqiyatsiz avto-ishlar;
- `metrics.problems` — kunlik takrorlanadigan muammolar (GPS/texnik/jadval),
  bir xil avtobus bir necha kun ketma-ket aniqlansa qayd qilinadi;
- `logs/bm.log` fayl logi — LLM konteksti uchun oxirgi satrlar.

OpenRouter sozlangan bo'lsa tahlil natijalari LLM'ga uzatiladi va u aniq
tavsiyalar beradi; aks holda lokal qoidaviy tavsiyalar ishlaydi
(`configured()` bo'lmasa bot hech qachon buzilmaydi).

Holat `state/self_review.json`:
  {"date": "YYYY-MM-DD"}  # bugun allaqachon yuborilganmi

`AI_SELFREVIEW=off` bilan o'chiriladi.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ...config.settings import telegram_settings
from ...dashboard.metrics import Metrics
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ...utils.logger import tail as log_tail
from ...utils.names import short_name
from ..telegram import send_message
from . import kb, openrouter
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "self_review.json"

# Tekshiruvlar orasidagi eng kichik interval (kuniga bir marta uchun yetarli).
_CHECK_INTERVAL_S = 300.0

# Takroriy muammolar uchun skaner oynasi (kun) — oxirgi hafta yetarli.
_RECUR_SCAN_DAYS = 7

# Hafta kunlari (analiz uchun).
_WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba",
             "Payshanba", "Juma", "Shanba", "Yakshanba"]

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0  # in-memory interval hisoblagichi (restart'da nollanadi)


def enabled() -> bool:
    s = telegram_settings()
    return str(s.get("selfreview", "on")).lower() not in ("off", "0", "false")


def _hour() -> int:
    s = telegram_settings()
    try:
        return max(0, min(23, int(str(s.get("selfreview_hour", "9")))))
    except (TypeError, ValueError):
        return 9


def _days() -> int:
    s = telegram_settings()
    try:
        return max(1, min(90, int(str(s.get("selfreview_days", "14")))))
    except (TypeError, ValueError):
        return 14


def _targets() -> list[int]:
    roles = configured_roles()
    return [cid for cid, role in roles.items()
            if role in (Role.ADMIN, Role.DISPATCHER)]


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - holat yo'q bo'lsa boshidan
        pass
    return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - tahlilni buzmaydi
        print(f"AI tahlil holati saqlanmadi: {exc}")


def _norm_message(msg: str) -> str:
    """Xabarni takroriy guruhlash uchun normallashtiradi (raqamlar -> #)."""
    return re.sub(r"\d+", "#", (msg or "").strip()).strip() or "-"


# ---------------------------------------------------------------- analiz

def analyze(days: int | None = None) -> dict:
    """Log/xatolar manbasini tahlil qiladi — findings + tavsiyalar uchun xom ma'lumot."""
    days = max(1, int(days or _days()))
    st = get_storage()
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    ph = st.db.ph

    errors = st.query(
        "SELECT source, message, occurred_at FROM errors"
        f" WHERE occurred_at >= {ph} ORDER BY occurred_at ASC", (start,))
    runs = st.query(
        "SELECT run_id, trigger, status, started_at FROM automation_runs"
        f" WHERE started_at >= {ph} ORDER BY started_at DESC", (start,))

    # Xatolar bo'yicha kunlar va hafta kunlari taqsimoti.
    error_days: dict[str, int] = {}
    weekday_counts = [0] * 7
    for e in errors:
        at = e.get("occurred_at") or ""
        day = at[:10]
        if day:
            error_days[day] = error_days.get(day, 0) + 1
            try:
                weekday_counts[date.fromisoformat(day).weekday()] += 1
            except ValueError:
                pass

    # Takroriy xatolar (source + normallashtirilgan matn bo'yicha guruh).
    groups: dict[tuple[str, str], dict] = {}
    for e in errors:
        src = str(e.get("source") or "-")
        msg = str(e.get("message") or "").strip() or "-"
        key = (src, _norm_message(msg))
        g = groups.setdefault(key, {
            "source": src, "message": msg, "norm": _norm_message(msg),
            "n": 0, "first": None, "last": None,
        })
        g["n"] += 1
        at = e.get("occurred_at") or ""
        if g["first"] is None or (at and at < g["first"]):
            g["first"] = at
        if g["last"] is None or (at and at > g["last"]):
            g["last"] = at
    top_errors = sorted(groups.values(),
                        key=lambda g: (-g["n"], g["last"] or ""))[:5]

    # Eng "takroriy" hafta kuni (xatolarning kamida 40%i shu kunga to'g'ri kelsa).
    total = len(errors)
    top_wd = None
    if total:
        wi = max(range(7), key=lambda i: weekday_counts[i])
        if weekday_counts[wi] >= 2 and weekday_counts[wi] / total >= 0.4:
            top_wd = _WEEKDAYS[wi]

    # Muvaffaqiyatsiz avto-ishlar.
    failed = [r for r in runs
              if str(r.get("status") or "").upper() in ("ERROR", "FAILED")]
    failure_triggers = Counter(str(r.get("trigger") or "-") for r in failed)

    # Takroriy muammolar: bir xil avtobus GPS/texnik muammosi bir necha kun
    # aniqlansa (jadval/noma'lum kategoriyalari har kuni takrorlanishi mumkin —
    # shovqin bermaslik uchun hisobga olinmaydi).
    recur: list[dict] = []
    try:
        met = Metrics(storage=st)
        scan = min(days, _RECUR_SCAN_DAYS)
        vehicle_days: dict[str, set] = {}
        vehicle_cat: dict[str, str] = {}
        for i in range(scan):
            ds = (today - timedelta(days=i)).isoformat()
            items = (met.problems({"from": ds, "to": ds}) or {}).get("items") or {}
            for cat, cat_items in items.items():
                if cat not in ("gps", "technical"):
                    continue
                for it in cat_items:
                    vid = str(it.get("vehicle") or "")
                    if not vid:
                        continue
                    vehicle_days.setdefault(vid, set()).add(ds)
                    vehicle_cat.setdefault(vid, str(cat))
        for vid, dayset in vehicle_days.items():
            if len(dayset) >= 3:
                recur.append({
                    "vehicle": vid, "category": vehicle_cat[vid],
                    "days": len(dayset), "window": scan,
                })
        recur.sort(key=lambda r: -r["days"])
        recur = recur[:5]
    except Exception:  # noqa: BLE001 - muammolar skaneri bezak, xato bo'lsa bo'sh
        recur = []

    return {
        "days": days,
        "start": start,
        "end": today.isoformat(),
        "error_total": total,
        "error_days": dict(sorted(error_days.items())),
        "top_errors": top_errors,
        "weekday": top_wd,
        "failures": len(failed),
        "failure_triggers": dict(failure_triggers.most_common(3)),
        "recurring": recur,
        "log_tail": log_tail(150),
        "drivers": _analyze_drivers(st, start, today.isoformat()),
    }


def _analyze_drivers(st, start: str, end: str) -> dict:
    """Haydovchilar bo'yicha tahlil — xatolar, ishlash, avtobus almashtirish."""
    ph = st.db.ph
    issue_statuses = "('NOT_ACCEPTED','PENDING_ACCESS','REJECTED')"
    # Haydovchilar bo'yicha reyslar va xatoliklar (route_id bilan)
    trip_rows = st.query(
        "SELECT driver_id, vehicle_id, date, status, route_id FROM trips "
        f"WHERE date >= {ph} AND date <= {ph} AND driver_id != '' "
        "ORDER BY driver_id, date", (start, end))
    # Ish kunlari
    work_rows = st.query(
        "SELECT driver_id, SUM(trip_count) trips, SUM(distance_km) km, "
        "  COUNT(DISTINCT date) work_days "
        "FROM driver_work_logs "
        f"WHERE date >= {ph} AND date <= {ph} AND driver_id != '' "
        "GROUP BY driver_id", (start, end))
    # Haydovchi nomlari
    name_rows = st.query(
        "SELECT external_id, full_name FROM drivers WHERE external_id != ''")
    name_map = {r["external_id"]: short_name(str(r["full_name"] or ""))
            for r in name_rows}
    # Yo'nalish nomlari
    route_rows = st.query(
        "SELECT external_id, name FROM routes WHERE external_id != ''")
    route_map = {r["external_id"]: r["name"] for r in route_rows}

    # Reyslar bo'yicha tahlil
    by_driver: dict[str, dict] = {}
    for r in trip_rows:
        did = r["driver_id"]
        if did not in by_driver:
            by_driver[did] = {
                "driver_id": did,
                "name": name_map.get(did, did),
                "trips": 0, "issues": 0, "vehicles": set(),
                "work_days": set(), "routes": set(),
            }
        d = by_driver[did]
        d["trips"] += 1
        d["work_days"].add(r.get("date", ""))
        if r.get("vehicle_id"):
            d["vehicles"].add(r["vehicle_id"])
        rid = r.get("route_id") or ""
        if rid:
            d["routes"].add(rid)
        status = (r.get("status") or "").upper()
        if status in ("NOT_ACCEPTED", "PENDING_ACCESS", "REJECTED"):
            d["issues"] += 1

    # Ish kunlari qatorlarini qo'shish
    for wr in work_rows:
        did = wr["driver_id"]
        if did in by_driver:
            by_driver[did]["manual_km"] = round(wr.get("km", 0), 1)
            by_driver[did]["manual_trips"] = wr.get("trips", 0)
            by_driver[did]["manual_work_days"] = wr.get("work_days", 0)

    # Natijalarni tayyorlash
    drivers = list(by_driver.values())
    for d in drivers:
        d["vehicles_count"] = len(d["vehicles"])
        d["vehicles"] = sorted(d["vehicles"])
        d["routes_count"] = len(d["routes"])
        d["routes"] = sorted(d["routes"])
        d["routes_names"] = [route_map.get(r, r) for r in d["routes"]]
        d["work_days_count"] = len(d["work_days"])
        d["work_days"] = sorted(d["work_days"])
        d["issue_rate"] = (
            round(d["issues"] / d["trips"] * 100, 1) if d["trips"] else 0)

    # TOP reytinglar
    by_issue = sorted(
        [d for d in drivers if d["issues"] > 0],
        key=lambda d: -d["issues"])[:5]
    by_trips = sorted(drivers, key=lambda d: -d["trips"])[:5]
    by_vehicles = sorted(
        [d for d in drivers if d["vehicles_count"] > 1],
        key=lambda d: -d["vehicles_count"])[:5]
    by_multi_route = sorted(
        [d for d in drivers if d["routes_count"] > 1],
        key=lambda d: (-d["routes_count"], -d["trips"]))[:10]
    by_attendance = sorted(drivers, key=lambda d: -d["work_days_count"])[:5]
    least_active = sorted(drivers, key=lambda d: d["work_days_count"])[:5]

    return {
        "total_drivers": len(drivers),
        "total_trips": sum(d["trips"] for d in drivers),
        "total_issues": sum(d["issues"] for d in drivers),
        "most_errors": [{"name": d["name"], "driver_id": d["driver_id"],
                         "issues": d["issues"], "trips": d["trips"],
                         "issue_rate": d["issue_rate"]}
                        for d in by_issue],
        "most_active": [{"name": d["name"], "driver_id": d["driver_id"],
                         "trips": d["trips"], "work_days": d["work_days_count"]}
                        for d in by_trips],
        "bus_changers": [{"name": d["name"], "driver_id": d["driver_id"],
                          "vehicles_count": d["vehicles_count"],
                          "trips": d["trips"]}
                         for d in by_vehicles],
        "multi_route": [{"name": d["name"], "driver_id": d["driver_id"],
                         "routes_count": d["routes_count"],
                         "routes_names": d["routes_names"],
                         "trips": d["trips"]}
                        for d in by_multi_route],
        "best_attendance": [{"name": d["name"], "driver_id": d["driver_id"],
                             "work_days": d["work_days_count"],
                             "trips": d["trips"]}
                            for d in by_attendance],
        "least_active": [{"name": d["name"], "driver_id": d["driver_id"],
                          "work_days": d["work_days_count"],
                          "trips": d["trips"]}
                         for d in least_active],
    }


def _digest(a: dict) -> str:
    """LLM uchun ixcham tahlil xulosasi."""
    lines = [
        f"Davr: {a['start']} ... {a['end']} ({a['days']} kun)",
        f"Jami xatolar: {a['error_total']}",
        "Eng takroriy xatolar:",
    ]
    if not a["top_errors"]:
        lines.append("- (yo'q)")
    for t in a["top_errors"]:
        lines.append(f"- [{t['source']}] {t['norm']} x {t['n']}"
                     f" ({t['first']} ... {t['last']})")
    lines.append(f"Eng ko'p xato kuni: {a['weekday'] or '-'}")
    lines.append(f"Muvaffaqiyatsiz avto-ishlar: {a['failures']}"
                 f" ({a['failure_triggers'] or '-'})")
    lines.append("Takroriy muammolar:")
    if not a["recurring"]:
        lines.append("- (yo'q)")
    for r in a["recurring"]:
        lines.append(f"- {r['vehicle']} ({r['category']}) "
                     f"{r['days']}/{r['window']} kun")
    # Haydovchilar tahlili
    drv = a.get("drivers") or {}
    if drv:
        lines.append(f"\nHaydovchilar: jami {drv.get('total_drivers', 0)}, "
                     f"reyslar: {drv.get('total_trips', 0)}, "
                     f"xatoliklar: {drv.get('total_issues', 0)}")
        lines.append("Eng ko'p xato qilganlar:")
        for d in drv.get("most_errors", [])[:3]:
            lines.append(f"- {d['name']}: {d['issues']} xato / "
                         f"{d['trips']} reys ({d['issue_rate']}%)")
        lines.append("Eng ko'p ishlaganlar:")
        for d in drv.get("most_active", [])[:3]:
            lines.append(f"- {d['name']}: {d['trips']} reys, "
                         f"{d['work_days']} kun")
        lines.append("Ko'p avtobus almashtirganlar:")
        for d in drv.get("bus_changers", [])[:3]:
            lines.append(f"- {d['name']}: {d['vehicles_count']} ta avtobus, "
                         f"{d['trips']} reys")
        lines.append("Eng kam ishlaganlar:")
        for d in drv.get("least_active", [])[:3]:
            lines.append(f"- {d['name']}: {d['work_days']} kun, "
                         f"{d['trips']} reys")
    return "\n".join(lines)


# ------------------------------------------------------------ tavsiyalar

def _llm_recs(digest: str) -> str | None:
    """Tahlil asosida LLM'dan tavsiyalar (sozlanmagan/xato bo'lsa None)."""
    if not openrouter.configured():
        return None
    system = (
        "Siz transport parkini boshqarish tizimi uchun AI-muhandissiz. "
        "Quyidagi tahlil natijalariga asoslanib O'zbek tilida 5-8 ta aniq "
        "tavsiya bering. Raqamlarni o'zgartirmang va uydirma ma'lumot "
        "qo'shmang. Har bir tavsiya '• ' bilan boshlansin va bir jumla "
        "bo'lsin. Telegram HTML teglarini ishlatmang. "
        "Haydovchilar tahliliga alohida e'tibor bering: "
        "ko'p xato qilganlar, kam ishlaganlar, ko'p avtobus almashtirganlar "
        "haqida aniq tavsiyalar bering."
    )
    try:
        out = openrouter.complete(system, digest, max_tokens=900)
        return out.strip() if out and out.strip() else None
    except Exception:  # noqa: BLE001 - lokal rejimga o'tamiz
        return None


def _rule_recs(a: dict) -> list[str]:
    """LLM bo'lmasa ishlaydigan lokal qoidaviy tavsiyalar."""
    recs: list[str] = []
    if a["error_total"]:
        top = a["top_errors"][0] if a["top_errors"] else None
        if top:
            recs.append(
                f"[{top['source']}] manbasidagi '{top['norm']}' xatosi "
                f"{top['n']} marta takrorlangan — sababini logs/bm.log va "
                "errors jadvalidan tekshiring.")
    if a["weekday"]:
        recs.append(
            f"Xatolarning katta qismi {a['weekday']} kunlari kuzatilmoqda — "
            "shu kundagi sinxron/vazifalarni alohida kuzating.")
    if a["failures"]:
        triggers = ", ".join(a["failure_triggers"] or ["-"])
        recs.append(
            f"{a['failures']} ta muvaffaqiyatsiz avto-ish — trigger: "
            f"{triggers}. Ularning ketma-ketligini ko'rib chiqing.")
    for r in a["recurring"]:
        recs.append(
            f"{r['vehicle']} avtobusi ({r['category']}) {r['days']}/{r['window']} "
            "kun davomida muammoli — texnik holatini tekshiring.")
    if not recs:
        recs.append("Ko'rsatkichlar barqaror — maxsus harakat talab etilmaydi.")
    return recs


# ---------------------------------------------------------------- matn

def build_text(days: int | None = None, html: bool = True) -> str:
    """AI o'z-o'zini rivojlantirish hisoboti (Telegram/CLI uchun)."""
    st = get_storage()
    if not st.enabled:
        return "⚠️ Tahlil uchun DB yoqilmagan."
    a = analyze(days)
    esc = (lambda s: s) if not html else _esc_html

    parts = [
        "🧠 <b>AI O'Z-O'ZINI RIVOJLANTIRISH</b>",
        f"📅 {a['start']} … {a['end']} ({a['days']} kun)",
        "",
        "📛 <b>XATOLAR</b>",
    ]
    if not a["error_total"]:
        parts.append("Qayd etilgan xatolar yo'q ✓")
    else:
        avg = round(a["error_total"] / a["days"], 1)
        parts.append(f"Jami: <b>{a['error_total']}</b> ta "
                     f"(kuniga o'rtacha {avg})")
        if a["weekday"]:
            parts.append(f"Takrorlanish: asosan <b>{esc(a['weekday'])}</b> kunlari")
        if a["top_errors"]:
            parts.append("")
            parts.append("Eng takroriy:")
            for t in a["top_errors"]:
                parts.append(
                    f"• [{esc(t['source'])}] {esc(t['norm'])} — "
                    f"<b>{t['n']}</b> marta")
    parts += [
        "",
        "⚠️ <b>AVTO-ISH</b>",
    ]
    if a["failures"]:
        parts.append(f"Muvaffaqiyatsiz: <b>{a['failures']}</b> ta "
                     f"({esc(', '.join(a['failure_triggers']) or '-')})")
    else:
        parts.append("Barcha avto-ishlar muvaffaqiyatli ✓")

    parts += [
        "",
        "🔁 <b>TAKRORIY MUAMMOLAR</b>",
    ]
    if not a["recurring"]:
        parts.append("Takroriy muammolar aniqlanmadi ✓")
    else:
        for r in a["recurring"]:
            parts.append(f"• {esc(r['vehicle'])} ({esc(r['category'])}) — "
                         f"{r['days']}/{r['window']} kun")

    parts += ["", "💡 <b>TAVSIYALAR</b>"]
    llm = _llm_recs(_digest(a))
    if llm:
        parts.append(esc(llm))
    else:
        parts += ["• " + esc(r) for r in _rule_recs(a)]

    text = "\n".join(parts)
    if not html:
        text = re.sub(r"<[^>]+>", "", text)
    return text


def _esc_html(s: str) -> str:
    from ...utils.tgformat import esc as _e
    return _e(str(s))


# ---------------------------------------------------------------- yuborish

def check_and_send() -> None:
    """Kuniga bir marta tahlil yuboradi (soat chegarasi + holat fayl bilan)."""
    global _LAST_CHECK_AT
    if not enabled() or not get_storage().enabled:
        return
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    if time.localtime().tm_hour < _hour():
        return
    targets = _targets()
    if not targets:
        return
    with _LOCK:
        today = date.today().isoformat()
        if _load().get("date") == today:
            return
        text = build_text()
        if not text or text.startswith("⚠️"):
            return
        _save({"date": today})
        for cid in targets:
            try:
                send_message(text, chat_id=str(cid),
                             reply_markup=kb.nav_kb("nav:insights"))
            except Exception as exc:  # noqa: BLE001
                print(f"AI tahlil yuborilmadi [{cid}]: {exc}")


def send_now() -> str:
    """Qo'lda yuborish (`/insights`, CLI `insights`) — matn qaytaradi."""
    with _LOCK:
        text = build_text()
        if text.startswith("⚠️"):
            return text
        _save({"date": date.today().isoformat()})
        return text


def status_text() -> str:
    """Holat matni (masalan, `/alerts` ekranida ko'rsatiladi)."""
    s = _load()
    sent = s.get("date") == date.today().isoformat()
    mode = "✅ yonilgan" if enabled() else "⛔ o'chirilgan"
    state = "✓ bugun yuborilgan" if sent else "bugun hali yuborilmagan"
    return "\n".join([
        "🧠 <b>AI O'Z-O'ZINI RIVOJLANTIRISH</b>",
        "",
        f"Rejim: {mode} · soat <b>{_hour()}:00</b> · oyna: {_days()} kun",
        f"Holat: {state}",
        "",
        "Qo'lda yuborish: <b>/insights</b>",
    ])
