"""Muammo alertlari — davriy tekshiruv va ADMIN/DISPATCHER'ga bildirishnoma.

Bot poll-tsikli har iteratsiyada `check_and_notify()` chaqiradi; u har
`_CHECK_INTERVAL_S` da aktiv kundagi (bugun; bo'sh bo'lsa oxirgi reysli kun)
muammolar sonini tekshiradi. Ko'rsatkich o'zgargan (ko'paygan yoki kamaygan)
bo'lsa ruxsat etilgan chat'larga qisqa xabar yuboriladi.

Holat `state/problem_alerts.json` faylida saqlanadi:
  - bot restart'da takroriy spam bo'lmaydi;
  - birinchi tekshiruv faqat bazaviy holatni eslab qoladi (xabar emas) —
    muammolar tayyor turgan paytda yangi xabarlar to'kilmaydi;
  - kun almashganda ham jimgina yangi bazaviy holat yoziladi (kechagi
    raqamlar bilan solishtirib soxta "kamaydi" xabari chiqmasin).

Bildirishnomalar faqat ADMIN / DISPATCHER chat'lariga boradi
(`configured_roles`). Rol sozlanmagan bo'lsa (ochiq rejim) — hech kimga
yuborilmaydi.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ...dashboard.metrics import Metrics, parse_filters
from ...db.storage import get_storage
from ...utils.io import atomic_write
from ..telegram import send_message
from . import kb
from .roles import Role, configured_roles

STATE_FILE = Path("state") / "problem_alerts.json"

# Tekshiruvlar orasidagi eng kichik interval (soniya).
_CHECK_INTERVAL_S = 600.0

# Kunlik kesimda qatorlar 1000 dan oshishi mumkin — to'liq qamrov uchun chegara.
_PROBLEM_ROW_LIMIT = 5000

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


def _fmt_day(value: str) -> str:
    """ISO sanani '02.10.2026' ko'rinishida qaytaradi."""
    try:
        return f"{datetime.fromisoformat(str(value)[:10]):%d.%m.%Y}"
    except ValueError:
        return str(value or "")


def check_and_notify() -> None:
    """Joriy kun muammo sonini tekshiradi, o'zgarish bo'lsa xabar yuboradi.

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
            # Sana filteri majburiy: filtrsiz LIMIT-oyna butun tarixdan
            # tasodifiy qatorlar olib, raqamlar har tekshiruvda sakrardi.
            f = parse_filters({})
            day = str(f.get("date") or f.get("to") or now.date().isoformat())
            counts = {k: int(v) for k, v in
                      Metrics().problems(f, limit=_PROBLEM_ROW_LIMIT)["counts"].items()}
        except Exception as exc:  # noqa: BLE001 - DB xatosi xabar emas
            print(f"Muammo tekshiruvi xatosi: {exc}")
            return

        total = sum(counts.values())

        # Muammo yo'q — bazaviy holatni tozalaymiz (keyingi paydo bo'lishi xabar).
        if total == 0:
            if last or str(state.get("date") or "") != day:
                _save({"last": {}, "at": "", "date": day})
            return

        # Kun almashdi yoki birinchi aniqlash: jimgina eslab qolamiz
        # (kechagi raqamlar bilan solishtirib soxta farq chiqmasin).
        if str(state.get("date") or "") != day or not last:
            _save({"last": counts, "at": now.isoformat(timespec="seconds"),
                   "date": day})
            return

        changes = _changes(last, counts)
        if not any(changes.values()):
            return

        diff_txt = ", ".join(
            f"{_LABELS[k]}: {'+' if v > 0 else ''}{v}"
            for k, v in changes.items() if v)
        text = (
            "⚠️ <b>MUAMMO HOLATI O'ZGARDI</b>\n\n"
            f"📅 {_fmt_day(day)} · Jami: {total} ta\n"
            f"{_summary(counts)}\n"
            f"O'zgarish: {diff_txt}\n\n"
            f"🕒 {now:%H:%M} · oxirgi holat {last_at or '—'}"
        )
        _save({"last": counts, "at": now.isoformat(timespec="seconds"),
               "date": day})
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
    day = str(state.get("date") or "")
    parts = [
        "🔔 <b>MUAMMO ALERTLARI</b>",
        "",
        "Davriy tekshiruv: har 10 daqiqada (bot ishlayotganda).",
        f"So'nggi tekshiruv: {at or '—'}",
    ]
    if last:
        label = f"Saqlangan muammolar ({_fmt_day(day)}):" if day else "Saqlangan muammolar:"
        parts += [
            "",
            label,
            f"  {_summary(last)}",
        ]
    else:
        parts += [
            "",
            "Hozircha muammo qayd etilmagan ✓",
        ]
    return "\n".join(parts)
