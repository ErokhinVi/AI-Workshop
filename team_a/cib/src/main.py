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
MAX_PLAN_BYTES = 200_000


def _default_plan_path() -> Path:
    common_plan = Path(__file__).resolve().parents[1] / "tasks" / "TEAM_INTERACTION_PLAN.md"
    if common_plan.exists():
        return common_plan
    return Path(__file__).with_name("PLAN.md")


PLAN_PATH = Path(os.environ.get("PLAN_PATH") or _default_plan_path())

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

INVESTMENT_INSTRUMENTS = [
    {
        "ticker": "SBRF",
        "name": "Сбербанк",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 290.5,
        "lot_size": 1,
        "risk_level": "medium",
        "sector": "banks",
    },
    {
        "ticker": "VTBR",
        "name": "ВТБ",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 0.025,
        "lot_size": 1000,
        "risk_level": "high",
        "sector": "banks",
    },
    {
        "ticker": "ROSN",
        "name": "Роснефть",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 580.0,
        "lot_size": 1,
        "risk_level": "medium",
        "sector": "oil_and_gas",
    },
    {
        "ticker": "SIBN",
        "name": "Газпром нефть",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 760.0,
        "lot_size": 1,
        "risk_level": "medium",
        "sector": "oil_and_gas",
    },
]
INSTRUMENTS_BY_TICKER = {item["ticker"]: item for item in INVESTMENT_INSTRUMENTS}
INVESTMENT_COMMISSION_RATE = 0.003

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


class CreditDecisionRequest(BaseModel):
    client_id: str = Field(min_length=1)
    amount_rub: int = Field(gt=0)
    term_months: int = Field(ge=3, le=84)
    purpose: str | None = None


class InvestmentQuoteRequest(BaseModel):
    client_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1)
    side: str = "buy"
    quantity: int = Field(gt=0)


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
    credit_profile = (
        client.get("credit_profile")
        if isinstance(client.get("credit_profile"), dict)
        else {}
    )
    active_debt = int(credit_profile.get("active_debt_rub") or 0)
    max_overdue_days = int(credit_profile.get("max_overdue_days") or 0)
    active_credits = int(credit_profile.get("active_credits") or 0)
    rate_pct = _choose_rate(client)
    monthly_payment = _monthly_payment(payload.amount_rub, payload.term_months, rate_pct)
    payment_to_income = monthly_payment / income if income > 0 else 1.0
    amount_to_income = payload.amount_rub / income if income > 0 else 99.0
    burden_pct = round(payment_to_income * 100, 1)
    amount_to_income_pct = round(amount_to_income * 100, 1)

    if has_overdue and (payment_to_income > 0.35 or amount_to_income > 6):
        status = "declined"
        approved_amount = 0
        explanation = (
            f"Мы не можем одобрить заявку на {payload.amount_rub:,} ₽ на выбранных "
            f"условиях: при доходе {income:,} ₽ расчётный платёж составил бы "
            f"{monthly_payment:,} ₽, то есть {burden_pct}% дохода. В профиле есть "
            f"просрочки до {max_overdue_days} дней, активных кредитов: {active_credits}, "
            f"текущий активный долг около {active_debt:,} ₽, риск-скор {risk_score:.3f}. "
            "Рекомендуем уменьшить сумму, выбрать более длинный срок и несколько "
            "месяцев подтверждать стабильные платежи без задержек."
        )
    elif payment_to_income <= 0.35 and risk_score <= 0.45 and not has_overdue:
        status = "approved"
        approved_amount = payload.amount_rub
        explanation = (
            f"Заявка одобрена: при доходе {income:,} ₽ и балансе {balance:,} ₽ "
            f"ежемесячный платёж {monthly_payment:,} ₽ составляет только {burden_pct}% "
            f"дохода. Просрочек в профиле нет, риск-скор {risk_score:.3f}, активная "
            f"кредитная нагрузка около {active_debt:,} ₽. Клиент может подтвердить "
            "условия в мобильном банке и перейти к оформлению."
        )
    elif payment_to_income <= 0.5 and risk_score <= 0.55:
        status = "counter_offer"
        approved_amount = max(50_000, min(payload.amount_rub, int(income * 4)))
        monthly_payment = _monthly_payment(approved_amount, payload.term_months, rate_pct)
        revised_burden_pct = (
            round((monthly_payment / income) * 100, 1) if income > 0 else 100.0
        )
        explanation = (
            f"Запрошенная сумма {payload.amount_rub:,} ₽ создаёт повышенную нагрузку: "
            f"первичный платёж был бы {burden_pct}% дохода при риск-скоре {risk_score:.3f}. "
            f"Предлагаем безопасную сумму {approved_amount:,} ₽: платёж около "
            f"{monthly_payment:,} ₽, или {revised_burden_pct}% дохода. Такой вариант "
            "оставляет запас на регулярные расходы и снижает риск просрочки."
        )
    else:
        status = "declined"
        approved_amount = 0
        explanation = (
            f"Заявка отклонена: сумма {payload.amount_rub:,} ₽ равна {amount_to_income_pct}% "
            f"годового дохода, а расчётный платёж {monthly_payment:,} ₽ занял бы "
            f"{burden_pct}% текущего дохода. С учётом риск-скора {risk_score:.3f}, "
            f"активного долга около {active_debt:,} ₽ и истории просрочек до "
            f"{max_overdue_days} дней такая нагрузка выглядит небезопасной. Лучше "
            "запросить меньшую сумму или увеличить срок."
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
        "amount_to_income_pct": amount_to_income_pct,
        "explanation": explanation,
        "reason": explanation,
        "title": {
            "approved": "Кредит одобрен на запрошенных условиях",
            "counter_offer": "Предлагаем более безопасные условия",
            "declined": "Сейчас лучше не увеличивать долговую нагрузку",
        }[status],
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
            "active_debt_rub": active_debt,
            "max_overdue_days": max_overdue_days,
            "active_credits": active_credits,
        },
    }


