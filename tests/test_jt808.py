"""JT808 (2013/2019) yadro testlari: framing + signaling dispatch.

Real qurilmasiz, in-process: build_frame/parse_frame aylanma tekshiruvi va
_dispatch orqali register/heartbeat/location xabarlariga platforma javoblari.
storage/config monkeypatch qilinadi (SQLite backend GPS DDL'ni bilmaydi).
"""

import struct

import pytest

from bm_automation.app.gps import jt808


# ---------------------------------------------------------------- framing

def test_escape_unescape_roundtrip():
    raw = bytes([0x7E, 0x7D, 0x01, 0x02, 0x7E])
    assert jt808.unescape(jt808.escape(raw)) == raw


def test_escape_no_delimiter_inside():
    esc = jt808.escape(bytes([0x7E, 0x7D]))
    assert 0x7E not in esc  # faqat chegarada bo'ladi
    assert esc == bytes([0x7D, 0x02, 0x7D, 0x01])


def test_checksum_xor():
    assert jt808.checksum(b"\x01\x02\x03") == 0x01 ^ 0x02 ^ 0x03


def test_bcd_roundtrip():
    assert jt808.bcd_decode(jt808.bcd_encode("123456789012", 6)) == "123456789012"
    # yetakchi nollar tushadi
    assert jt808.bcd_decode(jt808.bcd_encode("42", 6)) == "42"


def test_build_parse_frame_2013():
    body = b"\xaa\xbb\xcc"
    frame = jt808.build_frame(0x0200, "123456", 7, body, version=0)
    assert frame[0] == 0x7E and frame[-1] == 0x7E
    m = jt808.parse_frame(frame)
    assert m["msg_id"] == 0x0200
    assert m["seq"] == 7
    assert m["phone"] == "123456"
    assert m["body"] == body
    assert m["version"] == 0


def test_build_parse_frame_2019():
    body = b"\x01\x02"
    frame = jt808.build_frame(0x0102, "13800138000", 9, body, version=1)
    m = jt808.parse_frame(frame)
    assert m["version"] == 1
    assert m["phone"] == "13800138000"
    assert m["seq"] == 9
    assert m["body"] == body


def test_parse_frame_bad_checksum():
    frame = bytearray(jt808.build_frame(0x0002, "123456", 1, b"", 0))
    frame[-2] ^= 0xFF  # checksum buzildi (escape ichida emas)
    with pytest.raises(jt808.Jt808Error):
        jt808.parse_frame(bytes(frame))


def test_extract_frames_multiple():
    f1 = jt808.build_frame(0x0002, "1", 1, b"", 0)
    f2 = jt808.build_frame(0x0200, "1", 2, b"\x00" * 28, 0)
    frames, rest = jt808.extract_frames(f1 + f2 + b"\x7e\x01")
    assert len(frames) == 2
    assert rest == b"\x7e\x01"  # tugallanmagan frame qoldi


# ---------------------------------------------------------------- location

def _loc_body(lat=41.30, lng=69.24, speed=55, status=1):
    return (struct.pack(">II", 0, status)
            + struct.pack(">II", int(lat * 1e6), int(lng * 1e6))
            + struct.pack(">HHH", 120, speed, 90)
            + bytes.fromhex("261010120000"))


def test_parse_location():
    loc = jt808.parse_location(_loc_body())
    assert abs(loc["lat"] - 41.30) < 1e-5
    assert abs(loc["lng"] - 69.24) < 1e-5
    assert loc["speed"] == 5.5       # 1/10 km/h
    assert loc["ignition"] is True   # status bit0


def test_parse_location_short():
    with pytest.raises(jt808.Jt808Error):
        jt808.parse_location(b"\x00" * 10)


# ---------------------------------------------------------------- dispatch

class _FakeWriter:
    def __init__(self):
        self.buf = bytearray()
        self.closed = False

    def write(self, data):
        self.buf += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def get_extra_info(self, key):
        return ("127.0.0.1", 40000)


def _sent_msg(writer):
    frames, _ = jt808.extract_frames(bytes(writer.buf))
    assert frames, "javob yuborilmadi"
    return jt808.parse_frame(frames[-1])


