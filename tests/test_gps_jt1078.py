"""JT1078 muxer yozuvchi thread testlari.

Asosiy talab: `_Muxer.write()` ffmpeg ulgurmasa ham EVENT LOOP'ni
BLOKLAMASLIGI kerak (ilgari bloklovchi stdin.write butun serverni muzlatib,
qurilma soketi o'qilmay qolardi). Ushbu testlar buni sun'iy "sekin" ffmpeg
bilan tekshiradi.
"""

from __future__ import annotations

import threading
import time

import pytest

from bm_automation.app.gps import config, jt1078


class _FakeStdin:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.data = bytearray()
        self._lock = threading.Lock()

    def write(self, b: bytes) -> None:
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.data.extend(b)

    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self, stdin: _FakeStdin):
        self.stdin = stdin
        self.returncode = None

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.returncode = -9


@pytest.fixture
def muxer_env(tmp_path, monkeypatch):
    """HLS_DIR/ffmpeg'ni izolyatsiya qiladi va sun'iy Popen beradi."""
    monkeypatch.setattr(config, "HLS_DIR", str(tmp_path / "hls"))
    monkeypatch.setattr(config, "JT1078_RAW_DUMP", False)
    monkeypatch.setattr(jt1078, "ffmpeg_available", lambda: True)
    state = {"delay": 0.0, "stdin": None, "cmd": None, "popens": 0}

    def fake_popen(cmd, **kw):
        stdin = _FakeStdin(delay=state["delay"])
        state["stdin"] = stdin
        state["cmd"] = cmd
        state["popens"] += 1
        return _FakeProc(stdin)

    monkeypatch.setattr(jt1078.subprocess, "Popen", fake_popen)
    for table in (jt1078._MUXERS, jt1078._STREAMS, jt1078._DIAG,
                  jt1078._MUXER_SRC):
        table.clear()
    yield state
    for m in list(jt1078._MUXERS.values()):
        try:
            m.close()
        except Exception:  # noqa: BLE001
            pass
    for table in (jt1078._MUXERS, jt1078._STREAMS, jt1078._DIAG,
                  jt1078._MUXER_SRC):
        table.clear()


def test_write_does_not_block_and_drops_when_full(muxer_env, monkeypatch):
    """ffmpeg sekin bo'lsa write() darhol qaytadi va ortiqcha kadr tashlanadi."""
    monkeypatch.setattr(config, "JT1078_QUEUE_MAX", 2)
    muxer_env["delay"] = 0.3  # ffmpeg har kadrni 0.3s "yozadi"
    m = jt1078._Muxer("1234", 1, codec="h264")
    try:
        assert m._in_q is not None
        t0 = time.monotonic()
        for _ in range(20):
            m.write(b"x" * 1000)
        dt = time.monotonic() - t0
        assert dt < 0.25, f"write() bloklandi: {dt:.3f}s"
        assert m.bytes == 20 * 1000
        assert m.dropped > 0
    finally:
        m.close()


def test_write_reaches_ffmpeg_when_fast(muxer_env, monkeypatch):
    """ffmpeg ulgurganda kadrlar navbatdan o'tib ffmpeg'ga yetib boradi."""
    monkeypatch.setattr(config, "JT1078_QUEUE_MAX", 64)
    muxer_env["delay"] = 0.0
    m = jt1078._Muxer("1234", 1, codec="h264")
    try:
        for _ in range(10):
            m.write(b"y" * 1000)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(muxer_env["stdin"].data) >= 10000:
                break
            time.sleep(0.01)
        assert m.dropped == 0
        assert bytes(muxer_env["stdin"].data) == b"y" * 10000
    finally:
        m.close()


def test_get_muxer_recreates_after_close(muxer_env, monkeypatch):
    """Muxer yopilgan (ffmpeg xatosi) bo'lsa, _get_muxer yangisini ochadi."""
    monkeypatch.setattr(config, "JT1078_QUEUE_MAX", 4)
    m1 = jt1078._get_muxer("999", 2, codec="h264")
    m1.closed = True  # ffmpeg xatosi holatini taqlid qilamiz
    m2 = jt1078._get_muxer("999", 2, codec="h264")
    assert m2 is not m1
    assert m1.closed is True
    assert m2.restarts == 1
    m2.close()
    assert jt1078._MUXERS.get(m2.key) is None


def test_playback_primes_and_uses_estimated_fps(muxer_env, monkeypatch):
    """Playback'da ffmpeg darhol emas — ts'dan fps baholanib ishga tushadi.

    Qurilma yozuvni real vaqtdan tez yuborsa ham, kirish fps ts oraliqidan
    o'lchanadi (bu yerda 25 fps) — video tez aylanmaydi."""
    monkeypatch.setattr(config, "JT1078_PLAYBACK_PRIME_MS", 1000)
    monkeypatch.setattr(config, "JT1078_PLAYBACK_PRIME_MAX_AU", 10_000)
    monkeypatch.setattr(config, "JT1078_FPS_IN_PB", 13)
    m = jt1078._Muxer("777", 1, mode="playback", codec="h264")
    try:
        assert m._proc is None          # hali ishga tushmagan
        assert muxer_env["popens"] == 0
        # 26 kadr, 40ms oraliq -> 1000ms span -> 25 fps
        for i in range(26):
            m.write(b"z" * 100, ts=1000 + i * 40)
        assert muxer_env["popens"] == 1
        cmd = muxer_env["cmd"]
        idx = cmd.index("-framerate")
        assert cmd[idx + 1] == "25"
        assert m.bytes == 26 * 100
    finally:
        m.close()
