"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")
PLAN_PATH = Path(os.environ.get("PLAN_PATH", Path(__file__).with_name("PLAN.md")))
MAX_PLAN_BYTES = 200_000

# Базовый каталог. Кредитный продукт добавляет владелец блока в рамках задачи.
PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
    {
        "id": "credit-cash",
        "kind": "credit",
        "name": "Кредит наличными",
        "min_amount_rub": 50_000,
        "max_amount_rub": 3_000_000,
        "min_term_months": 3,
        "max_term_months": 84,
    },
]

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


class CreditDecisionRequest(BaseModel):
    client_id: str = Field(min_length=1)
    amount_rub: int = Field(gt=0)
    term_months: int = Field(ge=3, le=84)
    purpose: str | None = None


async def _get_client(client_id: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{BACKEND_URL}/clients/{client_id}")
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Данные клиента сейчас недоступны, попробуйте позже.",
        ) from exc
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Клиент {client_id} не найден.")
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail="Backend вернул ошибку при получении данных клиента.",
        )
    data = response.json()
    if not isinstance(data, dict) or data.get("id") != client_id:
        raise HTTPException(status_code=502, detail="Backend вернул некорректные данные клиента.")
    return data


def _monthly_payment(amount_rub: int, term_months: int, rate_pct: float) -> int:
    monthly_rate = rate_pct / 100 / 12
    if monthly_rate <= 0:
        return round(amount_rub / term_months)
    factor = (1 + monthly_rate) ** term_months
    return round(amount_rub * monthly_rate * factor / (factor - 1))


def _choose_rate(client: dict[str, Any]) -> float:
    risk_score = float(client.get("risk_score") or 0.35)
    segment = str(client.get("segment") or "mass")
    rate = 13.9 + risk_score * 10
    if segment in {"premium", "private"}:
        rate -= 2.0
    elif segment == "sme":
        rate -= 0.8
    if client.get("has_overdue_history"):
        rate += 4.0
    return round(max(11.9, min(rate, 27.5)), 1)


def _decide_credit(payload: CreditDecisionRequest, client: dict[str, Any]) -> dict[str, Any]:
    income = int(client.get("income_rub") or 0)
    balance = int(client.get("balance_rub") or 0)
    risk_score = float(client.get("risk_score") or 0.5)
    has_overdue = bool(client.get("has_overdue_history"))
    rate_pct = _choose_rate(client)
    monthly_payment = _monthly_payment(payload.amount_rub, payload.term_months, rate_pct)
    payment_to_income = monthly_payment / income if income > 0 else 1.0
    amount_to_income = payload.amount_rub / income if income > 0 else 99.0

    if has_overdue and (payment_to_income > 0.35 or amount_to_income > 6):
        status = "declined"
        approved_amount = 0
        explanation = (
            "Мы не можем одобрить заявку на выбранных условиях: по клиентскому профилю "
            "видны прошлые просрочки, а запрошенный платёж создаёт слишком высокую "
            "нагрузку относительно текущего дохода. Рекомендуем уменьшить сумму, "
            "увеличить срок или сначала стабилизировать кредитную историю."
        )
    elif payment_to_income <= 0.35 and risk_score <= 0.45 and not has_overdue:
        status = "approved"
        approved_amount = payload.amount_rub
        explanation = (
            "Заявка одобрена: доход, баланс и кредитная история клиента позволяют "
            "обслуживать такой платёж без повышенной нагрузки. Предлагаем подтвердить "
            "условия в мобильном банке и перейти к оформлению."
        )
    elif payment_to_income <= 0.5 and risk_score <= 0.55:
        status = "counter_offer"
        approved_amount = max(50_000, min(payload.amount_rub, int(income * 4)))
        monthly_payment = _monthly_payment(approved_amount, payload.term_months, rate_pct)
        explanation = (
            "Полностью одобрить запрошенную сумму рискованно, но профиль клиента "
            "позволяет предложить более безопасные условия. Встречное предложение "
            "снижает ежемесячную нагрузку и оставляет запас на регулярные расходы."
        )
    else:
        status = "declined"
        approved_amount = 0
        explanation = (
            "Заявка отклонена, потому что сочетание суммы, срока, дохода и риск-профиля "
            "клиента создаёт чрезмерную долговую нагрузку. Чтобы повысить шанс "
            "одобрения, стоит запросить меньшую сумму или выбрать более длинный срок."
        )

    return {
        "application_id": f"cr-{payload.client_id}-{payload.amount_rub}-{payload.term_months}",
        "client_id": payload.client_id,
        "status": status,
        "decision": status,
        "amount_rub": payload.amount_rub,
        "approved_amount_rub": approved_amount,
        "term_months": payload.term_months,
        "rate_pct": rate_pct,
        "monthly_payment_rub": monthly_payment if approved_amount else 0,
        "payment_to_income_pct": round(payment_to_income * 100, 1),
        "explanation": explanation,
        "reason": explanation,
        "next_step": (
            "Подтвердите условия в мобильном банке."
            if status == "approved"
            else "Измените параметры заявки или обратитесь за консультацией."
        ),
        "client_snapshot": {
            "name": client.get("name"),
            "segment": client.get("segment"),
            "income_rub": income,
            "balance_rub": balance,
            "risk_score": risk_score,
            "has_overdue_history": has_overdue,
        },
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


@app.post("/credit/decide")
async def credit_decide(payload: CreditDecisionRequest) -> dict:
    client = await _get_client(payload.client_id)
    return _decide_credit(payload, client)


@app.get("/meta/plan", response_class=PlainTextResponse)
async def get_plan() -> PlainTextResponse:
    if not PLAN_PATH.exists():
        return PlainTextResponse("", media_type="text/markdown; charset=utf-8")
    return PlainTextResponse(
        PLAN_PATH.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


@app.post("/meta/plan", response_class=PlainTextResponse)
async def update_plan(request: Request) -> PlainTextResponse:
    body = await request.body()
    if len(body) > MAX_PLAN_BYTES:
        raise HTTPException(status_code=413, detail="план слишком большой")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="ожидается UTF-8 markdown") from exc
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(text, encoding="utf-8")
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


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
