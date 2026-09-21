"""MyAI configuration — env vars and settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MyAIConfig:
    """MyAI tizimi konfiguratsiyasi."""

    # LLM Provider
    llm_provider: str = field(
        default_factory=lambda: os.getenv("MYAI_LLM_PROVIDER", "openrouter").strip()
    )
    llm_model: str = field(
        default_factory=lambda: (
            os.getenv("MYAI_LLM_MODEL", "").strip()
            or os.getenv("OPENROUTER_MODEL", "").strip()
            or "openai/gpt-4o-mini"
        )
    )
    llm_api_key: str = field(
        default_factory=lambda: (
            os.getenv("MYAI_LLM_API_KEY", "").strip()
            or os.getenv("OPENROUTER_API_KEY", "").strip()
        )
    )
    llm_base_url: str = field(
        default_factory=lambda: (
            os.getenv("MYAI_LLM_BASE_URL", "").strip()
            or "https://openrouter.ai/api/v1"
        )
    )
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("MYAI_LLM_TEMPERATURE", "0.3"))
    )
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("MYAI_LLM_MAX_TOKENS", "2000"))
    )
    llm_timeout: float = field(
        default_factory=lambda: float(os.getenv("MYAI_LLM_TIMEOUT", "120"))
    )
    # Providerlar zanjiri (fallback) — vergul bilan ajratiladi.
    # Ulardan qaysi biri configured va ishlasa, o'sha ishlatiladi.
    llm_providers: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            p.strip().lower() for p in os.getenv(
                "MYAI_LLM_PROVIDERS",
                "openrouter,gemini,groq,cerebras,openai_compat,ollama"
            ).split(",") if p.strip()
        )
    )

    # Gemini
    gemini_api_key: str = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip()
    )
    gemini_model: str = field(
        default_factory=lambda: (
            os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.6-flash"
        )
    )

    # OpenAI-compatible boshqa bepul providerlar (legacy / umumiy)
    openai_compat_base_url: str = field(
        default_factory=lambda: os.getenv("MYAI_OPENAI_COMPAT_BASE_URL", "").strip()
    )
    openai_compat_api_key: str = field(
        default_factory=lambda: os.getenv("MYAI_OPENAI_COMPAT_API_KEY", "").strip()
    )
    openai_compat_model: str = field(
        default_factory=lambda: os.getenv("MYAI_OPENAI_COMPAT_MODEL", "").strip()
    )

    # Groq (bepul, OpenAI-mos API)
    groq_base_url: str = field(
        default_factory=lambda: os.getenv(
            "MYAI_GROQ_BASE_URL",
            "https://api.groq.com/openai/v1"
        ).strip()
    )
    groq_api_key: str = field(
        default_factory=lambda: os.getenv("MYAI_GROQ_API_KEY", "").strip()
    )
    groq_model: str = field(
        default_factory=lambda: os.getenv("MYAI_GROQ_MODEL", "").strip()
    )

    # Cerebras (bepul, OpenAI-mos API)
    cerebras_base_url: str = field(
        default_factory=lambda: os.getenv(
            "MYAI_CEREBRAS_BASE_URL",
            "https://api.cerebras.ai/v1"
        ).strip()
    )
    cerebras_api_key: str = field(
        default_factory=lambda: os.getenv("MYAI_CEREBRAS_API_KEY", "").strip()
    )
    cerebras_model: str = field(
        default_factory=lambda: os.getenv("MYAI_CEREBRAS_MODEL", "").strip()
    )

    # Task settings
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("MYAI_MAX_RETRIES", "3"))
    )
    task_timeout: int = field(
        default_factory=lambda: int(os.getenv("MYAI_TASK_TIMEOUT", "300"))
    )
    max_concurrent_tasks: int = field(
        default_factory=lambda: int(os.getenv("MYAI_MAX_CONCURRENT", "3"))
    )

    # Browser settings
    browser_headless: bool = field(
        default_factory=lambda: os.getenv("MYAI_BROWSER_HEADLESS", "true").lower() == "true"
    )
    browser_timeout: int = field(
        default_factory=lambda: int(os.getenv("MYAI_BROWSER_TIMEOUT", "60"))
    )

    # Data source: BM API ishlamasa (401/400) agentlar to'g'ridan-to'g'ri
    # PostgreSQL dan o'qiydi. db_primary=True bo'lsa BrowserAgent avval
    # bazadan, so'ng API/browser ga tushadi.
    db_primary: bool = field(
        default_factory=lambda: os.getenv("MYAI_DB_PRIMARY", "true").lower() == "true"
    )

    # Memory settings
    memory_short_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("MYAI_MEMORY_SHORT_TTL", "24"))
    )
    memory_long_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("MYAI_MEMORY_LONG_TTL", "720"))
    )

    # Security
    require_approval_for: list[str] = field(
        default_factory=lambda: os.getenv(
            "MYAI_REQUIRE_APPROVAL", "UPDATE_DATA,DELETE_DATA"
        ).split(",")
    )

    # Logging
    log_level: str = field(
        default_factory=lambda: os.getenv("MYAI_LOG_LEVEL", "INFO").strip()
    )

    @property
    def configured(self) -> bool:
        """LLM provider sozlanganmi."""
        return bool(self.llm_api_key)


_config: MyAIConfig | None = None


def get_myai_config() -> MyAIConfig:
    """MyAI konfiguratsiyasini qaytaradi (singleton)."""
    global _config
    if _config is None:
        _config = MyAIConfig()
    return _config
