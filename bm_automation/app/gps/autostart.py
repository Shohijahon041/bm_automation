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


async def _run_listeners(config, listener, jt808, jt1078) -> None:
    """Teltonika + (ixtiyoriy) JT808/JT1078 TCP listener'lari birga.

    Asosiy Teltonika listener'i hech qachon JT xatosi tufayli o'lmasligi uchun
    JT listener'lari alohida himoyalangan (port band bo'lsa faqat log).
    """
    import asyncio

    async def _guarded(name, coro):
        try:
            await coro
        except OSError as exc:
            log.warning("GPS autostart: %s listener ishga tushmadi (%s)",
                        name, exc)
        except Exception:  # noqa: BLE001
            log.exception("GPS autostart: %s listener xatosi", name)

    tasks = [asyncio.create_task(
        listener.run_tcp(config.GPS_TCP_HOST, config.GPS_TCP_PORT))]
    if getattr(config, "JT808_ENABLED", False):
        tasks.append(asyncio.create_task(_guarded(
            "JT808",
            jt808.run_tcp(config.GPS_TCP_HOST, config.JT808_TCP_PORT))))
        tasks.append(asyncio.create_task(_guarded(
            "JT1078",
            jt1078.run_tcp(config.GPS_TCP_HOST, config.JT1078_TCP_PORT))))
        log.info("GPS autostart: JT808 %s / JT1078 media %s yoqildi",
                 config.JT808_TCP_PORT, config.JT1078_TCP_PORT)
    await asyncio.gather(*tasks)


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

    # Dashboard (uvicorn) root logger'ni sozlamaydi — GPS INFO loglari yo'qoladi.
    # Fayl+stdout handlerni o'rnatamiz, shunda qurilma ulanishlari logs/bm.log'ga tushadi.
    try:
        from ..utils.logger import setup_logging

        setup_logging()
    except Exception:  # noqa: BLE001
        pass

    def _run() -> None:
        try:
            import asyncio

            from . import config, jt1078, jt808, listener, storage
            from .api import run_api

            if not storage.init_schema():
                log.warning("GPS autostart: DB mavjud emas — GPS servis ishga tushmadi")
                return
            # Fon xizmatlari: RTSP relay + kunlik rollup + hisobot (har biri fail-safe)
            try:
                from . import offline, relay, report, rollup

                relay.start_monitor(lambda: storage.list_cameras())
                rollup.start_monitor()
                report.start_monitor()
                offline.start_monitor()
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
            # TCP listener'lar — shu thread'da bloklovchi ishlaydi (daemon).
            # Teltonika (5027) + JT808 signaling (6808) + JT1078 media (6107).
            asyncio.run(_run_listeners(config, listener, jt808, jt1078))
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