def _investment_products() -> list[dict[str, Any]]:
    return [
        {
            "id": f"stock-{instrument['ticker'].lower()}",
            "kind": "investment",
            "asset_class": "stock",
            "name": f"Акции {instrument['name']}",
            "ticker": instrument["ticker"],
            "currency": instrument["currency"],
            "price_rub": instrument["price_rub"],
            "lot_size": instrument["lot_size"],
            "risk_level": instrument["risk_level"],
        }
        for instrument in INVESTMENT_INSTRUMENTS
    ]


def _money(value: int | float) -> str:
    numeric = float(value)
    precision = 3 if 0 < abs(numeric) < 1 else 2
    rounded = round(numeric, precision)
    if rounded.is_integer():
        return f"{int(rounded):,}".replace(",", " ")
    whole, fraction = f"{rounded:,.{precision}f}".split(".")
    fraction = fraction.rstrip("0")
    return f"{whole.replace(',', ' ')}.{fraction}"


def _quote_investment(payload: InvestmentQuoteRequest, client: dict[str, Any]) -> dict[str, Any]:
    ticker = payload.ticker.upper().strip()
    side = payload.side.lower().strip()
    if side != "buy":
        raise HTTPException(status_code=400, detail="сейчас поддерживается только покупка")
    instrument = INSTRUMENTS_BY_TICKER.get(ticker)
    if not instrument:
        allowed = ", ".join(INSTRUMENTS_BY_TICKER)
        raise HTTPException(status_code=404, detail=f"бумага не найдена, доступны: {allowed}")

    quantity = int(payload.quantity)
    price_rub = float(instrument["price_rub"])
    amount_rub = round(price_rub * quantity, 2)
    commission_rub = round(max(amount_rub * INVESTMENT_COMMISSION_RATE, 0.01), 2)
    total_rub = round(amount_rub + commission_rub, 2)
    balance = float(client.get("balance_rub") or 0)
    client_name = str(client.get("name") or payload.client_id)
    enough_cash = balance >= total_rub
    explanation = (
        f"Покупка {quantity} шт. {ticker} ({instrument['name']}) рассчитана по цене "
        f"{_money(price_rub)} ₽ за бумагу. Сумма сделки {_money(amount_rub)} ₽, комиссия "
        f"{_money(commission_rub)} ₽, всего к списанию {_money(total_rub)} ₽. "
    )
    if enough_cash:
        explanation += (
            f"На счёте клиента {client_name} достаточно средств: доступно {_money(balance)} ₽. "
            "После подтверждения retail может сохранить сделку в backend и показать её в портфеле."
        )
    else:
        explanation += (
            f"На счёте клиента {client_name} сейчас {_money(balance)} ₽, этого меньше суммы покупки. "
            "Покажите клиенту расчёт и предложите уменьшить количество бумаг или пополнить счёт."
        )

    return {
        "status": "quoted",
        "client_id": payload.client_id,
        "ticker": ticker,
        "side": "buy",
        "quantity": quantity,
        "price_rub": price_rub,
        "amount_rub": amount_rub,
        "commission_rate": INVESTMENT_COMMISSION_RATE,
        "commission_rub": commission_rub,
        "total_rub": total_rub,
        "currency": instrument["currency"],
        "instrument": instrument,
        "client_cash_balance_rub": balance,
        "enough_cash": enough_cash,
        "decision": "ready_to_buy" if enough_cash else "insufficient_funds",
        "explanation": explanation,
        "reason": explanation,
        "next_step": (
            "Подтвердите покупку в мобильном банке."
            if enough_cash
            else "Уменьшите количество бумаг или пополните счёт."
        ),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL,
            "products": len(PRODUCTS) + len(INVESTMENT_INSTRUMENTS)}


@app.get("/products")
async def products() -> dict:
    items = [*PRODUCTS, *_investment_products()]
    return {"total": len(items), "items": items}


@app.post("/credit/decide")
async def credit_decide(payload: CreditDecisionRequest) -> dict:
    client = await _get_client(payload.client_id)
    return _decide_credit(payload, client)


@app.get("/investments/instruments")
async def investment_instruments() -> dict:
    return {"total": len(INVESTMENT_INSTRUMENTS), "items": INVESTMENT_INSTRUMENTS}


@app.post("/investments/quote")
async def investment_quote(payload: InvestmentQuoteRequest) -> dict:
    client = await _get_client(payload.client_id)
    return _quote_investment(payload, client)


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
        for p in [*PRODUCTS, *_investment_products()]
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
