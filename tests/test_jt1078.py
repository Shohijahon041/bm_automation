"""JT1078 depacketize testlari: 30 baytli sarlavha + subpaket yig'ish.

Sarlavha real CMSV6 qurilmasi raw dump'iga mos: flag(4) + vendor/seq(4) +
sim(6) + kanal(1) + data_type(1) + ts(8) + lastI(2) + lastF(2) + bodylen(2).
data_type bayti: bits5-4 = kadr turi, bits1-0 = subpaket belgisi. Muxer
(ffmpeg) monkeypatch qilinadi — faqat freyming va yig'ish mantiq'i tekshiriladi.
"""

from bm_automation.app.gps import jt1078
from bm_automation.app.gps.jt1078 import (DT_AUDIO, DT_TRANSPARENT,
                                          DT_VIDEO_I, DT_VIDEO_P, FLAG,
                                          SUB_ATOMIC, SUB_FIRST, SUB_LAST,
                                          SUB_MIDDLE)


def _pkt(typ, payload, sub=SUB_ATOMIC, sim="000000000123", ch=1,
         ts=1700000000, seq=0):
    """Real qurilma sarlavhasiga mos JT1078 paketini qurish."""
    dtb = ((typ & 0x03) << 4) | (sub & 0x03)
    head = (FLAG + b"\x81\x62" + seq.to_bytes(2, "big")
            + bytes.fromhex(sim) + bytes([ch, dtb])
            + ts.to_bytes(8, "big") + b"\x00\x00" + b"\x00\x00"
            + len(payload).to_bytes(2, "big"))
    return head + payload


class _FakeMuxer:
    instances = []

    def __init__(self, sim, channel, mode="live"):
        self.sim = sim
        self.channel = channel
        self.mode = mode
        self.key = f"{sim}_{channel}" if mode == "live" else f"pb:{sim}_{channel}"
        self.prefix = "jt_" if mode == "live" else "jtpb_"
        self.bytes = 0
        self.since = 0.0
        self.last_active = 0.0
        self.closed = False
        self.aus = []
        _FakeMuxer.instances.append(self)

    def write(self, au):
        self.bytes += len(au)
        self.aus.append(au)

    def close(self):
        self.closed = True
        jt1078._MUXERS.pop(self.key, None)
        jt1078._STREAMS.pop(self.key, None)


def _reset(monkeypatch):
    """Monkeypatch muxer + toza registry (testlar orasi izolyatsiya)."""
    monkeypatch.setattr(jt1078, "_Muxer", _FakeMuxer)
    _FakeMuxer.instances.clear()
    jt1078._MUXERS.clear()
    jt1078._STREAMS.clear()
    jt1078.clear_playback()


def _feed(sess, typ, sub, payload, sim="123", ch=1):
    sess.on_packet({"sim": sim, "channel": ch, "data_type": typ,
                    "subpacket": sub, "ts": 0, "payload": payload})


# ---------------------------------------------------------------- parse

def test_parse_video_packet():
    raw = _pkt(DT_VIDEO_I, b"PAYLOAD", sub=SUB_FIRST, ch=3)
    p, consumed = jt1078.parse_packet(raw)
    assert consumed == len(raw)
    assert p["sim"] == "123"
    assert p["channel"] == 3
    assert p["data_type"] == DT_VIDEO_I
    assert p["subpacket"] == SUB_FIRST
    assert p["payload"] == b"PAYLOAD"
    assert p["ts"] == 1700000000


def test_parse_transparent_type():
    """0x30 = bits5-4=3 (shaffof), bits1-0=0 (atomic)."""
    dtb = (DT_TRANSPARENT << 4) | SUB_ATOMIC
    assert dtb == 0x30
    raw = _pkt(DT_TRANSPARENT, b"XXXX", sub=SUB_ATOMIC)
    p, consumed = jt1078.parse_packet(raw)
    assert p["data_type"] == DT_TRANSPARENT
    assert p["payload"] == b"XXXX"


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
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_AUDIO, SUB_ATOMIC, b"audio")
    _feed(sess, DT_TRANSPARENT, SUB_ATOMIC, b"priv")
    _feed(sess, DT_TRANSPARENT, SUB_FIRST, b"priv2")
    sess.close()
    assert _FakeMuxer.instances == []