@pytest.fixture
def gps_storage(monkeypatch):
    calls = {"upsert": [], "register": []}
    monkeypatch.setattr(jt808.storage, "device_known", lambda imei: False)
    monkeypatch.setattr(jt808.storage, "register_device",
                        lambda imei: calls["register"].append(imei))
    monkeypatch.setattr(jt808.storage, "upsert_device_position",
                        lambda *a, **k: calls["upsert"].append(a))
    monkeypatch.setattr(jt808.config, "GPS_AUTO_REGISTER", True)
    return calls


def _dispatch(phone, msg_id, body, version=0, seq=1):
    import asyncio
    frame = jt808.build_frame(msg_id, phone, seq, body, version)
    m = jt808.parse_frame(frame)
    w = _FakeWriter()
    asyncio.run(jt808._dispatch(w, m))
    return w


# ---------------------------------------------------------------- resources

def _resource_body(items=None):
    if items is None:
        items = [(1, "261011100000", "261011130000", 12345)]
    body = struct.pack(">HI", 5, len(items))
    for ch, start, end, size in items:
        body += (bytes([ch]) + bytes.fromhex(start) + bytes.fromhex(end)
                 + struct.pack(">Q", 0) + bytes([0, 0, 0])
                 + struct.pack(">I", size))
    return body


def test_iso_bcd6_roundtrip():
    raw = jt808.iso_to_bcd6("2026-10-11T10:00:00")
    assert raw == bytes.fromhex("261011100000")
    assert jt808.bcd6_to_iso(raw) == "2026-10-11T10:00:00"


def test_iso_to_bcd6_invalid_uses_now():
    raw = jt808.iso_to_bcd6("buzilgan")
    assert len(raw) == 6


def test_parse_resource_list():
    items = jt808.parse_resource_list(_resource_body())
    assert len(items) == 1
    assert items[0]["channel"] == 1
    assert items[0]["start"] == "2026-10-11T10:00:00"
    assert items[0]["end"] == "2026-10-11T13:00:00"
    assert items[0]["size"] == 12345


def test_parse_resource_list_record_is_28_bytes():
    """0x1205 resurs elementi 28 bayt: alarm 64-bit (8), hajm 32-bit (4)."""
    body = _resource_body()
    assert len(body) == 6 + 28
    items = jt808.parse_resource_list(body)
    assert len(items) == 1


def test_parse_resource_list_64bit_alarm():
    """Alarm 64-bit bo'lishi kerak (4-bayt format eski xato edi)."""
    alarm = 0xABCDEF0123456789
    body = (struct.pack(">HI", 5, 1)
            + bytes([2]) + bytes.fromhex("261011100000")
            + bytes.fromhex("261011130000")
            + struct.pack(">Q", alarm) + bytes([1, 2, 1])
            + struct.pack(">I", 999))
    items = jt808.parse_resource_list(body)
    assert len(items) == 1
    assert items[0]["channel"] == 2
    assert items[0]["alarm"] == alarm
    assert items[0]["resource_type"] == 1
    assert items[0]["stream_type"] == 2
    assert items[0]["memory_type"] == 1
    assert items[0]["size"] == 999


def test_request_av_resource_list_body_24_bytes(monkeypatch):
    """0x9205 tanasi: kanal(1)+start(6)+end(6)+alarm(8)+3*1 = 24 bayt."""
    import asyncio

    captured = {}

    async def _fake_send(phone, msg_id, body):
        captured["phone"] = phone
        captured["msg_id"] = msg_id
        captured["body"] = body
        return True

    monkeypatch.setattr(jt808, "_send", _fake_send)
    start = bytes.fromhex("261011100000")
    end = bytes.fromhex("261011130000")
    ok = asyncio.run(jt808.request_av_resource_list("221080344988", 3, start, end))
    assert ok
    body = captured["body"]
    assert captured["msg_id"] == 0x9205
    assert len(body) == 24
    assert body[0] == 3
    assert body[1:7] == start
    assert body[7:13] == end
    assert body[13:21] == b"\x00" * 8     # 64-bit alarm
    assert body[21:24] == bytes([0, 0, 0])


def test_parse_resource_list_short():
    with pytest.raises(jt808.Jt808Error):
        jt808.parse_resource_list(b"\x00\x01")


def test_dispatch_resource_list(gps_storage):
    jt808.clear_resources("123456")
    w = _dispatch("123456", 0x1205, _resource_body())
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8001
    r = jt808.resources("123456")
    assert len(r["items"]) == 1
    jt808.clear_resources("123456")


