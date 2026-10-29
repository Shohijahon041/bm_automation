"""MyAI Task State Management — PostgreSQL based."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

from ..db.storage import get_storage
from ..utils.logger import get_logger

log = get_logger("myai.state")

# ── Schema ───────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS myai_tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64) UNIQUE NOT NULL,
    user_request TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    current_agent VARCHAR(32),
    progress INTEGER DEFAULT 0,
    company VARCHAR(255) DEFAULT '',
    result JSONB DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS myai_task_steps (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64) REFERENCES myai_tasks(task_id),
    agent VARCHAR(32) NOT NULL,
    action VARCHAR(64) DEFAULT '',
    status VARCHAR(20) DEFAULT 'pending',
    input_data JSONB DEFAULT '{}',
    output_data JSONB DEFAULT '{}',
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS myai_agent_logs (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    agent VARCHAR(32) NOT NULL,
    level VARCHAR(10) DEFAULT 'info',
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS myai_events (
    id SERIAL PRIMARY KEY,
    event VARCHAR(32) NOT NULL,
    agent_id VARCHAR(32) DEFAULT '',
    status VARCHAR(20) DEFAULT '',
    action VARCHAR(128) DEFAULT '',
    task_id VARCHAR(64) DEFAULT '',
    progress INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    from_agent VARCHAR(32) DEFAULT '',
    to_agent VARCHAR(32) DEFAULT '',
    data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_myai_tasks_task_id ON myai_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_myai_tasks_status ON myai_tasks(status);
CREATE INDEX IF NOT EXISTS idx_myai_steps_task_id ON myai_task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_myai_logs_task_id ON myai_agent_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_myai_logs_created ON myai_agent_logs(created_at);

-- Mavjud bazaga korxona ustunini qo'shish (eski yozuvlar '' = global).
ALTER TABLE myai_tasks ADD COLUMN IF NOT EXISTS company VARCHAR(255) DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_myai_tasks_company ON myai_tasks(company);
"""


def init_myai_schema() -> bool:
    """MyAI jadvallarini yaratish."""
    storage = get_storage()
    if not storage.enabled:
        return False
    try:
        stmts = [s.strip() for s in _SCHEMA_SQL.split(";") if s.strip()]
        for stmt in stmts:
            try:
                storage.db.execute(stmt)
            except Exception:
                pass
        log.info("MyAI schema yaratildi (4 jadval)")
        return True
    except Exception as exc:
        log.error("MyAI schema xatosi: %s", exc)
        return False


# ── Task CRUD ────────────────────────────────────────────────────────

def create_task(user_request: str, route: str = "", date: str = "",
                params: dict | None = None, company: str = "") -> dict:
    """Yangi topshiriq yaratish. dict qaytaradi."""
    storage = get_storage()
    task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}"
    now = datetime.now().isoformat()
    result_json = json.dumps({"route": route, "date": date,
                              **(params or {})})
    company = str(company or "").strip()

    storage.db.execute(
        "INSERT INTO myai_tasks (task_id, user_request, status, progress, "
        "company, result, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (task_id, user_request, "pending", 0, company, result_json, now, now),
    )

    log.info("Topshiriq yaratildi: %s", task_id)
    return {
        "task_id": task_id,
        "user_request": user_request,
        "status": "pending",
        "progress": 0,
        "company": company,
        "result": {"route": route, "date": date, **(params or {})},
        "created_at": now,
        "updated_at": now,
    }


def get_task(task_id: str) -> dict | None:
    """Topshiriqni olish — dict qaytaradi."""
    storage = get_storage()
    rows = storage.query(
        "SELECT * FROM myai_tasks WHERE task_id = %s", (task_id,),
    )
    if not rows:
        return None
    row = rows[0]
    result = {}
    if row.get("result"):
        try:
            result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
        except Exception:
            result = {}
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "user_request": row["user_request"],
        "status": row["status"],
        "current_agent": row.get("current_agent") or "",
        "progress": row["progress"] or 0,
        "company": str(row.get("company") or ""),
        "result": result,
        "error": row.get("error") or "",
        "created_at": str(row["created_at"]) if row.get("created_at") else "",
        "updated_at": str(row["updated_at"]) if row.get("updated_at") else "",
        "completed_at": str(row["completed_at"]) if row.get("completed_at") else "",
    }


