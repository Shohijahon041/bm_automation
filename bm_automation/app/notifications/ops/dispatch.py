"""Buyruqlar va callback'larni yo'naltirish (role-gated).

`handle_message` — matnli buyruqlar va reply-menyu tugmalari;
`handle_callback` — inline tugmalar (nav / prob / sync / exp / rs).
"""

from __future__ import annotations

import re
import threading
from datetime import date as _date
from datetime import timedelta as _date_timedelta
from typing import Any

from ...db.models import TripStatus
from ...db.storage import get_storage
from ...core import bot_settings
from ...utils.logger import get_logger
from ...utils.names import short_name
from ...utils.tgformat import esc
from ..telegram import telegram_call
from . import assistant, context, driver_entry, kb, legacy, planning, reg, render
from .roles import Role, can, resolve_role, driver_id_for_chat
from .text import (DENIED_TEXT, HELP_TEXT, SYNC_STARTED, SYNC_MONTHLY_STARTED, WELCOME_TEXT)

log = get_logger("bm_automation.bot")

# Reply-menyu tugmalari matni -> handler nomi
MENU_MAP = {
    "📊 dashboard": "today",
    "📅 oy tarixi": "month",
    "📅 oy": "month",
    "🚌 avtobuslar": "vehicles",
    "👨‍✈️ haydovchilar": "drivers",
    "🛣 yo'nalishlar": "routes",
    "📋 reyslar": "trips",
    "⚠️ muammolar": "problems",
    "📈 hisobot": "reports",
    "🔄 sync": "sync",
    "🔄 syncmonthly": "syncmonthly",
    "🖼 grafik yuborish": "grafik",
    "🏢 firmalar": "profiles",
    "⚙️ settings": "settings",
    "❓ yordam": "help",
    "❓ help": "help",
    "➕ firma qo'shish": "addcompany",
    "🔑 login": "login",
    "🏆 reyting": "top",
    "📏 masofa": "distance",
    "📋 davomat": "attendance",
    "🤖 ai yordamchi": "ai",
    "🧠 myai": "myai",
    "🧠 MyAI": "myai",
    "🧠 agent": "myai",
    "💬 suhbat": "sohbat",
    "💬 диалог": "sohbat",
    "🗓 reja": "plan",
    "🗓 plan": "plan",
    "🧠 o'zini tahlil": "insights",
    "💰 oylik": "salary_dl",
    # Ruscha menyu (til tanlanganda)
    "📊 дашборд": "today",
    "📅 месяц": "month",
    "🚌 автобусы": "vehicles",
    "👨‍✈️ водители": "drivers",
    "🛣 маршруты": "routes",
    "📋 рейсы": "trips",
    "⚠️ проблемы": "problems",
    "📈 отчёт": "reports",
    "🔄 синхронизация": "sync",
    "🖼 отправить график": "grafik",
    "🏢 компании": "profiles",
    "⚙️ настройки": "settings",
    "❓ помощь": "help",
    "➕ добавить фирму": "addcompany",
    "🔑 логин": "login",
    "🏆 рейтинг": "top",
    "📏 расстояние": "distance",
    "📋 посещаемость": "attendance",
    "🤖 ai помощник": "ai",
    "🗓 план": "plan",
    "🧠 самоанализ": "insights",
    "💰 зарплата": "salary_dl",
}


def reply(chat_id: int, text: str, reply_markup=None) -> None:
    log.info("reply -> %s: %s", chat_id, (text or "").replace("\n", " ")[:120])
    try:
        from ..telegram import send_chunks
        send_chunks(chat_id, text, reply_markup)
    except Exception as exc:
        log.warning("sendMessage xatosi: %s", exc)


def answer(cq: dict, text: str = "") -> None:
    try:
        telegram_call("answerCallbackQuery", {
            "callback_query_id": cq["id"], "text": text})
    except Exception:
        pass


# ------------------------------------------------------------- myai

def _route_from_filters(f: dict | None) -> str:
    """Faol yo'nalish filteridan route id oladi (bo'sh bo'lsa '')."""
    if not f:
        return ""
    raw = (f.get("route") or "").strip()
    return raw.split(",")[0].strip() if raw else ""


def start_myai(chat_id: int, text: str, f: dict | None = None) -> None:
    """MyAI so'rovini fon thread'da ishga tushiradi (poll bloklanmaydi)."""
    reply(chat_id,
          "🧠 <b>MyAI</b> — so'rov qabul qilindi, javob tayyorlanmoqda...")
    threading.Thread(target=_run_myai,
                     args=(chat_id, text, f), daemon=True).start()


def _run_myai(chat_id: int, text: str, f: dict | None) -> None:
    from . import myai_bridge
    try:
        resp = myai_bridge.ask_myai(
            text, route=_route_from_filters(f), chat_id=chat_id)
        out = myai_bridge.format_response(resp)
        reply(chat_id, out, reply_markup=kb.nav_kb("nav:ai"))
    except Exception as exc:  # noqa: BLE001
        log.warning("MyAI bridge xatosi: %s", exc)
        reply(chat_id, f"❌ MyAI xatosi: {esc(str(exc))}")


# ------------------------------------------------------------- sync

def _sync_summary(results: list) -> str:
    lines = ["🔄 <b>Sinxronlash tugadi</b>", ""]
    for r in results:
        ent = getattr(r, "entity", r)
        ins = getattr(r, "inserted", 0)
        upd = getattr(r, "updated", 0)
        err = getattr(r, "error", "")
        base = f"  {ent}: +{ins} yangi / {upd} yangilangan"
        lines.append(base + (f"  ⚠️ {err}" if err else ""))
    return "\n".join(lines)


def _do_sync(chat_id: int) -> None:
    try:
        from ...db import sync as dsync

        storage = get_storage()
        results = [dsync.sync_statuses(storage), dsync.sync_profiles(storage)]

        try:
            client = legacy.ensure_client()
            results.append(dsync.sync_routes(storage, client))
        except Exception as exc:  # noqa: BLE001 - api xatosi syncni buzmaydi
            results.append(type("R", (), {"entity": "api", "inserted": 0,
                                          "updated": 0, "error": str(exc)})())

        try:
            client = legacy.ensure_client()
            today = _date.today()
            dates = [today.isoformat()]
            dates.insert(0, (today - _date_timedelta(days=1)).isoformat())
            if today.isoweekday() == 1:  # dushanba — shanba/yakshanba ham
                dates.insert(1, (today - _date_timedelta(days=2)).isoformat())
                dates.insert(1, (today - _date_timedelta(days=3)).isoformat())
            for date_str in dict.fromkeys(dates):
                results += dsync.sync_all_profiles(storage, client, date_str)
        except Exception as exc:  # noqa: BLE001
            storage.record_error(source="bot.sync", message=str(exc))

        reply(chat_id, _sync_summary(results), reply_markup=kb.nav_kb("nav:sync"))
    except Exception as exc:  # noqa: BLE001
        storage = get_storage()
        storage.record_error(source="bot.sync", message=str(exc))
        reply(chat_id, f"Sync xatosi: {exc}")


def start_sync(chat_id: int) -> None:
    reply(chat_id, SYNC_STARTED)
    threading.Thread(target=_do_sync, args=(chat_id,), daemon=True).start()


# ---------------------------------------------------------- monthly sync

