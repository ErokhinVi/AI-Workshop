"""Блок backend — ядро данных банка команды.

Хранит клиентов, транзакции, балансы; отдаёт базовый API. UI нет.
Данные in-memory из seed/*.jsonl. Кредитное хранилище
(POST/GET /credit-applications) добавляет владелец блока в рамках задачи.
"""
from __future__ import annotations

import json
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
CIB_URL = os.environ.get("CIB_URL", "http://localhost:8012").rstrip("/")

_credit_applications: list[dict[str, Any]] = []
_applications_by_id: dict[str, dict[str, Any]] = {}


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
_credit_history: list[dict[str, Any]] = []


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
    _credit_history.extend(_load_jsonl(SEED_DIR / "credit_history.jsonl"))


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


@app.get("/clients/{client_id}/products")
async def get_client_products(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    credit_types = {"consumer_credit", "auto_credit", "mortgage", "credit_card"}
    products = [p for p in c.get("products", []) if p in credit_types]
    return {"client_id": client_id, "products": products}


@app.get("/transactions/{client_id}")
async def get_transactions(
    client_id: str, limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    txs = [t for t in _transactions if t["client_id"] == client_id]
    txs.sort(key=lambda t: t["ts"], reverse=True)
    return {"total": len(txs), "items": txs[:limit]}


@app.get("/clients/{client_id}/summary")
async def get_client_summary(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    history = [h for h in _credit_history if h["client_id"] == client_id]
    active_credits = [h for h in history if h.get("status") == "active"]
    total_debt = sum(h.get("principal_rub", 0) for h in active_credits)
    return {
        "client_id": client_id,
        "name": c.get("name"),
        "segment": c.get("segment"),
        "balance_rub": c.get("balance_rub"),
        "income_rub": c.get("income_rub"),
        "active_credits_count": len(active_credits),
        "total_debt_rub": total_debt,
        "risk_score": c.get("risk_score"),
        "has_overdue_history": c.get("has_overdue_history"),
    }


@app.get("/clients/{client_id}/credit-history")
async def get_credit_history(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    history = [h for h in _credit_history if h["client_id"] == client_id]
    return {"client_id": client_id, "total": len(history), "items": history}


@app.post("/credit-applications")
async def create_credit_application(payload: dict) -> dict:
    client_id = payload.get("client_id")
    amount_rub = payload.get("amount_rub")
    product = payload.get("product") or payload.get("product_id")
    if not client_id or not amount_rub or not product:
        raise HTTPException(status_code=400, detail="укажи client_id, amount_rub и product")
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")

    app_id = f"app-{len(_credit_applications) + 1:06d}"
    now_iso = datetime.now().replace(microsecond=0).isoformat()
    application = {
        "id": app_id,
        "client_id": client_id,
        "product": product,
        "amount_rub": amount_rub,
        "status": "pending",
        "scoring_result": None,
        "created_at": now_iso,
    }
    _credit_applications.append(application)
    _applications_by_id[app_id] = application

    client_history = [h for h in _credit_history if h["client_id"] == client_id]
    term_months = int(payload.get("term_months") or 12)
    scoring_payload = {
        "client_id": client_id,
        "amount_rub": amount_rub,
        "product_id": product,
        "term_months": term_months,
        "segment": c.get("segment"),
        "monthly_income_rub": c.get("income_rub"),
        "age": c.get("age"),
        "existing_products": c.get("products", []),
        "risk_score": c.get("risk_score"),
        "credit_history": [
            {
                "product": h.get("product"),
                "principal_rub": h.get("principal_rub"),
                "term_months": h.get("term_months"),
                "rate_pct": h.get("rate_pct"),
                "opened_at": h.get("opened_at"),
                "status": h.get("status"),
                "overdue_days_max": h.get("overdue_days_max"),
            }
            for h in client_history
        ],
    }

    asyncio.create_task(_send_to_scoring(app_id, scoring_payload))

    return {
        "status": "accepted",
        "application_id": app_id,
        "message": "Заявка принята и отправлена на рассмотрение",
        "created_at": now_iso,
    }


async def _send_to_scoring(app_id: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{CIB_URL}/credit/decide", json=payload)
            if resp.status_code == 200:
                result = resp.json()
                if app_id in _applications_by_id:
                    _applications_by_id[app_id]["scoring_result"] = result
                    _applications_by_id[app_id]["status"] = result.get("decision", "reviewed")
    except Exception:
        pass


@app.get("/credit-applications")
async def list_credit_applications(client_id: str | None = None) -> dict:
    apps = _credit_applications
    if client_id:
        apps = [a for a in apps if a["client_id"] == client_id]
    return {"total": len(apps), "items": apps}


@app.get("/credit-applications/{application_id}")
async def get_credit_application(application_id: str) -> dict:
    app_ = _applications_by_id.get(application_id)
    if not app_:
        raise HTTPException(status_code=404, detail=f"заявка {application_id} не найдена")
    return app_


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
