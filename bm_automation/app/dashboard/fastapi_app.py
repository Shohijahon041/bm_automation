"""FastAPI application factory — BM Dashboard.

Ishga tushirish:
    uvicorn bm_automation.app.dashboard.fastapi_app:app --host 0.0.0.0 --port 8080

Yoki CLI orqali:
    python -m bm_automation dashboard --port 8080
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..utils.logger import get_logger
from .deps import ensure_bootstrap_admin, _load_sessions, _SESSION_LOCK, _dashboard_token, request_is_https

log = get_logger("bm_automation.dashboard")

WEB_DIR = Path(__file__).parent / "web"
INDEX_FILE = WEB_DIR / "index.html"

_STARTED = time.time()


def _prewarm() -> None:
    try:
        from ..db.storage import get_storage
        get_storage().enabled
        from . import metrics as m
        m.summary()
        m.Metrics().system()
    except Exception:
        log.exception("dastlabki tayyorgarlik xatosi")
    try:
        from ..db.backup import start_auto_backup, backup_enabled
        if backup_enabled():
            start_auto_backup()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    with _SESSION_LOCK:
        _load_sessions()
    ensure_bootstrap_admin()

    try:
        from ..gps import autostart as _gps_autostart
        _gps_autostart.start_background()
    except Exception:
        log.exception("GPS autostart hook xatosi (dashboard davom etadi)")

    import threading
    threading.Thread(target=_prewarm, daemon=True).start()

    log.info("FastAPI dashboard ishga tushdi")
    yield
    log.info("FastAPI dashboard to'xtatildi")


def create_app() -> FastAPI:
    app = FastAPI(
        title="BM Dashboard",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    _cors_origins = [
        o.strip() for o in os.environ.get("DASHBOARD_CORS_ORIGINS", "").split(",")
        if o.strip()
    ] or ["http://localhost:8080", "http://127.0.0.1:8080"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
        max_age=3600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.middleware("http")
    async def post_rate_limit(request: Request, call_next):
        from .deps import rate_limit_post
        if request.method == "POST" and request.url.path.startswith("/api/"):
            if request.url.path not in ("/api/login", "/api/logout"):
                if not rate_limit_post(request.url.path):
                    return JSONResponse(
                        {"error": "Juda ko'p so'rov — bir oz kuting"}, status_code=429)
        return await call_next(request)

    @app.middleware("http")
    async def tenant_middleware(request: Request, call_next):
        """Kompaniya holati nazorati (bloklangan/muddati o'tgan tenant → 403)."""
        from .deps import tenant_guard
        denied = tenant_guard(request)
        if denied is not None:
            return denied
        return await call_next(request)

    # --- Routers ---
    from .routers import auth, data, drivers, admin, system
    app.include_router(auth.router)
    app.include_router(data.router)
    app.include_router(drivers.router)
    app.include_router(admin.router)
    app.include_router(system.router)

    # --- Static files (SPA) ---
    @app.get("/", include_in_schema=False)
    async def serve_index(request: Request):
        if not INDEX_FILE.exists():
            return JSONResponse({"error": "fayl topilmadi"}, status_code=404)
        body = INDEX_FILE.read_bytes()
        dt = _dashboard_token()
        client = request.client
        is_local = client and (
            (client.host or "").lower().rstrip(".") in ("127.0.0.1", "::1", "localhost")
            or (client.host or "").startswith("127.")
        )
        resp = HTMLResponse(body)
        if dt and is_local:
            # Token faqat cookie'da (httponly), HTML body'ga emas — XSS/leak oldini olish.
            resp.set_cookie("bm_token", dt, path="/", httponly=True,
                            samesite="lax", secure=request_is_https(request))
        return resp

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")

    return app


app = create_app()
