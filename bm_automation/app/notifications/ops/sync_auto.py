"""Avtomatik kunlik sinxronlash — bot poll-tsiklidan chaqiriladi.

Har kuni soat `bot_settings.auto_sync_hour()` (standart 6:00) da o'tgan
kun (+dushanba shanba/yakshanba) va bugungi kunni BM API'dan DB'ga
sinxronlaydi:

- `sync_statuses` + `sync_profiles` + `sync_routes` — bir marta;
- so'ng har bir kun `sync_days` orqali (barcha profillar/kompaniyalar).

Sync fon oqimida ishlaydi (OneID login 1-3 daqiqa olishi mumkin) — poll
tsikli bloklanmaydi. Holat `state/sync_auto.json` {"date": "YYYY-MM-DD"}:
kuniga bir marta. Xatolikda kun belgilanmaydi — `_FAIL_BACKOFF_S` dan
keyin qayta urinadi va xato `storage.record_error`ga yoziladi.

`bot_settings.auto_sync()` (standart: yoniq) bilan o'chiriladi.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from ...core import bot_settings
from ...db.storage import get_storage
from ...utils.io import atomic_write

STATE_FILE = Path("state") / "sync_auto.json"

_CHECK_INTERVAL_S = 300.0   # poll-tsikl ichida tekshiruvlar orasi
_FAIL_BACKOFF_S = 900.0     # xatolikdan keyin qayta urinish oralig'i

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0
_LAST_FAIL_AT = 0.0
_RUNNING = False


def enabled() -> bool:
    return bot_settings.auto_sync()


def hour() -> int:
    return bot_settings.auto_sync_hour()


def _load() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 - holat yo'q bo'lsa boshidan
        pass
    return {}


def _save(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - syncni buzmaydi
        print(f"Avto-sync holati saqlanmadi: {exc}")


def _target_days(today: date | None = None) -> list[str]:
    """Sinxronlanadigan kunlar: kecha + bugun (dushanba — sh./yak. ham)."""
    today = today or date.today()
    days = [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    if today.isoweekday() == 1:
        days = [(today - timedelta(days=3)).isoformat(),
                (today - timedelta(days=2)).isoformat()] + days
    return list(dict.fromkeys(days))


def check_and_sync() -> None:
    """Soat kelganda kuniga bir marta fon oqimida sync boshlaydi."""
    global _LAST_CHECK_AT, _RUNNING
    if _RUNNING or not enabled():
        return
    now = time.time()
    if now - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = now
    if time.localtime().tm_hour < hour():
        return
    if _LAST_FAIL_AT and now - _LAST_FAIL_AT < _FAIL_BACKOFF_S:
        return
    storage = get_storage()
    if not storage.enabled:
        return
    if _load().get("date") == date.today().isoformat():
        return
    with _LOCK:
        if _RUNNING:
            return
        _RUNNING = True
    threading.Thread(target=_bg, args=(storage,), daemon=True).start()


def _bg(storage) -> None:
    """Fon oqimi: login → statuses/profiles/routes → kunlar bo'yicha sync."""
    global _RUNNING, _LAST_FAIL_AT
    try:
        from ...api.client import BMClient
        from ...db import sync as db_sync

        client = BMClient()
        client.login()

        results = [db_sync.sync_statuses(storage).as_dict(),
                   db_sync.sync_profiles(storage).as_dict(),
                   db_sync.sync_routes(storage, client).as_dict()]
        days = _target_days()
        results += db_sync.sync_days(storage, client, days, routes=None)

        errors = [str(r.get("error")) for r in results if r.get("error")]
        _save({"date": date.today().isoformat(), "days": days,
               "errors": len(errors)})
        if errors:
            storage.record_error(source="sync.auto",
                                 message="; ".join(errors[:5]))
    except Exception as exc:  # noqa: BLE001 - keyingi tsiklda qayta urinadi
        _LAST_FAIL_AT = time.time()
        try:
            storage.record_error(source="sync.auto", message=str(exc))
        except Exception:  # noqa: BLE001
            pass
    finally:
        _RUNNING = False
