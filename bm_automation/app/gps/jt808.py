"""JT/T 808 (2013/2019) protokol yadrosi — 4G kamera/tracker signaling listener'i.

Qurilma (kamera) 4G orqali O'ZI bizga ulanadi. Bu modul:
  * frame escape/unescape + XOR checksum (JT808 framing)
  * 2013 (12B sarlavha) va 2019 (21B, version bayt) farqlash
  * register(0x0100) / auth(0x0102) / heartbeat(0x0002) / location(0x0200)
    xabarlarini qabul qilib, platform javoblarini (0x8100 / 0x8001) qaytarish
  * qurilmani ro'yxatga olish + joyini saqlash (GPS xaritada ko'rinadi)
  * keyinroq video so'rash uchun 0x9101 / 0x9201 yuborish imkoni

Media (video) oqimi alohida JT1078 listener'ida (jt1078.py) qabul qilinadi.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from datetime import datetime, timezone

from . import config, storage

log = logging.getLogger(__name__)

# ---- xabar ID'lari ----
MSG_TERM_HEARTBEAT = 0x0002
MSG_TERM_GENERAL_RESP = 0x0001
MSG_TERM_REGISTER = 0x0100
MSG_TERM_AUTH = 0x0102
MSG_TERM_LOCATION = 0x0200
MSG_TERM_BATCH_LOCATION = 0x0704
MSG_TERM_DEVICE_INFO = 0x0104   # 0x8104 so'roviga javob: terminal paramlari
MSG_TERM_AV_RESOURCE_LIST = 0x1205  # qurilma xotirasidagi yozuvlar ro'yxati
MSG_PLAT_GENERAL_RESP = 0x8001
MSG_PLAT_REGISTER_RESP = 0x8100
MSG_PLAT_QUERY_PARAMS = 0x8104      # barcha terminal paramlarini so'rash
MSG_PLAT_AV_REQUEST = 0x9101        # jonli audio/video oqimini so'rash
MSG_PLAT_AV_CONTROL = 0x9102
MSG_PLAT_PLAYBACK_REQUEST = 0x9201  # yozuv (playback) oqimini so'rash
MSG_PLAT_PLAYBACK_CONTROL = 0x9202
MSG_PLAT_AV_RESOURCE_REQUEST = 0x9205  # xotiradagi yozuvlar ro'yxatini so'rash

# 0x9202 boshqaruv bayti qiymatlari
PB_CTRL_PAUSE = 0     # pauza
PB_CTRL_RESUME = 1    # davom ettirish
PB_CTRL_STOP = 2      # to'xtatish
PB_CTRL_FAST_FWD = 3  # oldinga
PB_CTRL_REWIND = 4    # orqaga
PB_CTRL_KEY_FWD = 5   # kalit kadr oldinga
PB_CTRL_KEY_REW = 6   # kalit kadr orqaga

_HDR_2013 = 12   # msg_id(2)+attr(2)+phone(6)+seq(2)
_HDR_2019 = 17   # msg_id(2)+attr(2)+ver(1)+phone(10)+seq(2)

_CONNECTED: dict = {}   # phone -> asyncio.StreamWriter
_VERSION: dict = {}     # phone -> 0 (2013) yoki 1 (2019) — chiqish frame formati
_LOOP = None            # listener asyncio loop'i (HTTP API shu orqali buyruq yuboradi)
_STATS = {"frames": 0, "locations": 0, "errors": 0, "started": None}
# phone -> {"at": epoch, "items": [resurs, ...]} — oxirgi 0x1205 javobi
_RESOURCES: dict = {}
# phone -> qurilma identifikatsiyasi/parametrlari (0x0100 ro'yxat + 0x0104 javob):
#   manufacturer, model, terminal_id, plate, imei, hw_version, fw_version,
#   term_type, gnss, comm, reg_hex, at
_DEVINFO: dict = {}


class Jt808Error(Exception):
    """Frame yoki xabar noto'g'ri bo'lsa."""


