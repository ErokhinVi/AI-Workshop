"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и логика кредитного решения. За данными клиента ходит в
backend. Решение — скоринг 0..100 с порогом одобрения; объяснение отказа
старается дать LLM, при недоступности — детерминированный человеческий шаблон
по факторам.
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from src.llm import LLMError, ask_llm

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8013").rstrip("/")

PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
    {"id": "credit-cash", "kind": "credit", "name": "Потребительский кредит",
     "rate_pct": 22.9, "max_amount_rub": 2_000_000, "max_term_months": 60},
    {"id": "credit-installment", "kind": "credit", "name": "Рассрочка на товары",
     "rate_pct": 0.0, "max_amount_rub": 300_000, "max_term_months": 12},
    {"id": "credit-mortgage", "kind": "credit", "name": "Ипотека",
     "rate_pct": 16.5, "max_amount_rub": 20_000_000, "max_term_months": 360},
]
PRODUCTS_BY_ID = {p["id"]: p for p in PRODUCTS}

SCORE_THRESHOLD = 60

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.1.0")


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


def _score(client: dict, amount: int, term: int) -> dict:
    """Скоринг 0..100. Возвращает score, breakdown и список негативных факторов."""
    score = 50
    factors: list[dict] = []

    age = int(client.get("age") or 0)
    if 25 <= age <= 55:
        score += 15
        factors.append({"k": "age", "delta": +15, "pos": True,
                        "label": f"возраст {age} лет — в активной финансовой группе"})
    elif age < 23 or age > 65:
        score -= 15
        factors.append({"k": "age", "delta": -15, "pos": False,
                        "label": f"возраст {age} лет — вне льготной группы кредитования"})

    income = int(client.get("income_rub") or 0)
    if income >= 100_000:
        score += 25
        factors.append({"k": "income", "delta": +25, "pos": True,
                        "label": f"доход {income:,} ₽ — высокий".replace(",", " ")})
    elif income >= 50_000:
        score += 15
        factors.append({"k": "income", "delta": +15, "pos": True,
                        "label": f"доход {income:,} ₽ — достаточный".replace(",", " ")})
    elif income > 0:
        score -= 5
        factors.append({"k": "income", "delta": -5, "pos": False,
                        "label": f"доход {income:,} ₽ — невысокий".replace(",", " ")})
    else:
        score -= 25
        factors.append({"k": "income", "delta": -25, "pos": False,
                        "label": "доход не подтверждён"})

    if client.get("has_overdue_history"):
        score -= 30
        factors.append({"k": "overdue", "delta": -30, "pos": False,
                        "label": "в кредитной истории есть просрочки"})
    else:
        score += 15
        factors.append({"k": "overdue", "delta": +15, "pos": True,
                        "label": "кредитная история чистая"})

    balance = int(client.get("balance_rub") or 0)
    if balance >= amount * 0.5:
        score += 10
        factors.append({"k": "balance", "delta": +10, "pos": True,
                        "label": "на счетах достаточно средств — хорошая подушка"})
    elif balance < amount * 0.1:
        score -= 5
        factors.append({"k": "balance", "delta": -5, "pos": False,
                        "label": "средств на счетах немного для такой суммы"})

    payment = amount // term if term else amount
    if income > 0 and payment / income > 0.5:
        score -= 20
        factors.append({"k": "load", "delta": -20, "pos": False,
                        "label": f"ежемесячный платёж ≈ {payment:,} ₽ — это больше "
                                 f"половины дохода".replace(",", " ")})

    segment = client.get("segment") or ""
    if segment in ("mass_affluent", "premium"):
        score += 10
        factors.append({"k": "segment", "delta": +10, "pos": True,
                        "label": f"премиальный сегмент ({segment})"})

    score = max(0, min(100, score))
    return {"score": score, "factors": factors,
            "negatives": [f for f in factors if not f["pos"]],
            "positives": [f for f in factors if f["pos"]]}


def _fallback_explanation(decision: str, score: int, breakdown: dict,
                          amount: int, term: int) -> str:
    if decision == "approved":
        pos = [f["label"] for f in breakdown["positives"][:3]]
        head = (f"Одобрено. Сумма {amount:,} ₽ на {term} мес, балл "
                f"{score}/100.").replace(",", " ")
        if pos:
            return head + " Помогло: " + "; ".join(pos) + "."
        return head
    negs = [f["label"] for f in breakdown["negatives"][:3]]
    head = f"К сожалению, кредит сейчас выдать не можем (балл {score}/100)."
    if negs:
        return head + " Что повлияло: " + "; ".join(negs) + "."
    return head + " По совокупности факторов это решение пока окончательное."


async def _llm_explanation(decision: str, score: int, breakdown: dict,
                           amount: int, term: int, client: dict) -> str:
    negatives = "; ".join(f["label"] for f in breakdown["negatives"]) or "нет"
    positives = "; ".join(f["label"] for f in breakdown["positives"]) or "нет"
    system = (
        "Ты — менеджер банка. Объясняешь клиенту решение по кредитной заявке "
        "коротко, по-человечески, без жаргона. Если отказ — мягко, но честно, "
        "называя 1-3 главные причины. Если одобрено — кратко поздравляешь и "
        "называешь сумму, срок, платёж. 2-3 предложения, не больше."
    )
    prompt = (
        f"Решение: {decision}. Сумма: {amount} ₽. Срок: {term} мес. "
        f"Балл: {score}/100, порог {SCORE_THRESHOLD}. "
        f"Плюсы клиента: {positives}. Минусы: {negatives}."
    )
    return (await ask_llm(prompt, system=system, max_tokens=220, temperature=0.5)).strip()


@app.post("/credit/decide")
async def credit_decide(payload: dict) -> dict:
    client_id = (payload.get("client_id") or "").strip()
    amount = int(payload.get("amount_rub") or 0)
    term = int(payload.get("term_months") or 0)
    product_id = (payload.get("product_id") or "credit-cash").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="укажи клиента")
    if amount <= 0 or term <= 0:
        raise HTTPException(status_code=400, detail="сумма и срок должны быть положительными")
    product = PRODUCTS_BY_ID.get(product_id)
    if not product or product.get("kind") != "credit":
        raise HTTPException(status_code=400, detail=f"продукт {product_id} не кредитный")
    if amount > int(product.get("max_amount_rub", 0)):
        return {
            "client_id": client_id, "product_id": product_id,
            "amount_rub": amount, "term_months": term,
            "decision": "declined", "score": 0,
            "reason": (f"Запрошенная сумма {amount:,} ₽ выше лимита продукта "
                       f"«{product['name']}» — {product['max_amount_rub']:,} ₽."
                       ).replace(",", " "),
        }
    if term > int(product.get("max_term_months", 0)):
        return {
            "client_id": client_id, "product_id": product_id,
            "amount_rub": amount, "term_months": term,
            "decision": "declined", "score": 0,
            "reason": (f"Срок {term} мес превышает лимит продукта "
                       f"«{product['name']}» ({product['max_term_months']} мес)."),
        }
    client = await _fetch_client(client_id)
    breakdown = _score(client, amount, term)
    score = breakdown["score"]
    decision = "approved" if score >= SCORE_THRESHOLD else "declined"
    try:
        reason = await _llm_explanation(decision, score, breakdown, amount, term, client)
    except LLMError:
        reason = _fallback_explanation(decision, score, breakdown, amount, term)
    return {
        "client_id": client_id, "product_id": product_id,
        "amount_rub": amount, "term_months": term,
        "decision": decision, "score": score, "reason": reason,
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