def _monthly_sync_summary(results: list) -> str:
    lines = ["🔄 <b>Oylik sinxronlash tugadi</b>", ""]
    for r in results:
        ent = getattr(r, "entity", r)
        ins = getattr(r, "inserted", 0)
        upd = getattr(r, "updated", 0)
        err = getattr(r, "error", "")
        base = f"  {ent}: +{ins} yangi / {upd} yangilangan"
        lines.append(base + (f"  ⚠️ {err}" if err else ""))
    return "\n".join(lines)


def _do_monthly_sync(chat_id: int, from_date: str, to_date: str) -> None:
    from datetime import date as _dt_date
    from datetime import timedelta as _dt_td

    try:
        from ...db import sync as dsync

        storage = get_storage()
        d_from = _dt_date.fromisoformat(from_date)
        d_to = _dt_date.fromisoformat(to_date)

        results = [dsync.sync_statuses(storage), dsync.sync_profiles(storage)]

        try:
            client = legacy.ensure_client()
            results.append(dsync.sync_routes(storage, client))
        except Exception as exc:
            results.append(type("R", (), {"entity": "api", "inserted": 0,
                                          "updated": 0, "error": str(exc)})())

        cur = d_from
        while cur <= d_to:
            ds = cur.isoformat()
            reply(chat_id, f"🔄 Sinxronlash: {ds}...")
            try:
                client = legacy.ensure_client()
                results += dsync.sync_all_profiles(storage, client, ds)
            except Exception as exc:
                storage.record_error(source="bot.sync_monthly", message=str(exc))
            cur += _dt_td(days=1)

        reply(chat_id, _monthly_sync_summary(results),
              reply_markup=kb.nav_kb("nav:sync"))
    except Exception as exc:
        storage = get_storage()
        storage.record_error(source="bot.sync_monthly", message=str(exc))
        reply(chat_id, f"Oylik sync xatosi: {exc}")


def start_monthly_sync(chat_id: int, from_date: str, to_date: str) -> None:
    from ...text import SYNC_MONTHLY_STARTED
    reply(chat_id, SYNC_MONTHLY_STARTED)
    threading.Thread(target=_do_monthly_sync,
                     args=(chat_id, from_date, to_date), daemon=True).start()


# ------------------------------------------------------------ verify (oylik)

def _verify_month_arg(parts: list[str]) -> str | None:
    """/verify argumentlaridan oy (YYYY-MM) topadi; yo'q bo'lsa None."""
    for tok in parts[1:]:
        tok = tok.strip()
        if re.match(r"^\d{4}-\d{2}$", tok):
            return tok
    return None


def _do_verify(chat_id: int, month: str | None = None) -> None:
    try:
        from ...services import verify

        data = verify.verify_month(month=month,
                                   filters=context.filters_for(chat_id))
        ai = None
        if data.get("ok"):
            try:
                ai = verify.ai_report(verify.digest(data))
            except Exception:  # noqa: BLE001 - AI xato bo'lsa lokal xulosa
                ai = None
        reply(chat_id, verify.render(data, ai)[0])
    except Exception as exc:  # noqa: BLE001
        get_storage().record_error(source="bot.verify", message=str(exc))
        reply(chat_id, f"❌ Tekshirish xatosi: {exc}")


def start_verify(chat_id: int, month: str | None = None) -> None:
    reply(chat_id, "🔎 Tekshirish boshlandi... bir necha daqiqa davom etishi "
                   "mumkin.")
    threading.Thread(target=_do_verify, args=(chat_id, month), daemon=True).start()


# ------------------------------------------------------------- grafik (18:00 dagi)

def _do_grafik(chat_id: int, route_ids: list[str] | None = None) -> None:
    try:
        import daily_grafik
        daily_grafik.main(chat_id=str(chat_id), route_ids=route_ids)
        reply(chat_id, "✅ <b>Grafik yuborildi</b>")
    except Exception as exc:  # noqa: BLE001 - barcha xatolar foydalanuvchiga
        log.warning("grafik yuborish xatosi: %s", exc)
        try:
            reply(chat_id, f"❌ Grafik yuborishda xatolik:\n<code>{exc}</code>")
        except Exception:  # noqa: BLE001
            pass


def start_grafik(chat_id: int, route_ids: list[str] | None = None) -> None:
    reply(chat_id, "🖼 <b>GRAFIK YUBORISH</b>\n\n"
                   "Ertangi kun grafiklari (xlsx + rasm + statistika) "
                   "tayyorlanmoqda. Bir necha daqiqa davom etishi mumkin...")
    threading.Thread(target=_do_grafik, args=(chat_id, route_ids), daemon=True).start()


# ------------------------------------------------------------- company add

def start_addcompany(chat_id: int) -> None:
    """Yangi firma ro'yxatdan o'tkazishni boshlaydi (ADMIN)."""
    reg.start(chat_id)
    prompts = reg.prompts()
    reply(chat_id, "➕ <b>YANGI FIRMA QO'SHISH</b>\n\n" + prompts["name"],
          reply_markup=kb.back_kb("nav:profiles", "❌ Bekor qilish"))


def continue_addcompany(chat_id: int, text: str) -> None:
    """Interaktiv ro'yxatdan o'tkazishning keyingi bosqichi."""
    prompts = reg.prompts()
    step = reg.set_value(chat_id, text)
    if step in (None, "done"):
        commit_addcompany(chat_id)
        return
    reply(chat_id, prompts.get(step, "❓ Davom eting:"))


def commit_addcompany(chat_id: int) -> None:
    """Yakunlangan firmani profillarga yozadi va statistikani ko'rsatadi."""
    from ...core.profiles import owner_chat_ids, set_owner_chat_ids, upsert_profile

    data = reg.commit(chat_id) or {}
    name = data.get("name") or ""
    route = data.get("route") or ""
    owners = data.get("owners") or ""
    username = data.get("username") or ""
    password = data.get("password") or ""

    if not name or not route:
        reply(chat_id, "⚠️ Nom yoki yo'nalish ID kiritilmadi. Bekor qilindi.")
        return

    profile = {"name": name, "routeVariantId": route}
    if username and password:
        profile["username"] = username
        profile["password"] = password
    upsert_profile(profile)

    owner_ids = [int(t.strip()) for t in owners.replace(",", " ").split()
                 if t.strip().lstrip("-").isdigit()]
    if owner_ids:
        set_owner_chat_ids(name, owner_ids)

    reply(chat_id,
          f"✅ <b>{name}</b> ro'yxatdan o'tkazildi.\n"
          f"🛣 Yo'nalish: <code>{route}</code>\n"
          f"👤 Egalar: {', '.join(str(o) for o in owner_ids) or '-'}\n"
          f"🔑 Login: {'kiritildi' if username and password else 'keyin /login orqali'}\n\n"
          f"Statistika tayyorlanmoqda...")

    threading.Thread(target=show_company_stats,
                     args=(chat_id, name, owner_ids), daemon=True).start()


def show_company_stats(chat_id: int, name: str, owner_ids: list[int]) -> None:
    """Yangi firma uchun byBus statistikasini yig'ib yuboradi."""
    from datetime import date
    from ...core.companies import client_for_profile
    from ...core.profiles import get_profile
    from ...services.stats_service import run as stats_run

    profile = get_profile(name)
    if not profile:
        reply(chat_id, f"⚠️ {name} topilmadi.")
        return
    rid = str(profile.get("routeVariantId") or "").strip()
    today = date.today()
    try:
        client = client_for_profile(profile)
        res = stats_run(client, rid, today.isoformat(), today.isoformat(),
                        title_word=today.strftime("%A"))
        text = res["text"]
    except Exception as exc:
        text = f"⚠️ Statistika olinmadi: {exc}"

    targets = list(dict.fromkeys([chat_id] + [o for o in owner_ids if o != chat_id]))
    for t in targets:
        try:
            reply(t, text)
        except Exception as exc:
            log.warning("Statistika yuborilmadi [%s]: %s", t, exc)
    reg.cancel(chat_id)


