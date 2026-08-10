"""Telegram bildirishnoma (bot orqali xabar, rasm, fayl va tugmalar).

`notify.py` ning yangi manzili. Telegram'ga bevosita API chaqiriqlar,
klaviaturalar va hisobot natijasini jo'natish shu yerda.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from ..config.settings import telegram_settings

API = "https://api.telegram.org/bot{token}/{method}"


def telegram_call(method: str, payload: dict | None = None, files=None):
    """Telegram Bot API'ga bevosita qo'ng'iroq. result ni qaytaradi."""
    s = telegram_settings()
    if not s["token"]:
        raise ValueError("TG_BOT_TOKEN .env'da ko'rsatilmagan")
    url = API.format(token=s["token"], method=method)
    if files:
        body = dict(payload or {})
        if "reply_markup" in body:
            body["reply_markup"] = json.dumps(body["reply_markup"])
        resp = requests.post(url, data=body, files=files, timeout=120)
    else:
        resp = requests.post(url, json=payload or {}, timeout=120)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram xatosi: {data.get('description')}")
    return data.get("result")


def _configured() -> bool:
    s = telegram_settings()
    return bool(s["token"] and s["chat_id"])


def resend_keyboard(profile=None):
    """'Qayta yuborish' tugmasi (inline keyboard)."""
    data = f"rs:{profile or 'ALL'}"
    return {
        "inline_keyboard": [
            [{"text": "Qayta yuborish", "callback_data": data[:64]}]
        ]
    }


def export_keyboard(key: str):
    """'Yuborish' tugmasi — tayyor export faylini bot orqali yuborish."""
    return {
        "inline_keyboard": [
            [{"text": "Yuborish", "callback_data": f"exp:{key}"}]
        ]
    }


def command_keyboard():
    """Doimiy tugmalar klaviaturasi (reply keyboard)."""
    return {
        "keyboard": [
            [{"text": "📊 Status"}, {"text": "📁 Fayllar"}],
            [{"text": "🔄 Qayta yuborish"}, {"text": "📤 Export"}],
            [{"text": "❓ Yordam"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def send_message(text: str, chat_id: str | None = None, reply_markup=None) -> bool:
    """Bot orqali matnli xabar yuboradi."""
    s = telegram_settings()
    if not s["token"] or not (chat_id or s["chat_id"]):
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    payload = {"chat_id": chat_id or s["chat_id"], "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_call("sendMessage", payload)
    return True


def send_document(
    file_path: str,
    caption: str = "",
    chat_id: str | None = None,
    reply_markup=None,
) -> bool:
    """Telegram'ga fayl (Excel/hisobot) yuboradi."""
    s = telegram_settings()
    if not s["token"] or not (chat_id or s["chat_id"]):
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    payload = {"chat_id": chat_id or s["chat_id"], "caption": caption}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with open(file_path, "rb") as fh:
        telegram_call(
            "sendDocument",
            payload,
            files={"document": (os.path.basename(file_path), fh)},
        )
    return True


def send_bytes(
    filename: str,
    data: bytes,
    caption: str = "",
    chat_id: str | None = None,
    reply_markup=None,
) -> bool:
    """Telegram'ga bytes ko'rinishidagi faylni yuboradi (eksport)."""
    import io

    s = telegram_settings()
    if not s["token"] or not (chat_id or s["chat_id"]):
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    payload = {"chat_id": chat_id or s["chat_id"], "caption": caption}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with io.BytesIO(data) as bio:
        telegram_call(
            "sendDocument",
            payload,
            files={"document": (filename, bio)},
        )
    return True


def send_photo(
    file_path: str,
    caption: str = "",
    chat_id: str | None = None,
    reply_markup=None,
) -> bool:
    """Telegram'ga rasm (haydovchilar jadvali) yuboradi."""
    s = telegram_settings()
    if not s["token"] or not (chat_id or s["chat_id"]):
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    payload = {"chat_id": chat_id or s["chat_id"], "caption": caption}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with open(file_path, "rb") as fh:
        telegram_call(
            "sendPhoto",
            payload,
            files={"photo": (os.path.basename(file_path), fh)},
        )
    return True


def send_report_summary(result: dict) -> None:
    """Hisobot natijasini matn ko'rinishida yuboradi."""
    if not _configured():
        return
    lines = [f"Hisobot: {result.get('kind', '')}", f"Sana: {result.get('from', result.get('date', ''))}"]
    files = result.get("files", [])
    if files:
        lines.append("Fayllar:")
        for f in files:
            lines.append(f"  - {Path(f).name}")
    send_message("\n".join(lines))
    for f in files:
        if str(f).lower().endswith((".xlsx", ".json")):
            try:
                send_document(f)
            except Exception:
                pass
