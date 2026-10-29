"""JT1078 depacketize testlari: 30 baytli sarlavha + subpaket yig'ish.

Sarlavha real CMSV6 qurilmasi raw dump'iga mos: flag(4) + vendor/seq(4) +
sim(6) + kanal(1) + data_type(1) + ts(8) + lastI(2) + lastF(2) + bodylen(2).
data_type bayti: bits5-4 = kadr turi, bits1-0 = subpaket belgisi. Muxer
(ffmpeg) monkeypatch qilinadi — faqat freyming va yig'ish mantiq'i tekshiriladi.
"""

import asyncio

import pytest

from bm_automation.app.gps import jt1078
from bm_automation.app.gps.jt1078 import (AUDIO_HEADER_LEN, DT_AUDIO,
                                          DT_TRANSPARENT, DT_VIDEO_B,
                                          DT_VIDEO_I, DT_VIDEO_P, FLAG,
                                          SUB_ATOMIC, SUB_FIRST, SUB_LAST,
                                          SUB_MIDDLE, TRANSPARENT_HEADER_LEN)


def _pkt(typ, payload, sub=SUB_ATOMIC, pt=98, sim="000000000123", ch=1,
         ts=1700000000, seq=0):
    """Real qurilma sarlavhasiga mos JT1078 paketini qurish.

    pt — RTP payload type (98=H.264, 99=H.265). Sarlavha uzunligi turiga
    bog'liq: video 30, audio 26, shaffof 18 bayt.
    """
    dtb = ((typ & 0x0F) << 4) | (sub & 0x0F)
    head = (FLAG + bytes([0x81, 0x80 | (pt & 0x7F)]) + seq.to_bytes(2, "big")
            + bytes.fromhex(sim) + bytes([ch, dtb]))
    if typ != DT_TRANSPARENT:
        head += ts.to_bytes(8, "big")
    if typ in (DT_VIDEO_I, DT_VIDEO_P, DT_VIDEO_B):
        head += b"\x00\x00" + b"\x00\x00"
    head += len(payload).to_bytes(2, "big")
    return head + payload


class _FakeMuxer:
    instances = []

    def __init__(self, sim, channel, mode="live", codec="h264"):
        self.sim = sim
        self.channel = channel
        self.mode = mode
        self.codec = codec
        self.key = f"{sim}_{channel}" if mode == "live" else f"pb:{sim}_{channel}"
        self.prefix = "jt_" if mode == "live" else "jtpb_"
        self.bytes = 0
        self.since = 0.0
        self.last_active = 0.0
        self.closed = False
        self.aus = []
        self.last_frame_at = None
        self.re_requests = 0
        self.notes = []
        _FakeMuxer.instances.append(self)

    def write(self, au, ts=None):
        self.bytes += len(au)
        self.aus.append(au)

    def note(self, msg):
        self.notes.append(msg)

    def close(self):
        self.closed = True
        jt1078._MUXERS.pop(self.key, None)
        jt1078._STREAMS.pop(self.key, None)


def _registries_clear():
    jt1078._MUXERS.clear()
    jt1078._STREAMS.clear()
    jt1078._DIAG.clear()
    jt1078._SUPPRESS.clear()
    jt1078._LIVE_REQ.clear()
    jt1078._STATS.update({"access_units": 0})
    jt1078.clear_playback()


def _reset(monkeypatch):
    """Monkeypatch muxer + toza registry (testlar orasi izolyatsiya)."""
    monkeypatch.setattr(jt1078, "_Muxer", _FakeMuxer)
    _FakeMuxer.instances.clear()
    _registries_clear()


