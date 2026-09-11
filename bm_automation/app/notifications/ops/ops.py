"""Transport Operations Bot — asosiy tsikl va entrypoint.

Uzoq poll (getUpdates) orqali ishlaydi. --watchdog rejimida avtomatik
qayta ishga tushiriladi: crash bo'lsa 5→10→20→... soniya kutib qayta
boshlaydi. Bir vaqtda bitta nusxa ishlaydi (PID lock).

Buyruqlar va inline tugmalar `dispatch` da; matnlar `render` da.
"""

from __future__ import annotations

import sys
import time

from ...config.settings import telegram_settings
from ...core.bot_users import record_user, record_activity
from ...core.state import acquire_lock, release_lock
from ..telegram import telegram_call
from . import dispatch
from .roles import is_allowed, resolve_role


START_TIME = time.time()


def _update_chat_id(upd: dict) -> int | None:
    """Update'dan chat ID (message yoki callback_query)."""
    if "message" in upd:
        return (upd["message"].get("chat") or {}).get("id")
    if "callback_query" in upd:
        return ((upd["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    return None


def _update_user_info(upd: dict) -> dict:
    """Update'dan foydalanuvchi ma'lumotlarini oladi (from qismidan)."""
    sender = None
    if "message" in upd:
        sender = upd["message"].get("from") or {}
    elif "callback_query" in upd:
        sender = upd["callback_query"].get("from") or {}
    if not sender:
        return {}
    return {
        "first_name": sender.get("first_name", ""),
        "username": sender.get("username", ""),
    }


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


def _handle_update(upd: dict) -> None:
    chat_id = _update_chat_id(upd)
    if chat_id is None:
        return
    # Har qanday foydalanuvchini qayd qilamiz (ruxsatsiz ham)
    uinfo = _update_user_info(upd)
    role = resolve_role(chat_id)
    try:
        record_user(chat_id, first_name=uinfo.get("first_name", ""),
                    username=uinfo.get("username", ""),
                    role=role.value)
    except Exception:
        pass

    # Harakatni qayd qilamiz
    action = ""
    detail = ""
    if "message" in upd:
        msg = upd["message"]
        text = (msg.get("text") or "").strip()
        if msg.get("document"):
            action = "document"
            detail = (msg["document"].get("file_name") or "fayl")
        elif text:
            action = "message"
            detail = text[:200]
    elif "callback_query" in upd:
        action = "callback"
        detail = (upd["callback_query"].get("data") or "")[:200]
    if action:
        try:
            record_activity(chat_id, action, detail)
        except Exception:
            pass

    # Qat'iy allowlist: faqat dasturga kiritilgan chat'lar uchun ishlaydi.
    if not is_allowed(chat_id):
        _deny(upd, chat_id)
        return

    if "message" in upd:
        msg = upd["message"]
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
    offset = None
    print("Bot ishga tushdi. getUpdates kuzatilmoqda...")
    while True:
        payload = {"timeout": 50, "allowed_updates": []}
        if offset:
            payload["offset"] = offset
        try:
            updates = telegram_call("getUpdates", payload) or []
        except Exception as exc:
            print(f"getUpdates xatosi: {exc}")
            time.sleep(5)
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
        print(f"[watchdog] Bot ishga tushirilmoqda...")
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
