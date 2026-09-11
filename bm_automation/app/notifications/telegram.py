"""Telegram bildirishnoma (bot orqali xabar, rasm, fayl va tugmalar).

`notify.py` ning yangi manzili. Telegram'ga bevosita API chaqiriqlar,
klaviaturalar va hisobot natijasini jo'natish shu yerda.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

from ..config.settings import telegram_settings

API = "https://api.telegram.org/bot{token}/{method}"

_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 2


def telegram_call(method: str, payload: dict | None = None, files=None):
    """Telegram Bot API'ga bevosita qo'ng'iroq. result ni qaytaradi.

    429 (rate limit) va tarmoq xatolari uchun avtomatik qayta urinadi.
    """
    s = telegram_settings()
    if not s["token"]:
        raise ValueError("TG_BOT_TOKEN .env'da ko'rsatilmagan")
    url = API.format(token=s["token"], method=method)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            if files:
                body = dict(payload or {})
                if "reply_markup" in body:
                    body["reply_markup"] = json.dumps(body["reply_markup"])
                resp = requests.post(url, data=body, files=files, timeout=120)
            else:
                resp = requests.post(url, json=payload or {}, timeout=120)
            if resp.status_code == 429:
                retry_after = (resp.json().get("parameters", {})
                               .get("retry_after", _RETRY_DELAY_BASE * (attempt + 1)))
                time.sleep(min(retry_after, 30))
                continue
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram xatosi: {data.get('description')}")
            return data.get("result")
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(_RETRY_DELAY_BASE * (attempt + 1))
            continue
        except RuntimeError:
            raise
        except Exception as exc:
            last_exc = exc
            time.sleep(_RETRY_DELAY_BASE * (attempt + 1))
            continue
    raise last_exc or RuntimeError("Telegram API: barcha urinishlar ishlamadi")


def get_chat(chat_id: int) -> dict | None:
    """Telegram Bot API getChat — foydalanuvchi profil ma'lumotini oladi."""
    try:
        return telegram_call("getChat", {"chat_id": chat_id})
    except Exception:
        return None


def get_profile_photo_url(chat_id: int, photo_file_id: str = "") -> str | None:
    """Telegram profil rasmini URL sifatida qaytaradi (kichik o'lcham)."""
    _ = photo_file_id  # unused, kept for API compat
    try:
        info = telegram_call("getUserProfilePhotos", {
            "user_id": chat_id, "offset": 0, "limit": 1,
        })
        photos = (info or {}).get("photos") or []
        if not photos:
            return None
        file_id = photos[0][-1].get("file_id")
        if not file_id:
            return None
        file_info = telegram_call("getFile", {"file_id": file_id})
        file_path = file_info.get("file_path")
        if not file_path:
            return None
        s = telegram_settings()
        return f"https://api.telegram.org/file/bot{s['token']}/{file_path}"
    except Exception:
        return None


def _configured() -> bool:
    s = telegram_settings()
    return bool(s["token"] and s["chat_id"])


MAX_TEXT_LEN = 4096


def _strip_html(text: str) -> str:
    import re
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", text))


def split_text(text: str, limit: int = MAX_TEXT_LEN) -> list:
    """Telegram xabar limitiga mos (matn, parse_mode) bloklari.

    HTML teglar `\n\n` (paragraf) chegarasidan o'tmaydi — bloklar shu
    chegara bo'yicha bo'linadi. Bitta paragraf limitdan oshsa (masalan
    katta `<pre>` jadval) HTML tashlab, oddiy matn sifatida bo'laklanadi.
    """
    if not text:
        return [("", "HTML")]
    paragraphs = text.split("\n\n")
    chunks = []
    buf = []
    size = 0
    for p in paragraphs:
        plen = len(p)
        if buf and size + plen + 2 > limit:
            chunks.append(("\n\n".join(buf), "HTML"))
            buf, size = [], 0
        if plen <= limit:
            buf.append(p)
            size += plen
            continue
        if buf:
            chunks.append(("\n\n".join(buf), "HTML"))
            buf, size = [], 0
        chunks.extend(_split_oversized(p, limit))
    if buf:
        chunks.append(("\n\n".join(buf), "HTML"))
    return chunks


def _split_oversized(paragraph: str, limit: int) -> list:
    """Limitdan oshgan bitta paragrafni plain text qilib bo'laklaydi."""
    plain = _strip_html(paragraph)
    lines = plain.split("\n")
    chunks = []
    buf = []
    size = 0
    for ln in lines:
        if len(ln) > limit:
            if buf:
                chunks.append(("\n".join(buf), None))
                buf, size = [], 0
            for i in range(0, len(ln), limit):
                chunks.append((ln[i:i + limit], None))
            continue
        if buf and size + len(ln) + 1 > limit:
            chunks.append(("\n".join(buf), None))
            buf, size = [], 0
        buf.append(ln)
        size += len(ln)
    if buf:
        chunks.append(("\n".join(buf), None))
    return chunks


def send_chunks(chat_id, text: str, reply_markup=None) -> None:
    """Uzun xabarni limitga mos bloklarga bo'lib yuboradi.

    Klaviatura faqat oxirgi blokka biriktiriladi; har blok o'z
    parse_mode'ida (HTML yoki oddiy matn) yuboriladi.
    """
    s = telegram_settings()
    if not s["token"] or not chat_id:
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    chunks = split_text(text)
    last = len(chunks) - 1
    for i, (body, mode) in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": body}
        if mode:
            payload["parse_mode"] = mode
        if i == last and reply_markup:
            payload["reply_markup"] = reply_markup
        telegram_call("sendMessage", payload)


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


def send_message(text: str, chat_id: str | None = None, reply_markup=None,
                 parse_mode: str = "HTML") -> bool:
    """Bot orqali matnli xabar yuboradi.

    Xabar 4096 belgidan oshsa avtomatik bir nechta xabarga bo'linadi
    (aks holda Telegram "message is too long" xatosi bilan butun xabarni
    qaytaradi).
    """
    s = telegram_settings()
    if not s["token"] or not (chat_id or s["chat_id"]):
        raise ValueError("TG_BOT_TOKEN/TG_CHAT_ID .env'da ko'rsatilmagan")
    target = chat_id or s["chat_id"]
    if len(text) > MAX_TEXT_LEN:
        send_chunks(target, text, reply_markup)
        return True
    payload = {"chat_id": target, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_call("sendMessage", payload)
    return True


def _with_filename(caption: str, name: str) -> str:
    """Caption'ga fayl nomini qo'shadi (foydalanuvchiga 'bu nima' ko'rinishi)."""
    head = caption.strip() or "📄 Fayl"
    return f"{head}\n\n📄 Fayl: {name}"


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
    name = os.path.basename(file_path)
    payload = {"chat_id": chat_id or s["chat_id"],
               "caption": _with_filename(caption, name)}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with open(file_path, "rb") as fh:
        telegram_call(
            "sendDocument",
            payload,
            files={"document": (name, fh)},
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
    payload = {"chat_id": chat_id or s["chat_id"],
               "caption": _with_filename(caption, filename)}
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
    name = os.path.basename(file_path)
    payload = {"chat_id": chat_id or s["chat_id"],
               "caption": _with_filename(caption, name)}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    with open(file_path, "rb") as fh:
        telegram_call(
            "sendPhoto",
            payload,
            files={"photo": (name, fh)},
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
                send_document(f, caption=f"📊 Hisobot: {result.get('kind', '')}")
            except Exception as exc:
                print(f"Hisobot fayl yuborish xatosi ({f}): {exc}")
