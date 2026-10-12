"""JT/T 1078 media listener — 4G kameradan VIDEO oqimini qabul qilish.

Qurilma JT808 orqali 0x9101 (jonli) / 0x9201 (playback) so'rovini olgach,
video oqimini shu TCP portga (JT1078 RTP-simon paketlar) yuboradi. Bu modul:
  * paketlarni 0x30316364 flag + body-length bilan freymleydi (TCP
    birlashtirgan/bo'laklagan holatlarda ham to'g'ri)
  * sarlavhadan (video 30 / audio 26 / shaffof 18 bayt) SIM, kanal, data
    type, timestamp, payload oladi
  * subpaketlarni (birinchi/oraliq/oxirgi) yig'ib to'liq H.264 kadrini
    hosil qiladi; audio/shaffof paketlar tashlanadi
  * har bir (qurilma, kanal) uchun ffmpeg jarayonini yoqib, xom H.264'ni
    HLS segmentlariga aylantiradi: <HLS_DIR>/jt_<sim>_<ch>.m3u8
  * diagnostika uchun xom oqimni diskka ham yozadi (.h264 va raw_*.bin)

Sarlavha tartibi real CMSV6 qurilmasi raw dump'i orqali tasdiqlangan
(pastdagi HEADER_LEN izohiga qarang).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

# JT/T 1078 RTP-simon paket: har biri 0x30316364 flag bilan boshlanadi.
FLAG = b"\x30\x31\x63\x64"
# Sarlavha tuzilishi (JT/T 1078-2016 — real CMSV6 dump + standart bilan
# tasdiqlangan). MUHIM: sarlavha uzunligi paket TURIGA qarab FARQ qiladi:
#   [0:4]   flag 30316364
#   [4:8]   V/P/X/CC (1) + M/PT (1)   (PT 7 bit; PT=98 -> H.264)
#   [8:14]  SIM (6 bayt BCD)
#   [14]    kanal (logical channel)
#   [15]    data_type: yuqori 4 bit = tur, past 4 bit = subpaket
#   [16:24] timestamp (ms, 8 bayt)      — shaffof (0100) turida YO'Q
#   [24:26] oxirgi I-kadr oralig'i       — faqat video (I/P/B)
#   [26:28] oxirgi kadr oralig'i         — faqat video (I/P/B)
#   [last2] body uzunligi (2 bayt, big-endian, HAR DOIM oxirida)
#   [..]    body (H.264 Annex-B yoki boshqa data)
# Demak sarlavha uzunligi (flag bilan): video 30, audio 26, shaffof 18 bayt.
HEADER_LEN = 30           # video sarlavha (moslik uchun)
AUDIO_HEADER_LEN = 26
TRANSPARENT_HEADER_LEN = 18
# data_type bayti: yuqori 4 bit = kadr/ma'lumot turi, past 4 bit = subpaket.
DT_VIDEO_I = 0        # 0000 — I-kadr
DT_VIDEO_P = 1        # 0001 — P-kadr
DT_VIDEO_B = 2        # 0010 — B-kadr
DT_AUDIO = 3          # 0011 — audio (hozircha tashlanadi)
DT_TRANSPARENT = 4    # 0100 — shaffof/xizmat ma'lumoti (tashlanadi)
SUB_ATOMIC = 0        # bo'linmagan (butun kadr bitta paketda)
SUB_FIRST = 1         # birinchi subpaket
SUB_LAST = 2          # oxirgi subpaket
SUB_MIDDLE = 3        # oraliq subpaket
_VIDEO_TYPES = (DT_VIDEO_I, DT_VIDEO_P, DT_VIDEO_B)


def _header_len(data_type: int) -> int:
    """data_type bo'yicha sarlavha uzunligi (flag bilan).

    video (I/P/B) = 30, audio = 26, shaffof (0100) = 18 bayt.
    Noto'g'ri/notanish tur uchun video (eng katta) qaytariladi.
    """
    if data_type == DT_TRANSPARENT:
        return TRANSPARENT_HEADER_LEN
    if data_type == DT_AUDIO:
        return AUDIO_HEADER_LEN
    return HEADER_LEN

_STATS = {"packets": 0, "access_units": 0, "errors": 0, "started": None}
_STREAMS: dict = {}   # "key" -> {"bytes", "since", "sim", "channel", "kind"}
# Kamera media ulanishini qisqa-qisqa ochib yopadi (~20s). Har bir uzilishda
# ffmpeg'yopilib HLS playlist'ini ENDLIST bilan yakunlamasligi uchun muxerlar
# (sim,kanal,mode) bo'yicha DOIMIY saqlanadi; qayta ulanishda o'sha muxer davom
# etadi.
_MUXERS: dict = {}    # "key" -> _Muxer
# "{sim}_{ch}" -> expiry epoch. Yozuv (playback) so'ralganda shu (sim,kanal)
# uchun kelayotgan oqim "playback" deb belgilanadi (jonli bilan aralashmasin).
_PLAYBACK: dict = {}
MUXER_IDLE_TIMEOUT = 120.0   # sekund data kelmasa muxer yopiladi


def set_playback(sim: str, channel: int, seconds: int | None = None) -> None:
    """(sim, kanal) uchun kelayotgan oqimni yozuv (playback) deb belgilash."""
    secs = config.JT1078_PLAYBACK_WINDOW_S if seconds is None else seconds
    _PLAYBACK[f"{sim}_{channel}"] = _now() + max(1, int(secs))
    log.info("JT1078: playback rejimi yoqildi %s_%s (%ss)",
             sim, channel, secs)


def clear_playback(sim: str | None = None, channel: int | None = None) -> None:
    if sim is None:
        _PLAYBACK.clear()
    else:
        _PLAYBACK.pop(f"{sim}_{channel}", None)


def _playback_active(sim: str, channel: int) -> bool:
    exp = _PLAYBACK.get(f"{sim}_{channel}")
    return bool(exp and _now() < exp)


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def ffmpeg_available() -> bool:
    return bool(config.FFMPEG_BIN) and bool(shutil.which(config.FFMPEG_BIN))


def streams() -> list:
    out = []
    for key, st in _STREAMS.items():
        sim = st.get("sim")
        ch = st.get("channel")
        kind = st.get("kind") or "live"
        if sim is None or ch is None:
            sim, ch = key.rsplit("_", 1)
        prefix = "jt_" if kind == "live" else "jtpb_"
        out.append({
            "sim": str(sim), "channel": int(ch),
            "bytes": st["bytes"],
            "kind": kind,
            "hls": f"/hls/{prefix}{sim}_{ch}.m3u8",
        })
    return out


def stats() -> dict:
    return {
        "streams": len(_STREAMS),
        **_STATS,
        "uptime_s": (
            int(datetime.now(timezone.utc).timestamp() - _STATS["started"])
            if _STATS["started"] else 0
        ),
    }


# ---------------------------------------------------------------- parse

def _bcd_sim(raw: bytes) -> str:
    """6 baytli BCD SIM -> matn. Yetakchi nollar SAQLANADI (protokol
    terminal identifikatori 6 bayt BCD, to'liq 12 xona). Oxiridagi 0xF
    to'ldirgich ('f') olib tashlanadi."""
    s = raw.hex()
    return s.rstrip("f") or "0"


def parse_packet(data: bytes) -> tuple:
    """JT/T 1078 media paketini parse qilish.

    Sarlavha uzunligi data_type'ga bog'liq: video (I/P/B) 30, audio 26,
    shaffof (0100) 18 bayt (_header_len). data_type: yuqori 4 bit = tur
    (I=0,P=1,B=2,audio=3,shaffof=4), past 4 bit = subpaket belgisi.
    Qaytaradi: (dict, iste'mol_qilingan_bayt) yoki (None, 0) — paket hali
    to'liq emas yoki flag mos emas.
    """
    if data[0:4] != FLAG or len(data) < 16:
        return None, 0
    dt = data[15]
    data_type = (dt >> 4) & 0x0F
    hdr = _header_len(data_type)
    if len(data) < hdr:
        return None, 0
    body_len = int.from_bytes(data[hdr - 2:hdr], "big")
    total = hdr + body_len
    if len(data) < total:
        return None, 0
    sim = _bcd_sim(data[8:14])
    ts = int.from_bytes(data[16:24], "big") if data_type != DT_TRANSPARENT else 0
    return {
        "sim": sim,
        "channel": data[14],
        "data_type": data_type,
        "subpacket": dt & 0x0F,
        "ts": ts,
        "payload": data[hdr:total],
    }, total


def extract_packets(buf: bytes) -> tuple:
    """Oqim buferidan to'liq paketlarni ajratish.

    TCP bir read()'da bir nechta paketni birlashtirib yoki bitta paketni
    bo'laklab yuborishi mumkin — shuning uchun FLAG + body_len bilan
    aniq freymleymiz. Qaytaradi: (paketlar_ro'yxati, qoldiq_buf).
    """
    pkts = []
    i = 0
    n = len(buf)
    while True:
        s = buf.find(FLAG, i)
        if s < 0:
            # flag topilmadi — iste'mol qilinmagan qismning oxirgi 3 bayti
            # (bo'lak flag bo'lishi mumkin) saqlanadi, qolgani tashlanadi
            return pkts, buf[max(i, n - 3):]
        p, consumed = parse_packet(buf[s:])
        if not p:
            # bu joyda paket boshlanadi, lekin hali to'liq emas — kutamiz
            return pkts, buf[s:]
        pkts.append(p)
        i = s + consumed


# ---------------------------------------------------------------- ffmpeg mux

class _Muxer:
    """Xom H.264 access-unit'larni ffmpeg stdin'iga yozib HLS chiqaradi."""

    def __init__(self, sim: str, channel: int, mode: str = "live"):
        self.sim = sim
        self.channel = channel
        self.mode = mode
        # key: jonli "sim_ch", yozuv "pb:sim_ch" (bir xil kanalda to'qnashmasin)
        self.key = f"{sim}_{channel}" if mode == "live" else f"pb:{sim}_{channel}"
        self.prefix = "jt_" if mode == "live" else "jtpb_"
        self.bytes = 0
        self.since = _now()
        self.last_active = self.since
        self.closed = False
        self._dir = Path(config.HLS_DIR) / "jt1078"
        # Serverga video SAQLANMAYDI — xom H.264 faqat diagnostika uchun
        # (GPS_JT1078_RAW_DUMP=1).
        self._raw = None
        if config.JT1078_RAW_DUMP:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._raw = open(self._dir / f"{sim}_{channel}.h264",
                                 "ab", buffering=0)
            except Exception:  # noqa: BLE001
                self._raw = None
        self._proc = None
        self._errlog = None
        if ffmpeg_available():
            out = Path(config.HLS_DIR) / f"{self.prefix}{sim}_{channel}.m3u8"
            # Kamera oqimida B-frame bor va aniq fps yo'q. `-use_wallclock`
            # B-frame reorder bilan to'qnashib jonli oqimda tiqilib qoladi;
            # `-c copy` esa mpegts'ga PTS bermaydi. Shuning uchun kirish fps'ni
            # o'zimiz beramiz (config.JT1078_FPS_IN, real kamera fps'iga mos) va
            # libx264'ga qayta kodlab, CFR chiqishda barqaror 2s HLS olamiz.
            cmd = [
                config.FFMPEG_BIN,
                "-hide_banner", "-loglevel", "warning",
                "-fflags", "+genpts+discardcorrupt",
                "-framerate", str(config.JT1078_FPS_IN),
                "-f", "h264", "-i", "pipe:0",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-vsync", "cfr", "-r", str(config.JT1078_FPS_OUT),
                "-force_key_frames", "expr:gte(t,n_forced*2)",
                "-f", "hls",
                "-hls_time", "2",
                "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments",
                str(out),
            ]
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._errlog = open(self._dir / f"ffmpeg_{self.key.replace(':', '_')}.log",
                                    "ab", buffering=0)
            except Exception:  # noqa: BLE001
                self._errlog = None
            try:
                self._proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=(self._errlog if self._errlog else subprocess.DEVNULL),
                )
            except Exception:  # noqa: BLE001
                log.exception("JT1078: ffmpeg ishga tushmadi (%s)", self.key)
                self._proc = None

    def write(self, au: bytes) -> None:
        self.bytes += len(au)
        self.last_active = _now()
        if self._raw is not None:
            try:
                self._raw.write(au)
            except Exception:  # noqa: BLE001
                pass
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(au)
                self._proc.stdin.flush()
            except Exception:  # noqa: BLE001
                # ffmpeg o'lgan — muxer'ni yopamiz; keyingi kadrda yangi
                # ffmpeg ishga tushadi (doimiy registry qayta yaratadi).
                log.warning("JT1078: ffmpeg yozish xatosi (%s) — qayta ishga "
                            "tushiriladi", self.key)
                self.close()

    def close(self) -> None:
        self.closed = True
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:  # noqa: BLE001
                pass
            self._raw = None
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        if self._errlog is not None:
            try:
                self._errlog.close()
            except Exception:  # noqa: BLE001
                pass
            self._errlog = None
        _STREAMS.pop(self.key, None)
        _MUXERS.pop(self.key, None)


