"""System router — health, disk, logs, backup, sync, myai, SSE."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
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
async def api_health(request: Request, scope: dict = Depends(Scope())):
    from .. import metrics as m
    sys_info = m.Metrics().system()
    return {"ok": True, "uptime_s": int(time.time() - _STARTED), "system": sys_info}


@router.get("/api/disk")
async def api_disk(request: Request, scope: dict = Depends(Scope())):
    return _disk_usage()


@router.get("/api/logs")
async def api_logs(request: Request, scope: dict = Depends(Scope())):
    from ...utils.logger import FILE_LOG
    params = _parse_qs(request)
    try:
        lines = int(params.get("lines", "200"))
    except (TypeError, ValueError):
        lines = 200
    lines = min(lines, 2000)
    level_filter = params.get("level", "").upper()
    search = params.get("q", "")
    source = params.get("source", "bm").strip().lower()

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

    bot_log = Path("state") / "watchdog_bot.out.log"
    wd_log = Path("state") / "watchdog.log"

    if source == "bot":
        all_lines = _tail_lines(bot_log, lines)
    elif source == "watchdog":
        all_lines = _tail_lines(wd_log, lines)
    elif source == "all":
        per = max(lines // 3, 20)
        bm_tail = _tail_lines(FILE_LOG, per)
        all_lines = ([f"[BOT] {l}" for l in _tail_lines(bot_log, per)]
                     + [f"[WD] {l}" for l in _tail_lines(wd_log, per)]
                     + [f"[BM] {l}" for l in bm_tail])
    else:
        try:
            size = FILE_LOG.stat().st_size
        except OSError:
            size = 0
        if size == 0:
            all_lines = []
        else:
            read_n = min(size, max(lines * 160 + 8192, 65536))
            try:
                with open(FILE_LOG, "rb") as f:
                    f.seek(size - read_n)
                    raw = f.read(read_n)
                all_lines = raw.decode("utf-8", errors="replace").splitlines()
            except OSError:
                all_lines = []

    if level_filter:
        all_lines = [l for l in all_lines if f"| {level_filter}" in l]
    if search:
        sl = search.lower()
        all_lines = [l for l in all_lines if sl in l.lower()]

    return {"ok": True, "lines": all_lines[-lines:], "total": len(all_lines), "source": source}


@router.get("/api/insights")
async def api_insights(request: Request, scope: dict = Depends(Scope())):
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


@router.get("/api/backup/run")
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

_DAILY_SYNC_BG: dict = {"running": False, "progress": None, "result": None}


@router.post("/api/sync")
async def api_sync(request: Request, scope: dict = Depends(Scope())):
    global _last_sync_at
    payload = await request.json()
    now = time.monotonic()
    with _SYNC_LOCK:
        if now - _last_sync_at < _SYNC_MIN_INTERVAL_S:
            return {"ok": False, "error": "Sync yaqinda bajarilgan — 20 soniya kuting"}
        _last_sync_at = now
    return _run_sync(payload, scope)


@router.get("/api/sync/status")
async def sync_status(request: Request, scope: dict = Depends(Scope())):
    st = _DAILY_SYNC_BG
    if st["running"]:
        return {"ok": True, "running": True, "progress": st["progress"]}
    elif st["result"]:
        return {"ok": True, "running": False, **st["result"]}
    return {"ok": True, "running": False}


@router.post("/api/sync/monthly")
async def sync_monthly(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    return _run_sync_monthly(payload, scope)


@router.get("/api/sync/monthly/status")
async def sync_monthly_status(request: Request, scope: dict = Depends(Scope())):
    return _sync_monthly_status()


def _run_sync(payload: dict, scope: dict) -> dict:
    from ...db.sync import sync_all
    from ...api.client import BMClient

    route = str(payload.get("route") or "").strip()
    sync_date = str(payload.get("date") or "").strip()
    what = str(payload.get("what") or "statuses,profiles,routes,drivers,vehicles,duties,trips,waybills").strip()

    scopes = scope_routes(scope)
    if scopes is not None and route:
        if route not in scopes:
            return {"ok": False, "error": "Yo'nalish topilmadi"}
    if scopes is not None and not route:
        route = list(scopes)[0] if scopes else ""

    try:
        results = sync_all(route=route, date_str=sync_date, what=what)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    lines = _sync_lines(results)
    return {"ok": True, "results": results, "lines": lines}


def _run_sync_monthly(payload: dict, scope: dict) -> dict:
    from ...db.sync import sync_monthly

    route = str(payload.get("route") or "").strip()
    month = str(payload.get("month") or "").strip()

    scopes = scope_routes(scope)
    if scopes is not None and route and route not in scopes:
        return {"ok": False, "error": "Yo'nalish topilmadi"}
    if scopes is not None and not route:
        route = list(scopes)[0] if scopes else ""

    def _bg():
        _DAILY_SYNC_BG["running"] = True
        _DAILY_SYNC_BG["result"] = None
        try:
            result = sync_monthly(route=route, month=month,
                                  progress_cb=lambda p: _DAILY_SYNC_BG.update(progress=p))
            _DAILY_SYNC_BG["result"] = {"ok": True, **result}
        except Exception as exc:
            _DAILY_SYNC_BG["result"] = {"ok": False, "error": str(exc)}
        finally:
            _DAILY_SYNC_BG["running"] = False

    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "started": True}


def _sync_monthly_status() -> dict:
    st = _DAILY_SYNC_BG
    if st["running"]:
        return {"ok": True, "running": True, "progress": st["progress"]}
    elif st["result"]:
        return {"ok": True, "running": False, **st["result"]}
    return {"ok": True, "running": False}


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

@router.post("/api/myai/task")
async def myai_task_create(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    request_text = (payload.get("request") or "").strip()
    if not request_text:
        raise HTTPException(400, "request kiritilmagan")
    from ...myai.state import create_task
    task = create_task(request_text)
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
async def myai_tasks(request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import list_tasks, count_tasks
    params = _parse_qs(request)
    try:
        limit = min(max(int(params.get("limit", "20")), 1), 200)
        offset = max(int(params.get("offset", "0")), 0)
    except (TypeError, ValueError):
        limit, offset = 20, 0
    status = params.get("status", "").strip()
    q = params.get("q", "").strip()
    tasks = list_tasks(limit=limit, status=status, offset=offset, q=q)
    return {"ok": True, "tasks": tasks, "total": count_tasks(status=status, q=q),
            "limit": limit, "offset": offset}


@router.get("/api/myai/task/{task_id}")
async def myai_task_detail(task_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import get_task, get_logs
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Vazifa topilmadi")
    task["logs"] = get_logs(task_id, limit=30)
    return {"ok": True, "task": task}


@router.get("/api/myai/task/{task_id}/logs")
async def myai_task_logs(task_id: str, request: Request, scope: dict = Depends(Scope())):
    from ...myai.state import get_logs
    return {"ok": True, "logs": get_logs(task_id, limit=50)}


@router.post("/api/myai/task/cancel")
async def myai_task_cancel(request: Request, scope: dict = Depends(Scope())):
    payload = await request.json()
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        raise HTTPException(400, "task_id kerak")
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
async def myai_status(request: Request, scope: dict = Depends(Scope())):
    try:
        from ...myai.providers.factory import get_provider
        from ...myai.state import get_stats
        stats = get_stats()
        provider = get_provider()
        return {"ok": True, "provider": provider.name if provider else None, "stats": stats}
    except Exception:
        return {"ok": True, "provider": None,
                "stats": {"total": 0, "active": 0, "completed": 0, "failed": 0}}


@router.get("/api/myai/registry")
async def myai_registry(request: Request, scope: dict = Depends(Scope())):
    from ...myai.registry import all_agents
    return {"ok": True, "agents": all_agents()}


@router.get("/api/myai/activity")
async def myai_activity(request: Request, scope: dict = Depends(Scope())):
    from ...myai.events import get_event_bus
    params = _parse_qs(request)
    try:
        limit = int(params.get("limit", "50"))
    except (TypeError, ValueError):
        limit = 50
    return {"ok": True, "events": get_event_bus().recent(limit)}


@router.get("/api/myai/events")
async def myai_events_sse(request: Request, scope: dict = Depends(Scope())):
    from ...myai.events import get_event_bus
    bus = get_event_bus()

    def event_stream():
        for line in bus.events_sse(timeout=30):
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
        "db": db, "db_trend": trend,
    }
