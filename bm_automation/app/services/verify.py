"""Sayt (BM API) ↔ Baza oylik tekshirish — AI tahlilli.

`verify_month()` har kompaniya (route) uchun saytdagi rasmiy hisob-kitob
(`gross/route`) summalarini baza (`route_daily`) summalari bilan solishtiradi.
Farqlar, tizim holati va xatolar bitta digest'ga yig'iladi va OpenRouter
orqali AI'ga uzatiladi — u vaziyatni "real so'zlar bilan" tahlil qiladi.
AI sozlanmagan yoki xato bo'lsa lokal qoidaviy xulosa ishlaydi.

Har profil o'z tokeni bilan ishlaydi (profileId bo'lsa `login_by_profile`),
xuddi `sync_all_profiles` va `check.run_check` kabi.

Ishlatish:
    data = verify_month(month="2026-08")      # yoki filters={"route": rid}
    ai = ai_report(digest(data))
    text, _ = render(data, ai)
"""

from __future__ import annotations

from datetime import date, timedelta

from ..api.client import BMClient
from ..core.profiles import all_profiles
from ..db.storage import get_storage
from ..repositories.gross_repo import GrossRepository
from ..utils.logger import get_logger

log = get_logger("bm_automation.services")

# `gross/route` bitta so'rovda 30 kundan ortiq oralig'ni qabul qilmaydi.
_CHUNK_DAYS = 30


# ------------------------------------------------------------ yordamchilar

