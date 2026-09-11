"""Brutto-route (byBus) umumiy statistikasi: Telegram (HTML) matnini tayyorlash.

Endpoint: GET /brutto-management/api/v1/gross/route
  -> {routeVariantId, routeName, orgName, from, to,
      dates: {YYYY-MM-DD: {vehicles: [...], *_Sum, percentage_Avg...}},
      *_Sum, *PercentageAvg}  # butun davr uchun

Yuboriladigan matn Telegram HTML parse_mode'da ("Jo'nash taxtasi" uslubi):
  📊 UMUMIY STATISTIKA - B-80
  📅 11.08.2026 · Wednesday

  <b>🚌 Masofa (km)</b>
  Reja: 232.4 | Amalda: 232.4 | ✅ <b>100%</b>
  ...
"""

from __future__ import annotations

from datetime import date

from ..api.client import BMClient
from ..repositories.gross_repo import GrossRepository
from ..utils.tgformat import badge, badge_plain, fmt, table

__all__ = ["build_route_text", "run", "run_all"]

_fmt = fmt
_table = table


def build_route_text(data: dict, d: date, title_word: str = "") -> str:
    """Bitta route (byBus) uchun Telegram HTML matnini qaytaradi."""
    route_name = data.get("routeName") or ""
    org = data.get("orgName") or ""
    day_word = title_word or d.strftime("%A")

    lines = [f"📊 <b>UMUMIY STATISTIKA</b> — {route_name}"]
    if org:
        lines.append(f"🏢 {org}")
    lines.append(f"📅 {d:%d.%m.%Y} · {day_word}")

    # Umumiy (jamlama)
    tp = data.get("tripPlanSum")
    tf = data.get("tripFactSum")
    tpr = data.get("tripPercentageAvg")
    dp = data.get("distancePlanSum")
    df = data.get("distanceFactSum")
    dpr = data.get("distancePercentageAvg")
    wd = data.get("workingDaySum")

    lines.append("")
    lines.append("<b>🚌 Masofa (km)</b>")
    lines.append(
        f"Reja: {_fmt(dp)}  |  Amalda: {_fmt(df)}  |  {badge(dpr)}"
    )
    lines.append("")
    lines.append("<b>🔁 Qatnovlar</b>")
    lines.append(
        f"Reja: {_fmt(tp, 0)}  |  Amalda: {_fmt(tf, 0)}  |  {badge(tpr)}"
    )
    if wd is not None:
        lines.append(f"Ишлаган автобус-кун: <b>{_fmt(wd, 0)}</b>")

    # Avtobuslar bo'yicha jadval
    dates = data.get("dates") or {}
    vehicles = []
    for ds, info in dates.items():
        vehicles.extend(info.get("vehicles") or [])
    if vehicles:
        vehicles.sort(key=lambda v: str(v.get("shiftName") or ""))
        lines.append("")
        lines.append("<b>🚍 Avtobuslar bo'yicha — Masofa (km)</b>")
        rows = []
        tot_plan = tot_fact = 0.0
        for v in vehicles:
            p = str(v.get("shiftName") or "?")
            bus = str(v.get("vehicleNumber") or "-")
            plan = _fmt(v.get("distancePlan"))
            fact = _fmt(v.get("distanceFact"))
            pct = badge_plain(v.get("distancePercentage"))
            rows.append([p, bus, plan, fact, pct])
            tot_plan += float(v.get("distancePlan") or 0)
            tot_fact += float(v.get("distanceFact") or 0)
        tot_pct = (tot_fact / tot_plan * 100) if tot_plan else None
        rows.append(["JAMI", "", _fmt(tot_plan), _fmt(tot_fact), badge_plain(tot_pct)])
        lines.append(_table(["P", "Avtobus", "Reja", "Amalda", "Foiz"], rows))

        lines.append("")
        lines.append("<b>🔁 Avtobuslar bo'yicha — Qatnovlar</b>")
        rows = []
        tot_plan = tot_fact = 0.0
        for v in vehicles:
            p = str(v.get("shiftName") or "?")
            bus = str(v.get("vehicleNumber") or "-")
            plan = _fmt(v.get("tripPlan"), 0)
            fact = _fmt(v.get("tripFact"), 0)
            pct = badge_plain(v.get("tripPercentage"))
            rows.append([p, bus, plan, fact, pct])
            tot_plan += float(v.get("tripPlan") or 0)
            tot_fact += float(v.get("tripFact") or 0)
        tot_pct = (tot_fact / tot_plan * 100) if tot_plan else None
        rows.append(["JAMI", "", _fmt(tot_plan, 0), _fmt(tot_fact, 0), badge_plain(tot_pct)])
        lines.append(_table(["P", "Avtobus", "Reja", "Amalda", "Foiz"], rows))

    return "\n".join(lines)


def run(client: BMClient, route_id: str, from_date: str, to_date: str,
        title_word: str = "") -> dict:
    """Bir route uchun statistikani yig'adi."""
    data = GrossRepository(client).route(route_id, from_date, to_date)
    d = date.fromisoformat(to_date)
    text = build_route_text(data, d, title_word=title_word)
    return {
        "routeName": data.get("routeName") or route_id,
        "from": from_date,
        "to": to_date,
        "text": text,
        "data": data,
    }


def run_all(client: BMClient, from_date: str, to_date: str, only: str = "") -> list[dict]:
    """Barcha profillar (yo'nalishlar) uchun statistika.

    Har bir kompaniya uchun `client_for_profile` orqali alohida authenticated
    client olinadi (o'z kredensiallari yoki admin tokeni + login_by_profile).
    """
    from ..core.companies import client_for_profile
    from ..core.profiles import all_profiles

    results = []
    for p in all_profiles():
        name = p.get("name")
        if only and only.lower() not in str(name).lower():
            continue
        rid = str(p.get("routeVariantId", "") or "").strip()
        if not rid:
            results.append({"profile": name, "error": "routeVariantId yo'q"})
            continue
        try:
            c = client_for_profile(p)
            res = run(c, rid, from_date, to_date)
            res["profile"] = name
            results.append(res)
            print(f"  OK [{name}]: {res['routeName']}")
        except Exception as exc:
            results.append({"profile": name, "error": str(exc)})
            print(f"  XATO [{name}]: {exc}")
    return results
