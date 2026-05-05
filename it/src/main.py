"""
Блок: IT / Платформа
Owner: Александр Ложечкин

Стартовая точка для воркшопа правления. На дне старта здесь:
 - LLM-прокси к внешнему провайдеру (`/llm/ask`),
 - простой in-memory audit (`/llm/audit`).

Соседние блоки (Risk, CIB, Finance) ходят сюда когда им нужно
обратиться к внешней модели — никто не должен дёргать OpenAI напрямую,
всё через платформу.

Что блок будет делать дальше (журнал событий, единый каталог
инструментов, шина событий) — решает Александр вместе с AI-помощником.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx


app = FastAPI(title="IT / Платформа", version="0.1.0")
BLOCK_NAME = "it"

# --- LLM provider configuration -------------------------------------------
# Поддерживаем OpenAI-совместимые провайдеры. Если ключ не задан —
# /llm/ask вернёт 503 со ссылкой на документацию.

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S   = float(os.environ.get("LLM_TIMEOUT_S", "30"))


_audit: list[dict[str, Any]] = []
MAX_AUDIT = 200


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "llm_configured": bool(OPENAI_API_KEY),
        "llm_model": OPENAI_MODEL if OPENAI_API_KEY else None,
        "audit_records": len(_audit),
    }


# --- LLM proxy ------------------------------------------------------------

class LLMRequest(BaseModel):
    prompt: str
    system: str | None = None
    max_tokens: int = 600
    temperature: float = 0.4
    requester: str | None = None  # имя блока-вызывающего (для audit)


@app.post("/llm/ask")
async def llm_ask(req: LLMRequest) -> dict:
    """Простой proxy к OpenAI Chat Completions.

    Соседние блоки используют это как `httpx.post(NEIGHBOR_IT + '/llm/ask',
    json={'prompt': '...', 'requester': 'risk'})`. Никаких ключей у соседей.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail=("LLM не сконфигурирован: задайте OPENAI_API_KEY в env. "
                    "На Render — Settings → Environment → секрет."),
        )

    messages: list[dict[str, str]] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})

    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                json=payload, headers=headers,
            )
    except httpx.HTTPError as e:
        _record_audit({
            "ts": int(time.time()),
            "requester": req.requester or "unknown",
            "ok": False,
            "error": f"network: {e}",
            "elapsed_ms": int((time.time() - started) * 1000),
            "prompt_len": len(req.prompt),
        })
        raise HTTPException(status_code=502, detail=f"провайдер не ответил: {e}")

    elapsed_ms = int((time.time() - started) * 1000)

    if resp.status_code != 200:
        body = resp.text[:600]
        _record_audit({
            "ts": int(time.time()),
            "requester": req.requester or "unknown",
            "ok": False,
            "error": f"http {resp.status_code}: {body}",
            "elapsed_ms": elapsed_ms,
            "prompt_len": len(req.prompt),
        })
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"провайдер вернул {resp.status_code}: {body}",
        )

    data = resp.json()
    answer = ""
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    usage = data.get("usage") or {}

    _record_audit({
        "ts": int(time.time()),
        "requester": req.requester or "unknown",
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "prompt_len": len(req.prompt),
        "answer_len": len(answer),
        "model": OPENAI_MODEL,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
    })

    return {
        "answer": answer,
        "model": OPENAI_MODEL,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
    }


def _record_audit(entry: dict[str, Any]) -> None:
    _audit.append(entry)
    if len(_audit) > MAX_AUDIT:
        del _audit[: len(_audit) - MAX_AUDIT]


@app.get("/llm/audit")
async def llm_audit(limit: int = 50, requester: str | None = None) -> dict:
    items = _audit
    if requester:
        items = [a for a in items if a.get("requester") == requester]
    return {
        "total": len(items),
        "items": items[-limit:],
    }


@app.get("/llm/status")
async def llm_status() -> dict:
    return {
        "configured": bool(OPENAI_API_KEY),
        "base_url": OPENAI_BASE_URL,
        "model": OPENAI_MODEL,
        "timeout_s": LLM_TIMEOUT_S,
    }
