"""OpenRouter LLM Provider."""

from __future__ import annotations

import time

import requests

from ..config import get_myai_config
from ..models import LLMResponse, LLMMessage
from . import LLMProvider


class OpenRouterProvider(LLMProvider):
    """OpenRouter API provider.

    Mavjud openrouter.py modulini provider pattern ga moslashtirish.
    Architecture provider-independent — keyinchalik Ollama, OpenAI
    yoki boshqa provider qo'shish oson.
    """

    name = "openrouter"

    def __init__(self):
        self._config = get_myai_config()
        self._base_url = self._config.llm_base_url.rstrip("/")
        self._session = requests.Session()
        self._last_stats: dict = {}

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._config.llm_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://bm.dtransport.uz",
            "X-Title": "MyAI Agent System",
        }

    def configured(self) -> bool:
        return bool(self._config.llm_api_key)

    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        """Bitta system + user prompt bilan completion."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._call_api(messages, **kwargs)

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """Multi-turn chat."""
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        return self._call_api(msg_dicts, **kwargs)

    def _call_api(self, messages: list[dict], **kwargs) -> LLMResponse:
        """OpenRouter API chaqirig'i."""
        model = kwargs.get("model", self._config.llm_model)
        temperature = kwargs.get("temperature", self._config.llm_temperature)
        max_tokens = kwargs.get("max_tokens", self._config.llm_max_tokens)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        start = time.monotonic()
        try:
            resp = self._session.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=60,
            )
            latency_ms = (time.monotonic() - start) * 1000

            if resp.status_code != 200:
                self._last_stats = {
                    "status": resp.status_code,
                    "latency_ms": latency_ms,
                    "error": resp.text[:200],
                }
                raise RuntimeError(
                    f"OpenRouter API xatosi {resp.status_code}: {resp.text[:200]}"
                )

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            self._last_stats = {
                "status": 200,
                "model": model,
                "tokens_prompt": usage.get("prompt_tokens", 0),
                "tokens_completion": usage.get("completion_tokens", 0),
                "tokens_total": usage.get("total_tokens", 0),
                "latency_ms": latency_ms,
            }

            return LLMResponse(
                content=content,
                model=model,
                tokens_used=usage.get("total_tokens", 0),
                latency_ms=latency_ms,
            )

        except requests.Timeout:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"status": "timeout", "latency_ms": latency_ms}
            raise RuntimeError("OpenRouter API timeout (60s)")
        except requests.ConnectionError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"status": "connection_error", "latency_ms": latency_ms}
            raise RuntimeError(f"OpenRouter API ulanib bo'lmadi: {exc}") from exc

    @property
    def last_stats(self) -> dict:
        """Oxirgi API chaqirig'i statistikasi."""
        return dict(self._last_stats)
