"""LLM-хелпер банка — прямой вызов OpenAI-совместимого API.

Использование в обработчике FastAPI:
    from src.llm import ask_llm, LLMError
    try:
        text = await ask_llm("Объясни клиенту отказ по кредиту простыми словами")
    except LLMError:
        text = "Решение принято, подробное объяснение временно недоступно."
"""
from __future__ import annotations

import os

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "30"))


class LLMError(RuntimeError):
    """LLM не сконфигурирован или провайдер не ответил."""


async def ask_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.4,
) -> str:
    """Задать вопрос модели и вернуть текст ответа. Бросает LLMError при сбое."""
    if not OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY не задан")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions", json=payload, headers=headers
            )
    except httpx.HTTPError as exc:
        raise LLMError(f"провайдер не ответил: {exc}") from exc
    if resp.status_code != 200:
        raise LLMError(f"провайдер вернул {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"неожиданный формат ответа: {data}") from exc
