"""JT/T 1078 media listener — 4G kameradan VIDEO oqimini qabul qilish.

Qurilma JT808 orqali 0x9101 (jonli) / 0x9201 (playback) so'rovini olgach,
video oqimini shu TCP portga (JT1078 RTP-simon paketlar) yuboradi. Bu modul:
  * paketlarni 0x30316364 flag + body-length bilan freymleydi (TCP
    birlashtirgan/bo'laklagan holatlarda ham to'g'ri)
  * sarlavhadan (video 30 / audio 26 / shaffof 18 bayt) SIM, kanal, data
    type, timestamp, payload oladi
  * RTP payload type (PT) bo'yicha video kodekni aniqlaydi (98=H.264,
    99=H.265) va ffmpeg'ga mos kirish formatini beradi
  * subpaketlarni (birinchi/oraliq/oxirgi) yig'ib to'liq video kadrini
    hosil qiladi; audio va shaffof/xizmat paketlari tashlanadi
  * har bir (qurilma, kanal) uchun ffmpeg jarayonini yoqib, xom H.264'ni
    HLS segmentlariga aylantiradi: <HLS_DIR>/jt_<sim>_<ch>.m3u8
  * diagnostika uchun xom oqimni diskka ham yozadi (.h264 va raw_*.bin)

Dashboard ko'rsatadigan diagnostika (har bir oqim uchun): kodek,
kadr/kalit-kadr soni va oxirgi kalit kadr vaqti, muxer qayta ishga tushish
soni, oxirgi ilova xatosi hamda ffmpeg'ning oxirgi xato qatori. Yopilgan
oqimlar ham bir muddat saqlanadi (`recent_streams()`) — "video nega o'chdi?"
savoliga javob berish uchun.

Sarlavha tartibi real CMSV6 qurilmasi raw dump'i orqali tasdiqlangan
(pastdagi HEADER_LEN izohiga qarang).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config, diag

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
DT_AUDIO = 3          # 0011 — audio (tashlanadi)
DT_TRANSPARENT = 4    # 0100 — shaffof/xizmat ma'lumoti (tashlanadi)
# RTP payload type (M/PT baytining past 7 biti) — video kodekni aniqlaydi.
# lkmio/lkm (jt_packet.go) va JT/T 1078-2016 standartiga mos: 98=H.264,
# 99=H.265(HEVC), 100=AVS, 101=SVAC. Biz faqat ffmpeg qo'llay oladigan
# H.264/H.265'ni qabul qilamiz; AVS/SVAC uchun H.264 deb qaraladi.
PT_VIDEO_H264 = 98
PT_VIDEO_H265 = 99
PT_VIDEO_AVS = 100
PT_VIDEO_SVAC = 101
_PT_VIDEO_CODEC = {
    PT_VIDEO_H264: "h264",
    PT_VIDEO_H265: "hevc",
}
DEFAULT_VIDEO_CODEC = "h264"
# PT -> odam o'qiydigan video kodek nomi (dashboard diagnostikasi uchun).
_CODEC_NAME = {"h264": "H.264", "hevc": "H.265"}
DIAG_LOG_TAIL = 4096   # ffmpeg logidan o'qiladigan oxirgi baytlar
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
_STREAMS: dict = {}   # "key" -> {"bytes", "since", "sim", "channel", "kind",
                      #           "codec", "frames",
                      #           "keyframes", "last_keyframe_at", "restarts",
                      #           "last_error", "last_error_at"}
# Yopilgan oqimlarning OXIRGI holati (diagnostika): muxer qayta ishga tushganda
# yoki idle sababli yopilganda shu yerga ko'chiriladi. `streams()` faqat faol
# oqimlarni beradi — yopilganlar `recent_streams()` orqali ko'rinadi.
_DIAG: dict = {}      # "key" -> _STREAMS yozuvi + "closed_at", "close_reason"
DIAG_TTL = 24 * 3600.0   # yopilgan oqim diagnostikasi shu muddat saqlanadi
DIAG_KEEP = 50           # eng ko'p saqlanadigan yopilgan oqim yozuvi
# Kamera media ulanishini qisqa-qisqa ochib yopadi (~20s). Har bir uzilishda
# ffmpeg'yopilib HLS playlist'ini ENDLIST bilan yakunlamasligi uchun muxerlar
# (sim,kanal,mode) bo'yicha DOIMIY saqlanadi; qayta ulanishda o'sha muxer davom
# etadi.
_MUXERS: dict = {}    # "key" -> _Muxer
# "key" -> oxirgi ulanish manzili (ip, port). Log qatorlarida "qaysi kamera
# (IMEI) qaysi kanalni yubordi" ko'rinishi uchun muxer bilan birga saqlanadi.
_MUXER_SRC: dict = {}
# peer -> _Session. Media ulanishining IMEI/kanali FAQAT birinchi paketdan
# ma'lum bo'ladi; shu registry tufayli "media ulanishi/uzildi" qatorlariga ham
# (qayta ulanishda) avval aniqlangan "IMEI_kanal" qo'shiladi.
_SESSIONS: dict = {}
# IP -> [(sim, kanal), ...]. Kamera qisqa-qisqa qayta ulanadi va har safar
# BOSHQA portdan keladi — shu registry tufayli "yangi ulanish"/"birinchi N
# bayt" qatorlarida ham IMEI/kanal darhol ko'rinadi.
_PEER_KEYS: dict = {}
_PEER_KEYS_MAX = 500
# "{sim}_{ch}" -> expiry epoch. Yozuv (playback) so'ralganda shu (sim,kanal)
# uchun kelayotgan oqim "playback" deb belgilanadi (jonli bilan aralashmasin).
_PLAYBACK: dict = {}
MUXER_IDLE_TIMEOUT = 120.0   # sekund data kelmasa muxer yopiladi
# Bitta kadr (access-unit) buferining eng katta hajmi. Qurilma timestamp'ni
# yangilamasa yoki subMark'ni butunlay noto'g'ri belgilasa, bufer cheksiz
# o'smasligi uchun chegaraga yetganda kadr majburan chiqariladi.
FRAME_MAX_BYTES = 4 * 1024 * 1024


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


def _touch_playback(sim: str, channel: int) -> None:
    """Playback oqimi kelayotganda oynani uzaytiradi (uzun yozuv uzilmasin).

    Oyna FAQAT so'rov paytida hisoblansa, uzoq yozuv davom etayotib "jonli"ga
    o'tib ketardi. Oqim kelguncha har kadrda yangilanadi.
    """
    k = f"{sim}_{channel}"
    if k in _PLAYBACK:
        _PLAYBACK[k] = _now() + max(1, int(config.JT1078_PLAYBACK_WINDOW_S))


def _playback_active(sim: str, channel: int) -> bool:
    exp = _PLAYBACK.get(f"{sim}_{channel}")
    return bool(exp and _now() < exp)


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _remember_peer(peer, keys) -> None:
    """Shu IP ostida ko'rilgan (sim, kanal)larni eslab qolish.

    Qurilma qayta ulanganda port o'zgaradi, shuning uchun IP bo'yicha
    saqlanadi — "yangi ulanish" qatorida IMEI darhol ko'rinsin.
    """
    if not (peer and keys):
        return
    ip = peer[0]
    if len(_PEER_KEYS) >= _PEER_KEYS_MAX and ip not in _PEER_KEYS:
        # Uzoq jarayonda cheksiz o'smasin (FIFO — eng eskisi chiqadi).
        _PEER_KEYS.pop(next(iter(_PEER_KEYS)), None)
    seen = _PEER_KEYS.setdefault(ip, [])
    for k in keys:
        if k not in seen:
            seen.append(k)


def _known_peers(peer) -> tuple:
    """Shu peer uchun aniqlangan (sim, kanal) juftliklari (IP bo'yicha)."""
    if not peer:
        return ()
    return tuple(_PEER_KEYS.get(peer[0], ()))


def _src(peer, *keys) -> str:
    """Log yorlig'i: "ip:port" + aniqlangan "sim_kanal" (bo'lsa).

    Kamera o'z IP'sidan keladi va bir necha kanal yuboradi — log qatorida
    ikkalasi ham ko'rinmasa "qaysi kamera/kanal?" savoliga javob bo'lmaydi.
    """
    label = f"{peer[0]}:{peer[1]}" if peer else "?"
    seen: list[str] = []
    for k in keys:
        if not k:
            continue
        s = f"{k[0]}_{k[1]}" if isinstance(k, tuple) else str(k)
        if s not in seen:
            seen.append(s)
    return f"{label} {'/'.join(seen)}" if seen else label


def ffmpeg_available() -> bool:
    return bool(config.FFMPEG_BIN) and bool(shutil.which(config.FFMPEG_BIN))


def _ffmpeg_log(key: str) -> Path:
    """Muxer kaliti bo'yicha ffmpeg logi (xato matni shu fayldan o'qiladi)."""
    return Path(config.HLS_DIR) / "jt1078" / f"ffmpeg_{key.replace(':', '_')}.log"


def codec_name(codec: str | None) -> str:
    """"h264"/"hevc" -> "H.264"/"H.265" (UI uchun)."""
    return _CODEC_NAME.get(codec or "", codec or "?")


def _stream_view(key: str, st: dict, active: bool) -> dict:
    """Bitta oqim diagnostikasi (dashboard API/UI uchun yagona ko'rinish)."""
    now = _now()
    sim, ch = st.get("sim"), st.get("channel")
    if sim is None or ch is None:
        sim, ch = key.rsplit("_", 1)
    kind = st.get("kind") or "live"
    prefix = "jt_" if kind == "live" else "jtpb_"
    lk = st.get("last_keyframe_at")
    err_at = st.get("last_error_at")
    since = st.get("since")
    out = {
        "sim": str(sim), "channel": int(ch),
        "bytes": int(st.get("bytes") or 0),
        "kind": kind,
        "active": bool(active),
        "codec": st.get("codec") or DEFAULT_VIDEO_CODEC,
        "codec_name": codec_name(st.get("codec")),
        "hls": f"/hls/{prefix}{sim}_{ch}.m3u8",
        # Diagnostika: kadrlar va oxirgi kalit kadr
        "frames": int(st.get("frames") or 0),
        "keyframes": int(st.get("keyframes") or 0),
        "keyframe_age_s": round(now - lk, 1) if lk else None,
        "uptime_s": int(now - since) if since else None,
        # Diagnostika: muxer qayta ishga tushishi va oxirgi xato
        "restarts": int(st.get("restarts") or 0),
        "dropped": int(st.get("dropped") or 0),
        "last_error": st.get("last_error"),
        "error_age_s": round(now - err_at) if err_at else None,
        "ffmpeg_note": (diag.tail_note(_ffmpeg_log(key), DIAG_LOG_TAIL)
                        if active else st.get("ffmpeg_note")),
    }
    if not active:
        closed = st.get("closed_at")
        out["closed_s"] = round(now - closed) if closed else None
        out["close_reason"] = st.get("close_reason")
    return out


def streams() -> list:
    """FAOL oqimlar (diagnostika maydonlari bilan) — sim/kanal bo'yicha tartibda."""
    out = [_stream_view(key, st, True) for key, st in _STREAMS.items()]
    out.sort(key=lambda s: (s["sim"], s["channel"]))
    return out


def recent_streams(limit: int = 8) -> list:
    """Yaqinda yopilgan oqimlar (eng yangisi birinchi) — oxirgi xatosi bilan.

    Kamera uzilib qolsa yoki muxer qayta ishga tushsa, faol ro'yxatdan
    yo'qoladi; sababni ko'rish uchun shu ro'yxat dashboard'da ko'rsatiladi.
    """
    items = [v for v in _DIAG.values() if v.get("closed_at")]
    items.sort(key=lambda v: v.get("closed_at") or 0, reverse=True)
    return [_stream_view(str(v.get("key") or ""), v, False)
            for v in items[:max(1, limit)]]


def _prune_diag(ttl: float = DIAG_TTL, keep: int = DIAG_KEEP) -> None:
    """Yopilgan oqim diagnostikasini chegaralab turish (xotira o'smasin)."""
    now = _now()
    for key in [k for k, v in _DIAG.items()
                if not v.get("closed_at") or now - v["closed_at"] > ttl]:
        _DIAG.pop(key, None)
    if len(_DIAG) > keep:
        old = sorted(_DIAG.items(), key=lambda kv: kv[1].get("closed_at") or 0)
        for key, _ in old[:len(_DIAG) - keep]:
            _DIAG.pop(key, None)


def stats() -> dict:
    return {
        "streams": len(_STREAMS),
        "closed_streams": len(_DIAG),
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
        "pt": data[5] & 0x7F,
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

def _codec_for_pt(pt) -> str:
    """PT (payload type) -> ffmpeg kirish kodeki nomi.

    Noma'lum/NULL PT (eski drayverlar uni bermaydi) uchun standart H.264.
    """
    return _PT_VIDEO_CODEC.get(pt, DEFAULT_VIDEO_CODEC)


def _muxer_key(sim: str, channel: int, mode: str = "live") -> str:
    """Muxer/stream registry kaliti (yozuv jonli bilan to'qnashmasin)."""
    return f"{sim}_{channel}" if mode == "live" else f"pb:{sim}_{channel}"


class _Muxer:
    """Xom video access-unit'larni ffmpeg stdin'iga yozib HLS chiqaradi.

    `codec` — ffmpeg kirish formati (PT'dan aniqlanadi): H.264 uchun "h264",
    H.265/HEVC uchun "hevc". Chiqish har doim H.264 (brauzer mosligi uchun)."""

    def __init__(self, sim: str, channel: int, mode: str = "live",
                 codec: str = DEFAULT_VIDEO_CODEC):
        self.sim = sim
        self.channel = channel
        self.mode = mode
        self.codec = codec or DEFAULT_VIDEO_CODEC
        # key: jonli "sim_ch", yozuv "pb:sim_ch" (bir xil kanalda to'qnashmasin)
        self.key = _muxer_key(sim, channel, mode)
        self.prefix = "jt_" if mode == "live" else "jtpb_"
        self.bytes = 0
        self.since = _now()
        self.last_active = self.since
        self.closed = False
        self._dir = Path(config.HLS_DIR) / "jt1078"
        # --- Diagnostika (dashboard): oxirgi xato + muxer/qayta ishga tushish ---
        self.log_path = _ffmpeg_log(self.key)
        self.restarts = 0
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self.ffmpeg_note: str | None = None
        self.close_reason: str | None = None
        # Kadrlar soni `_Session._emit`da sanaladi; bu yerda faqat tayyorlaymiz.
        self.frames = 0
        self.keyframes = 0
        self.last_keyframe_at: float | None = None
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
        # Yozuvchi thread (bounded navbat): ffmpeg stdin'iga yozish alohida
        # thread'da bajariladi. Ilgari bloklovchi write+flush butun event
        # loop'ni muzlatib, qurilma soketi o'qilmay qolardi (TCP "win 0" ->
        # "buyruq yuborishda xato" timeout). Endi navbat to'lsa kadr tashlanadi.
        self._in_q = None
        self._pump_thread = None
        self.dropped = 0
        if ffmpeg_available():
            out = Path(config.HLS_DIR) / f"{self.prefix}{sim}_{channel}.m3u8"
            # Kamera oqimida B-frame bor va aniq fps yo'q. `-use_wallclock`
            # B-frame reorder bilan to'qnashib jonli oqimda tiqilib qoladi;
            # `-c copy` esa mpegts'ga PTS bermaydi. Shuning uchun kirish fps'ni
            # o'zimiz beramiz va libx264'ga qayta kodlab, CFR chiqishda
            # barqaror 2s HLS olamiz. Kirish fps real kadr tezligiga mos
            # bo'lmog'i shart: jonli ~3fps, yozuv(playback) ~13fps (o'lchangan),
            # shu sababli rejimga qarab alohida qiymat tanlanadi.
            fps_in = (config.JT1078_FPS_IN_PB if self.mode == "playback"
                      else config.JT1078_FPS_IN)
            fps_out = (config.JT1078_FPS_OUT_PB if self.mode == "playback"
                       else config.JT1078_FPS_OUT)
            cmd = [
                config.FFMPEG_BIN,
                "-hide_banner", "-loglevel", "warning",
                "-fflags", "+genpts+discardcorrupt",
                "-thread_queue_size", str(config.JT1078_THREAD_QUEUE),
                "-framerate", str(fps_in),
                "-f", self.codec, "-i", "pipe:0",
            ]
            cmd += [
                "-c:v", "libx264", "-preset", "ultrafast",
                "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-vsync", "cfr", "-r", str(fps_out),
                "-force_key_frames", "expr:gte(t,n_forced*2)",
                "-max_muxing_queue_size", "1024",
            ]
            cmd += [
                "-f", "hls",
                "-hls_time", "2",
                "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments",
                str(out),
            ]
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                # "wb": log har yangi muxer bilan boshlanadi — shunda
                # diagnostikadagi "oxirgi xato" joriy ffmpeg'ga tegishli
                # bo'ladi (eski jarayonning xatosi qolib ketmaydi).
                self._errlog = open(self.log_path, "wb", buffering=0)
            except Exception:  # noqa: BLE001
                self._errlog = None
            try:
                self._proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=(self._errlog if self._errlog else subprocess.DEVNULL),
                )
                self._start_pump()
            except Exception as exc:  # noqa: BLE001
                log.exception("JT1078: ffmpeg ishga tushmadi (%s)",
                              _src(_MUXER_SRC.get(self.key), self.key))
                self._proc = None
                self.note(f"ffmpeg ishga tushmadi: {exc}")

    def note(self, msg: str) -> None:
        """Diagnostika: oxirgi xato/ogohlantirishni yozib qo'yish.

        Xato muxer yaratilishida ham (registry hali yo'q) chaqirilishi mumkin —
        shuning uchun faqat mavjud bo'lsa oqim yozuviga ham ko'chiriladi.
        """
        self.last_error = msg
        self.last_error_at = _now()
        st = _STREAMS.get(self.key)
        if st is not None:
            st["last_error"] = msg
            st["last_error_at"] = self.last_error_at

    def _start_pump(self) -> None:
        """ffmpeg stdin'iga yozuvchi thread'ni ishga tushirish (bounded navbat)."""
        if self._proc is None or self._proc.stdin is None:
            return
        self._in_q = queue.Queue(maxsize=max(1, config.JT1078_QUEUE_MAX))
        self._pump_thread = threading.Thread(
            target=self._pump, name=f"jt1078-{self.key}", daemon=True)
        self._pump_thread.start()

    def _pump(self) -> None:
        """Navbatdan access-unit'larni olib ffmpeg stdin'iga yozadi.

        Bloklovchi write/flush SHU thread'da bajariladi — event loop hech qachon
        bloklanmaydi. ffmpeg o'lsa thread to'xtaydi va muxer "closed" belgilanadi
        (keyingi kadrda yangi ffmpeg ochiladi)."""
        proc = self._proc
        stdin = proc.stdin if proc is not None else None
        q = self._in_q
        if stdin is None or q is None:
            return
        while True:
            au = q.get()
            if au is None:
                break
            try:
                stdin.write(au)
                stdin.flush()
            except Exception as exc:  # noqa: BLE001
                log.warning("JT1078: ffmpeg yozish xatosi (%s) — qayta ishga "
                            "tushiriladi",
                            _src(_MUXER_SRC.get(self.key), self.key))
                self.note(f"ffmpeg yozish xatosi — qayta ishga tushiriladi: {exc}")
                self.close_reason = self.close_reason or "ffmpeg xatosi"
                self.closed = True
                break

    def write(self, au: bytes) -> None:
        self.bytes += len(au)
        self.last_active = _now()
        if self._raw is not None:
            try:
                self._raw.write(au)
            except Exception:  # noqa: BLE001
                pass
        q = self._in_q
        if q is None:
            return
        try:
            q.put_nowait(au)
        except queue.Full:
            # ffmpeg ulgurmayapti — eng eski kadrni tashlab, eng yangisini
            # qoldiramiz (jonli oqimda kechikish o'smasin). Event loop
            # HECH QACHON bloklanmaydi.
            self.dropped += 1
            try:
                q.get_nowait()
                q.put_nowait(au)
            except Exception:  # noqa: BLE001
                pass
            if self.dropped % 100 == 1:
                log.warning("JT1078: ffmpeg ulgurmayapti — kadr tashlandi "
                            "(%s, jami %d)",
                            _src(_MUXER_SRC.get(self.key), self.key),
                            self.dropped)

    def close(self) -> None:
        self.closed = True
        # Yozuvchi thread'ni to'xtatamiz: sentinel qo'yib, qisqa kutamiz. Agar
        # ffmpeg'ga yozishda qolib ketgan bo'lsa — jarayonni o'ldiramiz (aks
        # holda stdin.close() event loop'ni bloklashi mumkin).
        if self._in_q is not None:
            try:
                self._in_q.put_nowait(None)
            except Exception:  # noqa: BLE001
                pass
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=2.0)
            if self._pump_thread.is_alive() and self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
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
        # Diagnostika: yopilgan oqimning oxirgi holatini saqlab qolamiz
        # (dashboard "oxirgi xato"ni shundan ko'rsatadi) va keyingi muxer
        # shu yozuvdan qayta ishga tushish sonini meros oladi.
        self.ffmpeg_note = diag.tail_note(self.log_path, DIAG_LOG_TAIL)
        st = _STREAMS.pop(self.key, None)
        if st is not None:
            st.update({
                "bytes": self.bytes,
                "frames": st.get("frames") or self.frames,
                "keyframes": st.get("keyframes") or self.keyframes,
                "last_keyframe_at": st.get("last_keyframe_at") or self.last_keyframe_at,
                "restarts": self.restarts,
                "dropped": self.dropped,
                "last_error": self.last_error or st.get("last_error"),
                "last_error_at": self.last_error_at or st.get("last_error_at"),
                "ffmpeg_note": self.ffmpeg_note,
                "closed_at": _now(),
                "close_reason": self.close_reason or "oqim yakunlandi",
                "key": self.key,
                "active": False,
            })
            _DIAG[self.key] = st
            _prune_diag()
        _MUXERS.pop(self.key, None)
        _MUXER_SRC.pop(self.key, None)


