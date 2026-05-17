# Трёхблочная структура команд — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перестроить каждую команду из одного банка-монолита в три сервиса-блока (`retail`, `cib`, `backend`), по одному на участника, и переписать симулятор под интегральную оценку трёх блоков.

**Architecture:** Каждая команда — три FastAPI-сервиса. `backend` хранит данные (in-memory seed) и отдаёт базовый API. `cib` — каталог продуктов и логика решений, ходит в backend. `retail` — мобильный UI, ходит в backend (данные) и cib (решение). Симулятор опрашивает все 6 банк-сервисов, оценивает каждую команду по 10 критериям (по блокам), счёт — сумма.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, httpx, asyncpg (только симулятор), Postgres, Docker, Render, pytest.

**Спека:** `docs/superpowers/specs/2026-05-17-three-block-teams-design.md`

**Соглашение по коммитам:** каждый коммит заканчивается строкой
`Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
Работа в worktree-ветке `worktree-three-block`.

**ПРОГРЕСС:** Фаза 0 (коммит `5e0f74e`) и Фаза 1 — блок backend (коммит
`9efe3e1`) выполнены и закоммичены. Начинать с Фазы 2. Фазу 0 повторно НЕ
запускать — `git rm -r team_a` снесёт уже собранный `team_a/backend`.

---

## Структура файлов

**Создаются** (для team_a; team_b — копией в Task 3.4):
- `team_a/backend/{Dockerfile, pyproject.toml, src/main.py}`
- `team_a/retail/{Dockerfile, pyproject.toml, src/main.py, src/static/index.html}`
- `team_a/cib/{Dockerfile, pyproject.toml, src/main.py, src/llm.py}`
- `.claude/templates/settings-team_a-{retail,cib,backend}.json` + 3 для team_b

**Переписываются:** `simulator/src/{scoring.py,probe.py,judge.py,main.py}`,
`simulator/tests/{test_scoring.py,test_judge.py}`, `render.yaml`,
`docker-compose.yml`, `.github/workflows/deploy-render.yml`, `CLAUDE.md`,
`TEAM.md`, `RULES.md`, `ORGANIZER.md`, `DEPLOY.md`, `tools/cowork-onboard.py`,
`tools/bootstrap/raif-workshop-setup.cmd`, `tasks/task_01_credit.md`,
`tasks/task_02_invest.md`

**Удаляются:** старые `team_a/{Dockerfile,pyproject.toml,src/main.py,src/db.py}`
и `team_b/**`, `.claude/templates/settings-team-a.json`, `settings-team-b.json`

---

## Фаза 0 — Хирургия репозитория

### Task 0.1: Сохранить переиспользуемое, снести старый монолит

**Files:** перемещение и удаление в `team_a/`, `team_b/`

- [ ] **Step 1: Сохранить UI и llm-хелпер из team_a**

```bash
mkdir -p team_a/retail/src/static team_a/cib/src team_a/backend/src
git mv team_a/src/static/index.html team_a/retail/src/static/index.html
git mv team_a/src/llm.py team_a/cib/src/llm.py
```

- [ ] **Step 2: Удалить старый монолит**

```bash
git rm -r --quiet team_a/Dockerfile team_a/pyproject.toml team_a/src team_b \
  .claude/templates/settings-team-a.json .claude/templates/settings-team-b.json
```

- [ ] **Step 3: Проверить**

Run: `ls team_a team_a/retail/src/static team_a/cib/src`
Expected: `team_a` → `backend cib retail`; виден `index.html`; виден `llm.py`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: снят банк-монолит, сохранены UI и llm-хелпер под новые блоки"
```

---

## Фаза 1 — Блок backend

### Task 1.1: `team_a/backend/src/main.py` — ядро данных

**Files:**
- Create: `team_a/backend/src/main.py`

- [ ] **Step 1: Создать файл**

```python
"""Блок backend — ядро данных банка команды.

Хранит клиентов, транзакции, балансы; отдаёт базовый API. UI нет.
Данные in-memory из seed/*.jsonl. Кредитное хранилище
(POST/GET /credit-applications) добавляет владелец блока в рамках задачи.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")


def _find_seed_dir() -> Path | None:
    """Ищем seed/ — работает и в Docker (/app/seed), и локально."""
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / "seed",
        here.parents[2] / "seed" if len(here.parents) >= 3 else None,
        here.parents[3] / "seed" if len(here.parents) >= 4 else None,
        here.parents[4] / "seed" if len(here.parents) >= 5 else None,
    ]
    for c in candidates:
        if c and c.exists():
            return c
    return None


SEED_DIR = _find_seed_dir()
_clients: list[dict[str, Any]] = []
_clients_by_id: dict[str, dict[str, Any]] = {}
_transactions: list[dict[str, Any]] = []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _load_seed() -> None:
    if not SEED_DIR:
        return
    clients = _load_jsonl(SEED_DIR / "clients.jsonl")
    _clients.extend(clients)
    _clients_by_id.update({c["id"]: c for c in clients})
    _transactions.extend(_load_jsonl(SEED_DIR / "transactions.jsonl"))


_load_seed()

app = FastAPI(title="backend — ядро данных", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "backend",
            "commit": COMMIT, "clients_loaded": len(_clients),
            "transactions_loaded": len(_transactions)}


@app.get("/clients")
async def list_clients(
    segment: str | None = Query(default=None),
    has_overdue: bool | None = None,
    min_income: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    out = _clients
    if segment:
        out = [c for c in out if c.get("segment") == segment]
    if has_overdue is not None:
        out = [c for c in out if bool(c.get("has_overdue_history")) == has_overdue]
    if min_income is not None:
        out = [c for c in out if c.get("income_rub", 0) >= min_income]
    return {"total": len(out), "items": out[:limit]}


@app.get("/clients/{client_id}")
async def get_client(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    return c


@app.get("/transactions/{client_id}")
async def get_transactions(
    client_id: str, limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    txs = [t for t in _transactions if t["client_id"] == client_id]
    txs.sort(key=lambda t: t["ts"], reverse=True)
    return {"total": len(txs), "items": txs[:limit]}


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    from_id = payload.get("from_client_id")
    to_query = (payload.get("to") or "").strip()
    amount = int(payload.get("amount_rub") or 0)
    if from_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail="отправитель не найден")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="укажи положительную сумму")
    if not to_query:
        raise HTTPException(status_code=400, detail="укажи получателя")
    sender = _clients_by_id[from_id]
    if amount > sender["balance_rub"]:
        raise HTTPException(
            status_code=400,
            detail=f"недостаточно средств: на счёте {sender['balance_rub']} ₽",
        )
    receiver: dict[str, Any] | None = None
    if to_query in _clients_by_id and to_query != from_id:
        receiver = _clients_by_id[to_query]
    else:
        tql = to_query.lower()
        for c in _clients:
            if c["id"] != from_id and (tql == c["name"].lower() or tql in c["name"].lower()):
                receiver = c
                break
    now_iso = datetime.now().replace(microsecond=0).isoformat()
    sender["balance_rub"] -= amount
    out_tx = {
        "id": f"t-{100000 + len(_transactions) + 1:08d}",
        "client_id": from_id, "type": "transfer_out", "amount_rub": -amount,
        "ts": now_iso, "counterparty": receiver["name"] if receiver else to_query,
    }
    _transactions.append(out_tx)
    if receiver:
        receiver["balance_rub"] += amount
        _transactions.append({
            "id": f"t-{100000 + len(_transactions) + 1:08d}",
            "client_id": receiver["id"], "type": "transfer_in", "amount_rub": amount,
            "ts": now_iso, "counterparty": sender["name"],
        })
        kind, label = "internal", receiver["name"]
    else:
        kind, label = "external", to_query
    return {
        "status": "ok", "kind": kind, "amount_rub": amount, "to": label,
        "from_client_id": from_id, "new_balance_rub": sender["balance_rub"],
        "tx_id": out_tx["id"], "ts": now_iso,
    }
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('team_a/backend/src/main.py').read()); print('ok')"`
Expected: `ok`

### Task 1.2: `team_a/backend/pyproject.toml` и `Dockerfile`

**Files:**
- Create: `team_a/backend/pyproject.toml`, `team_a/backend/Dockerfile`

- [ ] **Step 1: Создать `team_a/backend/pyproject.toml`**

```toml
[project]
name = "raif-backend"
version = "1.0.0"
description = "Блок backend — ядро данных банка команды"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Создать `team_a/backend/Dockerfile`**

```dockerfile
# Build context = корень монорепо.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7"

COPY seed /app/seed
COPY team_a/backend/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
```

- [ ] **Step 3: Commit**

```bash
git add team_a/backend
git commit -m "feat(backend): блок ядра данных — клиенты, транзакции, переводы"
```

---

## Фаза 2 — Блок retail

### Task 2.1: `team_a/retail/src/main.py` — UI и тонкий прокси

**Files:**
- Create: `team_a/retail/src/main.py`

- [ ] **Step 1: Создать файл**

```python
"""Блок retail — клиентский мобильный банк команды.

UI плюс тонкий слой: за данными ходит в backend, за кредитным решением — в cib.
Своих данных не держит. Вкладку «Кредиты» и /api/credit-apply (оркестрацию
cib + backend) добавляет владелец блока в рамках задачи.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")
CIB_URL = os.environ.get("CIB_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="retail — мобильный банк", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "retail",
            "commit": COMMIT, "backend_url": BACKEND_URL, "cib_url": CIB_URL}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    f = STATIC_DIR / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>Розница</h1>"


async def _backend_get(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BACKEND_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


@app.get("/clients")
async def list_clients(request: Request) -> dict:
    return await _backend_get("/clients", dict(request.query_params))


@app.get("/transactions/{client_id}")
async def transactions(client_id: str, request: Request) -> dict:
    return await _backend_get(f"/transactions/{client_id}", dict(request.query_params))


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{BACKEND_URL}/api/transfer", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('team_a/retail/src/main.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Проверить, что UI на месте**

Run: `ls team_a/retail/src/static/index.html`
Expected: файл существует (перенесён в Task 0.1)

### Task 2.2: `team_a/retail/pyproject.toml` и `Dockerfile`

**Files:**
- Create: `team_a/retail/pyproject.toml`, `team_a/retail/Dockerfile`

- [ ] **Step 1: Создать `team_a/retail/pyproject.toml`**

```toml
[project]
name = "raif-retail"
version = "1.0.0"
description = "Блок retail — клиентский мобильный банк команды"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Создать `team_a/retail/Dockerfile`**

```dockerfile
# Build context = корень монорепо.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "httpx>=0.27"

COPY team_a/retail/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
```

- [ ] **Step 3: Commit**

```bash
git add team_a/retail
git commit -m "feat(retail): мобильный банк — UI и прокси в backend"
```

---

## Фаза 3 — Блок cib

### Task 3.1: `team_a/cib/src/main.py` — каталог и статус

**Files:**
- Create: `team_a/cib/src/main.py`

- [ ] **Step 1: Создать файл**

```python
"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

# Базовый каталог. Кредитный продукт добавляет владелец блока в рамках задачи.
PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
]

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    rows = "".join(
        f"<tr><td>{p['id']}</td><td>{p['kind']}</td><td>{p['name']}</td></tr>"
        for p in PRODUCTS
    )
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<title>cib · Райффайзен</title><style>"
        "body{font-family:system-ui;background:#0c0d10;color:#e8e9ec;padding:32px}"
        "h1{font-weight:500}table{border-collapse:collapse;margin-top:16px}"
        "td,th{border:1px solid #23262f;padding:8px 14px;text-align:left}"
        "</style></head><body>"
        "<h1>cib — корпоратив и бизнес-логика</h1>"
        f"<p>Команда: {TEAM_NAME}. Каталог продуктов:</p>"
        f"<table><tr><th>id</th><th>вид</th><th>название</th></tr>{rows}</table>"
        "</body></html>"
    )
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('team_a/cib/src/main.py').read()); print('ok')"`
Expected: `ok`

### Task 3.2: `team_a/cib/pyproject.toml` и `Dockerfile`

**Files:**
- Create: `team_a/cib/pyproject.toml`, `team_a/cib/Dockerfile`

- [ ] **Step 1: Создать `team_a/cib/pyproject.toml`**

```toml
[project]
name = "raif-cib"
version = "1.0.0"
description = "Блок cib — корпоратив и бизнес-логика банка команды"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 2: Создать `team_a/cib/Dockerfile`**

```dockerfile
# Build context = корень монорепо.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30" \
    "pydantic>=2.7" \
    "httpx>=0.27"

COPY team_a/cib/src /app/src

ENV PYTHONPATH=/app
EXPOSE 8020
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8020}"]
```

- [ ] **Step 3: Commit**

```bash
git add team_a/cib
git commit -m "feat(cib): корпоратив — каталог продуктов и статус-страница"
```

### Task 3.3: Смоук team_a — три блока поднимаются и стыкуются

**Files:** —

- [ ] **Step 1: Поставить зависимости (если ещё не стоят)**

Run: `python3 -m pip install --user --quiet fastapi "uvicorn[standard]" httpx pydantic eval_type_backport`
Expected: без ошибок (eval_type_backport нужен для FastAPI на Python 3.9)

- [ ] **Step 2: Поднять три блока team_a**

```bash
PYTHONPATH=team_a/backend TEAM_NAME=team_a python3 -m uvicorn src.main:app --port 8003 --host 127.0.0.1 &
PYTHONPATH=team_a/cib TEAM_NAME=team_a BACKEND_URL=http://127.0.0.1:8003 python3 -m uvicorn src.main:app --port 8002 --host 127.0.0.1 &
PYTHONPATH=team_a/retail TEAM_NAME=team_a BACKEND_URL=http://127.0.0.1:8003 CIB_URL=http://127.0.0.1:8002 python3 -m uvicorn src.main:app --port 8001 --host 127.0.0.1 &
```

- [ ] **Step 3: Проверить блоки и сквозной поток retail→backend**

Run: `sleep 5; curl -s 127.0.0.1:8003/health; echo; curl -s 127.0.0.1:8002/health; echo; curl -s 127.0.0.1:8001/health; echo; curl -s '127.0.0.1:8001/clients?limit=2'`
Expected: backend — `clients_loaded:500`; cib — `block:"cib"`; retail — `block:"retail"`; `/clients` через retail возвращает двух клиентов из backend

- [ ] **Step 4: Остановить блоки**

Run: `pkill -f "uvicorn src.main:app"`

### Task 3.4: Создать team_b копией team_a

**Files:**
- Create: `team_b/` (копия `team_a/`)

- [ ] **Step 1: Скопировать**

```bash
cp -r team_a team_b
```

- [ ] **Step 2: Поправить COPY-пути в трёх Dockerfile team_b**

В `team_b/backend/Dockerfile` заменить `COPY team_a/backend/src` на `COPY team_b/backend/src`.
В `team_b/retail/Dockerfile` заменить `COPY team_a/retail/src` на `COPY team_b/retail/src`.
В `team_b/cib/Dockerfile` заменить `COPY team_a/cib/src` на `COPY team_b/cib/src`.

- [ ] **Step 3: Проверить**

Run: `grep -h "^COPY team" team_b/*/Dockerfile`
Expected: все три строки ссылаются на `team_b/...`

- [ ] **Step 4: Commit**

```bash
git add team_b
git commit -m "feat: team_b — идентичная копия трёх блоков team_a"
```

---

## Фаза 4 — Симулятор: переоценка под три блока

### Task 4.1: `simulator/src/scoring.py` — RUBRIC_MAX=20 (TDD)

**Files:**
- Modify: `simulator/src/scoring.py:9` (RUBRIC_MAX)
- Modify: `simulator/tests/test_scoring.py` (значения под 20)

- [ ] **Step 1: Переписать `simulator/tests/test_scoring.py`**

```python
from src.scoring import compute_round, compute_unreachable, rubric_total


