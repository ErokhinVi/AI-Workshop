"""
Блок: Финансы и Операции
Owner: Герт Хебенштрайт

Стартовая точка для воркшопа правления. На дне старта здесь:
 - база клиентов с балансами,
 - простые лимиты по сегментам (для определения максимальной суммы кредита),
 - ручки `/balance/{client_id}` и `/limits`.

Что блок будет делать дальше (отчёты по портфелям, расчёт комиссий,
финансовая модель сделки) — решает Герт вместе со своим AI-помощником.
"""
# redeploy-trigger: 2026-05-07T08:43:17Z

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException


BLOCK_NAME = "finance"

_clients_by_id: dict[str, dict[str, Any]] = {}

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
    yield


app = FastAPI(title="Финансы и Операции", version="0.1.0", lifespan=lifespan)

# Кредитные лимиты по сегментам — стартовая консервативная политика.
# Можно менять в ходе воркшопа; финансовый блок отвечает за это.
LIMITS = {
    "mass":          {"max_unsecured_credit_rub": 500_000,   "max_lt_rate_pct": 24.0},
    "mass_affluent": {"max_unsecured_credit_rub": 2_000_000, "max_lt_rate_pct": 19.0},
    "premium":       {"max_unsecured_credit_rub": 8_000_000, "max_lt_rate_pct": 14.0},
    "private":       {"max_unsecured_credit_rub": 50_000_000,"max_lt_rate_pct": 11.0},
    "sme":           {"max_unsecured_credit_rub": 15_000_000,"max_lt_rate_pct": 17.0},
}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "clients_loaded": len(_clients_by_id),
    }


@app.post("/_seed/load")
async def seed_load(payload: dict) -> dict:
    if "clients" in payload:
        global _clients_by_id
        _clients_by_id = {c["id"]: c for c in payload["clients"]}
    return {"status": "ok", "clients": len(_clients_by_id)}


@app.get("/balance/{client_id}")
async def get_balance(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    return {
        "client_id": client_id,
        "balance_rub": c.get("balance_rub"),
        "income_rub_monthly": c.get("income_rub"),
        "segment": c.get("segment"),
    }


@app.get("/limits")
async def get_limits(segment: str | None = None) -> dict:
    if segment:
        if segment not in LIMITS:
            raise HTTPException(status_code=404, detail=f"unknown segment: {segment}")
        return {"segment": segment, "limits": LIMITS[segment]}
    return {"limits_by_segment": LIMITS}
