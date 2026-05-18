"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и логика кредитного решения. За данными клиента ходит
в backend по BACKEND_URL. Решение по заявке — POST /credit/decide:
сбалансированная политика, считаем «тревожные звоночки», состоятельность
перевешивает один лишний минус. Объяснение отказа — человеческим языком
через LLM-хелпер src/llm.py.
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.llm import LLMError, ask_llm

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

# Каталог продуктов банка. Кредитный продукт добавлен в рамках задачи 1.
PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
    {"id": "credit-consumer", "kind": "credit", "name": "Потребительский кредит",
     "rate_pct": 24.0},
]

# --- Кредитное решение: пороги. Можно подкрутить после первого показа на табло. ---
RISK_SCORE_FLAG = 0.5        # внутренний рейтинг выше — тревожный звоночек
LOW_INCOME_FLAG_RUB = 35_000  # доход ниже — тревожный звоночек
STRONG_INCOME_RUB = 150_000   # доход выше — клиент считается состоятельным
STRONG_BALANCE_RUB = 500_000  # накопления выше — клиент считается состоятельным

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


class CreditRequest(BaseModel):
    """Заявка на кредит, приходит из retail."""

    client_id: str
    amount_rub: int = 0
    term_months: int = 0


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


async def _fetch_client(client_id: str) -> dict:
    """Забрать карточку клиента из ядра данных (backend)."""
    url = f"{BACKEND_URL}/clients/{client_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ядро данных недоступно: {exc}") from exc
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"клиент {client_id} не найден")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ядро данных вернуло {resp.status_code}")
    return resp.json()


def evaluate_credit(client: dict) -> tuple[bool, list[str]]:
    """Сбалансированное кредитное решение по самому клиенту.

    Считаем тревожные звоночки: просрочки в прошлом, высокий внутренний
    рейтинг риска, низкий доход. Один звоночек банк прощает, два — отказ.
    Состоятельность (солидный доход или накопления) прощает один лишний.
    Возвращаем (одобрить?, список причин-минусов для объяснения).
    """
    flags: list[str] = []
    if client.get("has_overdue_history"):
        flags.append("в прошлом были просрочки по кредитам")
    if float(client.get("risk_score", 0)) >= RISK_SCORE_FLAG:
        flags.append("внутренний рейтинг надёжности ниже комфортного уровня")
    if int(client.get("income_rub", 0)) < LOW_INCOME_FLAG_RUB:
        flags.append("текущего дохода недостаточно для комфортного обслуживания кредита")

    strong = (
        int(client.get("income_rub", 0)) >= STRONG_INCOME_RUB
        or int(client.get("balance_rub", 0)) >= STRONG_BALANCE_RUB
    )
    forgiven = 1 if strong else 0
    approved = (len(flags) - forgiven) <= 1
    return approved, flags


def _approve_text(req: CreditRequest, client: dict) -> str:
    """Короткое доброе подтверждение одобрения."""
    name = (client.get("first_name") or "").strip()
    hello = f"{name}, поздравляем! " if name else "Поздравляем! "
    if req.amount_rub > 0 and req.term_months > 0:
        amount = f"{req.amount_rub:,}".replace(",", " ")
        body = (f"Банк одобрил вашу заявку на {amount} ₽ "
                f"на {req.term_months} мес.")
    else:
        body = "Банк одобрил вашу заявку на кредит."
    return hello + body + " Оформить можно прямо в приложении — рады, что вы с нами."


async def _decline_text(client: dict, req: CreditRequest, flags: list[str]) -> str:
    """Человеческое объяснение отказа. Через LLM, с надёжным запасным вариантом."""
    reasons = "; ".join(flags)
    system = (
        "Ты — вежливый кредитный специалист Райффайзен банка. Объясни клиенту "
        "простым человеческим языком, по-русски, почему банк сейчас не может "
        "одобрить кредит. Тон — уважительный и заботливый, без канцелярита и "
        "сухих формулировок. Ровно 2–3 предложения. В конце — короткий "
        "доброжелательный совет, что поможет получить одобрение в будущем."
    )
    prompt = (
        f"Клиент: {client.get('name', 'клиент')}. "
        f"Запрошен кредит {req.amount_rub} ₽ на {req.term_months} мес. "
        f"Причины, по которым банк не может одобрить: {reasons}."
    )
    try:
        return (await ask_llm(prompt, system=system)).strip()
    except LLMError:
        return (
            f"К сожалению, сейчас мы не можем одобрить кредит. Причина: {reasons}. "
            "Это решение не окончательное — вернитесь к нам позже, и мы "
            "с радостью рассмотрим заявку снова."
        )


@app.post("/credit/decide")
async def credit_decide(req: CreditRequest) -> dict:
    """Принять заявку из retail и вынести решение по кредиту."""
    client = await _fetch_client(req.client_id)
    approved, flags = evaluate_credit(client)
    if approved:
        decision = "approve"
        explanation = _approve_text(req, client)
    else:
        decision = "decline"
        explanation = await _decline_text(client, req, flags)
    return {
        "decision": decision,
        "client_id": req.client_id,
        "client_name": client.get("name"),
        "product_id": "credit-consumer",
        "amount_rub": req.amount_rub,
        "term_months": req.term_months,
        "explanation": explanation,
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
