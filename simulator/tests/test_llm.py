"""Тесты LLM-хелпера: пустой ответ рассуждающей модели и поле reasoning."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src import llm


class _FakeClient:
    """httpx.AsyncClient, который запоминает тело запроса и отвечает content."""

    payloads: list[dict] = []
    content: object = "ok"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.payloads.append(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": _FakeClient.content}}]})


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "AsyncClient", _FakeClient)
    _FakeClient.payloads = []
    _FakeClient.content = "ok"
    return _FakeClient


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_answer_is_llm_error(fake_client, content):
    # модель потратила лимит на размышления: судья уйдёт в запасную формулу
    fake_client.content = content
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.ask_llm("вопрос", max_tokens=400))


def test_no_reasoning_field_by_default(fake_client, monkeypatch):
    monkeypatch.setattr(llm, "LLM_REASONING", "")
    assert asyncio.run(llm.ask_llm("вопрос", max_tokens=400)) == "ok"
    payload = fake_client.payloads[0]
    assert "reasoning" not in payload
    assert payload["max_tokens"] == 400


def test_reasoning_off(fake_client, monkeypatch):
    monkeypatch.setattr(llm, "LLM_REASONING", "off")
    asyncio.run(llm.ask_llm("вопрос", max_tokens=400))
    payload = fake_client.payloads[0]
    assert payload["reasoning"] == {"enabled": False}
    assert payload["max_tokens"] == 400


def test_reasoning_effort_adds_budget(fake_client, monkeypatch):
    monkeypatch.setattr(llm, "LLM_REASONING", "low")
    monkeypatch.setattr(llm, "LLM_REASONING_TOKENS", 3000)
    asyncio.run(llm.ask_llm("вопрос", max_tokens=400))
    payload = fake_client.payloads[0]
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_tokens"] == 3400
