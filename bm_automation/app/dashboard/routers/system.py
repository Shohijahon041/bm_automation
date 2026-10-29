"""System router — health, disk, logs, backup, sync, myai, SSE."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..deps import Scope, force_route, scope_routes

router = APIRouter(tags=["system"])

_STARTED = time.time()


def _parse_qs(request: Request) -> dict:
    return dict(request.query_params)


# ---------------------------------------------------------------------------
# Health / Disk / Logs
# ---------------------------------------------------------------------------

@router.get("/api/health")
def api_health(request: Request, scope: dict = Depends(Scope(2))):
    # Host/disk/xotira tafsilotlari — MANAGER va yuqori (ichki monitoring).
    from .. import metrics as m
    sys_info = m.Metrics().system()
    return {"ok": True, "uptime_s": int(time.time() - _STARTED), "system": sys_info}


@router.get("/api/disk")
def api_disk(request: Request, scope: dict = Depends(Scope())):
    return _disk_usage()


def _log_dir() -> Path:
    return Path(os.getenv("BM_LOG_DIR", "logs"))


def _nginx_log_dir() -> Path:
    return Path(os.getenv("BM_NGINX_LOG_DIR", "/var/log/nginx"))


def _log_file_map() -> dict[str, list[tuple[str, Path]]]:
    """Manba -> [(teg, fayl), ...]. Teg bo'sh bo'lsa satr o'zgarishsiz qoladi."""
    from ...utils.logger import FILE_LOG

    d = _log_dir()
    n = _nginx_log_dir()
    return {
        "bm": [("", FILE_LOG)],
        "bot": [("BOT", d / "bot_service.log"),
                ("BOT-ERR", d / "bot_service.err.log")],
        "dashboard": [("DASH", d / "dashboard_service.log"),
                      ("DASH-ERR", d / "dashboard_service.err.log")],
        "nginx": [("NGX", n / "access.log"),
                  ("NGX-ERR", n / "error.log")],
        "watchdog": [("WD", Path("state") / "watchdog.log")],
        "gps": [("", FILE_LOG)],
    }


LOG_SOURCES = ["bm", "bot", "dashboard", "nginx", "gps", "watchdog",
               "journal", "all"]