def handle_login(chat_id: int, parts: list[str]) -> None:
    """Firma egasi o'z login/parolini kiritadi: /login <username> <password>.

    Argumentlarsiz `/login` yuborilsa — interaktiv oqim boshlanadi
    (avval username, keyin parol). Firma avtomatik aniqlanadi: kredensiallar
    bilan qaysi firmaning yo'nalishiga kirish mumkin bo'lsa, o'sha firma
    egasiga biriktiriladi (ownerChatIds + kredensiallar saqlanadi) va
    statistikasi yuboriladi.
    """
    args = [p for p in parts[1:] if p]
    if len(args) < 2:
        reg.start(chat_id, flow="login")
        reply(chat_id, "🔑 <b>LOGIN</b>\n\n" + reg.login_prompts()["username"],
              reply_markup=kb.back_kb("nav:profiles", "❌ Bekor qilish"))
        return
    _run_login(chat_id, args[0], args[1])


def _run_login(chat_id: int, username: str, password: str) -> None:
    """Login/parol bilan firmani aniqlab, egasiga ulaydi (thread'da)."""
    reply(chat_id, "🔍 Login tekshirilmoqda...")

    def _run() -> None:
        from ...core.companies import detect_company
        from ...core.profiles import owner_chat_ids, profile_by_owner, set_credentials

        try:
            profile, _client = detect_company(username, password)
        except Exception as exc:
            reply(chat_id, f"⛔ Login muvaffaqiyatsiz: {exc}")
            return

        if not profile:
            # Firmasi topilmadi — lekin kredensiallar to'g'ri. Ega ekanini
            # avvaldan ro'yxatda bo'lsa ham tekshiramiz.
            owned = profile_by_owner(chat_id)
            if not owned:
                reply(chat_id, "⛔ Firmangiz topilmadi. Admin bilan bog'laning "
                               "(<code>/addcompany</code>).")
                return
            profile = owned[0]

        name = profile["name"]
        set_credentials(name, username, password)
        # Mavjud egalarni saqlab, yangi chat_id ni qo'shamiz.
        owners = list(dict.fromkeys(owner_chat_ids(profile) + [chat_id]))
        set_owner_chat_ids(name, owners)

        reply(chat_id, f"✅ <b>{name}</b> firmasiga ulandingiz.")
        show_company_stats(chat_id, name, [chat_id])

    threading.Thread(target=_run, daemon=True).start()


def continue_login(chat_id: int, text: str) -> None:
    """Interaktiv login oqimini davom ettiradi (username -> password)."""
    prompts = reg.login_prompts()
    step = reg.set_value(chat_id, text)
    if step in (None, "done"):
        data = reg.commit(chat_id) or {}
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()
        if not username or not password:
            reply(chat_id, "⚠️ Login yoki parol kiritilmadi. Bekor qilindi.")
            return
        _run_login(chat_id, username, password)
        return
    reply(chat_id, prompts.get(step, "❓ Davom eting:"))


# ----------------------------------------------------------- driver card

def handle_driver(chat_id: int, parts: list[str], f: dict) -> None:
    """`/driver` buyrug'i: ism/ID bo'yicha haydovchi kartasi.

    Argument bo'lmasa — tanlash uchun `/drivers` ro'yxati ko'rsatiladi.
    """
    arg = " ".join(parts[1:]).strip()
    if not arg:
        reply(chat_id, *render.drivers(f))
        return
    driver_id = render.resolve_driver(arg, f)
    if not driver_id:
        reply(chat_id,
              "👨‍✈️ <b>HAYDOVCHI TOPILMADI</b>\n\n"
              f"<code>{esc(arg)}</code> ism yoki ID bilan topilmadi.\n"
              "Ro'yxat: /drivers")
        return
    reply(chat_id, *render.driver_card(driver_id, f))


# ------------------------------------------------------ driver entry flow

def start_driver_entry(chat_id: int, flow: str, driver_id: str) -> None:
    """Haydovchi kunlik qayd / jarima dialogini boshlaydi."""
    driver_entry.start(chat_id, flow, driver_id)
    st = driver_entry.current(chat_id) or {}
    title = ("📝 <b>KUNLIK KM QAYDI</b>" if flow == "log"
             else "⚠️ <b>JARIMA KIRITISH</b>")
    prompt = driver_entry.prompts().get(st.get("step", "date"), "❓ Davom eting:")
    reply(chat_id, f"{title}\n\n{prompt}", reply_markup=kb.driver_entry_cancel_kb())


def continue_driver_entry(chat_id: int, text: str) -> None:
    """Interaktiv qayd / jarima oqimini davom ettiradi."""
    prompts = driver_entry.prompts()
    try:
        step = driver_entry.set_value(chat_id, text)
    except ValueError as exc:
        st = driver_entry.current(chat_id) or {}
        reply(chat_id, f"⚠️ {exc}\n\n"
                       f"{prompts.get(st.get('step', 'date'), '❓ Davom eting:')}",
              reply_markup=kb.driver_entry_cancel_kb())
        return
    if step is None:
        commit_driver_entry(chat_id)
        return
    reply(chat_id, prompts.get(step, "❓ Davom eting:"),
          reply_markup=kb.driver_entry_cancel_kb())


def _notify_driver(driver_id: str, flow: str, data: dict) -> str:
    """Haydovchiga Telegram bildirishnoma yuboradi.

    Qaytaradi: "✅ Yuborildi" yoki "⚠️ Yuborilmadi (sabab)".
    """
    st = get_storage()
    profile = st.find("driver_profiles", driver_id=str(driver_id)) or {}
    target = str(profile.get("notification_target") or "").strip()
    enabled = profile.get("notification_enabled")

    if not enabled or not target:
        return "⚠️ Bildirishnoma yoqilmagan yoki Telegram ID kiritilmagan"

    driver = st.find("drivers", external_id=str(driver_id)) or {}
    name = short_name(str(driver.get("full_name") or profile.get("phone") or "Haydovchi"))

    from html import escape as _esc
    en = _esc(name)
    ed = _esc(data.get("date", "?"))

    if flow == "log":
        km = _esc(str(data.get("km") or "0"))
        trips = _esc(str(data.get("trips") or "0"))
        vehicle = _esc(str(data.get("vehicle") or "—"))
        msg = (
            f"🚌 <b>Kunlik qayd — tasdiqlandi</b>\n"
            f"\n"
            f"Hurmatli <b>{en}</b>,\n"
            f"Sizning {ed} kungi ish qaydingiz <b>admin tomonidan tasdiqlandi</b>:\n"
            f"\n"
            f"  📅 Sana: <code>{ed}</code>\n"
            f"  🚌 Avtobus: <b>{vehicle}</b>\n"
            f"  📏 Masofa: <b>{km} km</b>\n"
            f"  🔁 Qatnovlar: <b>{trips}</b>\n"
            f"\n"
            f"Ma'lumotlar tizimga kiritildi."
        )
    else:
        amount = _esc(str(data.get("amount") or "0"))
        reason = _esc(str(data.get("reason") or "—"))
        msg = (
            f"⚠️ <b>Jarima — tasdiqlandi</b>\n"
            f"\n"
            f"Hurmatli <b>{en}</b>,\n"
            f"Sizning {ed} kungi jarimangiz <b>admin tomonidan tasdiqlandi</b>:\n"
            f"\n"
            f"  📅 Sana: <code>{ed}</code>\n"
            f"  💸 Summa: <b>{amount} so'm</b>\n"
            f"  📝 Sabab: <b>{reason}</b>\n"
            f"\n"
            f"Ma'lumotlar tizimga kiritildi."
        )

    try:
        from ..telegram import send_message
        send_message(msg, chat_id=target)
        return "✅ Haydovchiga yuborildi"
    except Exception as exc:  # noqa: BLE001
        log.warning("Haydovchiga bildirishnoma yuborilmadi (%s): %s", driver_id, exc)
        return f"⚠️ Yuborilmadi: {exc}"


