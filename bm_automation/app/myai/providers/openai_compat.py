"""OpenAI-compatible LLM Provider — Groq, Cerebras, Together, LM Studio va h.k.

Bepul yoki shaxsiy OpenAI-mos API'lar uchun generic provider.
Bitta instance bitta endpointdir (name, base_url, api_key, model).

.env'da (legacy generic):
    MYAI_OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
    MYAI_OPENAI_COMPAT_API_KEY=xxxx
    MYAI_OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile

Mashhur bepul endpointlar uchun alohida bloklar:
    # Groq
    MYAI_GROQ_API_KEY=gsk_...
    MYAI_GROQ_MODEL=qwen/qwen3.8-27b
    # Cerebras
    MYAI_CEREBRAS_API_KEY=csk-...
    MYAI_CEREBRAS_MODEL=qwen-3.8-27b

Kalit/URL berilmasa configured=False — zanjirdan chiqib ketadi.
"""

from __future__ import annotations

import time

import requests

from ..config import get_myai_config
from ..models import LLMResponse, LLMMessage
from . import LLMProvider


class OpenAICompatProvider(LLMProvider):
    """OpenAI-compatible REST provider (generic endpoint)."""

    name = "openai_compat"

    def __init__(self, name: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, model: str | None = None):
        self._config = get_myai_config()
        if name:
            self.name = name
        self._base_url_override = (base_url or "").strip()
        self._api_key_override = (api_key or "").strip()
        self._model_override = (model or "").strip()
        self._session = requests.Session()
        self._last_stats: dict = {}

    def _base_url(self) -> str:
        return self._base_url_override or \
            self._config.openai_compat_base_url or \
            "http://localhost:11434/v1"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        key = self._api_key_override or self._config.openai_compat_api_key
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def configured(self) -> bool:
        base = self._base_url_override or self._config.openai_compat_base_url or ""
        return bool(
            base
            and (self._api_key_override or self._config.openai_compat_api_key
                 or self._model_override or self._config.openai_compat_model)
        )

    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._call_api(messages, **kwargs)

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        return self._call_api(msg_dicts, **kwargs)

    def _call_api(self, messages: list[dict], **kwargs) -> LLMResponse:
        model = kwargs.get("model") or self._model_override or \
            self._config.openai_compat_model or "gpt-4o-mini"
        temperature = kwargs.get("temperature", self._config.llm_temperature)
        max_tokens = kwargs.get("max_tokens", self._config.llm_max_tokens)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = f"{self._base_url()}/chat/completions"
        start = time.monotonic()
        try:
            resp = self._session.post(
                url, json=payload, headers=self._headers(), timeout=60,
            )
            latency_ms = (time.monotonic() - start) * 1000

            if resp.status_code != 200:
                self._last_stats = {
                    "provider": "openai_compat", "status": resp.status_code,
                    "latency_ms": latency_ms, "error": resp.text[:200],
                }
                raise RuntimeError(
                    f"OpenAI-compat API xatosi {resp.status_code}: {resp.text[:200]}"
                )

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            self._last_stats = {
                "provider": "openai_compat", "status": 200, "model": model,
                "latency_ms": latency_ms,
                "tokens_total": usage.get("total_tokens", 0),
            }
            return LLMResponse(
                content=content, model=model,
                tokens_used=usage.get("total_tokens", 0),
                latency_ms=latency_ms,
            )
        except requests.Timeout:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"provider": "openai_compat", "status": "timeout",
                                "latency_ms": latency_ms}
            raise RuntimeError("OpenAI-compat API timeout (60s)")
        except requests.ConnectionError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"provider": "openai_compat", "status": "connection_error",
                                "latency_ms": latency_ms}
            raise RuntimeError(f"OpenAI-compat API ulanib bo'lmadi: {exc}") from exc

    @property
    def last_stats(self) -> dict:
        return dict(self._last_stats)