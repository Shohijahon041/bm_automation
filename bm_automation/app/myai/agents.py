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

    # Agar None bo'lsa — barcha tool'lar ruxsat etilgan (master uchun).
    # Bo'sh set — hech qanday tool ruxsat etilmaydi.
    allowed_tools: set[str] | None = None

    def __init__(self, llm: LLMProvider, tools: Any = None):
        self.llm = llm
        self.tools = tools
        self.config = get_myai_config()
        self.logger = get_logger(f"myai.{self.name.value}")
        self._status = AgentStatus(agent=self.name)
        self._last_tool: tuple[str, str] | None = None
        self.source_note = ""
        self.allowed_tools: set[str] | None = None

    def list_allowed_tools(self) -> list[str]:
        """Agent ruxsat etilgan tool nomlari (None = hammasi)."""
        if self.allowed_tools is None:
            if self.tools is None:
                return []
            return self.tools.list_tools()
        return sorted(self.allowed_tools)

    def _check_tool_allowed(self, tool_name: str) -> None:
        """Tool agent uchun ruxsat etilganligini tekshiradi (least privilege)."""
        if self.allowed_tools is None:
            return
        if tool_name not in self.allowed_tools:
            allowed = ", ".join(sorted(self.allowed_tools))
            raise PermissionError(
                f"{self.name.value} agent {tool_name!r} tool'iga ruxsatga ega emas "
                f"(ruxsat etilgan: {allowed})")

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
        self._check_tool_allowed(tool_name)
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

    async def self_check(self, output_data: dict,
                         source: str = "") -> dict:
        """Tool natijasini invariantlar bilan tekshirish (deterministik).

        ReviewerAgent local tekshiruvi bilan aynan bir xil qoidalar
        (verify_invariants). Xato topilsa — natijaga `_self_check` bloki
        qo'shiladi va (ruxsat bo'lsa) vault'ga tasdiqlangan xato yoziladi.
        Tekshiruv natija qiymatini o'zgartirmaydi — faqat tekshiradi.
        """
        if not isinstance(output_data, dict):
            return output_data or {}
        try:
            from .reviewer import verify_invariants
            review = verify_invariants(output_data)
            if review.approved:
                return output_data
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Self-check xatosi: %s", exc)
            return output_data

        tag = source or self.name.value
        self.logger.warning("Self-check rad etdi (%s): %s",
                            tag, review.errors)
        output_data = dict(output_data)
        output_data["_self_check"] = {
            "ok": False,
            "source": tag,
            "errors": review.errors,
            "corrections": review.corrections,
        }
        await self._write_lesson(tag, review.errors, review.corrections)
        return output_data

    async def _write_lesson(self, source: str, errors: list[str],
                            corrections: list[str]) -> None:
        """Tasdiqlangan xatolarni vault'ga yozish (o'zini o'zi oshiradi).

        Faqat vault tool'ga ruxsat bo'lsa. Taxmin emas — aniqlangan
        invariant buzilishi (masalan accepted+not_accepted != total).
        """
        try:
            self._check_tool_allowed("vault")
        except PermissionError:
            return
        if self.tools is None:
            return
        try:
            tool = self.tools.get("vault")
            if tool is None:
                return
            title = f"{source} — aniqlangan xato (self-check)"
            content = (
                f"## Self-check: {source}\n\n"
                "Deterministik tekshiruv (verify_invariants) tasdiqladi "
                "(taxmin emas, kod natijasi):\n\n"
                + "\n".join(f"- {e}" for e in errors[:5])
                + "\n\nTuzatishlar:\n"
                + "\n".join(f"- {c}" for c in corrections[:5] or ["—"])
                + "\n\n> Avtomatik yozib qo'yildi — keyingi safar `vault` "
                  "qidirib, qayta xato qilmang."
            )
            result = await tool.execute(action="write",
                                        title=title, category="Xatolar",
                                        content=content)
            if result.get("ok"):
                self.logger.info("Self-check vault'ga yozdi: %s",
                                 result.get("path"))
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("Vault'ga yozish amalga oshmadi: %s", exc)

    async def _llm_tool_hint(self, task: str, choices: list[dict]) -> dict | None:
        """LLM yordamida maqsadli tool tanlash (optional, LLM sozlanganida).

        Deterministik natija bo'sh/xato bo'lganda agent LLM dan berilgan
        choices ichidan eng mos tool/action'ni tanlashni so'raydi.
        LLM yo'q yoki JSON noto'g'ri bo'lsa — None (xavfsiz fallback),
        deterministik yo'l o'zgarmaydi.

        choices: [{"tool": ..., "action": ..., "args": {...}, "desc": ...}]
        Qaytadi: tanlangan choice dict (allowed_tools bilan tekshirilgan)
        yoki None.
        """
        if not choices:
            return None
        try:
            if self.llm is None or not self.llm.configured():
                return None
        except Exception:  # noqa: BLE001
            return None

        import json as _json
        listed = "\n".join(
            f"- [{i}] tool={c.get('tool')}, action={c.get('action')}, "
            f"args={_json.dumps(c.get('args') or {}, ensure_ascii=False)}, "
            f"nima: {c.get('desc', '')}"
            for i, c in enumerate(choices)
        )
        system = (
            "Siz transport AI tizimi uchun tool-dispetchersiz. "
            "Foydalanuvchi topshirig'i va mavjud tool variantlariga qarab "
            "ENG MOS bittasini tanlaysiz. Faqat JSON qaytaring: "
            '{"tool": ..., "action": ..., "args": {...}}'
        )
        user = (
            f"Topshiriq: {task}\n\n"
            f"Mavjud variantlar:\n{listed}\n\n"
            "Eng mos variant indeksini aniq JOIN qiling va JSON qaytaring. "
            "Hech biri mos bo'lmasa bo'sh JSON {} qaytaring."
        )
        try:
            response = await self._call_llm(system, user, max_tokens=100)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("LLM tool hint xatosi: %s", exc)
            return None
        response = (response or "").strip()
        if response.startswith("```"):
            import re as _re
            m = _re.search(r"```(?:json)?\s*(.*?)```", response, _re.S)
            response = m.group(1).strip() if m else response
        try:
            data = _json.loads(response)
        except Exception:  # noqa: BLE001
            return None
        tool = (data.get("tool") or "").strip()
        action = (data.get("action") or "").strip()
        args = data.get("args") or {}
        for c in choices:
            if c.get("tool") == tool and c.get("action") == action:
                try:
                    self._check_tool_allowed(tool)
                except PermissionError:
                    return None
                merged = dict(c.get("args") or {})
                merged.update({k: v for k, v in args.items()
                               if k in (c.get("args") or {})})
                self.logger.info("LLM tool hint: %s.%s", tool, action)
                return {"tool": tool, "action": action,
                        "args": merged, "desc": c.get("desc", "")}
        return None

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