def update_task(task_id: str, **kwargs) -> bool:
    """Topshirig'ni yangilash."""
    storage = get_storage()
    sets = []
    params = []
    for key, val in kwargs.items():
        if key in ("status", "current_agent", "progress", "error", "result"):
            if key == "result" and isinstance(val, dict):
                val = json.dumps(val)
            if key == "progress":
                val = int(val)
            sets.append(f"{key} = %s")
            params.append(val)
    if not sets:
        return False
    sets.append("updated_at = NOW()")
    params.append(task_id)
    storage.db.execute(
        f"UPDATE myai_tasks SET {', '.join(sets)} WHERE task_id = %s",
        tuple(params),
    )
    return True


def list_tasks(limit: int = 20, status: str = "",
               offset: int = 0, q: str = "", company: str = "") -> list[dict]:
    """Topshiriqlar ro'yxati — dict list qaytaradi.

    Paginatsiya: limit + offset. status va q (matn qidiruv) bo'yicha filtrlash.
    ``company`` berilsa — faqat shu korxona vazifalari (korxona scope'i).
    """
    storage = get_storage()
    where = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    q = (q or "").strip()
    if q:
        where.append("user_request ILIKE %s")
        params.append(f"%{q}%")
    if company:
        where.append("company = %s")
        params.append(str(company))
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    params.extend((limit, offset))
    rows = storage.query(
        f"SELECT * FROM myai_tasks{where_sql} "
        "ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params),
    )
    tasks = []
    for row in rows:
        result = {}
        if row.get("result"):
            try:
                result = json.loads(row["result"]) if isinstance(row["result"], str) else row["result"]
            except Exception:
                result = {}
        tasks.append({
            "id": row["id"],
            "task_id": row["task_id"],
            "user_request": row["user_request"],
            "status": row["status"],
            "current_agent": row.get("current_agent") or "",
            "progress": row["progress"] or 0,
            "company": str(row.get("company") or ""),
            "result": result,
            "error": row.get("error") or "",
            "created_at": str(row["created_at"]) if row.get("created_at") else "",
            "updated_at": str(row["updated_at"]) if row.get("updated_at") else "",
            "completed_at": str(row["completed_at"]) if row.get("completed_at") else "",
        })
    return tasks


def count_tasks(status: str = "", q: str = "", company: str = "") -> int:
    """Topshiriqlar soni — status, q va company bo'yicha filtrlash mumkin."""
    storage = get_storage()
    where = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    q = (q or "").strip()
    if q:
        where.append("user_request ILIKE %s")
        params.append(f"%{q}%")
    if company:
        where.append("company = %s")
        params.append(str(company))
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    rows = storage.query(
        f"SELECT COUNT(*) AS n FROM myai_tasks{where_sql}", tuple(params),
    )
    return rows[0]["n"] if rows else 0


# ── Steps ────────────────────────────────────────────────────────────

def add_task_step(task_id: str, agent: str, action: str = "") -> int:
    """Task ga qadam qo'shish. Yangi task step yaratadi."""
    storage = get_storage()
    rows = storage.query(
        "INSERT INTO myai_task_steps (task_id, agent, action, status) "
        "VALUES (%s, %s, %s, 'pending') RETURNING id",
        (task_id, agent, action),
    )
    return rows[0]["id"] if rows else 0


# Backward compatibility
add_step = add_task_step


