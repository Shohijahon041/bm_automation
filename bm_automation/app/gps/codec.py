"""Teltonika AVL (Codec 8 / Codec 8 Extended) dekoderi.

FMB920 TCP orqali quyidagi oqimni yuboradi:

1. IMEI handshake:  <2B length><IMEI ASCII>  →  server javob: 0x01 (ok)
2. AVL paket:       <4B zero preamble><4B data length><CodecID 0x08|0x8E>
                    <N x record><1B record count>
3. Server javobi:   <4B accepted record count>
4. Keepalive:       0x64 (codec id yagona bayt) → javob: 0x64

Record tuzilishi (Codec 8/8E):
  Timestamp  8B (ms since epoch, big-endian)
  Priority   1B
  GPS element 15B:
    Longitude  4B int32 (deg * 10^7)
    Latitude   4B int32 (deg * 10^7)
    Altitude   2B int16 (metr)
    Angle      2B uint16 (0-360)
    Satellites 1B
    Speed      2B uint16 (km/h)
  (Codec 8E) IO element full: event_id 2B, N_total 2B, then 4 groups
  (Codec 8)  IO element:       event_id 1B, N_total 1B, then 4 groups
  IO group: <N 2B><N x (id 2B, value 1B)> ... value size 1/2/4/8B

Bu modul FAQAT dekodlash bilan shug'ullanadi — tarmoq va DB kodidan xoli.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class CodecError(ValueError):
    """Notogri/ikkilanuvchi AVL baytlari."""


@dataclass
class AvlRecord:
    """Bitta AVL yozuv — FMB920 dan kelgan bitta pozitsiya/voqea."""

    timestamp: datetime
    priority: int
    longitude: float
    latitude: float
    altitude: int
    angle: int
    satellites: int
    speed: int
    event_id: int = 0
    io: dict = field(default_factory=dict)

    @property
    def ignition(self) -> int:
        # FMB920: IDX 239 = External Voltage (mains present),
        #         IDX 240 = Ignition (engine on).
        if "240" in self.io:
            return 1 if self.io["240"] else 0
        if "239" in self.io:
            return 1 if self.io["239"] else 0
        return 0

    @property
    def movement(self) -> int:
        # IDX 240 (ignition) yoki speed > 5 km/h → harakat bor.
        if self.ignition:
            return 1
        return 1 if self.speed > 5 else 0


@dataclass
class ParsedFrame:
    """Bitta TCP frame natijasi."""

    kind: str  # "imei" | "avl" | "keepalive" | "empty"
    imei: str = ""
    records: list = field(default_factory=list)
    codec_id: int = 0
    accepted: int = 0  # server javobi uchun


def decode_imei_handshake(buf: bytes) -> str | None:
    """IMEI handshake'ni o'qiydi. Yetarli bayt bo'lmasa None.

    Format: 2B big-endian length (IMEI ASCII uzunligi) + IMEI.
    """
    if len(buf) < 2:
        return None
    (length,) = struct.unpack(">H", buf[:2])
    if length == 0 or length > 16:
        raise CodecError(f"IMEI uzunligi notogri: {length}")
    if len(buf) < 2 + length:
        return None
    imei_raw = buf[2 : 2 + length]
    try:
        imei = imei_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise CodecError("IMEI ASCII emas") from exc
    if not imei.isdigit():
        raise CodecError(f"IMEI raqam emas: {imei!r}")
    return imei


def decode_frame(buf: bytes) -> ParsedFrame:
    """TCP buferni tahlil qiladi: AVL paket / keepalive / bo'sh.

    Yetarli ma'lumot bo'lmasa `kind="empty"` qaytaradi (qismlab kelgan paket).
    Notogri baytlar CodecError — chaqiruvchi ulanishni uzadi.
    """
    if not buf:
        return ParsedFrame(kind="empty")

    # Keepalive: yagona 0x64 bayt.
    if buf == b"\x64":
        return ParsedFrame(kind="keepalive", codec_id=0x64)

    # AVL paket: 4B preamble (nol) + 4B data length.
    if len(buf) < 8:
        # IMEI handshake'ga o'xshagan qisqa frame bo'lishi mumkin — empty.
        return ParsedFrame(kind="empty")

    preamble = buf[:4]
    (data_len,) = struct.unpack(">I", buf[4:8])
    if data_len == 0 or data_len > 8 * 1024 * 1024:
        raise CodecError(f"AVL data length notogri: {data_len}")
    if len(buf) < 8 + data_len:
        return ParsedFrame(kind="empty")  # hali to'liq kelmagan

    body = buf[8 : 8 + data_len]
    if preamble != b"\x00\x00\x00\x00":
        # Ba'zi qurilmalar preamble'ni nol qilib yuboradi, lekin biz baribir
        # codec id tekshiramiz — notogri bo'lsa xato.
        raise CodecError("AVL preamble nol emas")

    codec_id = body[0]
    if codec_id not in (0x08, 0x8E):
        raise CodecError(f"Codec id qo'llab-quvvatlanmaydi: {codec_id:#x}")

    records, _ = _decode_records(body[1:], codec8e=(codec_id == 0x8E))
    # Yakuniy 1B record count tekshiruvi
    expected = body[-1]
    if len(records) != expected:
        log.warning(
            "Record soni mos kelmadi: %d vs %d (codec %#x)",
            len(records), expected, codec_id,
        )
    return ParsedFrame(kind="avl", records=records, codec_id=codec_id,
                       accepted=len(records))


def _decode_records(body: bytes, codec8e: bool) -> tuple[list, int]:
    """Codec body'dan (codec_id dan keyingi qism) recordlarni o'qiydi."""
    records: list = []
    off = 0

    if codec8e:
        # 2B record count
        if len(body) < 2:
            raise CodecError("Codec8E body qisqa")
        (count,) = struct.unpack(">H", body[:2])
        off = 2
    else:
        if len(body) < 1:
            raise CodecError("Codec8 body qisqa")
        count = body[0]
        off = 1

    for _ in range(count):
        if len(body) < off + 8 + 1:
            raise CodecError("Record sarlavhasi qisqa")
        (ts_ms,) = struct.unpack(">Q", body[off : off + 8])
        off += 8
        priority = body[off]
        off += 1

        # GPS element 15B
        if len(body) < off + 15:
            raise CodecError("GPS element qisqa")
        (lng_raw, lat_raw, alt, angle) = struct.unpack(
            ">iiH H", body[off : off + 12]
        )
        satellites = body[off + 12]
        (speed,) = struct.unpack(">H", body[off + 13 : off + 15])
        off += 15

        # IO elementlar
        if codec8e:
            if len(body) < off + 2:
                raise CodecError("Codec8E IO header qisqa")
            (event_id,) = struct.unpack(">H", body[off : off + 2])
            off += 2
            (n_total,) = struct.unpack(">H", body[off : off + 2])
            off += 2
            n1, n2, n4, n8 = _unpack_4x_n8e(body, off)
            off += 8
        else:
            if len(body) < off + 1:
                raise CodecError("Codec8 IO header qisqa")
            event_id = body[off]
            off += 1
            if len(body) < off + 1:
                raise CodecError("Codec8 N_total qisqa")
            n_total = body[off]
            off += 1
            n1, n2, n4, n8 = _unpack_4x_n8(body, off)
            off += 4

        io: dict = {}
        for size, n in ((1, n1), (2, n2), (4, n4), (8, n8)):
            if n == 0:
                continue
            for _ in range(n):
                id_len = 2 if codec8e else 1
                if len(body) < off + id_len + size:
                    raise CodecError("IO element qisqa")
                if codec8e:
                    (io_id,) = struct.unpack(">H", body[off : off + 2])
                else:
                    io_id = body[off]
                off += id_len
                raw = body[off : off + size]
                off += size
                if size == 1:
                    val = raw[0]
                elif size == 2:
                    (val,) = struct.unpack(">H", raw)
                elif size == 4:
                    (val,) = struct.unpack(">I", raw)
                else:
                    (val,) = struct.unpack(">Q", raw)
                io[str(io_id)] = val

        records.append(
            AvlRecord(
                timestamp=datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc),
                priority=priority,
                longitude=lng_raw / 1e7,
                latitude=lat_raw / 1e7,
                altitude=alt,
                angle=angle,
                satellites=satellites,
                speed=speed,
                event_id=event_id,
                io=io,
            )
        )

    return records, off


def _unpack_4x_n8e(body: bytes, off: int) -> tuple[int, int, int, int]:
    """Codec8E: 4 x 2B group counts (1B, 2B, 4B, 8B value groups)."""
    if len(body) < off + 8:
        raise CodecError("Codec8E group counts qisqa")
    n1, n2, n4, n8 = struct.unpack(">HHHH", body[off : off + 8])
    return n1, n2, n4, n8


def _unpack_4x_n8(body: bytes, off: int) -> tuple[int, int, int, int]:
    """Codec8: 4 x 1B group counts."""
    if len(body) < off + 4:
        raise CodecError("Codec8 group counts qisqa")
    return body[off], body[off + 1], body[off + 2], body[off + 3]


def encode_imei_response(ok: bool = True) -> bytes:
    """IMEI handshake javobi: 1B (0x01 ok / 0x00 reject)."""
    return b"\x01" if ok else b"\x00"


def encode_avl_response(accepted: int) -> bytes:
    """AVL javobi: 4B accepted record count."""
    return struct.pack(">I", accepted)


def encode_keepalive_response() -> bytes:
    """Keepalive javobi: 0x64."""
    return b"\x64"
