"""GPS HTTP API — frontend uchun (port 8081).

Stdlib ThreadingHTTPServer, DASHBOARD_TOKEN (Bearer) bilan himoyalangan,
JSON javoblar. (Asosiy dashboard FastAPI'ga o'tgan; bu servis mustaqil.)
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pathlib import Path

from . import (compare, config, jt1078, jt808, listener, relay, route_alert,
               stops, storage)

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
    """
    if not config.GPS_API_TOKEN:
        return True
    want = config.GPS_API_TOKEN
    auth = (handler.headers.get("Authorization") or "").strip()
    if auth[:7].lower() == "bearer " and auth[7:].strip() == want:
        return True
    for part in (handler.headers.get("Cookie") or "").split(";"):
        kv = part.strip().split("=", 1)
        if len(kv) == 2 and kv[0].strip() == "bm_token" and kv[1].strip() == want:
            return True
    if qs:
        qtok = (qs.get("token") or [""])[0].strip()
        if qtok and qtok == want:
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


class GpsHandler(BaseHTTPRequestHandler):
    server_version = "BM-GPS/1.0"

    def log_message(self, fmt, *args):  # noqa: A003
        log.debug("gps-api: %s", fmt % args)

    # ------------------------------------------------------------- helpers

    def _json(self, obj, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        # Dashboard (:8080) sessiya cookie'si bilan to'g'ridan-to'g'ri
        # ulanishi uchun CORS (ko'p korxonali qamrov: bm_session cookie
        # token'dan ustun turadi → Origin'ni aks ettiramiz).
        origin = self.headers.get("Origin") or "*"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
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
        origin = self.headers.get("Origin") or "*"
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
                for d in storage.list_devices()
                if str(d.get("vehicle_id") or "") in allowed}

    def _camera_allowed(self, cam_id: int) -> bool:
        """Kamera (hls oqimi ham) qamrovga tegishli bo'lsa True."""
        allowed = self._company_vehicles()
        if allowed is None:
            return True
        for cam in storage.list_cameras():
            if str(cam.get("id") or "") == str(cam_id):
                return str(cam.get("vehicle_id") or "") in allowed
        return False

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
                "jt808_enabled": config.JT808_ENABLED,
                "jt808": jt808.stats() if config.JT808_ENABLED else None,
                "jt1078": jt1078.stats() if config.JT808_ENABLED else None,
                "jt1078_streams": (jt1078.streams()
                                   if config.JT808_ENABLED else []),
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
            if path == "/api/gps/devices":
                devices = self._only_vehicles(storage.list_devices(), vehicles)
                route_q = (qs.get("route") or [""])[0].strip()
                if route_q:
                    devices = [d for d in devices
                               if str(d.get("route_id") or "") == route_q]
                self._json({"ok": True, "count": len(devices), "devices": devices})
            elif path == "/api/gps/routes":
                # GPS qurilmasi bog'langan avtobuslar bo'yicha yo'nalishlar
                # (qamrov allaqachon qurilmalarga qo'llangan).
                devs = self._only_vehicles(storage.list_devices(), vehicles)
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
                self._json({"ok": True, "count": len(cams), "cameras": cams})
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
                    "relays": relays,
                })
            elif path == "/api/video/jt-streams":
                # JT1078 (CMSV6/4G) avtomatik aniqlangan jonli/yozuv oqimlari.
                streams = jt1078.streams() if config.JT808_ENABLED else []
                phones = jt808.connected_phones() if config.JT808_ENABLED else []
                self._json({
                    "ok": True,
                    "enabled": config.JT808_ENABLED,
                    "ffmpeg": jt1078.ffmpeg_available(),
                    "media_host": config.JT1078_MEDIA_HOST,
                    "devices": phones,
                    "count": len(streams),
                    "streams": streams,
                })
            elif path == "/api/video/jt-files":
                # Qurilma xotirasidagi yozuvlar ro'yxati (oxirgi 0x1205 javobi).
                phone = (qs.get("phone") or [""])[0].strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                res = jt808.resources(phone)
                self._json({"ok": True, "phone": phone,
                            "at": res["at"], "items": res["items"]})
            elif path == "/api/gps/geofences":
                fences = self._only_vehicles(storage.list_geofences(), vehicles)
                self._json({"ok": True, "count": len(fences), "geofences": fences})
            elif path == "/api/gps/route-paths":
                paths = storage.list_route_paths()
                if vehicles is not None:
                    devs = self._only_vehicles(storage.list_devices(), vehicles)
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
                dev_scope = self._only_vehicles(storage.list_devices(),
                                                vehicles)
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
                m = re.match(r"^cam(\d+)[.\-]", fname)
                if m and not self._camera_allowed(int(m.group(1))):
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
        if not self._require_admin():
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
                host = (str(payload.get("host") or "").strip()
                        or config.JT1078_MEDIA_HOST)
                if not host:
                    self._json({"ok": False,
                                "error": "media host belgilanmagan "
                                         "(JT1078_MEDIA_HOST)"}, 400)
                    return
                channel = int(payload.get("channel") or 1)
                stream_type = int(payload.get("stream_type") or 0)
                ok = jt808.send_live_request(
                    phone, host, config.JT1078_TCP_PORT, channel, stream_type)
                self._json({"ok": bool(ok), "phone": phone, "channel": channel,
                            "host": host, "port": config.JT1078_TCP_PORT,
                            "sent": bool(ok)})
            elif path == "/api/video/jt-stop":
                # 0x9102: jonli oqimni to'xtatish (4G trafikni tejash).
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                channel = int(payload.get("channel") or 1)
                ok = jt808.send_live_stop(phone, channel)
                jt1078.clear_playback(phone, channel)
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel})
            elif path == "/api/video/jt-files":
                # 0x9205: qurilma xotirasidagi yozuvlar ro'yxatini so'rash.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                channel = int(payload.get("channel") or 1)
                date = str(payload.get("date") or "").strip()
                raw_start = str(payload.get("start") or "").strip()
                raw_end = str(payload.get("end") or "").strip()
                start = jt808.iso_to_bcd6(
                    raw_start or (f"{date} 00:00:00" if date else ""))
                end = jt808.iso_to_bcd6(
                    raw_end or (f"{date} 23:59:59" if date else ""))
                ok = jt808.send_resource_list(phone, channel, start, end)
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel, "sent": bool(ok)})
            elif path == "/api/video/jt-playback":
                # 0x9201: yozuvni qurilma xotirasidan oqim sifatida so'rash.
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                host = (str(payload.get("host") or "").strip()
                        or config.JT1078_MEDIA_HOST)
                if not host:
                    self._json({"ok": False,
                                "error": "media host belgilanmagan "
                                         "(JT1078_MEDIA_HOST)"}, 400)
                    return
                channel = int(payload.get("channel") or 1)
                start = jt808.iso_to_bcd6(str(payload.get("start") or ""))
                end = jt808.iso_to_bcd6(str(payload.get("end") or ""))
                ok = jt808.send_playback(phone, host, config.JT1078_TCP_PORT,
                                         start, end, channel)
                if ok:
                    jt1078.set_playback(phone, channel)
                self._json({
                    "ok": bool(ok), "phone": phone, "channel": channel,
                    "hls": f"/hls/jtpb_{phone}_{channel}.m3u8",
                    "start": str(payload.get("start") or ""),
                    "end": str(payload.get("end") or ""),
                })
            elif path == "/api/video/jt-playback-control":
                # 0x9202: yozuv ijrosini boshqarish (pauza/davom/to'xtat/…).
                phone = str(payload.get("phone") or "").strip()
                if not phone:
                    self._json({"ok": False, "error": "phone kerak"}, 400)
                    return
                channel = int(payload.get("channel") or 1)
                control = int(payload.get("control") or 0)
                speed = int(payload.get("speed") or 0)
                ok = jt808.send_playback_control(phone, channel, control, speed)
                if control == jt808.PB_CTRL_STOP:
                    jt1078.clear_playback(phone, channel)
                self._json({"ok": bool(ok), "phone": phone,
                            "channel": channel, "control": control})
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