def _reset_real(monkeypatch, tmp_path):
    """Haqiqiy `_Muxer` bilan sinash: ffmpeg jarayoni yo'q, HLS tmp'da.

    Shu tariqa muxerning diagnostikasi (restart soni, oxirgi xato, log)
    ffmpeg o'rnatilgan-o'rnatilmaganiga bog'liq bo'lmaydi.
    """
    _registries_clear()
    monkeypatch.setattr(jt1078.config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(jt1078.config, "JT1078_RAW_DUMP", False)
    monkeypatch.setattr(jt1078, "ffmpeg_available", lambda: False)


def _feed(sess, typ, sub, payload, sim="123", ch=1, pt=None, ts=0):
    """Bitta JT1078 paketini session'ga beradi.

    `ts` — real qurilma har kadrda timestamp'ni oshiradi va kadr chegarasi
    shu maydon bo'yicha ham aniqlanadi: shuning uchun ATOMIC kadrlarni
    sinaganda ts'ni ham oshirib berish kerak (bitta kadrning bo'laklari esa
    bir xil ts bilan keladi).
    """
    sess.on_packet({"sim": sim, "channel": ch, "data_type": typ,
                    "subpacket": sub, "pt": pt, "ts": ts, "payload": payload})


# ---------------------------------------------------------------- parse

def test_parse_video_packet():
    raw = _pkt(DT_VIDEO_I, b"PAYLOAD", sub=SUB_FIRST, ch=3)
    p, consumed = jt1078.parse_packet(raw)
    assert consumed == len(raw)
    assert p["sim"] == "000000000123"
    assert p["channel"] == 3
    assert p["data_type"] == DT_VIDEO_I
    assert p["subpacket"] == SUB_FIRST
    assert p["payload"] == b"PAYLOAD"
    assert p["ts"] == 1700000000


def test_parse_transparent_type():
    """data_type=0100 (4) — bits5-4=4, bits1-0=0 (atomic)."""
    dtb = (DT_TRANSPARENT << 4) | SUB_ATOMIC
    assert dtb == 0x40
    raw = _pkt(DT_TRANSPARENT, b"XXXX", sub=SUB_ATOMIC)
    p, consumed = jt1078.parse_packet(raw)
    assert consumed == len(raw)
    assert p["data_type"] == DT_TRANSPARENT
    assert p["payload"] == b"XXXX"
    assert p["ts"] == 0        # shaffof turida timestamp yo'q


def test_transparent_header_length():
    """Shaffof (0100) sarlavha 18 bayt — timestamp/oraliq maydonlari yo'q."""
    raw = _pkt(DT_TRANSPARENT, b"Z")
    assert len(raw) == TRANSPARENT_HEADER_LEN + 1
    p, _ = jt1078.parse_packet(raw)
    assert p is not None and p["payload"] == b"Z"


def test_audio_header_length():
    """Audio (0011) sarlavha 26 bayt — timestamp bor, oraliq maydonlar yo'q."""
    raw = _pkt(DT_AUDIO, b"audio")
    assert len(raw) == AUDIO_HEADER_LEN + 5
    p, _ = jt1078.parse_packet(raw)
    assert p is not None and p["data_type"] == DT_AUDIO
    assert p["payload"] == b"audio"


def test_parse_video_b_frame():
    raw = _pkt(DT_VIDEO_B, b"BFRAME", sub=SUB_ATOMIC)
    p, consumed = jt1078.parse_packet(raw)
    assert consumed == len(raw)
    assert p["data_type"] == DT_VIDEO_B
    assert p["payload"] == b"BFRAME"


def test_parse_extracts_pt_h265():
    """RTP payload type ajratiladi (99 -> H.265)."""
    raw = _pkt(DT_VIDEO_I, b"x", pt=99)
    p, _ = jt1078.parse_packet(raw)
    assert p["pt"] == 99


def test_parse_default_pt_h264():
    raw = _pkt(DT_VIDEO_I, b"x")            # standart pt=98
    p, _ = jt1078.parse_packet(raw)
    assert p["pt"] == 98


def test_codec_for_pt_mapping():
    assert jt1078._codec_for_pt(98) == "h264"
    assert jt1078._codec_for_pt(99) == "hevc"
    assert jt1078._codec_for_pt(None) == "h264"   # noma'lum -> standart
    assert jt1078._codec_for_pt(100) == "h264"    # AVS qo'llanmaydi -> fallback


def test_sim_keeps_leading_zeros():
    """SIM BCD dekodida yetakchi nollar yo'qolmasligi kerak."""
    raw = _pkt(DT_VIDEO_I, b"x", sim="012345678901")
    p, _ = jt1078.parse_packet(raw)
    assert p["sim"] == "012345678901"


def test_all_subpacket_values_parsed():
    for sub in (SUB_ATOMIC, SUB_FIRST, SUB_MIDDLE, SUB_LAST):
        raw = _pkt(DT_VIDEO_I, b"x", sub=sub)
        p, _ = jt1078.parse_packet(raw)
        assert p["subpacket"] == sub


def test_audio_incomplete_returns_none():
    raw = _pkt(DT_AUDIO, b"x" * 10)
    p, consumed = jt1078.parse_packet(raw[:20])   # sarlavha (26) to'liq emas
    assert p is None and consumed == 0


def test_extract_mixed_types_no_desync():
    """Audio + shaffof + video bitta buferda — turlicha sarlavha uzunligi
    to'g'ri o'qilib, desinxron bo'lmasligi kerak."""
    raw = (_pkt(DT_AUDIO, b"a" * 10) + _pkt(DT_TRANSPARENT, b"t" * 5)
           + _pkt(DT_VIDEO_I, b"v" * 20))
    pkts, rest = jt1078.extract_packets(raw)
    assert [p["data_type"] for p in pkts] == [DT_AUDIO, DT_TRANSPARENT,
                                              DT_VIDEO_I]
    assert [p["payload"] for p in pkts] == [b"a" * 10, b"t" * 5, b"v" * 20]
    assert rest == b""


def test_parse_incomplete_returns_none():
    raw = _pkt(DT_VIDEO_P, b"X" * 100)
    p, consumed = jt1078.parse_packet(raw[:20])   # body to'liq emas
    assert p is None and consumed == 0


def test_parse_bad_flag():
    p, consumed = jt1078.parse_packet(b"\x00" * 40)
    assert p is None and consumed == 0


def test_extract_coalesced_packets():
    """Bitta buferda 3 ta paket birlashib kelsa — hammasi ajratiladi."""
    raw = (_pkt(DT_VIDEO_I, b"A", seq=0) + _pkt(DT_VIDEO_I, b"B", seq=1)
           + _pkt(DT_VIDEO_P, b"C", seq=2))
    pkts, rest = jt1078.extract_packets(raw)
    assert [p["payload"] for p in pkts] == [b"A", b"B", b"C"]
    assert rest == b""


def test_extract_split_packet_buffered():
    """Paket ikki read()'ga bo'linsa — birinchisi kutadi, ikkinchisi oladi."""
    raw = _pkt(DT_VIDEO_I, b"HELLO")
    half = len(raw) // 2
    pkts1, rest1 = jt1078.extract_packets(raw[:half])
    assert pkts1 == [] and rest1 == raw[:half]   # to'liq emas, saqlanadi
    pkts2, rest2 = jt1078.extract_packets(rest1 + raw[half:])
    assert len(pkts2) == 1 and pkts2[0]["payload"] == b"HELLO"
    assert rest2 == b""


def test_extract_skips_leading_garbage():
    raw = b"\x99\x99" + _pkt(DT_VIDEO_I, b"G")
    pkts, rest = jt1078.extract_packets(raw)
    assert len(pkts) == 1 and pkts[0]["payload"] == b"G"


# ---------------------------------------------------------------- session

def test_session_assembles_subpackages(monkeypatch):
    """first+middle+last bitta access-unit'ga yig'iladi."""
    _reset(monkeypatch)
    sess = jt1078._Session()

    _feed(sess, DT_VIDEO_I, SUB_FIRST, b"A")
    _feed(sess, DT_VIDEO_I, SUB_MIDDLE, b"B")
    _feed(sess, DT_VIDEO_I, SUB_LAST, b"C")     # -> b"ABC" emit
    _feed(sess, DT_VIDEO_P, SUB_FIRST, b"D")
    _feed(sess, DT_VIDEO_P, SUB_LAST, b"E")     # -> b"DE" emit

    assert len(_FakeMuxer.instances) == 1
    assert _FakeMuxer.instances[0].aus == [b"ABC", b"DE"]


def test_session_waits_for_keyframe(monkeypatch):
    """Birinchi I-kadr kelgunga qadar P-kadrlar yozilmaydi."""
    _reset(monkeypatch)
    sess = jt1078._Session()

    _feed(sess, DT_VIDEO_P, SUB_FIRST, b"P1")   # hali prime yo'q
    _feed(sess, DT_VIDEO_P, SUB_LAST, b"P2")    # -> tashlanadi
    assert _FakeMuxer.instances == []

    _feed(sess, DT_VIDEO_I, SUB_LAST, b"I")     # -> prime, b"I" emit
    _feed(sess, DT_VIDEO_P, SUB_LAST, b"P3")    # -> b"P3" emit
    assert _FakeMuxer.instances[0].aus == [b"I", b"P3"]


def test_session_ignores_audio_and_transparent(monkeypatch):
    """Audio va shaffof/xizmat paketlari butunlay tashlanadi — video muxer
    ochilmaydi (qo'llanilmaydi)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_AUDIO, SUB_ATOMIC, b"audio")
    _feed(sess, DT_TRANSPARENT, SUB_ATOMIC, b"priv")
    _feed(sess, DT_TRANSPARENT, SUB_FIRST, b"priv2")
    sess.close()
    assert _FakeMuxer.instances == []


def test_session_accepts_b_frames(monkeypatch):
    """B-kadrlar ham I/P kabi muxerga uzatilishi kerak."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", ts=1)
    _feed(sess, DT_VIDEO_B, SUB_ATOMIC, b"B", ts=2)   # I-kadr chiqadi
    sess.close()                                        # B-kadr chiqadi
    assert _FakeMuxer.instances[0].aus == [b"I", b"B"]