# ---------------------------------------------------------------- server

def _get_muxer(sim: str, channel: int, mode: str = "live",
               codec: str = DEFAULT_VIDEO_CODEC, peer=None) -> "_Muxer":
    """(sim,kanal,mode) uchun DOIMIY muxer: mavjudini qaytaradi yoki yangi ochadi.

    Kamera qisqa-qisqa ulanib/uzilib turgani uchun muxer bir marta yaratiladi
    va uzilishlar orasida ham ffmpeg/HLS'ni davom ettiradi (playlist ENDLIST
    bo'lib qolmaydi). Faqat uzoq vaqt data kelmasa reaper yopadi.

    mode: "live" (jonli) yoki "playback" (qurilma xotirasidagi yozuv).
    codec: ffmpeg kirish formati ("h264"/"hevc"). Oqim o'rtasida o'zgarsa
    (masalan kanal kodeki almashsa) muxer qayta ochiladi — aks holda eski
    format bilan yangi oqim buziladi.
    peer: ulanish manzili (ip, port) — logda qaysi kamera ekanini ko'rsatish
    uchun muxer bilan birga saqlanadi.
    """
    key = _muxer_key(sim, channel, mode)
    codec = codec or DEFAULT_VIDEO_CODEC
    m = _MUXERS.get(key)
    if peer:
        # Doimiy muxer qayta ulanishda davom etadi — manba manzilini yangilab
        # turamiz (logda oxirgi ulanish IP'si ko'rinadi).
        _MUXER_SRC[key] = peer
    if m is not None and not m.closed and getattr(m, "codec", None) != codec:
        # Kodek o'zgardi (yoki muxer mos emas) — eskisini yopib qayta ochamiz.
        log.info("JT1078: kodek o'zgardi %s: %s -> %s — muxer qayta yaratiladi",
                 _src(peer, key), getattr(m, "codec", "?"), codec)
        m.close_reason = "kodek o'zgardi"
        m.close()
        m = None
    if m is not None and m.closed:
        # Yozuvchi thread ffmpeg xatosi bilan to'xtagan — resurslarni
        # bo'shatamiz; pastda yangi muxer ochiladi.
        m.close()
        m = None
    if m is None or m.closed:
        prev = _DIAG.get(key) or {}
        m = _MUXERS[key] = _Muxer(sim, channel, mode=mode, codec=codec)
        # Diagnostika: avvalgi holatdan meros (qayta ishga tushish soni, xato)
        m.restarts = int(prev.get("restarts") or 0) + (1 if prev else 0)
        _STREAMS[key] = {
            "bytes": 0, "since": m.since,
            "sim": sim, "channel": channel, "kind": mode,
            "codec": m.codec,
            "frames": 0, "keyframes": 0, "last_keyframe_at": None,
            "restarts": m.restarts,
            "dropped": 0,
            "last_error": getattr(m, "last_error", None),
            "last_error_at": getattr(m, "last_error_at", None),
            "active": True,
        }
        log.info("JT1078: %s oqim boshlandi %s_%s kodek=%s "
                 "(HLS /hls/%s%s_%s.m3u8) manba=%s",
                 mode, sim, channel, m.codec,
                 m.prefix, sim, channel,
                 (_MUXER_SRC.get(key) or (None,))[0] or "?")
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
            log.info("JT1078: oqim yopildi (idle) %s",
                     _src(_MUXER_SRC.get(key), key))
            m.close_reason = (f"data kelmadi ({int(MUXER_IDLE_TIMEOUT)}s)"
                              if m.bytes == 0 else "kamera uzildi")
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
        try:
            _prune_diag()  # yopilgan oqim diagnostikasi cheksiz o'smasin
        except Exception:  # noqa: BLE001
            log.exception("JT1078: diagnostika tozalash xatosi")
        # Server to'lmasin: har ~10 daqiqada eski/bo'sh HLS fayllarni tozalash
        ticks += 1
        if ticks % 20 == 0:
            try:
                cleanup_hls_dir()
            except Exception:  # noqa: BLE001
                log.exception("JT1078: HLS tozalash xatosi")


