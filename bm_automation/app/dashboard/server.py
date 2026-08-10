"""Web dashboard — stdlib HTTP server (qo'shimcha bog'liqliksiz).

Ishga tushirish:
    python -m bm_automation dashboard --port 8080

Yo'nalishlar (JSON API):
    GET  /                 -> SPA (web/index.html)
    GET  /api/summary      -> to'liq dashboard xulosasi (filterlar bilan)
    GET  /api/routes       -> filter dropdown uchun yo'nalishlar
    GET  /api/health       -> tizim holati (BM API, DB, Telegram, Scheduler)
    POST /api/sync         -> BM API'dan ma'lumotni DB'ga sync (best-effort)
    GET  /api/export       -> CSV/Excel/PDF eksport

Auto-refresh brauzer tomonida (JS setInterval) amalga oshiriladi.
"""

from __future__ import annotations

import json
import socket
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..utils.logger import get_logger

log = get_logger("bm_automation.dashboard")

WEB_DIR = Path(__file__).parent / "web"
INDEX_FILE = WEB_DIR / "index.html"

_STARTED = time.time()


def _parse(qs: dict) -> dict:
    return {k: v[0] for k, v in qs.items()}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "BM-Dashboard/1.0"

    # ---------------------------------------------------------------- helpers

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str, code: int = 200) -> None:
        if not path.exists():
            self._json({"error": "fayl topilmadi"}, 404)
            return
        body = path.read_bytes()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _export(self, qs: dict) -> None:
        from . import export as ex

        p = _parse(qs)
        fmt = (p.get("format") or "csv").lower()
        scope = (p.get("scope") or "trips").lower()
        try:
            filters = {k: p[k] for k in ("date", "from", "to", "route",
                                         "vehicle", "driver", "status") if p.get(k)}
            data = ex.build_export(filters, fmt, scope)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 400)
            return
        fname = ex.filename(fmt, scope, p.get("date", ""))
        self.send_response(200)
        self.send_header("Content-Type", ex.content_type(fmt))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{fname}"')
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------- HTTP

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._file(INDEX_FILE, "text/html; charset=utf-8")
            elif path == "/api/summary":
                from . import metrics as m
                self._json(m.summary(_parse(qs)))
            elif path == "/api/routes":
                from . import metrics as m
                self._json({"ok": True, "routes": m.route_options()})
            elif path == "/api/health":
                from . import metrics as m
                sys = m.Metrics().system()
                self._json({"ok": True, "uptime_s": int(time.time() - _STARTED),
                            "system": sys})
            elif path == "/api/export":
                self._export(qs)
            else:
                self._json({"error": f"yo'nalish topilmadi: {path}"}, 404)
        except Exception as exc:  # noqa: BLE001 - server yiqilmaydi
            log.exception("dashboard so'rov xatosi: %s", self.path)
            self._json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path != "/api/sync":
            self._json({"error": "yo'nalish topilmadi"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw or b"{}")
            result = self._run_sync(payload)
            self._json(result)
        except Exception as exc:  # noqa: BLE001
            log.exception("sync xatosi")
            self._json({"ok": False, "error": str(exc)}, 500)

    def _run_sync(self, payload: dict) -> dict:
        """BM API -> DB sync (best-effort). Token eskirgan bo'lsa tez xato."""
        from datetime import date

        from ..api.client import BMClient
        from ..db.storage import get_storage
        from ..db import sync as db_sync

        storage = get_storage()
        if not storage.enabled:
            return {"ok": False, "error": "DB rejimi o'chirilgan"}

        client = BMClient()
        client.auto_relogin = False  # OneID login sikliga tushmaslik
        client.login()

        route = str(payload.get("route") or "").strip()
        date_str = str(payload.get("date") or date.today().isoformat())

        if route:
            results = [r.as_dict() for r in db_sync.sync_route_day(
                storage, client, route, date_str)]
            results.append(db_sync.sync_waybills(
                storage, client, route, date_str, date_str).as_dict())
        else:
            # route berilmagan — barcha profillar (kompaniyalar) sync qilinadi
            results = [r.as_dict() for r in db_sync.sync_all_profiles(
                storage, client, date_str)]
        results.append(db_sync.sync_routes(storage, client).as_dict())
        ok = not any(r.get("error") for r in results)
        return {"ok": ok, "route": route, "date": date_str, "results": results}

    # ------------------------------------------------------------------ log

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("dash %s | %s", self.address_string(), fmt % args)


def run(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True) -> int:
    """Server'ni ishga tushiradi (bloklanadi)."""
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    httpd.daemon_threads = True
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"Dashboard: {url}")
    print("To'xtatish: Ctrl+C")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]