# ---------------------------------------------------------------- framing

def escape(data: bytes) -> bytes:
    """0x7d -> 7D 01, 0x7e -> 7D 02 (avval 7d, keyin 7e)."""
    return data.replace(b"\x7d", b"\x7d\x01").replace(b"\x7e", b"\x7d\x02")


def unescape(data: bytes) -> bytes:
    """7D 02 -> 7E, 7D 01 -> 7D."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0x7D and i + 1 < n:
            nxt = data[i + 1]
            if nxt == 0x02:
                out.append(0x7E)
                i += 2
                continue
            if nxt == 0x01:
                out.append(0x7D)
                i += 2
                continue
        out.append(b)
        i += 1
    return bytes(out)


def checksum(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
    return c


def bcd_decode(data: bytes) -> str:
    return "".join(f"{b:02x}" for b in data).lstrip("0") or "0"


def bcd_encode(digits: str, width: int) -> bytes:
    d = digits or "0"
    d = d.zfill(width * 2)[-width * 2:]
    return bytes(int(d[i:i + 2], 16) for i in range(0, width * 2, 2))


def iso_to_bcd6(value: str) -> bytes:
    """'YYYY-MM-DD[ T]HH:MM[:SS]' yoki 'YYYY-MM-DD' -> BCD[6] (YYMMDDHHMMSS).

    Yaroqsiz qiymatda hozirgi vaqt ishlatiladi.
    """
    text = (value or "").strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        dt = datetime.now()
    return bcd_encode(dt.strftime("%y%m%d%H%M%S"), 6)


def bcd6_to_iso(raw: bytes) -> str:
    """BCD[6] (YYMMDDHHMMSS) -> 'YYYY-MM-DDTHH:MM:SS'."""
    if len(raw) < 6:
        return ""
    y, mo, d = 2000 + (raw[0] >> 4) * 10 + (raw[0] & 0xF), \
        (raw[1] >> 4) * 10 + (raw[1] & 0xF), (raw[2] >> 4) * 10 + (raw[2] & 0xF)
    h, mi, s = (raw[3] >> 4) * 10 + (raw[3] & 0xF), \
        (raw[4] >> 4) * 10 + (raw[4] & 0xF), (raw[5] >> 4) * 10 + (raw[5] & 0xF)
    try:
        return datetime(y, mo, d, h, mi, s).isoformat()
    except ValueError:
        return ""


def build_frame(msg_id: int, phone: str, seq: int, body: bytes,
                version: int = 0) -> bytes:
    """Platforma -> qurilma frame'i (0x7e ... 0x7e)."""
    attr = len(body) & 0x3FF
    if version:
        attr |= 1 << 14
        header = (struct.pack(">HH", msg_id, attr) + b"\x01"
                  + bcd_encode(phone, 10) + struct.pack(">H", seq))
    else:
        header = (struct.pack(">HH", msg_id, attr)
                  + bcd_encode(phone, 6) + struct.pack(">H", seq))
    inner = header + body
    raw = inner + bytes([checksum(inner)])
    return b"\x7e" + escape(raw) + b"\x7e"


def parse_frame(raw: bytes) -> dict:
    """0x7e-lar orasidagi frame'ni parse qilish. dict qaytaradi."""
    if len(raw) < 2 or raw[0] != 0x7E or raw[-1] != 0x7E:
        raise Jt808Error("frame chegarasi noto'g'ri")
    inner = unescape(raw[1:-1])
    if len(inner) < 2:
        raise Jt808Error("frame juda qisqa")
    body_cs = inner[-1]
    payload = inner[:-1]
    if checksum(payload) != body_cs:
        raise Jt808Error("checksum mos emas")
    if len(payload) < 4:
        raise Jt808Error("sarlavha juda qisqa")
    msg_id, attr = struct.unpack(">HH", payload[0:4])
    version = 1 if (attr >> 14) & 1 else 0
    if version:
        if len(payload) < _HDR_2019:
            raise Jt808Error("2019 sarlavha qisqa")
        phone = bcd_decode(payload[5:15])
        (seq,) = struct.unpack(">H", payload[15:17])
        body = payload[_HDR_2019:]
    else:
        if len(payload) < _HDR_2013:
            raise Jt808Error("2013 sarlavha qisqa")
        phone = bcd_decode(payload[4:10])
        (seq,) = struct.unpack(">H", payload[10:12])
        body = payload[_HDR_2013:]
    return {"msg_id": msg_id, "seq": seq, "phone": phone,
            "body": body, "version": version}


