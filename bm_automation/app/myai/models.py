"""MyAI Pydantic models — task, step, agent status, results."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentType(str, enum.Enum):
    MASTER = "master"
    PLANNER = "planner"
    BROWSER = "browser"
    TRANSPORT = "transport"
    ANALYTICS = "analytics"
    REVIEWER = "reviewer"
    DRIVER = "driver"
    ROUTE = "route"
    SCHEDULE = "schedule"
    ATTENDANCE = "attendance"
    EXCEL = "excel"
    REPORT = "report"
    TELEGRAM = "telegram"
    SECURITY = "security"


class LogLevel(str, enum.Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Permission(str, enum.Enum):
    READ_DATA = "READ_DATA"
    CREATE_DATA = "CREATE_DATA"
    UPDATE_DATA = "UPDATE_DATA"
    DELETE_DATA = "DELETE_DATA"


# ── LLM Models ──────────────────────────────────────────────────────

class LLMResponse(BaseModel):
    """LLM provider natijasi."""
    content: str
    model: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0


class LLMMessage(BaseModel):
    """Chat message."""
    role: str  # "system", "user", "assistant"
    content: str


# ── Task Models ──────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    """Yangi topshiriq yaratish uchun."""
    user_request: str
    route: str = ""
    date: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStep(BaseModel):
    """Topshiriq qadami."""
    id: int = 0
    task_id: str = ""
    agent: AgentType
    action: str = ""
    status: StepStatus = StepStatus.PENDING
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0


class Task(BaseModel):
    """Topshiriq."""
    id: int = 0
    task_id: str = ""
    user_request: str = ""
    status: TaskStatus = TaskStatus.PENDING
    current_agent: AgentType | None = None
    progress: int = 0
    steps: list[TaskStep] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None


# ── Agent Models ─────────────────────────────────────────────────────

class AgentContext(BaseModel):
    """Agent uchun kontekst."""
    task_id: str = ""
    user_request: str = ""
    route: str = ""
    date: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    previous_results: dict[str, Any] = Field(default_factory=dict)
    step_index: int = 0


class AgentResult(BaseModel):
    """Agent natijasi."""
    success: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    source: str = ""  # ma'lumot manbasi (shaffoflik uchun)
    metrics: dict[str, Any] = Field(default_factory=dict)


class AgentStatus(BaseModel):
    """Agent holati (dashboard uchun)."""
    agent: AgentType
    status: StepStatus = StepStatus.PENDING
    message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0


# ── Plan Models ──────────────────────────────────────────────────────

class PlanStep(BaseModel):
    """Plandagi bir qadam."""
    agent: AgentType
    action: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)


class Plan(BaseModel):
    """Execution plan — Planner Agent natijasi."""
    task_type: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[PlanStep] = Field(default_factory=list)


# ── Review Models ────────────────────────────────────────────────────

class ReviewResult(BaseModel):
    """Reviewer Agent natijasi."""
    approved: bool = True
    errors: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    confidence: float = 1.0


# ── API Response Models ─────────────────────────────────────────────

class TaskResponse(BaseModel):
    """API task response."""
    ok: bool = True
    task: Task | None = None
    error: str = ""


class TasksResponse(BaseModel):
    """API tasks list response."""
    ok: bool = True
    tasks: list[Task] = Field(default_factory=list)
    total: int = 0


class SystemStatus(BaseModel):
    """MyAI tizimi holati."""
    ok: bool = True
    configured: bool = False
    provider: str = ""
    model: str = ""
    agents: list[AgentStatus] = Field(default_factory=list)
    active_tasks: int = 0
    total_tasks: int = 0


# Fix forward references
AgentResult.model_rebuild()
SystemStatus.model_rebuild()
