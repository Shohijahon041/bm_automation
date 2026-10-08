"""GPS codec testlari — Codec 8 va Codec 8 Extended."""

from __future__ import annotations

import struct

from bm_automation.app.gps import codec, simulator

# VPS'da haqiqiy Teltonika qurilmasidan (IMEI 353742370197154) ushlangan
# Codec 8E paket: 768B data + 4B CRC, 9 record, 12 IO har birida.
REAL_FRAME_HEX = (
    "00000000000003008e09000001a11b9a04a8002b1bc74f1853a84601e900001300000000"
    "000c000500ef0100f00000150500c800004501000500b5000800b6000500426a9800430f"
    "c500440066000200f10000a98f001002a232b700000000000001a11baa96d8002b1bc74f"
    "1853a84601da00001200000000000c000500ef0100f00000150500c800004501000500b5"
    "000800b6000600426a1d00430ffa0044003d000200f10000a98f001002a232b700000000"
    "000001a11baa96e2002b1bc9fa1853980f01da012012000000f0000c000500ef0100f001"
    "00150500c800004501000500b5000800b6000600426a6e004310000044003e000200f100"
    "00a98f001002a232b700000000000001a11baab230002b1bc53918539b1f01d801161200"
    "090000000c000500ef0100f00100150500c800004501000500b5000a00b6000600426a87"
    "004310060044003f000200f10000a98f001002a232be00000000000001a11bab4a88002b"
    "1bcc631853a22701d600431200070000000c000500ef0100f00100150500c80000450100"
    "0500b5000800b6000600426a94004310070044003e000200f10000a98f001002a232d600"
    "000000000001a11babb7e8002b1bcf51185397ee01d400ab13000b0000000c000500ef01"
    "00f00100150500c800004501000500b5000800b6000500426a94004310070044003d0002"
    "00f10000a98f001002a232fd00000000000001a11babbbd0002b1bceed185395c801d300"
    "ba13000c0000000c000500ef0100f00100150500c800004501000500b5000900b6000500"
    "426a92004310070044003e000200f10000a98f001002a2330300000000000001a11bab"
    "cb70002b1bc932185390c501d000c61300060000000c000500ef0100f00100150500c800"
    "004501000500b5000a00b6000500426a91004310080044003c000200f10000a98f001002"
    "a2331900000000000001a11bad1778002b1bc73e18538e6d01ce00ed12000000f0000c00"
    "0500ef0100f00000150500c800004501000500b5000800b6000500426abd0043100c0044"
    "003b000200f10000a98f001002a233190000000009000052d5"
)


def test_decode_real_codec8e_frame():
    frame = codec.decode_frame(bytes.fromhex(REAL_FRAME_HEX))
    assert frame.kind == "avl"
    assert frame.codec_id == 0x8E
    assert len(frame.records) == 9

    r0 = frame.records[0]
    assert round(r0.longitude, 5) == 72.32408
    assert round(r0.latitude, 5) == 40.81358
    assert r0.satellites == 19
    assert r0.speed == 0
    assert r0.event_id == 0
    assert len(r0.io) == 12
    assert r0.io["239"] == 1
    assert r0.io["240"] == 0
    assert r0.io["66"] == 27288
    assert r0.io["16"] == 44184247

    # Ignition recordlari (240=1) mavjud
    assert any(r.io.get("240") == 1 for r in frame.records)


def test_decode_requires_full_frame_including_crc():
    data = bytes.fromhex(REAL_FRAME_HEX)
    # CRC (oxirgi 4B) kelmagan bo'lsa — hali to'liq emas
    assert codec.decode_frame(data[:-4]).kind == "empty"
    assert codec.decode_frame(data).kind == "avl"


def test_decode_handles_multiple_frames_in_buffer():
    data = bytes.fromhex(REAL_FRAME_HEX)
    buf = data + data
    f1 = codec.decode_frame(buf)
    assert len(f1.records) == 9
    f2 = codec.decode_frame(buf[len(data):])
    assert len(f2.records) == 9


def test_simulator_roundtrip():
    ts = 1_700_000_000_000
    io = {"239": 1, "240": 1, "66": 123}
    packet = simulator.build_avl_8e(
        [(ts, 69.2797, 41.3111, 430, 90, 12, 45, io)]
    )
    frame = codec.decode_frame(packet)
    assert frame.kind == "avl"
    assert frame.codec_id == 0x8E
    assert len(frame.records) == 1
    r = frame.records[0]
    assert round(r.longitude, 4) == 69.2797
    assert round(r.latitude, 4) == 41.3111
    assert r.speed == 45
    assert r.satellites == 12
    assert r.io == {"239": 1, "240": 1, "66": 123}
    assert r.timestamp.timestamp() == ts / 1000.0


def test_frame_len_includes_crc():
    from bm_automation.app.gps.listener import _frame_len

    data = bytes.fromhex(REAL_FRAME_HEX)
    assert _frame_len(data) == len(data)
    data_len = struct.unpack(">I", data[4:8])[0]
    assert data_len == 768