def commit_driver_entry(chat_id: int) -> None:
    """Yakunlangan qayd/jarimani saqlaydi, haydovchiga bildirishnoma yuboradi."""
    result = driver_entry.commit(chat_id)
    if not result:
        return
    data, flow, driver_id = result
    st = get_storage()

    if flow == "log":
        st.save_driver_work_log(date=data.get("date", ""), driver_id=driver_id,
                                vehicle_id=data.get("vehicle", ""),
                                distance_km=float(data.get("km") or 0),
                                trip_count=int(data.get("trips") or 0))
        reply(chat_id, "✅ <b>Kunlik qayd saqlandi</b>")
    else:
        st.add_driver_fine(driver_id=driver_id, date=data.get("date", ""),
                           amount=float(data.get("amount") or 0),
                           reason=data.get("reason", ""))
        reply(chat_id, "✅ <b>Jarima saqlandi</b>")

    # Haydovchiga bildirishnoma yuborish
    notify_result = _notify_driver(driver_id, flow, data)
    reply(chat_id, f"📨 {notify_result}")

    reply(chat_id, *render.driver_card(driver_id, context.filters_for(chat_id)))


# ------------------------------------------------------------- export

_SCOPES = ("today", "routes", "vehicles", "drivers", "trips", "all",
           "distance", "schedule", "attendance", "rating")
_FORMATS = ("csv", "xlsx", "pdf")


def _parse_export_args(parts: list[str]) -> tuple[str, str]:
    fmt, scope = "xlsx", "all"
    for tok in parts[1:]:
        t = tok.lower()
        if t in _FORMATS:
            fmt = t
        elif t in _SCOPES:
            scope = t
    return fmt, scope


def _do_export(chat_id: int, fmt: str, scope: str) -> None:
    try:
        from ...dashboard.export import build_export, filename

        f = context.filters_for(chat_id)
        data = build_export(f, fmt, scope)
        name = filename(fmt, scope, f.get("date", ""))
        reply(chat_id, f"📎 Eksport tayyor: {name}")
        from ..telegram import send_bytes
        send_bytes(name, data, caption=f"📤 Dashboard eksport ({scope})",
                   chat_id=chat_id)
    except Exception as exc:  # noqa: BLE001
        reply(chat_id, f"Eksport xatosi: {exc}")


def start_export(chat_id: int, fmt: str, scope: str) -> None:
    reply(chat_id, "📤 Eksport tayyorlanmoqda...")
    threading.Thread(target=_do_export, args=(chat_id, fmt, scope),
                     daemon=True).start()


def _do_salary_dl(chat_id: int, from_date: str, to_date: str) -> None:
    try:
        from ...dashboard.export import salary_export, salary_filename

        data = salary_export(from_date, to_date)
        name = salary_filename(from_date, to_date)
        reply(chat_id, f"📎 Oylik hisobot tayyor: {name}")
        from ..telegram import send_bytes
        send_bytes(name, data,
                   caption=f"💰 Oylik hisobot ({from_date} — {to_date})",
                   chat_id=chat_id)
    except Exception as exc:  # noqa: BLE001
        reply(chat_id, f"❌ Oylik yuklab olishda xatolik:\n<code>{exc}</code>")


def _start_salary_dl(chat_id: int, from_date: str, to_date: str) -> None:
    reply(chat_id, "💰 Oylik hisobot tayyorlanmoqda...")
    threading.Thread(target=_do_salary_dl, args=(chat_id, from_date, to_date),
                     daemon=True).start()


# ------------------------------------------------------------ /trips args

def _trip_filters(parts: list[str]) -> dict:
    f: dict[str, str] = {}
    for tok in parts[1:]:
        tok = tok.strip()
        if not tok:
            continue
        try:
            _date.fromisoformat(tok)
            f["date"] = tok
            continue
        except ValueError:
            pass
        up = tok.upper()
        if TripStatus.is_valid(up):
            f["status"] = up
    return f


# --------------------------------------------------------------- routing

def _set_route_km_rate(chat_id: int, value: float) -> int:
    """Joriy yo'nalishdagi haydovchilar km_rate ni DB ga yozadi. O'zgartirilgan sonini qaytaradi."""
    try:
        from ...db import get_storage
        from .context import filters_for, get_active
        st = get_storage()
        if not st.enabled:
            return 0
        f = filters_for(chat_id)
        route_id = f.get("route") or ""
        if not route_id:
            name = get_active(chat_id)
            if name:
                from .context import _filter_for
                f2 = _filter_for(name)
                route_id = f2.get("route") or ""
        if not route_id:
            return 0
        rids = [r.strip() for r in route_id.split() if r.strip()]
        if len(rids) == 1:
            result = st.db.execute(
                "UPDATE driver_profiles SET km_rate = %s "
                "WHERE driver_id IN ("
                "  SELECT external_id FROM drivers WHERE route_id = %s"
                ")",
                (value, rids[0]),
            )
        else:
            placeholders = ", ".join(["%s"] * len(rids))
            result = st.db.execute(
                f"UPDATE driver_profiles SET km_rate = %s "
                f"WHERE driver_id IN ("
                f"  SELECT external_id FROM drivers WHERE route_id IN ({placeholders})"
                f")",
                (value, *rids),
            )
        return int(result or 0) if isinstance(result, int) else 0
    except Exception as exc:
        log.warning("km_rate DB ga yozishda xatolik: %s", exc)
        return 0


_DRIVER_LINK_STATE: set[int] = set()
_DRIVER_LINK_LOCK = threading.Lock()

# ── MyAI "suhbat" rejimi (har qanday matn to'g'ridan-to'g'ri MyAI ga) ──
_CHAT_MODE_STATE: set[int] = set()
_CHAT_MODE_LOCK = threading.Lock()
_CHAT_MODE_EXIT = {"o'chirish", "ochirish", "tugatish", "chiqish", "chiq",
                   "toxtat", "to'xtat", "stop", "off", "exit", "/chiqish",
                   "/stop", "/off", "suhbatni tugat", "suhbatni yop"}


def _chat_mode_on(chat_id: int) -> None:
    with _CHAT_MODE_LOCK:
        _CHAT_MODE_STATE.add(chat_id)


def _chat_mode_off(chat_id: int) -> None:
    with _CHAT_MODE_LOCK:
        _CHAT_MODE_STATE.discard(chat_id)


def _chat_mode_active(chat_id: int) -> bool:
    with _CHAT_MODE_LOCK:
        return chat_id in _CHAT_MODE_STATE