class _Session:
    """Bitta TCP ulanishdagi video oqim(lar)ni kadrlarga (access-unit) yig'adi.

    Kadr chegarasi IKKI belgi bo'yicha aniqlanadi (lkmio/lkm kabi): bir xil
    (sim, kanal) uchun *timestamp o'zgarishi* yoki subMark `FIRST`. Sabab:
    ba'zi qurilmalar subMark'ni noto'g'ri (hammasini atomic) belgilaydi, bunda
    faqat timestamp ishonchli; boshqalari esa timestamp'ni yangilamaydi —
    shunda subMark (FIRST/LAST) yordam beradi. Shu tariqa ikkala nosozlik
    holatida ham kadr to'g'ri yig'iladi."""

    def __init__(self, peer=None):
        # (sim,ch) -> {"buf": bytearray, "ts", "dt", "codec"} — joriy kadr
        self.peer = peer
        # Shu ulanishda ko'rilgan (sim, kanal) juftliklari — log yorlig'i uchun.
        self.keys: set = set()
        self.frames: dict = {}
        self.primed: set = set()  # I-kadr ko'rilgan (sim,ch) — shundagina yozamiz
        self.codec: dict = {}     # (sim,ch) -> ffmpeg kirish kodeki (h264/hevc)

    def on_packet(self, p: dict) -> None:
        typ = p["data_type"]
        key = (p["sim"], p["channel"])
        # Log yorlig'i uchun: qaysi kamera (IMEI) qaysi kanalni yuboryapti.
        self.keys.add(key)
        _remember_peer(self.peer, (key,))
        # Audio va shaffof/xizmat paketlari tashlanadi (faqat video olinadi).
        if typ not in _VIDEO_TYPES:
            return
        sub = p["subpacket"]
        # PT'dan video kodekni aniqlaymiz (98=H.264, 99=H.265) — H.265 kameralar
        # uchun ffmpeg kirish formati `hevc` bo'lishi kerak.
        codec = _codec_for_pt(p.get("pt"))
        self.codec[key] = codec
        payload = p["payload"]
        if not payload:
            return
        ts = p.get("ts")
        st = self.frames.get(key)
        if st is None:
            st = self.frames[key] = {"buf": bytearray(), "ts": ts,
                                     "dt": typ, "codec": codec}
        elif st["buf"] and (ts != st["ts"] or sub == SUB_FIRST):
            # timestamp o'zgardi yoki yangi kadr boshi -> avvalgisini chiqaramiz
            self._emit(key, st["dt"], st["codec"], bytes(st["buf"]))
            st["buf"] = bytearray()
        if not st["buf"]:
            # Yangi kadr — tur/kodek joriy paketdan olinadi. Aks holda
            # SUB_LAST yo'lida eski (oldingi kadrning) tur qolib, birinchi
            # I-kadr "P" deb hisoblanib, oqim umuman boshlanmasdi.
            st["ts"], st["dt"], st["codec"] = ts, typ, codec
        st["buf"].extend(payload)
        if sub == SUB_LAST or len(st["buf"]) >= FRAME_MAX_BYTES:
            # oxirgi subpaket (yoki bufer chegarasi) — kadrni darhol chiqaramiz
            self._emit(key, st["dt"], st["codec"], bytes(st["buf"]))
            st["buf"] = bytearray()

    def _emit(self, key, typ: int, codec: str, au: bytes) -> None:
        if not au:
            return
        # ffmpeg SPS/PPS'siz P-kadrlarni dekodlay olmaydi — birinchi I-kadr
        # kelgunga qadar yozmaymiz (har bir yangi ulanishda keyframe kutamiz,
        # chunki uzilishdan keyingi P-kadr buzilgan bo'ladi).
        if key not in self.primed:
            if typ != DT_VIDEO_I:
                return
            self.primed.add(key)
        mode = "playback" if _playback_active(key[0], key[1]) else "live"
        if mode == "playback":
            _touch_playback(key[0], key[1])
        m = _get_muxer(key[0], key[1], mode, codec=codec, peer=self.peer)
        if m.bytes == 0 and typ != DT_VIDEO_I:
            # Muxer ENDIGINA ochildi (kodek o'zgardi).
            # Yangi ffmpeg SPS/PPS'siz P-kadrni dekodlay olmaydi va HLS'ga
            # umuman yozmaydi — keyingi I-kadrgacha kutamiz.
            return
        _STATS["access_units"] += 1
        m.write(au)
        # Diagnostika: muxer'ga haqiqatan yozilgan kadrlar shu yerda sanaladi
        # (kalit kadr vaqti — "nega video qora?" savolining asosiy belgisi).
        st = _STREAMS.get(m.key)
        if st is not None:
            st["bytes"] = m.bytes
            st["dropped"] = getattr(m, "dropped", 0)
            st["frames"] = int(st.get("frames") or 0) + 1
            if typ == DT_VIDEO_I:
                st["keyframes"] = int(st.get("keyframes") or 0) + 1
                st["last_keyframe_at"] = _now()

    def note(self, msg: str) -> None:
        """Diagnostika: ulanish darajasidagi xatoni shu sessiya oqimlariga yozish."""
        for sim, ch in list(self.frames.keys()):
            mode = "playback" if _playback_active(sim, ch) else "live"
            m = _MUXERS.get(_muxer_key(sim, ch, mode))
            fn = getattr(m, "note", None)
            if fn is not None:
                fn(msg)

    def close(self) -> None:
        # Ulanish yopildi — muxerlarni YOPMAYMIZ (doimiy, qayta ulanishda
        # davom etadi). Kutayotgan (chiqarilmagan) kadrlarni bo'shatamiz, chunki
        # keyingi paket kelmaydi — aks holda oxirgi kadr yo'qoladi.
        for key, st in list(self.frames.items()):
            if st["buf"]:
                self._emit(key, st["dt"], st["codec"], bytes(st["buf"]))
            st["buf"] = bytearray()
        self.frames.clear()
        self.primed.clear()
        self.codec.clear()


