"""OpenRouter orqali LLM chaqiruv (ixtiyoriy AI-yordamchi).

Token ishlatilishi va javob vaqti qayd qilinadi.
"""

from __future__ import annotations

import time

import requests

from ...config.settings import openrouter_settings

API = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT_S = 30.0

# So'nggi so'rov statistikasi (xotira ichida)
_last_stats: dict = {}


def configured() -> bool:
    """OpenRouter sozlanganmi? (kalit mavjud bo'lsa)."""
    return bool(openrouter_settings().get("api_key"))


def model() -> str:
    return openrouter_settings().get("model") or "openai/gpt-4o-mini"


def last_stats() -> dict:
    """So'nggi API so'rovi statistikasini qaytaradi."""
    return dict(_last_stats)


def complete(system: str, user: str, max_tokens: int = 600) -> str:
    """Bitta suhbat aylanishi: system + user -> javob matni."""
    return chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )


def chat(messages: list[dict], max_tokens: int = 600) -> str:
    """To'liq `messages` ro'yxati bilan chat (multi-turn uchun)."""
    global _last_stats
    s = openrouter_settings()
    api_key = s.get("api_key") or ""
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY ko'rsatilmagan")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if s.get("referer"):
        headers["HTTP-Referer"] = s["referer"]
    if s.get("title"):
        headers["X-Title"] = s["title"]
    payload = {
        "model": s.get("model") or "openai/gpt-4o-mini",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    t0 = time.monotonic()
    resp = requests.post(API, headers=headers, json=payload, timeout=_TIMEOUT_S)
    elapsed = round(time.monotonic() - t0, 2)
    if resp.status_code != 200:
        _last_stats = {
            "ok": False, "error": f"HTTP {resp.status_code}",
            "status": resp.status_code, "elapsed_s": elapsed,
            "model": payload["model"],
        }
        raise RuntimeError(
            f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        _last_stats = {"ok": False, "error": "Bo'sh javob", "elapsed_s": elapsed}
        raise RuntimeError("OpenRouter bo'sh javob qaytardi")
    usage = data.get("usage") or {}
    _last_stats = {
        "ok": True,
        "model": data.get("model") or payload["model"],
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "elapsed_s": elapsed,
        "status": resp.status_code,
    }
    return (choices[0].get("message") or {}).get("content") or ""
