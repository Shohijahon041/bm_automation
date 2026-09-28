"""GPS servisi: TCP listener (Teltonika) + HTTP API bir jarayonda.

Ishga tushirish:
    python -m bm_automation.app.gps

Env (ixtiyoriy):
    GPS_TCP_PORT=5027  GPS_API_PORT=8081  GPS_AUTO_REGISTER=1
    GPS_RETENTION_DAYS=30  GPS_IMEI_WHITELIST=imei1,imei2
"""

from __future__ import annotations

import asyncio
import logging
import threading

from . import config, listener, relay, report, rollup, storage
from .api import run_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bm.gps")


def main() -> int:
    log.info("BM GPS servis ishga tushmoqda...")
    if not storage.init_schema():
        log.error("DB mavjud emas — servis to'xtatildi")
        return 1

    try:
        storage.purge_old_positions()
    except Exception:  # noqa: BLE001
        log.exception("Boshlang'ich purge xatosi (davom etamiz)")

    # Fon xizmatlari: RTSP relay nazorati + kunlik rollup + hisobot
    try:
        relay.start_monitor(lambda: storage.list_cameras())
    except Exception:  # noqa: BLE001
        log.exception("relay monitor ishga tushmadi")
    try:
        rollup.start_monitor()
    except Exception:  # noqa: BLE001
        log.exception("rollup monitor ishga tushmadi")
    try:
        report.start_monitor()
    except Exception:  # noqa: BLE001
        log.exception("report monitor ishga tushmadi")

    httpd = run_api(config.GPS_API_HOST, config.GPS_API_PORT)
    api_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    api_thread.start()

    try:
        # TCP listener — asosiy (bloklovchi) oqim.
        asyncio.run(listener.run_tcp(config.GPS_TCP_HOST, config.GPS_TCP_PORT))
    except KeyboardInterrupt:
        log.info("To'xtatilmoqda (Ctrl+C)...")
    finally:
        httpd.shutdown()
        log.info("GPS servis to'xtadi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
