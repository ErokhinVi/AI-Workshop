"""Блок cib — корпоратив и бизнес-логика банка команды."""
from __future__ import annotations

import os
from typing import Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

# Базовый каталог. Кредитный продукт добавляет владелец блока в рамках задачи.
PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass", "product_type": "Некредитные"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0, "product_type": "Некредитные"},
    {"id": "card-credit", "kind": "credit", "name": "Кредитная карта", "limit_rub": 300000, "rate_pct": 19.9, "segment": "mass", "product_type": "Кредитные"},
    {"id": "credit-consumer", "kind": "credit", "name": "Потребительский кредит на любые цели", "amount_max_rub": 5000000, "term_months_max": 84, "rate_pct": 14.5, "segment": "mass", "product_type": "Кредитные"},
    {"id": "credit-mortgage", "kind": "credit", "name": "Ипотечный кредит", "amount_max_rub": 50000000, "term_months_max": 360, "rate_pct": 10.9, "segment": "mass", "product_type": "Кредитные"},
    {"id": "credit-syndicated", "kind": "credit", "name": "Синдицированный кредит", "amount_max_rub": 10000000000, "segment": "corporate", "product_type": "Кредитные"},
    {"id": "credit-line-corp", "kind": "credit", "name": "Кредитная линия для корпоративных клиентов", "amount_max_rub": 500000000, "segment": "corporate", "product_type": "Кредитные"},
]

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


# ── Модели скоринга ────────────────────────────────────────────────────────

class CreditHistoryItem(BaseModel):
    product: str                      # название продукта
    amount_rub: float                 # сумма кредита
    term_months: int                  # срок в месяцах
    rate_pct: float                   # процентная ставка
    opened_date: str                  # дата открытия, YYYY-MM-DD
    status: str                       # active | closed_clean | closed_overdue
    max_overdue_days: int = 0         # максимальное количество дней просрочки


class ScoringRequest(BaseModel):
    client_id: str
    amount_rub: float                 # запрошенная сумма
    product_id: str                   # id продукта из каталога
    segment: str                      # mass | premium | corporate
    monthly_income_rub: float         # ежемесячный доход
    age: int                          # возраст клиента
    existing_products: list[str] = [] # список имеющихся продуктов
    risk_score: float                 # внешний риск-скор 0–100 (100 = высокий риск)
    credit_history: list[CreditHistoryItem] = []


class ScoringResponse(BaseModel):
    client_id: str
    decision: str                     # approved | rejected
    reason: str                       # объяснение решения
    offered_amount_rub: Optional[float] = None
    offered_rate_pct: Optional[float] = None
    offered_term_months: Optional[int] = None
    score_total: int                  # итоговый балл 0–100
    score_breakdown: dict             # детализация по факторам


