"""Tools Registry — agent tool management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...utils.logger import get_logger

log = get_logger("myai.tools")


class BaseTool(ABC):
    """Barcha tool'lar uchun asos."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Tool ni bajarish."""
        ...

    def __repr__(self) -> str:
        return f"<Tool:{self.name}>"


class ToolRegistry:
    """Tool'lar ro'yxatini boshqarish."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Tool ni ro'yxatga olish."""
        self._tools[tool.name] = tool
        log.debug("Tool ro'yxatga olindi: %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        """Tool ni olish."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Barcha tool nomlari."""
        return list(self._tools.keys())

    async def execute(self, name: str, **kwargs) -> Any:
        """Tool ni bajarish."""
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Tool topilmadi: {name}")
        return await tool.execute(**kwargs)


# Global registry
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Global tool registry."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry) -> None:
    """Default tool'larni ro'yxatga olish."""
    from .browser import BrowserTool
    from .dtransport import DTransportTool
    from .excel import ExcelTool
    from .db import DBTool
    from .monthly import MonthlyTool
    from .telegram import TelegramTool
    from .calendar import CalendarTool
    from .search import SearchTool

    registry.register(BrowserTool())
    registry.register(DTransportTool())
    registry.register(ExcelTool())
    registry.register(DBTool())
    registry.register(MonthlyTool())
    registry.register(TelegramTool())
    registry.register(CalendarTool())
    registry.register(SearchTool())
