"""Общие фикстуры тестов симулятора."""
from __future__ import annotations

import pytest

from src import judge


@pytest.fixture(autouse=True)
def _fresh_judge_cache():
    """Судья запоминает вердикты по промпту: тесты с разными ответами LLM на
    один и тот же снимок не должны видеть чужой запомненный вердикт."""
    judge.clear_cache()
    yield
    judge.clear_cache()