def _try_link_driver(chat_id: int, text: str) -> None:
    """Haydovchini Telegram ga bog'lash oqimi.

    1) Agar /start bo'lsa — telefon raqamini so'raymiz.
    2) Agar raqam kiritilgan bo'lsa — DB dan qidiramiz va bog'laymiz.
    """
    from .roles import driver_id_for_chat, _DRIVER_CHAT_CACHE
    from ...db import get_storage

    text_clean = text.strip().lower().replace(" ", "").replace("-", "")
    if text_clean in ("start", "/start"):
        reply(chat_id,
              "👤 <b>Haydovchi kirishi</b>\n\n"
              "Telefon raqamingizni kiriting (masalan: <code>901234567</code>).\n"
              "Biz sizni tizimdan topamiz.")
        with _DRIVER_LINK_LOCK:
            _DRIVER_LINK_STATE.add(chat_id)
        return

    with _DRIVER_LINK_LOCK:
        in_link = chat_id in _DRIVER_LINK_STATE
        if in_link:
            _DRIVER_LINK_STATE.discard(chat_id)
    if in_link:
        phone = text_clean.lstrip("+").removeprefix("998")
        if len(phone) < 7:
            reply(chat_id, "⚠️ Noto'g'ri raqam. Qaytadan kiriting:")
            with _DRIVER_LINK_LOCK:
                _DRIVER_LINK_STATE.add(chat_id)
            return
        st = get_storage()
        row = st.find_driver_by_notification(phone)
        if not row:
            row = st.find_driver_by_notification(text_clean)
        if not row:
            reply(chat_id, "❌ Bu raqam tizimda topilmadi. "
                           "Admin bilan bog'laning.")
            return
        driver_id = str(row.get("driver_id") or "")
        if not driver_id:
            reply(chat_id, "❌ Haydovchi ID topilmadi.")
            return
        st.link_driver_telegram(driver_id, chat_id)
        _DRIVER_CHAT_CACHE[chat_id] = driver_id
        from ...db import get_storage as _gs
        _drow = _gs().find("drivers", external_id=driver_id)
        dname = short_name(str((_drow or {}).get("full_name") or driver_id[:12]))
        reply(chat_id,
              f"✅ <b>Xush kelibsiz, {dname}!</b>\n\n"
              "Siz tizimga bog'landingiz. Menyudan foydalaning.",
              reply_markup=kb.main_menu_kb(chat_id))
        return

def _insights_render(_filters: dict | None = None) -> tuple[str, dict]:
    """AI o'z-o'zini rivojlantirish hisoboti (nav:insights uchun)."""
    from . import self_review
    return self_review.build_text(), {"inline_keyboard": []}


