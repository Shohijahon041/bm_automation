"""Grafik o'zgarishi kuzatuvi — ertangi kun SMS'larini doimiy tekshiradi.

Bot poll-tsikli har iteratsiyada ``check_and_send()`` chaqiradi; u har 15
daqiqada ertangi kun grafigini BM API'dan qayta yuklab, avval yuborilgan
holat bilan solishtiradi (``send_sms_for_graphs`` diff logikasi):
  - grafikka yangi qo'shilgan / o'zgargan haydovchilar -> eslatma SMS;
  - grafikdan chiqarilgan haydovchilar -> "chiqarildi" SMS (bir marta).

Bu 18:00 (daily_grafik) va 20:00 (daily) tasklaridan KEYIN sodir bo'ladigan
grafik o'zgarishlarini ham ushlaydi. Diff idempotent — qayta urinish
dublikat yubormaydi.

Faol davr: soat 17:00 dan ertalabgi 06:00 gacha (ertasi kunga qadar).
Tokenlar fayldan o'qiladi; eskirgan bo'lsa faqat silent refresh (OneID
brauzer oqimi bu yerda ishga tushmaydi — jarayon bloklanmasligi uchun).

O'zgarish yuz bersa (yuborilgan/chiqarilgan/failed) ADMIN/DISPATCHER
rollariga qisqa Telegram xulosa yuboriladi.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta

_CHECK_INTERVAL_S = 900.0  # 15 daqiqa
_ACTIVE_START_H = 17  # faol: 17:00 dan ...
_ACTIVE_END_H = 6     # ... ertalabgi 06:00 gacha
_SUMMARY_KEYS = ("sent", "updated", "removed", "failed", "invalid", "skipped")

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_LAST_CHECK_AT = 0.0


def _active_now() -> bool:
    """Faqat grafik an'anaviy e'lon qilinadigan davrda ishlaydi."""
    h = time.localtime().tm_hour
    return h >= _ACTIVE_START_H or h < _ACTIVE_END_H


def _fresh_client():
    """Saqlangan token bilan client; brauzer loginsiz (bloklanmasligi uchun).

    Token yo'q yoki refresh ham ishlamasa ``None`` qaytariladi — bu davrda
    tekshiruv o'tkazib yuboriladi (daily task keyingi login'da yangilaydi).
    """
    try:
        from ...api.client import BMAuthError, BMClient
    except Exception:  # noqa: BLE001
        return None
    try:
        client = BMClient()
        if not client.load_tokens_from_file():
            return None
        if client._token_remaining(client.access_token) > 60:
            return client
        try:
            client._refresh()
            return client
        except BMAuthError:
            return None
    except Exception:  # noqa: BLE001 - tekshiruv ishlamasa bir davr o'tkazib yuboriladi
        return None


def _targets() -> list[int]:
    try:
        from .roles import Role, configured_roles
    except Exception:  # noqa: BLE001
        return []
    return [cid for cid, role in configured_roles().items()
            if role in (Role.ADMIN, Role.DISPATCHER)]


def _notify_summary(total: dict, reports: list[str], date_str: str) -> None:
    try:
        from ..telegram import send_message
        tg = _targets()
        if not tg:
            return
        bits = []
        for k in ("sent", "updated", "removed", "failed", "invalid"):
            v = total.get(k, 0)
            if v:
                label = {"sent": "yuborildi", "updated": "tuzatildi",
                         "removed": "chiqarildi",
                         "failed": "xato", "invalid": "noaniq"}[k]
                bits.append(f"{label}: {v}")
        if not bits:
            return
        lines = [f"📋 <b>Grafik kuzatuvi</b> · {date_str}",
                 "  · ".join(bits), ""]
        lines.extend(f"• {r}" for r in reports[:8])
        text = "\n".join(lines)
        for cid in tg:
            try:
                send_message(text, chat_id=str(cid))
            except Exception as exc:  # noqa: BLE001
                print(f"Grafik kuzatuvi xulosasi yuborilmadi [{cid}]: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"Grafik kuzatuvi xulosasi xatosi: {exc}")


def _run_once() -> None:
    try:
        from ...api.client import BMApiError, BMClient
        from ...core.profiles import all_profiles
        from ...db.storage import get_storage
        from ...repositories.duty_repo import DutyRepository
        from ..sms_notify import send_sms_for_graphs

        client = _fresh_client()
        if client is None:
            return
        storage = get_storage()
        if not storage.enabled:
            return

        date_str = (date.today() + timedelta(days=1)).isoformat()
        total = {k: 0 for k in _SUMMARY_KEYS}
        reports = []

        for p in all_profiles():
            rid = str(p.get("routeVariantId", "") or "").strip()
            if not rid:
                continue
            label = str(p.get("routeName") or p.get("name") or "").strip() or rid
            try:
                duty = DutyRepository(client).by_date(rid, date_str)
                graphs = duty.get("graphs") or []
                if not graphs:
                    continue
            except BMApiError as exc:
                if exc.status == 404:
                    continue  # grafik hali e'lon qilinmagan
                print(f"[{label}] grafik olinmadi: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[{label}] grafik olinmadi: {exc}")
                continue
            try:
                res = send_sms_for_graphs(storage, graphs, rid, date_str,
                                          route_name=label)
            except Exception as exc:  # noqa: BLE001
                print(f"[{label}] SMS kuzatuvi xatosi: {exc}")
                continue
            for k in _SUMMARY_KEYS:
                total[k] += int(res.get(k) or 0)
            if res.get("sent") or res.get("updated") or res.get("removed") \
                    or res.get("failed"):
                reports.append(
                    f"{label}: +{res.get('sent', 0)} yuborildi, "
                    f"{res.get('updated', 0)} tuzatildi, "
                    f"{res.get('removed', 0)} chiqarildi, "
                    f"{res.get('failed', 0)} xato")

        if reports:
            for r in reports:
                print(f"Grafik kuzatuvi: {r}")
        _notify_summary(total, reports, date_str)
    except Exception as exc:  # noqa: BLE001 - bot umuman to'xtamasligi uchun
        print(f"Grafik kuzatuvi xatosi: {exc}")


def check_and_send() -> None:
    """15 daqiqalik tekshiruvni fon thread'da ishga tushiradi (idempotent).

    Asosiy poll thread bloklanmaydi; bir vaqtda bitta tekshiruv ishlaydi.
    """
    global _LAST_CHECK_AT, _THREAD
    if not _active_now():
        return
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_run_once, daemon=True)
        _THREAD.start()