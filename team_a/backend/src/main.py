"""Блок backend — ядро данных банка команды.

Хранит клиентов, транзакции, балансы; отдаёт базовый API. UI нет.
Данные in-memory из seed/*.jsonl. Кредитное хранилище
(POST/GET /credit-applications) добавляет владелец блока в рамках задачи.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
APPLICATIONS_PATH = Path(
    os.environ.get(
        "CREDIT_APPLICATIONS_PATH",
        Path(__file__).resolve().parents[1] / "data" / "credit_applications.jsonl",
    )
)


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
_credit_applications: list[dict[str, Any]] = []
_credit_applications_lock = threading.Lock()
_APPLICATION_ID_RE = re.compile(r"^ca-(\d{6,})$")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def _load_seed() -> None:
    if not SEED_DIR:
        return
    clients = _load_jsonl(SEED_DIR / "clients.jsonl")
    _clients.extend(clients)
    _clients_by_id.update({c["id"]: c for c in clients})
    _transactions.extend(_load_jsonl(SEED_DIR / "transactions.jsonl"))
    _credit_history.extend(_load_jsonl(SEED_DIR / "credit_history.jsonl"))


def _load_credit_applications() -> None:
    _credit_applications.extend(_load_jsonl(APPLICATIONS_PATH))


def _save_credit_application(application: dict[str, Any]) -> None:
    APPLICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPLICATIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(application, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _next_credit_application_id() -> str:
    max_number = 0
    for application in _credit_applications:
        raw_id = str(application.get("application_id") or "")
        match = _APPLICATION_ID_RE.match(raw_id)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"ca-{max_number + 1:06d}"


_load_seed()
_load_credit_applications()

app = FastAPI(title="backend — ядро данных", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "backend",
            "commit": COMMIT, "clients_loaded": len(_clients),
            "transactions_loaded": len(_transactions),
            "credit_history_loaded": len(_credit_history),
            "credit_applications_loaded": len(_credit_applications)}


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
    return {**c, "credit_profile": _build_credit_profile(client_id)}


@app.get("/transactions/{client_id}")
async def get_transactions(
    client_id: str, limit: int = Query(default=20, ge=1, le=200),
) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    txs = [t for t in _transactions if t["client_id"] == client_id]
    txs.sort(key=lambda t: t["ts"], reverse=True)
    return {"total": len(txs), "items": txs[:limit]}


def _credit_records_for_client(client_id: str) -> list[dict[str, Any]]:
    records = [r for r in _credit_history if r["client_id"] == client_id]
    records.sort(key=lambda r: r.get("opened_at", ""), reverse=True)
    return records


def _applications_for_client(client_id: str) -> list[dict[str, Any]]:
    applications = [a for a in _credit_applications if a["client_id"] == client_id]
    applications.sort(key=lambda a: a["created_at"], reverse=True)
    return applications


def _build_credit_profile(client_id: str) -> dict[str, Any]:
    records = _credit_records_for_client(client_id)
    applications = _applications_for_client(client_id)
    active_debt_rub = sum(
        int(r.get("principal_rub") or 0)
        for r in records
        if r.get("status") == "active"
    )
    max_overdue_days = max(
        [int(r.get("overdue_days_max") or 0) for r in records],
        default=0,
    )
    return {
        "history_total": len(records),
        "active_credits": sum(1 for r in records if r.get("status") == "active"),
        "active_debt_rub": active_debt_rub,
        "max_overdue_days": max_overdue_days,
        "has_overdue": max_overdue_days > 0,
        "recent_records": records[:10],
        "applications_total": len(applications),
        "recent_applications": applications[:10],
    }


@app.get("/credit-history/{client_id}")
async def get_credit_history(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    records = _credit_records_for_client(client_id)
    return {
        "client_id": client_id,
        "total": len(records),
        "summary": _build_credit_profile(client_id),
        "items": records,
    }


def _validate_credit_application(payload: dict) -> dict:
    client_id = str(payload.get("client_id") or "").strip()
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")

    try:
        amount_rub = int(payload.get("amount_rub") or 0)
        term_months = int(payload.get("term_months") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректная сумма или срок")

    status = (payload.get("status") or payload.get("decision") or "").strip()
    explanation = (payload.get("explanation") or "").strip()
    if payload.get("decision") and not explanation:
        explanation = "Заявка сохранена в истории клиента."

    if amount_rub <= 0:
        raise HTTPException(status_code=400, detail="укажи положительную сумму")
    if term_months < 3 or term_months > 84:
        raise HTTPException(
            status_code=400,
            detail="срок должен быть от 3 до 84 месяцев",
        )
    if status not in ("approved", "counter_offer", "declined"):
        raise HTTPException(status_code=400, detail="некорректный статус заявки")
    if not explanation:
        raise HTTPException(status_code=400, detail="нужно объяснение для клиента")

    try:
        approved_amount_rub = int(payload.get("approved_amount_rub") or 0)
        monthly_payment_rub = int(payload.get("monthly_payment_rub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректная одобренная сумма или платеж")

    rate_pct_raw = payload.get("rate_pct")
    try:
        rate_pct = float(rate_pct_raw) if rate_pct_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректная ставка")

    if approved_amount_rub < 0 or monthly_payment_rub < 0:
        raise HTTPException(status_code=400, detail="суммы не могут быть отрицательными")
    if rate_pct is not None and rate_pct < 0:
        raise HTTPException(status_code=400, detail="ставка не может быть отрицательной")

    application = {
        "client_id": client_id,
        "amount_rub": amount_rub,
        "term_months": term_months,
        "status": status,
        "approved_amount_rub": approved_amount_rub,
        "rate_pct": rate_pct,
        "monthly_payment_rub": monthly_payment_rub,
        "explanation": explanation,
    }
    for optional_field in (
        "decision",
        "reason",
        "title",
        "next_step",
        "product_type",
        "product_name",
        "source",
        "client_snapshot",
    ):
        if optional_field in payload:
            application[optional_field] = payload[optional_field]
    return application


@app.post("/credit-applications")
async def create_credit_application(payload: dict) -> dict:
    application = _validate_credit_application(payload)
    with _credit_applications_lock:
        application["application_id"] = _next_credit_application_id()
        application["created_at"] = datetime.now().replace(microsecond=0).isoformat()
        _credit_applications.append(application)
        _save_credit_application(application)
    return application


@app.get("/credit-applications")
async def list_credit_applications(
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = sorted(_credit_applications, key=lambda a: a["created_at"], reverse=True)
    return {"total": len(items), "items": items[:limit]}


@app.get("/credit-applications/{client_id}")
async def get_credit_applications(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    items = [a for a in _credit_applications if a["client_id"] == client_id]
    items.sort(key=lambda a: a["created_at"], reverse=True)
    return {"client_id": client_id, "total": len(items), "items": items}


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