def _score_request(req: ScoringRequest) -> ScoringResponse:
    """Скоринговая логика: набираем баллы по каждому фактору, принимаем решение."""

    breakdown: dict = {}
    score = 0

    # 1. Долговая нагрузка: отношение платежа к доходу (PTI)
    # Ориентировочный ежемесячный платёж
    term = 12
    product = next((p for p in PRODUCTS if p["id"] == req.product_id), None)
    if product:
        term = product.get("term_months_max", 12)
    term = max(term, 1)
    monthly_payment = req.amount_rub / term
    pti = monthly_payment / max(req.monthly_income_rub, 1)
    if pti < 0.25:
        breakdown["debt_load"] = 25
        score += 25
    elif pti < 0.40:
        breakdown["debt_load"] = 15
        score += 15
    elif pti < 0.60:
        breakdown["debt_load"] = 5
        score += 5
    else:
        breakdown["debt_load"] = 0

    # 2. Возраст
    if 25 <= req.age <= 55:
        breakdown["age"] = 20
        score += 20
    elif 22 <= req.age <= 65:
        breakdown["age"] = 12
        score += 12
    else:
        breakdown["age"] = 5
        score += 5

    # 3. Кредитная история
    history_score = 0
    overdue_products = [h for h in req.credit_history if h.status == "closed_overdue" or h.max_overdue_days > 0]
    clean_products = [h for h in req.credit_history if h.status == "closed_clean"]
    active_products = [h for h in req.credit_history if h.status == "active"]
    max_overdue = max((h.max_overdue_days for h in req.credit_history), default=0)

    if not req.credit_history:
        history_score = 10  # нет истории — нейтрально
    elif max_overdue == 0 and len(clean_products) > 0:
        history_score = 25  # чистая история
    elif max_overdue <= 30:
        history_score = 15  # лёгкие просрочки
    elif max_overdue <= 90:
        history_score = 5   # серьёзные просрочки
    else:
        history_score = 0   # критические просрочки

    breakdown["credit_history"] = history_score
    score += history_score

    # 4. Внешний риск-скор (0 = лучший, 100 = худший)
    if req.risk_score <= 20:
        breakdown["risk_score"] = 20
        score += 20
    elif req.risk_score <= 40:
        breakdown["risk_score"] = 15
        score += 15
    elif req.risk_score <= 60:
        breakdown["risk_score"] = 8
        score += 8
    elif req.risk_score <= 80:
        breakdown["risk_score"] = 3
        score += 3
    else:
        breakdown["risk_score"] = 0

    # 5. Сегмент клиента
    seg_bonus = {"premium": 10, "mass": 5, "corporate": 8}.get(req.segment, 5)
    breakdown["segment"] = seg_bonus
    score += seg_bonus

    score = min(score, 100)

    # Принятие решения
    APPROVE_THRESHOLD = 45
    decision = "approved" if score >= APPROVE_THRESHOLD else "rejected"

    # Предлагаемые условия при одобрении
    offered_amount = None
    offered_rate = None
    offered_term = None
    reason = ""

    if decision == "approved":
        # Корректируем сумму под нагрузку
        if pti > 0.40:
            offered_amount = req.amount_rub * 0.75
        else:
            offered_amount = req.amount_rub

        # Ставка зависит от скора
        base_rate = product.get("rate_pct", 16.0) if product else 16.0
        if score >= 80:
            offered_rate = base_rate - 1.5
        elif score >= 60:
            offered_rate = base_rate
        else:
            offered_rate = base_rate + 2.0

        offered_term = product.get("term_months_max", 12) if product else 12
        reason = (
            f"Заявка одобрена. Скоринговый балл {score} из 100. "
            f"Предлагаем {offered_amount:,.0f} ₽ на {offered_term} мес. "
            f"по ставке {offered_rate:.1f}% годовых."
        )
    else:
        factors = []
        if breakdown.get("debt_load", 25) < 10:
            factors.append("высокая долговая нагрузка относительно дохода")
        if breakdown.get("credit_history", 25) < 10:
            factors.append("наличие серьёзных просрочек в кредитной истории")
        if breakdown.get("risk_score", 20) < 5:
            factors.append("высокий уровень риска по внешней оценке")
        if not factors:
            factors.append("совокупность факторов риска")
        reason = (
            f"В выдаче кредита отказано. Скоринговый балл {score} из 100 "
            f"(минимальный для одобрения — {APPROVE_THRESHOLD}). "
            f"Основные причины: {'; '.join(factors)}."
        )

    return ScoringResponse(
        client_id=req.client_id,
        decision=decision,
        reason=reason,
        offered_amount_rub=round(offered_amount) if offered_amount else None,
        offered_rate_pct=round(offered_rate, 2) if offered_rate else None,
        offered_term_months=offered_term,
        score_total=score,
        score_breakdown=breakdown,
    )


async def _humanize_reason(decision: str, reason: str, score: int,
                            offered_amount: Optional[float],
                            offered_rate: Optional[float],
                            offered_term: Optional[int]) -> str:
    """Просим ИИ переформулировать решение живым человеческим языком."""
    from src.llm import ask_llm, LLMError
    if decision == "approved":
        prompt = (
            f"Ты сотрудник банка. Напиши клиенту короткое радостное сообщение об одобрении кредита. "
            f"Сумма: {offered_amount:,.0f} ₽, срок: {offered_term} мес., ставка: {offered_rate:.1f}% годовых. "
            f"Пиши тепло, по-человечески, без канцелярита. Одним абзацем, не длиннее 3 предложений. Только русский язык."
        )
    else:
        prompt = (
            f"Ты сотрудник банка. Напиши клиенту вежливое и сочувствующее сообщение об отказе в кредите. "
            f"Скоринговый балл клиента: {score} из 100. Причина отказа: {reason} "
            f"Объясни по-человечески, без жаргона и канцелярита, почему так вышло и что можно улучшить. "
            f"Одним абзацем, не длиннее 4 предложений. Только русский язык."
        )
    try:
        return await ask_llm(prompt, max_tokens=200, temperature=0.5)
    except LLMError:
        return reason


