"""Aloqa uzilishi (offline) nazorati: qurilma uzoq vaqt yubormasa.

Fon tredi davriy (`OFFLINE_CHECK_S`) har bir yoqilgan qurilmaning oxirgi
aloqa vaqtini tekshiradi:
  1. oxirgi ko'rinishdan `OFFLINE_AFTER_S` dan ko'p o'tgan bo'lsa → OFFLINE
  2. `OFFLINE_MAX_AGE_S`dan uzoq o'chiq turgan "eski" qurilmalar
     ogohlantirilmaydi (o'lik qurilmalar shovqin qilmasin)
  3. ishga tushgandan keyingi birinchi tekshiruv "warmup" — mavjud holat
     jimgina qayd etiladi, alert berilmaydi (deploy'da spam bo'lmasin)
  4. bir uzilish davrida faqat bir marta ogohlantiriladi (state saqlanadi)
  5. qurilma qayta yubora boshlasa — holat "online"ga o'zgaradi
  6. gps_alerts'ga yoziladi + Telegram'ga yuboriladi (sozlangan bo'lsa)

Telegram xatosi hech qachon oqimni buzmaydi (fail-safe).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import config, storage

log = logging.getLogger(__name__)

# Hozir offline deb belgilangan IMEI'lar (takroriy alertning oldini oladi)
_offline: set[str] = set()
_lock = threading.Lock()
# Birinchi tekshiruv warmup: mavjud offline holat alert bermasdan qayd etiladi
_seeded = False


def _age_s(last_seen) -> float | None:
    """Oxirgi aloqa vaqtidan o'tgan sekund; noma'lum bo'lsa None."""
    if not last_seen:
        return None
    try:
        ts = last_seen if isinstance(last_seen, datetime) else (
            datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
        )
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def check_devices(devices: list | None = None) -> list:
    """Bir marta tekshiradi. Yangi offline qurilmalar ro'yxatini qaytaradi."""
    global _seeded
    if devices is None:
        try:
            # Faqat Teltonika GPS: kamera (kind='camera') aloqasi uzilishi
            # haqida ogohlantirilmaydi — u GPS nazoratiga kirmaydi.
            devices = storage.teltonika_only(storage.list_devices())
        except Exception:  # noqa: BLE001
            log.exception("offline: qurilmalar olinmadi")
            return []
    alert_ok = _seeded  # warmup tugamagan bo'lsa alert bermaymiz
    newly: list = []
    for d in devices or []:
        imei = str(d.get("imei") or "")
        if not imei:
            continue
        if not d.get("enabled"):
            with _lock:
                _offline.discard(imei)
            continue
        age = _age_s(d.get("last_seen"))
        if age is None:
            continue  # hech qachon ko'rinmagan — hisobga olinmaydi
        if age >= config.OFFLINE_AFTER_S:
            if age > config.OFFLINE_MAX_AGE_S:
                with _lock:
                    _offline.discard(imei)
                continue  # juda eski (o'lik) qurilma — ogohlantirmaymiz
            with _lock:
                if imei in _offline:
                    continue
                _offline.add(imei)
            if not alert_ok:
                continue  # warmup — jimgina qayd etildi
            newly.append(d)
            _raise_offline(d, age)
        else:
            with _lock:
                _offline.discard(imei)
    _seeded = True
    return newly


def _raise_offline(device: dict, age: float) -> None:
    imei = str(device.get("imei") or "")
    vehicle_id = str(device.get("vehicle_id") or "")
    nm = (device.get("plate_number") or device.get("name")
          or vehicle_id or imei)
    mins = int(age / 60)
    msg = (
        f"📵 <b>Aloqa uzildi (offline)</b>\n"
        f"Avtobus: <b>{nm}</b>\n"
        f"Qurilma: <code>{imei}</code>\n"
        f"Oxirgi aloqa: {mins} daqiqa oldin"
    )

    try:
        storage.insert_alert(
            imei=imei,
            vehicle_id=vehicle_id,
            kind="offline",
            fence_id=0,
            message=f"{mins} daqiqa oldin uzildi",
        )
    except Exception:  # noqa: BLE001
        log.exception("offline: alert yozilmadi (imei=%s)", imei)

    if config.OFFLINE_TELEGRAM:
        try:
            from ..notifications.telegram import send_message

            ok = send_message(msg)
            if not ok:
                log.warning("offline: telegram yuborilmadi (configured emas?)")
        except Exception:  # noqa: BLE001
            log.exception("offline: telegram xatosi (davom etamiz)")

    log.info("offline: imei=%s (%s daqiqa)", imei, mins)


def start_monitor() -> None:
    """Fon tredi: davriy offline tekshiruvi (standart: har 5 daqiqada)."""
    if not config.OFFLINE_ALERT_ENABLED:
        log.info("offline ogohlantirish o'chirilgan (GPS_OFFLINE_ALERT_ENABLED=0)")
        return

    def _loop() -> None:
        time.sleep(30)
        while True:
            try:
                check_devices()
            except Exception:  # noqa: BLE001
                log.exception("offline monitor xatosi")
            time.sleep(config.OFFLINE_CHECK_S)

    threading.Thread(target=_loop, daemon=True, name="bm-gps-offline").start()
