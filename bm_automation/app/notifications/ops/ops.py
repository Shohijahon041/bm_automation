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
from ...core.state import acquire_lock, release_lock
from ..telegram import telegram_call, send_message
from . import dispatch
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


def _handle_update(upd: dict) -> None:
    chat_id = _update_chat_id(upd)
    if chat_id is None:
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

    # Yangi foydalanuvchi — adminlarga ogohlantirish
    if is_new:
        _notify_new_user(chat_id)

    # Qat'iy allowlist: faqat dasturga kiritilgan chat'lar uchun ishlaydi.
    if not is_allowed(chat_id):
        _deny(upd, chat_id)
        return

    if "message" in upd or "edited_message" in upd:
        msg = upd.get("message") or upd.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        try:
            if msg.get("document"):
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
