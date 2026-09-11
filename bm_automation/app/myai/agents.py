"""Base Agent — barcha agentlar uchun asos."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from .config import get_myai_config
from .models import (
    AgentContext, AgentResult, AgentType, AgentStatus,
    LLMMessage, StepStatus,
)
from .providers import LLMProvider
from ..utils.logger import get_logger


class BaseAgent(ABC):
    """Barcha agentlar uchun asos sinfi.

    Har bir agent:
    - LLM provider dan foydalanadi
    - Tools registry orqali tool'larni chaqiradi
    - Structured logging qiladi
    - Xatolikni retry bilan boshqaradi
    """

    name: AgentType
    description: str = ""

    def __init__(self, llm: LLMProvider, tools: Any = None):
        self.llm = llm
        self.tools = tools
        self.config = get_myai_config()
        self.logger = get_logger(f"myai.{self.name.value}")
        self._status = AgentStatus(agent=self.name)
        self._last_tool: tuple[str, str] | None = None
        self.source_note = ""

    @property
    def last_tool(self) -> tuple[str, str] | None:
        """Oxirgi chaqirilgan tool (tool_name, action)."""
        return self._last_tool

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Asosiy bajarish metodi.

        Har bir agent o'z logikasini shu yerda implement qiladi.
        """
        ...

    async def _call_llm(self, system: str, user: str, **kwargs) -> str:
        """LLM chaqirig'i — xatolikni ushlaydi. Async-safe.

        Bekor qilish sezgirligi uchun chaqiruv asyncio.wait_for bilan
        cheklangan (llm_timeout). Vaqt tugasa TimeoutError ko'tariladi
        va step failed holatiga o'tadi.
        """
        import asyncio
        try:
            from .events import emit_thinking
            emit_thinking(self.name.value, action="LLM chaqirilmoqda...")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (thinking): %s", exc)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.llm.complete, system, user, **kwargs
                ),
                timeout=self.config.llm_timeout,
            )
            self.logger.debug(
                "LLM call: model=%s tokens=%d latency=%.0fms",
                result.model, result.tokens_used, result.latency_ms,
            )
            return result.content
        except asyncio.TimeoutError as exc:
            self.logger.error("LLM call timeout (%ss): %s",
                              self.config.llm_timeout, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error("LLM call xatosi: %s", exc)
            raise

    async def _call_llm_chat(
        self, messages: list[LLMMessage], **kwargs
    ) -> str:
        """Multi-turn LLM chaqirig'i — async-safe."""
        import asyncio
        try:
            from .events import emit_thinking
            emit_thinking(self.name.value, action="LLM chat chaqirilmoqda...")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (thinking): %s", exc)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.llm.chat, messages, **kwargs
                ),
                timeout=self.config.llm_timeout,
            )
            return result.content
        except asyncio.TimeoutError as exc:
            self.logger.error("LLM chat timeout (%ss): %s",
                              self.config.llm_timeout, exc)
            raise
        except Exception as exc:  # noqa: BLE001
            self.logger.error("LLM chat xatosi: %s", exc)
            raise

    async def _use_tool(self, tool_name: str, **kwargs) -> Any:
        """Tool chaqirig'i."""
        if self.tools is None:
            raise RuntimeError(f"Tools mavjud emas: {tool_name}")
        tool = self.tools.get(tool_name)
        if tool is None:
            raise RuntimeError(f"Tool topilmadi: {tool_name}")
        action = kwargs.get("action", "")
        try:
            from .events import emit_tool_call
            emit_tool_call(self.name.value, tool_name, action=action)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (tool_call): %s", exc)
        self.logger.debug("Tool call: %s.%s", tool_name, action)
        self._last_tool = (tool_name, action or "")
        result = await tool.execute(**kwargs)
        try:
            from .events import emit_tool_result
            emit_tool_result(self.name.value, tool_name, success=True)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (tool_result): %s", exc)
        return result

    def _start(self, task_id: str = "", action: str = "") -> None:
        """Agent ish boshladi."""
        self._status = AgentStatus(
            agent=self.name,
            status=StepStatus.RUNNING,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.logger.info("Agent ish boshladi: %s", self.name.value)
        try:
            from .events import emit
            emit(self.name.value, "working", task_id=task_id,
                 action=action or f"{self.name.value} ish boshladi")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (start): %s", exc)

    def _finish(self, success: bool = True, message: str = "",
                task_id: str = "") -> None:
        """Agent ish tugadi."""
        self._status.status = (
            StepStatus.COMPLETED if success else StepStatus.FAILED
        )
        self._status.completed_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._status.message = message
        self.logger.info(
            "Agent tugadi: %s success=%s msg=%s",
            self.name.value, success, message,
        )
        try:
            from .events import emit
            status = "completed" if success else "error"
            emit(self.name.value, status, task_id=task_id,
                 message=message or f"{self.name.value} tugadi")
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Event emit xatosi (finish): %s", exc)

    @property
    def status(self) -> AgentStatus:
        return self._status