@app.post("/scoring", summary="Скоринг заявки на кредит")
async def scoring(req: ScoringRequest) -> ScoringResponse:
    result = _score_request(req)
    human_reason = await _humanize_reason(
        result.decision, result.reason, result.score_total,
        result.offered_amount_rub, result.offered_rate_pct, result.offered_term_months,
    )
    return result.model_copy(update={"reason": human_reason})


# ── Ручка кредитного решения (для retail и симулятора) ────────────────────

class DecideRequest(BaseModel):
    client_id: str
    amount_rub: float
    term_months: int = 12


@app.post("/credit/decide", summary="Кредитное решение по заявке")
async def credit_decide(req: DecideRequest) -> dict:
    # Тянем реальные данные клиента из backend
    client_data: dict = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BACKEND_URL}/clients/{req.client_id}")
            if r.status_code == 200:
                client_data = r.json()
    except Exception:
        pass

    # Кредитная история из backend (если ручка есть)
    credit_history: list[CreditHistoryItem] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BACKEND_URL}/clients/{req.client_id}/credit-history")
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    try:
                        credit_history.append(CreditHistoryItem(
                            product=item.get("product", ""),
                            amount_rub=float(item.get("principal_rub", 0)),
                            term_months=int(item.get("term_months", 12)),
                            rate_pct=float(item.get("rate_pct", 0)),
                            opened_date=item.get("opened_at", "2020-01-01"),
                            status=item.get("status", "active"),
                            max_overdue_days=int(item.get("overdue_days_max", 0)),
                        ))
                    except Exception:
                        pass
    except Exception:
        pass

    # Если данных нет — строим историю из флага has_overdue_history
    if not credit_history and client_data.get("has_overdue_history"):
        credit_history = [CreditHistoryItem(
            product="unknown", amount_rub=0, term_months=12,
            rate_pct=0, opened_date="2020-01-01",
            status="closed_overdue", max_overdue_days=60,
        )]

    score_req = ScoringRequest(
        client_id=req.client_id,
        amount_rub=req.amount_rub,
        product_id="credit-consumer",
        segment=client_data.get("segment", "mass"),
        monthly_income_rub=float(client_data.get("income_rub", req.amount_rub / 6)),
        age=int(client_data.get("age", 35)),
        existing_products=client_data.get("products", []),
        risk_score=float(client_data.get("risk_score", 0.4)) * 100,
        credit_history=credit_history,
    )
    result = _score_request(score_req)
    human_explanation = await _humanize_reason(
        result.decision, result.reason, result.score_total,
        result.offered_amount_rub, result.offered_rate_pct, result.offered_term_months,
    )
    return {
        "client_id": req.client_id,
        "decision": result.decision,
        "explanation": human_explanation,
        "offered_amount_rub": result.offered_amount_rub,
        "offered_rate_pct": result.offered_rate_pct,
        "offered_term_months": result.offered_term_months,
        "score_total": result.score_total,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    rows = "".join(
        f"<tr><td>{p['id']}</td><td>{p['kind']}</td><td>{p.get('product_type','')}</td><td>{p['name']}</td></tr>"
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
        f"<table><tr><th>id</th><th>вид</th><th>тип</th><th>название</th></tr>{rows}</table>"
        "<h2 style='margin-top:32px;font-weight:500'>API скоринга</h2>"
        "<p><code>POST /scoring</code> — скоринг заявки на кредит с полным набором данных клиента</p>"
        "<p><code>POST /credit/decide</code> — быстрое кредитное решение (для интеграции)</p>"
        "<p><a href='/docs' style='color:#7eb3ff'>Документация API →</a></p>"
        "</body></html>"
    )