def test_session_separates_channels(monkeypatch):
    _reset(monkeypatch)
    sess = jt1078._Session()

    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"1a", ch=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"2a", ch=2)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"1b", ch=1)
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"2b", ch=2)
    assert sorted(m.key for m in _FakeMuxer.instances) == ["123_1", "123_2"]
    by = {m.key: m.aus for m in _FakeMuxer.instances}
    assert by["123_1"] == [b"1a", b"1b"]
    assert by["123_2"] == [b"2a", b"2b"]


# ---------------------------------------------------------------- persistence

def test_muxer_persists_across_reconnects(monkeypatch):
    """Kamera uzilib qayta ulansa — o'sha muxer davom etadi (yangi emas).

    Bu HLS playlist'ini har bir uzilishda ENDLIST bo'lib qolishining oldini
    oladi: jonli ko'rinish qayta ulanishlar orasida ham davom etadi.
    """
    _reset(monkeypatch)

    s1 = jt1078._Session()
    _feed(s1, DT_VIDEO_I, SUB_ATOMIC, b"A")
    s1.close()                       # kamera uzildi — muxer yopilmaydi
    assert len(_FakeMuxer.instances) == 1
    assert not _FakeMuxer.instances[0].closed
    assert "123_1" in jt1078._MUXERS

    s2 = jt1078._Session()           # qayta ulanish
    _feed(s2, DT_VIDEO_I, SUB_ATOMIC, b"B")   # yangi ulanishda I-kadr kutamiz
    _feed(s2, DT_VIDEO_P, SUB_ATOMIC, b"C")
    s2.close()
    assert len(_FakeMuxer.instances) == 1     # YANGI muxer yaratilmadi
    assert _FakeMuxer.instances[0].aus == [b"A", b"B", b"C"]


def test_reconnect_still_waits_for_keyframe(monkeypatch):
    """Qayta ulanishda eski P-kadr yozilmaydi — I-kadr kutiladi."""
    _reset(monkeypatch)
    s1 = jt1078._Session()
    _feed(s1, DT_VIDEO_I, SUB_ATOMIC, b"A")
    s1.close()
    s2 = jt1078._Session()
    _feed(s2, DT_VIDEO_P, SUB_ATOMIC, b"orphan")   # prime yo'q -> tashlanadi
    assert _FakeMuxer.instances[0].aus == [b"A"]
    _feed(s2, DT_VIDEO_I, SUB_ATOMIC, b"I2")
    assert _FakeMuxer.instances[0].aus == [b"A", b"I2"]


def test_playback_mode_routes_to_separate_muxer(monkeypatch):
    """Yozuv (playback) so'ralganda oqim alohida muxerga yo'naltiriladi."""
    _reset(monkeypatch)
    jt1078.set_playback("123", 1, seconds=60)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")
    assert [m.key for m in _FakeMuxer.instances] == ["pb:123_1"]
    assert _FakeMuxer.instances[0].mode == "playback"


def test_playback_expiry_returns_to_live(monkeypatch):
    """Playback muddati o'tsa oqim yana jonli deb hisoblanadi."""
    _reset(monkeypatch)
    jt1078.set_playback("123", 1, seconds=60)
    jt1078._PLAYBACK["123_1"] = 0  # muddat o'tib ketdi
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I")
    assert [m.key for m in _FakeMuxer.instances] == ["123_1"]


def test_streams_report_kind_and_hls(monkeypatch):
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"I", sim="123", ch=4)
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
    """Uzoq vaqt data kelmasa muxer yopiladi (ffmpeg leak'ining oldini oladi)."""
    _reset(monkeypatch)
    sess = jt1078._Session()
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A")
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
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"A")
    jt1078._MUXERS["123_1"].close()      # ffmpeg o'lganini simulyatsiya
    _feed(sess, DT_VIDEO_I, SUB_ATOMIC, b"B")
    assert len(_FakeMuxer.instances) == 2
    assert _FakeMuxer.instances[1].aus == [b"B"]

