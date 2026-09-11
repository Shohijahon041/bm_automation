"""Muammo alertlari — davriy tekshiruv va ADMIN/DISPATCHER'ga bildirishnoma.

Bot poll-tsikli har iteratsiyada `check_and_notify()` chaqiradi; u har
`_CHECK_INTERVAL_S` da DB'dagi joriy muammo sonini tekshiradi. Ko'rsatkich
o'zgargan (ko'paygan yoki kamaygan) bo'lsa ruxsat etilgan chat'larga qisqa
xabar yuboriladi.

Holat `state/problem_alerts.json` faylida saqlanadi:
  - bot restart'da takroriy spam bo'lmaydi;
  - birinchi tekshiruv faqat bazaviy holatni eslab qoladi (xabar emas) —
    muammolar tayyor turgan paytda yangi xabarlar to'kilmaydi.

Bildirishnomalar faqat ADMIN / DISPATCHER chat'lariga boradi
(`configured_roles`). Rol sozlanmagan bo'lsa (ochiq rejim) — hech kimga
yuborilmaydi.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ...dashboard.metrics import Metrics
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ..telegram import send_message
from . import kb
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "problem_alerts.json"

# Tekshiruvlar orasidagi eng kichik interval (soniya).
_CHECK_INTERVAL_S = 600.0

_LABELS = {
    "gps": "GPS",
    "technical": "Texnik",
    "schedule": "Jadval",
    "unknown": "Noma'lum",
}

_LOCK = threading.Lock()
_LAST_CHECK_AT = 0.0  # in-memory interval hisoblagichi (restart'da nollanadi)


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
    except Exception as exc:  # noqa: BLE001 - bildirishnomani buzmaydi
        print(f"Muammo alert holati saqlanmadi: {exc}")


def _targets() -> list[int]:
    roles = configured_roles()
    return [cid for cid, role in roles.items()
            if role in (Role.ADMIN, Role.DISPATCHER)]


def _changes(old: dict, new: dict) -> dict:
    """Har bir kategoriya bo'yicha farq (+/-/0)."""
    out = {}
    for key in _LABELS:
        o = int(old.get(key) or 0)
        n = int(new.get(key) or 0)
        if n != o:
            out[key] = n - o
    return out


def _summary(counts: dict) -> str:
    return (f"GPS: {counts.get('gps', 0)} · "
            f"Texnik: {counts.get('technical', 0)} · "
            f"Jadval: {counts.get('schedule', 0)} · "
            f"Noma'lum: {counts.get('unknown', 0)}")


def check_and_notify() -> None:
    """Joriy muammo sonini tekshiradi, o'zgarish bo'lsa xabar yuboradi.

    Interval geytidan o'tmagan chaqiruvlar darhol qaytadi — poll-tsikl
    har iteratsiyada bemalol chaqirishi mumkin.
    """
    global _LAST_CHECK_AT
    if not get_storage().enabled:
        return
    with _LOCK:
        now = datetime.now()
        if now.timestamp() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
            return
        _LAST_CHECK_AT = now.timestamp()

        state = _load()
        last = state.get("last") or {}
        last_at = state.get("at") or ""
        try:
            counts = {k: int(v) for k, v in
                      Metrics().problems({})["counts"].items()}
        except Exception as exc:  # noqa: BLE001 - DB xatosi xabar emas
            print(f"Muammo tekshiruvi xatosi: {exc}")
            return

        total = sum(counts.values())

        # Muammo yo'q — bazaviy holatni tozalaymiz (keyingi paydo bo'lishi xabar).
        if total == 0:
            if last:
                _save({"last": {}, "at": ""})
            return

        if not last:
            # Birinchi aniqlash: faqat eslab qolamiz (spam bo'lmasligi uchun).
            _save({"last": counts, "at": now.isoformat(timespec="seconds")})
            return

        changes = _changes(last, counts)
        if not any(changes.values()):
            return

        diff_txt = ", ".join(
            f"{_LABELS[k]}: {'+' if v > 0 else ''}{v}"
            for k, v in changes.items() if v)
        text = (
            "⚠️ <b>MUAMMO HOLATI O'ZGARDI</b>\n\n"
            f"Jami: {total} ta\n"
            f"{_summary(counts)}\n"
            f"O'zgarish: {diff_txt}\n\n"
            f"🕒 {now:%H:%M} · oxirgi holat {last_at or '—'}"
        )
        _save({"last": counts, "at": now.isoformat(timespec="seconds")})
        for cid in _targets():
            try:
                send_message(text, chat_id=str(cid),
                             reply_markup=kb.problems_kb({"counts": counts}))
            except Exception as exc:  # noqa: BLE001
                print(f"Muammo alert yuborilmadi [{cid}]: {exc}")


def status_text() -> str:
    """`/alerts` ekrani uchun holat matni."""
    state = _load()
    last = state.get("last") or {}
    at = state.get("at") or ""
    parts = [
        "🔔 <b>MUAMMO ALERTLARI</b>",
        "",
        "Davriy tekshiruv: har 10 daqiqada (bot ishlayotganda).",
        f"So'nggi tekshiruv: {at or '—'}",
    ]
    if last:
        parts += [
            "",
            "Saqlangan muammolar:",
            f"  {_summary(last)}",
        ]
    else:
        parts += [
            "",
            "Hozircha muammo qayd etilmagan ✓",
        ]
    return "\n".join(parts)
