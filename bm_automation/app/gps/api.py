"""GPS HTTP API — frontend uchun (port 8081).

Dashboard server.py'dagi xuddi shu naqsh: stdlib ThreadingHTTPServer,
DASHBOARD_TOKEN (Bearer) bilan himoyalangan, JSON javoblar.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pathlib import Path

from . import config, listener, relay, storage

log = logging.getLogger(__name__)

_STARTED = time.time()
_last_purge = {"at": 0.0}

# Kuzatuv UI — bu servisning o'z statik sahifasi.
UI_FILE = Path(__file__).with_name("gps_ui.html")


def _check_token(handler: BaseHTTPRequestHandler, qs: dict | None = None) -> bool:
    """DASHBOARD_TOKEN sozlangan bo'lsa tekshiradi (dashboard bilan bir xil).

    Qabul qilinadigan usullar:
      1) Authorization: Bearer <token>
      2) bm_token cookie (dashboard bilan umumiy sessiya)
      3) ?token=<token> query parametri (sahifa linklari / <video> tegi uchun)
    """
    if not config.GPS_API_TOKEN:
        return True
    auth = handler.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip() == config.GPS_API_TOKEN
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_token":
            return kv[1].strip() == config.GPS_API_TOKEN
    if qs:
        qtok = (qs.get("token") or [""])[0].strip()
        if qtok and qtok == config.GPS_API_TOKEN:
            return True
    return False


def _maybe_purge() -> None:
    """Har 6 soatda eski pozitsiyalarni tozalaydi."""
    if time.time() - _last_purge["at"] < 6 * 3600:
        return
    _last_purge["at"] = time.time()
    try:
        storage.purge_old_positions()
    except Exception:  # noqa: BLE001
        log.exception("GPS: purge xatosi")


class GpsHandler(BaseHTTPRequestHandler):
    server_version = "BM-GPS/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        log.debug("gps-api: %s", fmt % args)

    # ------------------------------------------------------------- helpers

    def _json(self, obj, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Dashboard (:8080) o'z tokeni bilan to'g'ridan-to'g'ri ulanishi uchun CORS.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Cookie",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Cookie",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_ui(self) -> None:
        """Kuzatuv sahifasi (vanilla JS + Leaflet) — token to'g'ridan-to'g'ri URL'da.
        Bu HTTP API faqat localhost'da emas, 0.0.0.0'da ishlaydi, shuning uchun
        sahifa ham tarmoqdan ochiladi (telefon/dacha monitorda ko'rish uchun).
        """
        if not UI_FILE.exists():
            self._json({"error": "gps_ui.html topilmadi"}, 404)
            return
        body = UI_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _qs(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def _serve_hls(self, fname: str) -> None:
        """HLS segmentlarini HLS_DIR'dan beradi (relay chiqishi).

        Token bilan himoyalangan (?token= yoki Bearer). Faqat xavfsiz
        fayl nomlari: harflar/raqamlar/_-.
        """
        if not re.fullmatch(r"[A-Za-z0-9_\-.]+", fname):
            self._json({"error": "noto'g'ri fayl nomi"}, 400)
            return
        f = Path(config.HLS_DIR) / fname
        if not f.exists():
            # live oqim boshlanishida segment hali tayyor bo'lmasligi mumkin
            self._json({"error": "segment hali tayyor emas"}, 404)
            return
        ctype = (
            "application/vnd.apple.mpegurl"
            if fname.endswith(".m3u8")
            else "video/mp2t"
        )
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store" if fname.endswith(".m3u8") else "max-age=30")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict:
        raw_len = (self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(raw_len)
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > 256 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw or b"{}")
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # --------------------------------------------------------------- GET

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = self._qs()

        # Kuzatuv UI — sahifa o'zi ochiq, data bilan so'rovlar token bilan
        # himoyalangan (token URL'ga ?token= sifatida yoziladi → JS ga o'tadi).
        if path in ("/", "/gps", "/index.html"):
            self._serve_ui()
            return

        if path == "/api/gps/health":
            # Health — himoyasiz (liveness uchun), faqat umumiy ma'lumot.
            self._json({
                "ok": True,
                "service": "bm-gps",
                "uptime_s": int(time.time() - _STARTED),
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "listener": listener.stats(),
                "tcp_port": config.GPS_TCP_PORT,
            })
            return

        if not _check_token(self, qs):
            self._json(
                {"error": "Token noto'g'ri. Authorization: Bearer <token> kerak"},
                401,
            )
            return

        try:
            _maybe_purge()
            if path == "/api/gps/devices":
                devices = storage.list_devices()
                self._json({"ok": True, "count": len(devices), "devices": devices})
            elif path == "/api/gps/positions":
                self._json({
                    "ok": True,
                    "positions": storage.positions(
                        imei=(qs.get("imei") or [""])[0].strip(),
                        vehicle_id=(qs.get("vehicle_id") or [""])[0].strip(),
                        date_from=(qs.get("from") or [""])[0].strip(),
                        date_to=(qs.get("to") or [""])[0].strip(),
                        limit=int((qs.get("limit") or ["5000"])[0] or 5000),
                    ),
                })
            elif path == "/api/video/cameras":
                cams = storage.list_cameras()
                self._json({"ok": True, "count": len(cams), "cameras": cams})
            elif path == "/api/video/relay":
                self._json({
                    "ok": True,
                    "ffmpeg": relay.ffmpeg_available(),
                    "hls_dir": config.HLS_DIR,
                    "relays": relay.status(),
                })
            elif path == "/api/gps/geofences":
                fences = storage.list_geofences()
                self._json({"ok": True, "count": len(fences), "geofences": fences})
            elif path == "/api/gps/alerts":
                alerts = storage.list_alerts(
                    limit=int((qs.get("limit") or ["50"])[0] or 50),
                    imei=(qs.get("imei") or [""])[0].strip(),
                )
                self._json({"ok": True, "count": len(alerts), "alerts": alerts})
            elif path == "/api/gps/daily":
                daily = storage.daily(
                    imei=(qs.get("imei") or [""])[0].strip(),
                    date_from=(qs.get("from") or [""])[0].strip(),
                    date_to=(qs.get("to") or [""])[0].strip(),
                )
                self._json({"ok": True, "count": len(daily), "daily": daily})
            elif path.startswith("/hls/"):
                self._serve_hls(path[len("/hls/"):])
            else:
                self._json({"error": "topilmadi"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("gps-api GET xato: %s", path)
            self._json({"ok": False, "error": str(exc)[:300]}, 500)

    # -------------------------------------------------------------- POST

    def do_POST(self):  # noqa: N802
        if not _check_token(self, self._qs()):
            self._json({"error": "Token noto'g'ri"}, 401)
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        payload = self._payload()
        try:
            if path == "/api/gps/devices":
                imei = str(payload.get("imei") or "").strip()
                if not imei:
                    self._json({"ok": False, "error": "imei kerak"}, 400)
                    return
                storage.register_device(imei)
                storage.bind_device(
                    imei,
                    str(payload.get("vehicle_id") or "").strip(),
                    str(payload.get("name") or "").strip(),
                )
                self._json({"ok": True, "imei": imei})
            elif path == "/api/video/cameras":
                name = str(payload.get("name") or "").strip()
                url = str(payload.get("url") or "").strip()
                if not name or not url:
                    self._json({"ok": False, "error": "name va url kerak"}, 400)
                    return
                src_type = str(payload.get("source_type") or "hls").strip()
                cid = storage.add_camera(
                    name=name,
                    url=url,
                    source_type=src_type,
                    vehicle_id=str(payload.get("vehicle_id") or "").strip(),
                )
                # RTSP kamera → relay darhol ko'tariladi (fon tredi ham nazorat qiladi)
                if src_type == "rtsp" and cid:
                    try:
                        relay.ensure_for_camera({
                            "id": cid, "name": name, "url": url,
                            "source_type": "rtsp", "enabled": 1,
                        })
                    except Exception:  # noqa: BLE001
                        log.exception("relay darhol ishga tushmadi (kamera %s)", cid)
                self._json({"ok": True, "id": cid})
            elif path == "/api/gps/geofences":
                try:
                    lat = float(payload.get("lat"))
                    lng = float(payload.get("lng"))
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "lat/lng kerak"}, 400)
                    return
                name = str(payload.get("name") or "").strip()
                if not name:
                    self._json({"ok": False, "error": "nomi kerak"}, 400)
                    return
                fid = storage.add_geofence(
                    name=name, lat=lat, lng=lng,
                    radius_m=int(payload.get("radius_m") or 500),
                    vehicle_id=str(payload.get("vehicle_id") or "").strip(),
                )
                self._json({"ok": True, "id": fid})
            else:
                self._json({"error": "topilmadi"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("gps-api POST xato: %s", path)
            self._json({"ok": False, "error": str(exc)[:300]}, 500)

    # -------------------------------------------------------------- PATCH/DELETE

    def do_PATCH(self):  # noqa: N802
        if not _check_token(self, self._qs()):
            self._json({"error": "Token noto'g'ri"}, 401)
            return
        path = urlparse(self.path).path.rstrip("/")
        parts = [p for p in path.split("/") if p]  # ["api","gps","devices",imei]
        try:
            if len(parts) == 4 and parts[:3] == ["api", "gps", "devices"]:
                payload = self._payload()
                if "enabled" in payload:
                    storage.set_device_enabled(parts[3], bool(payload["enabled"]))
                if "vehicle_id" in payload or "name" in payload:
                    storage.bind_device(
                        parts[3],
                        str(payload.get("vehicle_id") or "").strip(),
                        str(payload.get("name") or "").strip(),
                    )
                self._json({"ok": True})
                return
            if (len(parts) == 4 and parts[:3] == ["api", "gps", "geofences"]
                    and "enabled" in self._payload()):
                storage.set_geofence_enabled(
                    int(parts[3]), bool(self._payload()["enabled"])
                )
                self._json({"ok": True})
                return
            self._json({"error": "topilmadi"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("gps-api PATCH xato")
            self._json({"ok": False, "error": str(exc)[:300]}, 500)

    def do_DELETE(self):  # noqa: N802
        if not _check_token(self, self._qs()):
            self._json({"error": "Token noto'g'ri"}, 401)
            return
        path = urlparse(self.path).path.rstrip("/")
        parts = [p for p in path.split("/") if p]  # ["api","video","cameras","3"]
        try:
            if len(parts) == 4 and parts[:3] == ["api", "video", "cameras"]:
                relay.stop_for_camera(int(parts[3]))
                storage.delete_camera(int(parts[3]))
                self._json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "gps", "geofences"]:
                storage.delete_geofence(int(parts[3]))
                self._json({"ok": True})
                return
            self._json({"error": "topilmadi"}, 404)
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "id noto'g'ri"}, 400)
        except Exception as exc:  # noqa: BLE001
            log.exception("gps-api DELETE xato")
            self._json({"ok": False, "error": str(exc)[:300]}, 500)


def run_api(host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), GpsHandler)
    log.info("GPS HTTP API: http://%s:%s (DASHBOARD_TOKEN=%s)",
             host, port, "yoqilgan" if config.GPS_API_TOKEN else "o'chirilgan")
    return httpd
