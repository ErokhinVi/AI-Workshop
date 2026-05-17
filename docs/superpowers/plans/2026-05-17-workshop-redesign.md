# Редизайн воркшопа: два банка-команды + симулятор клиентов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переписать монорепо из 6 блоков в формат «два идентичных банка-команды + симулятор клиентов», где симулятор после каждого деплоя оценивает живой банк и двигает клиентскую базу с обоснованием.

**Architecture:** Три сервиса на Render — `team_a` и `team_b` (копии исходного retail-банка, in-memory на seed) и `simulator` (FastAPI + Postgres). Симулятор pull-моделью опрашивает `/health` банков, на новый git-коммит снимает probe-снапшот, оценивает рубрикой через LLM-судью (со скриптовым fallback) и детерминированной формулой считает прирост/отток клиентов. Табло встроено в симулятор.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, httpx, asyncpg, Postgres, Docker, Render Blueprint, GitHub Actions, pytest.

**Спека:** `docs/superpowers/specs/2026-05-17-workshop-redesign-design.md`

**Соглашение по коммитам:** каждый коммит заканчивается строкой
`Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
Работа идёт в worktree-ветке `worktree-workshop-redesign`.

---

## Структура файлов

**Создаются:**
- `team_a/`, `team_b/` — банки команд (копии `retail/`): `Dockerfile`, `pyproject.toml`, `src/main.py`, `src/db.py`, `src/llm.py`, `src/static/index.html`
- `seed/` — `clients.jsonl`, `transactions.jsonl`, `credit_history.jsonl`, `make_seed.py`, `README.md`
- `simulator/Dockerfile`, `simulator/pyproject.toml`
- `simulator/src/main.py` — FastAPI, эндпоинты, pull-loop
- `simulator/src/scoring.py` — чистая формула баллы→клиенты
- `simulator/src/judge.py` — LLM-судья + парсинг + скриптовый fallback
- `simulator/src/probe.py` — снятие состояния банка
- `simulator/src/db.py` — Postgres
- `simulator/src/llm.py` — хелпер OpenAI
- `simulator/src/static/index.html` — табло
- `simulator/tests/test_scoring.py`, `test_judge.py`
- `tasks/task_01_credit.md`, `tasks/task_02_invest.md`
- `.claude/templates/settings-team-a.json`, `settings-team-b.json`

**Переписываются:** `render.yaml`, `.github/workflows/deploy-render.yml`, `docker-compose.yml`, `CLAUDE.md`, `README.md`, `TEAM.md`, `RULES.md`, `ORGANIZER.md`, `DEPLOY.md`, `tools/cowork-onboard.py`, `tools/bootstrap/raif-workshop-setup.cmd`, `tools/bootstrap/raif-workshop-setup.applescript`

**Удаляются:** `ceo/ cib/ retail/ it/ finance/ risk/`, `INBOX/`, `contracts/`, `cases/`, `dashboard/`, `.claude/templates/settings-{ceo,cib,retail,it,finance,risk}.json`

---

## Фаза 0 — Хирургия репозитория

### Task 0.1: Создать `team_a/` и `seed/` из исходников

**Files:**
- Create: `team_a/` (копия `retail/`), `seed/` (из `cases/_seed/`)

- [ ] **Step 1: Скопировать retail в team_a и seed**

```bash
cd <repo-root>
cp -r retail team_a
mkdir -p seed
cp cases/_seed/clients.jsonl cases/_seed/transactions.jsonl \
   cases/_seed/credit_history.jsonl cases/_seed/make_seed.py \
   cases/_seed/README.md seed/
```

- [ ] **Step 2: Удалить из team_a файлы старого формата**

```bash
rm -f team_a/CLAUDE.md team_a/NEIGHBOR_AGENTS.md team_a/SESSION.md \
      team_a/state.md team_a/ARCHITECTURE.md team_a/KNOWLEDGE.md
```

- [ ] **Step 3: Проверить состав team_a**

Run: `ls team_a team_a/src`
Expected: `Dockerfile pyproject.toml src/` и `src/`: `main.py db.py static/`

- [ ] **Step 4: Commit**

```bash
git add team_a seed
git commit -m "feat: team_a и seed из исходного retail-банка"
```

### Task 0.2: Удалить старые блоки и каталоги

**Files:**
- Delete: `ceo/ cib/ retail/ it/ finance/ risk/ INBOX/ contracts/ cases/ dashboard/` и старые шаблоны permissions

- [ ] **Step 1: Удалить**

```bash
cd <repo-root>
rm -rf ceo cib retail it finance risk INBOX contracts cases dashboard
rm -f .claude/templates/settings-ceo.json .claude/templates/settings-cib.json \
      .claude/templates/settings-retail.json .claude/templates/settings-it.json \
      .claude/templates/settings-finance.json .claude/templates/settings-risk.json
```

- [ ] **Step 2: Проверить, что team_a, seed, tools, .github, docs на месте**

Run: `ls`
Expected: среди прочего `team_a seed tools .github docs render.yaml`

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: удалены 6 блоков, INBOX, contracts, cases, dashboard старого формата"
```

---

## Фаза 1 — Банк команды

### Task 1.1: Поправить поиск seed и `/health` в `team_a/src/main.py`

**Files:**
- Modify: `team_a/src/main.py:30` (BLOCK_NAME), `:33-43` (`_find_seed_dir`), `:124-142` (`/health`)

- [ ] **Step 1: Заменить BLOCK_NAME на TEAM/COMMIT-константы**

Заменить строку `BLOCK_NAME = "retail"` на:

```python
TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
```

- [ ] **Step 2: Переписать `_find_seed_dir` под каталог `seed/`**

Заменить функцию `_find_seed_dir` целиком на:

```python
def _find_seed_dir() -> Path | None:
    """Ищем seed/ — работает и в Docker (/app/seed), и локально."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "seed",                                  # Docker: /app/seed
        here.parents[2] / "seed" if len(here.parents) >= 3 else None,  # локально: <repo>/seed
        here.parents[3] / "seed" if len(here.parents) >= 4 else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None
```

- [ ] **Step 3: Добавить `commit` и `team` в оба ответа `/health`**

В функции `health()` в обоих `return`-словарях (ветка postgres и ветка memory)
заменить `"status": "ok", "block": BLOCK_NAME,` на:

```python
        "status": "ok", "team": TEAM_NAME, "commit": COMMIT,
```

