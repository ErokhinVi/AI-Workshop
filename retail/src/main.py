"""
Блок: Розница
Owner: Иван Курочкин

Стартовая точка для воркшопа правления. На дне старта здесь:
 - база из 500 синтетических клиентов и 5000 транзакций (загружается из seed),
 - простой UI мобильного банка с вкладкой «Переводы»,
 - публичные ручки для соседних блоков.

Persistence:
 - Если задан env DATABASE_URL — все mutations (переводы, кредитные
   заявки) пишутся в Postgres, переживают перезапуски контейнера.
   Seed заливается в БД при первом старте, дальше БД источник правды.
 - Если DATABASE_URL не задан — fallback на in-memory; seed читается
   из cases/_seed/*.jsonl при каждом старте.

Что блок будет делать дальше (выдача кредитов, инвест-кабинет премиум,
лидогенерация и т.п.) — решает Иван вместе со своим AI-помощником
по ходу воркшопа.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import db as dbmod


BLOCK_NAME = "retail"


def _find_seed_dir() -> Path | None:
    """Ищем cases/_seed/ — работает и в Docker (/app/cases/_seed), и локально."""
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent / "cases" / "_seed",
        here.parents[3] / "cases" / "_seed" if len(here.parents) >= 4 else None,
        here.parents[2] / "cases" / "_seed" if len(here.parents) >= 3 else None,
    ):
        if candidate and candidate.exists():
            return candidate
    return None


SEED_DIR = _find_seed_dir()

# --- in-memory fallback (используется если нет DATABASE_URL) -------------

_clients: list[dict[str, Any]] = []
_clients_by_id: dict[str, dict[str, Any]] = {}
_transactions: list[dict[str, Any]] = []
_credit_applications: list[dict[str, Any]] = []


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


def _load_in_memory() -> None:
    if not SEED_DIR:
        return
    if not _clients:
        clients = _load_jsonl(SEED_DIR / "clients.jsonl")
        if clients:
            _clients.extend(clients)
            _clients_by_id.update({c["id"]: c for c in clients})
    if not _transactions:
        txs = _load_jsonl(SEED_DIR / "transactions.jsonl")
        if txs:
            _transactions.extend(txs)


# --- lifespan: подключаем БД, иначе in-memory ----------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    try:
        pool = await dbmod.init_pool()
        if pool is not None:
            await dbmod.ensure_schema(pool)
            if SEED_DIR:
                summary = await dbmod.seed_if_empty(pool, SEED_DIR)
                if summary["clients"] or summary["transactions"]:
                    print(f"[retail] seeded into DB: {summary}")
            print(f"[retail] using Postgres")
        else:
            _load_in_memory()
            print(f"[retail] DATABASE_URL not set — using in-memory")
    except Exception as e:
        print(f"[retail] DB init failed: {e!r} — fallback to in-memory")
        pool = None
        _load_in_memory()
    app.state.pool = pool
    try:
        yield
    finally:
        if pool is not None:
            await pool.close()


app = FastAPI(title="Розница", version="0.2.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _pool():
    return getattr(app.state, "pool", None)


# --- системные ручки ------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    pool = _pool()
    if pool is not None:
        async with pool.acquire() as conn:
            n_clients = await conn.fetchval("SELECT COUNT(*) FROM retail_clients")
            n_tx      = await conn.fetchval("SELECT COUNT(*) FROM retail_transactions")
        return {
            "status": "ok", "block": BLOCK_NAME,
            "persistence": "postgres",
            "clients_loaded": int(n_clients),
            "transactions_loaded": int(n_tx),
        }
    return {
        "status": "ok", "block": BLOCK_NAME,
        "persistence": "memory",
        "clients_loaded": len(_clients),
        "transactions_loaded": len(_transactions),
    }


@app.post("/_seed/load")
async def seed_load(payload: dict) -> dict:
    """Принимает данные из cases/_seed/load_into_blocks.py.
    Идемпотентен — переписывает то что уже было.

    Если БД подключена — обновляет таблицы, иначе in-memory.
    """
    pool = _pool()
    if pool is not None:
        async with pool.acquire() as conn:
            if "clients" in payload:
                await conn.execute("TRUNCATE retail_clients CASCADE")
                rows = []
                for c in payload["clients"]:
                    rows.append((
                        c["id"], c.get("name"), c.get("segment"),
                        int(c.get("balance_rub", 0)),
                        int(c.get("income_rub", 0)) if c.get("income_rub") else None,
                        bool(c.get("has_overdue_history", False)),
                        json.dumps(c, ensure_ascii=False),
                    ))
                await conn.executemany(
                    "INSERT INTO retail_clients(id, name, segment, balance_rub, "
                    "income_rub, has_overdue_history, data) "
                    "VALUES($1, $2, $3, $4, $5, $6, $7::jsonb)",
                    rows,
                )
            if "transactions" in payload:
                await conn.execute("TRUNCATE retail_transactions")
                rows = []
                for t in payload["transactions"]:
                    rows.append((
                        t["id"], t["client_id"], t["type"],
                        int(t["amount_rub"]),
                        datetime.fromisoformat(t["ts"]),
                        t.get("counterparty"),
                    ))
                await conn.executemany(
                    "INSERT INTO retail_transactions(id, client_id, type, "
                    "amount_rub, ts, counterparty) "
                    "VALUES($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                    rows,
                )
            n_c = await conn.fetchval("SELECT COUNT(*) FROM retail_clients")
            n_t = await conn.fetchval("SELECT COUNT(*) FROM retail_transactions")
        return {"status": "ok", "clients": int(n_c), "transactions": int(n_t)}

    # in-memory fallback
    if "clients" in payload:
        global _clients, _clients_by_id
        _clients = list(payload["clients"])
        _clients_by_id = {c["id"]: c for c in _clients}
    if "transactions" in payload:
        global _transactions
        _transactions = list(payload["transactions"])
    return {"status": "ok", "clients": len(_clients), "transactions": len(_transactions)}


# --- UI ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    f = STATIC_DIR / "index.html"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return "<h1>Розница</h1>"


# --- публичные ручки -----------------------------------------------------

@app.get("/clients")
async def list_clients(
    segment: str | None = Query(default=None, description="mass / mass_affluent / premium / private / sme"),
    has_overdue: bool | None = None,
    min_income: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    pool = _pool()
    if pool is not None:
        total, items = await dbmod.list_clients(pool, segment, has_overdue, min_income, limit)
        return {"total": total, "items": items}

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
    pool = _pool()
    if pool is not None:
        c = await dbmod.get_client(pool, client_id)
        if not c:
            raise HTTPException(status_code=404, detail=f"client {client_id} not found")
        return c

    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    return c


@app.get("/transactions/{client_id}")
async def get_transactions(
    client_id: str,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    pool = _pool()
    if pool is not None:
        items = await dbmod.get_transactions(pool, client_id, limit)
        return {"total": len(items), "items": items}

    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    txs = [t for t in _transactions if t["client_id"] == client_id]
    txs.sort(key=lambda t: t["ts"], reverse=True)
    return {"total": len(txs), "items": txs[:limit]}


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    """Перевод денег между клиентами банка (или на внешний счёт).

    Через БД — атомарно, переживает перезапуски. В fallback'е (in-memory)
    также работает, но изменения теряются при деплое.
    """
    from_id = payload.get("from_client_id")
    to_query = (payload.get("to") or "").strip()
    amount = int(payload.get("amount_rub") or 0)

    pool = _pool()
    if pool is not None:
        try:
            return await dbmod.transfer(pool, from_id, to_query, amount)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ----- in-memory fallback -----
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
            detail=f"недостаточно средств: на счёте {sender['balance_rub']} ₽, запрошено {amount} ₽",
        )

    receiver: dict[str, Any] | None = None
    if to_query in _clients_by_id and to_query != from_id:
        receiver = _clients_by_id[to_query]
    else:
        tql = to_query.lower()
        for c in _clients:
            if c["id"] == from_id:
                continue
            if tql == c["name"].lower() or tql in c["name"].lower():
                receiver = c
                break

    now_iso = datetime.now().replace(microsecond=0).isoformat()
    sender["balance_rub"] -= amount
    out_tx = {
        "id": f"t-{100000 + len(_transactions) + 1:08d}",
        "client_id": from_id, "type": "transfer_out",
        "amount_rub": -amount, "ts": now_iso,
        "counterparty": receiver["name"] if receiver else to_query,
    }
    _transactions.append(out_tx)
    if receiver:
        receiver["balance_rub"] += amount
        _transactions.append({
            "id": f"t-{100000 + len(_transactions) + 1:08d}",
            "client_id": receiver["id"], "type": "transfer_in",
            "amount_rub": amount, "ts": now_iso,
            "counterparty": sender["name"],
        })
        kind = "internal"
        recipient_label = receiver["name"]
    else:
        kind = "external"
        recipient_label = to_query

    return {
        "status": "ok", "kind": kind, "amount_rub": amount,
        "to": recipient_label, "from_client_id": from_id,
        "new_balance_rub": sender["balance_rub"],
        "tx_id": out_tx["id"], "ts": now_iso,
    }


# --- кредитные заявки (для case_01) ---------------------------------------

@app.post("/api/credit-apply")
async def credit_apply(payload: dict) -> JSONResponse:
    """Заглушка для case_01.

    Кладёт заявку в БД (или in-memory) и возвращает 501 — полноценный
    pipeline (Risk + CIB + LLM) — задача воркшопа.
    """
    cid = payload.get("client_id")
    pool = _pool()
    if pool is not None:
        c = await dbmod.get_client(pool, cid) if cid else None
        if not c:
            return JSONResponse(status_code=404, content={"detail": f"клиент {cid} не найден"})
        record = await dbmod.add_credit_application(
            pool, cid,
            int(payload["amount_rub"]) if payload.get("amount_rub") else None,
            int(payload["term_months"]) if payload.get("term_months") else None,
        )
        total, _ = await dbmod.list_credit_applications(pool)
        return JSONResponse(
            status_code=501,
            content={
                "detail": "кредитный pipeline ещё не построен",
                "hint": "задача кейса case_01_credit — поднять полноценную проверку",
                "received_total": total,
                "application": record,
            },
        )

    # in-memory
    if cid not in _clients_by_id:
        return JSONResponse(status_code=404, content={"detail": f"клиент {cid} не найден"})
    _credit_applications.append({
        "client_id": cid,
        "amount_rub": payload.get("amount_rub"),
        "term_months": payload.get("term_months"),
        "status": "received",
    })
    return JSONResponse(status_code=501, content={
        "detail": "кредитный pipeline ещё не построен",
        "hint": "задача кейса case_01_credit",
        "received_total": len(_credit_applications),
    })


@app.get("/credit-applications")
async def list_credit_applications() -> dict:
    pool = _pool()
    if pool is not None:
        total, items = await dbmod.list_credit_applications(pool)
        return {"total": total, "items": items}
    return {"total": len(_credit_applications), "items": _credit_applications}
