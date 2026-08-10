"""Ish tarixi va bot yagona nusxasi (PID qulfi) uchun holat fayllari."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from ..utils.io import atomic_write

STATE_DIR = Path("state")
STATE_FILE = STATE_DIR / "status.json"
PID_FILE = STATE_DIR / "bot.pid"

MAX_RUNS = 20


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"runs": []}


def get_state() -> dict:
    return _load()


def save_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(STATE_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def record_run(results: list, sheet_date: str = "", month: str = "",
               trigger: str = "auto") -> None:
    """Bajarilgan ishni tarixga yozadi (oxirgi MAX_RUNS ta)."""
    data = _load()
    rec = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": trigger,
        "sheet_date": sheet_date,
        "month": month,
        "results": results,
    }
    data["runs"] = ([rec] + data.get("runs", []))[:MAX_RUNS]
    data["last_run"] = rec
    save_state(data)


def _pid_alive(pid: int) -> bool:
    """PID tirikligini tekshiradi (Windows va POSIX uchun)."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def acquire_lock() -> bool:
    """Boshqa bot nusxasi ishlayotgan bo'lsa False qaytaradi."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if PID_FILE.exists():
            try:
                raw = PID_FILE.read_text().strip()
                pid = int(raw.split("|")[0])
                if _pid_alive(pid):
                    return False     # jonli nusxa bor — egallab olinmaydi
                return _write_lock()  # vafot etgan — qayta egallash
            except (ValueError, OSError):
                return _write_lock()
            except Exception:
                return False
        return _write_lock()
    except Exception:
        return True


def _write_lock() -> bool:
    try:
        PID_FILE.write_text(f"{os.getpid()}|{time.time()}")
        return True
    except Exception:
        return False


def release_lock() -> None:
    try:
        if PID_FILE.exists():
            raw = PID_FILE.read_text().strip()
            pid = int(raw.split("|")[0])
            if pid == os.getpid():
                PID_FILE.unlink()
    except Exception:
        pass
