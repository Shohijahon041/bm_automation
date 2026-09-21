"""Gemini LLM Provider — Google Gemini API (bepul tier).

REST API orqali ishlaydi — qo'shimcha kutubxona talab qilmaydi.
`GEMINI_API_KEY` .env'da bo'lsa configured=True bo'ladi.
Chaqiruv xatolari/e'lon bo'lsa RuntimeError ko'tariladi — factory
boshqa providerga o'tadi (fallback zanjiri).
"""

from __future__ import annotations

import time

import requests

from ..config import get_myai_config
from ..models import LLMResponse, LLMMessage
from . import LLMProvider

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    """Google Gemini provider — bepul API kaliti bilan."""

    name = "gemini"

    def __init__(self):
        self._config = get_myai_config()
        self._session = requests.Session()
        self._last_stats: dict = {}

    def _model(self, kwargs: dict) -> str:
        return kwargs.get("model") or self._config.gemini_model

    def _headers(self) -> dict:
        return {
            "x-goog-api-key": self._config.gemini_api_key,
            "Content-Type": "application/json",
        }

    def configured(self) -> bool:
        return bool(self._config.gemini_api_key)

    def complete(self, system: str, user: str, **kwargs) -> LLMResponse:
        system_instruction = {"parts": [{"text": system or ""}]}
        contents = [{"role": "user", "parts": [{"text": user}]}]
        return self._call_api(contents, system_instruction, **kwargs)

    def chat(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        """Gemini chat — role: user/model (system → systemInstruction)."""
        sys_parts: list[str] = []
        contents: list[dict] = []
        last_role = None
        for m in messages:
            if m.role == "system":
                if m.content:
                    sys_parts.append(m.content)
                continue
            role = "model" if m.role == "assistant" else "user"
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append({"text": m.content})
            else:
                contents.append({"role": role, "parts": [{"text": m.content}]})
        if not contents:
            contents.append({"role": "user", "parts": [{"text": "OK"}]})
        system_instruction = (
            {"parts": [{"text": "\n".join(sys_parts)}]} if sys_parts else None
        )
        return self._call_api(contents, system_instruction, **kwargs)

    def _call_api(self, contents: list[dict], system_instruction: dict | None,
                  **kwargs) -> LLMResponse:
        model = self._model(kwargs)
        temperature = kwargs.get("temperature", self._config.llm_temperature)
        max_tokens = kwargs.get("max_tokens", self._config.llm_max_tokens)

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        url = f"{_API_BASE}/{model}:generateContent"
        start = time.monotonic()
        try:
            resp = self._session.post(
                url, json=payload, headers=self._headers(), timeout=60,
            )
            latency_ms = (time.monotonic() - start) * 1000

            if resp.status_code != 200:
                try:
                    msg = resp.json().get("error", {}).get("message", "") \
                        or resp.text[:200]
                except Exception:  # noqa: BLE001
                    msg = resp.text[:200]
                self._last_stats = {
                    "provider": "gemini", "status": resp.status_code,
                    "latency_ms": latency_ms, "error": msg,
                }
                raise RuntimeError(
                    f"Gemini API xatosi {resp.status_code}: {msg}"
                )

            data = resp.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            content = "".join(str(p.get("text", "")) for p in parts)
            usage = data.get("usageMetadata", {})
            tokens = (int(usage.get("totalTokenCount", 0) or 0)
                      or int(usage.get("candidatesTokenCount", 0) or 0))

            self._last_stats = {
                "provider": "gemini", "status": 200, "model": model,
                "latency_ms": latency_ms, "tokens_total": tokens,
            }
            return LLMResponse(
                content=content, model=model,
                tokens_used=tokens, latency_ms=latency_ms,
            )
        except requests.Timeout:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"provider": "gemini", "status": "timeout",
                                "latency_ms": latency_ms}
            raise RuntimeError("Gemini API timeout (60s)")
        except requests.ConnectionError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            self._last_stats = {"provider": "gemini", "status": "connection_error",
                                "latency_ms": latency_ms}
            raise RuntimeError(f"Gemini API ulanib bo'lmadi: {exc}") from exc

    @property
    def last_stats(self) -> dict:
        return dict(self._last_stats)