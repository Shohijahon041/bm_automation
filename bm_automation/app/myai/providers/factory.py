"""LLM Provider factory."""

from __future__ import annotations

from ..config import get_myai_config
from . import LLMProvider


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Provider yaratish (factory pattern).

    Args:
        provider_name: Provider nomi ("openrouter", "ollama", "openai").
                      None bo'lsa config'dan olinadi.

    Returns:
        LLMProvider: Sozlangan provider instance
    """
    config = get_myai_config()
    name = (provider_name or config.llm_provider).lower().strip()

    if name == "openrouter":
        from .openrouter import OpenRouterProvider
        return OpenRouterProvider()
    elif name == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider()
    else:
        raise ValueError(
            f"Noma'lum LLM provider: {name}. "
            f"Mavjud: openrouter, ollama"
        )


def get_provider() -> LLMProvider:
    """Default provider ni qaytaradi (singleton)."""
    if not hasattr(get_provider, "_instance"):
        get_provider._instance = create_provider()
    return get_provider._instance