def test_session_separates_channels(monkeypatch):
    _reset(monkeypatch)
    sess = jt1078._Session()

    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"1a", ch=1, ts=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"2a", ch=2, ts=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"1b", ch=1, ts=2)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"2b", ch=2, ts=2)
    sess.close()
    assert sorted(m.key for m in _FakeMuxer.instances) == ["123_1", "123_2"]
    by = {m.key: m.aus for m in _FakeMuxer.instances}
    assert by["123_1"] == [b"1a", b"1b"]
    assert by["123_2"] == [b"2a", b"2b"]


def test_session_h265_codec(monkeypatch):
    """PT=99 (H.265) — muxer hevc kirish formati bilan ochiladi."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", pt=99)
    sess.close()
    assert _FakeMuxer.instances[0].codec == "hevc"


def test_session_h264_codec_default(monkeypatch):
    """PT yo'q bo'lsa ham H.264 deb qaraladi (eski qurilmalar)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")      # pt=None
    sess.close()
    assert _FakeMuxer.instances[0].codec == "h264"


def test_session_codec_change_recreates_muxer(monkeypatch):
    """Bir kanalda kodek o'zgarsa muxer qayta ochiladi (h264 -> hevc)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", pt=98, ts=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I2", pt=99, ts=2)
    sess.close()
    assert [m.codec for m in _FakeMuxer.instances] == ["h264", "hevc"]


def test_frame_buffer_cap_forces_emit(monkeypatch):
    """Timestamp o'zgarmasa ham bufer chegaraga yetsa kadr chiqariladi —
    bufer cheksiz o'sib xotirani yemasligi kerak."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "FRAME_MAX_BYTES", 10)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"x" * 8, ts=0)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"y" * 8, ts=0)
    assert _FakeMuxer.instances[0].aus == [b"x" * 8 + b"y" * 8]
    assert bytes(sess.frames[("123", 1)]["buf"]) == b""


def test_session_discards_partial_frame_on_close(monkeypatch):
    """Uzilishda yarim subpaket (SUB_LAST kelmagan) ffmpeg'ga YUBORILMAYDI —
    tugallanmagan H.264 buzilgan kadr beradi (videoda pirpirak)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_LAST, b"I")            # prime + emit I
    _feed(sess, DT_VIDEO_P, SUB_FIRST, b"P-yarim")     # LAST yo'q — yarim
    sess.close()
    assert _FakeMuxer.instances[0].aus == [b"I"]        # yarim P tashlandi


def test_session_keeps_complete_atomic_frame_on_close(monkeypatch):
    """SUB_ATOMIC (butun kadr) uzilishda SAQLANADI (bugungi xatti-harakat)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"whole-I", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"whole-P", ts=2)
    sess.close()
    assert _FakeMuxer.instances[0].aus == [b"whole-I", b"whole-P"]


# ------------------------------------------------- playback pacing (tezlik)

def test_estimate_fps_25():
    ts = [i * 40 for i in range(26)]      # 25 oraliq * 40ms = 1000ms -> 25 fps
    assert jt1078.estimate_fps(ts) == 25


def test_estimate_fps_rejects_bad_samples():
    assert jt1078.estimate_fps([1, 1, 1, 1]) is None       # span 0
    assert jt1078.estimate_fps([100, 50, 20]) is None      # namuna kam
    assert jt1078.estimate_fps([1, 2, 3, 4, 100000]) is None  # juda sekin


def test_estimate_fps_ignores_wild_jump():
    """Yakka ts sakrashi bahoni buzmaydi (median) — maydonda jonli oqim
    boshida 4 namunada 940s sakrash kuzatilgan; birinchi-oxirgi usulida
    baho None bo'lib, standart fps'ga tushib qolardi."""
    ts = [1000 + i * 40 for i in range(3)]     # eski baza (yozuv qoldig'i)
    ts += [941120 + i * 40 for i in range(3)]  # sakrashdan keyingi baza
    assert jt1078.estimate_fps(ts) == 25


def test_estimate_fps_handles_reordered_b_frames():
    """B-kadr tartibsizligi (ts kelish tartibida orqaga sakraydi) fps'ni
    buzmasligi kerak — ffmpeg 'Increasing reorder buffer' shundan chiqardi."""
    ts = [1000, 1120, 1040, 1160, 1080, 1200, 1120, 1240, 1160, 1280]
    assert jt1078.estimate_fps(ts) == 25


def test_estimate_fps_handles_interleaved_series():
    """Ikki oqim aralashib kelganda ham haqiqiy oraliq topiladi."""
    ts = []
    for i in range(10):
        ts += [5000 + i * 40, 900000 + i * 40]
    assert jt1078.estimate_fps(ts) == 25


def test_pacer_first_packet_no_wait():
    p = jt1078._Pacer(max_sleep=1.0)
    assert p.delay(1000, now=0.0) == 0.0


def test_pacer_waits_to_stay_realtime():
    """ts 2000ms oldinga, 0.5s o'tgan -> 1.5s kutamiz (real vaqt)."""
    p = jt1078._Pacer(max_sleep=5.0)
    p.delay(1000, now=0.0)
    assert p.delay(3000, now=0.5) == pytest.approx(1.5)


def test_pacer_caps_sleep_and_resets_on_jump():
    p = jt1078._Pacer(max_sleep=0.25)
    p.delay(1000, now=0.0)
    assert p.delay(2000, now=0.0) == pytest.approx(0.25)   # cap
    assert p.delay(100000, now=0.0) == 0.0                 # katta sakrash reset
    assert p.delay(99000, now=0.0) == 0.0                  # orqaga -> reset


def test_pacer_ignores_invalid_ts():
    p = jt1078._Pacer()
    assert p.delay(0, now=0.0) == 0.0
    assert p.delay(None, now=0.0) == 0.0


def test_pacer_tolerates_reordered_timestamps():
    """Kichik orqaga qadam (B-kadr/qayta yuborish) bazani QAYTA O'RNATMAYDI.

    Avvalgi xato: har qanday `ts < last` bazani nolga qaytarardi — natijada
    pacing amalda ishlamay, yozuv real vaqtdan tez oqib ketardi."""
    p = jt1078._Pacer(max_sleep=5.0)
    assert p.delay(1000, now=0.0) == 0.0
    assert p.delay(1080, now=0.02) == pytest.approx(0.06)
    assert p.delay(1040, now=0.03) == pytest.approx(0.05)   # orqaga qadam
    assert p.delay(1200, now=0.10) == pytest.approx(0.10)   # baza saqlangan
    assert p.delay(1180, now=0.15) == pytest.approx(0.05)


# ---------------------------------------------- jonli/yozuv priming (fps)