def _month_bounds(month: str) -> tuple[str, str]:
    """Oyni (from, to) sanalariga yoyadi; joriy oyda bugun bilan cheklaydi."""
    y, m = month.split("-")
    first = date(int(y), int(m), 1)
    today = date.today()
    if first > today:
        return (today.isoformat(), today.isoformat())  # kelajak oy — bo'sh
    if (int(y), int(m)) == (today.year, today.month):
        last = today
    else:
        last = date(int(y), int(m) + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def _chunks(frm: str, to: str, max_days: int = _CHUNK_DAYS) -> list[tuple[str, str]]:
    """Sana oralig'ini sayt API limiti (30 kun) ichida bo'laklarga bo'ladi."""
    cur = date.fromisoformat(frm)
    end = date.fromisoformat(to)
    out = []
    while cur <= end:
        last = min(cur + timedelta(days=max_days - 1), end)
        out.append((cur.isoformat(), last.isoformat()))
        cur = last + timedelta(days=1)
    return out


def _routes(filters: dict | None) -> list[str]:
    """Route filtiri yoki barcha profillar route'lariga yoyadi."""
    route = str((filters or {}).get("route") or "").strip()
    if route:
        return [r for r in route.replace(",", " ").split() if r.strip()]
    return [str(p.get("routeVariantId") or "").strip()
            for p in all_profiles()
            if str(p.get("routeVariantId") or "").strip()]


def _db_monthly(storage, rid: str, frm: str, to: str) -> dict:
    """Baza tarafidagi oylik summalar (route_daily + schedules + trips)."""
    ph = storage.db.ph

    def one(sql: str, params: tuple, default=0):
        rows = storage.query(sql, params, limit=1)
        return rows[0][next(iter(rows[0]))] if rows else default

    row = storage.query(
        "SELECT COALESCE(SUM(trip_fact), 0) n, COALESCE(SUM(distance_fact), 0) k,"
        " COALESCE(SUM(working_day), 0) wd, COUNT(*) wb FROM route_daily"
        f" WHERE date >= {ph} AND date <= {ph} AND route_id = {ph}",
        (frm, to, rid), limit=1)
    r = row[0] if row else {}
    return {
        "db_trips": int(r.get("n") or 0),
        "db_km": round(float(r.get("k") or 0), 2),
        "db_work_days": int(r.get("wd") or 0),
        "db_waybills": int(r.get("wb") or 0),
        "db_plan": int(one(
            "SELECT COALESCE(SUM(trip_count), 0) FROM schedules"
            f" WHERE date >= {ph} AND date <= {ph} AND route_id = {ph}",
            (frm, to, rid)) or 0),
        "db_trip_rows": int(one(
            "SELECT COUNT(*) FROM trips"
            f" WHERE date >= {ph} AND date <= {ph} AND route_id = {ph}",
            (frm, to, rid)) or 0),
    }


def _site_monthly(client, rid: str, frm: str, to: str) -> dict:
    """Sayt tarafidagi oylik summalar (gross/route, 30 kunlik qismlarga)."""
    from ..db.sync import _not_found

    repo = GrossRepository(client)
    site = {"trips": 0, "waybills": 0, "km": 0.0, "work_days": 0,
            "days": 0, "error": ""}
    try:
        for f, t in _chunks(frm, to):
            data = repo.route(rid, f, t) or {}
            dates = data.get("dates") or {}
            vehicles = [v for info in dates.values()
                        for v in (info.get("vehicles") or [])]
            site["days"] += len(dates)
            site["waybills"] += len(vehicles)
            site["trips"] += int(data.get("tripFactSum") or sum(
                int(v.get("tripFact") or 0) for v in vehicles) or 0)
            site["km"] += float(data.get("distanceFactSum") or sum(
                float(v.get("distanceFact") or 0) for v in vehicles) or 0)
            site["work_days"] += int(data.get("workingDaySum") or sum(
                int(v.get("workingDay") or 0) for v in vehicles) or 0)
    except Exception as exc:  # noqa: BLE001 - 404 "ma'lumot yo'q" xato emas
        if not _not_found(exc):
            site["error"] = f"gross/route: {exc}"
    site["km"] = round(site["km"], 2)
    return site


def _row(rid: str, name: str, frm: str, to: str, site: dict | None,
         db: dict | None, error: str = "") -> dict:
    if error or site is None:
        return {"route_id": rid, "name": name, "from": frm, "to": to,
                "site": {"error": error or "ma'lumot yo'q"},
                "db": {}, "diff": {}, "status": "ERROR"}
    diff = {
        "trips": site["trips"] - db["db_trips"],
        "km": round(site["km"] - db["db_km"], 2),
        "work_days": site["work_days"] - db["db_work_days"],
        "waybills": site["waybills"] - db["db_waybills"],
    }
    if site.get("error"):
        return {"route_id": rid, "name": name, "from": frm, "to": to,
                "site": site, "db": db, "diff": {}, "status": "ERROR"}
    if any(diff.values()):
        status = "DIFF"
    else:
        status = "OK"
    return {"route_id": rid, "name": name, "from": frm, "to": to,
            "site": site, "db": db, "diff": diff, "status": status}


# ------------------------------------------------------------ asosiy oqim

def verify_month(month: str | None = None, filters: dict | None = None) -> dict:
    """Sayt ↔ baza oylik solishtirish. Xato bo'lsa `ok=False` + `error`."""
    month = month or date.today().strftime("%Y-%m")
    y, m = month.split("-")
    if date(int(y), int(m), 1) > date.today():
        return {"month": month, "ok": False,
                "error": "Ko'rsatilgan oy hali tugamagan."}
    frm, to = _month_bounds(month)
    base = {"month": month, "from": frm, "to": to}
    storage = get_storage()
    if not storage.enabled:
        return {**base, "ok": False, "error": "DB rejimi o'chirilgan"}
    routes = _routes(filters)
    if not routes:
        return {**base, "ok": False, "error": "Kompaniya (profil) topilmadi"}

    client = BMClient()
    client.login()
    main_access = client.access_token
    main_refresh = client.refresh_token

    profiles = {str(p.get("routeVariantId") or "").strip(): p
                for p in all_profiles()
                if str(p.get("routeVariantId") or "").strip()}

    def use_main() -> None:
        client.access_token = main_access
        client.refresh_token = main_refresh
        client.session.headers["Authorization"] = f"Bearer {main_access}"

    results = []
    for rid in routes:
        prof = profiles.get(rid, {})
        name = str(prof.get("name") or rid)
        pid = str(prof.get("profileId") or "").strip()
        try:
            if pid:
                client.login_for_profile(pid, fallback_to_main=True)
            else:
                use_main()
        except Exception as exc:  # noqa: BLE001 - token xatosi bir firmada
            results.append(_row(rid, name, frm, to, None, None,
                                error=f"token: {exc}"))
            continue
        site = _site_monthly(client, rid, frm, to)
        db = _db_monthly(storage, rid, frm, to)
        results.append(_row(rid, name, frm, to, site, db))
    return {**base, "ok": True, "results": results}


# ---------------------------------------------------------------- AI tahlil

_VERIFY_SYSTEM = (
    "Siz transport parki ma'lumotlarini tekshiruvchi AI-mutaxassissiz. "
    "Quyidagi sayt (BM API) va baza o'rtasidagi oylik solishtirish "
    "natijalarini O'zbek tilida 'real so'zlar bilan' tahlil qiling: "
    "vaziyat qanday, farqlar nimani bildiradi (sinxronlash kechikishi, "
    "sayt ma'lumotlari o'zgarishi yoki hisob-kitob xatosi), tizim holati "
    "va xatolarga izoh bering. Raqamlarni o'zgartirmang, uydirma ma'lumot "
    "qo'shmang, Telegram HTML teglarini ishlatmang. 4-8 jumla."
)


def digest(data: dict) -> str:
    """LLM uchun ixcham solishtirish + tizim holati xulosasi."""
    lines = [f"Oy: {data['month']} ({data['from']} ... {data['to']})",
             f"Kompaniyalar: {len(data.get('results') or [])}"]
    for r in data.get("results") or []:
        if r["status"] == "ERROR":
            lines.append(f"- {r['name']}: XATO ({r['site'].get('error') or '-'})")
            continue
        d = r["diff"]
        lines.append(
            f"- {r['name']}: sayt {r['site']['trips']} reys / "
            f"{r['site']['km']} km / {r['site']['work_days']} kun / "
            f"{r['site']['waybills']} qatnov; baza {r['db']['db_trips']} / "
            f"{r['db']['db_km']} / {r['db']['db_work_days']} / "
            f"{r['db']['db_waybills']}; farq {d['trips']:+} reys, "
            f"{d['km']:+.1f} km, {d['work_days']:+} kun, "
            f"{d['waybills']:+} qatnov; baza rejasi {r['db'].get('db_plan', '-')}")
    try:
        from ..dashboard.metrics import Metrics

        sysd = Metrics(storage=get_storage()).system()
        api = sysd.get("api") or {}
        db_ok = bool((sysd.get("database") or {}).get("ok"))
        lines.append("Tizim: BM API " + ("OK" if api.get("ok") else "XATO") +
                     " · DB " + ("yoqilgan" if db_ok else "o'chiq") +
                     f" · so'nggi sync {sysd.get('last_sync') or '-'}")
    except Exception:  # noqa: BLE001 - tizim holati bezak, xato bo'lsa o'tkazamiz
        pass
    try:
        from ..notifications.ops.self_review import analyze as _sr

        a = _sr(days=14)
        lines.append(f"Xatolar (14 kun): {a['error_total']} ta · "
                     f"muvaffaqiyatsiz avto-ish: {a['failures']} ta")
        for t in a["top_errors"][:3]:
            lines.append(f"- [{t['source']}] {t['norm']} x {t['n']}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def ai_report(digest_text: str) -> str | None:
    """LLM'dan vaziyat tahlili; sozlanmagan/xato bo'lsa None (lokal xulosa)."""
    from ..notifications.ops import openrouter

    if not openrouter.configured():
        return None
    try:
        out = openrouter.complete(_VERIFY_SYSTEM, digest_text)
        return out.strip() if out and out.strip() else None
    except Exception as exc:  # noqa: BLE001 - lokal rejimga o'tamiz
        log.warning("Verify AI tahlil xato: %s", exc)
        return None


# ------------------------------------------------------------ render

def _rule_report(data: dict) -> str:
    """LLM bo'lmasa ishlaydigan lokal qoidaviy xulosa."""
    results = data.get("results") or []
    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")
    err = sum(1 for r in results if r["status"] == "ERROR")
    diff = total - ok - err
    lines = [f"Jami {total} kompaniya: {ok} mos, {diff} farqli, {err} xato."]
    for r in results:
        if r["status"] == "ERROR":
            lines.append(f"• {r['name']}: xato — {r['site'].get('error') or '-'}")
            continue
        if r["status"] == "OK":
            continue
        d = r["diff"]
        bits = []
        for label, v in (("reys", d["trips"]), ("km", d["km"]),
                         ("kun", d["work_days"]), ("qatnov", d["waybills"])):
            if v:
                bits.append(f"{label} {v:+.1f}" if label == "km"
                            else f"{label} {v:+}")
        lines.append(f"• {r['name']}: {' · '.join(bits)}")
    if not diff and not err:
        lines.append("Baza sayt bilan to'liq mos — hisob-kitoblar to'g'ri ✅")
    else:
        lines.append("Tafovut bo'lsa /sync (sinxronlash) yoki /verify "
                     "(qayta tekshirish) ni ishga tushiring.")
    return "\n".join(lines)


def render(data: dict, ai_text: str | None = None) -> tuple[str, dict | None]:
    """Telegram HTML matn. (matn, markup) qaytaradi — `reply()` uchun."""
    from ..utils.tgformat import esc, fmt, table

    if data.get("error"):
        return ("🔎 <b>OYLIK TEKSHIRISH: SAYT ↔ BAZA</b>\n\n"
                f"⛔ {esc(data['error'])}"), None
    parts = [
        "🔎 <b>OYLIK TEKSHIRISH: SAYT ↔ BAZA</b>",
        f"📅 {data['month']} · {data['from']} … {data['to']}",
        "",
    ]
    rows = []
    for r in data.get("results") or []:
        icon = {"OK": "✅", "DIFF": "⚠️", "ERROR": "❌"}.get(r["status"], "❓")
        name = f"{icon} {r['name']}"
        if r["status"] == "ERROR":
            rows.append([name, "xato", "-", "-", "-"])
            continue
        rows.append([
            name,
            fmt(r["site"]["trips"], 0),
            fmt(r["db"]["db_trips"], 0),
            f"{r['diff']['km']:+.1f}",
            f"{r['diff']['trips']:+d}",
        ])
    parts.append(table(["Kompaniya", "Sayt", "Baza", "Km farq", "Reys farq"], rows))
    parts += ["", "🤖 <b>AI TAHLIL</b>"]
    if ai_text:
        parts.append(esc(ai_text))
    else:
        parts.append(esc(_rule_report(data)))
    parts += ["", "Tekshirish: /verify · Sinxronlash: /sync"]
    return "\n".join(parts), None
