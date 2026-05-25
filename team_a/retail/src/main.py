"""Блок retail - мобильный банк команды.

UI плюс тонкий слой: за данными и сохранением заявок ходит в backend.
Своих данных не держит.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

app = FastAPI(title="retail - мобильный банк", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "team": TEAM_NAME,
        "block": "retail",
        "commit": COMMIT,
        "backend_url": BACKEND_URL,
    }


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


async def _backend_post(path: str, payload: dict[str, Any]) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{BACKEND_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


def _client_brief(client: dict[str, Any]) -> dict[str, Any]:
    segment = client.get("segment") or "mass"
    return {
        "id": client.get("id"),
        "name": client.get("name"),
        "segment": segment,
        "is_business": segment == "sme",
        "income_rub": client.get("income_rub"),
        "balance_rub": client.get("balance_rub"),
        "products": client.get("products", []),
        "risk_score": client.get("risk_score"),
        "has_overdue_history": client.get("has_overdue_history"),
    }


def _default_amount(client: dict[str, Any]) -> int:
    income = int(client.get("income_rub") or 0)
    is_business = client.get("segment") == "sme"
    return max(50_000, min(income * (3 if is_business else 5), 2_500_000 if is_business else 1_500_000))


def _local_credit_decision(client: dict[str, Any], amount: int, months: int) -> dict[str, Any]:
    income = int(client.get("income_rub") or 0)
    balance = int(client.get("balance_rub") or 0)
    risk = float(client.get("risk_score") or 0.5)
    has_overdue = bool(client.get("has_overdue_history"))
    segment = client.get("segment") or "mass"
    is_business = segment == "sme"

    segment_multiplier = {
        "mass": 5,
        "mass_affluent": 7,
        "premium": 10,
        "private": 14,
        "sme": 4,
    }.get(segment, 5)
    risk_factor = max(0.35, 1.15 - risk)
    overdue_factor = 0.45 if has_overdue else 1.0
    liquidity_bonus = min(balance * 0.12, income * (3 if is_business else 2))
    max_amount = int((income * segment_multiplier + liquidity_bonus) * risk_factor * overdue_factor)
    max_amount = max(30_000, min(max_amount, 5_000_000))

    if has_overdue and risk >= 0.5:
        status = "declined"
        approved_amount = 0
        title = "Сейчас лучше не увеличивать нагрузку"
        explanation = (
            "Мы видим повышенную нагрузку и прошлые просрочки. "
            "Лучший следующий шаг - снизить долг и вернуться к заявке позже."
        )
    elif amount <= max_amount:
        status = "approved"
        approved_amount = amount
        title = "Бизнес-лимит доступен" if is_business else "Персональный лимит доступен"
        explanation = (
            "Обороты и профиль позволяют дать запас ликвидности для бизнеса."
            if is_business
            else "Доход, баланс и текущий риск позволяют предложить кредит без лишней нагрузки."
        )
    else:
        status = "counter_offer"
        approved_amount = max_amount
        title = "Предлагаем безопасную сумму"
        explanation = (
            "Запрошенный лимит выше комфортного уровня. Предлагаем сумму, которая не перегрузит оборот."
            if is_business
            else "Запрошенная сумма выше комфортного уровня. Предлагаем лимит, который выглядит устойчивым для бюджета клиента."
        )

    rate = (14.4 if is_business else 12.9) + risk * 10 + (1.8 if has_overdue else 0)
    monthly_payment = 0
    if approved_amount > 0:
        monthly_rate = rate / 100 / 12
        monthly_payment = int(approved_amount * monthly_rate / (1 - (1 + monthly_rate) ** (-months)))

    return {
        "status": status,
        "title": title,
        "requested_amount_rub": amount,
        "approved_amount_rub": approved_amount,
        "max_amount_rub": max_amount,
        "term_months": months,
        "rate_pct": round(rate, 1),
        "monthly_payment_rub": monthly_payment,
        "explanation": explanation,
        "product_kind": "business_limit" if is_business else "personal_limit",
        "source": "retail_demo_until_backend_save_ready",
    }


async def _try_save_credit_application(application: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return await _backend_post("/credit-applications", application)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


async def _try_get_credit_history(client_id: str) -> dict[str, Any]:
    try:
        return await _backend_get(f"/credit-applications/{client_id}")
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"client_id": client_id, "total": 0, "items": [], "storage": "not_ready"}
        raise


@app.get("/clients")
async def list_clients(request: Request) -> dict:
    return await _backend_get("/clients", dict(request.query_params))


@app.get("/transactions/{client_id}")
async def transactions(client_id: str, request: Request) -> dict:
    return await _backend_get(f"/transactions/{client_id}", dict(request.query_params))


@app.get("/api/client-brief/{client_id}")
async def api_client_brief(client_id: str) -> dict:
    client = await _backend_get(f"/clients/{client_id}")
    return _client_brief(client)


@app.get("/api/credit-history/{client_id}")
async def api_credit_history(client_id: str) -> dict:
    return await _try_get_credit_history(client_id)


@app.post("/api/credit-offer")
async def api_credit_offer(payload: dict) -> dict:
    client_id = payload.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="клиент не выбран")
    client = await _backend_get(f"/clients/{client_id}")
    decision = _local_credit_decision(client, _default_amount(client), 24)
    history = await _try_get_credit_history(client_id)
    return {"client": _client_brief(client), "offer": decision, "history": history}


@app.post("/api/credit-apply")
async def api_credit_apply(payload: dict) -> dict:
    client_id = payload.get("client_id")
    amount = int(payload.get("amount_rub") or 0)
    months = int(payload.get("term_months") or 0)
    if not client_id:
        raise HTTPException(status_code=400, detail="клиент не выбран")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="укажи сумму")
    if months not in {12, 24, 36, 48, 60}:
        raise HTTPException(status_code=400, detail="выбери срок")

    client = await _backend_get(f"/clients/{client_id}")
    decision = _local_credit_decision(client, amount, months)
    application = {
        "client_id": client_id,
        "amount_rub": amount,
        "term_months": months,
        "status": decision["status"],
        "approved_amount_rub": decision["approved_amount_rub"],
        "rate_pct": decision["rate_pct"],
        "monthly_payment_rub": decision["monthly_payment_rub"],
        "explanation": decision["explanation"],
    }
    saved = await _try_save_credit_application(application)
    return {
        "client": _client_brief(client),
        "decision": decision,
        "saved_application": saved,
        "storage": "saved" if saved else "backend_not_ready",
    }


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    return await _backend_post("/api/transfer", payload)
