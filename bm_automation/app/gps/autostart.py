"""GPS servisini dashboard bilan bir jarayonda FON rejimida ishga tushirish.

Dashboard server.py run() ichidan chaqiriladi (2-3 qatorli fail-safe hook).
Env bilan boshqariladi:
    GPS_AUTOSTART=1   — yoqilgan (standart)
    GPS_AUTOSTART=0   — o'chirilgan (alohida `python -m bm_automation.app.gps` bilan)

Fail-safe kafolatlar:
  * hech qanday xato dashboard ishlashiga xalaqit bermaydi (faqat log)
  * port band bo'lsa (alohida servis allaqachon ishlayotgan bo'lsa) —
    jimgina o'tkazib yuboradi, tashqi servis baribir ishlab turadi
  * thread'lar daemon — dashboard to'xtaganda GPS ham jarayon bilan tugaydi
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_state = {"started": False}


def enabled() -> bool:
    return (os.environ.get("GPS_AUTOSTART") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def start_background() -> bool:
    """GPS (TCP listener + HTTP API) ni daemon thread'larda ishga tushiradi.

    Bir marta ishga tushadi (idempotent). Hech qachon exception otmaydi —
    chaqiruvchi (dashboard) har doim davom etadi.
    """
    if _state["started"]:
        return True
    if not enabled():
        log.info("GPS autostart o'chirilgan (GPS_AUTOSTART=0)")
        return False
    _state["started"] = True

    def _run() -> None:
        try:
            import asyncio

            from . import config, listener, storage
            from .api import run_api

            if not storage.init_schema():
                log.warning("GPS autostart: DB mavjud emas — GPS servis ishga tushmadi")
                return
            # Fon xizmatlari: RTSP relay + kunlik rollup + hisobot (har biri fail-safe)
            try:
                from . import relay, report, rollup

                relay.start_monitor(lambda: storage.list_cameras())
                rollup.start_monitor()
                report.start_monitor()
            except Exception:  # noqa: BLE001
                log.exception("GPS fon xizmatlari ishga tushmadi (davom etamiz)")
            httpd = run_api(config.GPS_API_HOST, config.GPS_API_PORT)
            threading.Thread(
                target=httpd.serve_forever,
                daemon=True,
                name="bm-gps-api",
            ).start()
            log.info(
                "GPS autostart: kuzatuv UI http://<host>:%s (TCP %s)",
                config.GPS_API_PORT,
                config.GPS_TCP_PORT,
            )
            # TCP listener — shu thread'da bloklovchi ishlaydi (daemon).
            asyncio.run(listener.run_tcp(config.GPS_TCP_HOST, config.GPS_TCP_PORT))
        except OSError as exc:
            log.warning(
                "GPS autostart: port band yoki tarmoq xatosi (%s) — "
                "alohida GPS servis ishlab turgan bo'lishi mumkin",
                exc,
            )
        except Exception:  # noqa: BLE001
            log.exception("GPS autostart xatosi — dashboard davom etadi")

    threading.Thread(target=_run, daemon=True, name="bm-gps-autostart").start()
    return True
