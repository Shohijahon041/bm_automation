"""Teltonika TCP listener — FMB920 qurilmalari AVL paketlarini qabul qiladi.

Har bir ulanish — bitta qurilma. Oqim:
  1) qurilma IMEI yuboradi  → biz 0x01 (ok) qaytaramiz
  2) qurilma AVL paket yuboradi → biz accepted-count qaytaramiz
  3) davom etadi; 0x64 keepalive → 0x64 javob
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import codec, config, storage

log = logging.getLogger(__name__)

_CONNECTED: set = set()
_STATS = {"packets": 0, "records": 0, "errors": 0, "started": None}


def stats() -> dict:
    return {
        "connected": len(_CONNECTED),
        **_STATS,
        "uptime_s": (
            int(datetime.now(timezone.utc).timestamp() - _STATS["started"])
            if _STATS["started"]
            else 0
        ),
    }


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    imei = ""
    buf = b""
    try:
        _CONNECTED.add(writer)

        # ---- IMEI handshake ----
        while len(buf) < 2 and not reader.at_eof():
            buf += await asyncio.wait_for(reader.read(1024), timeout=30)
        imei = codec.decode_imei_handshake(buf)
        if not imei:
            return
        buf = buf[2 + len(imei):]

        if not storage.device_allowed(imei):
            log.warning("GPS: IMEI %s ruxsat etilmagan (%s) — uzildi", imei, peer)
            writer.write(codec.encode_imei_response(False))
            await writer.drain()
            return

        writer.write(codec.encode_imei_response(True))
        await writer.drain()
        log.info("GPS: qurilma ulandi IMEI=%s (%s)", imei, peer)

        # ---- AVL frames ----
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=120)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf += chunk

            # Buferdan ketma-ket to'liq frame'larni o'qiymiz
            while True:
                try:
                    frame = codec.decode_frame(buf)
                except codec.CodecError as exc:
                    log.warning("GPS: notogri frame (%s): %s", imei, exc)
                    _STATS["errors"] += 1
                    buf = b""  # ulanishni davom ettirish xavfsizroq — bufer tozalanadi
                    break
                if frame.kind == "empty":
                    break
                if frame.kind == "keepalive":
                    buf = buf[1:]
                    writer.write(codec.encode_keepalive_response())
                    await writer.drain()
                    continue
                # AVL paket
                consumed = 8 + _frame_len(buf)
                buf = buf[consumed:]
                _STATS["packets"] += 1
                n = 0
                try:
                    if storage.device_known(imei) or config.GPS_AUTO_REGISTER:
                        if not storage.device_known(imei):
                            storage.register_device(imei)
                        n = storage.insert_positions(imei, frame.records)
                        if frame.records:
                            r0 = frame.records[-1]
                            storage.upsert_device_position(
                                imei, r0.timestamp, r0.latitude, r0.longitude,
                                r0.speed, r0.ignition, r0.satellites,
                            )
                            if n:
                                try:
                                    from . import geofence, speed

                                    geofence.check_position(
                                        imei, r0.latitude, r0.longitude,
                                        r0.timestamp,
                                    )
                                    speed.check_position(
                                        imei, r0.latitude, r0.longitude,
                                        r0.speed, r0.timestamp,
                                    )
                                except Exception:  # noqa: BLE001
                                    log.exception("nazorat hook xatosi (%s)", imei)
                except Exception:  # noqa: BLE001
                    log.exception("GPS: DB yozishda xato (%s)", imei)
                    _STATS["errors"] += 1
                _STATS["records"] += n
                writer.write(codec.encode_avl_response(len(frame.records)))
                await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except asyncio.TimeoutError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("GPS: ulanishda kutilmagan xato (%s)", imei or peer)
    finally:
        _CONNECTED.discard(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        if imei:
            log.info("GPS: qurilma uzildi IMEI=%s", imei)


def _frame_len(buf: bytes) -> int:
    """AVL frame'ning umumiy uzunligi (8 + data_len)."""
    import struct
    (data_len,) = struct.unpack(">I", buf[4:8])
    return 8 + data_len


async def run_tcp(host: str, port: int) -> None:
    _STATS["started"] = datetime.now(timezone.utc).timestamp()
    server = await asyncio.start_server(_handle, host, port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    log.info("GPS TCP listener: %s (Teltonika AVL)", addrs)
    async with server:
        await server.serve_forever()
