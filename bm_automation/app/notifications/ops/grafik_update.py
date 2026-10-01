"""Grafik yangilanishi kuzatuvi — e'lon qilingandan KEYIN o'zgargan
grafikni qayta yuboradi.

Bot poll-tsikli har iteratsiyada ``check_and_send()`` chaqiradi; u har 15
daqiqada bugun/ertangi kun uchun AVVAL YUBORILGAN grafiklarning duty
ma'lumotini BM API'dan qayta yuklab, `state/grafik_sent.json` dagi
fingerprint (sha1 hash) bilan solishtiradi. Ma'lumot o'zgargan bo'lsa
(haydovchi/avtobus/vaqt almashtirilgan):
  - Excel qayta generatsiya qilinib yo'nalish guruhlariga yuboriladi;
  - jadval rasmi yangilanib guruhlarga yuboriladi;
  - FAQAT o'zgargan haydovchilarga shaxsiy karta qayta yuboriladi;
  - ADMIN/DISPATCHER'ga qisqa xulosa ketadi.

Hash'siz eski (``True``) yozuvlar birinchi tekshiruvda jimgina baseline
bilan to'ldiriladi — qayta yuborilmaydi (o'sha paytdagi holat boshlang'ich
nuqta bo'ladi; keyingi o'zgarishlar ushlanadi).

Tokenlar fayldan o'qiladi; eskirgan bo'lsa faqat silent refresh (OneID
brauzer oqimi ishga tushmaydi — jarayon bloklanmasligi uchun).
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from pathlib import Path

_CHECK_INTERVAL_S = 900.0  # 15 daqiqa

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_LAST_CHECK_AT = 0.0


def _notify(reports: list[str]) -> None:
    """ADMIN/DISPATCHER'ga yangilanish xulosasini yuboradi."""
    try:
        from ..telegram import send_message
        from .grafik_sms import _targets
        tg = _targets()
        if not tg:
            return
        lines = ["♻️ <b>GRAFIK YANGILANDI</b>",
                 "Grafik ma'lumoti o'zgargani uchun guruhlarga "
                 "qayta yuborildi:", ""]
        lines.extend(f"• {r}" for r in reports[:8])
        text = "\n".join(lines)
        for cid in tg:
            try:
                send_message(text, chat_id=str(cid))
            except Exception as exc:  # noqa: BLE001
                print(f"Grafik yangilash xulosasi yuborilmadi [{cid}]: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"Grafik yangilash xulosasi xatosi: {exc}")


def _send_update(client, route_tuple, duty: dict, date_str: str,
                 changed_ids: set) -> bool:
    """O'zgargan grafikni qayta yuboradi: Excel + rasm (+ kartalar).

    True — Excel va rasm ikkalasi ham muvaffaqiyatli yuborildi (holat
    yangilanadi); False — to'liq yuborilmadi (keyingi tsiklda qayta
    uriniladi). Shaxsiy kartalar faqat ``changed_ids`` haydovchilarga.
    """
    _out_prefix, rid, img_dir, label, _tpl_idx = route_tuple
    d = date.fromisoformat(date_str)

    import daily_grafik as dg
    from bm_automation.app.core.profiles import all_profiles
    from bm_automation.app.services.export_service import build_export
    from bm_automation.app.services.driver_sheet_service import run as run_sheet
    from ..telegram import send_document, send_photo

    targets = dg._route_targets(rid, None)
    if not targets:
        print(f"  [{label}] grafik o'zgardi, lekin yuboriladigan guruh "
              f"yo'q — keyinroq qayta uriniladi")
        return False

    prof = {str(p.get("routeVariantId", "") or "").strip(): p
            for p in all_profiles()}.get(rid) or {}

    excel_ok = False
    try:
        excel_file = build_export(
            client, rid, date_str, out_dir=dg.REPORTS, profile=prof,
            duty_data=duty,
            mech_name=prof.get("mechName") or "",
            dispatcher_name=prof.get("dispatcherName") or "",
        )
        for t in targets:
            send_document(
                str(excel_file),
                caption=(f"♻️ YANGILANGAN — {excel_file.stem}\n"
                         f"🚏 {label} — {d:%d.%m.%Y} grafigi o'zgargani "
                         f"uchun qayta yuborildi"),
                chat_id=t)
        excel_ok = True
        print(f"  [{label}] yangilangan Excel yuborildi: {excel_file.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] yangilangan Excel xatolik: {exc}")

    image_ok = False
    try:
        res = run_sheet(client, rid, date_str=date_str,
                        out_dir=str(dg.REPORTS / img_dir),
                        send=False, profile=prof, duty_data=duty)
        img = Path(res["image"])
        if img.exists():
            for t in targets:
                send_photo(
                    str(img),
                    caption=(f"♻️ YANGILANGAN — 🚌 {label} | "
                             f"{d:%d.%m.%Y} | Kunlik chiqish jadvali "
                             f"(o'zgarish kiritildi)"),
                    chat_id=t)
            image_ok = True
            print(f"  [{label}] yangilangan rasm yuborildi: {img.name}")
            if changed_ids:
                dg._send_personal_cards(res, rid, d, label, None,
                                        only_driver_ids=changed_ids)
        else:
            print(f"  [{label}] yangilangan rasm topilmadi: {img.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] yangilangan rasm xatolik: {exc}")

    return excel_ok and image_ok


def _run_once() -> None:
    try:
        from ...repositories.duty_repo import DutyRepository

        import daily_grafik as dg

        from .grafik_sms import _fresh_client

        client = _fresh_client()
        if client is None:
            return

        today = date.today()
        valid_dates = {today.isoformat(),
                       (today + timedelta(days=1)).isoformat()}
        routes = {r[1]: r for r in dg.ROUTES}

        state = dg._grafik_sent_state()
        reports: list[str] = []
        for key, entry in list(state.items()):
            if "|" not in key or key.startswith(("notfound|", "error|")):
                continue
            rid, _, date_str = key.partition("|")
            if rid not in routes or date_str not in valid_dates:
                continue
            if dg._is_inflight(rid, date_str):
                # Hozir tugma orqali qo'lda oqim ishlayapti — u o'zi
                # qayta yuboradi. Takror yubormaymiz.
                continue
            _out_prefix, _rid, _img_dir, label, _tpl_idx = routes[rid]
            try:
                duty = DutyRepository(client).by_date(rid, date_str)
            except Exception as exc:  # noqa: BLE001
                if dg._not_found(exc):
                    continue  # grafik yo'q — o'zgarish ham yo'q
                print(f"[{label}] yangilanishni tekshirib bo'lmadi: {exc}")
                continue
            if not (duty.get("graphs") or []):
                continue
            total_hash, driver_hashes = dg._duty_fingerprint(duty)

            old = entry if isinstance(entry, dict) else None
            old_hash = (old or {}).get("hash")
            old_drivers = (old or {}).get("drivers") or {}
            if not old_hash:
                # Hash'siz (eski formatdagi) yozuv — jimgina baseline.
                dg._mark_grafik_sent(rid, date_str, hash=total_hash,
                                     drivers=driver_hashes)
                print(f"[{label}] {date_str}: baseline fingerprint yozildi")
                continue
            if total_hash == old_hash:
                continue  # o'zgarish yo'q

            changed = {did for did, h in driver_hashes.items()
                       if old_drivers.get(did) != h}
            ok = _send_update(client, routes[rid], duty, date_str, changed)
            if ok:
                dg._mark_grafik_sent(rid, date_str, hash=total_hash,
                                     drivers=driver_hashes)
                _d = date.fromisoformat(date_str)
                reports.append(f"{label}: {_d:%d.%m.%Y} grafigi yangilandi "
                               f"({len(changed)} haydovchi o'zgardi)")
                print(f"[{label}] yangilangan grafik qayta yuborildi")

        if reports:
            _notify(reports)
    except Exception as exc:  # noqa: BLE001 - bot umuman to'xtamasligi uchun
        print(f"Grafik yangilash xatosi: {exc}")


def check_and_send() -> None:
    """15 daqiqalik tekshiruvni fon thread'da ishga tushiradi (idempotent).

    Asosiy poll thread bloklanmaydi; bir vaqtda bitta tekshiruv ishlaydi.
    """
    global _LAST_CHECK_AT, _THREAD
    if time.time() - _LAST_CHECK_AT < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK_AT = time.time()
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_run_once, daemon=True)
        _THREAD.start()
