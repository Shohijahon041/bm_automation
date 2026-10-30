"""GPS HTTP API — frontend uchun (port 8081).

Stdlib ThreadingHTTPServer, DASHBOARD_TOKEN (Bearer) bilan himoyalangan,
JSON javoblar. (Asosiy dashboard FastAPI'ga o'tgan; bu servis mustaqil.)
"""

from __future__ import annotations

import json
import logging
import os
import hashlib
import hmac
import secrets
import time
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import shutil
from pathlib import Path

from . import (camera, compare, config, jt1078, jt808, listener, relay,
               route_alert, stops, storage)

log = logging.getLogger(__name__)

_STARTED = time.time()
_last_purge = {"at": 0.0}

# Kuzatuv UI — bu servisning o'z statik sahifasi.
UI_FILE = Path(__file__).with_name("gps_ui.html")


def _int_or(qs: dict, key: str, default=None):
    """Query parametr → musbat int yoki default (bo'sh/noto'g'ri bo'lsa)."""
    try:
        v = int((qs.get(key) or [""])[0] or 0)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _client_is_local(handler: BaseHTTPRequestHandler) -> bool:
    """So'rov loopback'dan kelganmi (token sozlanmagan rejim uchun)."""
    try:
        host = str((handler.client_address or ("",))[0]).strip().lower()
    except (AttributeError, IndexError, TypeError):
        return False
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def _check_token(handler: BaseHTTPRequestHandler, qs: dict | None = None) -> bool:
    """DASHBOARD_TOKEN sozlangan bo'lsa tekshiradi (dashboard bilan bir xil).

    Qabul qilinadigan usullar:
      1) Authorization: Bearer <token>
      2) bm_token cookie (dashboard master token)
      3) ?token=<token> query parametri (sahifa linklari / <video> tegi uchun)
      4) bm_session cookie — dashboard foydalanuvchi sessiyasi (ko'p korxonali
         rejim: qamrov _company_vehicles orqali kompaniya bilan cheklanadi).

    Bo'sh yoki noto'g'ri Bearer boshqa usullarni bloklamaydi: dashboard
    `bmFetch` har doim `Authorization: Bearer <token>` yuboradi (token
    bo'lmasa bo'sh) — shuning uchun bu yerda erta `return False` qilinmaydi.
    Token umuman sozlanmagan bo'lsa API faqat loopback'dan ochiq (fail-closed
    — 0.0.0.0'da avtomatik ochilib qolmasin).
    """
    if not config.GPS_API_TOKEN:
        return _client_is_local(handler)
    want = config.GPS_API_TOKEN
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth[:7].lower() == "bearer " and hmac.compare_digest(auth[7:].strip(), want):
        return True
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_token" \
                and hmac.compare_digest(kv[1].strip(), want):
            return True
    if qs:
        qtok = (qs.get("token") or [""])[0].strip()
        if qtok and hmac.compare_digest(qtok, want):
            return True
    return _valid_session(handler)


def _valid_session(handler: BaseHTTPRequestHandler) -> bool:
    """Dashboard sessiyasi (bm_session) haqiqiy bo'lsa True."""
    token = _session_cookie(handler)
    if not token:
        return False
    from ..dashboard.deps import session_user
    try:
        return bool(session_user(token))
    except Exception:  # noqa: BLE001
        log.exception("GPS: sessiya tekshiruvi xatosi")
        return False


def _session_cookie(handler: BaseHTTPRequestHandler) -> str:
    """bm_session cookie qiymati (dashboard sessiyasi)."""
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_session":
            return kv[1].strip()
    return ""


def _user_scope(handler: BaseHTTPRequestHandler) -> dict | None:
    """Dashboard sessiyasi bo'yicha foydalanuvchi qamrovi (token ustun).

    Brauzerda master token (bm_token cookie) ham bor — sessiya uchun
    in'ektsiya qilingan. Ko'p korxonali rejimda xodim o'z kompaniyasi
    chegarasida qolishi uchun sessiya token'dan ustun turadi (dashboard
    `_resolve_scope` bilan bir xil qoida).

      None                            -> sessiya yo'q (master token = ADMIN)
      {"is_admin": True, ...}         -> ADMIN sessiya
      {"is_admin": False, "routes"}   -> kompaniya limiti
    """
    token = _session_cookie(handler)
    if not token:
        return None
    from ..dashboard.deps import session_user
    return session_user(token)


def _maybe_purge() -> None:
    """Har 6 soatda eski pozitsiyalarni tozalaydi."""
    if time.time() - _last_purge["at"] < 6 * 3600:
        return
    _last_purge["at"] = time.time()
    try:
        storage.purge_old_positions()
    except Exception:  # noqa: BLE001
        log.exception("GPS: purge xatosi")


def _scope_assist(items: list, allowed: set) -> list:
    """Solishtirma natijasini korxona qamroviga keltiradi (ko'p korxonali).

    * boshqa korxona avtobusiga bog'langan kamera — chiqarib tashlanadi;
    * bog'lanmagan kamera qoladi (u hamon "bog'lash kerak" ro'yxati), lekin
      tavsiya etilgan avtobus boshqa korxonaga tegishli bo'lsa, tavsiya
      (raqam/nom) ko'rsatilmaydi — aks holda boshqa korxona raqami sizib
      chiqadi.
    """
    out = []
    for it in items or []:
        vid = str(it.get("vehicle_id") or "")
        if vid and vid not in allowed:
            continue
        sug = str(it.get("suggest_vehicle_id") or "")
        if sug and sug not in allowed:
            it = dict(it)
            it["suggest_vehicle_id"] = ""
            it["suggest_plate"] = ""
            it["suggest_dist_m"] = None
        out.append(it)
    return out


def _device_kind(payload: dict, imei: str) -> str:
    """Payload'dagi 'kind' ni normallashtiradi (avtobusdagi 3 turdagi qurilma).

    teltonika = xarita GPS, camera = JT808 video, counter = odam sanagich.
    Tur berilmagan bo'lsa: 12 xonali IMEI — JT808 kamera (Teltonika 15
    xonali bo'ladi), aks holda teltonika. Noma'lum qiymat ham teltonika
    deb qabul qilinmaydi — chaqiruvchida 400 qaytarilishi uchun '' qaytadi.
    """
    k = str(payload.get("kind") or "").strip().lower()
    aliases = {
        "teltonika": "teltonika", "gps": "teltonika",
        "camera": "camera", "kamera": "camera", "jt808": "camera",
        "counter": "counter", "sanagich": "counter", "people": "counter",
    }
    if k in aliases:
        return aliases[k]
    if k:
        return ""
    return "camera" if len(imei) == 12 else "teltonika"


