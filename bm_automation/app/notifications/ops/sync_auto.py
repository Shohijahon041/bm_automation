"""Avtomatik sinxronlash — bot poll-tsiklidan chaqiriladi.

Har `bot_settings.auto_sync_interval_min()` daqiqada (standart: 10) BM
API'dan DB'ga fon oqimida sinxronlaydi:

- `sync_statuses` + `sync_profiles` + `sync_routes` — bir marta;
- kunlar: kunning birinchi sinxronlashida kechagi kun (dushanba —
  shanba/yakshanba ham), keyingi urinishlarda faqat bugun — `sync_days`
  orqali (barcha profillar/kompaniyalar).

Sync fon oqimida ishlaydi (OneID login 1-3 daqiqa olishi mumkin) — poll
tsikli bloklanmaydi. Holat `state/sync_auto.json`:
``{"last_run": epoch, "date": "YYYY-MM-DD", "days": [...], "errors": n}``.

- `last_run` sinxronlash boshlanishida va tugashida yangilanadi —
  bot qayta ishga tushsa ham interval saqlanadi;
- xatolikda `date` belgilanmaydi (keyingi urinishda kechagi kun yana
  qo'shiladi) va xato `storage.record_error`ga yoziladi.

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

_CHECK_INTERVAL_S = 60.0    # poll-tsikl ichida tekshiruvlar orasi

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0
_RUNNING = False


def enabled() -> bool:
    return bot_settings.auto_sync()


def interval_s() -> float:
    """Sinxronlash oralig'i (soniya) — sozlamadan (daqiqa)."""
    return max(1, bot_settings.auto_sync_interval_min()) * 60.0


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


def _target_days(first: bool, today: date | None = None) -> list[str]:
    """Sinxronlanadigan kunlar.

    Kun boshida (birinchi urinishda) kechagi kun — dushanba bo'lsa
    shanba/yakshanba ham; keyingi urinishlarda faqat bugun.
    """
    today = today or date.today()
    days = [today.isoformat()]
    if first:
        days = [(today - timedelta(days=1)).isoformat()] + days
        if today.isoweekday() == 1:  # dushanba — shanba/yakshanba ham
            days = [(today - timedelta(days=3)).isoformat(),
                    (today - timedelta(days=2)).isoformat()] + days
    return list(dict.fromkeys(days))


def check_and_sync() -> None:
    """Interval o'tgan bo'lsa fon oqimida sinxronlashni boshlaydi."""
    global _LAST_CHECK_AT, _RUNNING
    if _RUNNING or not enabled():
        return
    now = time.time()
    if now - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = now
    state = _load()
    try:
        last_run = float(state.get("last_run") or 0.0)
    except (TypeError, ValueError):
        last_run = 0.0
    if now - last_run < interval_s():
        return
    storage = get_storage()
    if not storage.enabled:
        return
    with _LOCK:
        if _RUNNING:
            return
        _RUNNING = True
    # Boshlanishdagi belgi — bot qayta ishga tushsa ham interval saqlanadi.
    _save({**state, "last_run": now})
    first = str(state.get("date") or "") != date.today().isoformat()
    threading.Thread(target=_bg, args=(storage, first), daemon=True).start()


def _bg(storage, first: bool) -> None:
    """Fon oqimi: login → statuses/profiles/routes → kunlar bo'yicha sync."""
    global _RUNNING
    try:
        from ...api.client import BMClient
        from ...db import sync as db_sync

        client = BMClient()
        client.login()

        results = [db_sync.sync_statuses(storage).as_dict(),
                   db_sync.sync_profiles(storage).as_dict(),
                   db_sync.sync_routes(storage, client).as_dict()]
        days = _target_days(first)
        results += db_sync.sync_days(storage, client, days, routes=None)

        errors = [str(r.get("error")) for r in results if r.get("error")]
        _save({"last_run": time.time(), "date": date.today().isoformat(),
               "days": days, "errors": len(errors)})
        if errors:
            storage.record_error(source="sync.auto",
                                 message="; ".join(errors[:5]))
    except Exception as exc:  # noqa: BLE001 - keyingi intervalda qayta urinadi
        _save({**_load(), "last_run": time.time()})
        try:
            storage.record_error(source="sync.auto", message=str(exc))
        except Exception:  # noqa: BLE001
            pass
    finally:
        _RUNNING = False