async def _peek(reader: asyncio.StreamReader, timeout: float = 60.0) -> bytes:
    """Birinchi baytlarni kutib oladi — protokolni aniqlash uchun."""
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        return b""


async def _handle(reader: asyncio.StreamReader,
                  writer: asyncio.StreamWriter) -> None:
    """Yangi ulanish — birinchi paketga qarab protokolni aniqlab yo'naltiradi.

    Qurilmada bitta "server port" maydoni bo'lsa (yoki o'rnatuvchi 6107'ni
    platforma porti qilib qo'ysa), JT808 signaling paketlari media portga
    keladi va ilgari jimgina tashlanardi — qurilma "oflayn" ko'rinardi.
    Endi bunday paket JT808 listener'iga o'tkaziladi.
    """
    peer = writer.get_extra_info("peername")
    # Diagnostika: ulanish qabul qilishning o'zidayoq yoziladi (qurilma jim
    # tursa ham "urinish bo'ldimi?" shu qatordan ko'rinadi).
    log.info("JT1078: yangi ulanish %s", _src(peer, *_known_peers(peer)))
    first = await _peek(reader)
    # Birinchi baytlar: protokol aniqlanmasa "qurilma nega oflayn?" savoliga
    # javob shu yerda.
    log.info("JT1078: %s — birinchi %d bayt: %s",
             _src(peer, *_known_peers(peer)), len(first), first[:16].hex())
    if first[:1] == b"\x7e":
        log.warning("JT1078 media portiga JT808 signaling keldi %s — "
                    "JT808 listener'ga o'tkazildi",
                    _src(peer, *_known_peers(peer)))
        from . import jt808  # aylanma importdan qochish uchun (lazy)
        await jt808.handle_session(reader, writer, first)
        return
    await handle_session(reader, writer, first)