- [ ] **Step 4: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('team_a/src/main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add team_a/src/main.py
git commit -m "feat(team_a): seed/-каталог и git-коммит в /health"
```

### Task 1.2: Добавить хелпер `team_a/src/llm.py`

**Files:**
- Create: `team_a/src/llm.py`

- [ ] **Step 1: Создать файл**

```python
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
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('team_a/src/llm.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add team_a/src/llm.py
git commit -m "feat(team_a): хелпер ask_llm для вызова модели"
```

### Task 1.3: Переписать `team_a/Dockerfile`

**Files:**
- Modify: `team_a/Dockerfile`

- [ ] **Step 1: Заменить файл целиком**

```dockerfile
# Build context = корень монорепо (см. docker-compose.yml и render.yaml).
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic[email]>=2.7" \
    "jinja2>=3.1" \
    "httpx>=0.27" \
    "python-multipart>=0.0.9" \
    "asyncpg>=0.29"

COPY seed /app/seed
COPY team_a/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
```

- [ ] **Step 2: Commit**

```bash
git add team_a/Dockerfile
git commit -m "feat(team_a): Dockerfile под seed/ и новый build-context"
```

### Task 1.4: Создать `team_b/` идентичной копией `team_a/` и проверить сборку

**Files:**
- Create: `team_b/` (копия `team_a/`)

- [ ] **Step 1: Скопировать team_a в team_b**

```bash
cd <repo-root>
rm -rf team_b
cp -r team_a team_b
```

- [ ] **Step 2: Заменить путь src в team_b/Dockerfile**

В `team_b/Dockerfile` заменить `COPY team_a/src /app/src` на `COPY team_b/src /app/src`.
(Это единственное отличие двух Dockerfile — имя команды задаётся переменной `TEAM_NAME`, код идентичен.)

- [ ] **Step 3: Собрать оба банка**

Run: `docker build -f team_a/Dockerfile -t raif-team-a . && docker build -f team_b/Dockerfile -t raif-team-b .`
Expected: обе сборки завершаются `naming to ... done`

- [ ] **Step 4: Запустить team_a и проверить /health**

```bash
docker run -d --rm -p 8001:8020 -e TEAM_NAME=team_a --name t-a raif-team-a
sleep 4
curl -s localhost:8001/health
docker stop t-a
```
Expected: JSON с `"team":"team_a"`, `"commit":"local"`, `"clients_loaded":500`

- [ ] **Step 5: Commit**

```bash
git add team_b
git commit -m "feat: team_b — идентичная копия team_a (стартовый банк команды)"
```

---

## Фаза 2 — Симулятор: чистая логика (TDD)

### Task 2.1: `simulator/src/scoring.py` — формула баллы→клиенты

**Files:**
- Create: `simulator/src/scoring.py`, `simulator/tests/test_scoring.py`, `simulator/pyproject.toml`

- [ ] **Step 1: Создать `simulator/pyproject.toml`**

```toml
[project]
name = "raif-simulator"
version = "0.1.0"
description = "Симулятор клиентов AI-воркшопа"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
    "asyncpg>=0.29",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 2: Написать падающий тест `simulator/tests/test_scoring.py`**

```python
from src.scoring import compute_round, compute_unreachable, rubric_total


def test_rubric_total_clamps():
    assert rubric_total([2, 2, 2, 2, 2, 2]) == 12
    assert rubric_total([2, 2, 2, 2, 2, 3]) == 12  # сверху зажато
    assert rubric_total([-5, 0, 0, 0, 0, 0]) == 0  # снизу зажато


def test_no_change_gives_zero_delta():
    r = compute_round(s_now=8, s_prev=8, s_base=2, client_base=650)
    assert r == {"delta": 0, "client_base": 650, "target": 650, "changed": False}


def test_full_completion_from_baseline():
    # S 2 -> 12: improvement = 10/12, target = round(500 * (1 + 0.6*0.8333)) = 750
    r = compute_round(s_now=12, s_prev=2, s_base=2, client_base=500)
    assert r["target"] == 750
    assert r["delta"] == 250
    assert r["client_base"] == 750
    assert r["changed"] is True


def test_partial_progress():
    # S 2 -> 8: improvement = 6/12 = 0.5, target = round(500 * 1.3) = 650
    r = compute_round(s_now=8, s_prev=2, s_base=2, client_base=500)
    assert r["target"] == 650
    assert r["delta"] == 150


def test_regression_below_baseline_loses_clients():
    # S 8 -> 0: improvement = (0-2)/12 = -0.1667, target = round(500 * 0.9) = 450
    r = compute_round(s_now=0, s_prev=8, s_base=2, client_base=650)
    assert r["target"] == 450
    assert r["delta"] == -200


def test_unreachable_bank_drops_base():
    r = compute_unreachable(client_base=700)
    assert r["target"] == 560  # round(700 * 0.8)
    assert r["delta"] == -140
    assert r["changed"] is True
```

- [ ] **Step 3: Запустить тест — убедиться, что падает**

Run: `cd simulator && python3 -m pytest tests/test_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.scoring'`

- [ ] **Step 4: Реализовать `simulator/src/scoring.py`**

```python
"""Чистая логика подсчёта клиентской базы. Без I/O — легко тестируется.

Параметры тюнятся; значения по умолчанию согласованы со спекой.
"""
from __future__ import annotations

B0 = 500          # стартовая клиентская база каждой команды
GAIN = 0.6        # сила влияния качества банка на базу
RUBRIC_MAX = 12   # 6 критериев по 2 балла
FLOOR = 50        # база не опускается ниже этого значения
UNREACHABLE_FACTOR = 0.8  # множитель базы, когда банк недоступен


def rubric_total(scores: list[int]) -> int:
    """Сумма баллов рубрики, зажатая в [0, RUBRIC_MAX]."""
    return max(0, min(RUBRIC_MAX, sum(scores)))


def compute_round(s_now: int, s_prev: int, s_base: int, client_base: int) -> dict:
    """Один раунд оценки команды.

    Возвращает {delta, client_base, target, changed}. Если рубрика не
    изменилась (s_now == s_prev) — дельта 0, база не двигается.
    """
    if s_now == s_prev:
        return {"delta": 0, "client_base": client_base,
                "target": client_base, "changed": False}
    improvement = (s_now - s_base) / RUBRIC_MAX
    improvement = max(-1.0, min(1.0, improvement))
    target = max(FLOOR, round(B0 * (1 + GAIN * improvement)))
    return {"delta": target - client_base, "client_base": target,
            "target": target, "changed": True}


def compute_unreachable(client_base: int) -> dict:
    """Банк не открывается — клиенты не могут войти, база падает."""
    target = max(FLOOR, round(client_base * UNREACHABLE_FACTOR))
    return {"delta": target - client_base, "client_base": target,
            "target": target, "changed": True}
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `cd simulator && python3 -m pytest tests/test_scoring.py -q`
Expected: PASS — 6 passed

- [ ] **Step 6: Commit**

```bash
git add simulator/pyproject.toml simulator/src/scoring.py simulator/tests/test_scoring.py
git commit -m "feat(simulator): чистая формула баллы→клиенты с тестами"
```

### Task 2.2: `simulator/src/judge.py` — рубрика, fallback, парсинг (TDD)

**Files:**
- Create: `simulator/src/judge.py`, `simulator/tests/test_judge.py`

- [ ] **Step 1: Написать падающий тест `simulator/tests/test_judge.py`**

```python
from src.judge import RUBRIC_CRITERIA, fallback_rubric, parse_judge_response


def _baseline_snapshot():
    return {
        "reachable": True,
        "checks": {
            "credit_mentioned_in_ui": False,
            "credit_apply_status": 501,
            "credit_apply_latency_ms": 120,
            "decision_is_discriminating": False,
            "credit_response_has_explanation": False,
            "transfer_regression_ok": True,
        },
    }


def _done_snapshot():
    return {
        "reachable": True,
        "checks": {
            "credit_mentioned_in_ui": True,
            "credit_apply_status": 200,
            "credit_apply_latency_ms": 1500,
            "decision_is_discriminating": True,
            "credit_response_has_explanation": True,
            "transfer_regression_ok": True,
        },
    }


def test_rubric_has_six_criteria():
    assert len(RUBRIC_CRITERIA) == 6


def test_fallback_baseline_scores_two():
    scores, reason = fallback_rubric(_baseline_snapshot())
    assert scores == [0, 0, 0, 0, 0, 2]
    assert isinstance(reason, str) and reason


def test_fallback_done_scores_twelve():
    scores, reason = fallback_rubric(_done_snapshot())
    assert scores == [2, 2, 2, 2, 2, 2]


def test_fallback_slow_decision_partial_c3():
    snap = _done_snapshot()
    snap["checks"]["credit_apply_latency_ms"] = 7000
    scores, _ = fallback_rubric(snap)
    assert scores[2] == 1  # C3: 5-10 c -> 1 балл


def test_parse_judge_response_valid():
    raw = ('{"team_a": {"scores": [2,2,2,0,0,2], "reason": "ок"}, '
           '"team_b": {"scores": [0,0,0,0,0,2], "reason": "пусто"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"] == [2, 2, 2, 0, 0, 2]
    assert parsed["team_b"]["reason"] == "пусто"


def test_parse_judge_response_with_code_fence():
    raw = '```json\n{"team_a": {"scores": [1,1,1,1,1,1], "reason": "x"}}\n```'
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"] == [1, 1, 1, 1, 1, 1]


def test_parse_judge_response_garbage_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("это не json")
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd simulator && python3 -m pytest tests/test_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.judge'`

- [ ] **Step 3: Реализовать `simulator/src/judge.py` (часть 1 — рубрика, fallback, парсинг)**

```python
"""Судья: рубрика, скриптовый fallback и парсинг ответа LLM.

LLM-вызов — в judge_round() ниже; чистые функции (fallback_rubric,
parse_judge_response) вынесены отдельно и покрыты тестами.
"""
from __future__ import annotations

import json

RUBRIC_CRITERIA: list[str] = [
    "C1. Вкладка «Кредиты» присутствует в интерфейсе банка.",
    "C2. Заявка на кредит отправляется, сервер отвечает без ошибки (HTTP 200).",
    "C3. Решение по заявке синхронное — приходит быстрее 5 секунд.",
    "C4. Решение опирается на данные клиента: сильный и слабый заявители "
    "получают разные вердикты.",
    "C5. Отказ сопровождается осмысленным человеческим объяснением.",
    "C6. Нет регрессии: вкладка «Переводы» и сам перевод по-прежнему работают.",
]


def fallback_rubric(snapshot: dict) -> tuple[list[int], str]:
    """Механически вывести 6 баллов из probe-снапшота, без LLM.

    Возвращает (scores, reason). Применяется, когда LLM недоступен.
    """
    c = snapshot.get("checks", {})
    status = c.get("credit_apply_status")
    latency = c.get("credit_apply_latency_ms") or 0
    apply_ok = status == 200

    c1 = 2 if c.get("credit_mentioned_in_ui") else 0
    c2 = 2 if apply_ok else 0
    if apply_ok and latency < 5000:
        c3 = 2
    elif apply_ok and latency < 10000:
        c3 = 1
    else:
        c3 = 0
    c4 = 2 if c.get("decision_is_discriminating") else 0
    c5 = 2 if c.get("credit_response_has_explanation") else 0
    c6 = 2 if c.get("transfer_regression_ok") else 0
    scores = [c1, c2, c3, c4, c5, c6]

    parts: list[str] = []
    parts.append("вкладка кредитов есть" if c1 else "вкладки кредитов нет")
    if c2:
        parts.append(f"заявка обрабатывается за {latency} мс")
    else:
        parts.append("заявка не обрабатывается")
    if c5:
        parts.append("отказ объясняют по-человечески")
    if not c6:
        parts.append("переводы сломаны")
    reason = "Автооценка: " + ", ".join(parts) + "."
    return scores, reason


def parse_judge_response(raw: str) -> dict:
    """Разобрать JSON-ответ LLM-судьи. Бросает ValueError при мусоре."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"в ответе судьи нет JSON-объекта: {raw[:200]}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ судьи — не валидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("ответ судьи — не объект")
    return data
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `cd simulator && python3 -m pytest tests/test_judge.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/src/judge.py simulator/tests/test_judge.py
git commit -m "feat(simulator): рубрика, скриптовый fallback и парсинг судьи с тестами"
```

### Task 2.3: `simulator/src/llm.py` и LLM-вызов судьи

**Files:**
- Create: `simulator/src/llm.py`
- Modify: `simulator/src/judge.py` (добавить `judge_round`)

- [ ] **Step 1: Создать `simulator/src/llm.py`**

Содержимое — идентично `team_a/src/llm.py` из Task 1.2 (тот же модуль `ask_llm`/`LLMError`):

```python
"""LLM-хелпер симулятора — прямой вызов OpenAI-совместимого API."""
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
    temperature: float = 0.0,
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
```

- [ ] **Step 2: Добавить LLM-вызов судьи в `simulator/src/judge.py`**

В начало файла, сразу после строки `import json`, добавить импорт:

```python
from src.llm import LLMError, ask_llm
```

В конец файла добавить `_JUDGE_SYSTEM`, `_build_judge_prompt`, `judge_round`:

```python
_JUDGE_SYSTEM = (
    "Ты — рыночный аналитик розничного банка. Тебе дают результаты "
    "технической проверки двух версий банковского приложения (команда A и "
    "команда B), которые независимо решают одну задачу — добавить функцию "
    "«Кредиты». Оцени каждую команду по 6 критериям, каждый строго 0, 1 или "
    "2 балла. Будь одинаково строг к обеим. Верни СТРОГО JSON без пояснений."
)


def _build_judge_prompt(snap_a: dict, snap_b: dict) -> str:
    criteria = "\n".join(RUBRIC_CRITERIA)
    return (
        f"Критерии оценки (каждый 0, 1 или 2 балла):\n{criteria}\n\n"
        f"Проверка команды A:\n{json.dumps(snap_a.get('checks', {}), ensure_ascii=False)}\n"
        f"reachable={snap_a.get('reachable')}\n\n"
        f"Проверка команды B:\n{json.dumps(snap_b.get('checks', {}), ensure_ascii=False)}\n"
        f"reachable={snap_b.get('reachable')}\n\n"
        'Верни JSON вида: {"team_a": {"scores": [c1,c2,c3,c4,c5,c6], '
        '"reason": "одно-два предложения по-русски, привязанные к фактам"}, '
        '"team_b": {"scores": [...], "reason": "..."}}'
    )


async def judge_round(snap_a: dict, snap_b: dict) -> dict:
    """Оценить обе команды одним вызовом LLM.

    Возвращает {"team_a": {"scores": [...], "reason": str, "judge": "llm"|"fallback"},
    "team_b": {...}}. При сбое LLM каждая команда получает скриптовый fallback.
    """
    result: dict[str, dict] = {}
    try:
        raw = await ask_llm(
            _build_judge_prompt(snap_a, snap_b),
            system=_JUDGE_SYSTEM,
            max_tokens=500,
            temperature=0.0,
        )
        parsed = parse_judge_response(raw)
        for team in ("team_a", "team_b"):
            block = parsed.get(team) or {}
            scores = block.get("scores")
            if not (isinstance(scores, list) and len(scores) == 6):
                raise ValueError(f"судья не дал 6 баллов для {team}")
            result[team] = {
                "scores": [int(x) for x in scores],
                "reason": str(block.get("reason", "")).strip() or "(без обоснования)",
                "judge": "llm",
            }
        return result
    except (LLMError, ValueError, KeyError, TypeError):
        for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
            scores, reason = fallback_rubric(snap)
            result[team] = {"scores": scores, "reason": reason, "judge": "fallback"}
        return result
```

- [ ] **Step 3: Проверить, что тесты Task 2.2 всё ещё проходят**

Run: `cd simulator && python3 -m pytest tests/ -q`
Expected: PASS — все тесты зелёные

- [ ] **Step 4: Commit**

```bash
git add simulator/src/llm.py simulator/src/judge.py
git commit -m "feat(simulator): LLM-судья одним вызовом для обеих команд"
```

---

## Фаза 3 — Симулятор: probe и БД

### Task 3.1: `simulator/src/probe.py` — снятие состояния банка

**Files:**
- Create: `simulator/src/probe.py`

- [ ] **Step 1: Создать файл**

```python
"""Probe — снятие состояния живого банка. Закрытый список проверок P1–P6.

Фиксированные клиенты выбраны из seed/clients.jsonl:
  STRONG_APPLICANT — премиум, высокий доход, без просрочек;
  WEAK_APPLICANT   — масса, низкий доход, история просрочек.
"""
from __future__ import annotations

import json
import time

import httpx

STRONG_APPLICANT = "c-01394"  # София Лебедева, premium, доход 589 545 ₽
WEAK_APPLICANT = "c-01434"    # Карина Воробьёва, mass, доход 40 358 ₽, просрочки

PROBE_TIMEOUT_S = 20.0

_APPROVE_WORDS = ("approv", "одобр", "выдан", "accept", "положительн")
_REJECT_WORDS = ("reject", "отказ", "decline", "denied", "отрицательн")


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"_list": data}
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_decision(body: dict) -> str | None:
    """Достать вердикт из ответа банка независимо от формы. → approved|rejected|None."""
    if not isinstance(body, dict):
        return None
    for key in ("decision", "status", "verdict", "result", "approved"):
        if key in body:
            v = str(body[key]).lower()
            if v in ("true", "ok") or any(w in v for w in _APPROVE_WORDS):
                return "approved"
            if v == "false" or any(w in v for w in _REJECT_WORDS):
                return "rejected"
    blob = json.dumps(body, ensure_ascii=False).lower()
    has_app = any(w in blob for w in _APPROVE_WORDS)
    has_rej = any(w in blob for w in _REJECT_WORDS)
    if has_app and not has_rej:
        return "approved"
    if has_rej and not has_app:
        return "rejected"
    return None


def _extract_explanation(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for key in ("explanation", "reason", "message", "comment", "text", "detail"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def probe_bank(bank_url: str) -> dict:
    """Снять снапшот банка. Никогда не бросает: при недоступности reachable=False."""
    bank_url = bank_url.rstrip("/")
    snap: dict = {"bank_url": bank_url, "commit": None, "reachable": False,
                  "checks": {}, "raw": {}}
    checks = snap["checks"]
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            # P1 — /health
            try:
                h = await client.get(f"{bank_url}/health")
                snap["reachable"] = h.status_code == 200
                if h.status_code == 200:
                    snap["commit"] = _safe_json(h).get("commit")
            except httpx.HTTPError:
                return snap  # банк недоступен — дальше нет смысла

            # P2 — / (UI)
            try:
                root = await client.get(f"{bank_url}/")
                html = root.text.lower() if root.status_code == 200 else ""
            except httpx.HTTPError:
                html = ""
            checks["credit_mentioned_in_ui"] = "кредит" in html
            checks["transfer_mentioned_in_ui"] = "перевод" in html

            # P3 — credit-apply, сильный заявитель
            t0 = time.time()
            try:
                r3 = await client.post(
                    f"{bank_url}/api/credit-apply",
                    json={"client_id": STRONG_APPLICANT, "amount_rub": 300000,
                          "term_months": 12},
                )
                checks["credit_apply_status"] = r3.status_code
                checks["credit_apply_latency_ms"] = int((time.time() - t0) * 1000)
                body3 = _safe_json(r3)
                snap["raw"]["strong"] = body3
                checks["decision_strong"] = _extract_decision(body3)
                checks["credit_response_has_decision"] = checks["decision_strong"] is not None
            except httpx.HTTPError:
                checks["credit_apply_status"] = 0
                checks["credit_apply_latency_ms"] = -1
                checks["decision_strong"] = None
                checks["credit_response_has_decision"] = False

            # P4 — credit-apply, слабый заявитель
            try:
                r4 = await client.post(
                    f"{bank_url}/api/credit-apply",
                    json={"client_id": WEAK_APPLICANT, "amount_rub": 900000,
                          "term_months": 6},
                )
                body4 = _safe_json(r4)
                snap["raw"]["weak"] = body4
                checks["decision_weak"] = _extract_decision(body4)
                expl = _extract_explanation(body4)
                checks["credit_response_has_explanation"] = bool(expl) and len(expl) > 40
            except httpx.HTTPError:
                checks["decision_weak"] = None
                checks["credit_response_has_explanation"] = False

            ds, dw = checks.get("decision_strong"), checks.get("decision_weak")
            checks["decision_is_discriminating"] = (
                ds is not None and dw is not None and ds != dw
            )

            # P5 — /credit-applications
            try:
                r5 = await client.get(f"{bank_url}/credit-applications")
                items = _safe_json(r5).get("items")
                checks["credit_applications_listed"] = (
                    r5.status_code == 200 and isinstance(items, list)
                )
            except httpx.HTTPError:
                checks["credit_applications_listed"] = False

            # P6 — регрессия: перевод между двумя клиентами
            try:
                cl = await client.get(f"{bank_url}/clients?limit=2")
                ids = [c["id"] for c in _safe_json(cl).get("items", []) if "id" in c]
                if len(ids) >= 2:
                    rt = await client.post(
                        f"{bank_url}/api/transfer",
                        json={"from_client_id": ids[0], "to": ids[1],
                              "amount_rub": 1000},
                    )
                    checks["transfer_regression_ok"] = rt.status_code == 200
                else:
                    checks["transfer_regression_ok"] = False
            except httpx.HTTPError:
                checks["transfer_regression_ok"] = False
    except httpx.HTTPError:
        pass
    return snap
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('simulator/src/probe.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add simulator/src/probe.py
git commit -m "feat(simulator): probe — закрытый список проверок банка P1-P6"
```

### Task 3.2: `simulator/src/db.py` — Postgres-хранение

**Files:**
- Create: `simulator/src/db.py`

- [ ] **Step 1: Создать файл**

```python
"""Postgres-хранение симулятора: счёт команд и журнал событий.

Если DATABASE_URL не задан — init_pool возвращает None, и main.py
переходит на in-memory режим (для локального запуска без БД).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sim_state (
    team           TEXT PRIMARY KEY,
    client_base    INT NOT NULL,
    last_commit    TEXT,
    baseline_score INT,
    last_score     INT,
    updated_at     TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sim_events (
    id                BIGSERIAL PRIMARY KEY,
    team              TEXT,
    ts                TIMESTAMPTZ,
    commit            TEXT,
    delta             INT,
    client_base_after INT,
    rubric            JSONB,
    reason            TEXT,
    snapshot          JSONB,
    judge             TEXT
);
"""


async def init_pool() -> asyncpg.Pool | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    return await asyncpg.create_pool(url, min_size=1, max_size=4)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def get_state(pool: asyncpg.Pool) -> dict[str, dict]:
    """Вернуть состояние обеих команд: {team: {client_base, last_commit, ...}}."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM sim_state")
    return {
        r["team"]: {
            "client_base": r["client_base"],
            "last_commit": r["last_commit"],
            "baseline_score": r["baseline_score"],
            "last_score": r["last_score"],
        }
        for r in rows
    }


async def upsert_state(pool: asyncpg.Pool, team: str, client_base: int,
                       last_commit: str | None, baseline_score: int,
                       last_score: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO sim_state(team, client_base, last_commit,
                   baseline_score, last_score, updated_at)
               VALUES($1, $2, $3, $4, $5, $6)
               ON CONFLICT (team) DO UPDATE SET
                   client_base=$2, last_commit=$3, baseline_score=$4,
                   last_score=$5, updated_at=$6""",
            team, client_base, last_commit, baseline_score, last_score,
            datetime.now(timezone.utc),
        )


async def add_event(pool: asyncpg.Pool, team: str, commit: str | None,
                    delta: int, client_base_after: int, rubric: list[int],
                    reason: str, snapshot: dict, judge: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO sim_events(team, ts, commit, delta,
                   client_base_after, rubric, reason, snapshot, judge)
               VALUES($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9)""",
            team, datetime.now(timezone.utc), commit, delta, client_base_after,
            json.dumps(rubric), reason, json.dumps(snapshot, ensure_ascii=False),
            judge,
        )


async def recent_events(pool: asyncpg.Pool, limit: int = 30) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT team, ts, commit, delta, client_base_after, rubric, "
            "reason, judge FROM sim_events ORDER BY id DESC LIMIT $1",
            limit,
        )
    return [
        {
            "team": r["team"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "commit": r["commit"],
            "delta": r["delta"],
            "client_base_after": r["client_base_after"],
            "rubric": json.loads(r["rubric"]) if r["rubric"] else [],
            "reason": r["reason"],
            "judge": r["judge"],
        }
        for r in rows
    ]


async def reset(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE sim_events")
        await conn.execute("TRUNCATE sim_state")
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('simulator/src/db.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add simulator/src/db.py
git commit -m "feat(simulator): Postgres-слой — sim_state и sim_events"
```

---

## Фаза 4 — Симулятор: сервис

### Task 4.1: `simulator/src/main.py` — FastAPI, эндпоинты, pull-loop

**Files:**
- Create: `simulator/src/main.py`

- [ ] **Step 1: Создать файл**

```python
"""Симулятор клиентов AI-воркшопа.

Pull-моделью опрашивает /health обоих банков; на новый git-коммит
снимает probe-снапшот, оценивает рубрикой (LLM-судья + fallback) и
детерминированной формулой двигает клиентскую базу команды.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src import db as dbmod
from src.judge import judge_round
from src.probe import probe_bank
from src.scoring import B0, compute_round, compute_unreachable, rubric_total

BANK_URLS = {
    "team_a": os.environ.get("BANK_A_URL", "http://localhost:8001").rstrip("/"),
    "team_b": os.environ.get("BANK_B_URL", "http://localhost:8002").rstrip("/"),
}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))

# in-memory зеркало состояния (используется и как кэш, и как fallback без БД)
_state: dict[str, dict] = {
    "team_a": {"client_base": B0, "last_commit": None, "baseline_score": None,
               "last_score": None},
    "team_b": {"client_base": B0, "last_commit": None, "baseline_score": None,
               "last_score": None},
}
_events: list[dict] = []
_eval_lock = asyncio.Lock()


def _pool():
    return getattr(app.state, "pool", None)


async def _load_state() -> None:
    """Поднять состояние из БД в память (или оставить дефолт)."""
    pool = _pool()
    if pool is None:
        return
    saved = await dbmod.get_state(pool)
    for team, row in saved.items():
        _state[team] = row
    _events.clear()
    _events.extend(await dbmod.recent_events(pool, limit=50))


async def _persist(team: str, snapshot: dict, scores: list[int], reason: str,
                   judge: str, delta: int) -> None:
    st = _state[team]
    pool = _pool()
    if pool is not None:
        await dbmod.upsert_state(pool, team, st["client_base"],
                                 st["last_commit"], st["baseline_score"],
                                 st["last_score"])
        await dbmod.add_event(pool, team, snapshot.get("commit"), delta,
                              st["client_base"], scores, reason, snapshot, judge)
    _events.insert(0, {
        "team": team, "ts": None, "commit": snapshot.get("commit"),
        "delta": delta, "client_base_after": st["client_base"],
        "rubric": scores, "reason": reason, "judge": judge,
    })
    del _events[60:]


async def _baseline() -> None:
    """Первый запуск: снять нетронутые банки и зафиксировать S_base."""
    snap_a = await probe_bank(BANK_URLS["team_a"])
    snap_b = await probe_bank(BANK_URLS["team_b"])
    verdict = await judge_round(snap_a, snap_b)
    for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
        s_base = rubric_total(verdict[team]["scores"])
        _state[team] = {
            "client_base": B0, "last_commit": snap.get("commit"),
            "baseline_score": s_base, "last_score": s_base,
        }
    pool = _pool()
    if pool is not None:
        for team in ("team_a", "team_b"):
            st = _state[team]
            await dbmod.upsert_state(pool, team, st["client_base"],
                                     st["last_commit"], st["baseline_score"],
                                     st["last_score"])


async def evaluate_round() -> dict:
    """Полный раунд: probe обоих банков, один вызов судьи, обновление баз."""
    async with _eval_lock:
        snap_a = await probe_bank(BANK_URLS["team_a"])
        snap_b = await probe_bank(BANK_URLS["team_b"])
        verdict = await judge_round(snap_a, snap_b)
        out: dict[str, dict] = {}
        for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
            st = _state[team]
            s_base = st["baseline_score"]
            if s_base is None:
                s_base = rubric_total(verdict[team]["scores"])
                st["baseline_score"] = s_base
                st["last_score"] = s_base
            scores = verdict[team]["scores"]
            reason = verdict[team]["reason"]
            judge = verdict[team]["judge"]
            if not snap.get("reachable"):
                r = compute_unreachable(st["client_base"])
                reason = "Банк не открывается — клиенты не могут войти."
                s_now = st["last_score"]
            else:
                s_now = rubric_total(scores)
                r = compute_round(s_now, st["last_score"], s_base,
                                  st["client_base"])
            st["client_base"] = r["client_base"]
            st["last_score"] = s_now
            st["last_commit"] = snap.get("commit")
            await _persist(team, snap, scores, reason, judge, r["delta"])
            out[team] = {"delta": r["delta"], "client_base": st["client_base"],
                         "reason": reason, "judge": judge}
        return out


async def _poll_loop() -> None:
    """Фон: раз в POLL_INTERVAL_S смотреть /health банков, ловить новый коммит."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            changed = False
            for team in ("team_a", "team_b"):
                snap = await probe_bank(BANK_URLS[team])
                commit = snap.get("commit")
                if commit and commit not in (None, "local") \
                        and commit != _state[team]["last_commit"]:
                    changed = True
            if changed:
                await evaluate_round()
        except Exception as exc:  # noqa: BLE001 — фон не должен падать
            print(f"[simulator] poll error: {exc!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    try:
        pool = await dbmod.init_pool()
        if pool is not None:
            await dbmod.ensure_schema(pool)
        app.state.pool = pool
        await _load_state()
        if _state["team_a"]["baseline_score"] is None:
            await _baseline()
    except Exception as exc:  # noqa: BLE001
        print(f"[simulator] init error: {exc!r}")
        app.state.pool = pool
    task = asyncio.create_task(_poll_loop())
    try:
        yield
    finally:
        task.cancel()
        if pool is not None:
            await pool.close()


app = FastAPI(title="Симулятор клиентов", version="1.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "simulator",
            "db": _pool() is not None, "banks": BANK_URLS}


@app.get("/state")
async def state() -> dict:
    return {
        "teams": {t: {"client_base": s["client_base"],
                      "last_score": s["last_score"],
                      "baseline_score": s["baseline_score"]}
                  for t, s in _state.items()},
        "events": _events[:30],
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    f = STATIC_DIR / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>Симулятор</h1>"


def _check_admin(token: str | None) -> None:
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="нужен корректный admin-токен")


@app.post("/admin/evaluate")
async def admin_evaluate(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    return await evaluate_round()


@app.post("/admin/reset")
async def admin_reset(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    pool = _pool()
    if pool is not None:
        await dbmod.reset(pool)
    _events.clear()
    for team in ("team_a", "team_b"):
        _state[team] = {"client_base": B0, "last_commit": None,
                        "baseline_score": None, "last_score": None}
    await _baseline()
    return {"status": "reset", "state": _state}
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('simulator/src/main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add simulator/src/main.py
git commit -m "feat(simulator): FastAPI-сервис, эндпоинты и pull-loop оценки"
```

### Task 4.2: `simulator/src/static/index.html` — табло

**Files:**
- Create: `simulator/src/static/index.html`

- [ ] **Step 1: Создать файл**

```html
<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>Табло воркшопа · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:28px;
       background:#0c0d10;color:#e8e9ec;min-height:100vh}
  h1{font-weight:500;font-size:22px;margin:0 0 4px}
  .meta{font-size:12px;color:#6b6d75;margin-bottom:22px}
  .teams{display:flex;gap:20px;flex-wrap:wrap}
  .team{flex:1;min-width:300px;background:#13151c;border:1px solid #23262f;
        border-radius:10px;padding:22px}
  .team h2{margin:0 0 6px;font-size:15px;color:#8a8d96;letter-spacing:.14em;
           text-transform:uppercase}
  .base{font-size:56px;font-weight:700;font-variant-numeric:tabular-nums}
  .delta{font-size:15px;margin-top:2px}
  .up{color:#7ee787}.down{color:#ff8c8c}.flat{color:#8a8d96}
  h3{font-size:12px;color:#8a8d96;letter-spacing:.12em;text-transform:uppercase;
     margin:28px 0 10px}
  .ev{background:#13151c;border:1px solid #23262f;border-radius:8px;
      padding:10px 14px;margin-bottom:8px;font-size:13px}
  .ev .t{color:#FFE600;font-weight:600}
  .ev .d{font-variant-numeric:tabular-nums;font-weight:600}
  .ev .j{font-size:11px;color:#6b6d75}
</style></head><body>
<h1>Табло воркшопа — клиентская база команд</h1>
<div class="meta">обновляется автоматически · симулятор клиентов</div>
<div class="teams" id="teams"></div>
<h3>Лента событий</h3>
<div id="events"></div>
<script>
const fmt = n => Number(n||0).toLocaleString('ru-RU');
const LABEL = {team_a:'Команда A', team_b:'Команда B'};
async function tick(){
  let d;
  try { d = await (await fetch('/state')).json(); } catch(e){ return; }
  const tEl = document.getElementById('teams');
  tEl.innerHTML = Object.entries(d.teams).map(([k,v])=>{
    const last = (d.events||[]).find(e=>e.team===k);
    let delta = '<span class="flat">— без изменений</span>';
    if(last){
      if(last.delta>0) delta = `<span class="up">▲ +${fmt(last.delta)} клиентов</span>`;
      else if(last.delta<0) delta = `<span class="down">▼ ${fmt(last.delta)} клиентов</span>`;
    }
    return `<div class="team"><h2>${LABEL[k]||k}</h2>
      <div class="base">${fmt(v.client_base)}</div>
      <div class="delta">${delta}</div></div>`;
  }).join('');
  const eEl = document.getElementById('events');
  eEl.innerHTML = (d.events||[]).map(e=>{
    const cls = e.delta>0?'up':(e.delta<0?'down':'flat');
    const sign = e.delta>0?'+':'';
    return `<div class="ev"><span class="t">${LABEL[e.team]||e.team}</span>
      <span class="d ${cls}">${sign}${fmt(e.delta)}</span> — ${e.reason||''}
      <span class="j">[${e.judge||'?'}]</span></div>`;
  }).join('') || '<div class="ev flat">пока событий нет</div>';
}
tick(); setInterval(tick, 4000);
</script>
</body></html>
```

- [ ] **Step 2: Commit**

```bash
git add simulator/src/static/index.html
git commit -m "feat(simulator): табло — клиентские базы команд и лента событий"
```

### Task 4.3: `simulator/Dockerfile`

**Files:**
- Create: `simulator/Dockerfile`

- [ ] **Step 1: Создать файл**

```dockerfile
# Build context = корень монорепо.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "httpx>=0.27" \
    "asyncpg>=0.29"

COPY simulator/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

- [ ] **Step 2: Собрать образ**

Run: `cd <repo-root> && docker build -f simulator/Dockerfile -t raif-simulator .`
Expected: сборка завершается `naming to ... done`

- [ ] **Step 3: Commit**

```bash
git add simulator/Dockerfile
git commit -m "feat(simulator): Dockerfile"
```

### Task 4.4: Смоук-проверка симулятора без БД

**Files:** —

- [ ] **Step 1: Запустить симулятор и два банка**

```bash
docker network create raifnet 2>/dev/null || true
docker run -d --rm --network raifnet -e TEAM_NAME=team_a --name team_a raif-team-a
docker run -d --rm --network raifnet -e TEAM_NAME=team_b --name team_b raif-team-b
docker run -d --rm --network raifnet -p 8000:8000 \
  -e BANK_A_URL=http://team_a:8020 -e BANK_B_URL=http://team_b:8020 \
  -e ADMIN_TOKEN=test --name simulator raif-simulator
sleep 8
```

- [ ] **Step 2: Проверить /health и /state**

Run: `curl -s localhost:8000/health && echo && curl -s localhost:8000/state`
Expected: `/health` — `"status":"ok"`; `/state` — обе команды с `client_base:500`, `baseline_score` ≈ 2

- [ ] **Step 3: Прогнать ручной раунд оценки**

Run: `curl -s -X POST localhost:8000/admin/evaluate -H "X-Admin-Token: test"`
Expected: JSON с `team_a`/`team_b`, `delta` 0 (банки не менялись), `judge` `llm` или `fallback`

- [ ] **Step 4: Остановить контейнеры**

```bash
docker stop simulator team_a team_b
```

- [ ] **Step 5: Commit (если были правки)**

Если шаги выявили баги — поправить и закоммитить `fix(simulator): ...`.
Если всё зелёное — коммита нет.

---

## Фаза 5 — Инфраструктура

### Task 5.1: Переписать `render.yaml`

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
# Render Blueprint для AI-воркшопа: 2 банка-команды + симулятор.
#
# Однократно в UI Render:
#  1. New → Blueprint → Connect repo → выбрать ветку.
#  2. Render покажет 3 web-сервиса + 1 Postgres. Apply.
#  3. В env-группе ai-workshop-shared задать OPENAI_API_KEY и ADMIN_TOKEN.

databases:
  - name: raif-workshop-db
    plan: free
    region: oregon

envVarGroups:
  - name: ai-workshop-shared
    envVars:
      - key: OPENAI_API_KEY
        sync: false
      - key: OPENAI_BASE_URL
        value: https://api.openai.com/v1
      - key: OPENAI_MODEL
        value: gpt-4o-mini
      - key: ADMIN_TOKEN
        sync: false

services:
  - type: web
    name: raif-team-a
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_a/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_a/**, seed/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_a

  - type: web
    name: raif-team-b
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_b/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_b/**, seed/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_b

  - type: web
    name: raif-simulator
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./simulator/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [simulator/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: BANK_A_URL
        value: https://raif-team-a.onrender.com
      - key: BANK_B_URL
        value: https://raif-team-b.onrender.com
      - key: ACTIVE_TASK
        value: credit
      - key: DATABASE_URL
        fromDatabase:
          name: raif-workshop-db
          property: connectionString
```

- [ ] **Step 2: Проверить YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('render.yaml')); print('ok')"`
Expected: `ok` (если PyYAML не стоит — `pip install pyyaml` или пропустить, проверив глазами отступы)

- [ ] **Step 3: Commit**

```bash
git add render.yaml
git commit -m "feat: render.yaml — 3 сервиса и Postgres вместо 6 блоков"
```

### Task 5.2: Переписать `.github/workflows/deploy-render.yml`

**Files:**
- Modify: `.github/workflows/deploy-render.yml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
name: Deploy services via Render Deploy Hooks

# Дёргаем Deploy Hook только того сервиса, чья папка реально менялась.
# Секреты в GitHub → Settings → Secrets and variables → Actions:
#   RENDER_HOOK_TEAM_A, RENDER_HOOK_TEAM_B, RENDER_HOOK_SIMULATOR

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      services:
        description: "Что передеплоить (team_a,team_b,simulator или 'all')"
        required: false
        default: "all"

permissions:
  contents: read

concurrency:
  group: render-deploy
  cancel-in-progress: false

jobs:
  detect:
    runs-on: ubuntu-latest
    outputs:
      team_a:    ${{ steps.detect.outputs.team_a }}
      team_b:    ${{ steps.detect.outputs.team_b }}
      simulator: ${{ steps.detect.outputs.simulator }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - name: Detect changed services
        id: detect
        env:
          MANUAL_SERVICES: ${{ inputs.services }}
          BEFORE: ${{ github.event.before }}
          AFTER:  ${{ github.sha }}
        run: |
          set -e
          SERVICES="team_a team_b simulator"
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            REQ="${MANUAL_SERVICES:-all}"
            for S in $SERVICES; do
              if [ "$REQ" = "all" ] || echo ",$REQ," | grep -q ",$S,"; then
                echo "$S=true"  >> "$GITHUB_OUTPUT"
              else
                echo "$S=false" >> "$GITHUB_OUTPUT"
              fi
            done
            exit 0
          fi
          if [ -z "$BEFORE" ] || [ "$BEFORE" = "0000000000000000000000000000000000000000" ]; then
            CHANGED=$(git ls-files)
          else
            CHANGED=$(git diff --name-only "$BEFORE" "$AFTER" || true)
          fi
          echo "===== changed ====="; echo "$CHANGED" | head -60; echo "==================="
          # render.yaml меняет всё; seed/ — оба банка
          if echo "$CHANGED" | grep -qE "^render\.yaml$"; then
            for S in $SERVICES; do echo "$S=true" >> "$GITHUB_OUTPUT"; done
            exit 0
          fi
          SEED=$(echo "$CHANGED" | grep -qE "^seed/" && echo yes || echo no)
          for S in $SERVICES; do
            HIT=false
            echo "$CHANGED" | grep -qE "^${S}/" && HIT=true
            if [ "$S" != "simulator" ] && [ "$SEED" = "yes" ]; then HIT=true; fi
            echo "$S=$HIT" >> "$GITHUB_OUTPUT"
            echo "  $S → $HIT"
          done

  deploy:
    runs-on: ubuntu-latest
    needs: detect
    steps:
      - name: Deploy raif-team-a
        if: needs.detect.outputs.team_a == 'true'
        env: { HOOK: "${{ secrets.RENDER_HOOK_TEAM_A }}" }
        run: |
          if [ -z "$HOOK" ]; then echo "::warning::RENDER_HOOK_TEAM_A not set"; exit 0; fi
          curl -fsS -X POST "$HOOK" && echo " → triggered raif-team-a"
      - name: Deploy raif-team-b
        if: needs.detect.outputs.team_b == 'true'
        env: { HOOK: "${{ secrets.RENDER_HOOK_TEAM_B }}" }
        run: |
          if [ -z "$HOOK" ]; then echo "::warning::RENDER_HOOK_TEAM_B not set"; exit 0; fi
          curl -fsS -X POST "$HOOK" && echo " → triggered raif-team-b"
      - name: Deploy raif-simulator
        if: needs.detect.outputs.simulator == 'true'
        env: { HOOK: "${{ secrets.RENDER_HOOK_SIMULATOR }}" }
        run: |
          if [ -z "$HOOK" ]; then echo "::warning::RENDER_HOOK_SIMULATOR not set"; exit 0; fi
          curl -fsS -X POST "$HOOK" && echo " → triggered raif-simulator"
```

- [ ] **Step 2: Проверить YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-render.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-render.yml
git commit -m "feat: deploy-render.yml под 3 сервиса"
```

### Task 5.3: Переписать `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
# AI-воркшоп — локальный запуск: docker-compose up --build
# Банки: http://localhost:8001 (A), http://localhost:8002 (B)
# Табло: http://localhost:8000

services:
  team_a:
    build: { context: ., dockerfile: team_a/Dockerfile }
    image: raif-team-a
    container_name: raif-team-a
    environment:
      TEAM_NAME: team_a
    ports: ["8001:8020"]
    networks: [raif]

  team_b:
    build: { context: ., dockerfile: team_b/Dockerfile }
    image: raif-team-b
    container_name: raif-team-b
    environment:
      TEAM_NAME: team_b
    ports: ["8002:8020"]
    networks: [raif]

  postgres:
    image: postgres:16-alpine
    container_name: raif-postgres
    environment:
      POSTGRES_USER: sim
      POSTGRES_PASSWORD: sim
      POSTGRES_DB: simulator
    ports: ["5432:5432"]
    networks: [raif]

  simulator:
    build: { context: ., dockerfile: simulator/Dockerfile }
    image: raif-simulator
    container_name: raif-simulator
    environment:
      BANK_A_URL: http://team_a:8020
      BANK_B_URL: http://team_b:8020
      DATABASE_URL: postgres://sim:sim@postgres:5432/simulator
      ADMIN_TOKEN: localdev
      POLL_INTERVAL_S: "30"
    depends_on: [team_a, team_b, postgres]
    ports: ["8000:8000"]
    networks: [raif]

networks:
  raif:
    driver: bridge
```

- [ ] **Step 2: Поднять стенд**

Run: `cd <repo-root> && docker-compose up --build -d && sleep 12`
Expected: 4 контейнера в статусе Up

- [ ] **Step 3: Проверить связку**

Run: `curl -s localhost:8001/health && echo && curl -s localhost:8000/state`
Expected: банк A отвечает; `/state` симулятора — обе команды, `baseline_score` заполнен

- [ ] **Step 4: Остановить стенд**

Run: `docker-compose down`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-compose — 2 банка, симулятор, Postgres"
```

---

## Фаза 6 — Онбординг

### Task 6.1: Шаблоны permissions `settings-team-a.json` и `settings-team-b.json`

**Files:**
- Create: `.claude/templates/settings-team-a.json`, `.claude/templates/settings-team-b.json`

- [ ] **Step 1: Создать `.claude/templates/settings-team-a.json`**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "deny": [
      "Read(team_b/**)",
      "Edit(team_b/**)",
      "Write(team_b/**)",
      "Read(simulator/**)",
      "Edit(simulator/**)",
      "Write(simulator/**)",
      "Edit(seed/**)",
      "Write(seed/**)",
      "Edit(render.yaml)",
      "Write(render.yaml)",
      "Edit(.github/**)",
      "Write(.github/**)",
      "Edit(CLAUDE.md)",
      "Write(CLAUDE.md)",
      "Edit(TEAM.md)",
      "Edit(RULES.md)",
      "Edit(README.md)",
      "Edit(docker-compose.yml)",
      "Write(docker-compose.yml)"
    ],
    "allow": [
      "Edit(team_a/**)",
      "Write(team_a/**)",
      "Read(team_a/**)",
      "Read(tasks/**)",
      "Read(seed/**)",
      "Bash(docker-compose:*)",
      "Bash(docker:*)",
      "Bash(curl:*)",
      "Bash(python:*)",
      "Bash(python3:*)",
      "Bash(pip:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(git add:*)",
      "Bash(git commit:*)",
      "Bash(git push:*)",
      "Bash(git pull:*)",
      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(grep:*)",
      "Bash(find:*)"
    ]
  }
}
```

- [ ] **Step 2: Создать `.claude/templates/settings-team-b.json`**

То же, что Step 1, но зеркально: в `deny` — `team_a/**` (Read/Edit/Write),
в `allow` — `team_a/**` заменить на `team_b/**` (Edit/Write/Read).

- [ ] **Step 3: Проверить JSON**

Run: `python3 -c "import json; json.load(open('.claude/templates/settings-team-a.json')); json.load(open('.claude/templates/settings-team-b.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .claude/templates/settings-team-a.json .claude/templates/settings-team-b.json
git commit -m "feat: шаблоны permissions со слепотой между командами"
```

### Task 6.2: Починить `tools/cowork-onboard.py` и перевести на `WORKSHOP_TEAM`

**Files:**
- Modify: `tools/cowork-onboard.py`

- [ ] **Step 1: Прочитать текущий файл**

Run: `cat tools/cowork-onboard.py | tail -30`
Expected: файл обрывается на `setup_git_ident` — `main()` не завершён, нет вызова.

- [ ] **Step 2: Заменить хвост файла (от `def main()` до конца) на завершённую версию**

Найти строку `def main() -> int:` и заменить всё от неё до конца файла на:

```python
def main() -> int:
    step("Настраиваю SSH в sandbox-е")
    setup_ssh()

    step("Читаю мета-инфо участника")
    info = parse_info()
    if info:
        ok(f"WORKSHOP_TEAM={info.get('WORKSHOP_TEAM', '?')}  "
           f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '?')}")
    else:
        warn("info-файла нет — Claude должен будет спросить имя и команду")

    step("Прописываю git identity")
    setup_git_identity(info)

    step("Поднимаю Linux-side git-dir и шим")
    setup_linux_gitdir()

    step("Закаляю git config")
    harden_git_config()

    step("Чищу залипшие локи на Windows-mount")
    cleanup_stale_locks_on_mount()

    step("Проверяю доступ к GitHub")
    github_ok = test_github()

    print("=== READY ===", flush=True)
    print(f"WORKSHOP_TEAM={info.get('WORKSHOP_TEAM', '')}", flush=True)
    print(f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '')}", flush=True)
    print(f"WORKSHOP_GIT_NAME={info.get('WORKSHOP_GIT_NAME', '')}", flush=True)
    print(f"GIT_SHIM={SHIM_PATH}", flush=True)
    print(f"GITHUB_OK={'yes' if github_ok else 'no'}", flush=True)
    return 0 if info else 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Проверить синтаксис и прогон**

Run: `python3 -c "import ast; ast.parse(open('tools/cowork-onboard.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add tools/cowork-onboard.py
git commit -m "fix(tools): завершён cowork-onboard.py, переход на WORKSHOP_TEAM"
```

### Task 6.3: Переписать корневой `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Заменить файл целиком**

Содержимое — онбординг агента команды. Обязательные разделы:

1. **Стоп-проверка организатора** — если пользователь Виталий Ерохин / Нерсес
   Багиян, читать `ORGANIZER.md`, не запускать онбординг участника.
2. **Сеттинг** — AI-воркшоп правления Райффайзен; участник — нетехнический
   член правления; две команды по 3 человека решают одну выданную задачу.
3. **Главное правило общения** — пользователь не программист: никакого жаргона
   без бизнес-аналогии, описывать результат, не код; **в чате — без markdown**,
   только живой текст абзацами; на русском; праздновать маленькие победы.
4. **Первая задача — онбординг (шаги 0–6):**
   - Шаг 0: `python3 tools/cowork-onboard.py`; читать `WORKSHOP_TEAM` из вывода;
     все git-команды — через шим `/tmp/bin/git`.
   - Шаг 1: прочитать `TEAM.md`, `RULES.md`.
   - Шаг 2: поздороваться, узнать имя (если info-файл дал команду — пропустить).
   - Шаг 3: сопоставить имя → команда по `TEAM.md`.
   - Шаг 4: `cp .claude/templates/settings-<team>.json .claude/settings.local.json`,
     сказать «активировал защиту: смогу менять только папку твоей команды».
   - Шаг 5: прочитать бриф активной задачи `tasks/task_01_credit.md`.
   - Шаг 6: коротко представить задачу пользователю живым языком и спросить,
     с чего начать.
5. **Границы папок** — менять только `team_a/` или `team_b/` (своей команды);
   папка другой команды и `simulator/` — недоступны (permissions блокируют);
   `seed/`, `tasks/` — read-only.
6. **Никакой связи с другой командой** — INBOX больше нет, команды независимы;
   внутри команды люди договариваются офлайн.
7. **Git и общая копилка** — после каждого изменения предлагать «отправить в
   общую копилку команды» (`git add -A && git commit && git pull --rebase
   --autostash && git push`); перед push всегда `git pull --rebase --autostash`;
   запрещённые при разговоре слова — `push/commit/merge/rebase/branch/main`,
   замена бизнес-аналогиями.
8. **Петля обратной связи** — после отправки работы банк команды
   пересобирается, симулятор переоценивает его и двигает клиентскую базу;
   предложить пользователю открыть табло, чтобы увидеть реакцию клиентов.
9. **Деплой** — банк команды на Render (`raif-team-a` / `raif-team-b`),
   сборка 2–4 минуты; после push сказать об этом пользователю человеческим
   языком и дать ссылку на его банк и на табло.

Тон и формулировки — перенести из старого `CLAUDE.md` (правила про жаргон,
markdown, «общую копилку», запрещённые слова сохранить дословно). Убрать всё
про INBOX, contracts, `state.md`-церемонию, выбор «что строить», NEIGHBOR-механику.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "feat: CLAUDE.md — онбординг агента команды, новый формат"
```

### Task 6.4: Переписать `TEAM.md`, `RULES.md`, `README.md`

**Files:**
- Modify: `TEAM.md`, `RULES.md`, `README.md`

- [ ] **Step 1: Переписать `TEAM.md`**

Содержит:
- Таблицу «участник → команда → папка»: 6 членов правления (Монин, Патрахин,
  Курочкин, Ложечкин, Хебенштрайт, Васс), распределённые по `team_a` / `team_b`.
  Конкретное распределение определяет Виталий — оставить таблицу с пометкой
  «составы команд — заполнить организатору» и плейсхолдерами `<команда A/B>`.
- Алиасы имён для распознавания (как в старом TEAM.md).
- Блок про организаторов (Виталий Ерохин, Нерсес Багиян) — без изменений.
- Раздел «банки и порты»: `team_a` → 8001 локально / `raif-team-a.onrender.com`;
  `team_b` → 8002 / `raif-team-b.onrender.com`; табло → 8000 / `raif-simulator`.

- [ ] **Step 2: Переписать `RULES.md`**

Содержит: каждая команда отвечает за свою папку; папка другой команды и
`simulator/` недоступны (слепота); INBOX и contracts удалены — команды
независимы; обе команды решают одну задачу параллельно; общая ветка `main`,
перед push — `git pull --rebase --autostash`, папки команд не пересекаются;
табло симулятора показывает счёт; в чате с участником — без markdown и жаргона.

- [ ] **Step 3: Переписать `README.md`**

Простой нетехнический текст: что это за репозиторий, формат воркшопа
(две команды, одна задача, симулятор клиентов, табло), без жаргона.

- [ ] **Step 4: Commit**

```bash
git add TEAM.md RULES.md README.md
git commit -m "feat: TEAM.md, RULES.md, README.md под формат двух команд"
```

### Task 6.5: Переписать `ORGANIZER.md` и `DEPLOY.md`

**Files:**
- Modify: `ORGANIZER.md`, `DEPLOY.md`

- [ ] **Step 1: Переписать `ORGANIZER.md`**

Brief организатора под новый формат: две команды по 3 человека, одна задача
(сначала «Кредиты», потом «Инвестиции»), симулятор клиентов и принцип оценки
(probe → рубрика → формула), 3 сервиса на Render, слепота команд, ручные шаги
организатора (см. §12 спеки), ссылка на спеку и этот план.

- [ ] **Step 2: Переписать `DEPLOY.md`**

Как блок попадает в прод: 3 сервиса (`raif-team-a`, `raif-team-b`,
`raif-simulator`), Deploy Hooks (`RENDER_HOOK_TEAM_A/TEAM_B/SIMULATOR`),
buildFilter по папкам, Postgres `raif-workshop-db` для симулятора,
переменные `OPENAI_API_KEY`/`ADMIN_TOKEN` в env-группе, симулятор сам
переоценивает банк после деплоя (pull-модель — Action его не дёргает).

- [ ] **Step 3: Commit**

```bash
git add ORGANIZER.md DEPLOY.md
git commit -m "feat: ORGANIZER.md и DEPLOY.md под новый формат"
```

### Task 6.6: Обновить bootstrap-скрипты под `WORKSHOP_TEAM`

**Files:**
- Modify: `tools/bootstrap/raif-workshop-setup.cmd`, `tools/bootstrap/raif-workshop-setup.applescript`

- [ ] **Step 1: Найти, где формируется `.git/raif-workshop-info`**

Run: `grep -n "WORKSHOP_BLOCK\|raif-workshop-info" tools/bootstrap/raif-workshop-setup.cmd tools/bootstrap/raif-workshop-setup.applescript`
Expected: строки, где в info-файл пишется `WORKSHOP_BLOCK=...`

- [ ] **Step 2: Заменить ключ `WORKSHOP_BLOCK` на `WORKSHOP_TEAM`**

В обоих файлах в местах формирования info-файла заменить `WORKSHOP_BLOCK` на
`WORKSHOP_TEAM`; значение (`team_a` / `team_b`) задаётся организатором на
машину участника. Остальную логику bootstrap не трогать.

- [ ] **Step 3: Обновить `tools/bootstrap/README.md`**

Отразить: info-файл теперь содержит `WORKSHOP_TEAM`, значение — `team_a` или
`team_b` по распределению из `TEAM.md`.

- [ ] **Step 4: Commit**

```bash
git add tools/bootstrap/
git commit -m "feat(bootstrap): info-файл пишет WORKSHOP_TEAM"
```

---

## Фаза 7 — Брифы задач

### Task 7.1: `tasks/task_01_credit.md`

**Files:**
- Create: `tasks/task_01_credit.md`

- [ ] **Step 1: Создать файл**

Нетехнический бриф для члена правления. Обязательно:
- **Что делаем:** в банке команды есть вкладка «Переводы»; добавить рядом
  вкладку «Кредиты», где клиент подаёт заявку (сумма + срок) и сразу получает
  решение.
- **Что значит «готово»** (на языке ощущений, не кода): заявку можно подать;
  решение приходит сразу, в течение нескольких секунд; решение зависит от
  самого клиента (надёжному — да, рискованному — нет), а не случайно; при
  отказе клиент видит понятное человеческое объяснение; вкладка «Переводы»
  по-прежнему работает.
- **Как проверить:** открыть банк команды, подать заявку, посмотреть на табло —
  довольны ли клиенты.
- Без шагов реализации и без жаргона — это решает агент с участником.

- [ ] **Step 2: Commit**

```bash
git add tasks/task_01_credit.md
git commit -m "feat(tasks): бриф задачи 1 — вкладка Кредиты"
```

### Task 7.2: `tasks/task_02_invest.md`

**Files:**
- Create: `tasks/task_02_invest.md`

- [ ] **Step 1: Создать файл**

Аналогичный нетехнический бриф для задачи «Инвестиции»: добавить вкладку
«Инвестиции», где клиенту предлагают инвест-продукты и он может «вложить».
«Готово»: вкладка есть; продукты показываются; есть действие «вложить»;
прочие вкладки не сломаны. Пометка: probe и рубрику задачи 2 в симуляторе
дорабатываем фаст-фоллоу (Фаза 8 спеки).

- [ ] **Step 2: Commit**

```bash
git add tasks/task_02_invest.md
git commit -m "feat(tasks): бриф задачи 2 — вкладка Инвестиции"
```

---

## Фаза 8 — Тест-прогон (приёмка)

### Task 8.1: Полный сценарий через docker-compose

**Files:** —

- [ ] **Step 1: Поднять стенд**

Run: `docker-compose up --build -d && sleep 14`
Expected: 4 контейнера Up

- [ ] **Step 2: Базовая линия**

Run: `curl -s localhost:8000/state`
Expected: обе команды `client_base:500`, `baseline_score` ≈ 2, событий нет

- [ ] **Step 3: Сымитировать выполнение задачи командой A**

В `team_a/src/main.py`, в функции `credit_apply`, в in-memory ветке заменить
блок от `# in-memory` до конца функции на временную тестовую логику:

```python
    # in-memory  (ВРЕМЕННАЯ тестовая логика — откатывается в Task 8.1 Step 5)
    if cid not in _clients_by_id:
        return JSONResponse(status_code=404, content={"detail": f"клиент {cid} не найден"})
    client = _clients_by_id[cid]
    approved = int(client.get("income_rub") or 0) >= 60000
    _credit_applications.append({
        "client_id": cid, "amount_rub": payload.get("amount_rub"),
        "term_months": payload.get("term_months"),
        "status": "approved" if approved else "rejected",
    })
    return JSONResponse(status_code=200, content={
        "decision": "approved" if approved else "rejected",
        "explanation": (
            "Заявка одобрена: подтверждённого дохода достаточно для платежей."
            if approved else
            "К сожалению, мы вынуждены отказать: текущего подтверждённого "
            "дохода недостаточно для комфортного обслуживания этого кредита."
        ),
    })
```

Пересобрать и перезапустить только team_a:
`docker-compose up -d --build team_a && sleep 6`

- [ ] **Step 4: Прогнать раунд оценки**

Run: `curl -s -X POST localhost:8000/admin/evaluate -H "X-Admin-Token: localdev"`
Expected: у `team_a` положительная `delta` (клиенты пришли), у `team_b` —
`delta` 0; `reason` непустой; на `localhost:8000` табло показывает рост A

- [ ] **Step 5: Откатить временную правку team_a**

```bash
git checkout team_a/src/main.py
docker-compose up -d --build team_a
```

- [ ] **Step 6: Зафиксировать результат прогона**

Коммита с кодом нет (правка откачена). Если прогон выявил баги — поправить
соответствующий модуль и закоммитить `fix: ...`.

### Task 8.2: Проверка скриптового fallback

**Files:** —

- [ ] **Step 1: Поднять симулятор без ключа OpenAI**

Стенд из Task 8.1 уже без `OPENAI_API_KEY` — судья работает в режиме fallback.

- [ ] **Step 2: Прогнать раунд и проверить метку judge**

Run: `curl -s -X POST localhost:8000/admin/evaluate -H "X-Admin-Token: localdev" && curl -s localhost:8000/state`
Expected: события с `"judge":"fallback"`, дельты и обоснования осмысленные,
симулятор не падает

- [ ] **Step 3: Остановить стенд**

Run: `docker-compose down -v`

- [ ] **Step 4: Финальная проверка тестов**

Run: `cd simulator && python3 -m pytest tests/ -q`
Expected: все тесты зелёные

---

## Деферд (фаст-фоллоу, вне этого плана)

- Probe и рубрика задачи 2 («Инвестиции») — отдельная итерация после задачи 1.
- SVG-спарклайн тренда на табло — если останется время.

## Ручные шаги организатора (не автоматизируются планом)

- Render: удалить 6 старых сервисов, применить новый Blueprint, задать
  `OPENAI_API_KEY` и `ADMIN_TOKEN` в env-группе, завести 3 секрета
  деплой-хуков в GitHub.
- Распределить 6 членов правления по командам — заполнить `TEAM.md`.
- Прогнать обновлённый bootstrap на ноутбуках участников.