def extract_frames(buf: bytes) -> tuple:
    """Buferdan to'liq frame'larni ajratib olish. (frames, qoldiq_buf)."""
    frames = []
    while True:
        start = buf.find(b"\x7e")
        if start < 0:
            return frames, b""
        end = buf.find(b"\x7e", start + 1)
        if end < 0:
            return frames, buf[start:]
        frames.append(buf[start:end + 1])
        buf = buf[end + 1:]


# ---------------------------------------------------------------- location

def parse_location(body: bytes) -> dict:
    """0x0200 tanasi: alarm/status/lat/lng/alt/speed/dir/BCD-time."""
    if len(body) < 28:
        raise Jt808Error("location tanasi qisqa")
    alarm, status = struct.unpack(">II", body[0:8])
    lat_raw, lng_raw = struct.unpack(">II", body[8:16])
    (alt,) = struct.unpack(">H", body[16:18])
    (speed,) = struct.unpack(">H", body[18:20])
    (direction,) = struct.unpack(">H", body[20:22])
    return {
        "alarm": alarm,
        "status": status,
        "lat": lat_raw / 1e6,
        "lng": lng_raw / 1e6,
        "altitude": alt,
        # JT808 tezlik 1/10 km/h
        "speed": speed / 10.0,
        "direction": direction,
        "ignition": bool(status & 0x01),
        "time_bcd": body[22:28].hex(),
    }


# ---------------------------------------------------------------- javoblar

def parse_resource_list(body: bytes) -> list:
    """0x1205 tanasi: seq(2) + total(4) + [kanal(1)+start[6]+end[6]+
    alarm(4)+resurs_turi(1)+oqim_turi(1)+xotira_turi(1)+hajm(4)] * N."""
    if len(body) < 6:
        raise Jt808Error("resurs ro'yxati qisqa")
    total = struct.unpack(">I", body[2:6])[0]
    items = []
    i = 6
    item_len = 1 + 6 + 6 + 4 + 1 + 1 + 1 + 4
    while i + item_len <= len(body):
        start = bcd6_to_iso(body[i + 1:i + 7])
        end = bcd6_to_iso(body[i + 7:i + 13])
        if not start or not end:
            i += item_len
            continue
        items.append({
            "channel": body[i],
            "start": start,
            "end": end,
            "alarm": struct.unpack(">I", body[i + 13:i + 17])[0],
            "resource_type": body[i + 17],
            "stream_type": body[i + 18],
            "memory_type": body[i + 19],
            "size": struct.unpack(">I", body[i + 20:i + 24])[0],
        })
        i += item_len
    return items[:total] if total else items


def resources(phone: str) -> dict:
    """Oxirgi 0x1205 javobi: {"at": epoch|0, "items": [...]}."""
    r = _RESOURCES.get(phone) or {}
    return {"at": r.get("at", 0), "items": list(r.get("items") or [])}


def clear_resources(phone: str) -> None:
    """Yangi so'rov oldidan keshlangan ro'yxatni tozalash."""
    _RESOURCES.pop(phone, None)


# ---------------------------------------------------- qurilma identifikatsiyasi