async def handle_session(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter,
                         first: bytes = b"") -> None:
    """JT1078 media sessiyasi. `first` — allaqachon o'qilgan baytlar."""
    peer = writer.get_extra_info("peername")
    sess = _Session(peer)
    if peer:
        _SESSIONS[peer] = sess
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
    # "media ulanishi" qatori IMEI/kanal bilan chiqishi uchun birinchi paket
    # ishlanishini kutamiz (har bir qatorda qaysi kamera ekani ko'rinishi kerak).
    announced = False
    buf = first
    try:
        while True:
            # TCP paketlarni birlashtirishi/bo'laklashi mumkin — FLAG+body_len
            # bilan aniq freymleymiz. To'liq paketlar olinadi, qoldiq kutadi.
            # Bufer avval ishlanadi (jumladan `first` — protokol o'tkazilganda
            # birinchi paket kutib qolmasligi uchun).
            pkts, buf = extract_packets(buf)
            for p in pkts:
                _STATS["packets"] += 1
                sess.on_packet(p)
            if not announced:
                # Paket kelmagan/tanish bo'lmasa IMEI aniqlanmaydi — baribir
                # ulanish fakti ko'rinsin ("qurilma urindimi?" savoliga javob).
                announced = True
                log.info("JT1078: media ulanishi %s",
                         _src(peer, *sess.keys) if sess.keys
                         else f"{peer} (IMEI aniqlanmadi)")
            # Desinxronizatsiyada bufer cheksiz o'smasligi uchun himoya.
            if len(buf) > 8 * 1024 * 1024:
                log.warning("JT1078: bufer juda katta (%s) — tozalandi",
                            _src(peer, *sess.keys))
                _STATS["errors"] += 1
                sess.note("bufer to'ldi — paketlar sinxronizatsiyasi buzildi")
                buf = b""
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
    except (asyncio.IncompleteReadError, ConnectionResetError):
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("JT1078: ulanishda xato (%s)", _src(peer, *sess.keys))
        sess.note(f"ulanishda xato: {exc}")
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
        if peer:
            _SESSIONS.pop(peer, None)
        log.info("JT1078: media uzildi %s", _src(peer, *sess.keys))


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
