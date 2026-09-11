"""Lokal AI-yordamchi — qoidaviy NLU + tahlil (internetsiz).

Tashqi API kerak emas. So'rov matnidan intent va filtirlarni aniqlab,
`metrics.Metrics` ma'lumotlari asosida tabiiy tildagi javob yozadi.
Ishlatish: `/ai <savol>` yoki oddiy matn (tanib bo'lmasa yordam matni).

Funksiyalar:
  - `assist(text, filters)`  — so'rovni tushunadi, (matn, markup) qaytaradi
  - `analyze(filters)`       — anomaliya aniqlash + tavsiyalar
  - `daily_summary(filters)` — kunlik xulosa matni
  - `help_text()`            — AI imkoniyatlari ro'yxati
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ...dashboard.metrics import Metrics, parse_filters
from ...utils.logger import get_logger
from ...utils.names import short_name
from ...utils.tgformat import esc, fmt
from . import context, kb, openrouter, planning, render
from .roles import Role, resolve_role

log = get_logger("bm_automation.bot")

_WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba",
             "juma", "shanba", "yakshanba"]
_MONTHS = {"yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5,
           "iyun": 6, "iyul": 7, "avgust": 8, "sentabr": 9, "oktabr": 10,
           "noyabr": 11, "dekabr": 12}

_LLM_SYSTEM = (
    "Siz avtobus transport parki uchun AI-yordamchisiz. O'zbek tilida "
    "javob bering. Faqat berilgan ma'lumotlardagi faktlardan foydalaning, "
    "raqamlarni o'zgartirmang va uydirma ma'lumot qo'shmang. Javob qisqa "
    "va aniq bo'lsin (5-8 jumla), emoji o'rinli bo'lsa ishlating. "
    "Telegram HTML teglarini ishlatmang."
)


def _llm_answer(question: str, context_text: str) -> str | None:
    """LLM orqali javob; kalit yo'q yoki xato bo'lsa None (lokal fallback)."""
    if not openrouter.configured():
        return None
    try:
        user = (f"Savol: {question}\n\n"
                f"Ma'lumot:\n{context_text}\n\nJavob yozing:")
        return openrouter.complete(_LLM_SYSTEM, user)
    except Exception as exc:  # noqa: BLE001 - API xatosi lokal javobga tushadi
        log.warning("OpenRouter javob olmadi, lokal rejimga o'tildi: %s", exc)
        return None


def _maybe_llm(text: str, local_text: str, q: str) -> str:
    """LLM sozlangan bo'lsa javobni jonlantiradi, aks holda lokal matn.

    LLM matni HTML-parse rejimida yuboriladi — shuning uchun tasodifiy
    HTML teglarini escape qilib xavfsiz qilamiz (aks holda yopilmagan tag
    Telegram'da xato beradi).
    """
    if not openrouter.configured():
        return local_text
    llm = _llm_answer(q, local_text)
    if not llm:
        return local_text
    return esc(llm)


def ai_status() -> str:
    """AI ish rejimi haqida bir qatorli holat."""
    if not openrouter.configured():
        return "🤖 AI yordamchi: <b>lokal</b> rejim (OpenRouter kaliti yo'q)"
    return f"🤖 AI yordamchi: <b>OpenRouter</b> · {esc(openrouter.model())}"


# ----------------------------- ko'p bosqichli suhbat xotirasi ---------------

_HISTORY: dict[int, list[dict]] = {}
_MAX_HISTORY = 6  # 3 javob aylanishi (har birida user + assistant)


def _history_messages(chat_id: int, question: str,
                      context_text: str) -> list[dict]:
    """Suhbat tarixi + joriy savol bilan to'liq messages ro'yxati."""
    msgs = [{"role": "system", "content": _LLM_SYSTEM}]
    for m in _HISTORY.get(chat_id, [])[-_MAX_HISTORY:]:
        msgs.append(m)
    if context_text:
        question = f"{question}\n\nMa'lumot:\n{context_text}"
    msgs.append({"role": "user", "content": question})
    return msgs


def _remember(chat_id: int, role: str, content: str) -> None:
    h = _HISTORY.setdefault(chat_id, [])
    h.append({"role": role, "content": content})
    del h[:-_MAX_HISTORY]


def clear_history(chat_id: int) -> None:
    """Berilgan chat uchun suhbat tarixini tozalaydi."""
    _HISTORY.pop(chat_id, None)


def _met() -> Metrics:
    return Metrics()


def _extract_date(low: str) -> dict:
    """Matndan sana: ISO / bugun / kecha / oy nomi."""
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", low)
    if m:
        return {"date": m.group(1)}
    if "kecha" in low:
        return {"date": (date.today() - timedelta(days=1)).isoformat()}
    if "bugun" in low or "hozir" in low:
        return {"date": date.today().isoformat()}
    for name, num in _MONTHS.items():
        if name in low:
            return {"month": f"{date.today().year}-{num:02d}"}
    return {}


def _extract_plate(text: str) -> str:
    """Avtobus davlat raqami: '40128RCA', '60865GCA', '01A001' kabi."""
    m = re.search(r"\b([0-9]{2,}[A-Za-zА-Яа-я]{1,3}[0-9]{0,3})\b", text)
    return (m.group(1) if m else "").upper()


def _extract_company(text: str) -> str:
    """Matnda firma nomi uchrasa — shu firma."""
    from ...core.profiles import all_profiles

    low = text.lower()
    for p in all_profiles():
        name = str(p.get("name") or "").strip()
        if name and name.lower() in low:
            return name
    return ""


def _extract_driver(text: str) -> str:
    """Matndagi haydovchi ismini aniqlaydi (eng uzun moslik)."""
    low = text.lower()
    rows = _met().storage.query("SELECT external_id, full_name FROM drivers")
    best, best_len = "", 0
    for r in rows:
        name = str(r.get("full_name") or "").strip()
        if len(name) > 4 and name.lower() in low and len(name) > best_len:
            best, best_len = str(r["external_id"]), len(name)
    return best


def _detect(low: str, text: str, f: dict) -> str | None:
    """Intent turini aniqlaydi. Aniqlanmasa None."""
    has = lambda *w: any(x in low for x in w)

    if has("oy xulosasi", "oylik", "oy yakuni", "bu oy"):
        return "month"
    if has("xulosa", "tahlil", "analiz", "diagnostika", "umumiy holat"):
        return "analysis"
    if has("tavsiya", "yaxshilash", "baho", "bahola"):
        return "analysis"
    if has("rejaga", "rejalashtir", "ertangi reja", "yozib qo'y",
           "grafikka yoz", "grafikga yoz", "grafikka qo'sh",
           "grafikga qo'sh", "ertangi chiqish"):
        return "plan"
    if has("bashorat", "prognoz", "ertaga", "kelajak", "trend"):
        return "forecast"

    if has("tekshir", "verify", "audit", "solishtir", "solishtirma",
           "farqini", "mosligini", "baza bilan"):
        return "verify"
    if has("sinxronlash", "sync qil", "yangilab yubor", "ma'lumotni yangila"):
        return "run_sync"
    if has("tizim holati", "server holati", "server qanday", "tizim qanday"):
        return "status"

    if _extract_plate(text) and has("avtobus", "mashina", "karta"):
        return "vehicle"
    if _extract_driver(text) and has("haydovchi", "shofyor", "karta", "qancha"):
        return "driver"
    if _extract_company(text) or f.get("profile"):
        pass  # firma — umumiy so'rovlarga filtir sifatida

    if has("davomat", "kelmadi", "kelganlar", "qatnashdi"):
        return "attendance"
    if has("masofa", "kilometr", "km ") or low.strip() == "km":
        return "distance"
    if has("jadval", "grafik", "smena"):
        return "schedule"
    if has("reyting", "eng yaxshi", "eng zo'r", "top", "raqobat"):
        return "top"
    if has("muammo", "nosoz", "buzilgan", "gps", "xato"):
        return "problems"
    if has("haydovchilar", "shofyorlar"):
        return "drivers"
    if has("yo'nalish", "marshrut", "trassa"):
        return "routes"
    if has("avtobuslar", "avtobus"):
        return "vehicles"
    if has("reys", "qatnov", "poezdka", "smennaya"):
        return "trips"
    if has("oy", "oylik"):
        return "month"
    if has("kecha"):
        return "yesterday"
    if has("bugun", "holat", "qanday", "ishlar", "umumiy"):
        return "today"

    return None


def _perf_line(t: dict) -> str:
    perf = (t["completed"] / t["planned"] * 100) if t["planned"] else None
    if perf is None:
        return "reja aniqlanmadi"
    if perf >= 95:
        return f"a'lo ({perf:.0f}%)"
    if perf >= 80:
        return f"qoniqarli ({perf:.0f}%)"
    return f"past ({perf:.0f}%)"


def _problems_line(p: dict) -> str:
    c = p["counts"]
    total = sum(c.values())
    if not total:
        return "muammolar yo'q ✅"
    bits = []
    for key, label in (("gps", "GPS"), ("technical", "texnik"),
                       ("schedule", "jadval"), ("unknown", "noma'lum")):
        if c.get(key):
            bits.append(f"{label} {c[key]}")
    return f"jami {total} ta muammo: {', '.join(bits)}"


def _day_filters(f: dict, ds: str) -> dict:
    """Kunlik so'rovlar uchun from/to ni ham sana bo'yicha belgilaydi."""
    return {**f, "date": ds, "from": ds, "to": ds}


def _today_text(f: dict, ds: str) -> str:
    m = _met()
    fd = _day_filters(f, ds)
    t = m.today(fd)
    p = m.problems(fd)
    c = p["counts"]
    title = "📊 <b>KUN HOLATI</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    lines = [
        title,
        f"📅 {render._date_label(t['date'])}",
        "",
        f"Rejalashtirilgan <b>{fmt(t['planned'], 0)}</b> reysdan "
        f"<b>{fmt(t['completed'], 0)}</b> bajarildi — {_perf_line(t)}.",
        f"🚌 Avtobuslar: jami <b>{t['total_buses']}</b>, faol "
        f"<b>{t['active_buses']}</b>.",
        f"✅ Qabul: <b>{fmt(t['accepted'], 0)}</b> · ⚠️ Qabul qilinmagan: "
        f"<b>{fmt(t['not_accepted'], 0)}</b> · ⛔ Bekor: "
        f"<b>{fmt(t['rejected'], 0)}</b> · 0️⃣ Zero: "
        f"<b>{fmt(t['zero_mileage'], 0)}</b>.",
        f"🔧 {_problems_line(p)}",
    ]
    return "\n".join(lines)


def _month_text(f: dict) -> str:
    m = _met()
    f = {**f, "month": f.get("month") or date.today().strftime("%Y-%m")}
    d = m.monthly(f)
    t = d["totals"]
    pr = t["problems"]
    title = "📅 <b>OY XULOSASI</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    return "\n".join([
        title,
        f"📅 {render._month_label(d['month'])}",
        "",
        f"Davr bo'yi <b>{fmt(t['planned'], 0)}</b> rejalashtirilgan reysdan "
        f"<b>{fmt(t['actual'], 0)}</b> amalga oshdi — "
        f"{_perf_line({'planned': t['planned'], 'completed': t['actual']})}.",
        f"📌 Jami reyslar: <b>{fmt(t['total'], 0)}</b> · Qabul: "
        f"<b>{fmt(t['accepted'], 0)}</b> · Qabul qilinmagan: "
        f"<b>{fmt(t['not_accepted'], 0)}</b> · Bekor: "
        f"<b>{fmt(t['rejected'], 0)}</b>.",
        f"🔧 Muammolar: GPS {pr['gps']} · Texnik {pr['technical']} · "
        f"Jadval {pr['schedule']} (jami {t['problems_total']}).",
    ])


def daily_summary(filters: dict | None = None) -> str:
    """Kunlik xulosa — bugungi eng muhim ko'rsatkichlar."""
    m = _met()
    f = parse_filters(filters or {})
    today_iso = date.today().isoformat()
    fd = _day_filters(f, today_iso)
    t = m.today(fd)
    p = m.problems(fd)
    att = m.attendance(fd)

    drivers = m.drivers({**f, "month": date.today().strftime("%Y-%m")})
    active = [x for x in drivers if x["km"] > 0 and not x["blacklisted"]]
    top = max(active, key=lambda x: x["km"]) if active else None

    perf = (t["completed"] / t["planned"] * 100) if t["planned"] else None
    perf_txt = f"{perf:.0f}%" if perf is not None else "n/a"

    lines = [
        "🤖 <b>KUNLIK XULOSA</b>",
        f"📅 {render._date_label(today_iso)}",
        "",
        f"Bugungi holat: <b>{fmt(t['completed'], 0)}</b> / "
        f"<b>{fmt(t['planned'], 0)}</b> reys bajarildi ({perf_txt}). "
        f"Faol avtobuslar <b>{t['active_buses']}</b> / {t['total_buses']}.",
        f"Davomat: <b>{att['rate']:.0f}%</b> ({att['present_count']} / {att['total']}) — "
        f"{att['absent_count']} haydovchi kelmadi.",
    ]
    if top:
        lines.append(
            f"🏆 Eng faol haydovchi: <b>{esc(short_name(str(top['name'])))}</b> — "
            f"{fmt(top['km'], 1)} km, {top['trips']} reys.")
    lines.append(f"🔧 {_problems_line(p)}")
    return "\n".join(lines)


def analyze(filters: dict | None = None) -> str:
    """Anomaliya aniqlash va tavsiyalar (qoidaviy tahlil)."""
    m = _met()
    f = parse_filters(filters or {})
    today_iso = date.today().isoformat()
    month = date.today().strftime("%Y-%m")
    fday = _day_filters(f, today_iso)
    fmon = {**f, "month": month}

    t = m.today(fday)
    p = m.problems(fday)
    att = m.attendance(fday)
    monthly = m.monthly({**f, "month": month})
    drivers = m.drivers(fmon)
    routes = m.routes(fday)
    dist = m.distance({**f, "month": month})

    title = "🤖 <b>AI TAHLIL</b>"
    if f.get("profile"):
        title += f" — 🏢 {context.short_name(f['profile'])}"
    lines = [title, f"📅 {render._date_label(today_iso)}", ""]
    find = []
    advice = []

    # 1) Bajarilish
    perf = (t["completed"] / t["planned"] * 100) if t["planned"] else None
    if perf is None:
        find.append("⚠️ Bugungi reja aniqlanmadi (schedules bo'sh bo'lishi mumkin).")
        advice.append("Sinxronlash /sync orqali rejani yangilang.")
    elif perf < 80:
        find.append(f"❌ Bajarilish past: {perf:.0f}% "
                    f"({t['completed']}/{t['planned']}).")
        advice.append("Yuklamani qayta taqsimlash yoki qo'shimcha avtobus "
                      "kiritishni ko'rib chiqing.")
    elif perf < 95:
        find.append(f"⚠️ Bajarilish o'rtacha: {perf:.0f}%.")

    # 2) Davomat
    if att["total"]:
        rate = att["rate"]
        if rate < 70:
            find.append(f"❌ Davomat past: {rate:.0f}% "
                        f"({att['absent_count']} haydovchi kelmadi).")
            advice.append("Kelmagan haydovchilar bilan bog'lanib, smena "
                          "zaxirasini tekshiring.")
        elif rate < 85:
            find.append(f"⚠️ Davomat o'rtacha: {rate:.0f}%.")

    # 3) Yo'nalishlar
    if routes:
        low_routes = [r for r in routes
                      if r["planned"] and r["performance"] < 80]
        if low_routes:
            worst = min(low_routes, key=lambda r: r["performance"])
            find.append(f"❌ Eng yomon yo'nalish: {esc(worst['name'])} — "
                        f"{worst['performance']:.0f}% "
                        f"({worst['actual']}/{worst['planned']}).")
            advice.append(f"'{esc(worst['name'])}' yo'nalishida haydovchi/"
                          "avtobus taqchilligini tekshiring.")

    # 4) Avtobuslar: masofa rejaga nisbatan
    low_km = [v for v in dist["rows"]
              if v["distance_plan"] and v["distance_fact"] / v["distance_plan"] < 0.8]
    if low_km:
        worst_v = min(low_km, key=lambda v: v["distance_fact"] / v["distance_plan"])
        ratio = worst_v["distance_fact"] / worst_v["distance_plan"] * 100
        find.append(f"❌ Kam yurgan avtobus: {esc(worst_v['plate_number'])} — "
                    f"reja {fmt(worst_v['distance_plan'], 0)} km, fakt "
                    f"{fmt(worst_v['distance_fact'], 0)} km ({ratio:.0f}%).")
        advice.append(f"{esc(worst_v['plate_number'])} avtobusini texnik "
                      "tekshiruvdan o'tkazing.")

    # 5) Ko'p muammoli avtobus
    veh = m.vehicles({**f, "from": monthly["from"], "to": monthly["to"]})
    if veh:
        issue_veh = sorted(veh, key=lambda v: v["issues"], reverse=True)
        if issue_veh and issue_veh[0]["issues"] > 0:
            top_v = issue_veh[0]
            find.append(f"⚠️ Eng ko'p muammo: {esc(top_v['plate_number'])} — "
                        f"{top_v['issues']} ta.")
            advice.append(f"{esc(top_v['plate_number'])} reyslarini kuzatishni "
                          "kuchaytiring.")

    # 6) Haydovchilar
    active_d = [x for x in drivers if x["km"] > 0 and not x["blacklisted"]]
    if active_d:
        best = max(active_d, key=lambda x: x["km"])
        find.append(f"🏆 Eng faol haydovchi: {esc(short_name(str(best['name'])))} — "
                    f"{fmt(best['km'], 0)} km, {best['trips']} reys.")

    # 7) Trend (so'nggi kunlar)
    trend = _trend(monthly["days"])
    if trend == "up":
        find.append("📈 So'nggi kunlarda bajarilish o'smoqda.")
    elif trend == "down":
        find.append("📉 So'nggi kunlarda bajarilish pasaymoqda.")
        advice.append("Tendensiya sababini tekshiring: taqchillik yoki "
                      "texnik muammolar.")

    lines.append("<b>🔍 ANIQLANGANLAR</b>")
    lines.append("\n".join(f"• {x}" for x in find) if find else "• Aniqlanmadi ✅")
    lines += ["", "<b>💡 TAVSIYALAR</b>"]
    lines.append("\n".join(f"• {x}" for x in advice) if advice
                 else "• Hozircha alohida chora shart emas.")
    lines += ["", "⚡ Tezkor buyruqlar: /attendance · /distance · /problems"]
    return "\n".join(lines)


def _trend(days: list[dict]) -> str | None:
    """So'nggi 3 kun o'rtachasini undan oldingi 3 kun bilan solishtiradi."""
    vals = [int(d.get("actual") or 0) for d in days if d.get("actual") is not None]
    if len(vals) < 4:
        return None
    recent = sum(vals[-3:]) / 3
    prior = sum(vals[-6:-3]) / 3 if len(vals) >= 6 else \
        sum(vals[:-3]) / (len(vals) - 3)
    if prior <= 0:
        return None
    r = recent / prior
    if r >= 1.1:
        return "up"
    if r <= 0.9:
        return "down"
    return "flat"


def forecast(filters: dict | None = None) -> str:
    """Oddiy trendga asoslangan bashorat."""
    m = _met()
    f = parse_filters(filters or {})
    month = date.today().strftime("%Y-%m")
    monthly = m.monthly({**f, "month": month})
    days = monthly["days"]
    vals = [int(d.get("actual") or 0) for d in days if d.get("actual") is not None]
    trend = _trend(days)
    if len(vals) < 3:
        return ("🤖 <b>BASHORAT</b>\n\n"
                "Ma'lumot yetarli emas — kamida 3 kunlik statistika kerak.")
    avg = sum(vals[-3:]) / 3
    words = {"up": "ortishi", "down": "kamayishi", "flat": "barqarorligi"}.get(
        trend, "barqarorligi")
    total = sum(monthly["totals"]["problems"].values())
    return "\n".join([
        "🤖 <b>BASHORAT</b>",
        f"📅 {render._month_label(monthly['month'])}",
        "",
        f"So'nggi 3 kun o'rtacha <b>{avg:.0f}</b> reys/ kun. "
        f"Trend: <b>{words}</b>.",
        f"Agar davom etsa, oy oxirigacha ≈ <b>{fmt(avg * _remaining(), 0)}</b> "
        f"reys qo'shilishi mumkin.",
        f"Joriy muammolar: <b>{total}</b> ta.",
    ])


def _remaining() -> int:
    today = date.today()
    return ((date(today.year, today.month + 1, 1) - timedelta(days=1))
            - today).days


def help_text() -> str:
    return "\n".join([
        "🤖 <b>AI YORDAMCHI</b>",
        "",
        "Tabiiy tilda so'rang, men ma'lumotlardan javob beraman:",
        "",
        "• <b>\"Bugungi holat qanday?\"</b> — kunlik ko'rsatkichlar",
        "• <b>\"Kecha qanday edi?\"</b> — kechagi ko'rsatkichlar",
        "• <b>\"Oy xulosasi\"</b> — oylik statistika",
        "• <b>\"Davomat qanday?\"</b> — bugungi davomat",
        "• <b>\"Masofa necha km?\"</b> — avtobuslar masofasi",
        "• <b>\"Qaysi avtobus muammoli?\"</b> — muammolar tahlili",
        "• <b>\"Eng yaxshi haydovchi kim?\"</b> — reyting",
        "• <b>\"Xulosa ber\"</b> / <b>\"Tahlil\"</b> — AI tahlil + tavsiyalar",
        "• <b>\"Ertaga qanday bo'ladi?\"</b> — trend bashorati",
        "• <b>\"Haydovchi Aliyev\"</b> — haydovchi kartasi",
        "• <b>\"Avtobus 40128RCA\"</b> — avtobus kartasi",
        "• <b>\"Yuldashovni ertaga rejaga yoz\"</b> — ertangi rejaga qo'shish",
        "• <b>\"/plan\"</b> — ertangi reja bilan ishlash",
        "",
        "Suhbatni yangilash: <b>\"/ai toza\"</b> — eski savol-javoblarni o'chirish.",
        "",
        "Firma nomini aytsangiz (masalan: \"FERGANATEX xulosasi\") — "
        "faqat o'sha firma ko'rsatiladi.",
        "",
        ai_status(),
    ])


def assist(text: str, filters: dict | None = None,
           chat_id: int | None = None) -> tuple[str, dict] | None:
    """So'rovni tushunib, (matn, reply_markup) qaytaradi; tanib bo'lmasa None."""
    low = (text or "").lower().strip()
    if not low:
        return None
    f = parse_filters(filters or {})

    # Firma nomi — filtir sifatida birlashtiramiz
    comp = _extract_company(text)
    if comp and not f.get("profile"):
        f["profile"] = comp
    # Sana/oy
    dt = _extract_date(low)
    f = {**f, **dt}

    intent = _detect(low, text, f)
    if intent is None:
        return None

    markup = render._combine(render._profile_bar(f), kb.nav_kb("nav:settings"))

    if intent == "plan":
        from .roles import can
        role = resolve_role(chat_id) if chat_id is not None else None
        if role is None or not can(role, "plan"):
            return ("⛔ Rejalashtirish uchun ruxsat yo'q."), markup
        msg = planning.add_from_text(text, by=int(chat_id or 0))
        return msg, render._combine(render._profile_bar(f), kb.plan_kb())

    if intent in ("today", "yesterday"):
        ds = f.get("date") or date.today().isoformat()
        local = _today_text(f, ds)
        return _maybe_llm(text, local, text), markup
    if intent == "month":
        local = _month_text(f)
        return _maybe_llm(text, local, text), markup
    if intent == "analysis":
        local = analyze(f)
        return _maybe_llm(text, local, text), markup
    if intent == "forecast":
        local = forecast(f)
        return _maybe_llm(text, local, text), markup
    if intent == "help":
        return help_text(), markup

    if intent == "verify":
        month = f.get("month") or date.today().strftime("%Y-%m")
        return (
            "🔎 <b>OYLIK TEKSHIRISH: SAYT ↔ BAZA</b>\n\n"
            f"📅 {month}\n\nBM saytidagi rasmiy hisob-kitobni baza bilan "
            "solishtiraman. Boshlaymi?",
            {"inline_keyboard": [[
                {"text": "✅ Ha, tekshirsin", "callback_data": f"verify:{month}"},
                {"text": "❌ Yo'q", "callback_data": "verify:cancel"},
            ]]},
        )
    if intent == "run_sync":
        from .roles import can as _can

        role = resolve_role(chat_id) if chat_id is not None else None
        if role is None or not _can(role, "sync"):
            return ("⛔ Sinxronlash uchun ruxsat yo'q."), markup
        return ("🔄 Sinxronlashni boshlaymi?\n\n"
                "BM sayt ma'lumotlarini bazaga yangilayman. Bir necha "
                "daqiqa davom etishi mumkin.",
                {"inline_keyboard": [[
                    {"text": "✅ Ha, bajarsin", "callback_data": "sync:confirm"},
                    {"text": "❌ Yo'q", "callback_data": "sync:cancel"},
                ]]})
    if intent == "status":
        text, _mk = render.status()
        return text, markup

    if intent == "vehicle":
        plate = _extract_plate(text)
        vid = _met().resolve_vehicle(plate, f) if plate else ""
        if not vid:
            return ("🚌 Avtobus topilmadi. Ro'yxat: /vehicles "
                    "yoki raqamni kiriting (masalan: 40128RCA)."), markup
        return render.vehicle_card(vid, f)
    if intent == "driver":
        did = _extract_driver(text)
        if not did:
            return ("👨‍✈️ Haydovchi topilmadi. Ro'yxat: /drivers."), markup
        return render.driver_card(did, f)

    if intent == "routes":
        return render.routes(f)
    if intent == "vehicles":
        return render.vehicles(f)
    if intent == "drivers":
        return render.drivers(f)
    if intent == "trips":
        return render.trips(f)
    if intent == "problems":
        return render.problems(f)
    if intent == "distance":
        fd = {**f, "month": date.today().strftime("%Y-%m")} if not dt else f
        return render.distance(fd)
    if intent == "attendance":
        return render.attendance(f)
    if intent == "schedule":
        return render.schedule(f)
    if intent == "top":
        metric = "km" if "km" in low else ("trips" if "reys" in low else
                                           "net" if "netto" in low else "km")
        return render.leaderboard(f, metric=metric)

    return None


def assist_general(text: str,
                   filters: dict | None = None,
                   chat_id: int | None = None) -> tuple[str, dict] | None:
    """Intent aniqlanmagan savollar uchun LLM javob (kalit bo'lsa).

    Kontekst sifatida bugungi xulosa beriladi; `chat_id` berilsa suhbat
    tarixi ham uzatiladi (ko'p bosqichli savollar uchun). Kalit yo'q yoki
    API xato bo'lsa None — dispatch yordam matniga o'tadi.
    """
    if not openrouter.configured():
        return None
    f = parse_filters(filters or {})
    comp = _extract_company(text)
    if comp and not f.get("profile"):
        f["profile"] = comp
    try:
        ctx = daily_summary(f)
    except Exception as exc:  # noqa: BLE001
        log.warning("assist_general kontekst tayyorlanmadi: %s", exc)
        ctx = "Bugungi ma'lumotlar hozircha mavjud emas."

    ans: str | None = None
    try:
        if chat_id is not None:
            msgs = _history_messages(chat_id, text, ctx)
            ans = openrouter.chat(msgs)
            _remember(chat_id, "user", text)
            _remember(chat_id, "assistant", ans)
        else:
            ans = _llm_answer(text, ctx)
    except Exception as exc:  # noqa: BLE001
        log.warning("assist_general LLM xato: %s", exc)
        return None

    if not ans:
        return None
    markup = render._combine(render._profile_bar(f), kb.nav_kb("nav:settings"))
    return esc(ans), markup
