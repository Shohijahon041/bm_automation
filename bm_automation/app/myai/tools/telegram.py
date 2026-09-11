"""Telegram Tool — Telegram'ga xabar va fayl yuborish.

`notifications.telegram` modulidagi send_* funksiyalariga bevosita
chaqiriq. chat_id berilmasa TG_CHAT_ID (default) ishlatiladi.
"""

from __future__ import annotations

from typing import Any

from . import BaseTool
from ...utils.logger import get_logger

log = get_logger("myai.tools.telegram")


class TelegramTool(BaseTool):
    """Telegram orqali xabar/fayl yuborish."""

    name = "telegram"
    description = "Telegram'ga xabar yoki fayl yuborish"

    async def execute(self, action: str = "send_message", **kwargs) -> Any:
        from ...notifications.telegram import (
            send_message, send_document, send_chunks,
        )

        chat_id = kwargs.get("chat_id") or ""
        text = kwargs.get("text") or kwargs.get("message") or ""
        path = kwargs.get("path") or ""
        caption = kwargs.get("caption") or ""
        reply_markup = kwargs.get("reply_markup")

        if action == "send_document":
            if not path:
                raise ValueError("send_document uchun 'path' kerak")
            send_document(path, caption=caption, chat_id=chat_id or None,
                          reply_markup=reply_markup)
            return {"sent": True, "method": "document", "path": path,
                    "chat_id": chat_id, "message": caption}

        if action == "send_chunks":
            if not text:
                raise ValueError("send_chunks uchun 'text' kerak")
            send_chunks(chat_id or None, text, reply_markup)
            return {"sent": True, "method": "chunks", "chat_id": chat_id,
                    "message": text}

        if action == "send_status":
            if not text:
                raise ValueError("send_status uchun 'text' kerak")
            send_message(text, chat_id=chat_id or None,
                         reply_markup=reply_markup, parse_mode="HTML")
            return {"sent": True, "method": "message", "chat_id": chat_id,
                    "message": text}

        if not text:
            raise ValueError("send_message uchun 'text' kerak")
        send_message(text, chat_id=chat_id or None,
                     reply_markup=reply_markup, parse_mode="HTML")
        return {"sent": True, "method": "message", "chat_id": chat_id,
                "message": text}