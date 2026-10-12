"""ffmpeg RTSP→HLS relay menejeri.

Brauzer RTSP'ni o'ynamaydi — har bir RTSP kamera uchun ffmpeg relay
jarayoni oqimni HLS segmentlarga aylantiradi:

    rtsp://kamera/...  →  ffmpeg  →  <HLS_DIR>/cam<id>.m3u8

Boshqaruv:
  * ensure_for_camera(cam)  — relay kerak bo'lsa ishga tushiradi
  * stop_for_camera(cam_id) — to'xtatadi (kamera o'chirilganda)
  * sync(cameras)           — ro'yxat bilan rekreasyon (periodik)
  * status()                — monitoring uchun

Fail-safe: ffmpeg yo'q bo'lsa relay o'chirilgan hisoblanadi (GPS_LOST
yo'q, faqat log); jarayon o'lsa keyingi sync'da qayta ko'tariladi.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_procs: dict[int, dict] = {}  # camera_id -> {"proc": Popen, "cam": dict}
# On-demand: kamera HLS'iga oxirgi so'rov vaqti. Viewer yo'q bo'lsa
# HLS_RELAY_IDLE_S sekunddan keyin relay to'xtatiladi (100+ kamera uchun).
_last_seen: dict[int, float] = {}
_cam_provider = None  # cameras_provider (start_monitor o'rnatadi)


def _now() -> float:
    return time.time()


def note_viewer(cam_id: int) -> None:
    """Kamera HLS'i so'ralganda chaqiriladi — relay tirik qoladi (on-demand)."""
    with _lock:
        _last_seen[int(cam_id)] = _now()


def viewer_active(cam_id: int, now: float | None = None) -> bool:
    now = _now() if now is None else now
    with _lock:
        last = _last_seen.get(int(cam_id))
    return bool(last and now - last < config.HLS_RELAY_IDLE_S)


def resolve_camera(cam_id: int) -> dict | None:
    """cameras_provider orqali kamera metama'lumotini topadi (on-demand)."""
    if _cam_provider is None:
        return None
    try:
        for cam in _cam_provider():
            if str(cam.get("id")) == str(cam_id):
                return cam
    except Exception:  # noqa: BLE001
        log.exception("relay: kamera ro'yxatini olish xatosi")
    return None


def _running_count() -> int:
    """Tirik relay'lar soni (lock ushlab turilganda chaqiriladi)."""
    return sum(1 for e in _procs.values() if e["proc"].poll() is None)


def ffmpeg_available() -> bool:
    return bool(config.FFMPEG_BIN) and bool(
        shutil.which(config.FFMPEG_BIN) or Path(config.FFMPEG_BIN).exists()
    )


def hls_path(cam_id: int) -> Path:
    return Path(config.HLS_DIR) / f"cam{cam_id}.m3u8"


def public_url(cam_id: int) -> str:
    """Relay HLS manzili (UI shu URL'ni playerga beradi)."""
    return f"/hls/cam{cam_id}.m3u8"


def _start(cam: dict) -> None:
    cam_id = int(cam["id"])
    out = hls_path(cam_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        config.FFMPEG_BIN,
        "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", str(cam["url"]),
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+independent_segments",
        str(out),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        log.warning("relay: ffmpeg ishga tushmadi (kamera %s): %s", cam_id, exc)
        return
    _procs[cam_id] = {"proc": proc, "cam": cam}
    log.info("relay: kamera %s (%s) → %s", cam_id, cam.get("name"), out)


def ensure_for_camera(cam: dict) -> bool:
    """Kamera uchun relay ishlab turganini ta'minlaydi (idempotent).

    Global limit (`HLS_RELAY_MAX`) dan oshsa ishga tushirmaydi — ortiqcha
    ffmpeg serverga yuklama qilmasin. Qaytaradi: relay tirikmi.
    """
    if not relay_enabled_for(cam):
        return False
    cam_id = int(cam["id"])
    with _lock:
        entry = _procs.get(cam_id)
        if entry and entry["proc"].poll() is None:
            return True
        # o'lgan relay bo'lsa tozalaymiz
        if entry:
            try:
                entry["proc"].wait(timeout=1)
            except Exception:  # noqa: BLE001
                pass
            _procs.pop(cam_id, None)
        if _running_count() >= config.HLS_RELAY_MAX:
            log.info("relay: limit %s — kamera %s ishga tushirilmadi",
                     config.HLS_RELAY_MAX, cam_id)
            return False
        _start(cam)
        return cam_id in _procs


def relay_enabled_for(cam: dict) -> bool:
    return (
        config.HLS_RELAY_ENABLED
        and ffmpeg_available()
        and str(cam.get("source_type") or "") == "rtsp"
        and bool(cam.get("enabled", 1))
    )


def stop_for_camera(cam_id: int) -> None:
    with _lock:
        entry = _procs.pop(int(cam_id), None)
    if not entry:
        return
    proc = entry["proc"]
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    log.info("relay: kamera %s to'xtatildi", cam_id)


def sync(cameras: list) -> None:
    """Kamera ro'yxati bilan relay'larni sinxronlaydi (on-demand).

    * rtsp + enabled + yaqinda ko'rilgan → relay ishga tushadi (limit ichida)
    * ko'rilmagan / o'chirilgan / rtsp emas → relay to'xtatiladi
    * ffmpeg yo'q bo'lsa hech narsa qilmaydi (log bir marta)

    Endi barcha rtsp kamera doim ishlamaydi: faqat HLS'iga so'rov kelgan
    (`note_viewer`) kameralar uchun relay ko'tariladi (100+ kamera uchun).
    """
    if not ffmpeg_available():
        return
    now = _now()
    wanted: dict[int, dict] = {}
    for cam in cameras:
        if relay_enabled_for(cam) and viewer_active(int(cam["id"]), now):
            wanted[int(cam["id"])] = cam
    for cam_id, cam in wanted.items():
        ensure_for_camera(cam)
    with _lock:
        stale = [cid for cid in _procs if cid not in wanted]
    for cam_id in stale:
        stop_for_camera(cam_id)


def status() -> dict:
    with _lock:
        out = {}
        for cam_id, entry in _procs.items():
            out[str(cam_id)] = {
                "running": entry["proc"].poll() is None,
                "pid": entry["proc"].pid,
                "hls": public_url(cam_id),
            }
        return out


def start_monitor(cameras_provider, interval: float | None = None) -> None:
    """Fon tredi: periodik sync + o'lgan relay'larni qayta ko'tarish."""
    global _cam_provider
    _cam_provider = cameras_provider

    def _loop() -> None:
        while True:
            try:
                sync(cameras_provider())
            except Exception:  # noqa: BLE001
                log.exception("relay monitor xatosi")
            time.sleep(interval or config.HLS_RELAY_CHECK_S)

    threading.Thread(target=_loop, daemon=True, name="bm-gps-relay").start()
