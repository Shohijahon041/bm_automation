"""MyAI Bridge — Telegram botdan MyAI orchestrator ga sync ko'prik.

Bot sync ishlaydi, orchestrator async. Shu modul async loop ni
thread ichida ishga tushiradi va natijani qaytaradi.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ...utils.logger import get_logger

log = get_logger("myai.bridge")

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Foydalanilayotgan yoki yangi event loop yaratish (thread-safe)."""
    global _loop
    if _loop is not None and _loop.is_running():
        return _loop
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()
        t = threading.Thread(target=_loop.run_forever, daemon=True)
        t.start()
    return _loop


def ask_myai(user_request: str, route: str = "", date: str = "",
             chat_id: str | int = "", timeout: float = 120.0) -> dict[str, Any]:
    """Sinxron ko'prik: foydalanuvchi so'rovini MyAI ga yuboradi.

    Returns: {"ok": bool, "response": str, "task_id": str, "error": str}
    """
    from ...myai.state import create_task, get_task
    from ...myai.orchestrator import get_orchestrator

    params = None
    if chat_id:
        params = {"chat_id": str(chat_id)}

    task = create_task(user_request, route=route, date=date, params=params)
    task_id = task["task_id"]
    log.info("MyAI bridge: task yaratildi %s — %s", task_id, user_request[:60])

    loop = _get_loop()
    orch = get_orchestrator()

    future = asyncio.run_coroutine_threadsafe(orch.run_task(task_id), loop)

    try:
        result = future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        log.warning("MyAI bridge: timeout (%ss) — %s", timeout, task_id)
        return {"ok": False, "task_id": task_id,
                "error": "Timeout — javob vaqtida kelmadi"}
    except Exception as exc:
        log.error("MyAI bridge xatosi: %s", exc)
        return {"ok": False, "task_id": task_id, "error": str(exc)}

    if not result.get("ok"):
        return {"ok": False, "task_id": task_id,
                "error": result.get("error", "Noma'lum xato")}

    response = ""
    res_data = result.get("result", {})
    if isinstance(res_data, dict):
        response = res_data.get("response", "")
    if not response:
        response = "Javob tayyorlandi, lekin bo'sh qaytdi."

    return {"ok": True, "task_id": task_id, "response": response}


def format_response(resp: dict) -> str:
    """MyAI javobini Telegram HTML formatiga aylantirish."""
    if not resp.get("ok"):
        err = resp.get("error", "Noma'lum xato")
        return f"❌ <b>MyAI xatosi:</b> {esc(err)}"

    text = resp.get("response", "")
    if not text:
        return "✅ Task bajarildi, lekin javob bo'sh."

    # Agar javob allaqachon HTML bo'lsa — to'g'ridan-to'g'ri qaytar
    if "<b>" in text or "<i>" in text:
        return text

    # Markdown → Telegram HTML
    import re
    # **bold** → <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # *italic* → <i>italic</i>
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    # `code` → <code>code</code>
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    # Sarlavhalar
    lines = text.split("\n")
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            out.append("")
            continue
        if line.startswith("### "):
            out.append(f"\n<b>{line[4:]}</b>")
        elif line.startswith("## "):
            out.append(f"\n<b>{line[3:]}</b>")
        elif line.startswith("# "):
            out.append(f"\n<b>{line[2:]}</b>")
        elif line.startswith("- "):
            out.append(f"  • {line[2:]}")
        elif line.startswith("• "):
            out.append(f"  {line}")
        else:
            out.append(line)
    return "\n".join(out)


def esc(text: str) -> str:
    """HTML escape for Telegram."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
