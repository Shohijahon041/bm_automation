"""LLM Provider — abstract interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import LLMResponse, LLMMessage


class LLMProvider(ABC):
    """LLM provider uchun abstrakt interface.

    Har bir provider (OpenRouter, Ollama, OpenAI, Gemini va h.k.)
    ushbu interface ni implement qilishi kerak.
    """

    name: str = "base"

    @abstractmethod
    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        """Bitta system + user prompt bilan completion.

        Args:
            system: System prompt (AI roli, cheklovlar)
            user: Foydalanuvchi so'rovi
            **kwargs: Qo'shimcha parametrlar (temperature, max_tokens va h.k.)

        Returns:
            LLMResponse: LLM natijasi
        """
        ...

    @abstractmethod
    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """Multi-turn chat.

        Args:
            messages: Xabarlar ro'yxati (system, user, assistant)
            **kwargs: Qo'shimcha parametrlar

        Returns:
            LLMResponse: LLM natijasi
        """
        ...

    @abstractmethod
    def configured(self) -> bool:
        """Provider sozlanganmi (API key mavjudmi)."""
        ...

    def health_check(self) -> bool:
        """Provider sog'lommi (ixtiyoriy)."""
        try:
            result = self.complete("Test", "OK", max_tokens=10)
            return bool(result.content)
        except Exception:  # noqa: BLE001
            return False
