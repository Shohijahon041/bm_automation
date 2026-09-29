"""Transport Operations Bot — asosiy tsikl va entrypoint.

Uzoq poll (getUpdates) orqali ishlaydi. --watchdog rejimida avtomatik
qayta ishga tushiriladi: crash bo'lsa 5→10→20→... soniya kutib qayta
boshlaydi. Bir vaqtda bitta nusxa ishlaydi (PID lock).

Buyruqlar va inline tugmalar `dispatch` da; matnlar `render` da.
Kuzatuv: har qanday update turi (xabar, callback, tahrirlangan xabar,
kanal posti, bot holati) `bot_users.json`ga yoziladi; yangi foydalanuvchi
haqida adminlar Telegram orqali ogohlantiriladi.
"""

from __future__ import annotations

import sys
import time

from ...config.settings import telegram_settings
from ...core.bot_users import record_user, record_activity, get_user
from ...core.group_stats import record_group_message
from ...core.state import acquire_lock, release_lock
from ..telegram import telegram_call, send_message
from . import context, dispatch
from .roles import is_allowed, resolve_role, configured_roles, Role
from .text import PRIVATE_TEXT

START_TIME = time.time()
_CONFLICT_BACKOFF = 5


def _update_chat_id(upd: dict) -> int | None:
    """Update'dan chat ID (barcha turlar: xabar, callback, kanal, holat)."""
    for key in ("message", "edited_message", "channel_post", "my_chat_member"):
        obj = upd.get(key)
        if isinstance(obj, dict):
            cid = (obj.get("chat") or {}).get("id")
            if cid is not None:
                return cid
    if "callback_query" in upd:
        return ((upd["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    return None


def _is_group(upd: dict) -> bool:
    """Update guruh/superguruh chat'dan kelganmi?"""
    for key in ("message", "edited_message", "channel_post"):
        obj = upd.get(key)
        if isinstance(obj, dict):
            ctype = str((obj.get("chat") or {}).get("type") or "")
            if ctype in ("group", "supergroup"):
                return True
    if "callback_query" in upd:
        ctype = str((((upd["callback_query"].get("message") or {})
                      .get("chat")) or {}).get("type") or "")
        if ctype in ("group", "supergroup"):
            return True
    return False


def _bot_mentioned(upd: dict) -> bool:
    """Xabarda bot eslatilganmi (@username) yoki botga javobmi?"""
    me = _bot_username_cached()
    msg = (upd.get("message") or upd.get("edited_message") or {})
    text = str(msg.get("text") or "")
    if me and f"@{me}" in text:
        return True
    # Botga javob (reply)
    reply_msg = msg.get("reply_to_message") or {}
    if reply_msg:
        reply_from = reply_msg.get("from") or {}
        if reply_from.get("is_bot"):
            return True
    return False


# /getMe natijasi keshi (guruh rejimida @username tekshiruvi uchun).
_BOT_USERNAME: dict[str, str] = {}


def _bot_username_cached() -> str:
    """Bot @username (bir marta so'raladi, keyin keshda turadi)."""
    if _BOT_USERNAME.get("username"):
        return _BOT_USERNAME["username"]
    try:
        info = telegram_call("getMe") or {}
        me = str(info.get("username") or "")
        _BOT_USERNAME["username"] = me
        return me
    except Exception:  # noqa: BLE001 - username olinmasa jim o'tkazamiz
        return ""


def _auto_agents_enabled() -> bool:
    """Avtomatik agentlar (planli hisobot/tahlil xabarlar) yoqilganmi?

    `AI_AUTO_AGENTS=on` bo'lsagina poll-tsikl agentlarni o'z jadvalida
    chaqiradi. Aks holda ular jim turadi va faqat foydalanuvchi buyruq
    yozganda javob beradi.
    """
    try:
        s = telegram_settings()
        return str(s.get("auto_agents", "off")).lower() not in ("off", "0", "false")
    except Exception:
        return False


def _update_user_info(upd: dict) -> dict:
    """Update ichidan foydalanuvchi ma'lumoti (message/callback/kanal/bot)."""
    for key in ("message", "edited_message", "channel_post",
                "callback_query", "my_chat_member"):
        obj = upd.get(key)
        if not isinstance(obj, dict):
            continue
        frm = obj.get("from") or {}
        if isinstance(frm, dict) and frm.get("id"):
            return {
                "first_name": str(frm.get("first_name") or ""),
                "username": str(frm.get("username") or ""),
            }
    return {}


def _update_sender(upd: dict) -> dict:
    """Update yuboruvchisi (id + ism + username), guruh uchun jaddektor.

    Guruh xabarida `from` — aniq foydalanuvchi (guruh chat emas). Rol
    shu `from.id` bo'yicha aniqlanadi — guruh chat_id si roli emas.
    """
    for key in ("message", "edited_message", "callback_query"):
        obj = upd.get(key)
        if not isinstance(obj, dict):
            continue
        frm = obj.get("from") or {}
        if isinstance(frm, dict) and frm.get("id"):
            first = str(frm.get("first_name") or "")
            last = str(frm.get("last_name") or "")
            return {
                "id": int(frm["id"]),
                "name": (first + (" " + last if last else "")).strip(),
                "username": str(frm.get("username") or ""),
            }
    return {}


def _deny(upd: dict, chat_id: int) -> None:
    """Ruxsatsiz chat uchun yagona rad javobi (callback — toast, aks holda xabar)."""
    if "callback_query" in upd:
        try:
            telegram_call("answerCallbackQuery", {
                "callback_query_id": upd["callback_query"]["id"],
                "text": "⛔ Ruxsat yo'q",
                "show_alert": True,
            })
            return
        except Exception:
            pass
    try:
        telegram_call("sendMessage", {
            "chat_id": chat_id, "text": PRIVATE_TEXT, "parse_mode": "HTML"})
    except Exception:
        pass


def _classify(upd: dict) -> tuple[str, str]:
    """Update turini (action, detail) juftligiga aylantiradi."""
    if "message" in upd:
        msg = upd["message"]
        text = (msg.get("text") or "").strip()
        if msg.get("document"):
            return "document", (msg["document"].get("file_name") or "fayl")
        if msg.get("photo"):
            return "photo", f"{len(msg.get('photo') or [])} o'lcham"
        if text:
            return "message", text[:200]
        return "message", "(media/kapital xabar)"
    if "edited_message" in upd:
        return "edited", ((upd["edited_message"].get("text") or "")[:200])
    if "channel_post" in upd:
        return "channel_post", ((upd["channel_post"].get("text") or "")[:200])
    if "callback_query" in upd:
        return "callback", (upd["callback_query"].get("data") or "")[:200]
    if "my_chat_member" in upd:
        mcm = upd["my_chat_member"]
        old = ((mcm.get("old_chat_member") or {}).get("status") or "?")
        new = ((mcm.get("new_chat_member") or {}).get("status") or "?")
        return "bot_status", f"{old} -> {new}"
    return "", ""


def _notify_new_user(chat_id: int) -> None:
    """Yangi foydalanuvchi haqida adminlarga Telegram ogohlantirish."""
    try:
        u = get_user(chat_id) or {}
        admins = [cid for cid, r in configured_roles().items()
                  if r == Role.ADMIN]
        lines = [
            "🆕 <b>Yangi foydalanuvchi botga yozildi</b>",
            f"ID: <code>{chat_id}</code>",
            f"Ism: {u.get('first_name') or '-'}",
            f"Username: @{u.get('username') or '-'}",
            f"Vaqt: {u.get('first_seen', '')}",
        ]
        text = "\n".join(lines)
        for cid in admins:
            if cid == chat_id:
                continue
            try:
                send_message(text, chat_id=str(cid), parse_mode="HTML")
            except Exception:
                pass
    except Exception:
        pass


def _handle_group_update(upd: dict, chat_id: int) -> None:
    """Guruh chat'idan kelgan update'ni qisqa javob bilan ishlaydi.

    Qoidalar:
      • Har qanday guruh xabari statistika fayliga yoziladi
        (`group_stats.json` — admin panel `/api/group-stats` orqali ko'radi).
      • Bot faqat ADMIN / DISPATCHER / MANAGER so'rovlariga javob beradi —
        oddiy a'zolar buyrug'i butunlay e'tiborsiz qoldiriladi.
      • `/today` va `/grafik` buyruqlariga to'liq karta o'rniga qisqa
        matn qaytariladi (guruh spamini oldini olish uchun).
      • `/chiqish[_on|_off]` — shu guruh uchun chiqish (reys/obed)
        eslatmasini yoqadi/o'chiradi/holatini ko'rsatadi.
      • Qolgan buyruqlar guruhda ishlamaydi (jim).
    """
    msg = upd.get("message") or upd.get("edited_message") or \
        (upd.get("callback_query") or {}).get("message") or {}
    text = str(msg.get("text") or "").strip()
    is_command = text.startswith("/")

    # Statistika (HAMMA guruh xabarlari uchun — rol/mention cheklovidan
    # oldin, aks holda panel faqat botga murojaatlarni sanaydi).
    sender = _update_sender(upd)
    get_group_title = ((msg.get("chat") or {}).get("title") or "")
    try:
        record_group_message(chat_id, get_group_title, sender,
                             is_command, detail=text[:200])
    except Exception as exc:  # noqa: BLE001
        print(f"Guruh statistika xatosi: {exc}")

    if not (is_command or _bot_mentioned(upd)):
        return  # guruhda jim turamiz

    # Faqat ADMIN / DISPATCHER / MANAGER javob oladi.
    sender_id = (sender or {}).get("id")
    if sender_id is None:
        return
    srole = resolve_role(sender_id)
    if srole not in (Role.ADMIN, Role.DISPATCHER, Role.MANAGER):
        return  # oddiy a'zolar javobsiz qoldiriladi

    # Guruhda faqat qisqa javoblar (to'liq karta/Excel/PNG spam emas).
    cmd = (text.split()[0].lower().replace("/", "", 1).split("@")[0]
           if is_command else "")

    if cmd == "today":
        try:
            from . import render as _render
            f = context.filters_for(sender_id)
            send_message(_render.today_short(f, chat_id=sender_id),
                         chat_id=chat_id, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            print(f"Guruh /today xatosi: {exc}")
        return

    if cmd in ("grafik", "grafik_today", "grafik_tomorrow", "grafik_yesterday"):
        send_message("🖼 <b>GRAFIK</b>\n\n"
                     "Guruhda grafik karta/Excel yuborilmaydi. "
                     "Shaxsiy chatda /grafik yoki dashboard'dan "
                     "yoqishingiz mumkin.",
                     chat_id=chat_id, parse_mode="HTML")
        return

    if cmd in ("chiqish_on", "chiqish_off", "chiqish"):
        from . import group_departures
        try:
            if cmd == "chiqish_on":
                group_departures.set_chat_reminders(chat_id, True)
                send_message("🔔 <b>Chiqish (reys/obed) eslatmalari"
                             " yoqildi.</b>\nBu guruhga chiqish va "
                             "obed vaqtlari keladi.",
                             chat_id=chat_id, parse_mode="HTML")
            elif cmd == "chiqish_off":
                group_departures.set_chat_reminders(chat_id, False)
                send_message("🔕 <b>Chiqish (reys/obed) eslatmalari"
                             " o'chirildi.</b>",
                             chat_id=chat_id, parse_mode="HTML")
            else:
                holat = ("YOQILGAN" if
                         group_departures.chat_enabled(chat_id)
                         else "O'CHIRILGAN")
                send_message(f"🔔 <b>Chiqish (reys/obed) eslatmalari:"
                             f" {holat}</b>\n\n"
                             "/chiqish_on — yoqish,\n"
                             "/chiqish_off — o'chirish.",
                             chat_id=chat_id, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            print(f"Guruh /chiqish xatosi: {exc}")
        return

    if _bot_mentioned(upd) and not is_command:
        send_message("ℹ️ Guruhda faqat admin/dispetcher so'rovlariga "
                     "javob beraman. To'liq hisobot shaxsiy chatda "
                     "mavjud.",
                     chat_id=chat_id, parse_mode="HTML")
        return

    # Qolgan buyruqlar guruhda — jim.


def _handle_update(upd: dict) -> None:
    chat_id = _update_chat_id(upd)
    if chat_id is None:
        return

    # ---- Guruh rejimi ----
    # Guruh chat'larida bot faqat "chaqirilganda" javob beradi:
    #   • /buyruq (slash bilan boshlanadi)
    #   • @bot_username eslatmasi
    #   • bot xabariga javob (reply)
    # Javoblar faqat ADMIN/DISPATCHER/MANAGER uchun, yana qisqa matn.
    # Qolgan hollarda — butunlay jim (update e'tiborga olinmaydi).
    if _is_group(upd):
        _handle_group_update(upd, chat_id)
        return

    # Har qanday foydalanuvchini qayd qilamiz (ruxsatsiz ham)
    uinfo = _update_user_info(upd)
    role = resolve_role(chat_id)
    is_new = False
    try:
        is_new = get_user(chat_id) is None
        record_user(chat_id, first_name=uinfo.get("first_name", ""),
                    username=uinfo.get("username", ""),
                    role=role.value)
    except Exception:
        pass

    # Harakatni qayd qilamiz (barcha update turlari)
    action, detail = _classify(upd)
    if action:
        try:
            record_activity(chat_id, action, detail)
        except Exception:
            pass

    # Yangi foydalanuvchi — adminlarga ogohlantirish (faqat shaxsiy chat —
    # guruh update'lar yuqorida erta qaytarilgan, shuning uchun guard kerak emas).
    # Kanal postlari, bot holati (my_chat_member) va closedchat kabi texnik
    # update'lar "nomsiz" (first_name/username bo'sh) — ular haqida xabar
    # yuborilmaydi, aks holda doimiy spam bo'lib ko'rinadi.
    if is_new and (uinfo.get("first_name") or uinfo.get("username")):
        _notify_new_user(chat_id)

    # Qat'iy allowlist: faqat dasturga kiritilgan chat'lar uchun ishlaydi.
    if not is_allowed(chat_id):
        _deny(upd, chat_id)
        return

    if "message" in upd or "edited_message" in upd:
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        try:
            if msg.get("contact") and not text:
                dispatch.handle_contact(chat_id, msg["contact"])
            elif msg.get("document"):
                dispatch.handle_document(chat_id, msg["document"])
            elif text:
                dispatch.handle_message(chat_id, text)
        except Exception as exc:
            print(f"Xabar ishlovida xato: {exc}")
    elif "callback_query" in upd:
        cq = upd["callback_query"]
        data = (cq.get("data") or "")
        try:
            dispatch.handle_callback(chat_id, cq, data)
        except Exception as exc:
            print(f"Callback ishlovida xato: {exc}")


def poll_forever() -> None:
    global _CONFLICT_BACKOFF
    offset = None
    print("Bot ishga tushdi. getUpdates kuzatilmoqda...")
    while True:
        payload = {"timeout": 50, "allowed_updates": []}
        if offset:
            payload["offset"] = offset
        try:
            updates = telegram_call("getUpdates", payload) or []
        except Exception as exc:
            msg = str(exc)
            # Konflikt: boshqa mashinada xuddi shu token bilan bot polling
            # qilmoqda. Tez-tez qayta urinish foyda bermaydi — sekin backoff.
            if "Conflict" in msg or "409" in msg:
                _CONFLICT_BACKOFF = min(_CONFLICT_BACKOFF * 2, 120)
            else:
                _CONFLICT_BACKOFF = 5
            print(f"getUpdates xatosi ({_CONFLICT_BACKOFF}s kutish): {exc}")
            time.sleep(_CONFLICT_BACKOFF)
            continue
        for upd in updates:
            offset = upd.get("update_id", 0) + 1
            try:
                _handle_update(upd)
            except Exception as exc:
                print(f"Update ishlovida xato: {exc}")
        # Avtomatik agentlar — AI_AUTO_AGENTS=on bo'lsagina ishlaydi.
        # Standart "off": bot jim, faqat foydalanuvchi buyruq yozganda javob
        # beradi (masalan: /daily, /insights, /today, /grafik).
        if _auto_agents_enabled():
            try:
                from . import problem_alerts
                problem_alerts.check_and_notify()
            except Exception as exc:
                print(f"Muammo alert xatosi: {exc}")
            try:
                from . import doc_expiry
                doc_expiry.check_and_send()
            except Exception as exc:
                print(f"Hujjat muddati xatosi: {exc}")
            try:
                from . import monthly_results
                monthly_results.check_and_send()
            except Exception as exc:
                print(f"Oylik natijalar xatosi: {exc}")
            try:
                from . import fines_report
                fines_report.check_and_send()
            except Exception as exc:
                print(f"Jarima hisoboti xatosi: {exc}")
            try:
                from . import daily_summary
                daily_summary.check_and_send()
            except Exception as exc:
                print(f"Ertalabki xulosa xatosi: {exc}")
            try:
                from . import self_review
                self_review.check_and_send()
            except Exception as exc:
                print(f"AI o'z-o'zini rivojlantirish xatosi: {exc}")
            try:
                from . import grafik_sms
                grafik_sms.check_and_send()
            except Exception as exc:
                print(f"Grafik kuzatuvi xatosi: {exc}")
            try:
                from ..sms_notify import retry_stale_pending
                res = retry_stale_pending()
                if res.get("polled"):
                    print(f"Stale PENDING SMS: {res}")
            except Exception as exc:
                print(f"Stale PENDING tekshiruvi xatosi: {exc}")
        # Gazeta chiqish/obed eslatmasi AI_AUTO_AGENTS'ga bog'liq emas —
        # guruhlarda /chiqish_on|off bilan boshqariladi (har 40s ichki
        # siklda, o'z holat fayli orqali).
        try:
            from . import group_departures
            group_departures.check_and_send()
        except Exception as exc:
            print(f"Chiqish vaqti eslatmasi xatosi: {exc}")
        try:
            from ...utils import cleanup
            msg = cleanup.run_once()
            if msg:
                print(msg)
        except Exception as exc:
            print(f"Tozalash xatosi: {exc}")


def poll_forever_reliable() -> None:
    """Watchdog rejimi — crash bo'lsa avtomatik qayta ishga tushiradi."""
    import random
    _BASE_DELAY = 5
    _MAX_DELAY = 120
    _JITTER = 3
    delay = _BASE_DELAY
    while True:
        print("[watchdog] Bot ishga tushirilmoqda...")
        try:
            poll_forever()
            print("[watchdog] Bot to'xtadi (normal). Qayta ishga tushiriladi.")
            delay = _BASE_DELAY
        except KeyboardInterrupt:
            print("[watchdog] Foydalanuvchi to'xtatdi.")
            return
        except Exception as exc:
            print(f"[watchdog] Bot xato bilan to'xtadi: {exc}")
            print(f"[watchdog] {delay} soniyadan keyin qayta ishga tushiriladi...")
            time.sleep(delay + random.uniform(0, _JITTER))
            delay = min(delay * 2, _MAX_DELAY)


def main(argv: list | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="bm_automation bot")
    parser.add_argument("--once", action="store_true",
                        help="Bitta getUpdates tsikli (sinash uchun)")
    parser.add_argument("--log", default="",
                        help="stdout/stderr yo'naltiriladigan log fayl")
    parser.add_argument("--watchdog", action="store_true",
                        help="Avtomatik qayta ishga tushirish (crash → restart)")
    args = parser.parse_args(argv)

    if args.log:
        try:
            logf = open(args.log, "a", encoding="utf-8", buffering=1)
        except Exception as exc:
            print(f"Log fayl ochilmadi: {exc}", file=sys.stderr)
            return 1
        sys.stdout = logf
        sys.stderr = logf

    if not telegram_settings()["token"]:
        print("TG_BOT_TOKEN .env'da ko'rsatilmagan", file=sys.stderr)
        return 1

    if not acquire_lock():
        print("Boshqa bot nusxasi ishlayapti, chiqilmoqda.")
        return 0
    try:
        if args.once:
            updates = telegram_call("getUpdates", {"timeout": 5, "offset": None,
                                                    "allowed_updates": []}) or []
            for upd in updates:
                _handle_update(upd)
            print(f"{len(updates)} update ko'rib chiqildi.")
            return 0
        if args.watchdog:
            poll_forever_reliable()
        else:
            poll_forever()
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