def update_task_step(step_id: int, **kwargs) -> bool:
    """Qadamni yangilash."""
    storage = get_storage()
    sets = []
    params = []
    for key, val in kwargs.items():
        if key in ("status", "output_data", "error", "duration_ms"):
            if key == "output_data" and isinstance(val, dict):
                val = json.dumps(val)
            sets.append(f"{key} = %s")
            params.append(val)
    if not sets:
        return False
    params.append(step_id)
    storage.db.execute(
        f"UPDATE myai_task_steps SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )
    return True


# Backward compatibility
update_step = update_task_step


def get_task_steps(task_id: str) -> list[dict]:
    """Task qadamlarini olish."""
    storage = get_storage()
    rows = storage.query(
        "SELECT * FROM myai_task_steps WHERE task_id = %s ORDER BY id",
        (task_id,),
    )
    return rows


# Backward compatibility
_get_steps = get_task_steps


# ── Logs ─────────────────────────────────────────────────────────────

def add_log(task_id: str, agent: str, message: str,
            level: str = "info", metadata: dict | None = None) -> None:
    """Agent log qo'shish."""
    storage = get_storage()
    storage.db.execute(
        "INSERT INTO myai_agent_logs (task_id, agent, level, message, metadata) "
        "VALUES (%s, %s, %s, %s, %s)",
        (task_id, agent, level, message,
         json.dumps(metadata or {})),
    )


def get_logs(task_id: str, limit: int = 50) -> list[dict]:
    """Task loglarini olish."""
    storage = get_storage()
    rows = storage.query(
        "SELECT * FROM myai_agent_logs WHERE task_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (task_id, limit),
    )
    return rows


# ── Stats ────────────────────────────────────────────────────────────

def reconcile_stale_tasks(max_age_minutes: int = 45) -> int:
    """Uzilib qolgan 'running' tasklarni 'failed' deb yopish.

    Bot/dashboard qayta ishga tushganda o'sha paytda bajarilayotgan
    tasklar DB'da 'running' bo'lib qoladi va panelda abadiy "ishlayapti"
    ko'rinadi. `updated_at` eski bo'lganlar yopiladi.
    """
    # Testlar umumiy bazaga yozmasin — ular o'z fake state'ini ishlatadi.
    if os.getenv("BM_TEST_MODE"):
        return 0
    storage = get_storage()
    if not getattr(storage, "enabled", False):
        return 0
    try:
        rows = storage.query(
            "SELECT task_id, updated_at FROM myai_tasks "
            "WHERE status = 'running'", [],
        )
    except Exception as exc:  # noqa: BLE001
        # Jadval hali yaratilmagan bo'lishi mumkin (birinchi ishga tushish).
        log.debug("Uzilgan tasklarni tekshirib bo'lmadi: %s", exc)
        return 0

    cutoff = datetime.now().astimezone() - timedelta(minutes=max_age_minutes)
    now = datetime.now().isoformat()
    reason = ("Jarayon qayta ishga tushdi — vazifa uzildi "
              "(natija saqlanmagan). So'rovni qayta yuboring.")
    closed = 0
    for row in rows:
        raw = row.get("updated_at")
        if isinstance(raw, datetime):
            ts = raw
        else:
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
        if ts.tzinfo is None:
            ts = ts.astimezone()
        if ts >= cutoff:
            continue
        try:
            storage.db.execute(
                "UPDATE myai_tasks SET status = %s, error = %s, "
                "updated_at = %s WHERE task_id = %s",
                ("failed", reason, now, row["task_id"]),
            )
            closed += 1
        except Exception as exc:  # noqa: BLE001
            log.debug("Task %s ni yopib bo'lmadi: %s",
                      row.get("task_id"), exc)
    if closed:
        log.warning("MyAI: %d uzilgan task 'failed' deb belgilandi", closed)
    return closed


def get_stats(company: str = "") -> dict:
    """MyAI umumiy statistikasi (``company`` berilsa — faqat shu korxona)."""
    storage = get_storage()
    company = str(company or "").strip()
    where = " WHERE company = %s" if company else ""
    params: tuple = (company,) if company else ()
    try:
        total = storage.query(
            f"SELECT COUNT(*) AS n FROM myai_tasks{where}", params)
        active = storage.query(
            "SELECT COUNT(*) AS n FROM myai_tasks WHERE status = 'running'"
            + (" AND company = %s" if company else ""), params)
        completed = storage.query(
            "SELECT COUNT(*) AS n FROM myai_tasks WHERE status = 'completed'"
            + (" AND company = %s" if company else ""), params)
        failed = storage.query(
            "SELECT COUNT(*) AS n FROM myai_tasks WHERE status = 'failed'"
            + (" AND company = %s" if company else ""), params)
        return {
            "total": total[0]["n"] if total else 0,
            "active": active[0]["n"] if active else 0,
            "completed": completed[0]["n"] if completed else 0,
            "failed": failed[0]["n"] if failed else 0,
        }
    except Exception:
        return {"total": 0, "active": 0, "completed": 0, "failed": 0}