def _txt(raw: bytes) -> str:
    """Xitoy (GBK) yoki ASCII maydonini toza matnga aylantirish."""
    for enc in ("gbk", "ascii", "latin-1"):
        try:
            s = raw.decode(enc)
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        s = raw.decode("latin-1", "ignore")
    return s.replace("\x00", " ").strip()


def parse_register(body: bytes, version: int = 0) -> dict:
    """0x0100 ro'yxat tanasi: manufacturer/model/terminal_id/plate.

    Maydon uzunliklari versiyaga bog'liq (2013: 5/20/7, 2019: 11/30/30).
    Ishonchlilik uchun xom hex ham qaytariladi.
    """
    mlen, modlen, idlen = (11, 30, 30) if version else (5, 20, 7)
    out = {"reg_hex": body[:64].hex()}
    off = 4   # province(2) + city(2)
    if len(body) < off + mlen + modlen + idlen:
        return out
    out["manufacturer"] = _txt(body[off:off + mlen]); off += mlen
    out["model"] = _txt(body[off:off + modlen]); off += modlen
    out["terminal_id"] = _txt(body[off:off + idlen]); off += idlen
    if off < len(body):
        out["plate_color"] = body[off]; off += 1
        out["plate"] = _txt(body[off:])
    return out


def parse_device_info(body: bytes) -> dict:
    """0x0104 (0x8104 so'roviga javob): terminal parametrlari.

    Tana: ack_seq(2) + param_soni(1) + [param_id(4) + uzunlik(1) + qiymat].
    Bizni qiziqtirganlari: 0x01 tur, 0x02 IMEI, 0x03 apparat versiyasi,
    0x04 proshivka versiyasi, 0x05 GNSS moduli, 0x06 aloqa moduli.
    """
    out: dict = {}
    i = 3
    n = len(body)
    while i + 5 <= n:
        (pid,) = struct.unpack(">I", body[i:i + 4])
        plen = body[i + 4]
        val = body[i + 5:i + 5 + plen]
        if len(val) < plen:
            break
        i += 5 + plen
        if pid == 0x01 and plen >= 2:
            out["term_type"] = struct.unpack(">H", val[:2])[0]
        elif pid == 0x02:
            digits = "".join(c for c in val.decode("ascii", "ignore")
                             if c.isdigit())
            out["imei"] = digits or val.hex()
        elif pid == 0x03:
            out["hw_version"] = _txt(val)
        elif pid == 0x04:
            out["fw_version"] = _txt(val)
        elif pid == 0x05 and plen >= 1:
            out["gnss"] = val[0]
        elif pid == 0x06 and plen >= 1:
            out["comm"] = val[0]
    return out


def _merge_devinfo(phone: str, patch: dict) -> None:
    if not patch:
        return
    cur = _DEVINFO.setdefault(phone, {})
    cur.update({k: v for k, v in patch.items() if v not in (None, "")})
    cur["at"] = datetime.now(timezone.utc).timestamp()


def device_info(phone: str | None = None):
    """Keshlangan qurilma ma'lumoti: bitta phone yoki hammasi (dict)."""
    if phone:
        return dict(_DEVINFO.get(phone) or {})
    return {p: dict(v) for p, v in _DEVINFO.items()}


def _general_resp(phone: str, seq: int, resp_id: int, result: int = 0,
                  version: int = 0) -> bytes:
    body = struct.pack(">HHB", seq, resp_id, result)
    return build_frame(MSG_PLAT_GENERAL_RESP, phone, _next_seq(), body,
                       version)


def _register_resp(phone: str, seq: int, auth: bytes, version: int = 0) -> bytes:
    body = struct.pack(">HB", seq, 0) + auth
    return build_frame(MSG_PLAT_REGISTER_RESP, phone, _next_seq(), body,
                       version)


_SEQ = 0


def _next_seq() -> int:
    global _SEQ
    _SEQ = (_SEQ + 1) & 0xFFFF
    return _SEQ


# ---------------------------------------------------------------- server