def _has_video_perm(scope: dict | None, perm: str) -> bool:
    if not scope:
        return True
    if scope.get("is_admin") or scope.get("role") in ("ADMIN", "DIRECTOR"):
        return True
    perms = scope.get("video_perms")
    if perms is None:
        return True
    if isinstance(perms, (list, tuple, set)):
        return perm in perms or "all" in perms
    return True


def _resolve_media_host(req_host: str | None = None) -> str:
    """Qurilma oqimni yuborishi uchun media host manzilini aniqlash.

    Qurilmalarga 'bmbrutto.uz' va port 6107 sozlangan. 0x9101/0x9201 buyruqlarida
    domen nomi kelsa, 4G MDVR DNS kechikishlarini oldini olish uchun
    'bmbrutto.uz' -> '189.74.100.25' ga o'giriladi.
    """
    h = (req_host or "").strip() or config.JT1078_MEDIA_HOST or "bmbrutto.uz"
    if h in ("bmbrutto.uz", "www.bmbrutto.uz"):
        return "189.74.100.25"
    return h


class GpsHandler(BaseHTTPRequestHandler):
    server_version = "BM-GPS/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        log.debug("gps-api: %s", fmt % args)

    # ------------------------------------------------------------- helpers

    def _cors_origin(self) -> str:
        """So'rov Origin'i uchun ruxsat etilgan qiymat (aks holda '').

        Ilgari HAR QANDAY Origin aks ettirilardi (`Allow-Credentials: true`
        bilan) — begona sayt foydalanuvchi cookie'si bilan ma'lumot o'qishi
        mumkin edi. Endi whitelist:
          * o'z hosti (nginx `/gps` proxy va to'g'ridan-to'g'ri kirish);
          * `GPS_CORS_ORIGINS` (vergul bilan, `.env`);
          * lokal ishlab chiqish (`localhost`/`127.0.0.1` istalgan portda).
        """
        raw = (self.headers.get("Origin") or "").strip().rstrip("/")
        if not raw:
            return ""
        low = raw.lower()
        host = (self.headers.get("Host") or "").strip().lower()
        allowed = {f"http://{host}", f"https://{host}"} if host else set()
        for item in (os.environ.get("GPS_CORS_ORIGINS") or "").split(","):
            item = item.strip().rstrip("/").lower()
            if item:
                allowed.add(item)
        try:
            parsed = urlparse(low)
            if parsed.scheme in ("http", "https") and (
                    parsed.hostname in ("localhost", "127.0.0.1", "::1")):
                return raw
        except ValueError:
            return ""
        return raw if low in allowed else ""

    def _json(self, obj, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, Cookie",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802
        """CORS preflight — faqat whitelist'dagi Origin uchun."""
        origin = self._cors_origin()
        if not origin:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
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
        # Yangilangandan keyin eski sahifa keshdan ochilib qolmasin.
        self.send_header("Cache-Control", "no-cache")
        # URL'da token bo'lishi mumkin — tashqi resurslarga token ketmasligi uchun faqat origin.
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self' blob: data: https: 'unsafe-inline' 'unsafe-eval'; "
            "frame-src 'self' blob: data: https://leafletjs.com; "
            "frame-ancestors 'self' https: http:;",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _qs(self) -> dict:
        return parse_qs(urlparse(self.path).query)

    def _session_admin(self) -> bool:
        """So'rov egasi ADMIN (sessiyasiz/master token yoki ADMIN sessiya)."""
        scope = _user_scope(self)
        return not scope or bool(scope.get("is_admin"))

    def _company_vehicles(self) -> set | None:
        """Non-admin uchun ruxsat etilgan vehicle_id'lar; admin -> None.

        Kompaniya foydalanuvchisi o'z yo'nalishlaridagi avtomobillar sonini
        ko'radi xolos; bo'sh qamrov -> bo'sh set (fail-closed).
        """
        scope = _user_scope(self)
        if not scope or scope.get("is_admin"):
            return None
        rids = [str(r) for r in (scope.get("routes") or []) if str(r).strip()]
        if not rids:
            return set()
        from ..db.storage import get_storage
        st = get_storage()
        if not st.enabled or not getattr(st, "db", None):
            return set()
        try:
            ph = ", ".join(st.db.ph for _ in rids)
            rows = st.db.query(
                f"SELECT external_id FROM vehicles WHERE route_id IN ({ph})",
                tuple(rids))
            return {str(r.get("external_id") or "") for r in rows}
        except Exception:  # noqa: BLE001
            log.exception("GPS: vehicle qamrovi hisoblanmadi")
            return set()

    @staticmethod
    def _only_vehicles(items: list, allowed: set | None) -> list:
        """allowed None bo'lsa hammasi; aks holda vehicle_id bo'yicha filtraydi."""
        if allowed is None:
            return items
        return [it for it in items
                if str(it.get("vehicle_id") or "") in allowed]

    def _allowed_imes(self, allowed: set | None) -> set:
        """Qamrovdagi IMEI'lar (device'lar vehicle_id orqali bog'langan)."""
        if allowed is None:
            return set()
        return {str(d.get("imei") or "")
                for d in storage.teltonika_only(storage.list_devices())
                if str(d.get("vehicle_id") or "") in allowed}

    def _camera_allowed(self, cam_id: int) -> bool:
        """Kamera (hls oqimi ham) qamrovga tegishli bo'lsa True."""
        allowed = self._company_vehicles()
        if allowed is None:
            return True
        for cam in storage.list_cameras():
            if str(cam.get("id") or "") == str(cam_id):
                vid = str(cam.get("vehicle_id") or "").strip()
                return not vid or vid in allowed
        return False

    def _phone_allowed(self, phone: str) -> bool:
        allowed = self._company_vehicles()
        if allowed is None:
            return True
        cam_devices = storage.list_camera_devices()
        phone_norm = phone.lstrip("0")
        for device in cam_devices:
            dev_imei = str(device.get("imei") or "").lstrip("0")
            if dev_imei == phone_norm:
                vid = str(device.get("vehicle_id") or "").strip()
                return not vid or vid in allowed
        connected = [p.lstrip("0") for p in jt808.connected_phones()]
        if phone_norm in connected:
            return True
        return False

    @staticmethod
    def _valid_phone(phone: str) -> bool:
        return bool(re.fullmatch(r"[0-9]{3,20}", phone))

    @staticmethod
    def _media_datetime(value: str) -> datetime | None:
        text = str(value or "").strip().replace("T", " ").removesuffix("Z")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _require_video_perm(self, perm: str) -> bool:
        scope = _user_scope(self)
        if scope is None:
            return True
        if _has_video_perm(scope, perm):
            return True
        self._json({"error": "Sizda bu video harakati uchun ruxsat yo'q"}, 403)
        return False

    def _log_jt_video(self, phone: str, channel: int, action: str) -> None:
        try:
            scope = _user_scope(self) or {}
            vehicle_id = ""
            for device in storage.list_camera_devices():
                if str(device.get("imei") or "") == phone:
                    vehicle_id = str(device.get("vehicle_id") or "")
                    break
            storage.log_video_audit(
                0, int(scope.get("user_id") or 0),
                str(scope.get("username") or ""), action,
                channel=channel, vehicle_id=vehicle_id,
                ip=str(self.client_address[0] if self.client_address else ""),
            )
        except Exception:  # noqa: BLE001
            log.exception("JT video audit yozilmadi")

    def _log_camera_video(self, cam_id: int, action: str) -> None:
        try:
            scope = _user_scope(self) or {}
            storage.log_video_audit(
                cam_id, int(scope.get("user_id") or 0),
                str(scope.get("username") or ""), action,
                ip=str(self.client_address[0] if self.client_address else ""),
            )
        except Exception:  # noqa: BLE001
            log.exception("Kamera video audit yozilmadi")

    def _sign_video_token(self, cam_id: int, action: str, ttl_s: int = 300) -> str:
        """Video oqimi uchun dashboard bilan bir xil imzoli token yaratadi."""
        from ..dashboard.routers.admin import _sign_video_token as _sign
        scope = _user_scope(self) or {}
        return _sign(cam_id, int(scope.get("user_id") or 0), action, ttl_s)

    def _require_admin(self) -> bool:
        """Yozish amallari faqat ADMIN uchun (403 bo'lsa javob yuborilgan)."""
        if self._session_admin():
            return True
        self._json({"error": "Sizda bu bo'limga ruxsat yo'q"}, 403)
        return False

    def _serve_hls(self, fname: str) -> None:
        """HLS segmentlarini HLS_DIR'dan beradi (relay chiqishi).

        Token bilan himoyalangan (?token= yoki Bearer). Faqat xavfsiz
        fayl nomlari: harflar/raqamlar/_-.
        """
        if not re.fullmatch(r"[A-Za-z0-9_\-.]+", fname):
            self._json({"error": "noto'g'ri fayl nomi"}, 400)
            return
        f = Path(config.HLS_DIR) / fname
        try:
            st = f.stat()
        except OSError:
            # live oqim boshlanishida segment hali tayyor bo'lmasligi mumkin
            self._json({"error": "segment hali tayyor emas"}, 404)
            return
        is_playlist = fname.endswith(".m3u8")
        etag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        origin = self.headers.get("Origin") or "*"
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Authorization, Content-Type")
        self.send_header(
            "Content-Type",
            "application/vnd.apple.mpegurl" if is_playlist else "video/mp2t")
        self.send_header("ETag", etag)
        self.send_header(
            "Last-Modified", self.date_time_string(int(st.st_mtime)))
        if fname.endswith(".m3u8"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("Content-Length", str(st.st_size))
        self.end_headers()
        if self.command == "HEAD":
            return
        # Bo'laklab yuboramiz — bitta sekin mijoz katta segmentni RAM'ga
        # to'liq yuklamaydi.
        with f.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile, 256 * 1024)

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
                "jt808_enabled": config.JT808_ENABLED,
                "jt808": jt808.stats() if config.JT808_ENABLED else None,
                "jt1078": jt1078.stats() if config.JT808_ENABLED else None,
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
            vehicles = self._company_vehicles()
            if path.startswith("/api/video/") or path.startswith("/hls/"):
                perm = "archive" if (
                    path == "/api/video/jt-files"
                    or path.startswith("/hls/jtpb_")
                ) else "live"
                if not self._require_video_perm(perm):
                    return
            if path == "/api/gps/devices":
                # Kamera (JT808/JT1078) va sanagich (counter) qurilmalari
                # xaritaga chiqmaydi — faqat Teltonika GPS. Kameralar
                # /api/gps/camera-assist va video bo'limida ko'rinadi.
                # Boshqaruv dialogi uchun ?kind=all barcha turlarni qaytaradi.
                rows = storage.list_devices()
                if (qs.get("kind") or [""])[0].strip().lower() != "all":
                    rows = storage.teltonika_only(rows)
                devices = self._only_vehicles(rows, vehicles)
                route_q = (qs.get("route") or [""])[0].strip()
                if route_q:
                    devices = [d for d in devices
                               if str(d.get("route_id") or "") == route_q]
                self._json({"ok": True, "count": len(devices), "devices": devices})
            elif path == "/api/gps/camera-assist":
                # Kamera GPS ↔ Teltonika GPS solishtirmasi (YORDAMCHI).
                # Kamera XARITADA ko'rsatilmaydi; bu ma'lumot kamera qaysi
                # avtobusda ekanini aniqlash va "drift" (joy farqi, ya'ni
                # o'rnatish/bog'lash xatosi) ni ko'rish uchun.
                items = camera.assist()
                if vehicles is not None:
                    items = _scope_assist(items, vehicles)
                self._json({
                    "ok": True,
                    "threshold_m": config.GPS_CAMERA_MATCH_M,
                    "count": len(items),
                    "items": items,
                })
            elif path == "/api/gps/routes":
                # GPS qurilmasi bog'langan avtobuslar bo'yicha yo'nalishlar
                # (qamrov allaqachon qurilmalarga qo'llangan).
                devs = self._only_vehicles(
                    storage.teltonika_only(storage.list_devices()), vehicles)
                agg: dict = {}
                for d in devs:
                    rid = str(d.get("route_id") or "").strip()
                    if not rid:
                        continue
                    a = agg.setdefault(rid, {"id": rid, "name": "",
                                             "devices": 0, "online": 0})
                    a["devices"] += 1
                    if d.get("online"):
                        a["online"] += 1
                names = storage.route_names(list(agg.keys()))
                routes = []
                for rid, a in agg.items():
                    a["name"] = names.get(rid) or rid
                    routes.append(a)
                routes.sort(key=lambda x: x["name"])
                self._json({"ok": True, "count": len(routes), "routes": routes})
            elif path == "/api/gps/vehicles":
                # Mavjud avtobuslar (vehicles jadvali) + bog'langan qurilma.
                # Qurilmani avtobusga bog'lash uchun tanlov ro'yxati.
                vs = storage.list_vehicles()
                if vehicles is not None:
                    vs = [v for v in vs
                          if str(v.get("external_id") or "") in vehicles]
                self._json({"ok": True, "count": len(vs), "vehicles": vs})
            elif path == "/api/gps/positions":
                imes = self._allowed_imes(vehicles)
                rows = storage.positions(
                    imei=(qs.get("imei") or [""])[0].strip(),
                    vehicle_id=(qs.get("vehicle_id") or [""])[0].strip(),
                    date_from=(qs.get("from") or [""])[0].strip(),
                    date_to=(qs.get("to") or [""])[0].strip(),
                    limit=int((qs.get("limit") or ["5000"])[0] or 5000),
                )
                if imes:
                    rows = [p for p in rows
                            if str(p.get("imei") or "") in imes
                            or str(p.get("vehicle_id") or "") in vehicles]
                self._json({"ok": True, "positions": rows})
            elif path == "/api/video/cameras":
                cams = self._only_vehicles(storage.list_cameras(), vehicles)
                safe_cams = []
                for camera_row in cams:
                    camera_row = dict(camera_row)
                    camera_row.pop("url", None)
                    safe_cams.append(camera_row)
                self._json({"ok": True, "count": len(safe_cams),
                            "cameras": safe_cams})
            elif path == "/api/video/grid":
                # Video setka: bitta kanal bo'yicha barcha avtobuslar
                channel = int((qs.get("channel") or ["1"])[0] or 1)
                page = int((qs.get("page") or ["1"])[0] or 1)
                page_size = int((qs.get("page_size") or ["25"])[0] or 25)
                cams = self._only_vehicles(storage.list_cameras(), vehicles)
                result = []
                for cam in cams:
                    cam_id = cam.get("id")
                    if not cam_id:
                        continue
                    # Qisqa muddatli token (5 daqiqa)
                    token = self._sign_video_token(cam_id, "grid", 300)
                    result.append({
                        "camera_id": cam_id,
                        "name": cam.get("name"),
                        "source_type": cam.get("source_type"),
                        "vehicle_id": cam.get("vehicle_id"),
                        "plate_number": cam.get("plate_number"),
                        "token": token,
                        "channel": channel,
                        "status": "online",
                    })
                total = len(result)
                start = (page - 1) * page_size
                end = start + page_size
                self._json({
                    "ok": True,
                    "channel": channel,
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": (total + page_size - 1) // page_size,
                    "items": result[start:end],
                })
            elif path == "/api/video/relay":
                relays = relay.status()
                if vehicles is not None:
                    ok_cams = {str(c.get("id") or "") for c in
                               self._only_vehicles(storage.list_cameras(),
                                                   vehicles)}
                    relays = {cid: v for cid, v in relays.items()
                              if cid in ok_cams}
                self._json({
                    "ok": True,
                    "ffmpeg": relay.ffmpeg_available(),
                    "hls_dir": config.HLS_DIR,
                    "limit": config.HLS_RELAY_MAX,
                    "relays": relays,
                })
            elif path == "/api/video/jt-streams":
                # JT1078 (CMSV6/4G) avtomatik aniqlangan jonli/yozuv oqimlari.
                # Har bir oqim diagnostikasi bilan: kodek, kadr/kalit kadr
                # va oxirgi kalit kadr vaqti, restart soni, oxirgi xato.
                enabled = config.JT808_ENABLED
                streams = jt1078.streams() if enabled else []
                phones = jt808.connected_phones() if enabled else []
                # Kamera ↔ avtobus bog'lanishi (video xonasida avtobus
                # bo'yicha tanlash uchun) va GPS moslik/drift holati.
                #
                # Ko'p korxonali rejim: kamera telefon raqami, avtobus
                # raqami va kanallar boshqa korxonaga oid ma'lumot —
                # non-admin faqat o'z qamrovidagi kameralarni ko'radi
                # (dashboard `video_grid` bilan bir xil qoida).
                cams = []
                try:
                    cams = storage.list_camera_devices()
                except Exception:  # noqa: BLE001
                    log.exception("jt-streams: kameralar olinmadi")
                if vehicles is not None:
                    cams = [d for d in cams
                            if str(d.get("vehicle_id") or "") in vehicles]
                allowed_phones_norm = {str(d.get("imei") or "").lstrip("0") for d in cams}
                bindings: dict = {}
                for d in cams:
                    raw_imei = str(d.get("imei") or "").strip()
                    if not raw_imei:
                        continue
                    val = {
                        "vehicle_id": str(d.get("vehicle_id") or ""),
                        "plate": str(d.get("plate_number") or ""),
                        "name": str(d.get("name") or ""),
                    }
                    bindings[raw_imei] = val
                    norm_imei = raw_imei.lstrip("0")
                    if norm_imei:
                        bindings[norm_imei] = val
                assist: list = []
                try:
                    assist = camera.assist()
                except Exception:  # noqa: BLE001
                    log.exception("jt-streams: assist tahlili bajarilmadi")
                recent = jt1078.recent_streams() if enabled else []
                if vehicles is not None:
                    streams = [s for s in streams
                               if str(s.get("sim") or "").lstrip("0") in allowed_phones_norm]
                    recent = [s for s in recent
                              if str(s.get("sim") or "").lstrip("0") in allowed_phones_norm]
                    phones = [p for p in phones
                              if str(p).lstrip("0") in allowed_phones_norm]
                    assist = _scope_assist(assist, vehicles)
                self._json({
                    "ok": True,
                    "enabled": enabled,
                    "ffmpeg": jt1078.ffmpeg_available(),
                    "media_host": config.JT1078_MEDIA_HOST,
                    "devices": phones,
                    "camera_channels": config.JT_CAMERA_CHANNELS,
                    # PILOT: ZLMediaKit media-server holati (flag yoqilganda
                    # oqimlarda "flv" maydoni paydo bo'ladi).
                    "zlmediakit": bool(config.zlmediakit_enabled()),
                    "zlmediakit_http": config.ZLMEDIAKIT_HTTP,
                    "bindings": bindings,
                    "assist": assist,
                    "assist_threshold_m": config.GPS_CAMERA_MATCH_M,
                    "count": len(streams),
                    "streams": streams,
                    "recent": recent,
                })
            elif path == "/api/video/jt-files":
                # Qurilma xotirasidagi yozuvlar ro'yxati (oxirgi 0x1205
                # javobi) + so'rov holati: qurilma "jami" sonini yuborsa
                # received/total orqali ro'yxat tayyorligini bilish mumkin.
                phone = (qs.get("phone") or [""])[0].strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                if not self._valid_phone(phone):
                    self._json({"ok": False, "error": "phone noto'g'ri"}, 400)
                    return
                if not self._phone_allowed(phone):
                    self._json({"error": "Qurilma topilmadi"}, 404)
                    return
                info = jt808.resources_info(phone)
                self._json({"ok": True, "phone": phone, **info})
            elif path == "/api/video/jt-device":
                # Qurilma imkoniyatlari (0x0100/0x0104/0x0107/0x1003 keshi)
                # + oxirgi xom kadrlar — diagnostika.
                phone = (qs.get("phone") or [""])[0].strip()
                if phone:
                    if not self._valid_phone(phone):
                        self._json({"ok": False, "error": "phone noto'g'ri"}, 400)
                        return
                    if not self._phone_allowed(phone):
                        self._json({"error": "Qurilma topilmadi"}, 404)
                        return
                    self._json({
                        "ok": True, "phone": phone,
                        "info": jt808.device_info(phone),
                        "extras": jt808.av_info(phone),
                        "raw": jt808.raw_log(
                            int((qs.get("raw") or ["40"])[0] or 40), phone),
                    })
                    return
                phones = jt808.connected_phones()
                if vehicles is not None:
                    phones = [p for p in phones if self._phone_allowed(str(p))]
                    self._json({
                        "ok": True,
                        "devices": phones,
                        "info": {p: jt808.device_info(p) for p in phones},
                        "extras": {p: jt808.av_info(p) for p in phones},
                    })
                    return
                self._json({
                    "ok": True,
                    "devices": phones,
                    "info": jt808.device_info(),
                    "extras": jt808.av_info(),
                })
            elif path == "/api/gps/geofences":
                fences = self._only_vehicles(storage.list_geofences(), vehicles)
                self._json({"ok": True, "count": len(fences), "geofences": fences})
            elif path == "/api/gps/route-paths":
                paths = storage.list_route_paths()
                if vehicles is not None:
                    devs = self._only_vehicles(
                        storage.teltonika_only(storage.list_devices()), vehicles)
                    allowed = {str(d.get("route_id") or "") for d in devs}
                    paths = [p for p in paths
                             if str(p.get("route_id") or "") in allowed]
                for p in paths:
                    p["points_count"] = len(p.get("points") or [])
                self._json({"ok": True, "count": len(paths), "paths": paths})
            elif path == "/api/gps/alerts":
                alerts = storage.list_alerts(
                    limit=int((qs.get("limit") or ["50"])[0] or 50),
                    imei=(qs.get("imei") or [""])[0].strip(),
                )
                if vehicles is not None:
                    imes = self._allowed_imes(vehicles)
                    alerts = [a for a in alerts
                              if str(a.get("vehicle_id") or "") in vehicles
                              or str(a.get("imei") or "") in imes]
                self._json({"ok": True, "count": len(alerts), "alerts": alerts})
            elif path == "/api/gps/daily":
                daily = storage.daily(
                    imei=(qs.get("imei") or [""])[0].strip(),
                    date_from=(qs.get("from") or [""])[0].strip(),
                    date_to=(qs.get("to") or [""])[0].strip(),
                )
                if vehicles is not None:
                    imes = self._allowed_imes(vehicles)
                    daily = [d for d in daily
                             if str(d.get("imei") or "") in imes]
                self._json({"ok": True, "count": len(daily), "daily": daily})
            elif path == "/api/gps/stops":
                # To'xtashlar: imei yoki vehicle_id yoki qamrovdagi barcha
                # qurilmalar (ko'pi bilan 20 ta) uchun aniqlanadi.
                imei = (qs.get("imei") or [""])[0].strip()
                vehicle = (qs.get("vehicle_id") or [""])[0].strip()
                date_from = (qs.get("from") or [""])[0].strip()
                date_to = (qs.get("to") or [""])[0].strip()
                min_s = _int_or(qs, "min_s")
                radius = _int_or(qs, "radius")
                dev_scope = self._only_vehicles(
                    storage.teltonika_only(storage.list_devices()), vehicles)
                if imei:
                    targets = [d for d in dev_scope
                               if str(d.get("imei")) == imei]
                elif vehicle:
                    targets = [d for d in dev_scope
                               if str(d.get("vehicle_id")) == vehicle]
                else:
                    targets = dev_scope[:20]
                result = []
                for dv in targets:
                    im = str(dv.get("imei") or "")
                    if not im:
                        continue
                    try:
                        found = stops.stops_for(
                            im, date_from=date_from, date_to=date_to,
                            min_s=min_s, radius_m=radius,
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("stops: %s uchun xato", im)
                        continue
                    for s in found:
                        s["imei"] = im
                        s["vehicle_id"] = str(dv.get("vehicle_id") or "")
                        s["plate"] = str(
                            dv.get("plate_number") or dv.get("name") or "")
                        result.append(s)
                result.sort(key=lambda s: str(s.get("start") or ""))
                self._json({"ok": True, "count": len(result),
                            "stops": result[:2000]})
            elif path == "/api/gps/vs-manual":
                # GPS kunlik km ↔ qo'lda kiritilgan km (driver_work_logs)
                date_to = (qs.get("to") or [""])[0].strip()
                date_from = (qs.get("from") or [""])[0].strip()
                if not date_to:
                    date_to = datetime.now(timezone.utc).date().isoformat()
                if not date_from:
                    try:
                        date_from = (
                            datetime.fromisoformat(date_to).date()
                            - timedelta(days=6)
                        ).isoformat()
                    except ValueError:
                        date_from = date_to
                rows = compare.daily_comparison(
                    date_from, date_to, scope_vehicles=vehicles)
                self._json({"ok": True, "count": len(rows),
                            "from": date_from, "to": date_to, "rows": rows})
            elif path.startswith("/hls/"):
                fname = path[len("/hls/"):]
                cam_match = re.match(r"^cam(\d+)[.\-]", fname)
                jt_match = re.match(r"^(?:jt|jtpb)_([0-9]{3,20})_\d+[.\-]", fname)
                if cam_match:
                    cam_id = int(cam_match.group(1))
                    relay.note_viewer(cam_id)
                    if not self._camera_allowed(cam_id):
                        self._json({"error": "segment topilmadi"}, 404)
                        return
                elif jt_match:
                    if not self._phone_allowed(jt_match.group(1)):
                        self._json({"error": "segment topilmadi"}, 404)
                        return
                elif vehicles is not None:
                    self._json({"error": "segment topilmadi"}, 404)
                    return
                self._serve_hls(fname)
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
        video_perms = {
            "/api/video/jt-live": "live",
            "/api/video/jt-stop": "live",
            "/api/video/jt-files": "archive",
            "/api/video/jt-playback": "archive",
            "/api/video/jt-playback-control": "archive",
            "/api/video/relay": "live",
            "/api/video/relay-stop": "live",
        }
        video_perm = video_perms.get(path)
        if video_perm:
            if not self._require_video_perm(video_perm):
                return
        elif not self._require_admin():
            return
        payload = self._payload()
        jt_phone_paths = {
            "/api/video/jt-live", "/api/video/jt-stop",
            "/api/video/jt-query", "/api/video/jt-files",
            "/api/video/jt-playback", "/api/video/jt-playback-control",
        }
        if path in jt_phone_paths:
            phone = str(payload.get("phone") or "").strip()
            if phone and not self._valid_phone(phone):
                self._json({"ok": False, "error": "phone noto'g'ri"}, 400)
                return
            if phone and not self._phone_allowed(phone):
                self._json({"error": "Qurilma topilmadi"}, 404)
                return
        try:
            if path == "/api/gps/devices":
                imei = str(payload.get("imei") or "").strip()
                if not imei:
                    self._json({"ok": False, "error": "imei kerak"}, 400)
                    return
                kind = _device_kind(payload, imei)
                if not kind:
                    self._json(
                        {"ok": False,
                         "error": "kind noto'g'ri "
                                  "(teltonika/camera/counter)"}, 400)
                    return
                try:
                    storage.register_device(imei, kind=kind)
                except TypeError:
                    storage.register_device(imei)
                storage.bind_device(
                    imei,
                    str(payload.get("vehicle_id") or "").strip(),
                    str(payload.get("name") or "").strip(),
                )
                self._json({"ok": True, "imei": imei, "kind": kind})
            elif path == "/api/gps/counters":
                imei = str(payload.get("imei") or "").strip()
                if not imei:
                    self._json({"ok": False, "error": "imei kerak"}, 400)
                    return
                try:
                    in_c = int(payload.get("in_count") or 0)
                    out_c = int(payload.get("out_count") or 0)
                except (TypeError, ValueError):
                    self._json(
                        {"ok": False,
                         "error": "in_count/out_count son bo'lishi kerak"},
                        400)
                    return
                vid = str(payload.get("vehicle_id") or "").strip()
                if vid:
                    storage.register_device(imei, kind="counter")
                    storage.bind_device(
                        imei, vid,
                        str(payload.get("name") or "").strip())
                storage.update_counter_counts(imei, in_c, out_c)
                self._json({"ok": True, "imei": imei,
                            "in_count": in_c, "out_count": out_c})
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
                if src_type == "rtsp" and cid:
                    try:
                        relay.ensure_for_camera({
                            "id": cid, "name": name, "url": url,
                            "source_type": "rtsp", "enabled": 1,
                        })
                    except Exception:  # noqa: BLE001
                        log.exception("relay darhol ishga tushmadi (kamera %s)", cid)
                storage.update_camera_health(cid, success=True)
                log.info("Kamera qo'shildi: id=%s, name=%s, source_type=%s, "
                         "vehicle_id=%s", cid, name, src_type,
                         payload.get("vehicle_id"))
                self._json({"ok": True, "id": cid,
                            "health": storage.get_camera_health(cid)})
            elif path == "/api/gps/daily/send":
                # Kunlik hisobotni Telegram'ga qo'lda yuborish (admin).
                raw_day = str(payload.get("day") or "").strip()
                day = None
                if raw_day:
                    try:
                        day = datetime.strptime(raw_day, "%Y-%m-%d").date()
                    except ValueError:
                        self._json({"ok": False, "error": "sana noto'g'ri"}, 400)
                        return
                from . import report as gps_report

                ok = bool(gps_report.send_daily_report(day))
                self._json({"ok": ok})
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
            elif path == "/api/gps/route-paths":
                route_id = str(payload.get("route_id") or "").strip()
                if not route_id:
                    self._json({"ok": False, "error": "route_id kerak"}, 400)
                    return
                tol = int(payload.get("tolerance_m")
                          or config.ROUTE_DEVIATION_TOLERANCE_M)
                name = str(payload.get("name") or "").strip()
                if not name:
                    name = storage.route_names([route_id]).get(route_id, "")
                points = payload.get("points")
                if not points or payload.get("learn"):
                    points = route_alert.learn_route_path(
                        route_id,
                        date_from=str(payload.get("date_from") or "").strip(),
                        date_to=str(payload.get("date_to") or "").strip(),
                        imei=str(payload.get("imei") or "").strip(),
                        simplify_m=payload.get("simplify_m"),
                    )
                if not points or len(points) < 2:
                    self._json({"ok": False,
                                "error": "yo'lak nuqtalari topilmadi"}, 400)
                    return
                storage.upsert_route_path(
                    route_id=route_id, points=points, name=name,
                    tolerance_m=tol)
                self._json({"ok": True, "route_id": route_id,
                            "points": len(points), "name": name,
                            "tolerance_m": tol})
            elif path == "/api/video/jt-live":
                # 4G kameraga 0x9101: videoni shu serverga yuborishni so'rash.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                host = _resolve_media_host(str(payload.get("host") or "").strip())
                if not host:
                    self._json({"ok": False,
                                "error": "media host belgilanmagan: "
                                         ".env ga JT1078_MEDIA_HOST=bmbrutto.uz "
                                         "qo'shing"}, 400)
                    return
                try:
                    channel = int(payload.get("channel") or 1)
                    stream_type = int(payload.get("stream_type") or 0)
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "channel/stream_type noto'g'ri"}, 400)
                    return
                if not 1 <= channel <= 255 or stream_type not in (0, 1):
                    self._json({"ok": False, "error": "channel/stream_type noto'g'ri"}, 400)
                    return
                jt1078.allow_stream(phone, channel, "live")
                # Oqim jimgina qotib qolsa watchdog shu parametrlar bilan
                # 0x9101 ni qayta yuboradi (I-kadr bilan qayta boshlanadi).
                jt1078.remember_live_request(
                    phone, channel, host, config.JT1078_TCP_PORT, stream_type)
                ok = jt808.send_live_request(
                    phone, host, config.JT1078_TCP_PORT, channel, stream_type)
                self._log_jt_video(phone, channel, "live_start")
                self._json({"ok": bool(ok), "phone": phone, "channel": channel,
                            "host": host, "port": config.JT1078_TCP_PORT,
                            "sent": bool(ok)})
            elif path == "/api/video/jt-stop":
                # 0x9102: jonli oqimni to'xtatish (4G trafikni tejash).
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                try:
                    channel = int(payload.get("channel") or 1)
                except (TypeError, ValueError):
                    channel = 0
                if channel in (0, -1):
                    for c in range(1, 9):
                        jt808.send_live_stop(phone, c)
                        jt1078.forget_live_request(phone, c)
                        jt1078.close_stream(phone, c, "live")
                        jt1078.close_stream(phone, c, "playback")
                        jt1078.clear_playback(phone, c)
                    self._json({"ok": True, "phone": phone, "channel": "all", "closed": True})
                    return
                if not 1 <= channel <= 255:
                    self._json({"ok": False, "error": "channel noto'g'ri"}, 400)
                    return
                ok = jt808.send_live_stop(phone, channel)
                jt1078.forget_live_request(phone, channel)
                closed = jt1078.close_stream(phone, channel, "live")
                jt1078.close_stream(phone, channel, "playback")
                jt1078.clear_playback(phone, channel)
                self._log_jt_video(phone, channel, "live_stop")
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel, "closed": bool(closed)})
            elif path == "/api/video/jt-query":
                # Qurilma imkoniyatlarini so'rash: params|attrs|av|specific.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                what = str(payload.get("what") or "attrs").strip().lower()
                if what == "params":
                    ok = jt808.send_query_params(phone)
                elif what == "av":
                    ok = jt808.send_query_av_attrs(phone)
                elif what == "specific":
                    ids = payload.get("ids") or []
                    ok = jt808.send_query_specific(phone, ids)
                else:
                    ok = jt808.send_query_attrs(phone)
                self._json({"ok": bool(ok), "phone": phone, "what": what,
                            "sent": bool(ok)})
            elif path == "/api/video/jt-files":
                # 0x9205: qurilma xotirasidagi yozuvlar ro'yxatini so'rash.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                try:
                    channel = int(payload.get("channel") or 0)
                except (TypeError, ValueError):
                    channel = -1
                date = str(payload.get("date") or "").strip()
                raw_start = str(payload.get("start") or "").strip()
                raw_end = str(payload.get("end") or "").strip()
                start_text = raw_start or (f"{date} 00:00:00" if date else "")
                end_text = raw_end or (f"{date} 23:59:59" if date else "")
                start_dt = self._media_datetime(start_text)
                end_dt = self._media_datetime(end_text)
                if not 0 <= channel <= 255 or not start_dt or not end_dt \
                        or start_dt > end_dt:
                    self._json({"ok": False,
                                "error": "channel yoki sana oralig'i noto'g'ri"}, 400)
                    return
                start = jt808.iso_to_bcd6(start_text)
                end = jt808.iso_to_bcd6(end_text)
                ok = jt808.send_resource_list(
                    phone, channel, start, end,
                    request_start=start_text, request_end=end_text)
                self._log_jt_video(phone, channel, "archive_list")
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel, "sent": bool(ok),
                            "request": {"channel": channel,
                                        "start": start_text,
                                        "end": end_text}})
            elif path == "/api/video/jt-playback":
                # 0x9201: yozuvni qurilma xotirasidan oqim sifatida so'rash.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                host = _resolve_media_host(str(payload.get("host") or "").strip())
                if not host:
                    self._json({"ok": False,
                                "error": "media host belgilanmagan: "
                                         ".env ga JT1078_MEDIA_HOST=bmbrutto.uz "
                                         "qo'shing"}, 400)
                    return
                start_text = str(payload.get("start") or "").strip()
                end_text = str(payload.get("end") or "").strip()
                start_dt = self._media_datetime(start_text)
                end_dt = self._media_datetime(end_text)
                try:
                    channel = int(payload.get("channel") or 1)
                    media_type = int(payload.get("media") or 0)
                    raw_st = payload.get("stream")
                    if raw_st is None:
                        raw_st = payload.get("stream_type")
                    stream_type = int(raw_st if raw_st is not None else 0)
                    memory_type = int(payload.get("memory") or 0)
                    method = int(payload.get("method") or 0)
                    multiples = int(payload.get("multiples") or 0)
                except (TypeError, ValueError):
                    channel = 0
                    media_type = stream_type = memory_type = method = multiples = -1
                valid_enums = (
                    media_type in (0, 1, 2)
                    and stream_type in (0, 1)
                    and memory_type in (0, 1)
                    and 0 <= method <= 4
                    and 0 <= multiples <= 255
                )
                if not 1 <= channel <= 255 or not start_dt or not end_dt \
                        or start_dt > end_dt or not valid_enums:
                    self._json({"ok": False,
                                "error": "playback parametrlari noto'g'ri"}, 400)
                    return
                start = jt808.iso_to_bcd6(start_text)
                end = jt808.iso_to_bcd6(end_text)
                # Yangi playback so'rashdan oldin, eski oqim va GOP buferini tozalaymiz
                jt1078.close_stream(phone, channel, "playback")
                jt1078.clear_playback(phone, channel)
                ok = jt808.send_playback(
                    phone, host, config.JT1078_TCP_PORT, start, end, channel,
                    media_type=media_type, stream_type=stream_type,
                    memory_type=memory_type, method=method,
                    multiples=multiples)
                if ok:
                    jt1078.set_playback(phone, channel)
                self._log_jt_video(phone, channel, "archive_play")
                # Pleyer manzillarini ham jt-streams bilan BIR xil beramiz:
                # pilot (ZLMediaKit) yoqilganda mahalliy /hls/jtpb_* fayllari
                # YO'Q — eski qattiq kodlangan manzil 404 berardi.
                pb_name = config.zlmediakit_stream_name("jtpb_", phone, channel)
                zl_on = config.zlmediakit_enabled()
                self._json({
                    "ok": bool(ok), "phone": phone, "channel": channel,
                    "hls": (config.zlmediakit_http_url(pb_name, "hls")
                            if (zl_on and config.ZLMEDIAKIT_HTTP)
                            else f"/hls/jtpb_{phone}_{channel}.m3u8"),
                    "flv": (config.zlmediakit_http_url(pb_name, "flv")
                            if zl_on else None),
                    "start": str(payload.get("start") or ""),
                    "end": str(payload.get("end") or ""),
                })
            elif path == "/api/video/jt-playback-control":
                # 0x9202: yozuv ijrosini boshqarish (pauza/davom/to'xtat/…).
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                try:
                    channel = int(payload.get("channel") or 1)
                    control = int(payload.get("control") or 0)
                    speed = int(payload.get("speed") or 0)
                except (TypeError, ValueError):
                    channel = control = speed = -1
                raw_time = str(payload.get("time") or "").strip()
                time_dt = self._media_datetime(raw_time) if raw_time else None
                if not 1 <= channel <= 255 or not 0 <= control <= 6 \
                        or not 0 <= speed <= 255 \
                        or (raw_time and not time_dt) \
                        or (control == jt808.PB_CTRL_DRAG and not time_dt):
                    self._json({"ok": False,
                                "error": "playback boshqaruvi noto'g'ri"}, 400)
                    return
                time_bcd = jt808.iso_to_bcd6(raw_time) if raw_time else None
                ok = jt808.send_playback_control(
                    phone, channel, control, speed, time_bcd)
                self._log_jt_video(phone, channel, f"archive_control_{control}")
                if control == jt808.PB_CTRL_STOP:
                    # ILDIZI TUZATISH: avval playback oynasi faqat 120s idle
                    # reaper'ni kutardi — "To'xtatish"dan keyin jtpb oqimi tirik
                    # qolardi va Xotira oynasini qayta ochganda ESLAGAN yozuv
                    # oqimi darhol "ko'rinishib" playback buzilgandek bo'lardi.
                    closed_pb = jt1078.close_stream(phone, channel, "playback")
                    jt1078.clear_playback(phone, channel)
                    # Qurilma 0x9202'ni tinglamasa media oqimi davom etadi;
                    # clear_playback'dan keyingi kadrlar "jonli"ga yorilib
                    # jtpb_... oqimini jonli karta ostiga qayta ochardi —
                    # shu sabli live muxer'ni ham yopamiz (oyna ichida).
                    closed_live = jt1078.close_stream(phone, channel, "live")
                    jt1078.forget_live_request(phone, channel)
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel, "control": control,
                            "speed": speed,
                            "closed": bool(closed_pb) if control == jt808.PB_CTRL_STOP else None})
            elif path == "/api/video/relay":
                # On-demand: UI "Ochish" bosganda RTSP relay'ni ko'taradi.
                try:
                    cam_id = int(payload.get("id"))
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "id kerak"}, 400)
                    return
                if not self._camera_allowed(cam_id):
                    self._json({"ok": False, "error": "kamera topilmadi"}, 404)
                    return
                cam = relay.resolve_camera(cam_id)
                if not cam:
                    self._json({"ok": False, "error": "kamera topilmadi"}, 404)
                    return
                relay.note_viewer(cam_id)
                ok = relay.ensure_for_camera(cam)
                self._log_camera_video(cam_id, "live_start")
                self._json({"ok": bool(ok), "id": cam_id,
                            "hls": relay.public_url(cam_id),
                            "limit": config.HLS_RELAY_MAX})
            elif path == "/api/video/relay-stop":
                try:
                    cam_id = int(payload.get("id"))
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "id kerak"}, 400)
                    return
                if not self._camera_allowed(cam_id):
                    self._json({"ok": False, "error": "kamera topilmadi"}, 404)
                    return
                relay.stop_for_camera(cam_id)
                self._log_camera_video(cam_id, "live_stop")
                self._json({"ok": True, "id": cam_id})
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
        if not self._require_admin():
            return
        path = urlparse(self.path).path.rstrip("/")
        parts = [p for p in path.split("/") if p]  # ["api","gps","devices",imei]
        try:
            payload = self._payload()
            if len(parts) == 4 and parts[:3] == ["api", "gps", "devices"]:
                if "enabled" in payload:
                    storage.set_device_enabled(parts[3], bool(payload["enabled"]))
                if "vehicle_id" in payload or "name" in payload:
                    storage.bind_device(
                        parts[3],
                        str(payload.get("vehicle_id") or "").strip(),
                        str(payload.get("name") or "").strip(),
                    )
                if payload.get("kind"):
                    k = _device_kind(payload, parts[3])
                    if not k:
                        self._json(
                            {"ok": False,
                             "error": "kind noto'g'ri "
                                      "(teltonika/camera/counter)"}, 400)
                        return
                    storage.set_device_kind(parts[3], k)
                self._json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "gps", "geofences"]:
                fid = int(parts[3])
                if "enabled" in payload:
                    storage.set_geofence_enabled(fid, bool(payload["enabled"]))
                # tahrirlash: nom / koordinata / radius / avtobus
                if any(k in payload for k in ("name", "lat", "lng", "radius_m")):
                    cur = {f["id"]: f for f in storage.list_geofences()}.get(fid)
                    if not cur:
                        self._json({"error": "zona topilmadi"}, 404)
                        return
                    try:
                        lat = float(payload.get("lat", cur.get("lat")))
                        lng = float(payload.get("lng", cur.get("lng")))
                    except (TypeError, ValueError):
                        self._json({"ok": False, "error": "lat/lng noto'g'ri"}, 400)
                        return
                    storage.update_geofence(
                        fid,
                        name=str(payload.get("name") or cur.get("name") or "").strip(),
                        lat=lat, lng=lng,
                        radius_m=int(payload.get("radius_m") or cur.get("radius_m") or 500),
                        vehicle_id=str(payload.get("vehicle_id", cur.get("vehicle_id")) or "").strip(),
                    )
                self._json({"ok": True})
                return
            if (len(parts) == 4 and parts[:3] == ["api", "gps", "route-paths"]
                    and "enabled" in payload):
                storage.set_route_path_enabled(
                    parts[3], bool(payload["enabled"]))
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
        if not self._require_admin():
            return
        path = urlparse(self.path).path.rstrip("/")
        parts = [p for p in path.split("/") if p]  # ["api","video","cameras","3"]
        try:
            if len(parts) == 4 and parts[:3] == ["api", "video", "cameras"]:
                relay.stop_for_camera(int(parts[3]))
                storage.delete_camera(int(parts[3]))
                self._json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "gps", "devices"]:
                storage.delete_device(parts[3])
                self._json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "gps", "geofences"]:
                storage.delete_geofence(int(parts[3]))
                self._json({"ok": True})
                return
            if len(parts) == 4 and parts[:3] == ["api", "gps", "route-paths"]:
                storage.delete_route_path(parts[3])
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