def test_rubric_total_clamps():
    assert rubric_total([2] * 10) == 20
    assert rubric_total([2] * 11) == 20  # сверху зажато
    assert rubric_total([-5, 0, 0, 0, 0, 0, 0, 0, 0, 0]) == 0  # снизу зажато


def test_no_change_gives_zero_delta():
    r = compute_round(s_now=10, s_prev=10, s_base=4, client_base=600)
    assert r == {"delta": 0, "client_base": 600, "target": 600, "changed": False}


def test_full_completion_from_baseline():
    # S 4 -> 20: improvement = 16/20 = 0.8, target = round(500 * 1.48) = 740
    r = compute_round(s_now=20, s_prev=4, s_base=4, client_base=500)
    assert r["target"] == 740
    assert r["delta"] == 240
    assert r["changed"] is True


def test_partial_progress():
    # S 4 -> 12: improvement = 8/20 = 0.4, target = round(500 * 1.24) = 620
    r = compute_round(s_now=12, s_prev=4, s_base=4, client_base=500)
    assert r["target"] == 620
    assert r["delta"] == 120


def test_regression_below_baseline_loses_clients():
    # S 12 -> 0: improvement = (0-4)/20 = -0.2, target = round(500 * 0.88) = 440
    r = compute_round(s_now=0, s_prev=12, s_base=4, client_base=620)
    assert r["target"] == 440
    assert r["delta"] == -180


