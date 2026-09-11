"""Ollama LLM Provider (kelajak uchun)."""

from __future__ import annotations

from ..models import LLMResponse, LLMMessage
from . import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama provider — local LLM uchun.

    Hozircha placeholder. Ollama o'rnatilgandan keyin implement qilinadi.
    """

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url

    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        raise NotImplementedError("Ollama provider hali implement qilinmagan")

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        raise NotImplementedError("Ollama provider hali implement qilinmagan")

    def configured(self) -> bool:
        return False