# ---------------------------------------------------------------- server

def _get_muxer(sim: str, channel: int, mode: str = "live") -> "_Muxer":
    """(sim,kanal,mode) uchun DOIMIY muxer: mavjudini qaytaradi yoki yangi ochadi.

    Kamera qisqa-qisqa ulanib/uzilib turgani uchun muxer bir marta yaratiladi
    va uzilishlar orasida ham ffmpeg/HLS'ni davom ettiradi (playlist ENDLIST
    bo'lib qolmaydi). Faqat uzoq vaqt data kelmasa reaper yopadi.

    mode: "live" (jonli) yoki "playback" (qurilma xotirasidagi yozuv).
    """
    key = f"{sim}_{channel}" if mode == "live" else f"pb:{sim}_{channel}"
    m = _MUXERS.get(key)
    if m is None or m.closed:
        m = _MUXERS[key] = _Muxer(sim, channel, mode=mode)
        _STREAMS[key] = {"bytes": 0, "since": m.since,
                         "sim": sim, "channel": channel, "kind": mode}
        log.info("JT1078: %s oqim boshlandi %s_%s (HLS /hls/%s%s_%s.m3u8)",
                 mode, sim, channel, m.prefix, sim, channel)
    return m


def cleanup_hls_dir(max_age_s: int | None = None) -> int:
    """HLS_DIR'dan bo'sh va eskirgan segmentlarni o'chiradi (server to'lmasin).

    Faol oqim fayllari doim yangilanadi (mtime yangi) — shuning uchun
    o'chirilmaydi. Qaytaradi: o'chirilgan fayllar soni.
    """
    limit = config.HLS_MAX_AGE_S if max_age_s is None else max_age_s
    now = _now()
    removed = 0
    roots = [Path(config.HLS_DIR), Path(config.HLS_DIR) / "jt1078"]
    for d in roots:
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                if st.st_size == 0 or now - st.st_mtime > limit:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    if removed:
        log.info("JT1078: HLS tozalandi — %s fayl o'chirildi", removed)
    return removed


