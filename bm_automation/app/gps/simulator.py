"""FMB920 simulyatori — haqiqiy qurilma oqimini imitatsiya qiladi.

Test uchun: Toshkent bo'ylab harakatlanuvchi soxta avtobus AVL paketlarini
yuboradi (Codec 8 Extended, IMEI handshake bilan).

Ishga tushirish:
    python -m bm_automation.app.gps.simulator --imei 350424106661230
    python -m bm_automation.app.gps.simulator --imei X --host 127.0.0.1 --port 5027 \
        --interval 2 --count 60
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import time
from datetime import datetime, timezone

# Toshkent markazi (BM avtobuslar yo'nalishi taxminiy koordinatalari)
TASHKENT = (41.3111, 69.2797)


def _io_block_8e(event_id: int, io: dict) -> bytes:
    """Codec 8E IO elementlar bloki (2B id'lar)."""
    g1 = {k: v for k, v in io.items() if 0 < v <= 0xFF and v >= 0 and v < 256}
    # Biz sodda qilamiz: barcha qiymatlarni 1B guruhga joylaymiz
    ids1 = sorted(io.keys())
    out = struct.pack(">H", event_id)
    out += struct.pack(">H", len(ids1))
    out += struct.pack(">HHHH", len(ids1), 0, 0, 0)
    for k in ids1:
        out += struct.pack(">H", int(k)) + struct.pack(">B", int(io[k]) & 0xFF)
    return out


def build_avl_8e(records: list) -> bytes:
    """records: [(ts_ms, lng, lat, alt, angle, sat, speed, io_dict), ...]"""
    body = b"\x8e"
    body += struct.pack(">H", len(records))
    for (ts_ms, lng, lat, alt, angle, sat, speed, io) in records:
        body += struct.pack(">Q", ts_ms)
        body += struct.pack(">B", 1)  # priority
        body += struct.pack(">i", int(lng * 1e7))
        body += struct.pack(">i", int(lat * 1e7))
        body += struct.pack(">h", alt)
        body += struct.pack(">H", angle)
        body += struct.pack(">B", sat)
        body += struct.pack(">H", speed)
        body += _io_block_8e(1, io)
    body += struct.pack(">H", len(records))
    return struct.pack(">I", 0) + struct.pack(">I", len(body)) + body


def simulate(imei: str, host: str, port: int, interval: float, count: int) -> None:
    lat, lng = TASHKENT
    sock = socket.create_connection((host, port), timeout=10)

    # 1) IMEI handshake
    imei_bytes = imei.encode("ascii")
    sock.sendall(struct.pack(">H", len(imei_bytes)) + imei_bytes)
    resp = sock.recv(1)
    if resp != b"\x01":
        print(f"IMEI rad etildi: {resp!r} - GPS_AUTO_REGISTER yoqilmagan bolishi mumkin")
        return
    print(f"Ulandi: {host}:{port} IMEI={imei}")

    # 2) AVL paketlar — aylana bo'ylab harakat
    sent = 0
    batch = []
    start = time.time()
    while sent < count:
        t = time.time() - start
        # aylana trajektoriya (Toshkent atrofida ~2km radius)
        angle_deg = (t * 10) % 360
        rad = math.radians(angle_deg)
        cur_lat = lat + 0.018 * math.sin(rad)
        cur_lng = lng + 0.028 * math.cos(rad)
        ts_ms = int(time.time() * 1000)
        io = {
            "239": 1,  # External Voltage (yonilg'i/mains)
            "240": 1,  # Ignition
            "66": int(70 + 20 * math.sin(rad)),  # analog fuel (taxminiy)
        }
        batch.append((ts_ms, cur_lng, cur_lat, 430, int(angle_deg), 12,
                      max(0, int(45 + 25 * math.cos(rad))), io))
        sent += 1

        if len(batch) >= 5 or sent >= count:
            sock.sendall(build_avl_8e(batch))
            ack = sock.recv(4)
            (accepted,) = struct.unpack(">I", ack)
            print(f"  +{len(batch)} record -> accepted={accepted}")
            batch = []
        time.sleep(interval)

    # 3) keepalive test
    sock.sendall(b"\x64")
    time.sleep(0.5)
    print("Keepalive yuborildi")
    sock.close()
    print(f"Tugadi: {sent} record yuborildi")


def main() -> int:
    ap = argparse.ArgumentParser(description="FMB920 AVL simulyator")
    ap.add_argument("--imei", default="350424106661230")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5027)
    ap.add_argument("--interval", type=float, default=1.0,
                    help="recordlar orasidagi sekund")
    ap.add_argument("--count", type=int, default=60)
    args = ap.parse_args()
    simulate(args.imei, args.host, args.port, args.interval, args.count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