def _tail_lines(path: Path, n: int) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []
    read_n = min(size, max(n * 200 + 8192, 65536))
    try:
        with open(path, "rb") as f:
            f.seek(size - read_n)
            raw = f.read(read_n)
        return raw.decode("utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _journal_lines(n: int) -> list[str]:
    import subprocess
    try:
        r = subprocess.run(
            ["journalctl", "-n", str(n), "--no-pager", "-o", "short-iso",
             "-u", "bm-bot", "-u", "bm-dashboard"],
            capture_output=True, text=True, timeout=10)
        return r.stdout.splitlines()[-n:]
    except Exception:  # noqa: BLE001
        return []


def _apply_filters(lines: list[str], level_filter: str,
                   search: str) -> list[str]:
    if level_filter:
        lines = [l for l in lines if f"| {level_filter}" in l]
    if search:
        sl = search.lower()
        lines = [l for l in lines if sl in l.lower()]
    return lines


def _files_for(source: str) -> list[tuple[str, Path]]:
    """Manba uchun fayllar ro'yxati ('journal' bundan mustasno)."""
    if source == "all":
        m = _log_file_map()
        return [("", m["bm"][0][1]), m["bot"][0], m["bot"][1],
                m["dashboard"][0], m["dashboard"][1],
                ("NGX-ERR", _nginx_log_dir() / "error.log")]
    return _log_file_map().get(source) or _log_file_map()["bm"]


def _read_tagged(source: str, n: int) -> list[str]:
    """Manba bo'yicha loglarni o'qiydi; ko'p fayl bo'lsa 'TEG | satr' ko'rinishida."""
    if source == "journal":
        return _journal_lines(n)
    files = _files_for(source)
    per = max(n // max(len(files), 1), 20)
    out: list[str] = []
    multi = len(files) > 1
    for tag, path in files:
        for line in _tail_lines(path, per):
            out.append(f"[{tag}] {line}" if (tag and multi) else line)
    if source == "gps":
        out = [l for l in out if ".gps" in l]
    return out


@router.get("/api/logs")
def api_logs(request: Request, scope: dict = Depends(Scope())):
    params = _parse_qs(request)
    source = (params.get("source") or "bm").strip().lower()
    if source not in LOG_SOURCES:
        source = "bm"
    try:
        lines = int(params.get("lines", "200"))
    except (TypeError, ValueError):
        lines = 200
    lines = max(1, min(lines, 5000))
    level_filter = params.get("level", "").upper()
    search = params.get("q", "")

    all_lines = _apply_filters(_read_tagged(source, lines), level_filter, search)
    return {"ok": True, "lines": all_lines[-lines:], "total": len(all_lines),
            "source": source, "sources": LOG_SOURCES}


@router.get("/api/logs/stream")
async def api_logs_stream(request: Request, scope: dict = Depends(Scope())):
    """SSE — log fayllarini real vaqtda (tail -f) oqim sifatida yuboradi."""
    import asyncio

    params = _parse_qs(request)
    source = (params.get("source") or "bm").strip().lower()
    if source not in LOG_SOURCES:
        source = "bm"
    try:
        backfill = int(params.get("lines", "200"))
    except (TypeError, ValueError):
        backfill = 200
    backfill = max(0, min(backfill, 1000))
    level_filter = params.get("level", "").upper()
    search = params.get("q", "")

    def _prefix(tag: str, line: str, multi: bool) -> str:
        return f"[{tag}] {line}" if (tag and multi) else line

    async def gen():
        initial = _read_tagged(source, backfill) if backfill else []
        initial = _apply_filters(initial, level_filter, search)
        yield "event: init\ndata: " + json.dumps({"lines": initial}) + "\n\n"

        if source == "journal":
            seen = len(_journal_lines(200))
            while True:
                if await request.is_disconnected():
                    return
                await asyncio.sleep(3)
                cur = _journal_lines(200)
                if len(cur) > seen:
                    new = _apply_filters(cur[seen:], level_filter, search)
                    seen = len(cur)
                    if new:
                        yield "data: " + json.dumps({"lines": new}) + "\n\n"
            return

        files = _files_for(source)
        multi = len(files) > 1
        handles: dict[str, list] = {}
        try:
            for tag, path in files:
                try:
                    fh = open(path, "rb")
                    fh.seek(0, 2)
                    handles[str(path)] = [tag, path, fh]
                except OSError:
                    continue
            idle = 0
            while True:
                if await request.is_disconnected():
                    return
                new: list[str] = []
                for key, entry in list(handles.items()):
                    tag, path, fh = entry
                    try:
                        if path.stat().st_size < fh.tell():
                            fh.close()
                            fh = open(path, "rb")
                            entry[2] = fh
                    except OSError:
                        continue
                    try:
                        data = fh.read()
                    except Exception:  # noqa: BLE001
                        data = b""
                    if not data:
                        continue
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        new.append(_prefix(tag, line, multi))
                if source == "gps":
                    new = [l for l in new if ".gps" in l]
                new = _apply_filters(new, level_filter, search)
                if new:
                    idle = 0
                    yield "data: " + json.dumps({"lines": new}) + "\n\n"
                else:
                    idle += 1
                    if idle % 25 == 0:
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.6)
        finally:
            for entry in handles.values():
                try:
                    entry[2].close()
                except Exception:  # noqa: BLE001
                    pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store",
                 "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


@router.get("/api/insights")
def api_insights(request: Request, scope: dict = Depends(Scope())):
    from ...notifications.ops import self_review, openrouter
    params = _parse_qs(request)
    days = params.get("days")
    a = self_review.analyze(days=int(days) if days else None)
    llm_text = None
    ai_stats = None
    if openrouter.configured():
        try:
            digest = self_review._digest(a)
            llm_text = self_review._llm_recs(digest)
            ai_stats = openrouter.last_stats()
        except Exception:
            ai_stats = openrouter.last_stats()
    a["recommendations"] = (
        [llm_text] if llm_text else self_review._rule_recs(a))
    return {"ok": True, "enabled": self_review.enabled(),
            "ai_configured": openrouter.configured(),
            "ai_model": openrouter.model(),
            "ai_stats": ai_stats or {},
            "insights": a}


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

@router.get("/api/backup/status")
async def backup_status(request: Request, scope: dict = Depends(Scope())):
    from ...db.backup import get_backup_state
    from ...config.settings import backup_enabled, backup_db_settings
    state = get_backup_state()
    state["enabled"] = backup_enabled()
    if backup_enabled():
        dsn = backup_db_settings().get("dsn", "")
        if "@" in dsn:
            state["host"] = dsn.split("@")[1].split("/")[0]
    return {"ok": True, **state}


@router.post("/api/backup/run")
async def backup_run(request: Request, scope: dict = Depends(Scope())):
    from ...db.backup import run_backup
    params = _parse_qs(request)
    tables = params.get("tables", "")
    table_list = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    return run_backup(tables=table_list)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

_SYNC_MIN_INTERVAL_S = 20.0
_SYNC_LOCK = threading.Lock()
_last_sync_at = 0.0

# Ikki mustaqil fon holati: kunlik (/api/sync) va oylik (/api/sync/monthly).
_DAILY_SYNC_BG: dict = {"running": False, "progress": "", "result": None}
_MONTHLY_SYNC_BG: dict = {"running": False, "progress": "", "result": None}


@router.post("/api/sync")
async def api_sync(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    return _run_sync(payload, scope)


@router.get("/api/sync/status")
def sync_status(request: Request, scope: dict = Depends(Scope())):
    return _sync_bg_status(_DAILY_SYNC_BG, scope)


@router.post("/api/sync/monthly")
async def sync_monthly(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    return _run_sync_monthly(payload, scope)


@router.get("/api/sync/monthly/status")
def sync_monthly_status(request: Request, scope: dict = Depends(Scope())):
    return _sync_bg_status(_MONTHLY_SYNC_BG, scope)


def _fmt_date(ds: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (progress/izoh uchun)."""
    try:
        return date.fromisoformat(ds).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return ds


def _sync_bg_status(st: dict, scope: dict | None = None) -> dict:
    """Fon holatini javobga aylantiradi (running → progress, tugagan → natija).

    Natija (yo'nalishlar, `profiles/<nom>` satrlari, hisoblar) boshqa
    kompaniya ma'lumotini oshkor qilmasligi uchun faqat adminlarga yoki
    sinxronni boshlagan foydalanuvchiga to'liq qaytariladi.
    """
    scope = scope or {}
    owner = str(st.get("owner") or "")
    user = str(scope.get("username") or "")
    full = bool(scope.get("is_admin")) or (bool(owner) and owner == user)
    if st["running"]:
        if full:
            return {"ok": True, "running": True, "progress": st["progress"]}
        return {"ok": True, "running": True, "progress": ""}
    if full and st["result"]:
        return {"ok": True, "running": False, **st["result"]}
    return {"ok": True, "running": False}


def _sync_target_routes(payload: dict, scope: dict) -> tuple[list[str], dict | None]:
    """Sync uchun yo'nalishlar ro'yxatini scope bilan cheklab aniqlaydi.

    `route` vergul bilan birlashtirilgan ham bo'lishi mumkin ("r1,r2" —
    non-admin sessiyasida `force_route` shunday qaytaradi).

    * admin: `route` berilgan bo'lsa — faqat o'shalar; bo'sh bo'lsa — []
      (barcha profillar/kompaniyalar `sync_all_profiles` orqali).
    * non-admin: faqat `scope_routes`dagi yo'nalishlar; ro'yxatdagi har biri
      scope'da bo'lishi shart, bo'sh bo'lsa — barcha ruxsat etilganlari.
      Bo'sh scope — xato (begona ma'lumot sinxronlanmaydi).

    Qaytaradi: (routes, error_dict | None).
    """
    requested = [r.strip() for r in str(payload.get("route") or "").split(",") if r.strip()]
    scopes = scope_routes(scope)
    if scopes is None:
        return requested, None
    if any(r not in scopes for r in requested):
        return [], {"ok": False, "error": "Yo'nalish topilmadi"}
    routes = requested or sorted(scopes)
    if not routes:
        return [], {"ok": False, "error": "Yo'nalishlar aniqlanmadi"}
    return routes, None


def _sync_interval_ok() -> dict | None:
    """Minimal interval tekshiruvi — tez-tez chaqirilsa xato dict qaytaradi."""
    with _SYNC_LOCK:
        if time.monotonic() - _last_sync_at < _SYNC_MIN_INTERVAL_S:
            return {"ok": False, "error": "Sync juda tez-tez chaqirildi — kutib turing"}
    return None


def _sync_busy(error: str = "Sinxronlash davom etmoqda") -> dict | None:
    """Kunlik yoki oylik sync ishlayotgan bo'lsa — band xabari.

    Ikki oqim birga BM API'ga urilmasligi kerak: login/token va DB
    yozuvlari chalkashib ketmasin.
    """
    for st in (_DAILY_SYNC_BG, _MONTHLY_SYNC_BG):
        if st.get("running"):
            return {"ok": False, "error": error,
                    "progress": st.get("progress") or ""}
    return None


def _sync_progress_cb(bg: dict):
    def _cb(day: str, idx: int, total: int) -> None:
        bg["progress"] = f"{_fmt_date(day)} ({idx}/{total})"
    return _cb


def _parse_days(payload: dict, default_from: str = "") -> tuple[list[str] | None, str | None]:
    """Payload'dan sinxronlanadigan kunlar ro'yxatini tayyorlaydi.

    ``from``/``to`` berilsa — oralıq; faqat ``date`` bo'lsa — bitta kun;
    hech biri bo'lmasa — bugun (`default_from` — oylik oqim, oy boshi).
    Qaytaradi: (kunlar ro'yxati, xato matni | None).
    """
    date_str = str(payload.get("date") or "").strip()
    from_str = str(payload.get("from") or "").strip()
    to_str = str(payload.get("to") or "").strip()
    try:
        if from_str and to_str:
            d_from = date.fromisoformat(from_str)
            d_to = date.fromisoformat(to_str)
        elif date_str:
            d_from = d_to = date.fromisoformat(date_str)
        elif default_from:
            d_from = date.fromisoformat(default_from)
            d_to = date.today()
        else:
            d_from = d_to = date.today()
    except ValueError:
        return None, "Sana formati noto'g'ri (YYYY-MM-DD)"
    if d_to < d_from:
        return None, "'to' sana 'from'dan oldin bo'lishi mumkin emas"
    return ([(d_from + timedelta(days=i)).isoformat()
             for i in range((d_to - d_from).days + 1)], None)


def _start_sync_bg(bg: dict, storage, days: list[str],
                   routes: list[str], client=None, owner: str = "") -> None:
    """Fon oqimida sync ishga tushiradi (bloklamaydi — login 1-3 daqiqa).

    Oldin `sync_routes` (yo'nalishlar daraxti), so'ng har bir kun
    `db_sync.sync_days` orqali (retry bilan). Tugagach natija `bg["result"]`
    ga yoziladi va `_last_sync_at` yangilanadi (20s cooldown).

    `client` berilsa o'sha (masalan, kompaniyaning O'Z tokeni bilan qurilgan)
    client ishlatiladi — yangi kompaniya onboarding'ida asosiy token boshqa
    firma yo'nalishini ko'rmaydi.
    """
    def _bg():
        global _last_sync_at
        try:
            from ...api.client import BMClient
            from ...db import sync as db_sync

            cl = client
            if cl is None:
                bg["progress"] = "Token olinmoqda..."
                cl = BMClient()
                cl.login()

            results = [db_sync.sync_routes(storage, cl).as_dict()]
            results += db_sync.sync_days(
                storage, cl, days, routes=routes,
                progress_cb=_sync_progress_cb(bg))

            ok = not any(r.get("error") for r in results)
            _last_sync_at = time.monotonic()
            bg["result"] = {
                "ok": ok, "routes": routes, "from": days[0], "to": days[-1],
                "days": len(days), "results": results, "lines": _sync_lines(results)}
        except Exception as exc:  # noqa: BLE001 - fon oqimida 500 bo'lmaydi
            bg["result"] = {"ok": False, "error": str(exc)}
        finally:
            bg["running"] = False
            bg["progress"] = ""

    bg["running"] = True
    bg["progress"] = "Login..."
    bg["result"] = None
    bg["owner"] = str(owner or "")
    threading.Thread(target=_bg, daemon=True).start()


def start_company_sync(client, storage, routes: list[str],
                       days: list[str] | None = None) -> dict:
    """Yangi kompaniya uchun kunlik sinxronni fon rejimida boshlaydi.

    Onboarding (`/api/admin/companies/onboard`) shu yordamchidan foydalanadi:
    kompaniyaning O'Z tokeni bilan sinxron qilinadi, holat esa mavjud
    `/api/sync/status` orqali kuzatiladi (alohida oqim yaratilmaydi).

    Qaytadi: `_run_sync` bilan bir xil shaklda dict (ok/started/error).
    """
    busy = _sync_busy("Sinxronlash davom etmoqda — keyinroq urinib ko'ring")
    if busy:
        return busy
    err = _sync_interval_ok()
    if err:
        return err
    clean = [str(r).strip() for r in (routes or []) if str(r).strip()]
    if not clean:
        return {"ok": False, "error": "Yo'nalish ko'rsatilmagan"}
    day_list = [str(d) for d in (days or []) if str(d).strip()] or [date.today().isoformat()]
    _start_sync_bg(_DAILY_SYNC_BG, storage, day_list, clean, client=client)
    return {"ok": True, "started": True, "routes": clean,
            "from": day_list[0], "to": day_list[-1], "days": len(day_list)}


def _run_sync(payload: dict, scope: dict) -> dict:
    """Kunlik sinxronlash (fon oqimida — OneID login 1-3 daqiqa olishi mumkin).

    ``from``/``to`` (oraliq), ``date`` (bitta kun) yoki bo'sh (bugun) qabul
    qiladi. Natijalar `/api/sync/status` orqali olinadi. Barcha tekshiruv
    xatolari JSON dict — endpoint hech qachon 500 qaytarmaydi.
    """
    busy = _sync_busy()
    if busy:
        return busy
    err = _sync_interval_ok()
    if err:
        return err
    routes, err = _sync_target_routes(payload, scope)
    if err:
        return err
    days, err = _parse_days(payload)
    if err:
        return {"ok": False, "error": err}

    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}

    _start_sync_bg(_DAILY_SYNC_BG, storage, days, routes,
                   owner=str(scope.get("username") or ""))
    return {"ok": True, "started": True, "routes": routes,
            "from": days[0], "to": days[-1], "days": len(days)}


def _run_sync_monthly(payload: dict, scope: dict) -> dict:
    """Oylik (from/to oralig'i) sinxronlash — fon oqimida."""
    busy = _sync_busy()
    if busy:
        return busy
    err = _sync_interval_ok()
    if err:
        return err
    routes, err = _sync_target_routes(payload, scope)
    if err:
        return err
    days, err = _parse_days(payload, default_from=date.today().replace(day=1).isoformat())
    if err:
        return {"ok": False, "error": err}

    from ...db.storage import get_storage
    storage = get_storage()
    if not storage.enabled:
        return {"ok": False, "error": "DB rejimi o'chirilgan"}

    _start_sync_bg(_MONTHLY_SYNC_BG, storage, days, routes,
                   owner=str(scope.get("username") or ""))
    return {"ok": True, "started": True, "routes": routes,
            "from": days[0], "to": days[-1], "days": len(days)}


_SYNC_ENTITY_LABELS = {
    "routes": "Yo'nalishlar", "vehicles": "Avtobuslar",
    "drivers": "Haydovchilar", "driver_profiles": "Haydovchi profillari",
    "driver_work_logs": "Haydovchi ish jurnali", "duties": "Navbatchiliklar",
    "trips": "Reyslar", "waybills": "Yo'l varaqalari",
    "route_daily": "Kunlik masofa hisoboti",
}


def _sync_entity_label(entity: str) -> str:
    if entity.startswith("profiles/"):
        return f"{entity.split('/', 1)[1]} profili"
    return _SYNC_ENTITY_LABELS.get(entity, entity or "?")


def _sync_lines(results: list[dict]) -> list[str]:
    lines: list[str] = []
    for r in results or []:
        label = _sync_entity_label(str(r.get("entity") or ""))
        if r.get("error"):
            lines.append(f"✗ {label}: {r['error']}")
            continue
        parts = []
        if r.get("inserted"):
            parts.append(f"qo'shildi {r['inserted']}")
        if r.get("updated"):
            parts.append(f"yangilandi {r['updated']}")
        if r.get("deleted"):
            parts.append(f"o'chirildi {r['deleted']}")
        if not parts:
            parts.append("o'zgarish yo'q")
        lines.append(f"• {label}: {', '.join(parts)}")
    return lines


# ---------------------------------------------------------------------------
# MyAI Agent
# ---------------------------------------------------------------------------

def _myai_company(scope: dict) -> str:
    """Sessiya uchun MyAI korxona yorlig'i ('' = global/admin)."""
    from ...myai.scope import scope_company_label, scope_for_session
    return scope_company_label(scope_for_session(scope))


def _myai_task_owned(task: dict | None, company: str) -> bool:
    """Korxona foydalanuvchisi faqat o'z korxonasi vazifasini ko'radi."""
    if not company:
        return True
    return bool(task) and str(task.get("company") or "") == company


def _myai_require_owned(task_id: str, company: str) -> dict:
    from ...myai.state import get_task
    task = get_task(task_id)
    if not task or not _myai_task_owned(task, company):
        raise HTTPException(404, "Vazifa topilmadi")
    return task


@router.post("/api/myai/task")
async def myai_task_create(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    request_text = (payload.get("request") or "").strip()
    if not request_text:
        raise HTTPException(400, "request kiritilmagan")
    from ...myai.scope import scope_for_session
    from ...myai.state import create_task
    myai_scope = scope_for_session(scope)
    company = _myai_company(scope)
    task = create_task(request_text, params={"scope": myai_scope},
                       company=company)
    task_id = task["task_id"] if isinstance(task, dict) else task.task_id

    def _run_myai():
        try:
            from ...myai.orchestrator import get_orchestrator
            import asyncio
            orch = get_orchestrator()
            asyncio.run(orch.run_task(task_id))
        except Exception:
            from ...utils.logger import get_logger
            get_logger("myai").exception("MyAI task xatosi #%s", task_id)

    threading.Thread(target=_run_myai, daemon=True).start()
    return {"ok": True, "task_id": task_id}


@router.get("/api/myai/tasks")
def myai_tasks(request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import list_tasks, count_tasks
    params = _parse_qs(request)
    try:
        limit = min(max(int(params.get("limit", "20")), 1), 200)
        offset = max(int(params.get("offset", "0")), 0)
    except (TypeError, ValueError):
        limit, offset = 20, 0
    status = params.get("status", "").strip()
    q = params.get("q", "").strip()
    company = _myai_company(scope)
    tasks = list_tasks(limit=limit, status=status, offset=offset, q=q,
                       company=company)
    return {"ok": True, "tasks": tasks,
            "total": count_tasks(status=status, q=q, company=company),
            "limit": limit, "offset": offset}


@router.get("/api/myai/task/{task_id}")
def myai_task_detail(task_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import get_logs
    task = _myai_require_owned(task_id, _myai_company(scope))
    task["logs"] = get_logs(task_id, limit=30)
    return {"ok": True, "task": task}


@router.get("/api/myai/task/{task_id}/logs")
def myai_task_logs(task_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import get_logs
    _myai_require_owned(task_id, _myai_company(scope))
    return {"ok": True, "logs": get_logs(task_id, limit=50)}


@router.post("/api/myai/task/cancel")
async def myai_task_cancel(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        raise HTTPException(400, "task_id kerak")
    _myai_require_owned(tid, _myai_company(scope))
    from ...myai.state import update_task
    from ...myai.events import emit_task
    update_task(tid, status="cancelled", error="Foydalanuvchi tomonidan bekor qilindi")
    emit_task("task.cancelled", task_id=tid, message="Bekor qilindi")
    try:
        from ...myai.orchestrator import get_orchestrator
        get_orchestrator().cancel_task(tid)
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/myai/task/pause")
async def myai_task_pause(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        raise HTTPException(400, "task_id kerak")
    _myai_require_owned(tid, _myai_company(scope))
    try:
        from ...myai.orchestrator import get_orchestrator
        from ...myai.events import emit_task
        ok = get_orchestrator().pause_task(tid)
        if ok:
            emit_task("task.paused", task_id=tid)
            return {"ok": True, "action": "paused"}
        return {"ok": False, "error": "allaqachon pauzada"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/api/myai/task/resume")
async def myai_task_resume(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        raise HTTPException(400, "task_id kerak")
    _myai_require_owned(tid, _myai_company(scope))
    try:
        from ...myai.orchestrator import get_orchestrator
        from ...myai.events import emit_task
        ok = get_orchestrator().resume_task(tid)
        if ok:
            emit_task("task.resumed", task_id=tid)
            return {"ok": True, "action": "resumed"}
        return {"ok": False, "error": "pauzada emas"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/api/myai/status")
def myai_status(request: Request, scope: dict = Depends(Scope())):
    try:
        from ...myai.providers.factory import get_provider
        from ...myai.state import get_stats
        stats = get_stats(company=_myai_company(scope))
        provider = get_provider()
        return {"ok": True, "provider": provider.name if provider else None, "stats": stats}
    except Exception:
        return {"ok": True, "provider": None,
                "stats": {"total": 0, "active": 0, "completed": 0, "failed": 0}}


@router.get("/api/myai/registry")
def myai_registry(request: Request, scope: dict = Depends(Scope())):
    from ...myai.registry import all_agents
    return {"ok": True, "agents": all_agents()}


def _myai_event_filter(company: str):
    """Korxona scope'i uchun task->company keshi bilan filtr."""
    if not company:
        return None
    from ...myai.state import get_task

    cache: dict[str, str] = {}

    def allow(ev) -> bool:
        tid = str(getattr(ev, "task_id", "") or "")
        if not tid:
            return False
        if tid not in cache:
            try:
                row = get_task(tid)
                cache[tid] = str((row or {}).get("company") or "")
            except Exception:  # noqa: BLE001
                cache[tid] = ""
        return cache[tid] == company

    return allow


@router.get("/api/myai/activity")
def myai_activity(request: Request, scope: dict = Depends(Scope())):
    from ...myai.events import get_event_bus
    params = _parse_qs(request)
    try:
        limit = int(params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50
    events = get_event_bus().recent(
        limit, allow=_myai_event_filter(_myai_company(scope)))
    return {"ok": True, "events": events}


@router.get("/api/myai/events")
async def myai_events_sse(request: Request, scope: dict = Depends(Scope())):
    from ...myai.events import get_event_bus
    bus = get_event_bus()
    allow = _myai_event_filter(_myai_company(scope))

    def event_stream():
        for line in bus.events_sse(timeout=30, allow=allow):
            yield line

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store",
                 "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# Disk usage helper
# ---------------------------------------------------------------------------

def _dir_size(path: Path, max_files: int = 20000) -> tuple[int, int]:
    total = 0
    files = 0
    if not path.exists():
        return 0, 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                    files += 1
                except OSError:
                    continue
                if files >= max_files:
                    break
    except OSError:
        pass
    return total, files


def _db_disk_info() -> dict:
    size_bytes = 0
    size_pretty = "?"
    engine = ""
    db_age_days = 0
    try:
        from ...db.storage import get_storage
        st = get_storage()
        if st.enabled:
            engine = "postgres"
            row = st.query(
                "SELECT pg_database_size(current_database()) b, "
                "pg_size_pretty(pg_database_size(current_database())) p", limit=1)
            if row:
                size_bytes = int(row[0].get("b") or 0)
                size_pretty = str(row[0].get("p") or "?")
            r0 = st.query("SELECT MIN(date) d0 FROM route_daily", limit=1)
            if r0 and r0[0].get("d0"):
                try:
                    d0 = date.fromisoformat(str(r0[0]["d0"])[:10])
                    db_age_days = max((date.today() - d0).days, 1)
                except ValueError:
                    pass
    except Exception:
        pass

    growth = size_bytes / db_age_days if (size_bytes and db_age_days) else 0.0
    history: dict[str, dict] = {}
    hist_path = Path("state") / "disk_history.json"
    try:
        if hist_path.is_file():
            loaded = json.loads(hist_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                history = loaded
    except (OSError, ValueError):
        history = {}
    today = date.today().isoformat()
    return {"engine": engine, "size_bytes": size_bytes, "size_pretty": size_pretty,
            "db_age_days": db_age_days, "daily_growth_bytes": int(growth),
            "history": history, "history_path": hist_path, "today": today}


def _proc_meminfo() -> dict:
    """Linux ``/proc/meminfo`` dan qiymatlarni baytda qaytaradi."""
    info: dict = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                parts = rest.strip().split()
                if parts and parts[0].isdigit():
                    info[key.strip()] = int(parts[0]) * 1024
    except OSError:
        return {}
    return info


def _proc_uptime_s() -> int:
    """Linux ``/proc/uptime`` dan ishlash vaqtini (sekund) qaytaradi."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _server_status() -> dict:
    """Server (OS) holati: disk, xotira, yuklama va ishlash vaqti.

    Dashboard serverning o'zida ishlaydi, shuning uchun bu yerda ko'rsatilgan
    qiymatlar aynan serverning haqiqiy holati (root fayl tizimi, RAM, load).
    """
    import shutil
    import socket

    out: dict = {"host": "", "disk": {}, "memory": {}, "cpu": {},
                 "uptime_s": 0}
    try:
        out["host"] = socket.gethostname()
    except OSError:
        pass
    try:
        du = shutil.disk_usage(os.getcwd())
        out["disk"] = {
            "total": du.total, "used": du.used, "free": du.free,
            "percent": round(du.used * 100 / du.total, 1) if du.total else 0.0,
        }
    except OSError:
        pass
    mem = _proc_meminfo()
    if mem.get("MemTotal"):
        total = mem["MemTotal"]
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        used = max(total - avail, 0)
        out["memory"] = {
            "total": total, "used": used, "free": avail,
            "percent": (round(used * 100 / total, 1) if total else 0.0),
        }
    cpu: dict = {"cores": os.cpu_count() or 0}
    try:
        load = os.getloadavg()
        cpu.update({"load1": round(load[0], 2), "load5": round(load[1], 2),
                    "load15": round(load[2], 2)})
    except (OSError, AttributeError):
        pass
    out["cpu"] = cpu
    out["uptime_s"] = _proc_uptime_s()
    return out


def _disk_usage() -> dict:
    folders = [
        ("reports/", "Generatsiya chiqishlari (30 kun TTL bilan tozalanadi)"),
        ("data/", "Haydovchi rasmlari va hujjatlari (arxiv, TTL yo'q)"),
        ("logs/", "Loglar (30 kun TTL + rotatsiya)"),
        ("backup/", "Qo'lda olingan SQL dump'lar (git'da yo'q)"),
        ("state/", "Bot holati, tokenlar, sozlamalar"),
    ]
    items = []
    total = 0
    for name, note in folders:
        b, f = _dir_size(Path(name))
        items.append({"name": name, "bytes": b, "files": f, "note": note})
        total += b
    db = _db_disk_info()
    history: dict = db.pop("history", {})
    hist_path: Path = db.pop("history_path")
    today: str = db.pop("today")
    if db.get("size_bytes"):
        history[today] = {"db_bytes": db.get("size_bytes", 0), "total_bytes": total}
    keep = sorted(history)[-60:]
    trimmed = {k: history[k] for k in keep}
    trend = [{"date": k, "week": k[5:], "bytes": v.get("db_bytes", 0)}
             for k, v in sorted(trimmed.items())][-30:]
    try:
        from ...utils.io import atomic_write
        atomic_write(hist_path, json.dumps(trimmed, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return {
        "ok": True, "folders": items, "total_bytes": total,
        "total_note": f"+ DB {db.get('size_pretty', '?')} alohida (Docker volume)",
        "daily_growth_bytes": db.get("daily_growth_bytes") or 0,
        "db": db, "db_trend": trend, "server": _server_status(),
    }
