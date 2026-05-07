"""
Блок: Управление рисками
Owner: Роланд Васс

Стартовая точка для воркшопа правления. На дне старта здесь:
 - база клиентов с риск-скорами и кредитной историей (загружается из seed),
 - простая ручка `/score/{client_id}` возвращающая риск-скор,
 - ручка `/credit-history/{client_id}`.

Что блок будет делать дальше (определение risk-аппетита, сложные модели,
LLM-анализ профиля) — решает Роланд вместе со своим AI-помощником.
"""
# redeploy-trigger: 2026-05-07T08:43:17Z

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException


BLOCK_NAME = "risk"

_clients_by_id: dict[str, dict[str, Any]] = {}
_credit_history: list[dict[str, Any]] = []
_credit_history_by_client: dict[str, list[dict[str, Any]]] = {}

def _find_seed_dir() -> Path | None:
    """Ищем cases/_seed/ — работает и в Docker (/app/cases/_seed), и локально."""
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent / "cases" / "_seed",         # Docker: /app/cases/_seed
        here.parents[3] / "cases" / "_seed" if len(here.parents) >= 4 else None,
        here.parents[2] / "cases" / "_seed" if len(here.parents) >= 3 else None,
    ):
        if candidate and candidate.exists():
            return candidate
    return None


SEED_DIR = _find_seed_dir()


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if SEED_DIR is None:
        yield
        return
    if not _clients_by_id:
        for c in _load_jsonl(SEED_DIR / "clients.jsonl"):
            _clients_by_id[c["id"]] = c
    if not _credit_history:
        rows = _load_jsonl(SEED_DIR / "credit_history.jsonl")
        if rows:
            _credit_history.extend(rows)
            for r in rows:
                _credit_history_by_client.setdefault(r["client_id"], []).append(r)
    yield


app = FastAPI(title="Управление рисками", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "clients_loaded": len(_clients_by_id),
        "credit_records_loaded": len(_credit_history),
    }


@app.post("/_seed/load")
async def seed_load(payload: dict) -> dict:
    if "clients" in payload:
        global _clients_by_id
        _clients_by_id = {c["id"]: c for c in payload["clients"]}
    if "credit_history" in payload:
        global _credit_history, _credit_history_by_client
        _credit_history = list(payload["credit_history"])
        idx: dict[str, list[dict[str, Any]]] = {}
        for r in _credit_history:
            idx.setdefault(r["client_id"], []).append(r)
        _credit_history_by_client = idx
    return {
        "status": "ok",
        "clients": len(_clients_by_id),
        "credit_records": len(_credit_history),
    }


@app.get("/score/{client_id}")
async def get_score(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    history = _credit_history_by_client.get(client_id, [])
    has_overdue = any(r.get("overdue_days_max", 0) >= 30 for r in history)
    return {
        "client_id": client_id,
        "risk_score": c.get("risk_score"),
        "segment": c.get("segment"),
        "has_overdue_history": c.get("has_overdue_history", False),
        "had_severe_overdue": has_overdue,
        "credit_records_count": len(history),
        "method": "seed_lookup",
    }


@app.get("/credit-history/{client_id}")
async def get_credit_history(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    return {
        "client_id": client_id,
        "items": _credit_history_by_client.get(client_id, []),
    }