def handle_message(chat_id: int, text: str) -> None:
    text = text.strip()
    if not text:
        return
    low = text.lower()
    parts = text.split()
    log.info("message <- %s: %s", chat_id, text[:120])

    cmd = MENU_MAP[low] if low in MENU_MAP and not low.startswith("/") else \
        parts[0].lower().replace("/", "", 1)

    role = resolve_role(chat_id)

    # Haydovchi bog'lash oqimi — davom ettirish
    with _DRIVER_LINK_LOCK:
        in_link_state = chat_id in _DRIVER_LINK_STATE
    if in_link_state and cmd not in ("start", "help"):
        _try_link_driver(chat_id, text)
        return

    if cmd in ("start", "help"):
        if cmd == "start" and role is Role.VIEWER:
            did = driver_id_for_chat(chat_id)
            if not did:
                _try_link_driver(chat_id, text)
                return
        if cmd == "start" and role is Role.DRIVER:
            did = driver_id_for_chat(chat_id)
            from ...db import get_storage as _dbs
            _drow = _dbs().find("drivers", external_id=did) if did else None
            dname = short_name(str((_drow or {}).get("full_name") or "Haydovchi"))
            reply(chat_id,
                  f"👋 <b>Xush kelibsiz, {dname}!</b>\n\n"
                  "Siz haydovchi sifatida bog'langansiz.\n"
                  "Menyudan foydalaning:",
                  reply_markup=kb.main_menu_kb(chat_id))
            return
        reply(chat_id, WELCOME_TEXT if cmd == "start" else HELP_TEXT,
              reply_markup=kb.main_menu_kb(chat_id))
        return

    f = context.filters_for(chat_id)

    # Settings'dan sozlamani o'zgartirish oqimi davom etmoqda (km narxi)
    pending_setting = bot_settings.pending(chat_id)
    if pending_setting == "km_rate":
        bot_settings.clear_pending(chat_id)
        try:
            value = float(text.replace(",", ".").replace(" ", "").strip())
        except ValueError:
            bot_settings.set_pending(chat_id, "km_rate")
            reply(chat_id, "⚠️ Son kiriting (so'm), masalan: <code>2000</code> "
                           "yoki <code>1782.98</code>. Bekor qilish: /settings")
            return
        value = max(value, 0.0)
        count = _set_route_km_rate(chat_id, value)
        if value == 0:
            reply(chat_id,
                  "✅ 1 km narxi asl holatga qaytarildi "
                  f"({count} ta haydovchi)",
                  reply_markup=kb.settings_kb(chat_id))
        else:
            reply(chat_id,
                  f"✅ 1 km narxi o'rnatildi: <b>{value:,.0f}".replace(",", " ")
                  + f" so'm</b> ({count} ta haydovchi)",
                  reply_markup=kb.settings_kb(chat_id))
        return

    if pending_setting == "salary_date":
        bot_settings.clear_pending(chat_id)
        import re as _re
        m = _re.match(r"(\d{4}-\d{2}-\d{2})\s*[-–]\s*(\d{4}-\d{2}-\d{2})", text.strip())
        if not m:
            bot_settings.set_pending(chat_id, "salary_date")
            reply(chat_id, "⚠️ Noto'g'ri format. Qaytadan kiriting:\n"
                           "<code>YYYY-MM-DD - YYYY-MM-DD</code>\n"
                           "Masalan: <code>2026-08-01 - 2026-08-15</code>")
            return
        from_date, to_date = m.group(1), m.group(2)
        if from_date > to_date:
            bot_settings.set_pending(chat_id, "salary_date")
            reply(chat_id, "⚠️ Boshlanish sanasi tugash sanasidan oldin bo'lishi kerak.")
            return
        _start_salary_dl(chat_id, from_date, to_date)
        return

    if cmd == "today":
        if role is Role.DRIVER:
            did = driver_id_for_chat(chat_id)
            if did:
                reply(chat_id, *render.driver_card(did, f))
                return
        reply(chat_id, *render.today(f))
        return
    if cmd == "month":
        reply(chat_id, *render.month(f))
        return
    if cmd == "profiles":
        reply(chat_id, *render.profiles(f, chat_id=chat_id))
        return
    if cmd == "routes":
        reply(chat_id, *render.routes(f))
        return
    if cmd == "vehicles":
        reply(chat_id, *render.vehicles(f))
        return
    if cmd == "drivers":
        reply(chat_id, *render.drivers(f))
        return
    if cmd == "driver":
        handle_driver(chat_id, parts, f)
        return
    if cmd == "vehicle":
        arg = " ".join(parts[1:]).strip()
        vehicle_id = render.resolve_vehicle(arg, f) if arg else None
        if not vehicle_id:
            reply(chat_id, *render.vehicles(f))
            return
        reply(chat_id, *render.vehicle_card(vehicle_id, f))
        return
    if cmd == "top":
        metric = parts[1].lower() if len(parts) > 1 else "km"
        reply(chat_id, *render.leaderboard(f, metric=metric, chat_id=chat_id))
        return
    if cmd == "distance":
        month_f = {**f, "month": _date.today().strftime("%Y-%m")}
        reply(chat_id, *render.distance(month_f))
        return
    if cmd == "schedule":
        f2 = _trip_filters(parts) | f
        reply(chat_id, *render.schedule(f2))
        return
    if cmd == "attendance":
        f2 = _trip_filters(parts) | f
        reply(chat_id, *render.attendance(f2))
        return
    if cmd == "alerts":
        reply(chat_id, *render.alerts_text())
        return
    if cmd == "daily":
        from . import daily_summary
        reply(chat_id, daily_summary.send_now())
        return
    if cmd == "ai":
        arg = " ".join(parts[1:]).strip()
        if not arg:
            reply(chat_id, assistant.analyze(f))
        elif arg.lower() in ("toza", "clear", "yangi", "reset"):
            assistant.clear_history(chat_id)
            reply(chat_id, "🗑 Suhbat tarixi tozalandi. Yangi savol bering!")
        else:
            out = assistant.assist(arg, f, chat_id=chat_id)
            if out is None:
                out = assistant.assist_general(arg, f, chat_id=chat_id)
            reply(chat_id, *(out or (assistant.help_text(), None)))
        return
    if cmd == "myai":
        arg = " ".join(parts[1:]).strip()
        if not arg:
            reply(chat_id,
                  "🧠 <b>MyAI Agent</b>\n\n"
                  "Menga istalgan savol yoki topshiriq bering.\n"
                  "Men ko'p agentli AI tizimi orqali javob beraman.\n\n"
                  "Masalan:\n"
                  "  • <code>/myai bugungi reyslar qanday?</code>\n"
                  "  • <code>/myai B-80 haydovchilari</code>\n"
                  "  • <code>/myai oylik hisobot tayyorla</code>\n"
                  "  • <code>/myai hisobotni telegramga yubor</code>\n\n"
                  "Qolgan buyruqlar uchun /yordam.",
                  reply_markup=kb.nav_kb("nav:ai"))
            return
        start_myai(chat_id, arg, f)
        return
    if cmd in ("sohbat", "suhbat", "chat"):
        if _chat_mode_active(chat_id):
            _chat_mode_off(chat_id)
            reply(chat_id,
                  "⏹ <b>Suhbat rejimi o'chirildi.</b>\n"
                  "Endi hech narsa avtomatik yuborilmaydi. "
                  "Yana yozish uchun <b>🧠 suhbat</b> tugmasini bosing.")
            return
        _chat_mode_on(chat_id)
        reply(chat_id,
              "💬 <b>Suhbat rejimi yoqildi!</b>\n\n"
              "Endi menga <u>har qanday savol yoki topshiriq</u> yozishingiz mumkin "
              "— <code>/myai</code> yozmasdan ham javob olasiz.\n\n"
              "Masalan:\n"
              "  • <code>bugungi reyslar qanday?</code>\n"
              "  • <code>B-80 haydovchilarini ko'rsat</code>\n"
              "  • <code>sentabr oyidagi elektr sarfi</code>\n\n"
              "Suhbatni tugatish uchun <b>⏹ tugatish</b> yoki <code>/chiqish</code> deb yozing.",
              reply_markup=kb.nav_kb("nav:ai"))
        return
    if cmd == "insights":
        from . import self_review
        reply(chat_id, self_review.send_now())
        return
    if cmd == "trips":
        reply(chat_id, *render.trips(_trip_filters(parts) | f))
        return
    if cmd == "problems":
        reply(chat_id, *render.problems(f))
        return
    if cmd == "reports":
        reply(chat_id, *render.reports())
        return
    if cmd == "errors":
        reply(chat_id, *render.errors())
        return
    if cmd == "status":
        reply(chat_id, *render.status())
        return
    if cmd == "settings":
        reply(chat_id, render.settings_text(chat_id),
              reply_markup=kb.settings_kb(chat_id))
        return
    if cmd in ("setrole", "role"):
        from ...core import bot_users
        from .roles import _parse_role, Role as _Role
        if resolve_role(chat_id) is not _Role.ADMIN:
            reply(chat_id, DENIED_TEXT)
            return
        if len(parts) < 3:
            reply(chat_id,
                  "🎭 <b>ROL TAYINLASH</b>\n\n"
                  "Ishlatish: <code>/setrole &lt;chat_id&gt; &lt;rol&gt;</code>\n\n"
                  "Rollar: <code>admin</code>, <code>dispatcher</code>, "
                  "<code>manager</code>, <code>driver</code>, <code>viewer</code>\n\n"
                  "Misol: <code>/setrole 123456789 manager</code>")
            return
        target = parts[1].strip()
        if not target.isdigit():
            reply(chat_id, "⚠️ <code>chat_id</code> raqam bo'lishi kerak.\n"
                           "Avval <code>/users</code> orqali ro'yxatni ko'ring.")
            return
        new_role = _parse_role(parts[2])
        if new_role is None:
            reply(chat_id, "⚠️ Noto'g'ri rol. Mavjud: admin, dispatcher, "
                           "manager, driver, viewer.")
            return
        target_id = int(target)
        if not bot_users.get_user(target_id):
            reply(chat_id,
                  f"⚠️ {target_id} ro'yxatda yo'q. Avval foydalanuvchi "
                  f"<b>/start</b> bosing yoki <code>/users</code> orqali ko'ring.")
            return
        bot_users.set_role(target_id, new_role.value)
        reply(chat_id,
              f"✅ {target_id} ga <b>{new_role.value}</b> roli tayinlandi.")
        return
    if cmd == "users":
        from ...core import bot_users
        if resolve_role(chat_id) is not Role.ADMIN and \
           resolve_role(chat_id) is not Role.DISPATCHER:
            reply(chat_id, DENIED_TEXT)
            return
        rows = bot_users.get_users()
        if not rows:
            reply(chat_id, "📭 Hozircha foydalanuvchilar yo'q.")
            return
        lines = ["👥 <b>BOT FOYDALANUVCHILARI</b>\n"]
        for u in rows[:40]:
            cid = u.get("chat_id")
            name = (u.get("first_name") or u.get("username") or str(cid))
            role = (u.get("role") or "—")
            seen = (u.get("last_seen") or "")[:16]
            lines.append(f"<code>{cid}</code> — {esc(name)} · <b>{role}</b> · "
                         f"{seen}")
        reply(chat_id, "\n".join(lines))
        return
    if cmd == "myrole":
        from .roles import role_label as _rl
        reply(chat_id, f"🎭 Sizning rolingiz: {_rl(resolve_role(chat_id))}")
        return
    if cmd == "list":
        reply(chat_id, legacy.list_text())
        return

    if cmd == "sync":
        if not can(role, "sync"):
            reply(chat_id, DENIED_TEXT)
            return
        reply(chat_id, "🔄 Sinxronlash bosilsinmi?",
              reply_markup=kb.sync_confirm_kb())
        return

    if cmd == "syncmonthly":
        if not can(role, "sync"):
            reply(chat_id, DENIED_TEXT)
            return
        from datetime import date as _dt_date
        from datetime import timedelta as _dt_td
        today = _dt_date.today()
        first = today.replace(day=1)
        from_date = first.isoformat()
        to_date = today.isoformat()
        reply(chat_id,
              f"🔄 <b>Oylik sinxronlash</b>\n"
              f"   {from_date} → {to_date}\n\n"
              f"Oy boshidan bugungacha sinxronlaymizmi?",
              reply_markup=kb.sync_monthly_confirm_kb())
        return

    if cmd == "verify":
        if not can(role, "salary"):
            reply(chat_id, DENIED_TEXT)
            return
        start_verify(chat_id, _verify_month_arg(parts))
        return

    if cmd == "plan":
        if not can(role, "plan"):
            reply(chat_id, DENIED_TEXT)
            return
        arg = " ".join(parts[1:]).strip()
        if arg:
            reply(chat_id, planning.add_from_text(arg, by=chat_id),
                  reply_markup=kb.plan_kb())
            return
        reply(chat_id, planning.plan_text(), reply_markup=kb.plan_kb())
        return

    if cmd == "export":
        if not can(role, "export"):
            reply(chat_id, DENIED_TEXT)
            return
        fmt, scope = _parse_export_args(parts)
        start_export(chat_id, fmt, scope)
        return

    if cmd == "resend":
        if not can(role, "resend"):
            reply(chat_id, DENIED_TEXT)
            return
        name = " ".join(parts[1:]).strip() or None
        # Argument'siz /resend — ADMIN barcha profillarni, boshqalar faqat
        # o'ziga biriktirilgan yo'nalish(lar) bo'yicha qayta yuboradi.
        if name is None and role is not Role.ADMIN:
            assigned = context.assigned_profile_names(chat_id)
            if not assigned:
                reply(chat_id, "Sizga yo'nalish biriktirilmagan. "
                               "Admin yo'nalish biriktirishini so'rang.")
                return
            rids = context.assigned_route_ids(chat_id)
            legacy.start_resend(chat_id, assigned, routes=rids or None)
            return
        legacy.start_resend(chat_id, name)
        return

    if cmd == "grafik":
        if not can(role, "sync"):
            reply(chat_id, DENIED_TEXT)
            return
        # ADMIN → hammasi; boshqalar → faqat o'z firmasi yo'nalishlari
        rids = None
        if role is not Role.ADMIN:
            f = context.filters_for(chat_id)
            route_filter = (f.get("route") or "").strip()
            rids = [t.strip() for t in route_filter.replace(",", " ").split()
                    if t.strip()] if route_filter else None
        start_grafik(chat_id, rids)
        return

    if cmd == "salary_dl":
        if not can(role, "salary"):
            reply(chat_id, DENIED_TEXT)
            return
        reply(chat_id, "💰 <b>OYLIK YUKLAB OLISH</b>\n\n"
                       "Sana oralig'ini tanlang:",
              reply_markup=kb.salary_dl_kb())
        return

    if cmd == "addcompany":
        if not can(role, "addcompany"):
            reply(chat_id, DENIED_TEXT)
            return
        start_addcompany(chat_id)
        return

    if cmd == "login":
        handle_login(chat_id, parts)
        return

    # Interaktiv ro'yxatdan o'tkazish / login davom etmoqda?
    pending = reg.current(chat_id)
    entry = driver_entry.current(chat_id)
    if entry and entry.get("step") not in (None, "done"):
        continue_driver_entry(chat_id, text)
        return
    if pending and pending.get("step") not in (None, "done"):
        if pending.get("flow") == "login":
            continue_login(chat_id, text)
        else:
            continue_addcompany(chat_id, text)
        return

    # Rejalashtirish dialogi davom etmoqda?
    flow = planning.flow_current(chat_id)
    if flow and flow.get("step") not in (None, "done"):
        reply(chat_id, planning.flow_input(chat_id, text),
              reply_markup=kb.plan_cancel_kb())
        return

    # Tanib bo'lmagan matn — AI-yordamchi tushunishga harakat qiladi.
    out = assistant.assist(text, f, chat_id=chat_id)
    if out is None:
        # OpenRouter sozlangan bo'lsa — erkin savolga LLM javob beradi.
        out = assistant.assist_general(text, f, chat_id=chat_id)
    if out:
        reply(chat_id, *out)
        return

    # Hech narsa taninmasa — MyAI ko'p agentli tizim sinab ko'radi.
    if _chat_mode_active(chat_id):
        if text.strip().lower() in _CHAT_MODE_EXIT:
            _chat_mode_off(chat_id)
            reply(chat_id,
                  "⏹ <b>Suhbat rejimi o'chirildi.</b>\n"
                  "Qayta yoqish uchun <b>🧠 suhbat</b> tugmasini bosing.")
            return
        start_myai(chat_id, text, f)
        return

    start_myai(chat_id, text, f)
    return


