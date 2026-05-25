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
INVESTMENT_ORDERS_PATH = Path(
    os.environ.get(
        "INVESTMENT_ORDERS_PATH",
        Path(__file__).resolve().parents[1] / "data" / "investment_orders.jsonl",
    )
)
CASINO_SESSIONS_PATH = Path(
    os.environ.get(
        "CASINO_SESSIONS_PATH",
        Path(__file__).resolve().parents[1] / "data" / "casino_sessions.jsonl",
    )
)
CASINO_SPINS_PATH = Path(
    os.environ.get(
        "CASINO_SPINS_PATH",
        Path(__file__).resolve().parents[1] / "data" / "casino_spins.jsonl",
    )
)
INVESTMENT_INSTRUMENTS = {
    "SBRF": {"name": "Сбербанк", "currency": "RUB", "lot_size": 1},
    "VTBR": {"name": "ВТБ", "currency": "RUB", "lot_size": 1000},
    "ROSN": {"name": "Роснефть", "currency": "RUB", "lot_size": 1},
    "SIBN": {"name": "Газпром нефть", "currency": "RUB", "lot_size": 1},
}
ALLOWED_INVESTMENT_TICKERS = set(INVESTMENT_INSTRUMENTS)
INVESTMENT_COMMISSION_RATE = 0.003


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
_investment_orders: list[dict[str, Any]] = []
_investment_orders_lock = threading.Lock()
_casino_sessions: list[dict[str, Any]] = []
_casino_sessions_lock = threading.Lock()
_casino_spins: list[dict[str, Any]] = []
_casino_spins_lock = threading.Lock()
_APPLICATION_ID_RE = re.compile(r"^ca-(\d{6,})$")
_INVESTMENT_ORDER_ID_RE = re.compile(r"^io-(\d{6,})$")
_CASINO_SESSION_ID_RE = re.compile(r"^cs-(\d{6,})$")
_CASINO_SPIN_ID_RE = re.compile(r"^sp-(\d{6,})$")


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


def _load_investment_orders() -> None:
    _investment_orders.extend(_load_jsonl(INVESTMENT_ORDERS_PATH))


def _load_casino_sessions() -> None:
    _casino_sessions.extend(_load_jsonl(CASINO_SESSIONS_PATH))


def _load_casino_spins() -> None:
    _casino_spins.extend(_load_jsonl(CASINO_SPINS_PATH))