def stats() -> dict:
    return {
        "connected": len(_CONNECTED),
        **_STATS,
        "uptime_s": (
            int(datetime.now(timezone.utc).timestamp() - _STATS["started"])
            if _STATS["started"] else 0
        ),
    }


def connected_phones() -> list:
    return sorted(_CONNECTED.keys())


async def _handle(reader: asyncio.StreamReader,
                  writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    phone = ""
    version = 0
    buf = b""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=120)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
            frames, buf = extract_frames(buf)
            for fr in frames:
                _STATS["frames"] += 1
                try:
                    m = parse_frame(fr)
                except Jt808Error as exc:
                    log.warning("JT808: yaroqsiz frame (%s): %s", peer, exc)
                    _STATS["errors"] += 1
                    continue
                phone = m["phone"]
                version = m["version"]
                _CONNECTED[phone] = writer
                _VERSION[phone] = version
                await _dispatch(writer, m)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("JT808: ulanishda xato (%s)", phone or peer)
    finally:
        if phone and _CONNECTED.get(phone) is writer:
            _CONNECTED.pop(phone, None)
            _VERSION.pop(phone, None)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        if phone:
            log.info("JT808: qurilma uzildi phone=%s", phone)


async def _dispatch(writer: asyncio.StreamWriter, m: dict) -> None:
    phone, seq, mid, body, ver = (m["phone"], m["seq"], m["msg_id"],
                                  m["body"], m["version"])
    if mid == MSG_TERM_REGISTER:
        auth = f"BM{phone}".encode()
        _ensure_device(phone)
        try:
            info = parse_register(body, ver)
            _merge_devinfo(phone, info)
            log.info("JT808: ro'yxatdan o'tdi phone=%s manufacturer=%r "
                     "model=%r plate=%r hex=%s", phone,
                     info.get("manufacturer"), info.get("model"),
                     info.get("plate"), info.get("reg_hex"))
        except Exception:  # noqa: BLE001
            log.exception("JT808: ro'yxat parse xato phone=%s", phone)
        writer.write(_register_resp(phone, seq, auth, ver))
        await writer.drain()
    elif mid == MSG_TERM_DEVICE_INFO:
        # 0x8104 so'roviga javob — terminal paramlari (IMEI/HW/FW/...).
        try:
            info = parse_device_info(body)
            _merge_devinfo(phone, info)
            log.info("JT808: qurilma paramlari phone=%s imei=%s hw=%r fw=%r "
                     "type=%s", phone, info.get("imei"),
                     info.get("hw_version"), info.get("fw_version"),
                     info.get("term_type"))
        except Exception:  # noqa: BLE001
            log.exception("JT808: 0x0104 parse xato phone=%s", phone)
        writer.write(_general_resp(phone, seq, mid, 0, ver))
        await writer.drain()
    elif mid in (MSG_TERM_AUTH, MSG_TERM_HEARTBEAT):
        if mid == MSG_TERM_AUTH:
            _merge_devinfo(phone, {"auth_hex": body[:40].hex()})
        writer.write(_general_resp(phone, seq, mid, 0, ver))
        await writer.drain()
    elif mid == MSG_TERM_LOCATION:
        try:
            loc = parse_location(body)
        except Jt808Error as exc:
            log.warning("JT808: location parse xato (%s): %s", phone, exc)
            _STATS["errors"] += 1
            return
        _STATS["locations"] += 1
        _ensure_device(phone)
        try:
            ts = datetime.now(timezone.utc)
            storage.upsert_device_position(
                phone, ts, loc["lat"], loc["lng"],
                int(round(loc["speed"])), int(loc["ignition"]), 0,
            )
        except Exception:  # noqa: BLE001
            log.exception("JT808: joy saqlashda xato (%s)", phone)
        writer.write(_general_resp(phone, seq, mid, 0, ver))
        await writer.drain()
    elif mid == MSG_TERM_AV_RESOURCE_LIST:
        try:
            # Qurilma ro'yxatni bir nechta 0x1205 paketda yuborishi mumkin —
            # barchasini merge qilamiz (dublikatlarni olib tashlab).
            items = parse_resource_list(body)
            prev = _RESOURCES.get(phone) or {}
            seen = set()
            merged = []
            for it in list(prev.get("items") or []) + items:
                k = (it.get("channel"), it.get("start"), it.get("end"),
                     it.get("size"))
                if k in seen:
                    continue
                seen.add(k)
                merged.append(it)
            merged.sort(key=lambda x: (x.get("start") or "", x.get("channel") or 0))
            _RESOURCES[phone] = {
                "at": datetime.now(timezone.utc).timestamp(),
                "items": merged,
            }
            log.info("JT808: resurs ro'yxati phone=%s -> %s paket, jami %s yozuv",
                     phone, len(items), len(merged))
        except Jt808Error as exc:
            log.warning("JT808: resurs ro'yxati parse xato (%s): %s",
                        phone, exc)
            _STATS["errors"] += 1
        writer.write(_general_resp(phone, seq, mid, 0, ver))
        await writer.drain()
    else:
        # boshqa xabarlar uchun umumiy javob (qurilma kutmoqda)
        writer.write(_general_resp(phone, seq, mid, 0, ver))
        await writer.drain()