def _reap_idle_muxers(now: float | None = None) -> int:
    """MUXER_IDLE_TIMEOUT'dan beri data olmagan muxerlarni yopish.

    Qaytuvchi: yopilgan muxerlar soni. run_tcp ichidagi reaper task chaqiradi.
    """
    now = _now() if now is None else now
    closed = 0
    for key in list(_MUXERS.keys()):
        m = _MUXERS.get(key)
        if m is None:
            continue
        if now - m.last_active > MUXER_IDLE_TIMEOUT:
            log.info("JT1078: oqim yopildi (idle) %s", key)
            m.close()
            closed += 1
    return closed


async def _reaper_loop(interval: float = 30.0) -> None:
    ticks = 0
    while True:
        await asyncio.sleep(interval)
        try:
            _reap_idle_muxers()
        except Exception:  # noqa: BLE001
            log.exception("JT1078: reaper xatosi")
        # Server to'lmasin: har ~10 daqiqada eski/bo'sh HLS fayllarni tozalash
        ticks += 1
        if ticks % 20 == 0:
            try:
                cleanup_hls_dir()
            except Exception:  # noqa: BLE001
                log.exception("JT1078: HLS tozalash xatosi")


class _Session:
    """Bitta TCP ulanishdagi oqim(lar)ni access-unit'larga yig'adi."""

    def __init__(self):
        self.pending: dict = {}   # (sim,ch) -> bytearray (subpackage yig'indi)
        self.primed: set = set()  # I-kadr ko'rilgan (sim,ch) — shundagina yozamiz

    def on_packet(self, p: dict) -> None:
        typ = p["data_type"]
        sub = p["subpacket"]
        key = (p["sim"], p["channel"])
        if typ not in _VIDEO_TYPES:
            return                      # audio / shaffof — video emas
        payload = p["payload"]
        if not payload:
            return
        if sub == SUB_FIRST:
            # yangi kadr boshi — tugallanmagan avvalgi yig'indi tashlanadi
            self.pending[key] = bytearray(payload)
        elif sub == SUB_MIDDLE:
            buf = self.pending.get(key)
            if buf is not None:
                buf.extend(payload)
        elif sub == SUB_LAST:
            buf = self.pending.pop(key, None)
            if buf is None:
                buf = bytearray()
            buf.extend(payload)
            self._emit(key, typ, bytes(buf))
        else:  # SUB_ATOMIC — butun kadr bitta paketda
            self.pending.pop(key, None)
            self._emit(key, typ, payload)

    def _emit(self, key, typ: int, au: bytes) -> None:
        if not au:
            return
        # ffmpeg SPS/PPS'siz P-kadrlarni dekodlay olmaydi — birinchi I-kadr
        # kelgunga qadar yozmaymiz (har bir yangi ulanishda keyframe kutamiz,
        # chunki uzilishdan keyingi P-kadr buzilgan bo'ladi).
        if key not in self.primed:
            if typ != DT_VIDEO_I:
                return
            self.primed.add(key)
        _STATS["access_units"] += 1
        mode = "playback" if _playback_active(key[0], key[1]) else "live"
        m = _get_muxer(key[0], key[1], mode)
        m.write(au)
        st = _STREAMS.get(m.key)
        if st is not None:
            st["bytes"] = m.bytes

    def close(self) -> None:
        # Ulanish yopildi — muxerlarni YOPMAYMIZ (doimiy, qayta ulanishda
        # davom etadi). Faqat tugallanmagan subpaket yig'indilari tashlanadi.
        self.pending.clear()
        self.primed.clear()