def handle_callback(chat_id: int, cq: dict, data: str) -> None:
    role = resolve_role(chat_id)
    f = context.filters_for(chat_id)

    if data.startswith("pr:"):
        route_id = data[3:].strip()
        result = context.profile_for_route(route_id)
        if not result:
            answer(cq, "Yo'nalish topilmadi")
            return
        name, rid = result
        if not context.can_view(chat_id, name):
            answer(cq, "Siz bu kompaniyani ko'ra olmaysiz")
            reply(chat_id, DENIED_TEXT)
            return
        try:
            context.set_active(chat_id, name, route=rid)
        except ValueError as exc:
            answer(cq, str(exc))
            return
        answer(cq, f"{context.short_name(name)} tanlandi")
        reply(chat_id, *render.today(context.filters_for(chat_id)))
        return

    if data.startswith("prof:"):
        name = data[5:]
        if name == "__all_routes__":
            context.clear_route(chat_id)
            name = context.get_active(chat_id)
            answer(cq, "Barcha yo'nalishlar ko'rsatiladi")
            reply(chat_id, *render.today(context.filters_for(chat_id)))
            return
        if name in ("", "__all__"):
            context.clear(chat_id)
            answer(cq, "Hamma kompaniyalar ko'rsatiladi")
            reply(chat_id, *render.profiles(context.filters_for(chat_id), chat_id=chat_id))
            return
        if not context.can_view(chat_id, name):
            answer(cq, "Siz bu kompaniyani ko'ra olmaysiz")
            reply(chat_id, DENIED_TEXT)
            return
        try:
            context.set_active(chat_id, name)
        except ValueError as exc:
            answer(cq, str(exc))
            return
        answer(cq, f"{context.short_name(name)} tanlandi")
        reply(chat_id, *render.today(context.filters_for(chat_id)))
        return

    if data.startswith("nav:"):
        target = data[4:]
        if target == "sync":
            if not can(role, "sync"):
                answer(cq, "Huquq yo'q")
                reply(chat_id, DENIED_TEXT)
                return
            answer(cq, "Sinxronlash")
            reply(chat_id, "🔄 Sinxronlash bosilsinmi?",
                  reply_markup=kb.sync_confirm_kb())
            return
        if target == "profiles":
            answer(cq)
            reply(chat_id, *render.profiles(f, chat_id=chat_id))
            return
        if target == "settings":
            answer(cq)
            reply(chat_id, render.settings_text(chat_id),
                  reply_markup=kb.settings_kb(chat_id))
            return
        if target == "ai":
            answer(cq, "Tahlil tayyorlanmoqda...")
            reply(chat_id, assistant.analyze(f))
            return
        if target == "plan":
            if not can(role, "plan"):
                answer(cq, "Huquq yo'q")
                reply(chat_id, DENIED_TEXT)
                return
            answer(cq)
            reply(chat_id, planning.plan_text(), reply_markup=kb.plan_kb())
            return
        handlers = {
            "dashboard": render.today,
            "month": render.month,
            "vehicles": render.vehicles,
            "drivers": render.drivers,
            "routes": render.routes,
            "trips": render.trips,
            "problems": render.problems,
            "reports": render.reports,
            "insights": _insights_render,
        }
        answer(cq)
        handler = handlers.get(target)
        if not handler:
            reply(chat_id, "⚠️ Noma'lum bo'lim.")
            return
        reply(chat_id, *handler(f))
        return

    if data == "settings:kmrate":
        if role not in (Role.ADMIN, Role.DISPATCHER, Role.MANAGER):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        bot_settings.set_pending(chat_id, "km_rate")
        answer(cq, "Boshlanadi")
        reply(chat_id,
              "💵 <b>1 KM NARXI</b>\n\n"
              "Yangi 1 km narxini so'mda yozing (masalan <code>2000</code>).\n"
              "Asl holatga qaytarish uchun <code>0</code> yuboring.",
              reply_markup=kb.back_kb("nav:settings", "❌ Bekor qilish"))
        return
    if data == "settings:lang":
        answer(cq)
        reply(chat_id,
              "🌐 <b>TIL TANLASH</b>\n\nTilingizni tanlang:",
              reply_markup=kb.lang_kb(bot_settings.lang(chat_id)))
        return
    if data.startswith("setlang:"):
        bot_settings.set_lang(chat_id, data[8:])
        answer(cq, "Til saqlandi")
        reply(chat_id, render.settings_text(chat_id),
              reply_markup=kb.settings_kb(chat_id))
        return

    if data.startswith("prob:"):
        key = data[5:]
        if key not in render.PROBLEM_LABELS:
            answer(cq, "Kategoriya topilmadi")
            return
        answer(cq, "Batafsil")
        reply(chat_id, *render.problem_category(key, f))
        return

    if data == "sync:confirm":
        if not can(role, "sync"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        answer(cq, "Boshlandi")
        start_sync(chat_id)
        return
    if data == "sync:cancel":
        answer(cq, "Bekor qilindi")
        reply(chat_id, "Sinxronlash bekor qilindi.")
        return
    if data == "sync:monthly_confirm":
        if not can(role, "sync"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        from datetime import date as _dt_date
        today = _dt_date.today()
        first = today.replace(day=1)
        answer(cq, "Oylik sync boshlandi")
        start_monthly_sync(chat_id, first.isoformat(), today.isoformat())
        return
    if data == "sync:monthly_cancel":
        answer(cq, "Bekor qilindi")
        reply(chat_id, "Oylik sinxronlash bekor qilindi.")
        return

    if data == "verify:cancel":
        answer(cq, "Bekor qilindi")
        return
    if data.startswith("verify:"):
        month = data[7:] or None
        if not re.match(r"^\d{4}-\d{2}$", month or ""):
            month = None
        answer(cq, "Tekshirish boshlandi")
        start_verify(chat_id, month)
        return

    if data.startswith("dlog:") or data.startswith("dfine:"):
        if not can(role, "driver_edit"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        flow = "log" if data.startswith("dlog:") else "fine"
        answer(cq, "Boshlanadi")
        start_driver_entry(chat_id, flow, data.split(":", 1)[1])
        return

    if data == "dentry:cancel":
        driver_entry.cancel(chat_id)
        answer(cq, "Bekor qilindi")
        return

    if data == "plan:add":
        if not can(role, "plan"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        answer(cq, "Haydovchi ismini yozing")
        reply(chat_id, planning.flow_start(chat_id),
              reply_markup=kb.plan_cancel_kb())
        return
    if data == "plan:clear":
        if not can(role, "plan"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        n = planning.clear()
        answer(cq, "Tozalandi")
        reply(chat_id, f"🗑 Ertangi reja tozalandi ({n} ta yozuv).",
              reply_markup=kb.plan_kb())
        return
    if data == "plan:cancel":
        planning.flow_cancel(chat_id)
        answer(cq, "Bekor qilindi")
        reply(chat_id, planning.plan_text(), reply_markup=kb.plan_kb())
        return

    if data.startswith("d:"):
        answer(cq)
        reply(chat_id, *render.driver_card(data[2:], f))
        return

    if data.startswith("v:"):
        answer(cq)
        reply(chat_id, *render.vehicle_card(data[2:], f))
        return

    if data.startswith("top:"):
        answer(cq)
        reply(chat_id, *render.leaderboard(f, metric=data[4:]))
        return

    if data.startswith("dl:"):
        if not can(role, "export"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        parts = data[3:].split(":")
        fmt = parts[0] if parts and parts[0] in _FORMATS else "xlsx"
        scope = parts[1] if len(parts) > 1 and parts[1] in _SCOPES else "all"
        answer(cq, "Yuklab olinmoqda...")
        start_export(chat_id, fmt, scope)
        return

    if data.startswith("sal:"):
        if not can(role, "salary"):
            answer(cq, "Huquq yo'q")
            reply(chat_id, DENIED_TEXT)
            return
        payload = data[4:]
        if payload == "custom":
            answer(cq, "Sanani kiriting")
            bot_settings.set_pending(chat_id, "salary_date")
            reply(chat_id,
                  "📅 <b>SANA ORALIG'INI KIRITING</b>\n\n"
                  "Format: <code>YYYY-MM-DD - YYYY-MM-DD</code>\n"
                  "Masalan: <code>2026-08-01 - 2026-08-15</code>",
                  reply_markup=kb.back_kb("nav:settings", "❌ Bekor qilish"))
            return
        parts = payload.split(":")
        if len(parts) == 2:
            from_date, to_date = parts[0], parts[1]
            answer(cq, "Yuklab olinmoqda...")
            _start_salary_dl(chat_id, from_date, to_date)
            return
        answer(cq, "Noto'g'ri format")
        return

    if data.startswith("exp:"):
        legacy.handle_export_callback(chat_id, cq, data[4:])
        return

    if data.startswith("rs:"):
        if not can(role, "resend"):
            answer(cq, "Huquq yo'q")
            return
        name = data[3:]
        if name == "ALL":
            name = None
            # ADMIN barcha profillarni; boshqalar faqat o'z yo'nalish(lar)ini
            if role is not Role.ADMIN:
                assigned = context.assigned_profile_names(chat_id)
                if not assigned:
                    answer(cq, "Yo'nalish biriktirilmagan")
                    return
                rids = context.assigned_route_ids(chat_id)
                answer(cq, "Boshlandi")
                legacy.start_resend(chat_id, assigned, routes=rids or None)
                return
        answer(cq, "Boshlandi")
        legacy.start_resend(chat_id, name)
        return

    answer(cq)


def handle_document(chat_id: int, doc: dict) -> None:
    role = resolve_role(chat_id)
    if not can(role, "document"):
        log.warning("Ruxsatsiz chat document yubordi: %s (%s)", chat_id, role.value)
        reply(chat_id, DENIED_TEXT)
        return
    filename = (doc.get("file_name") or "").strip()
    if not filename.lower().endswith(".xlsx"):
        reply(chat_id, "Faqat .xlsx fayl qabul qilinadi (sayt export formati).")
        return
    file_id = doc.get("file_id")
    if not file_id:
        reply(chat_id, "Fayl ID olinmadi.")
        return
    reply(chat_id, "Fayl qabul qilindi, ishlanmoqda...")
    threading.Thread(target=legacy.process_document,
                     args=(chat_id, file_id, filename), daemon=True).start()
