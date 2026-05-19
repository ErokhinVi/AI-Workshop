"""LLM-хелпер симулятора — прямой вызов OpenAI-совместимого Chat Completions.

Модель по умолчанию — gpt-4.1-mini: современная, дешёвая ($0.40/$1.60 за 1M
токенов) и держит temperature=0, то есть судейство остаётся детерминированным.
Организатор может через env OPENAI_MODEL поставить другую дешёвую модель
(например gpt-5.4-mini или gpt-5.4-nano) — слой устойчивости подстроит
параметры запроса под её требования.
"""
from __future__ import annotations

import os

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "30"))

# Порядок попыток запроса: (имя параметра лимита токенов, слать ли temperature).
# Сперва как принимает gpt-4.1-mini; затем придирки семейства GPT-5; в крайнем
# случае — без temperature (это и есть деградация детерминизма).
_ATTEMPTS = (
    ("max_tokens", True),
    ("max_completion_tokens", True),
    ("max_completion_tokens", False),
)

_last_degraded = False


class LLMError(RuntimeError):
    """LLM не сконфигурирована или провайдер не ответил."""


def last_call_degraded() -> bool:
    """True, если в последнем вызове пришлось снять temperature ради ответа.

    Сигнал организатору: модель из env не приняла temperature=0, судейство
    потеряло детерминизм. Такой раунд помечается на табло judge=llm-degraded.
    """
    return _last_degraded


def _extract(data: dict) -> str:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"неожиданный формат ответа: {data}") from exc


async def ask_llm(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.0,
) -> str:
    """Задать вопрос модели и вернуть текст ответа. Бросает LLMError при сбое.

    Устойчивость к разным семействам моделей: если провайдер вернул 400 с
    жалобой на параметр, запрос повторяется — сперва с max_completion_tokens
    вместо max_tokens (частая придирка GPT-5), и лишь в крайнем случае без
    temperature. Любая другая ошибка повтором не лечится — сразу LLMError.
    """
    global _last_degraded
    _last_degraded = False
    if not OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY не задан")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    last_err = "неизвестная ошибка"
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
        for token_param, with_temp in _ATTEMPTS:
            payload: dict = {
                "model": OPENAI_MODEL,
                "messages": messages,
                token_param: max_tokens,
            }
            if with_temp:
                payload["temperature"] = temperature
            try:
                resp = await client.post(
                    f"{OPENAI_BASE_URL}/chat/completions",
                    json=payload, headers=headers,
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"провайдер не ответил: {exc}") from exc
            if resp.status_code == 200:
                if not with_temp and temperature == 0.0:
                    _last_degraded = True
                return _extract(resp.json())
            last_err = f"{resp.status_code}: {resp.text[:300]}"
            if resp.status_code != 400:
                break  # не параметрическая ошибка — повторять бессмысленно
    raise LLMError(f"провайдер вернул {last_err}")