async def _handle(reader: asyncio.StreamReader,
                  writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    sess = _Session()
    # Serverga video saqlanmaydi — xom dump faqat diagnostika uchun yoqiladi.
    raw_dump = None
    if config.JT1078_RAW_DUMP:
        tag = (f"{peer[0]}_{peer[1]}_"
               f"{int(datetime.now(timezone.utc).timestamp())}")
        raw_path = Path(config.HLS_DIR) / "jt1078" / f"raw_{tag}.bin"
        try:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_dump = open(raw_path, "ab", buffering=0)
        except Exception:  # noqa: BLE001
            raw_dump = None
    log.info("JT1078: media ulanishi %s", peer)
    buf = b""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=60)
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            if raw_dump is not None:
                try:
                    raw_dump.write(chunk)
                except Exception:  # noqa: BLE001
                    pass
            buf += chunk
            # TCP paketlarni birlashtirishi/bo'laklashi mumkin — FLAG+body_len
            # bilan aniq freymleymiz. To'liq paketlar olinadi, qoldiq kutadi.
            pkts, buf = extract_packets(buf)
            for p in pkts:
                _STATS["packets"] += 1
                sess.on_packet(p)
            # Desinxronizatsiyada bufer cheksiz o'smasligi uchun himoya.
            if len(buf) > 8 * 1024 * 1024:
                log.warning("JT1078: bufer juda katta (%s) — tozalandi", peer)
                _STATS["errors"] += 1
                buf = b""
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("JT1078: ulanishda xato (%s)", peer)
    finally:
        sess.close()
        if raw_dump is not None:
            try:
                raw_dump.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        log.info("JT1078: media uzildi %s", peer)


async def run_tcp(host: str, port: int) -> None:
    _STATS["started"] = _now()
    try:
        cleanup_hls_dir(max_age_s=0)  # ishga tushishda eski/bo'sh fayllarni tozalash
    except Exception:  # noqa: BLE001
        log.exception("JT1078: boshlang'ich HLS tozalash xatosi")
    server = await asyncio.start_server(_handle, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("JT1078 media listener: %s", addrs)
    reaper = asyncio.ensure_future(_reaper_loop())
    async with server:
        try:
            await server.serve_forever()
        finally:
            reaper.cancel()
