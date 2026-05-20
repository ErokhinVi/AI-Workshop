"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
    {"id": "credit-cash", "kind": "credit", "name": "Потребительский кредит",
     "rate_pct": 24.5, "max_amount_rub": 3_000_000, "max_term_months": 60},
]

MIN_AGE = 21
MAX_AGE = 70
MAX_PAYMENT_SHARE = 0.40
MAX_AMOUNT_RUB = 3_000_000
MAX_TERM_MONTHS = 60

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


async def _fetch_client(client_id: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BACKEND_URL}/clients/{client_id}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}") from exc
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


def _decide(client: dict, amount: int, term: int) -> dict:
    """Жёсткие правила: возраст, просрочки, нагрузка на доход, лимиты продукта."""
    if amount > MAX_AMOUNT_RUB:
        return {"decision": "declined",
                "reason": f"Запрошенная сумма {amount:,} ₽ превышает лимит "
                          f"продукта {MAX_AMOUNT_RUB:,} ₽.".replace(",", " ")}
    if term > MAX_TERM_MONTHS:
        return {"decision": "declined",
                "reason": f"Срок {term} мес превышает максимум продукта "
                          f"({MAX_TERM_MONTHS} мес)."}
    age = int(client.get("age") or 0)
    if age < MIN_AGE or age > MAX_AGE:
        return {"decision": "declined",
                "reason": f"Возраст {age} лет вне условий кредитования "
                          f"({MIN_AGE}–{MAX_AGE} лет)."}
    if client.get("has_overdue_history"):
        return {"decision": "declined",
                "reason": "В кредитной истории есть просрочки — сейчас не можем выдать кредит."}
    income = int(client.get("income_rub") or 0)
    payment = amount // term
    if income <= 0:
        return {"decision": "declined",
                "reason": "Не можем подтвердить доход — без подтверждения кредит не выдаём."}
    share = payment / income
    if share > MAX_PAYMENT_SHARE:
        return {
            "decision": "declined",
            "reason": (
                f"Ежемесячный платёж ≈ {payment:,} ₽ — это "
                f"{int(share * 100)}% дохода ({income:,} ₽). "
                f"Допустимо не больше {int(MAX_PAYMENT_SHARE * 100)}%."
            ).replace(",", " "),
        }
    return {
        "decision": "approved",
        "reason": (
            f"Одобрено. Сумма {amount:,} ₽ на {term} мес, "
            f"платёж ≈ {payment:,} ₽/мес — это "
            f"{int(share * 100)}% дохода."
        ).replace(",", " "),
    }


@app.post("/credit/decide")
async def credit_decide(payload: dict) -> dict:
    client_id = (payload.get("client_id") or "").strip()
    amount = int(payload.get("amount_rub") or 0)
    term = int(payload.get("term_months") or 0)
    if not client_id:
        raise HTTPException(status_code=400, detail="укажи клиента")
    if amount <= 0 or term <= 0:
        raise HTTPException(status_code=400, detail="сумма и срок должны быть положительными")
    client = await _fetch_client(client_id)
    verdict = _decide(client, amount, term)
    return {
        "client_id": client_id, "amount_rub": amount, "term_months": term,
        **verdict,
    }


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