def test_dispatch_resource_list_merges_packets(gps_storage):
    """Qurilma ro'yxatni bir nechta 0x1205 paketda yuborsa — merge bo'ladi."""
    jt808.clear_resources("123456")
    _dispatch("123456", 0x1205, _resource_body([
        (1, "261011100000", "261011130000", 111)]))
    _dispatch("123456", 0x1205, _resource_body([
        (1, "261011130000", "261011140000", 222)]))
    _dispatch("123456", 0x1205, _resource_body([
        (1, "261011130000", "261011140000", 222)]))  # dublikat
    items = jt808.resources("123456")["items"]
    assert len(items) == 2
    assert items[0]["start"] == "2026-10-11T10:00:00"
    assert items[1]["start"] == "2026-10-11T13:00:00"
    jt808.clear_resources("123456")


def test_clear_resources():
    jt808._RESOURCES["123456"] = {"at": 1, "items": [{"x": 1}]}
    jt808.clear_resources("123456")
    assert jt808.resources("123456") == {"at": 0, "items": []}


def test_dispatch_register(gps_storage):
    w = _dispatch("123456", 0x0100, b"\x00" * 20)
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8100
    assert "123456" in gps_storage["register"]


def test_dispatch_heartbeat(gps_storage):
    w = _dispatch("123456", 0x0002, b"")
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8001
    assert resp["body"][4] == 0  # result = success (seq2+resp_id2+result1)


def test_dispatch_location_stores(gps_storage):
    w = _dispatch("123456", 0x0200, _loc_body(lat=41.31, lng=69.25))
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8001
    assert len(gps_storage["upsert"]) == 1
    args = gps_storage["upsert"][0]
    assert args[0] == "123456"
    assert abs(args[2] - 41.31) < 1e-5   # lat
    assert abs(args[3] - 69.25) < 1e-5   # lng


# ------------------------------------------------- qurilma imkoniyatlari

def test_dispatch_terminal_attrs(gps_storage):
    jt808._EXTRA.clear()
    w = _dispatch("123456", 0x0107, b"\x01\x02\x03\x04")
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8001
    extras = jt808.av_info("123456")
    assert extras["attrs_hex"] == "01020304"
    assert extras["attrs_hex_len"] == 4


def test_dispatch_av_attrs(gps_storage):
    jt808._EXTRA.clear()
    w = _dispatch("123456", 0x1003, b"\x0a\x0b")
    resp = _sent_msg(w)
    assert resp["msg_id"] == 0x8001
    assert jt808.av_info("123456")["av_hex"] == "0a0b"


def _query_frame(monkeypatch, coro, phone="123456"):
    import asyncio
    w = _FakeWriter()
    monkeypatch.setattr(jt808, "_writer_for", lambda p: w)
    monkeypatch.setattr(jt808, "_ver", lambda p: 0)
    assert asyncio.run(coro(phone)) is True
    frames, _ = jt808.extract_frames(bytes(w.buf))
    return jt808.parse_frame(frames[-1])


def test_query_params_frame(monkeypatch):
    m = _query_frame(monkeypatch, jt808.query_params)
    assert m["msg_id"] == 0x8104


def test_query_attrs_frame(monkeypatch):
    m = _query_frame(monkeypatch, jt808.query_attrs)
    assert m["msg_id"] == 0x8107


def test_query_av_attrs_frame(monkeypatch):
    m = _query_frame(monkeypatch, jt808.query_av_attrs)
    assert m["msg_id"] == 0x9003


def test_query_specific_frame(monkeypatch):
    m = _query_frame(monkeypatch, lambda p: jt808.query_specific(p, [0x01, 0x02]))
    assert m["msg_id"] == 0x8106
    assert m["body"][0] == 2                       # param soni
    assert m["body"][1:5] == struct.pack(">I", 1)
    assert m["body"][5:9] == struct.pack(">I", 2)


def test_raw_log_records_out(monkeypatch):
    jt808._RAW.clear()
    _query_frame(monkeypatch, jt808.query_attrs)
    entries = jt808.raw_log(limit=10, phone="123456")
    assert any(e["dir"] == "out" and e["msg_id"] == 0x8107 for e in entries)
