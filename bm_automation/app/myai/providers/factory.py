"""LLM Provider factory — fallback zanjirini quradi."""

from __future__ import annotations

from ..config import get_myai_config
from . import LLMProvider
from .fallback import FallbackProvider


def _build_single(name: str) -> LLMProvider | None:
    """Bitta provider yaratadi (noto'g'ri nom → None)."""
    name = (name or "").strip().lower()
    config = get_myai_config()
    if name == "openrouter":
        from .openrouter import OpenRouterProvider
        return OpenRouterProvider()
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if name == "groq":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(
            name="groq", base_url=config.groq_base_url,
            api_key=config.groq_api_key, model=config.groq_model,
        )
    if name == "cerebras":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(
            name="cerebras", base_url=config.cerebras_base_url,
            api_key=config.cerebras_api_key, model=config.cerebras_model,
        )
    if name == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider()
    if name == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider()
    return None


def create_provider(provider_name: str | None = None) -> LLMProvider:
    """Bitta provider yaratish (factory pattern).

    Args:
        provider_name: Provider nomi ("openrouter", "gemini", "ollama",
                      "openai_compat"). None bo'lsa config'dan olinadi.

    Returns:
        LLMProvider: Sozlangan provider instance.
    """
    config = get_myai_config()
    name = (provider_name or config.llm_provider).lower().strip()
    provider = _build_single(name)
    if provider is None:
        raise ValueError(
            f"Noma'lum LLM provider: {name}. "
            f"Mavjud: openrouter, gemini, groq, cerebras, openai_compat, ollama"
        )
    return provider


def create_chain() -> FallbackProvider | None:
    """Config'dagi providerlar zanjirini quradi.

    Faqat configured bo'lgan providerlar zanjirga kiradi.
    Hech biri configured bo'lmasa None qaytadi (deterministik rejim).
    """
    config = get_myai_config()
    names = list(config.llm_providers)
    if config.llm_provider and config.llm_provider not in names:
        names.insert(0, config.llm_provider)
    providers: list[LLMProvider] = []
    seen: set[str] = set()
    for name in names:
        name = (name or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        p = _build_single(name)
        if p is None:
            continue
        try:
            if p.configured():
                providers.append(p)
        except Exception:  # noqa: BLE001 — provider init xatosini e'tiborsiz qoldirish
            continue
    if not providers:
        return None
    return FallbackProvider(providers)


def get_provider() -> LLMProvider:
    """Default provider ni qaytaradi (singleton, fallback zanjir)."""
    if not hasattr(get_provider, "_instance"):
        get_provider._instance = create_chain()
    inst = get_provider._instance
    if inst is None:
        raise RuntimeError(
            "Hech qanday LLM provider configured emas. .env faylida "
            "kalitlarni tekshiring (OPENROUTER_API_KEY, GEMINI_API_KEY, "
            "MYAI_GROQ_API_KEY, MYAI_CEREBRAS_API_KEY yoki "
            "MYAI_OPENAI_COMPAT_*)."
        )
    return inst