def test_unreachable_team_drops_base():
    r = compute_unreachable(client_base=700)
    assert r["target"] == 560
    assert r["delta"] == -140
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `python3 -m pytest simulator/tests/test_scoring.py -q`
Expected: FAIL — старый `RUBRIC_MAX=12` даёт другие числа

- [ ] **Step 3: Поменять RUBRIC_MAX в `simulator/src/scoring.py`**

Заменить строку `RUBRIC_MAX = 12   # 6 критериев по 2 балла` на:

```python
RUBRIC_MAX = 20   # 10 критериев по 2 балла (3 блока команды)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `python3 -m pytest simulator/tests/test_scoring.py -q`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/src/scoring.py simulator/tests/test_scoring.py
git commit -m "feat(simulator): RUBRIC_MAX=20 — 10 критериев трёх блоков"
```

### Task 4.2: `simulator/src/probe.py` — probe трёх блоков

**Files:**
- Modify: `simulator/src/probe.py` (полная замена)

- [ ] **Step 1: Заменить файл целиком**

```python
"""Probe — снятие состояния трёх блоков команды. Закрытый список проверок.

Фиксированные клиенты из seed/clients.jsonl: сильный c-01394 (premium),
слабый c-01434 (mass, просрочки).
"""
from __future__ import annotations

import json
import time

import httpx

STRONG_APPLICANT = "c-01394"
WEAK_APPLICANT = "c-01434"
PROBE_TIMEOUT_S = 20.0

_APPROVE = ("approv", "одобр", "выдан", "accept", "положительн")
_REJECT = ("reject", "отказ", "decline", "denied", "отрицательн")


def _safe_json(resp: httpx.Response) -> dict:
    try:
        d = resp.json()
        return d if isinstance(d, dict) else {"_list": d}
    except (json.JSONDecodeError, ValueError):
        return {}


def _decision(body: dict) -> str | None:
    """Вердикт из ответа независимо от формы. → approved|rejected|None."""
    if not isinstance(body, dict):
        return None
    for k in ("decision", "status", "verdict", "result", "approved"):
        if k in body:
            v = str(body[k]).lower()
            if v in ("true", "ok") or any(w in v for w in _APPROVE):
                return "approved"
            if v == "false" or any(w in v for w in _REJECT):
                return "rejected"
    blob = json.dumps(body, ensure_ascii=False).lower()
    a, r = any(w in blob for w in _APPROVE), any(w in blob for w in _REJECT)
    if a and not r:
        return "approved"
    if r and not a:
        return "rejected"
    return None


def _explanation(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for k in ("explanation", "reason", "message", "comment", "text", "detail"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def _probe_backend(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        r = await client.get(f"{url}/clients/{STRONG_APPLICANT}")
        c["serves_client"] = r.status_code == 200 and "id" in _safe_json(r)
    except httpx.HTTPError:
        c["serves_client"] = False
    try:
        r = await client.post(
            f"{url}/credit-applications",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000,
                  "term_months": 12, "decision": "approved"},
        )
        c["accepts_application"] = r.status_code in (200, 201)
    except httpx.HTTPError:
        c["accepts_application"] = False
    try:
        r = await client.get(f"{url}/credit-applications")
        c["lists_applications"] = (
            r.status_code == 200 and isinstance(_safe_json(r).get("items"), list)
        )
    except httpx.HTTPError:
        c["lists_applications"] = False
    return snap


async def _probe_cib(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        r = await client.get(f"{url}/products")
        blob = json.dumps(_safe_json(r).get("items", []), ensure_ascii=False).lower()
        c["has_credit_product"] = r.status_code == 200 and (
            "кредит" in blob or "credit" in blob
        )
    except httpx.HTTPError:
        c["has_credit_product"] = False
    t0 = time.time()
    try:
        r = await client.post(
            f"{url}/credit/decide",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000, "term_months": 12},
        )
        c["decide_status"] = r.status_code
        c["decide_latency_ms"] = int((time.time() - t0) * 1000)
        c["decision_strong"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["decide_status"] = 0
        c["decide_latency_ms"] = -1
        c["decision_strong"] = None
    try:
        r = await client.post(
            f"{url}/credit/decide",
            json={"client_id": WEAK_APPLICANT, "amount_rub": 900000, "term_months": 6},
        )
        c["decision_weak"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["decision_weak"] = None
    ds, dw = c.get("decision_strong"), c.get("decision_weak")
    c["decision_is_discriminating"] = ds is not None and dw is not None and ds != dw
    return snap


async def _probe_retail(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        root = await client.get(f"{url}/")
        html = root.text.lower() if root.status_code == 200 else ""
    except httpx.HTTPError:
        html = ""
    c["credit_in_ui"] = "кредит" in html
    c["transfer_in_ui"] = "перевод" in html
    try:
        r = await client.post(
            f"{url}/api/credit-apply",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000, "term_months": 12},
        )
        c["credit_apply_status"] = r.status_code
        c["credit_apply_decision"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["credit_apply_status"] = 0
        c["credit_apply_decision"] = None
    try:
        r = await client.post(
            f"{url}/api/credit-apply",
            json={"client_id": WEAK_APPLICANT, "amount_rub": 900000, "term_months": 6},
        )
        expl = _explanation(_safe_json(r))
        c["credit_apply_explained"] = bool(expl) and len(expl) > 40
    except httpx.HTTPError:
        c["credit_apply_explained"] = False
    try:
        cl = await client.get(f"{url}/clients?limit=2")
        ids = [x["id"] for x in _safe_json(cl).get("items", []) if "id" in x]
        if len(ids) >= 2:
            rt = await client.post(
                f"{url}/api/transfer",
                json={"from_client_id": ids[0], "to": ids[1], "amount_rub": 1000},
            )
            c["transfer_ok"] = rt.status_code == 200
        else:
            c["transfer_ok"] = False
    except httpx.HTTPError:
        c["transfer_ok"] = False
    return snap


async def probe_team(team: str, urls: dict) -> dict:
    """Снять снапшот трёх блоков команды. urls = {retail, cib, backend}.

    Возвращает {team, blocks: {backend, cib, retail}}, каждый блок —
    {reachable, commit, checks}. Никогда не бросает.
    """
    out: dict = {"team": team, "blocks": {}}
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        out["blocks"]["backend"] = await _probe_backend(
            client, urls["backend"].rstrip("/"))
        out["blocks"]["cib"] = await _probe_cib(client, urls["cib"].rstrip("/"))
        out["blocks"]["retail"] = await _probe_retail(client, urls["retail"].rstrip("/"))
    return out
```

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('simulator/src/probe.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add simulator/src/probe.py
git commit -m "feat(simulator): probe трёх блоков команды (backend, cib, retail)"
```

### Task 4.3: `simulator/src/judge.py` — рубрика 10 критериев (TDD)

**Files:**
- Modify: `simulator/src/judge.py` (полная замена)
- Modify: `simulator/tests/test_judge.py` (полная замена)

- [ ] **Step 1: Переписать `simulator/tests/test_judge.py`**

```python
from src.judge import RUBRIC_CRITERIA, fallback_rubric, parse_judge_response


def _baseline_team():
    return {
        "team": "team_a",
        "blocks": {
            "backend": {"reachable": True, "checks": {
                "serves_client": True, "accepts_application": False,
                "lists_applications": False}},
            "cib": {"reachable": True, "checks": {
                "has_credit_product": False, "decide_status": 0,
                "decision_is_discriminating": False}},
            "retail": {"reachable": True, "checks": {
                "credit_in_ui": False, "credit_apply_status": 0,
                "credit_apply_decision": None, "credit_apply_explained": False,
                "transfer_ok": True}},
        },
    }


def _done_team():
    return {
        "team": "team_a",
        "blocks": {
            "backend": {"reachable": True, "checks": {
                "serves_client": True, "accepts_application": True,
                "lists_applications": True}},
            "cib": {"reachable": True, "checks": {
                "has_credit_product": True, "decide_status": 200,
                "decision_is_discriminating": True}},
            "retail": {"reachable": True, "checks": {
                "credit_in_ui": True, "credit_apply_status": 200,
                "credit_apply_decision": "approved", "credit_apply_explained": True,
                "transfer_ok": True}},
        },
    }


def test_rubric_has_ten_criteria():
    assert len(RUBRIC_CRITERIA) == 10


def test_fallback_baseline_scores_four():
    scores, reason = fallback_rubric(_baseline_team())
    assert scores == [2, 0, 0, 0, 0, 0, 0, 0, 0, 2]
    assert isinstance(reason, str) and reason


def test_fallback_done_scores_twenty():
    scores, _ = fallback_rubric(_done_team())
    assert scores == [2] * 10


def test_parse_judge_response_valid():
    raw = ('{"team_a": {"scores": [2,2,2,2,2,0,0,0,0,2], "reason": "ок"}, '
           '"team_b": {"scores": [2,0,0,0,0,0,0,0,0,2], "reason": "старт"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"][4] == 2
    assert parsed["team_b"]["reason"] == "старт"


def test_parse_judge_response_code_fence():
    raw = '```json\n{"team_a": {"scores": [1,1,1,1,1,1,1,1,1,1], "reason": "x"}}\n```'
    assert parse_judge_response(raw)["team_a"]["scores"] == [1] * 10


def test_parse_judge_response_garbage_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("не json")
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `python3 -m pytest simulator/tests/test_judge.py -q`
Expected: FAIL — старый judge даёт 6 критериев

- [ ] **Step 3: Заменить `simulator/src/judge.py` целиком**

```python
"""Судья: рубрика трёх блоков, скриптовый fallback, парсинг ответа LLM.

10 критериев (3 backend + 3 cib + 4 retail). Чистые функции fallback_rubric и
parse_judge_response покрыты тестами; LLM-вызов — в judge_round.
"""
from __future__ import annotations

import json

from src.llm import LLMError, ask_llm

RUBRIC_CRITERIA: list[str] = [
    "C1 (backend). Отдаёт данные клиента по запросу.",
    "C2 (backend). Принимает заявку на кредит на хранение.",
    "C3 (backend). Отдаёт список поданных заявок.",
    "C4 (cib). В каталоге продуктов есть кредитный продукт.",
    "C5 (cib). Ручка решения по заявке работает (отвечает без ошибки).",
    "C6 (cib). Решение опирается на данные клиента: сильный и слабый "
    "заявители получают разные вердикты.",
    "C7 (retail). Вкладка «Кредиты» присутствует в интерфейсе.",
    "C8 (retail). Сквозная подача заявки доходит до реального решения.",
    "C9 (retail). Отказ сопровождается человеческим объяснением.",
    "C10 (retail). Нет регрессии: переводы по-прежнему работают.",
]


def fallback_rubric(team_snapshot: dict) -> tuple[list[int], str]:
    """Механически вывести 10 баллов из probe-снапшота команды, без LLM."""
    blocks = team_snapshot.get("blocks", {})
    b = blocks.get("backend", {}).get("checks", {})
    ci = blocks.get("cib", {}).get("checks", {})
    r = blocks.get("retail", {}).get("checks", {})
    scores = [
        2 if b.get("serves_client") else 0,
        2 if b.get("accepts_application") else 0,
        2 if b.get("lists_applications") else 0,
        2 if ci.get("has_credit_product") else 0,
        2 if ci.get("decide_status") == 200 else 0,
        2 if ci.get("decision_is_discriminating") else 0,
        2 if r.get("credit_in_ui") else 0,
        2 if (r.get("credit_apply_status") == 200
              and r.get("credit_apply_decision") is not None) else 0,
        2 if r.get("credit_apply_explained") else 0,
        2 if r.get("transfer_ok") else 0,
    ]
    done = sum(1 for s in scores if s == 2)
    parts = [f"готово критериев: {done} из 10"]
    if scores[6] and scores[7]:
        parts.append("вкладка кредитов и сквозная заявка работают")
    if not scores[9]:
        parts.append("переводы сломаны")
    if scores[0] and not scores[1]:
        parts.append("backend ещё не принимает заявки")
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


_JUDGE_SYSTEM = (
    "Ты — рыночный аналитик розничного банка. Тебе дают результаты технической "
    "проверки двух команд; каждая команда — три блока (backend, cib, retail), "
    "которые вместе строят функцию «Кредиты». Оцени каждую команду по 10 "
    "критериям, каждый строго 0, 1 или 2 балла. Будь одинаково строг к обеим. "
    "Верни СТРОГО JSON без пояснений."
)


def _block_checks(team_snapshot: dict) -> dict:
    return {
        name: team_snapshot.get("blocks", {}).get(name, {})
        for name in ("backend", "cib", "retail")
    }


def _build_judge_prompt(snap_a: dict, snap_b: dict) -> str:
    criteria = "\n".join(RUBRIC_CRITERIA)
    return (
        f"Критерии (каждый 0, 1 или 2 балла):\n{criteria}\n\n"
        f"Команда A, блоки:\n{json.dumps(_block_checks(snap_a), ensure_ascii=False)}\n\n"
        f"Команда B, блоки:\n{json.dumps(_block_checks(snap_b), ensure_ascii=False)}\n\n"
        'Верни JSON: {"team_a": {"scores": [c1..c10], "reason": "1-2 предложения '
        'по-русски про факты"}, "team_b": {"scores": [...], "reason": "..."}}'
    )


async def judge_round(snap_a: dict, snap_b: dict) -> dict:
    """Оценить обе команды одним вызовом LLM. При сбое — скриптовый fallback."""
    result: dict[str, dict] = {}
    try:
        raw = await ask_llm(
            _build_judge_prompt(snap_a, snap_b),
            system=_JUDGE_SYSTEM, max_tokens=600, temperature=0.0,
        )
        parsed = parse_judge_response(raw)
        for team in ("team_a", "team_b"):
            block = parsed.get(team) or {}
            scores = block.get("scores")
            if not (isinstance(scores, list) and len(scores) == 10):
                raise ValueError(f"судья не дал 10 баллов для {team}")
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

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `python3 -m pytest simulator/tests/ -q`
Expected: PASS — все тесты зелёные

- [ ] **Step 5: Commit**

```bash
git add simulator/src/judge.py simulator/tests/test_judge.py
git commit -m "feat(simulator): рубрика 10 критериев трёх блоков, fallback и судья"
```

### Task 4.4: `simulator/src/main.py` — вложенные URL и probe команд

**Files:**
- Modify: `simulator/src/main.py` (полная замена)

- [ ] **Step 1: Заменить файл целиком**

```python
"""Симулятор клиентов AI-воркшопа — три блока на команду.

Pull-моделью опрашивает /health всех 6 банк-сервисов; на новый git-коммит
любого блока команды снимает probe трёх блоков, оценивает рубрикой из 10
критериев (LLM-судья + fallback) и двигает клиентскую базу команды.
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
from src.probe import probe_team
from src.scoring import B0, compute_round, compute_unreachable, rubric_total

BANK_URLS = {
    "team_a": {
        "retail": os.environ.get("A_RETAIL_URL", "http://localhost:8001").rstrip("/"),
        "cib": os.environ.get("A_CIB_URL", "http://localhost:8002").rstrip("/"),
        "backend": os.environ.get("A_BACKEND_URL", "http://localhost:8003").rstrip("/"),
    },
    "team_b": {
        "retail": os.environ.get("B_RETAIL_URL", "http://localhost:8011").rstrip("/"),
        "cib": os.environ.get("B_CIB_URL", "http://localhost:8012").rstrip("/"),
        "backend": os.environ.get("B_BACKEND_URL", "http://localhost:8013").rstrip("/"),
    },
}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))

_state: dict[str, dict] = {
    t: {"client_base": B0, "last_commit": None, "baseline_score": None,
        "last_score": None}
    for t in ("team_a", "team_b")
}
_events: list[dict] = []
_eval_lock = asyncio.Lock()


def _pool():
    return getattr(app.state, "pool", None)


def _commit_fingerprint(snapshot: dict) -> str:
    """Склейка git-коммитов трёх блоков — меняется на любой деплой команды."""
    blocks = snapshot.get("blocks", {})
    return "|".join(
        str(blocks.get(name, {}).get("commit")) for name in ("backend", "cib", "retail")
    )


async def _load_state() -> None:
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
        await dbmod.upsert_state(pool, team, st["client_base"], st["last_commit"],
                                 st["baseline_score"], st["last_score"])
        await dbmod.add_event(pool, team, _commit_fingerprint(snapshot), delta,
                              st["client_base"], scores, reason, snapshot, judge)
    _events.insert(0, {
        "team": team, "ts": None, "commit": _commit_fingerprint(snapshot),
        "delta": delta, "client_base_after": st["client_base"],
        "rubric": scores, "reason": reason, "judge": judge,
    })
    del _events[60:]


async def _baseline() -> None:
    snap_a = await probe_team("team_a", BANK_URLS["team_a"])
    snap_b = await probe_team("team_b", BANK_URLS["team_b"])
    verdict = await judge_round(snap_a, snap_b)
    for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
        s_base = rubric_total(verdict[team]["scores"])
        _state[team] = {
            "client_base": B0, "last_commit": _commit_fingerprint(snap),
            "baseline_score": s_base, "last_score": s_base,
        }
    pool = _pool()
    if pool is not None:
        for team in ("team_a", "team_b"):
            st = _state[team]
            await dbmod.upsert_state(pool, team, st["client_base"], st["last_commit"],
                                     st["baseline_score"], st["last_score"])


async def evaluate_round() -> dict:
    """Раунд: probe трёх блоков обеих команд, один вызов судьи, обновление баз."""
    async with _eval_lock:
        snap_a = await probe_team("team_a", BANK_URLS["team_a"])
        snap_b = await probe_team("team_b", BANK_URLS["team_b"])
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
            all_down = all(
                not snap["blocks"][b]["reachable"] for b in ("backend", "cib", "retail")
            )
            if all_down:
                r = compute_unreachable(st["client_base"])
                reason = "Все три блока недоступны — клиенты не могут войти."
                s_now = st["last_score"]
            else:
                s_now = rubric_total(scores)
                r = compute_round(s_now, st["last_score"], s_base, st["client_base"])
            st["client_base"] = r["client_base"]
            st["last_score"] = s_now
            st["last_commit"] = _commit_fingerprint(snap)
            await _persist(team, snap, scores, reason, judge, r["delta"])
            out[team] = {"delta": r["delta"], "client_base": st["client_base"],
                         "reason": reason, "judge": judge}
        return out


async def _poll_loop() -> None:
    """Фон: раз в POLL_INTERVAL_S смотреть коммиты блоков, ловить деплой."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            changed = False
            for team in ("team_a", "team_b"):
                snap = await probe_team(team, BANK_URLS[team])
                fp = _commit_fingerprint(snap)
                if "local" not in fp and "None" not in fp \
                        and fp != _state[team]["last_commit"]:
                    changed = True
            if changed:
                await evaluate_round()
        except Exception as exc:  # noqa: BLE001
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


app = FastAPI(title="Симулятор клиентов", version="2.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "simulator", "db": _pool() is not None,
            "banks": BANK_URLS}


@app.get("/state")
async def state() -> dict:
    return {
        "teams": {t: {"client_base": s["client_base"], "last_score": s["last_score"],
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
git commit -m "feat(simulator): probe и оценка трёх блоков на команду"
```

Примечание: `simulator/src/static/index.html` (табло) и `simulator/src/db.py`
не меняются — табло читает то же `/state`, схема БД та же.

---

## Фаза 5 — Инфраструктура

### Task 5.1: Переписать `render.yaml` (7 сервисов)

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
# Render Blueprint: 2 команды × 3 блока + симулятор.
#
# Однократно в UI Render: New → Blueprint → Connect repo → Apply.
# В env-группе ai-workshop-shared задать OPENAI_API_KEY и ADMIN_TOKEN.

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
    name: raif-a-backend
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_a/backend/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_a/backend/**, seed/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_a

  - type: web
    name: raif-a-cib
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_a/cib/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_a/cib/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_a
      - key: BACKEND_URL
        value: https://raif-a-backend.onrender.com

  - type: web
    name: raif-a-retail
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_a/retail/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_a/retail/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_a
      - key: BACKEND_URL
        value: https://raif-a-backend.onrender.com
      - key: CIB_URL
        value: https://raif-a-cib.onrender.com

  - type: web
    name: raif-b-backend
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_b/backend/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_b/backend/**, seed/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_b

  - type: web
    name: raif-b-cib
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_b/cib/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_b/cib/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_b
      - key: BACKEND_URL
        value: https://raif-b-backend.onrender.com

  - type: web
    name: raif-b-retail
    runtime: docker
    plan: free
    region: oregon
    branch: main
    autoDeploy: true
    dockerfilePath: ./team_b/retail/Dockerfile
    dockerContext: .
    rootDir: .
    buildFilter:
      paths: [team_b/retail/**, render.yaml]
    envVars:
      - fromGroup: ai-workshop-shared
      - key: TEAM_NAME
        value: team_b
      - key: BACKEND_URL
        value: https://raif-b-backend.onrender.com
      - key: CIB_URL
        value: https://raif-b-cib.onrender.com

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
      - key: ACTIVE_TASK
        value: credit
      - key: A_BACKEND_URL
        value: https://raif-a-backend.onrender.com
      - key: A_CIB_URL
        value: https://raif-a-cib.onrender.com
      - key: A_RETAIL_URL
        value: https://raif-a-retail.onrender.com
      - key: B_BACKEND_URL
        value: https://raif-b-backend.onrender.com
      - key: B_CIB_URL
        value: https://raif-b-cib.onrender.com
      - key: B_RETAIL_URL
        value: https://raif-b-retail.onrender.com
      - key: DATABASE_URL
        fromDatabase:
          name: raif-workshop-db
          property: connectionString
```

- [ ] **Step 2: Проверить YAML**

Run: `python3 -c "import yaml; d=yaml.safe_load(open('render.yaml')); print('сервисов:', len(d['services']))"`
Expected: `сервисов: 7`

- [ ] **Step 3: Commit**

```bash
git add render.yaml
git commit -m "feat: render.yaml — 7 сервисов (2 команды × 3 блока + симулятор)"
```

### Task 5.2: Переписать `.github/workflows/deploy-render.yml`

**Files:**
- Modify: `.github/workflows/deploy-render.yml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
name: Deploy services via Render Deploy Hooks

# Дёргаем Deploy Hook сервиса, чья папка менялась. Секреты в GitHub →
# Settings → Secrets and variables → Actions:
#   RENDER_HOOK_A_BACKEND, RENDER_HOOK_A_CIB, RENDER_HOOK_A_RETAIL,
#   RENDER_HOOK_B_BACKEND, RENDER_HOOK_B_CIB, RENDER_HOOK_B_RETAIL,
#   RENDER_HOOK_SIMULATOR

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      services:
        description: "Что передеплоить (через запятую или 'all')"
        required: false
        default: "all"

permissions:
  contents: read

concurrency:
  group: render-deploy
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - name: Detect and deploy
        env:
          MANUAL: ${{ inputs.services }}
          EVENT: ${{ github.event_name }}
          BEFORE: ${{ github.event.before }}
          AFTER: ${{ github.sha }}
          HOOK_A_BACKEND: ${{ secrets.RENDER_HOOK_A_BACKEND }}
          HOOK_A_CIB: ${{ secrets.RENDER_HOOK_A_CIB }}
          HOOK_A_RETAIL: ${{ secrets.RENDER_HOOK_A_RETAIL }}
          HOOK_B_BACKEND: ${{ secrets.RENDER_HOOK_B_BACKEND }}
          HOOK_B_CIB: ${{ secrets.RENDER_HOOK_B_CIB }}
          HOOK_B_RETAIL: ${{ secrets.RENDER_HOOK_B_RETAIL }}
          HOOK_SIMULATOR: ${{ secrets.RENDER_HOOK_SIMULATOR }}
        run: |
          set -e
          # имя → "путь-папки:переменная-хука"
          MAP="a-backend:team_a/backend:HOOK_A_BACKEND
          a-cib:team_a/cib:HOOK_A_CIB
          a-retail:team_a/retail:HOOK_A_RETAIL
          b-backend:team_b/backend:HOOK_B_BACKEND
          b-cib:team_b/cib:HOOK_B_CIB
          b-retail:team_b/retail:HOOK_B_RETAIL
          simulator:simulator:HOOK_SIMULATOR"

          if [ "$EVENT" = "workflow_dispatch" ]; then
            CHANGED="__manual__"
          elif [ -z "$BEFORE" ] || [ "$BEFORE" = "0000000000000000000000000000000000000000" ]; then
            CHANGED=$(git ls-files)
          else
            CHANGED=$(git diff --name-only "$BEFORE" "$AFTER" || true)
          fi
          RENDER_ALL=no
          echo "$CHANGED" | grep -qE "^(render\.yaml|seed/)" && RENDER_ALL=yes

          echo "$MAP" | while IFS=: read -r NAME PATHPREFIX HOOKVAR; do
            [ -z "$NAME" ] && continue
            HIT=no
            if [ "$EVENT" = "workflow_dispatch" ]; then
              REQ="${MANUAL:-all}"
              { [ "$REQ" = "all" ] || echo ",$REQ," | grep -q ",$NAME,"; } && HIT=yes
            else
              echo "$CHANGED" | grep -qE "^${PATHPREFIX}/" && HIT=yes
              [ "$RENDER_ALL" = "yes" ] && HIT=yes
            fi
            [ "$HIT" != "yes" ] && { echo "skip $NAME"; continue; }
            HOOK=$(eval echo "\$$HOOKVAR")
            if [ -z "$HOOK" ]; then
              echo "::warning::секрет для $NAME не задан — пропуск"
            else
              curl -fsS -X POST "$HOOK" && echo " → triggered raif-$NAME"
            fi
          done
```

- [ ] **Step 2: Проверить YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-render.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-render.yml
git commit -m "feat: deploy-render.yml — деплой-хуки 7 сервисов"
```

### Task 5.3: Переписать `docker-compose.yml`

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Заменить файл целиком**

```yaml
# AI-воркшоп — локальный запуск: docker-compose up --build
# Команда A: retail 8001, cib 8002, backend 8003
# Команда B: retail 8011, cib 8012, backend 8013
# Табло: 8000

services:
  a_backend:
    build: { context: ., dockerfile: team_a/backend/Dockerfile }
    container_name: raif-a-backend
    environment: { TEAM_NAME: team_a }
    ports: ["8003:8020"]
    networks: [raif]

  a_cib:
    build: { context: ., dockerfile: team_a/cib/Dockerfile }
    container_name: raif-a-cib
    environment:
      TEAM_NAME: team_a
      BACKEND_URL: http://a_backend:8020
    ports: ["8002:8020"]
    networks: [raif]

  a_retail:
    build: { context: ., dockerfile: team_a/retail/Dockerfile }
    container_name: raif-a-retail
    environment:
      TEAM_NAME: team_a
      BACKEND_URL: http://a_backend:8020
      CIB_URL: http://a_cib:8020
    ports: ["8001:8020"]
    networks: [raif]

  b_backend:
    build: { context: ., dockerfile: team_b/backend/Dockerfile }
    container_name: raif-b-backend
    environment: { TEAM_NAME: team_b }
    ports: ["8013:8020"]
    networks: [raif]

  b_cib:
    build: { context: ., dockerfile: team_b/cib/Dockerfile }
    container_name: raif-b-cib
    environment:
      TEAM_NAME: team_b
      BACKEND_URL: http://b_backend:8020
    ports: ["8012:8020"]
    networks: [raif]

  b_retail:
    build: { context: ., dockerfile: team_b/retail/Dockerfile }
    container_name: raif-b-retail
    environment:
      TEAM_NAME: team_b
      BACKEND_URL: http://b_backend:8020
      CIB_URL: http://b_cib:8020
    ports: ["8011:8020"]
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
    container_name: raif-simulator
    environment:
      A_BACKEND_URL: http://a_backend:8020
      A_CIB_URL: http://a_cib:8020
      A_RETAIL_URL: http://a_retail:8020
      B_BACKEND_URL: http://b_backend:8020
      B_CIB_URL: http://b_cib:8020
      B_RETAIL_URL: http://b_retail:8020
      DATABASE_URL: postgres://sim:sim@postgres:5432/simulator
      ADMIN_TOKEN: localdev
      POLL_INTERVAL_S: "30"
    depends_on: [a_backend, a_cib, a_retail, b_backend, b_cib, b_retail, postgres]
    ports: ["8000:8000"]
    networks: [raif]

networks:
  raif:
    driver: bridge
```

- [ ] **Step 2: Проверить YAML**

Run: `python3 -c "import yaml; d=yaml.safe_load(open('docker-compose.yml')); print('сервисов:', len(d['services']))"`
Expected: `сервисов: 8`

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-compose — 6 банк-блоков, симулятор, Postgres"
```

---

## Фаза 6 — Онбординг и права

### Task 6.1: Шесть шаблонов permissions

**Files:**
- Create: `.claude/templates/settings-team_a-retail.json`, `-team_a-cib.json`,
  `-team_a-backend.json`, `-team_b-retail.json`, `-team_b-cib.json`, `-team_b-backend.json`

- [ ] **Step 1: Создать `.claude/templates/settings-team_a-retail.json`**

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "deny": [
      "Read(team_b/**)",
      "Edit(team_b/**)",
      "Write(team_b/**)",
      "Edit(team_a/cib/**)",
      "Write(team_a/cib/**)",
      "Edit(team_a/backend/**)",
      "Write(team_a/backend/**)",
      "Edit(simulator/**)",
      "Write(simulator/**)",
      "Edit(seed/**)",
      "Write(seed/**)",
      "Edit(render.yaml)",
      "Write(render.yaml)",
      "Edit(.github/**)",
      "Write(.github/**)"
    ],
    "allow": [
      "Edit(team_a/retail/**)",
      "Write(team_a/retail/**)",
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

- [ ] **Step 2: Создать остальные пять**

Каждый файл — копия Step 1 с заменой двух вещей: пары (команда, блок) в путях
`allow` и в `deny`. Правило: `allow` Edit/Write — только `team_<X>/<block>/**`;
`deny` Edit/Write — два других блока своей команды; `deny` Read/Edit/Write —
вся другая команда (`team_<Y>/**`). Конкретно:

- `settings-team_a-cib.json`: allow `Edit/Write(team_a/cib/**)`; deny `Edit/Write` для `team_a/retail/**` и `team_a/backend/**`; deny `Read/Edit/Write(team_b/**)`.
- `settings-team_a-backend.json`: allow `Edit/Write(team_a/backend/**)`; deny `Edit/Write` для `team_a/retail/**` и `team_a/cib/**`; deny `Read/Edit/Write(team_b/**)`.
- `settings-team_b-retail.json`: allow `Edit/Write(team_b/retail/**)`, `Read(team_b/**)`; deny `Edit/Write` для `team_b/cib/**` и `team_b/backend/**`; deny `Read/Edit/Write(team_a/**)`.
- `settings-team_b-cib.json`: allow `Edit/Write(team_b/cib/**)`, `Read(team_b/**)`; deny `Edit/Write` для `team_b/retail/**` и `team_b/backend/**`; deny `Read/Edit/Write(team_a/**)`.
- `settings-team_b-backend.json`: allow `Edit/Write(team_b/backend/**)`, `Read(team_b/**)`; deny `Edit/Write` для `team_b/retail/**` и `team_b/cib/**`; deny `Read/Edit/Write(team_a/**)`.

Блок `Bash(...)` в `allow` во всех шести — без изменений.

- [ ] **Step 3: Проверить JSON всех шести**

Run: `python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('.claude/templates/settings-team_*.json')]; print('ok', len(glob.glob('.claude/templates/settings-team_*.json')))"`
Expected: `ok 6`

- [ ] **Step 4: Commit**

```bash
git add .claude/templates/
git commit -m "feat: 6 шаблонов permissions — команда × блок, слепота между командами"
```

### Task 6.2: `tools/cowork-onboard.py` — поле WORKSHOP_BLOCK

**Files:**
- Modify: `tools/cowork-onboard.py` (функция `main`)

- [ ] **Step 1: В функции `main()` дополнить вывод сводки блоком**

Найти строку `print(f"WORKSHOP_TEAM={info.get('WORKSHOP_TEAM', '')}", flush=True)`
и сразу после неё добавить:

```python
    print(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK', '')}", flush=True)
```

Найти строку
`ok(f"WORKSHOP_TEAM={info.get('WORKSHOP_TEAM', '?')}  "` и в её f-строке
после `WORKSHOP_TEAM=...` дописать ` WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK','?')}`
(в той же выводимой строке).

- [ ] **Step 2: Проверить синтаксис**

Run: `python3 -c "import ast; ast.parse(open('tools/cowork-onboard.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add tools/cowork-onboard.py
git commit -m "feat(tools): cowork-onboard читает и печатает WORKSHOP_BLOCK"
```

### Task 6.3: Bootstrap — поле Block в $Members

**Files:**
- Modify: `tools/bootstrap/raif-workshop-setup.cmd`

- [ ] **Step 1: Найти хэш `$Members`**

Run: `grep -n "Team=" tools/bootstrap/raif-workshop-setup.cmd`
Expected: семь строк участников с `Team='...'`

- [ ] **Step 2: Добавить поле `Block` каждому участнику**

В каждой из семи строк `$Members` после `Team='...'` добавить `Block='...'`.
Дефолтная раскладка (по одному на блок в команде, организатор поправит):
участники 1,2,3 команды A → `retail`, `cib`, `backend`; участники 4,5,6
команды B → `retail`, `cib`, `backend`; участник 7 (Виталий) → `Block='host'`.

- [ ] **Step 3: Дописать `WORKSHOP_BLOCK` в info-файл**

Найти строку `WORKSHOP_TEAM=$($cfg.Team)` (внутри here-string `$infoText`)
и сразу после неё добавить строку:

```
WORKSHOP_BLOCK=$($cfg.Block)
```

- [ ] **Step 4: Проверить, что концы строк остались CRLF**

Run: `python3 -c "b=open('tools/bootstrap/raif-workshop-setup.cmd','rb').read(); print('CRLF ok' if b.count(b'\r\n')>0 and b.count(b'\n')==b.count(b'\r\n') else 'СМЕШАНО')"`
Expected: `CRLF ok`

- [ ] **Step 5: Commit**

```bash
git add tools/bootstrap/raif-workshop-setup.cmd
git commit -m "feat(bootstrap): info-файл пишет WORKSHOP_BLOCK"
```

### Task 6.4: Переписать `CLAUDE.md`, `TEAM.md`, `RULES.md`

**Files:**
- Modify: `CLAUDE.md`, `TEAM.md`, `RULES.md`

- [ ] **Step 1: Переписать `CLAUDE.md`**

Онбординг агента участника, обязательные изменения относительно текущего:
- Сеттинг: команда — три блока (retail/cib/backend), участник отвечает за один.
- Шаг 0: `cowork-onboard.py` теперь даёт `WORKSHOP_TEAM` и `WORKSHOP_BLOCK`.
- Шаг 3: сопоставление с парой (команда, блок) по `TEAM.md`.
- Шаг 4: `cp .claude/templates/settings-<team>-<block>.json .claude/settings.local.json`.
- Шаг 5: прочитать бриф `tasks/task_01_credit.md` — там общая цель и часть
  своего блока.
- Границы: правит только свой блок (`team_<X>/<block>/`); читает все три блока
  своей команды (нужно знать API соседних блоков для стыковки); другую команду
  не видит вовсе.
- Раздел про интеграцию: фича готова, только когда все три блока команды сделали
  свою часть и состыкованы; внутри команды три участника договариваются офлайн.
- Сохранить дословно: правила «без жаргона», «в чате без markdown», «общая
  копилка» с запрещёнными словами, «петля обратной связи» (деплой → симулятор →
  табло), стоп-проверку организатора.

- [ ] **Step 2: Переписать `TEAM.md`**

Таблица «участник → команда → блок → папка»: 6 участников, у каждого пара
(команда, блок). Дефолтная раскладка: команда A — Монин/retail, Патрахин/cib,
Курочкин/backend; команда B — Ложечкин/retail, Хебенштрайт/cib, Васс/backend
(пометка: организатор поправит под реальное распределение). Алиасы имён,
блок организаторов — без изменений. Раздел «сервисы и порты»: 6 банк-блоков
(retail/cib/backend × 2) с локальными портами и Render-URL, плюс табло.

- [ ] **Step 3: Переписать `RULES.md`**

Команда — три блока, по участнику на блок. Участник правит только свой блок,
читает все три блока своей команды, другую команду не видит. Фича требует
стыковки трёх блоков. Связи блоков: retail→backend, retail→cib, cib→backend.
Симулятор оценивает команду интегрально по трём блокам. Общая ветка,
`pull --rebase` перед push. С участником — без жаргона и markdown.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md TEAM.md RULES.md
git commit -m "feat: CLAUDE.md, TEAM.md, RULES.md под трёхблочную структуру"
```

### Task 6.5: Обновить `ORGANIZER.md` и `DEPLOY.md`

**Files:**
- Modify: `ORGANIZER.md`, `DEPLOY.md`

- [ ] **Step 1: Обновить `ORGANIZER.md`**

Формат: команда — три блока-сервиса (retail/cib/backend), участник на блок,
фича требует стыковки трёх. 7 сервисов на Render. Probe трёх блоков,
интегральная оценка. Связи блоков. Ручные шаги: удалить старые сервисы,
применить Blueprint (7 сервисов + Postgres), задать `OPENAI_API_KEY` и
`ADMIN_TOKEN`, завести 7 секретов деплой-хуков; распределить участников по
парам (команда, блок); прогнать bootstrap. Риск Render free-плана по числу
сервисов и запасной вариант (объединить cib+backend). Ссылки на спеку и план.

- [ ] **Step 2: Обновить `DEPLOY.md`**

7 сервисов: `raif-a-{backend,cib,retail}`, `raif-b-{backend,cib,retail}`,
`raif-simulator`. Таблица «изменения в папке → деплой сервиса». 7 секретов
деплой-хуков. Env-переменные: `TEAM_NAME`, `BACKEND_URL`, `CIB_URL` по блокам;
симулятору — шесть `*_URL` и `DATABASE_URL`. Pull-модель переоценки.
Риск free-плана.

- [ ] **Step 3: Commit**

```bash
git add ORGANIZER.md DEPLOY.md
git commit -m "feat: ORGANIZER.md и DEPLOY.md под 7 сервисов и три блока"
```

---

## Фаза 7 — Брифы задач

### Task 7.1: Переписать `tasks/task_01_credit.md`

**Files:**
- Modify: `tasks/task_01_credit.md`

- [ ] **Step 1: Переписать файл**

Нетехнический бриф, общая цель плюс три части по блокам:
- **Общее:** в банке команды есть переводы; добавить кредиты — клиент подаёт
  заявку и сразу получает решение. Фича готова, только когда все три блока
  сделали свою часть и состыковались.
- **backend:** завести хранилище кредитных заявок — банк должен уметь принять
  заявку и показать список поданных.
- **cib:** построить решение по заявке — взять данные клиента у backend, решить
  «дать / отказать» по самому клиенту (надёжному — да, рискованному — нет),
  при отказе — понятное человеческое объяснение; завести кредитный продукт.
- **retail:** вкладка «Кредиты» с формой; собрать поток — отправить заявку в
  cib за решением, сохранить в backend, показать клиенту ответ.
- **Как проверить:** открыть банк команды, подать заявку надёжного и
  рискованного клиента, посмотреть на табло реакцию клиентов.
Без шагов реализации и без жаргона.

- [ ] **Step 2: Commit**

```bash
git add tasks/task_01_credit.md
git commit -m "feat(tasks): бриф задачи 1 — кредиты через три блока"
```

### Task 7.2: Переписать `tasks/task_02_invest.md`

**Files:**
- Modify: `tasks/task_02_invest.md`

- [ ] **Step 1: Переписать файл**

Аналогично: вкладка «Инвестиции» через три блока — backend хранит портфели/
сделки, cib даёт каталог инвест-продуктов и логику подбора, retail рисует
вкладку и поток. Пометка: probe и рубрика задачи 2 в симуляторе — фаст-фоллоу.

- [ ] **Step 2: Commit**

```bash
git add tasks/task_02_invest.md
git commit -m "feat(tasks): бриф задачи 2 — инвестиции через три блока"
```

---

## Фаза 8 — Тест-прогон и версионирование

### Task 8.1: Прогон через uvicorn — три блока + симулятор

**Files:** —

- [ ] **Step 1: Поднять шесть банк-блоков и симулятор**

```bash
PYTHONPATH=team_a/backend TEAM_NAME=team_a python3 -m uvicorn src.main:app --port 8003 --host 127.0.0.1 &
PYTHONPATH=team_a/cib TEAM_NAME=team_a BACKEND_URL=http://127.0.0.1:8003 python3 -m uvicorn src.main:app --port 8002 --host 127.0.0.1 &
PYTHONPATH=team_a/retail TEAM_NAME=team_a BACKEND_URL=http://127.0.0.1:8003 CIB_URL=http://127.0.0.1:8002 python3 -m uvicorn src.main:app --port 8001 --host 127.0.0.1 &
PYTHONPATH=team_b/backend TEAM_NAME=team_b python3 -m uvicorn src.main:app --port 8013 --host 127.0.0.1 &
PYTHONPATH=team_b/cib TEAM_NAME=team_b BACKEND_URL=http://127.0.0.1:8013 python3 -m uvicorn src.main:app --port 8012 --host 127.0.0.1 &
PYTHONPATH=team_b/retail TEAM_NAME=team_b BACKEND_URL=http://127.0.0.1:8013 CIB_URL=http://127.0.0.1:8012 python3 -m uvicorn src.main:app --port 8011 --host 127.0.0.1 &
sleep 6
A_BACKEND_URL=http://127.0.0.1:8003 A_CIB_URL=http://127.0.0.1:8002 A_RETAIL_URL=http://127.0.0.1:8001 \
B_BACKEND_URL=http://127.0.0.1:8013 B_CIB_URL=http://127.0.0.1:8012 B_RETAIL_URL=http://127.0.0.1:8011 \
ADMIN_TOKEN=test PYTHONPATH=simulator python3 -m uvicorn src.main:app --port 8000 --host 127.0.0.1 &
```

- [ ] **Step 2: Проверить базовую линию**

Run: `sleep 8; curl -s 127.0.0.1:8000/state`
Expected: обе команды `client_base:500`, `baseline_score` ≈ 4 (backend отдаёт
клиентов + переводы работают, кредитной фичи нет)

- [ ] **Step 3: Прогнать раунд оценки**

Run: `curl -s -X POST 127.0.0.1:8000/admin/evaluate -H "X-Admin-Token: test"`
Expected: обе команды `delta:0` (банки не менялись), `judge:"fallback"`,
осмысленное `reason`

- [ ] **Step 4: Остановить процессы**

Run: `pkill -f "uvicorn src.main:app"`

- [ ] **Step 5: Финальный прогон тестов**

Run: `python3 -m pytest simulator/tests/ -q`
Expected: все тесты зелёные

### Task 8.2: Тег версии после слияния

**Files:** —

> Выполняется после слияния `worktree-three-block` в `main` (через
> superpowers:finishing-a-development-branch).

- [ ] **Step 1: Поставить тег на слитый коммит**

```bash
git tag -a workshop-baseline-v2 -m "Базовая версия: команды из трёх блоков (retail/cib/backend)"
git push origin workshop-baseline-v2
```

Тег `workshop-baseline` (одно-банковая версия) остаётся как точка отката на v1.

---

## Деферд (фаст-фоллоу)

- Probe и рубрика задачи 2 («Инвестиции») в симуляторе.
- Спарклайн тренда на табло.

## Ручные шаги организатора

- Render: удалить старые 3 сервиса, проверить лимит free-плана по числу
  одновременных web-сервисов (7 шт.), применить новый Blueprint, задать
  `OPENAI_API_KEY` и `ADMIN_TOKEN`, завести 7 секретов деплой-хуков.
- Распределить 6 участников по парам (команда, блок) — `TEAM.md` и bootstrap.
- Прогнать обновлённый bootstrap на ноутбуках.