def _save_credit_application(application: dict[str, Any]) -> None:
    APPLICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPLICATIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(application, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _save_investment_order(order: dict[str, Any]) -> None:
    INVESTMENT_ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INVESTMENT_ORDERS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(order, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _save_casino_session(session: dict[str, Any]) -> None:
    CASINO_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CASINO_SESSIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(session, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _rewrite_casino_sessions() -> None:
    CASINO_SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CASINO_SESSIONS_PATH.open("w", encoding="utf-8") as f:
        for session in _casino_sessions:
            f.write(json.dumps(session, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _save_casino_spin(spin: dict[str, Any]) -> None:
    CASINO_SPINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CASINO_SPINS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(spin, ensure_ascii=False) + "\n")
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


def _next_investment_order_id() -> str:
    max_number = 0
    for order in _investment_orders:
        raw_id = str(order.get("order_id") or "")
        match = _INVESTMENT_ORDER_ID_RE.match(raw_id)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"io-{max_number + 1:06d}"


def _next_casino_session_id() -> str:
    max_number = 0
    for session in _casino_sessions:
        raw_id = str(session.get("session_id") or "")
        match = _CASINO_SESSION_ID_RE.match(raw_id)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"cs-{max_number + 1:06d}"


def _next_casino_spin_id() -> str:
    max_number = 0
    for spin in _casino_spins:
        raw_id = str(spin.get("spin_id") or "")
        match = _CASINO_SPIN_ID_RE.match(raw_id)
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"sp-{max_number + 1:06d}"


_load_seed()
_load_credit_applications()
_load_investment_orders()
_load_casino_sessions()
_load_casino_spins()

app = FastAPI(title="backend — ядро данных", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "backend",
            "commit": COMMIT, "clients_loaded": len(_clients),
            "transactions_loaded": len(_transactions),
            "credit_history_loaded": len(_credit_history),
            "credit_applications_loaded": len(_credit_applications),
            "investment_orders_loaded": len(_investment_orders),
            "casino_sessions_loaded": len(_casino_sessions),
            "casino_spins_loaded": len(_casino_spins)}


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
    purpose = str(payload.get("purpose") or "").strip()
    if purpose and purpose not in {"casino_slot", "investment_securities"}:
        raise HTTPException(status_code=400, detail="некорректная цель кредита")

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
    if purpose:
        application["purpose"] = purpose
    for optional_field in (
        "max_stake_rub",
        "session_limit_rub",
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


def _validate_investment_order(payload: dict) -> dict[str, Any]:
    client_id = str(payload.get("client_id") or "").strip()
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")

    ticker = str(payload.get("ticker") or "").strip().upper()
    if ticker not in ALLOWED_INVESTMENT_TICKERS:
        raise HTTPException(
            status_code=400,
            detail="доступны только акции SBRF, VTBR, ROSN, SIBN",
        )

    side = str(payload.get("side") or "buy").strip().lower()
    if side != "buy":
        raise HTTPException(status_code=400, detail="сейчас поддерживается только покупка")

    trade_rules = _investment_trade_rules(ticker, payload.get("trade_rules"))
    if side not in trade_rules["allowed_sides"]:
        raise HTTPException(status_code=400, detail="эта операция недоступна для инструмента")

    status = str(payload.get("status") or "executed").strip().lower()
    if status not in {"quoted", "executed", "rejected", "cancelled"}:
        raise HTTPException(status_code=400, detail="некорректный статус инвестиционной заявки")

    try:
        quantity = int(payload.get("quantity") or 0)
        price_rub = float(payload.get("price_rub") or 0)
        amount_rub = float(payload.get("amount_rub") or 0)
        commission_rub = float(payload.get("commission_rub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректные параметры сделки")

    if quantity <= 0:
        raise HTTPException(status_code=400, detail="укажи положительное количество акций")
    min_quantity = int(trade_rules["min_quantity"])
    quantity_step = int(trade_rules["quantity_step"])
    if quantity < min_quantity:
        raise HTTPException(status_code=400, detail=f"минимальное количество: {min_quantity}")
    if quantity_step > 0 and (quantity - min_quantity) % quantity_step != 0:
        raise HTTPException(status_code=400, detail=f"количество должно идти шагом {quantity_step}")
    if price_rub <= 0:
        raise HTTPException(status_code=400, detail="укажи положительную цену")
    if amount_rub <= 0:
        amount_rub = round(quantity * price_rub, 2)
    if commission_rub < 0:
        raise HTTPException(status_code=400, detail="комиссия не может быть отрицательной")
    try:
        total_rub = round(float(payload.get("total_rub") or amount_rub + commission_rub), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректный итог сделки")
    if total_rub < round(amount_rub + commission_rub, 2):
        raise HTTPException(status_code=400, detail="итог сделки меньше суммы и комиссии")
    if status == "executed":
        cash_balance = _build_investment_portfolio(client_id)["cash_balance_rub"]
        if total_rub > cash_balance:
            raise HTTPException(status_code=400, detail="недостаточно свободных средств для покупки")

    explanation = str(payload.get("explanation") or "").strip()
    if not explanation:
        explanation = (
            f"Куплено {quantity} акций {ticker} по ориентировочной цене "
            f"{price_rub:g} ₽."
        )

    order = {
        "client_id": client_id,
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price_rub": round(price_rub, 4),
        "amount_rub": round(amount_rub, 2),
        "commission_rub": round(commission_rub, 2),
        "total_rub": total_rub,
        "status": status,
        "explanation": explanation,
        "instrument_name": INVESTMENT_INSTRUMENTS[ticker]["name"],
        "currency": trade_rules["settlement_currency"],
        "trade_rules": trade_rules,
    }
    for optional_field in (
        "risk_level",
        "source",
        "instrument",
        "decision",
        "next_step",
    ):
        if optional_field in payload:
            order[optional_field] = payload[optional_field]
    return order


def _investment_trade_rules(
    ticker: str,
    payload_rules: Any,
) -> dict[str, Any]:
    instrument = INVESTMENT_INSTRUMENTS[ticker]
    rules = payload_rules if isinstance(payload_rules, dict) else {}
    allowed_sides = (
        rules.get("allowed_sides")
        if isinstance(rules.get("allowed_sides"), list)
        else ["buy"]
    )
    allowed_sides = [str(side).lower() for side in allowed_sides]
    try:
        min_quantity = int(rules.get("min_quantity") or 1)
        quantity_step = int(rules.get("quantity_step") or 1)
        lot_size = int(rules.get("lot_size") or instrument["lot_size"])
        commission_rate = float(rules.get("commission_rate") or INVESTMENT_COMMISSION_RATE)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректные правила сделки")
    settlement_currency = str(rules.get("settlement_currency") or instrument["currency"]).upper()
    if min_quantity <= 0 or quantity_step <= 0 or lot_size <= 0:
        raise HTTPException(status_code=400, detail="некорректный шаг или минимум сделки")
    if commission_rate < 0:
        raise HTTPException(status_code=400, detail="комиссия не может быть отрицательной")
    if settlement_currency != instrument["currency"]:
        raise HTTPException(status_code=400, detail="некорректная валюта расчёта")
    return {
        "allowed_sides": allowed_sides,
        "quantity_unit": str(rules.get("quantity_unit") or "share"),
        "min_quantity": min_quantity,
        "quantity_step": quantity_step,
        "lot_size": lot_size,
        "supports_fractional": bool(rules.get("supports_fractional", False)),
        "commission_rate": commission_rate,
        "settlement_currency": settlement_currency,
    }


def _investment_orders_for_client(client_id: str) -> list[dict[str, Any]]:
    orders = [o for o in _investment_orders if o["client_id"] == client_id]
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return orders


def _build_investment_portfolio(client_id: str) -> dict[str, Any]:
    client = _clients_by_id[client_id]
    executed_orders = [
        o
        for o in _investment_orders
        if o["client_id"] == client_id and o.get("status") == "executed"
    ]
    positions_by_ticker: dict[str, dict[str, Any]] = {}
    invested_total = 0.0
    for order in executed_orders:
        ticker = order["ticker"]
        quantity = int(order.get("quantity") or 0)
        amount = float(order.get("amount_rub") or 0)
        commission = float(order.get("commission_rub") or 0)
        total = float(order.get("total_rub") or amount + commission)
        if quantity <= 0:
            continue
        position = positions_by_ticker.setdefault(
            ticker,
            {"ticker": ticker, "quantity": 0, "cost_rub": 0.0},
        )
        position["quantity"] += quantity
        position["cost_rub"] += amount
        invested_total += total

    positions = []
    for position in positions_by_ticker.values():
        quantity = int(position["quantity"])
        cost = float(position["cost_rub"])
        avg_price = round(cost / quantity, 4) if quantity else 0
        positions.append(
            {
                "ticker": position["ticker"],
                "quantity": quantity,
                "avg_price_rub": avg_price,
                "market_value_rub": round(cost, 2),
            }
        )
    positions.sort(key=lambda p: p["ticker"])
    cash_balance = max(0.0, float(client.get("balance_rub") or 0) - invested_total)
    return {
        "client_id": client_id,
        "cash_balance_rub": round(cash_balance, 2),
        "positions": positions,
        "orders_total": len(_investment_orders_for_client(client_id)),
    }


def _applications_by_id() -> dict[str, dict[str, Any]]:
    return {str(a.get("application_id")): a for a in _credit_applications}


def _casino_sessions_for_client(client_id: str) -> list[dict[str, Any]]:
    sessions = [s for s in _casino_sessions if s["client_id"] == client_id]
    sessions.sort(key=lambda s: s["created_at"], reverse=True)
    return sessions


def _casino_spins_for_client(client_id: str) -> list[dict[str, Any]]:
    spins = [s for s in _casino_spins if s["client_id"] == client_id]
    spins.sort(key=lambda s: s["created_at"], reverse=True)
    return spins


def _find_casino_application(client_id: str, application_id: str | None) -> dict[str, Any]:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")

    if application_id:
        application = _applications_by_id().get(application_id)
        if not application or application.get("client_id") != client_id:
            raise HTTPException(status_code=404, detail="кредитная заявка не найдена")
    else:
        candidates = [
            a
            for a in _applications_for_client(client_id)
            if a.get("purpose") == "casino_slot" and a.get("status") == "approved"
        ]
        application = candidates[0] if candidates else None
        if not application:
            raise HTTPException(
                status_code=400,
                detail="сначала нужна одобренная кредитная заявка под слот-машину",
            )

    if application.get("status") != "approved":
        raise HTTPException(
            status_code=400,
            detail="игровая сессия доступна только после одобрения",
        )
    if application.get("purpose") != "casino_slot":
        raise HTTPException(
            status_code=400,
            detail="кредитная заявка должна быть под слот-машину",
        )
    return application


def _validate_casino_session(payload: dict) -> dict[str, Any]:
    client_id = str(payload.get("client_id") or "").strip()
    application_id = str(payload.get("application_id") or "").strip() or None
    application = _find_casino_application(client_id, application_id)

    if any(
        s.get("application_id") == application["application_id"]
        and s.get("status") == "active"
        for s in _casino_sessions
    ):
        raise HTTPException(
            status_code=400,
            detail="по этой заявке уже есть активная игровая сессия",
        )

    try:
        approved_amount = int(application.get("approved_amount_rub") or 0)
        session_limit = int(
            payload.get("session_limit_rub")
            or application.get("session_limit_rub")
            or approved_amount
        )
        max_stake = int(
            payload.get("max_stake_rub") or application.get("max_stake_rub") or 0
        )
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректный лимит игровой сессии")

    if approved_amount <= 0:
        raise HTTPException(status_code=400, detail="у заявки нет одобренной суммы")
    if session_limit <= 0:
        raise HTTPException(
            status_code=400,
            detail="лимит игровой сессии должен быть положительным",
        )
    if session_limit > approved_amount:
        raise HTTPException(
            status_code=400,
            detail="лимит сессии не может быть больше одобренного кредита",
        )
    if max_stake < 0:
        raise HTTPException(
            status_code=400,
            detail="максимальная ставка не может быть отрицательной",
        )

    return {
        "client_id": client_id,
        "application_id": application["application_id"],
        "status": "active",
        "session_limit_rub": session_limit,
        "remaining_limit_rub": session_limit,
        "max_stake_rub": max_stake or None,
        "total_staked_rub": 0,
        "total_win_rub": 0,
        "net_result_rub": 0,
        "currency": "RUB",
        "explanation": (
            "Игровая сессия открыта по одобренной кредитной заявке под слот-машину. "
            "Backend будет списывать ставки из лимита сессии и сохранять результаты CIB."
        ),
    }


def _find_active_casino_session(client_id: str, session_id: str) -> dict[str, Any]:
    for session in _casino_sessions:
        if session.get("session_id") == session_id and session.get("client_id") == client_id:
            if session.get("status") != "active":
                raise HTTPException(
                    status_code=400,
                    detail="игровая сессия уже завершена",
                )
            return session
    raise HTTPException(status_code=404, detail="игровая сессия не найдена")


def _validate_casino_spin(payload: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    client_id = str(payload.get("client_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="нужен идентификатор игровой сессии",
        )

    session = _find_active_casino_session(client_id, session_id)
    try:
        stake_rub = int(payload.get("stake_rub") or 0)
        win_rub = int(payload.get("win_rub") or 0)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="некорректная ставка или выигрыш",
        )
    if stake_rub <= 0:
        raise HTTPException(status_code=400, detail="ставка должна быть положительной")
    if win_rub < 0:
        raise HTTPException(status_code=400, detail="выигрыш не может быть отрицательным")
    if stake_rub > int(session["remaining_limit_rub"]):
        raise HTTPException(
            status_code=400,
            detail="ставка больше остатка лимита игровой сессии",
        )
    max_stake = session.get("max_stake_rub")
    if max_stake is not None and stake_rub > int(max_stake):
        raise HTTPException(
            status_code=400,
            detail="ставка больше максимума для одной попытки",
        )

    symbols = payload.get("symbols")
    if (
        not isinstance(symbols, list)
        or len(symbols) != 3
        or not all(isinstance(s, str) for s in symbols)
    ):
        raise HTTPException(
            status_code=400,
            detail="нужны три символа результата слот-машины",
        )
    explanation = str(payload.get("explanation") or "").strip()
    if not explanation:
        raise HTTPException(
            status_code=400,
            detail="нужно объяснение результата для клиента",
        )

    try:
        net_result = int(payload.get("net_result_rub") or win_rub - stake_rub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="некорректный чистый результат")
    if net_result != win_rub - stake_rub:
        raise HTTPException(
            status_code=400,
            detail="чистый результат должен равняться выигрышу минус ставка",
        )

    spin = {
        "client_id": client_id,
        "session_id": session_id,
        "stake_rub": stake_rub,
        "symbols": symbols,
        "win_rub": win_rub,
        "net_result_rub": net_result,
        "currency": str(payload.get("currency") or session.get("currency") or "RUB").upper(),
        "explanation": explanation,
    }
    for optional_field in ("status", "payout_multiplier", "source", "cib_decision_id"):
        if optional_field in payload:
            spin[optional_field] = payload[optional_field]
    return session, spin


@app.get("/investment-portfolio/{client_id}")
async def get_investment_portfolio(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    return _build_investment_portfolio(client_id)


@app.post("/investment-orders")
async def create_investment_order(payload: dict) -> dict:
    order = _validate_investment_order(payload)
    with _investment_orders_lock:
        order["order_id"] = _next_investment_order_id()
        order["created_at"] = datetime.now().replace(microsecond=0).isoformat()
        _investment_orders.append(order)
        _save_investment_order(order)
    return order


@app.get("/investment-orders")
async def list_investment_orders(
    client_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    items = (
        _investment_orders_for_client(client_id)
        if client_id
        else sorted(_investment_orders, key=lambda o: o["created_at"], reverse=True)
    )
    return {"total": len(items), "items": items[:limit]}


@app.post("/casino-sessions")
async def create_casino_session(payload: dict) -> dict:
    session = _validate_casino_session(payload)
    with _casino_sessions_lock:
        session["session_id"] = _next_casino_session_id()
        session["created_at"] = datetime.now().replace(microsecond=0).isoformat()
        _casino_sessions.append(session)
        _save_casino_session(session)
    return session


@app.get("/casino-sessions/{client_id}")
async def list_casino_sessions(
    client_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    items = _casino_sessions_for_client(client_id)
    return {"client_id": client_id, "total": len(items), "items": items[:limit]}


@app.post("/casino-spins")
async def create_casino_spin(payload: dict) -> dict:
    with _casino_sessions_lock:
        session, spin = _validate_casino_spin(payload)
        now_iso = datetime.now().replace(microsecond=0).isoformat()
        session["remaining_limit_rub"] = (
            int(session["remaining_limit_rub"]) - spin["stake_rub"]
        )
        session["total_staked_rub"] = int(session["total_staked_rub"]) + spin["stake_rub"]
        session["total_win_rub"] = int(session["total_win_rub"]) + spin["win_rub"]
        session["net_result_rub"] = int(session["net_result_rub"]) + spin["net_result_rub"]
        session["updated_at"] = now_iso
        if session["remaining_limit_rub"] == 0:
            session["status"] = "completed"
            session["completed_at"] = now_iso
        with _casino_spins_lock:
            spin["spin_id"] = _next_casino_spin_id()
            spin["created_at"] = now_iso
            spin["session_status"] = session["status"]
            spin["remaining_limit_rub"] = session["remaining_limit_rub"]
            _casino_spins.append(spin)
            _save_casino_spin(spin)
        _rewrite_casino_sessions()
    return spin


@app.get("/casino-spins/{client_id}")
async def list_casino_spins(
    client_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    items = _casino_spins_for_client(client_id)
    return {"client_id": client_id, "total": len(items), "items": items[:limit]}


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