def _ensure_device(phone: str) -> None:
    try:
        if storage.device_known(phone) or config.GPS_AUTO_REGISTER:
            if not storage.device_known(phone):
                storage.register_device(phone)
    except Exception:  # noqa: BLE001
        log.exception("JT808: qurilma ro'yxatga olishda xato (%s)", phone)


# ------------------------------------------- platforma -> qurilma buyruqlar

def _writer_for(phone: str):
    return _CONNECTED.get(phone)


def _ver(phone: str) -> int:
    return _VERSION.get(phone, 0)


async def _send(phone: str, msg_id: int, body: bytes) -> bool:
    """Qurilmaga frame yuborish (ulangan bo'lsa). Loop ichida chaqiriladi."""
    w = _writer_for(phone)
    if not w:
        return False
    w.write(build_frame(msg_id, phone, _next_seq(), body, _ver(phone)))
    await w.drain()
    return True


async def request_live_av(phone: str, host: str, tcp_port: int,
                          channel: int = 1, media_type: int = 0,
                          stream_type: int = 0) -> bool:
    """0x9101: qurilmaga 'video oqimini shu serverga yubor' deyish."""
    hb = (host or "").encode()
    body = (bytes([len(hb)]) + hb + struct.pack(">HH", tcp_port, 0)
            + bytes([channel, media_type, stream_type]))
    ok = await _send(phone, MSG_PLAT_AV_REQUEST, body)
    if ok:
        log.info("JT808: 0x9101 jonli video so'rildi phone=%s ch=%s -> %s:%s",
                 phone, channel, host, tcp_port)
    return ok


async def stop_live_av(phone: str, channel: int = 1) -> bool:
    """0x9102: jonli oqimni to'xtatish (control=0: yopish)."""
    body = bytes([channel & 0xFF, 0x00, 0x00, 0x00])
    ok = await _send(phone, MSG_PLAT_AV_CONTROL, body)
    if ok:
        log.info("JT808: 0x9102 video to'xtatildi phone=%s ch=%s", phone, channel)
    return ok


async def request_playback(phone: str, host: str, tcp_port: int,
                           start_bcd: bytes, end_bcd: bytes,
                           channel: int = 1) -> bool:
    """0x9201: yozuv (playback) oqimini so'rash.

    Qurilma yozuvni O'Z xotirasidan shu serverga oqim sifatida yuboradi —
    serverga saqlanmaydi (faqat HLS segmentlar orqali jonli uzatiladi).
    """
    hb = (host or "").encode()
    body = (bytes([len(hb)]) + hb + struct.pack(">HH", tcp_port, 0)
            + bytes([channel, 0, 0, 0, 1]) + start_bcd + end_bcd)
    ok = await _send(phone, MSG_PLAT_PLAYBACK_REQUEST, body)
    if ok:
        log.info("JT808: 0x9201 yozuv so'rildi phone=%s ch=%s -> %s:%s",
                 phone, channel, host, tcp_port)
    return ok


