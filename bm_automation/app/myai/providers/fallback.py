"""Fallback LLM Provider — bir nechta providerdan qaysi biri ishlasa.

Providerlar zanjir bo'ylab sinab ko'riladi (config.llm_providers tartibi):
1. openrouter (free model)
2. gemini
3. openai_compat (Groq, Cerebras, ...)
4. ollama (local; hali implement emas)

Birinchi muvaffaqiyatli javob qaytariladi; xato bo'lsa keyingisiga o'tiladi.
Hammasi muvaffaqiyatsiz bo'lsa — oxirgi xato RuntimeError qilib ko'tariladi
(orchestrator deterministik rejimga tushadi).
"""

from __future__ import annotations

from ..models import LLMResponse, LLMMessage
from . import LLMProvider


class FallbackProvider(LLMProvider):
    """Providerlar zanjiri — qaysi biri ishlasa o'sha ishlatiladi."""

    name = "fallback"

    def __init__(self, providers: list[LLMProvider]):
        self._providers = providers
        self._last_stats: dict = {}
        self._active: str | None = None

    def providers(self) -> list[LLMProvider]:
        return list(self._providers)

    def configured(self) -> bool:
        return any(p.configured() for p in self._providers)

    def _run(self, fn, *args, **kwargs) -> LLMResponse:
        """Har bir providerga urinib ko'radi; birinchi muvaffaqiyatni qaytaradi."""
        last_exc: Exception | None = None
        tried: list[str] = []
        for p in self._providers:
            if not p.configured():
                continue
            try:
                result = fn(p, *args, **kwargs)
                self._active = p.name
                self._last_stats = dict(getattr(p, "last_stats", {}))
                return result
            except Exception as exc:  # noqa: BLE001
                tried.append(f"{p.name}: {exc}")
                last_exc = exc
        self._last_stats = {
            "status": "all_failed", "tried": tried, "error": last_exc,
        }
        raise RuntimeError(
            "Barcha LLM providerlar ishlamadi: " + " | ".join(tried)
        ) from last_exc

    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        return self._run(lambda p, *a, **k: p.complete(*a, **k),
                         system, user, **kwargs)

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        return self._run(lambda p, *a, **k: p.chat(*a, **k),
                         messages, **kwargs)

    @property
    def active(self) -> str | None:
        """Oxirgi muvaffaqiyatli ishlagan provider nomi."""
        return self._active

    @property
    def last_stats(self) -> dict:
        return dict(self._last_stats)