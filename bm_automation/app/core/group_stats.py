"""Guruh chat'laridan botga yozilgan xabarlar statistikasi.

`state/group_stats.json` faylida har bir guruh chat uchun:
  - message_count — jami xabarlar soni
  - command_count — slash-buyruqlar soni
  - senders — jo'natuvchilar ro'yxati (user_id -> {"name", "count"})
  - last_message, first_seen, last_seen
Saqlash thread-safe, dashboard `/api/group-stats` orqali ko'radi.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ..utils.io import atomic_write

STATE_FILE = Path("state") / "group_stats.json"

_LOCK = threading.RLock()
_CACHE: dict = {}
_LOADED = False
_LAST_MTIME: float | None = None
_LAST_SIZE: int | None = None


def _load() -> dict:
    global _LOADED, _CACHE
    if _LOADED:
        return _CACHE
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE = data
    except Exception:
        pass
    _LOADED = True
    return _CACHE


def _reload_if_changed() -> None:
    global _CACHE, _LOADED, _LAST_MTIME, _LAST_SIZE
    try:
        if not STATE_FILE.exists():
            return
        st = STATE_FILE.stat()
        mtime, size = st.st_mtime, st.st_size
        if _LAST_MTIME == mtime and _LAST_SIZE == size:
            return
        with STATE_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _CACHE = data
        _LAST_MTIME, _LAST_SIZE, _LOADED = mtime, size, True
    except Exception:
        pass


def _save() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(_CACHE, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Guruh statistikasi saqlanmadi: {exc}")


def record_group_message(chat_id: int, title: str, sender: dict | None,
                         is_command: bool, detail: str = "") -> None:
    """Guruh chat'idan botga yozilgan xabarni qayd qiladi."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = str(chat_id)
    with _LOCK:
        _reload_if_changed()
        g = _CACHE.get(key)
        if g is None:
            g = _CACHE[key] = {
                "chat_id": chat_id,
                "title": title,
                "first_seen": now,
                "last_seen": now,
                "message_count": 0,
                "command_count": 0,
                "senders": {},
                "last_message": {},
            }
        g["title"] = title or g.get("title", "")
        g["last_seen"] = now
        g["message_count"] = g.get("message_count", 0) + 1
        if is_command:
            g["command_count"] = g.get("command_count", 0) + 1
        g["last_message"] = {
            "at": now,
            "sender": (sender or {}).get("name", ""),
            "detail": detail[:200],
        }
        if sender and sender.get("id"):
            uid = str(sender["id"])
            s = g.setdefault("senders", {}).get(uid, {})
            s["name"] = sender.get("name", "")
            s["count"] = s.get("count", 0) + 1
            g["senders"][uid] = s
        _save()


def group_stats() -> dict:
    """Barcha guruhlar bo'yicha yig'ma statistika."""
    with _LOCK:
        _reload_if_changed()
        groups = []
        for key, g in _CACHE.items():
            if not isinstance(g, dict) or not str(g.get("chat_id") or "").strip():
                continue
            snd = g.get("senders") or {}
            groups.append({
                "chat_id": g.get("chat_id"),
                "title": g.get("title") or f"Guruh {g.get('chat_id')}",
                "message_count": g.get("message_count", 0),
                "command_count": g.get("command_count", 0),
                "senders": sorted(snd.values(), key=lambda s: s.get("count", 0),
                                  reverse=True),
                "first_seen": g.get("first_seen", ""),
                "last_seen": g.get("last_seen", ""),
                "last_message": g.get("last_message", {}),
            })
        groups.sort(key=lambda g: g["message_count"], reverse=True)
        total_msgs = sum(g["message_count"] for g in groups)
        total_cmds = sum(g["command_count"] for g in groups)
        return {
            "total_groups": len(groups),
            "total_messages": total_msgs,
            "total_commands": total_cmds,
            "groups": groups,
        }