async def request_av_resource_list(phone: str, channel: int, start_bcd: bytes,
                                   end_bcd: bytes, resource_type: int = 0,
                                   stream_type: int = 0,
                                   memory_type: int = 0) -> bool:
    """0x9205: qurilma xotirasidagi yozuvlar ro'yxatini so'rash."""
    body = (bytes([channel & 0xFF]) + start_bcd + end_bcd
            + struct.pack(">I", 0)
            + bytes([resource_type & 0xFF, stream_type & 0xFF,
                     memory_type & 0xFF]))
    ok = await _send(phone, MSG_PLAT_AV_RESOURCE_REQUEST, body)
    if ok:
        log.info("JT808: 0x9205 resurs ro'yxati so'rildi phone=%s ch=%s",
                 phone, channel)
    return ok


async def playback_control(phone: str, channel: int, control: int,
                           speed: int = 0, time_bcd: bytes | None = None) -> bool:
    """0x9202: yozuv ijrosini boshqarish (pauza/davom/to'xtat/oldin/orqaga)."""
    body = (bytes([channel & 0xFF, control & 0xFF, speed & 0xFF])
            + (time_bcd or b"\x00" * 6))
    ok = await _send(phone, MSG_PLAT_PLAYBACK_CONTROL, body)
    if ok:
        log.info("JT808: 0x9202 yozuv boshqaruvi phone=%s ch=%s ctrl=%s",
                 phone, channel, control)
    return ok


# --- HTTP API (boshqa thread) uchun thread-safe sync wrapper'lar ---

def _run_coro(coro, timeout: float = 5.0):
    """Listener loop'ida koroutinani ishga tushirib natijasini qaytaradi."""
    if _LOOP is None or _LOOP.is_closed():
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, _LOOP)
        return fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001
        log.exception("JT808: buyruq yuborishda xato")
        return False


def send_live_request(phone: str, host: str, tcp_port: int,
                      channel: int = 1, stream_type: int = 0) -> bool:
    return bool(_run_coro(
        request_live_av(phone, host, tcp_port, channel,
                        media_type=0, stream_type=stream_type)))


def send_live_stop(phone: str, channel: int = 1) -> bool:
    return bool(_run_coro(stop_live_av(phone, channel)))


def send_playback(phone: str, host: str, tcp_port: int,
                  start_bcd: bytes, end_bcd: bytes, channel: int = 1) -> bool:
    return bool(_run_coro(
        request_playback(phone, host, tcp_port, start_bcd, end_bcd, channel)))


def send_resource_list(phone: str, channel: int, start_bcd: bytes,
                       end_bcd: bytes, resource_type: int = 0,
                       stream_type: int = 0, memory_type: int = 0) -> bool:
    clear_resources(phone)  # yangi so'rov: eski natija aralashmasin
    return bool(_run_coro(
        request_av_resource_list(phone, channel, start_bcd, end_bcd,
                                 resource_type, stream_type, memory_type)))


def send_playback_control(phone: str, channel: int, control: int,
                          speed: int = 0, time_bcd: bytes | None = None) -> bool:
    return bool(_run_coro(
        playback_control(phone, channel, control, speed, time_bcd)))


async def run_tcp(host: str, port: int) -> None:
    global _LOOP
    _LOOP = asyncio.get_running_loop()
    _STATS["started"] = datetime.now(timezone.utc).timestamp()
    server = await asyncio.start_server(_handle, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("JT808 listener: %s (2013/2019 + CMSV6 kameralar)", addrs)
    async with server:
        await server.serve_forever()
