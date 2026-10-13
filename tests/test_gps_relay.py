"""RTSP -> HLS relay: on-demand (viewer-gated) + global limit testlari."""

import time

from bm_automation.app.gps import config, relay


class _Proc:
    def __init__(self):
        self.pid = 1
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False


def _reset():
    relay._procs.clear()
    relay._last_seen.clear()
    relay._starts.clear()
    relay._cam_provider = None


def _fake_start(started):
    def _start(cam):
        started.append(cam["id"])
        relay._procs[cam["id"]] = {"proc": _Proc(), "cam": cam}
    return _start


def test_note_viewer_and_active(monkeypatch):
    _reset()
    monkeypatch.setattr(config, "HLS_RELAY_IDLE_S", 90)
    assert relay.viewer_active(5) is False
    relay.note_viewer(5)
    assert relay.viewer_active(5) is True


def test_ensure_respects_global_limit(monkeypatch):
    _reset()
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(config, "HLS_RELAY_ENABLED", True)
    monkeypatch.setattr(config, "HLS_RELAY_MAX", 1)
    started = []
    monkeypatch.setattr(relay, "_start", _fake_start(started))
    cam1 = {"id": 1, "url": "rtsp://a", "source_type": "rtsp", "enabled": 1}
    cam2 = {"id": 2, "url": "rtsp://b", "source_type": "rtsp", "enabled": 1}
    assert relay.ensure_for_camera(cam1) is True
    assert relay.ensure_for_camera(cam2) is False   # limit to'ldi
    assert started == [1]
    assert relay.ensure_for_camera(cam1) is True    # idempotent


def test_sync_only_keeps_viewed(monkeypatch):
    _reset()
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(config, "HLS_RELAY_ENABLED", True)
    monkeypatch.setattr(config, "HLS_RELAY_MAX", 8)
    monkeypatch.setattr(config, "HLS_RELAY_IDLE_S", 90)
    started = []
    stopped = []
    monkeypatch.setattr(relay, "_start", _fake_start(started))
    monkeypatch.setattr(relay, "stop_for_camera",
                        lambda cid: (stopped.append(cid),
                                     relay._procs.pop(cid, None)))
    cams = [
        {"id": 1, "url": "rtsp://a", "source_type": "rtsp", "enabled": 1},
        {"id": 2, "url": "rtsp://b", "source_type": "rtsp", "enabled": 1},
    ]
    # Hech kim ko'rmagan -> barcha relay ishlamaydi (100+ kamera uchun muhim).
    relay.sync(cams)
    assert started == [] and stopped == []
    # 1-kamera ko'rildi -> faqat u ko'tariladi.
    relay.note_viewer(1)
    relay.sync(cams)
    assert started == [1]
    # Viewer eskirsa -> relay to'xtatiladi.
    relay._last_seen[1] = time.time() - 10_000
    relay.sync(cams)
    assert stopped == [1]


def test_sync_ignores_non_rtsp(monkeypatch):
    _reset()
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(config, "HLS_RELAY_ENABLED", True)
    started = []
    monkeypatch.setattr(relay, "_start", _fake_start(started))
    cam = {"id": 1, "url": "http://x.m3u8", "source_type": "hls", "enabled": 1}
    relay.note_viewer(1)
    relay.sync([cam])
    assert started == []


def test_resolve_camera_uses_provider(monkeypatch):
    _reset()
    relay._cam_provider = lambda: [{"id": 9, "url": "x"}]
    assert relay.resolve_camera(9) == {"id": 9, "url": "x"}
    assert relay.resolve_camera(99) is None
    relay._cam_provider = None
    assert relay.resolve_camera(9) is None


# ------------------------------------------- diagnostika (dashboard uchun)

def test_status_reports_error_and_restarts(monkeypatch, tmp_path):
    """Relay holati: ishlayaptimi, qayta ishga tushish soni, oxirgi xato."""
    _reset()
    monkeypatch.setattr(config, "HLS_DIR", str(tmp_path))
    log = relay.relay_log_path(7)
    log.write_bytes(b"[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized\n")
    relay._procs[7] = {"proc": _Proc(), "cam": {"id": 7}, "log": log,
                       "since": time.time() - 30}
    relay._starts[7] = 3
    st = relay.status()["7"]
    assert st["running"] is True and st["exit_code"] is None
    assert st["restarts"] == 2
    assert st["uptime_s"] >= 29
    assert st["error"] == "[rtsp @ 0x1] method DESCRIBE failed: 401 Unauthorized"
    assert st["hls"] == "/hls/cam7.m3u8"


def test_status_reports_dead_relay(monkeypatch, tmp_path):
    """O'lgan ffmpeg chiqish kodi bilan ko'rinadi (jimgina qolmaydi)."""
    _reset()
    monkeypatch.setattr(config, "HLS_DIR", str(tmp_path))
    proc = _Proc()
    proc.wait(timeout=1)
    relay._procs[3] = {"proc": proc, "cam": {"id": 3}, "log": None,
                       "since": time.time()}
    st = relay.status()["3"]
    assert st["running"] is False and st["exit_code"] == 0
    assert st["error"] is None and st["restarts"] == 0


def test_start_writes_ffmpeg_stderr_to_log(monkeypatch, tmp_path):
    """Relay ffmpeg'i stderr'ni logga yozadi (aks holda xato yo'qoladi)."""
    _reset()
    monkeypatch.setattr(config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    captured = {}

    def _popen(cmd, **kw):
        captured.update(kw)
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(relay.subprocess, "Popen", _popen)
    relay._start({"id": 5, "url": "rtsp://cam/1", "name": "Test"})
    log = relay.relay_log_path(5)
    assert log.exists()                       # "wb" bilan yaratilgan
    assert captured["stderr"] is not None
    assert captured["stderr"] is not relay.subprocess.DEVNULL
    assert relay._starts[5] == 1
    st = relay.status()["5"]
    assert st["running"] is True and st["restarts"] == 0


def test_start_restarts_counted(monkeypatch, tmp_path):
    """Relay o'lib qayta ko'tarilsa restart soni oshadi (beqarorlik belgisi)."""
    _reset()
    monkeypatch.setattr(config, "HLS_DIR", str(tmp_path))
    monkeypatch.setattr(relay, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(config, "HLS_RELAY_ENABLED", True)
    monkeypatch.setattr(config, "HLS_RELAY_MAX", 4)
    monkeypatch.setattr(relay.subprocess, "Popen", lambda cmd, **kw: _Proc())
    cam = {"id": 2, "url": "rtsp://cam/2", "source_type": "rtsp", "enabled": 1}
    assert relay.ensure_for_camera(cam) is True
    relay._procs[2]["proc"].wait(timeout=1)     # ffmpeg o'ldi
    assert relay.ensure_for_camera(cam) is True  # qayta ko'tarildi
    assert relay.status()["2"]["restarts"] == 1
