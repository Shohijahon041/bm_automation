"""Security Agent — ruxsatlar tizimi."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .agents import BaseAgent
from .models import AgentContext, AgentResult, AgentType
from ..utils.logger import get_logger

log = get_logger("myai.security_agent")


class Permission(str, Enum):
    READ_ROUTES = "read_routes"
    READ_DRIVERS = "read_drivers"
    READ_TRIPS = "read_trips"
    READ_ATTENDANCE = "read_attendance"
    READ_SALARY = "read_salary"
    READ_SETTINGS = "read_settings"
    WRITE_ROUTES = "write_routes"
    WRITE_DRIVERS = "write_drivers"
    WRITE_SETTINGS = "write_settings"
    SYNC_DATA = "sync_data"
    GENERATE_REPORT = "generate_report"
    SEND_TELEGRAM = "send_telegram"
    RUN_AI_TASK = "run_ai_task"
    ADMIN = "admin"


ROLE_PERMISSIONS = {
    "admin": set(Permission),
    "dispatcher": {
        Permission.READ_ROUTES, Permission.READ_DRIVERS, Permission.READ_TRIPS,
        Permission.READ_ATTENDANCE, Permission.READ_SALARY, Permission.READ_SETTINGS,
        Permission.WRITE_ROUTES, Permission.WRITE_DRIVERS, Permission.SYNC_DATA,
        Permission.GENERATE_REPORT, Permission.SEND_TELEGRAM, Permission.RUN_AI_TASK,
    },
    "manager": {
        Permission.READ_ROUTES, Permission.READ_DRIVERS, Permission.READ_TRIPS,
        Permission.READ_ATTENDANCE, Permission.READ_SALARY,
        Permission.GENERATE_REPORT, Permission.RUN_AI_TASK,
    },
    "viewer": {
        Permission.READ_ROUTES, Permission.READ_DRIVERS, Permission.READ_TRIPS,
        Permission.READ_ATTENDANCE,
    },
}


class SecurityAgent(BaseAgent):
    """Security Agent — foydalanuvchi ruxsatlarini tekshiradi."""

    name = AgentType.SECURITY
    description = "Foydalanuvchi ruxsatlarini tekshiradi va saqlashni nazorat qiladi"

    def __init__(self, llm=None, tools=None, role: str = "viewer"):
        super().__init__(llm, tools)
        self._role = role
        self._allowed = ROLE_PERMISSIONS.get(
            self._role, ROLE_PERMISSIONS["viewer"]
        )

    async def run(self, context: AgentContext) -> AgentResult:
        self._start()
        try:
            action = context.params.get("action", "check")
            if action == "check":
                perm = context.params.get("permission", "")
                allowed = self._check(Permission(perm) if perm else None)
                result = {
                    "role": self._role,
                    "permission": perm,
                    "allowed": allowed,
                }
            elif action == "list_permissions":
                result = {
                    "role": self._role,
                    "permissions": [p.value for p in self._allowed],
                }
            elif action == "filter_data":
                data = context.params.get("data", {})
                result = self._filter(data)
            else:
                result = {"role": self._role}

            self._finish(True)
            return AgentResult(success=True, data=result)
        except Exception as exc:
            self._finish(False, str(exc))
            return AgentResult(success=False, error=str(exc))

    def _check(self, permission: Optional[Permission]) -> bool:
        if permission is None:
            return True
        return permission in self._allowed or Permission.ADMIN in self._allowed

    def _filter(self, data: dict) -> dict:
        if Permission.READ_SALARY not in self._allowed:
            data.pop("salary", None)
            data.pop("km_rate", None)
        if Permission.READ_SETTINGS not in self._allowed:
            data.pop("settings", None)
        return data


def get_user_role(user_id: int) -> str:
    """Telegram user ID dan rol aniqlash."""
    from ..config.settings import get_settings
    s = get_settings()
    uid_str = str(user_id)
    if uid_str in [str(x) for x in (s.tg_admin_ids or [])]:
        return "admin"
    if uid_str in [str(x) for x in (s.tg_dispatcher_ids or [])]:
        return "dispatcher"
    if uid_str in [str(x) for x in (s.tg_manager_ids or [])]:
        return "manager"
    return "viewer"