def _priming_muxer(monkeypatch, tmp_path, mode="live"):
    """Haqiqiy _Muxer, lekin ffmpeg o'rniga yozib oluvchi `_spawn`/`_enqueue`.

    Qaytaradi: (muxer, spawned, sent). `spawned` — (fps_in, fps_out) lar,
    `sent` — ffmpeg'ga ketgan AU'lar (bufer oqizilgandan keyin)."""
    _registries_clear()
    monkeypatch.setattr(jt1078.config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(jt1078.config, "JT1078_RAW_DUMP", False)
    monkeypatch.setattr(jt1078, "ffmpeg_available", lambda: True)
    spawned, sent = [], []

    def fake_spawn(self, fi, fo):
        self._started = True
        self.fps_in, self.fps_out = fi, fo
        spawned.append((fi, fo))

    monkeypatch.setattr(jt1078._Muxer, "_spawn", fake_spawn)
    monkeypatch.setattr(jt1078._Muxer, "_enqueue",
                        lambda self, au: sent.append(au))
    return jt1078._Muxer("123", 1, mode=mode), spawned, sent


def test_live_priming_measures_fps(monkeypatch, tmp_path):
    """Jonli oqim DARHOL ochilmaydi: REAL vaqt oynasi (600ms) to'lgach
    o'lchangan fps bilan muxer ochiladi va buferlangan kadrlar oqiziladi."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(jt1078, "_now", lambda: clock["t"])
    m, spawned, sent = _priming_muxer(monkeypatch, tmp_path, mode="live")
    assert spawned == []                       # hali ochilmadi — bufer
    # 25 fps (ts oraliq 40ms); har yozuvdan keyin real vaqt +45ms
    for i in range(16):
        m.write(b"au", ts=1000000 + i * 40)
        clock["t"] += 0.045
    assert spawned == [(25, 25)]               # o'lchandi — sekin/tez emas
    assert len(sent) == 16                     # bufer oqizildi
    assert m._pending == []


def test_live_priming_wall_backup_when_ts_broken(monkeypatch, tmp_path):
    """ts butunlay yaroqsiz bo'lsa (hammasi 0) jonli oqim REAL kelish
    tezligidan baholanadi — aks holda qat'iy standart 20 da qolib ketardi."""
    clock = {"t": 3000.0}
    monkeypatch.setattr(jt1078, "_now", lambda: clock["t"])
    m, spawned, _ = _priming_muxer(monkeypatch, tmp_path, mode="live")
    for _ in range(16):
        m.write(b"au", ts=0)                   # qurilma ts bermayapti
        clock["t"] += 0.045
    # 630ms real oraliqda 15 AU oqizilgan -> ~22 fps (standart 20 EMAS)
    assert spawned == [(22, 22)]


def test_live_priming_fallback_uses_config_fps(monkeypatch, tmp_path):
    """Namuna yetmasa (juda sekin kamera) cap o'tgach standart fps bilan
    ochiladi — oqim baribir boshlanishi shart."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(jt1078, "_now", lambda: clock["t"])
    m, spawned, _ = _priming_muxer(monkeypatch, tmp_path, mode="live")
    m.write(b"au", ts=1000)                    # 1 namuna — baholab bo'lmaydi
    assert spawned == []
    clock["t"] += jt1078.config.JT1078_LIVE_PRIME_CAP_S + 0.1
    m.write(b"au", ts=1001)
    want = int(jt1078.config.JT1078_FPS_IN)
    assert spawned == [(want, want)]


def test_live_priming_disabled_spawns_immediately(monkeypatch, tmp_path):
    """GPS_JT1078_LIVE_PRIME_MS=0 — eski xatti-harakat qaytadi (darhol)."""
    monkeypatch.setattr(jt1078.config, "JT1078_LIVE_PRIME_MS", 0)
    _, spawned, _ = _priming_muxer(monkeypatch, tmp_path, mode="live")
    assert spawned == [(jt1078.config.JT1078_FPS_IN,
                        jt1078.config.JT1078_FPS_OUT)]


def test_playback_priming_still_measures(monkeypatch, tmp_path):
    """Yozuv (playback) priming avvalgidek ishlaydi (regressiya yo'q)."""
    clock = {"t": 2000.0}
    monkeypatch.setattr(jt1078, "_now", lambda: clock["t"])
    m, spawned, sent = _priming_muxer(monkeypatch, tmp_path, mode="playback")
    # 13 fps (ts oraliq 77ms): ~1500ms real oynada o'lchanadi
    for i in range(21):
        m.write(b"au", ts=2000000 + i * 77)
        clock["t"] += 0.08
    assert spawned == [(13, 13)]
    assert len(sent) == 21


# ---------------------------------------------------------------- persistence

def test_muxer_persists_across_reconnects(monkeypatch):
    """Kamera uzilib qayta ulansa — o'sha muxer davom etadi (yangi emas).

    Bu HLS playlist'ini har bir uzilishda ENDLIST bo'lib qolishining oldini
    oladi: jonli ko'rinish qayta ulanishlar orasida ham davom etadi.
    """
    _reset(monkeypatch)

    s1 = jt1078._Session()
    _feed(s1, DT_VIDEO_I, SUB_ATOMIC, b"A", ts=1)
    s1.close()                       # kamera uzildi — muxer yopilmaydi
    assert len(_FakeMuxer.instances) == 1
    assert not _FakeMuxer.instances[0].closed
    assert "123_1" in jt1078._MUXERS

    s2 = jt1078._Session()           # qayta ulanish
    _feed(s2, DT_VIDEO_I, SUB_ATOMIC, b"B", ts=1)  # yangi ulanishda I-kadr
    _feed(s2, DT_VIDEO_P, SUB_ATOMIC, b"C", ts=2)
    s2.close()
    assert len(_FakeMuxer.instances) == 1     # YANGI muxer yaratilmadi
    assert _FakeMuxer.instances[0].aus == [b"A", b"B", b"C"]


def test_reconnect_still_waits_for_keyframe(monkeypatch):
    """Qayta ulanishda eski P-kadr yozilmaydi — I-kadr kutiladi."""
    _reset(monkeypatch)
    s1 = jt1078._Session()
    _feed(s1, DT_VIDEO_I, SUB_ATOMIC, b"A", ts=1)
    s1.close()
    s2 = jt1078._Session()
    _feed(s2, DT_VIDEO_P, SUB_ATOMIC, b"orphan", ts=1)   # prime yo'q -> tashlanadi
    assert _FakeMuxer.instances[0].aus == [b"A"]
    _feed(s2, DT_VIDEO_I, SUB_ATOMIC, b"I2", ts=2)
    s2.close()
    assert _FakeMuxer.instances[0].aus == [b"A", b"I2"]


def test_playback_mode_routes_to_separate_muxer(monkeypatch):
    """Yozuv (playback) so'ralganda oqim alohida muxerga yo'naltiriladi."""
    _reset(monkeypatch)
    jt1078.set_playback("123", 1, seconds=60)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")
    sess.close()
    assert [m.key for m in _FakeMuxer.instances] == ["pb:123_1"]
    assert _FakeMuxer.instances[0].mode == "playback"


def test_live_connection_not_rerouted_when_playback_starts(monkeypatch):
    """Jonli ulanish ochilgandan keyin playback so'ralsa ham u JONLI qoladi.

    Ilgari rejim har kadrda global `_playback_active` bo'yicha tanlanardi —
    jonli va yozuv timestamp'lari bitta muxer'da aralashib, ffmpeg
    'Increasing reorder buffer' berardi."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"L1", ts=1000)
    jt1078.set_playback("123", 1, seconds=60)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"L2", ts=1040)
    sess.close()
    assert [m.key for m in _FakeMuxer.instances] == ["123_1"]
    assert _FakeMuxer.instances[0].mode == "live"
    assert _FakeMuxer.instances[0].aus == [b"L1", b"L2"]


def test_connection_switches_to_playback_on_backward_jump(monkeypatch):
    """Bitta ulanishda yozuv oqimi boshlandi (ts katta orqaga sakradi)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"L1", ts=10000000)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"L2", ts=1000040)
    jt1078.set_playback("123", 1, seconds=60)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", ts=1000000)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P", ts=1000040)
    sess.close()
    assert [m.mode for m in _FakeMuxer.instances] == ["live", "playback"]
    assert _FakeMuxer.instances[0].aus == [b"L1"]
    assert _FakeMuxer.instances[1].aus[0] == b"I"


