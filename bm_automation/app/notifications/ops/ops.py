"""Transport Operations Bot — asosiy tsikl va entrypoint.

Uzoq poll (getUpdates) orqali ishlaydi. Windows Task Scheduler har 5 daqiqada
ishga tushirib turadi; PID fayl bo'lsa o'zi chiqib ketadi — bir vaqtda bitta
nusxa ishlaydi.

Buyruqlar va inline tugmalar `dispatch` da; matnlar `render` da.
"""

from __future__ import annotations

import sys
import time

from ...config.settings import telegram_settings
from ...core.state import acquire_lock, release_lock
from ..telegram import telegram_call
from . import dispatch

START_TIME = time.time()


def _handle_update(upd: dict) -> None:
    if "message" in upd:
        msg = upd["message"]
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is None:
            return
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
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        if chat_id is None:
            return
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


def main(argv: list | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="bm_automation bot")
    parser.add_argument("--once", action="store_true",
                        help="Bitta getUpdates tsikli (sinash uchun)")
    parser.add_argument("--log", default="",
                        help="stdout/stderr yo'naltiriladigan log fayl")
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
        poll_forever()
    finally:
        release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
