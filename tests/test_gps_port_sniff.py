"""Portlar aro protokol aniqlash: bitta port maydoni bilan ham ishlaydi.

O'rnatishda ko'p qurilmalarda FAQAT "server port" maydoni bo'ladi va u ba'zan
media port (6107) qilib qo'yiladi. Bunda JT808 signaling paketlari media
listener'ga kelib jimgina tashlanardi (qurilma dashboardda "oflayn" ko'rinardi).
Shu sababli ikkala listener ham birinchi paketga qarab to'g'ri protokolga
o'tkazadi. Testlar haqiqiy TCP listener + haqiqiy asyncio socket bilan ishlaydi.
"""

import asyncio
import logging

from bm_automation.app.gps import jt1078, jt808

PHONE = "221080344988"
SIM_HEX = "000000000123"


class _FakeMuxer:
    """ffmpeg ishga tushmasligi uchun (faqat freyming tekshiriladi)."""

    instances = []

    def __init__(self, sim, channel, mode="live", codec="h264"):
        self.sim = sim
        self.channel = channel
        self.mode = mode
        self.key = jt1078._muxer_key(sim, channel, mode)
        self.prefix = "jt_" if mode == "live" else "jtpb_"
        self.bytes = 0
        self.closed = False
        _FakeMuxer.instances.append(self)

    def write(self, au):
        self.bytes += len(au)

    def note(self, msg):
        pass

    def close(self):
        self.closed = True
        jt1078._MUXERS.pop(self.key, None)
        jt1078._STREAMS.pop(self.key, None)


def _serve(handler, payload, wait_for=None):
    """`handler` bilan vaqtincha TCP server ochib, `payload` yuboradi.

    `wait_for(data)` — kutish sharti: bajarilmaguncha (yoki 3 sekund) javob
    o'qiladi. Shartsiz holatda faqat 0.5 sekund kutib ulanish yopiladi.
    """
    async def _run():
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(payload)
        await writer.drain()
        data = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (3.0 if wait_for else 0.5)
        while loop.time() < deadline:
            if wait_for is not None and wait_for(data):
                break
            try:
                chunk = await asyncio.wait_for(reader.read(4096), 0.2)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            data += chunk
        await asyncio.sleep(0.2)
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()
        return data
    return asyncio.run(_run())


def _register_body(version=1):
    """0x0100 ro'yxat tanasi (2019: 11/30/30, 2013: 5/20/7)."""
    mlen, modlen, idlen = (11, 30, 30) if version else (5, 20, 7)
    return (b"\x00\x00\x00\x00"
            + b"BUFFCORP".ljust(mlen, b" ")
            + b"MODEL-M1".ljust(modlen, b"\x00")
            + PHONE.encode().ljust(idlen, b"\x00")
            + bytes([1]) + b"01A123BC")


def _media_packet(payload=b"\x00\x00\x00\x01\x65AU", pt=98, ch=1):
    """JT1078 media paketi (I-kadr, bo'linmagan)."""
    dtb = (jt1078.DT_VIDEO_I << 4) | jt1078.SUB_ATOMIC
    head = (jt1078.FLAG + bytes([0x81, 0x80 | (pt & 0x7F)])
            + (0).to_bytes(2, "big") + bytes.fromhex(SIM_HEX) + bytes([ch, dtb])
            + (1700000000).to_bytes(8, "big") + b"\x00\x00" + b"\x00\x00")
    return head + len(payload).to_bytes(2, "big") + payload


def _isolate(monkeypatch):
    monkeypatch.setattr(jt808.storage, "device_known", lambda imei: False)
    monkeypatch.setattr(jt808.storage, "register_device",
                        lambda imei, kind="teltonika": None)
    monkeypatch.setattr(jt808.config, "GPS_AUTO_REGISTER", True)
    monkeypatch.setattr(jt1078, "_Muxer", _FakeMuxer)
    _FakeMuxer.instances.clear()
    jt1078._MUXERS.clear()
    jt1078._STREAMS.clear()
    jt1078._STATS.update({"packets": 0, "access_units": 0})


def test_jt808_register_on_media_port(monkeypatch):
    """6107'ga (media portga) kelgan JT808 2019 ro'yxati qabul qilinadi."""
    _isolate(monkeypatch)
    jt808._DEVINFO.pop(PHONE, None)
    jt808._CONNECTED.clear()
    frame = jt808.build_frame(jt808.MSG_TERM_REGISTER, PHONE, 3,
                              _register_body(), version=1)
    data = _serve(jt1078._handle, frame,
                  wait_for=lambda d: bool(jt808.extract_frames(d)[0]))

    frames, _ = jt808.extract_frames(data)
    assert frames, "JT808 javobi qaytmadi — paket media deb tashlandi"
    m = jt808.parse_frame(frames[-1])
    assert m["msg_id"] == jt808.MSG_PLAT_REGISTER_RESP
    assert m["phone"] == PHONE
    assert m["version"] == 1
    # Ro'yxat ma'lumotlari keshga tushdi (qurilma ro'yxatdan o'tdi).
    assert jt808.device_info(PHONE).get("model") == "MODEL-M1"
    # Media sifatida qaralmadi.
    assert jt1078._STATS["packets"] == 0
    assert _FakeMuxer.instances == []


def test_jt808_media_logs_show_imei_on_reconnect(monkeypatch, caplog):
    """Haqiqiy TCP: qayta ulanishda (boshqa port) IMEI manba IP bo'yicha ko'rinadi.

    Qurilma qisqa-qisqa qayta ulanadi va birinchi qatorlarida IMEI yo'q —
    "qaysi kamera?" savoliga log javob berishi kerak.
    """
    _isolate(monkeypatch)
    jt808._CONNECTED.clear()
    jt808._PEER_IMEI.clear()
    caplog.set_level(logging.INFO, logger=jt808.log.name)
    frame = jt808.build_frame(jt808.MSG_TERM_REGISTER, PHONE, 3,
                              _register_body(), version=1)

    async def _run():
        server = await asyncio.start_server(jt808._handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(frame)
            await writer.drain()
            try:
                await asyncio.wait_for(reader.read(4096), 1.0)
            except asyncio.TimeoutError:
                pass
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.1)
        server.close()
        await server.wait_closed()

    asyncio.run(_run())
    out = [r.getMessage() for r in caplog.records]
    joins = [l for l in out if "yangi ulanish" in l]
    assert len(joins) >= 2, joins
    # 2-ulanish qatorida ro'yxatdan o'tishdan OLDIN ham IMEI ko'rinadi
    assert f"IMEI={PHONE}" in joins[1], joins[1]
    assert any("qurilma uzildi" in l and f"IMEI={PHONE}" in l for l in out)


def test_jt1078_media_on_jt808_port(monkeypatch):
    """6808'ga (signaling portga) kelgan media paketi media listener'da ishlanadi."""
    _isolate(monkeypatch)
    jt808._CONNECTED.clear()
    before_err = jt808._STATS["errors"]
    data = _serve(jt808._handle, _media_packet(),
                  wait_for=lambda d: jt1078._STATS["packets"] >= 1)

    assert data == b"", "JT808 javobi yuborilmadi — paket signaling deb qaraldi"
    assert jt1078._STATS["packets"] == 1
    assert jt808._STATS["errors"] == before_err
    assert _FakeMuxer.instances, "media sessiyasi ishga tushmadi"
    assert _FakeMuxer.instances[0].sim == SIM_HEX
    assert _FakeMuxer.instances[0].channel == 1