def test_playback_expiry_returns_to_live(monkeypatch):
    """Playback muddati o'tsa oqim yana jonli deb hisoblanadi."""
    _reset(monkeypatch)
    jt1078.set_playback("123", 1, seconds=60)
    jt1078._PLAYBACK["123_1"] = 0  # muddat o'tib ketdi
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")
    sess.close()
    assert [m.key for m in _FakeMuxer.instances] == ["123_1"]


def test_playback_window_extends_while_streaming(monkeypatch):
    """Oqim kelganda playback oynasi uzayadi (uzun yozuv 'jonli'ga o'tmasin)."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "_now", lambda: 1000.0)
    jt1078.set_playback("123", 1, seconds=60)
    assert jt1078._PLAYBACK["123_1"] == 1060.0
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")
    sess.close()
    want = 1000.0 + jt1078.config.JT1078_PLAYBACK_WINDOW_S
    assert jt1078._PLAYBACK["123_1"] == want
    assert _FakeMuxer.instances[0].mode == "playback"


def test_streams_report_kind_and_hls(monkeypatch):
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", sim="123", ch=4)
    sess.close()
    out = jt1078.streams()
    assert out and out[0]["kind"] == "live"
    assert out[0]["hls"] == "/hls/jt_123_4.m3u8"


def test_cleanup_hls_dir_removes_stale_and_empty(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setattr(jt1078.config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(jt1078.config, "HLS_MAX_AGE_S", 10)
    old = tmp_path / "a.ts"
    old.write_bytes(b"x" * 5)
    empty = tmp_path / "b.ts"
    empty.write_bytes(b"")
    fresh = tmp_path / "c.ts"
    fresh.write_bytes(b"y" * 5)
    past = time.time() - 100
    os.utime(old, (past, past))
    removed = jt1078.cleanup_hls_dir()
    assert removed == 2
    assert fresh.exists() and not old.exists() and not empty.exists()


def test_reap_idle_muxers_closes_idle(monkeypatch):
    """Uzoq vaqt data kelmasa muxer yopiladi (ffmpeg leak'ining oldini oladi)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A")
    sess.close()
    m = jt1078._MUXERS["123_1"]
    m.last_active = 1000.0

    # hali idle emas
    assert jt1078._reap_idle_muxers(now=1000.0 + 10) == 0
    assert "123_1" in jt1078._MUXERS
    # idle timeout o'tdi -> yopildi
    assert jt1078._reap_idle_muxers(now=1000.0 + jt1078.MUXER_IDLE_TIMEOUT + 1) == 1
    assert m.closed
    assert "123_1" not in jt1078._MUXERS
    assert "123_1" not in jt1078._STREAMS


def test_broken_ffmpeg_recreated(monkeypatch):
    """Muxer yopilgach (closed) keyingi kadrda yangi muxer yaratiladi."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A", ts=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"B", ts=2)   # A chiqadi -> muxer #1
    jt1078._MUXERS["123_1"].close()      # ffmpeg o'lganini simulyatsiya
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"C", ts=3)   # B chiqadi -> muxer #2
    assert len(_FakeMuxer.instances) == 2
    assert _FakeMuxer.instances[1].aus == [b"B"]


# --------------------------------------------- oqim diagnostikasi (dashboard)

def test_streams_report_diagnostics(monkeypatch):
    """Har bir oqim uchun kodek nomi, kadr/kalit kadr soni va kalit kadr yoshi."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I1", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P1", ts=2)   # I1 yoziladi
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I2", ts=3)   # P1 yoziladi
    sess.close()                                       # I2 yoziladi
    out = jt1078.streams()
    assert len(out) == 1
    s = out[0]
    assert s["active"] is True
    assert s["codec"] == "h264" and s["codec_name"] == "H.264"
    assert s["frames"] == 3 and s["keyframes"] == 2
    assert s["restarts"] == 0
    assert s["last_error"] is None
    assert s["keyframe_age_s"] is not None and s["keyframe_age_s"] >= 0


def test_hevc_stream_reports_h265_name(monkeypatch):
    """H.265 (PT=99) oqim diagnostikasida "H.265" ko'rinadi."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", pt=jt1078.PT_VIDEO_H265)
    sess.close()
    s = jt1078.streams()[0]
    assert s["codec"] == "hevc" and s["codec_name"] == "H.265"


def test_note_records_last_error_on_active_stream(monkeypatch, tmp_path):
    """Ulanish xatosi oqim diagnostikasiga yoziladi (dashboard'da ko'rinadi)."""
    _reset_real(monkeypatch, tmp_path)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P", ts=2)
    sess.note("bufer to'ldi — paketlar sinxronizatsiyasi buzildi")
    s = jt1078.streams()[0]
    assert s["last_error"].startswith("bufer to'ldi")
    assert s["error_age_s"] is not None


def test_ffmpeg_log_tail_reported(monkeypatch, tmp_path):
    """Oqim diagnostikasi ffmpeg'ning oxirgi MA'NOLI xato qatorini beradi."""
    _reset_real(monkeypatch, tmp_path)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P", ts=2)
    log = jt1078._ffmpeg_log("123_1")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(
        b"[h264 @ 0x1] non monotonically increasing dts to muxer\n"
        b"[hls @ 0x2] Invalid NAL unit size (300 > 4)\n"
        b"[libx264 @ 0x3] using cpu capabilities: MMX2 SSE2Fast\n")
    assert jt1078.streams()[0]["ffmpeg_note"] == \
        "[libx264 @ 0x3] using cpu capabilities: MMX2 SSE2Fast"


def test_recent_streams_keep_last_error(monkeypatch, tmp_path):
    """Yopilgan oqim sababi va oxirgi xatosi bilan `recent_streams()`da qoladi."""
    _reset_real(monkeypatch, tmp_path)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P", ts=2)
    m = jt1078._MUXERS["123_1"]
    m.note("ffmpeg yozish xatosi — qayta ishga tushiriladi")
    m.last_active = 0.0
    assert jt1078._reap_idle_muxers(now=jt1078.MUXER_IDLE_TIMEOUT + 1) == 1
    assert jt1078.streams() == []          # faol oqim yo'q
    rec = jt1078.recent_streams()
    assert len(rec) == 1
    r = rec[0]
    assert r["active"] is False
    assert r["last_error"].startswith("ffmpeg yozish xatosi")
    assert r["close_reason"] == "kamera uzildi"
    assert r["closed_s"] is not None and r["closed_s"] >= 0
    assert r["uptime_s"] is not None and r["uptime_s"] >= 0
    assert r["keyframes"] == 1 and r["frames"] == 1


def test_restart_increments_counter(monkeypatch, tmp_path):
    """Muxer qayta yaratilsa (kodek o'zgardi) restart soni oshadi."""
    _reset_real(monkeypatch, tmp_path)
    jt1078._get_muxer("123", 1, codec="h264")
    assert jt1078.streams()[0]["restarts"] == 0
    jt1078._get_muxer("123", 1, codec="hevc")   # kodek o'zgardi -> restart
    s = jt1078.streams()[0]
    assert s["restarts"] == 1 and s["codec"] == "hevc"
    assert s["uptime_s"] is not None and s["uptime_s"] >= 0
    assert jt1078.recent_streams()[0]["close_reason"] == "kodek o'zgardi"


# ------------------------------------------------ log yorlig'i (IMEI/kanal)

def test_media_logs_include_imei_and_channel(monkeypatch, caplog):
    """Haqiqiy media sessiyasi loglarida IMEI va kanal ko'rinadi.

    Haqiqiy TCP listener + haqiqiy `_handle`/`handle_session` ishlatiladi —
    "qaysi kamera, qaysi kanal?" savoliga log javob berishini tasdiqlash.
    """
    import asyncio
    import logging

    _reset(monkeypatch)
    jt1078._SESSIONS.clear()
    caplog.set_level(logging.INFO, logger=jt1078.log.name)

    async def _run():
        server = await asyncio.start_server(jt1078._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(_pkt(DT_VIDEO_I, b"I" * 20, sim="221080344988", ch=7))
        writer.write(_pkt(DT_VIDEO_I, b"I" * 20, sim="221080344988", ch=2,
                          ts=1700000001))
        await writer.drain()
        await asyncio.sleep(0.3)
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.3)
        server.close()
        await server.wait_closed()

    asyncio.run(_run())
    out = [r.getMessage() for r in caplog.records]
    # "media ulanishi" va "media uzildi" — ikkalasi ham IMEI_kanal bilan
    assert any("media ulanishi" in l and "221080344988_7" in l for l in out)
    assert any("media ulanishi" in l and "221080344988_2" in l for l in out)
    assert any("media uzildi" in l and "221080344988_" in l for l in out)
    # Oqim boshlandi qatorida IMEI, kanal va manba IP
    assert any("oqim boshlandi" in l and "221080344988_7" in l
               and "manba=127.0.0.1" in l for l in out)
    # Ulanish qatori ham manba ip:port bilan
    assert any("yangi ulanish" in l and "127.0.0.1:" in l for l in out)


def test_known_peers_remembers_imei_across_reconnect(monkeypatch):
    """Qayta ulanishda (BOSHQA port) IMEI manba IP bo'yicha eslab qolinadi."""
    _reset(monkeypatch)
    jt1078._PEER_KEYS.clear()
    first = jt1078._Session(("10.2.3.4", 5555))
    _feed(first, DT_VIDEO_I, SUB_ATOMIC, b"I", sim="221080344988", ch=3)
    first.close()

    # Qurilma boshqa portdan qayta ulanadi — IMEI/kanal baribir ko'rinadi
    again = ("10.2.3.4", 6001)
    assert jt1078._known_peers(again) == (("221080344988", 3),)
    assert jt1078._src(again, *jt1078._known_peers(again)) == \
        "10.2.3.4:6001 221080344988_3"


def test_peer_keys_registry_capped(monkeypatch):
    """Manba reyestri chegaralangan (uzoq jarayonda xotira o'smasin)."""
    _reset(monkeypatch)
    jt1078._PEER_KEYS.clear()
    monkeypatch.setattr(jt1078, "_PEER_KEYS_MAX", 3)
    for i in range(5):
        jt1078._remember_peer((f"10.0.0.{i}", 1), (("123", i),))
    assert len(jt1078._PEER_KEYS) == 3
    assert jt1078._known_peers(("10.0.0.4", 9)) == (("123", 4),)
    assert jt1078._known_peers(("10.0.0.0", 9)) == ()


def test_src_label_falls_back_to_peer():
    """IMEI/kanal noma'lum bo'lsa faqat ip:port qoladi."""
    assert jt1078._src(("1.2.3.4", 9)) == "1.2.3.4:9"
    assert jt1078._src(("1.2.3.4", 9), ("123", 2), ("123", 2)) == \
        "1.2.3.4:9 123_2"
    assert jt1078._src(None) == "?"


def test_reconnect_connection_log_shows_imei(monkeypatch, caplog):
    """Qayta ulanishning "yangi ulanish" qatorida ham IMEI/kanal bo'ladi."""
    import asyncio
    import logging

    _reset(monkeypatch)
    jt1078._SESSIONS.clear()
    jt1078._PEER_KEYS.clear()
    caplog.set_level(logging.INFO, logger=jt1078.log.name)

    async def _run():
        server = await asyncio.start_server(jt1078._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        for ch, ts in ((5, 1700000000), (6, 1700000100)):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_pkt(DT_VIDEO_I, b"I" * 20, sim="221080344988",
                              ch=ch, ts=ts))
            await writer.drain()
            await asyncio.sleep(0.3)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)
        server.close()
        await server.wait_closed()

    asyncio.run(_run())
    joins = [r.getMessage() for r in caplog.records if "yangi ulanish" in r.getMessage()]
    assert len(joins) >= 2, joins
    # 1-ulanishda paket hali o'qilmagan — IMEI ob'ektiv noma'lum.
    assert "221080344988" not in joins[0], joins[0]
    # 2-ulanish boshqa portdan, lekin IP bo'yicha IMEI/kanal darhol ko'rinadi.
    assert "221080344988_5" in joins[1], joins[1]
    # "birinchi N bayt" qatorida ham yorliq bor. Yorliq " — birinchi"
    # gacha bo'lgan qism (hex'da IMEI raqamlari bo'lishi mumkin).
    firsts = [r.getMessage().split(" — birinchi")[0]
              for r in caplog.records if "birinchi" in r.getMessage()]
    assert "221080344988" not in firsts[0], firsts[0]
    assert "221080344988_5" in firsts[1], firsts[1]


# ------------------------------------------------- close_stream (thread-safe)

def test_close_stream_without_loop_closes_direct(monkeypatch):
    """_LOOP yo'q bo'lsa close_stream joyida yopadi (fallback)."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "_LOOP", None)
    m = jt1078._get_muxer("123", 1, mode="live", codec="h264")
    assert m is not None and "123_1" in jt1078._MUXERS
    assert jt1078.close_stream("123", 1, "live") is True
    assert "123_1" not in jt1078._MUXERS      # ffmpeg birorta qolmadi
    # yopiq/yo'q oqimni qayta yopishga urinish — False.
    assert jt1078.close_stream("123", 1, "live") is False


def test_close_stream_routes_to_media_loop(monkeypatch):
    """_LOOP borida yopish HTTP thread'dan EMAS, media loop'ida bajariladi.

    Bu race tuzatishining kaliti: muxer lifecycle barcha mutatsiyalari bitta
    thread'da serilashadi, shunda bir kanalga ikki ffmpeg tushmaydi."""
    _reset(monkeypatch)
    m = jt1078._get_muxer("123", 1, mode="live", codec="h264")
    assert m is not None

    routed = {}

    class _FakeFuture:
        def result(self, timeout=None):
            return True

    def _fake_rcts(coro, loop):
        routed["coro"] = coro
        routed["loop"] = loop
        coro.close()  # haqiqiy loop'ga tushmadi — koroutinni yopamiz
        return _FakeFuture()

    class _FakeLoop:
        def is_closed(self):
            return False

    sentinel_loop = _FakeLoop()
    monkeypatch.setattr(jt1078, "_LOOP", sentinel_loop)
    monkeypatch.setattr(jt1078.asyncio, "run_coroutine_threadsafe", _fake_rcts)

    assert jt1078.close_stream("123", 1, "live") is True
    assert routed.get("loop") is sentinel_loop
    assert "coro" in routed  # yopish media loop'iga yo'naltirildi


def test_one_muxer_per_channel_even_with_codec_change(monkeypatch):
    """Kodek o'zgarsa eskisi yopilib yenisi ochiladi — bir vaqtda bitta."""
    _reset(monkeypatch)
    m1 = jt1078._get_muxer("123", 1, mode="live", codec="h264")
    assert m1.closed is False
    m2 = jt1078._get_muxer("123", 1, mode="live", codec="hevc")
    assert m1.closed is True          # eskisi yopildi
    assert m2 is not m1               # yangi muxer
    live = [x for x in jt1078._MUXERS.values() if not x.closed]
    assert len(live) == 1
    assert live[0] is m2


# ------------------------------------------------------ to'xtatish suppression

def test_live_stop_suppresses_muxer_respawn(monkeypatch):
    """'To'xtatish' = 0x9102 + muxer yopish. Qurilma buyruqni KECH bajaradi:
    yopilgandan keyin ham media paketlari keladi — shunda ffmpeg qayta
    ochilib karta 'tirilib qolardi'. Endi suppression oynasida ochilmaydi."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "_LOOP", None)
    sess = jt1078._Session()
    # Kadr AGARIDI: birinchi I-kadr bufarda turadi, ikkinchisi kelganda
    # chiqariladi (timestamp o'zgarishi bilan) — shunda muxer ochiladi.
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A", ts=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"B", ts=2)
    assert "123_1" in jt1078._MUXERS          # oqim ochildi
    assert jt1078.close_stream("123", 1, "live") is True
    before = len(_FakeMuxer.instances)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"C", ts=3)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"D", ts=4)
    assert "123_1" not in jt1078._MUXERS      # qayta ochilmadi
    assert len(_FakeMuxer.instances) == before


def test_suppression_expires_after_ttl(monkeypatch):
    """Oyna TTL'ni yengib o'tgach qurilma qayta ulansa chiqish oqimi
    tiklanadi — suppression abadiy bloklamasligi kerak."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "_LOOP", None)
    key = jt1078._muxer_key("123", 1, "live")
    clock = {"t": jt1078._now()}
    monkeypatch.setattr(jt1078, "_now", lambda: clock["t"])
    jt1078._mark_suppressed(key)
    assert jt1078._is_suppressed(key) is True
    assert jt1078._get_muxer("123", 1, "live", codec="h264") is None
    clock["t"] += jt1078.SUPPRESS_TTL_S + 1   # oyna muddati o'tdi
    assert jt1078._is_suppressed(key) is False
    m = jt1078._get_muxer("123", 1, "live", codec="h264")
    assert m is not None


def test_playback_stop_close_suppresses_pb_key(monkeypatch):
    """0x9202 stop → close_stream('playback'): pb kaliti yopiladi va
    suppression qo'yiladi; yangi 0x9201 (set_playback) oynani bekor qiladi."""
    _reset(monkeypatch)
    monkeypatch.setattr(jt1078, "_LOOP", None)
    m = jt1078._get_muxer("123", 4, mode="playback", codec="h264")
    assert m is not None and "pb:123_4" in jt1078._MUXERS
    assert jt1078.close_stream("123", 4, "playback") is True
    assert "pb:123_4" not in jt1078._MUXERS
    assert jt1078._is_suppressed("pb:123_4") is True
    assert jt1078._get_muxer("123", 4, mode="playback", codec="h264") is None
    # Qurilmaga yangi 0x9201 yuborildi — to'siq olib tashlanadi.
    jt1078.set_playback("123", 4)
    assert jt1078._is_suppressed("pb:123_4") is False
    m2 = jt1078._get_muxer("123", 4, mode="playback", codec="h264")
    assert m2 is not None


def test_idle_reaper_reopen_not_suppressed(monkeypatch):
    """Idle reaper bilan yopilgan oqim suppression OLMAYDI: kamera qisqa
    uzilib qayta ulanganda chiqish oqimi o'z-o'zidan tiklanishi shart."""
    _reset(monkeypatch)
    m = jt1078._get_muxer("123", 1, mode="live", codec="h264")
    assert m is not None
    m.last_active = jt1078._now() - jt1078.MUXER_IDLE_TIMEOUT - 1
    assert jt1078._reap_idle_muxers() == 1
    assert jt1078._is_suppressed("123_1") is False
    m2 = jt1078._get_muxer("123", 1, mode="live", codec="h264")
    assert m2 is not None
    assert not any(not x.closed for x in _FakeMuxer.instances[:-1])




# --------------------------------------- jonli oqim qotish nazorati (Setka)

def _live_session(monkeypatch):
    """Kadr qabul qilgan ochiq jonli muxer: I1 emit → muxer, keyin I2 → P1."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I1", ts=1)
    _feed(sess, DT_VIDEO_P, SUB_ATOMIC, b"P1", ts=2)   # I1 yoziladi
    return sess, jt1078._MUXERS["123_1"]


def test_live_stall_targets_skips_without_user_request(monkeypatch):
    """Foydalanuvchi 0x9101 so'ramagan oqim kuzatilmaydi (traffik tejash)."""
    sess, m = _live_session(monkeypatch)
    m.last_frame_at = 1000.0
    now = 1000.0 + jt1078.config.JT1078_LIVE_STALL_S + 1
    assert jt1078._live_stall_targets(now) == []

    jt1078.remember_live_request("123", 1, "10.0.0.5", 6808)
    targets = jt1078._live_stall_targets(now)
    assert len(targets) == 1
    assert targets[0][0] is m
    assert targets[0][1]["host"] == "10.0.0.5"


def test_live_stall_targets_ignore_fresh_frames(monkeypatch):
    """Kadr yaqinda kelgan bo'lsa (qotmagan) qayta so'rov yo'q."""
    sess, m = _live_session(monkeypatch)
    jt1078.remember_live_request("123", 1, "10.0.0.5", 6808)
    now = jt1078._now()
    assert jt1078._live_stall_targets(now) == []


def test_live_stall_check_resends_and_rate_limits(monkeypatch):
    """Qotgan oqimga 0x9101 qayta yuboriladi va ketma-ket takrorlanmaydi."""
    from bm_automation.app.gps import jt808

    sess, m = _live_session(monkeypatch)
    m.last_frame_at = jt1078._now() - (jt1078.config.JT1078_LIVE_STALL_S + 5)
    jt1078.remember_live_request("123", 1, "10.0.0.5", 6808, stream_type=0)
    calls = []

    def fake_send(sim, host, tcp_port, channel=1, stream_type=0):
        calls.append((sim, host, tcp_port, channel, stream_type))
        return True

    monkeypatch.setattr(jt808, "send_live_request", fake_send)
    assert asyncio.run(jt1078._live_stall_check()) == 1
    assert calls == [("123", "10.0.0.5", 6808, 1, 0)]
    assert m.re_requests == 1
    assert jt1078.streams()[0]["re_requests"] == 1
    # Darhol ikkinchi tekshiruv — minimal interval o'tmagan, yuborilmaydi.
    assert asyncio.run(jt1078._live_stall_check()) == 0
    assert len(calls) == 1


def test_live_stall_check_unreachable_device_noted(monkeypatch):
    """0x9101 yetkazilmasa diagnostika qayd etiladi (foydalanuvchi sabab ko'rsin)."""
    from bm_automation.app.gps import jt808

    sess, m = _live_session(monkeypatch)
    m.last_frame_at = jt1078._now() - (jt1078.config.JT1078_LIVE_STALL_S + 5)
    jt1078.remember_live_request("123", 1, "10.0.0.5", 6808)
    monkeypatch.setattr(jt808, "send_live_request",
                        lambda *a, **k: False)
    assert asyncio.run(jt1078._live_stall_check()) == 0
    assert m.re_requests == 1
    assert any("0x9101" in n for n in m.notes)


def test_stalled_flag_and_frame_age(monkeypatch):
    """Kamera jim qolsa `frame_age_s` o'sadi va `stalled` yoqiladi."""
    sess, m = _live_session(monkeypatch)
    s = jt1078.streams()[0]
    assert s["frame_age_s"] is not None
    assert s["stalled"] is False

    old = jt1078._now() - (jt1078.config.JT1078_LIVE_STALL_S + 3)
    m.last_frame_at = old
    jt1078._STREAMS["123_1"]["last_frame_at"] = old
    s = jt1078.streams()[0]
    assert s["stalled"] is True
    assert s["frame_age_s"] > jt1078.config.JT1078_LIVE_STALL_S


def test_forget_live_request(monkeypatch):
    """To'xtatish va idle reap kuzatuvni olib tashlaydi (qayta so'rov yo'q)."""
    _reset(monkeypatch)
    jt1078.remember_live_request("123", 1, "h", 6808)
    jt1078.remember_live_request("123", 2, "h", 6808)
    jt1078.remember_live_request("999", 1, "h", 6808)
    jt1078.forget_live_request("123", 1)
    assert set(jt1078._LIVE_REQ) == {"123_2", "999_1"}
    jt1078.forget_live_request("123")
    assert set(jt1078._LIVE_REQ) == {"999_1"}


def test_live_restart_gives_up_after_max(monkeypatch):
    """Qurilma javob bermasa cheksiz 4G trafik bo'lmasin: urinishlar cheklanadi."""
    from bm_automation.app.gps import jt808

    sess, m = _live_session(monkeypatch)
    m.last_frame_at = jt1078._now() - (jt1078.config.JT1078_LIVE_STALL_S + 5)
    jt1078.remember_live_request("123", 1, "10.0.0.5", 6808)
    monkeypatch.setattr(jt1078.config, "JT1078_LIVE_RESTART_MAX", 2)
    monkeypatch.setattr(jt1078.config, "JT1078_LIVE_RESTART_MIN_S", 0)
    calls = []
    monkeypatch.setattr(jt808, "send_live_request",
                        lambda *a, **k: calls.append(a) or True)
    for _ in range(4):
        asyncio.run(jt1078._live_stall_check())
    assert len(calls) == 2
    assert m.re_requests == 2


def test_frame_arrival_resets_restart_attempts(monkeypatch):
    """Kadr qayta kela boshladi — urinishlar hisoblagichi nolga tushadi."""
    sess, m = _live_session(monkeypatch)
    jt1078.remember_live_request("123", 1, "h", 6808)
    req = jt1078._LIVE_REQ["123_1"]
    req["attempts"] = 3
    req["fails"] = 2
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I2", ts=3)   # P1 yoziladi
    assert req["attempts"] == 0 and req["fails"] == 0


def test_idle_reap_forgets_live_request(monkeypatch):
    """Idle reap kuzatuvni ham tozalaydi (yopilgan oqimga 0x9101 ketmasin)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A")
    sess.close()
    jt1078.remember_live_request("123", 1, "h", 6808)
    jt1078._MUXERS["123_1"].last_active = 1000.0
    assert jt1078._reap_idle_muxers(
        now=1000.0 + jt1078.MUXER_IDLE_TIMEOUT + 1) == 1
    assert "123_1" not in jt1078._LIVE_REQ
