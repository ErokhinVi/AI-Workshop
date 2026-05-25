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
    {"id": "invest-pif-mixed", "kind": "investment", "name": "ПИФ «Смешанные инвестиции»", "min_amount_rub": 1000, "expected_return_pct": 12.0, "segment": "mass", "product_type": "Инвестиционные"},
    {"id": "invest-pif-bonds", "kind": "investment", "name": "ПИФ «Облигации надёжных эмитентов»", "min_amount_rub": 1000, "expected_return_pct": 9.5, "segment": "mass", "product_type": "Инвестиционные"},
    {"id": "invest-iis", "kind": "investment", "name": "Индивидуальный инвестиционный счёт (ИИС)", "min_amount_rub": 10000, "tax_deduction_rub": 52000, "expected_return_pct": 11.0, "segment": "mass", "product_type": "Инвестиционные"},
    {"id": "invest-structured-note", "kind": "investment", "name": "Структурная нота с защитой капитала", "min_amount_rub": 300000, "capital_protection_pct": 100, "expected_return_pct": 15.0, "segment": "premium", "product_type": "Инвестиционные"},
    {"id": "invest-trust-mgmt", "kind": "investment", "name": "Доверительное управление портфелем", "min_amount_rub": 1000000, "segment": "premium", "product_type": "Инвестиционные"},
    {"id": "invest-bonds-retail", "kind": "investment", "name": "Облигации федерального займа (ОФЗ)", "min_amount_rub": 1000, "expected_return_pct": 8.5, "segment": "mass", "product_type": "Инвестиционные"},
    {"id": "invest-corp-portfolio", "kind": "investment", "name": "Корпоративный инвестиционный портфель", "min_amount_rub": 5000000, "segment": "corporate", "product_type": "Инвестиционные"},
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
    term_months: int = 12             # запрошенный срок кредита, мес.
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
    # Платёж считаем по фактически запрошенному сроку, а не по максимуму
    # продукта — иначе короткий срок с большой суммой выглядит «подъёмным».
    product = next((p for p in PRODUCTS if p["id"] == req.product_id), None)
    max_term = product.get("term_months_max", 12) if product else 12
    term = max(1, min(req.term_months or max_term, max_term))
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
        base_rate = product.get("rate_pct", 16.0) if product else 16.0
        max_term = product.get("term_months_max", 12) if product else 12
        product_kind = product.get("kind", "credit") if product else "credit"

        # Индивидуальная ставка: чем выше скор — тем ниже ставка
        if score >= 80:
            offered_rate = base_rate - 2.0
        elif score >= 65:
            offered_rate = base_rate - 1.0
        elif score >= 50:
            offered_rate = base_rate
        else:
            offered_rate = base_rate + 2.5

        # Индивидуальная сумма: корректируем под реальную долговую нагрузку
        max_affordable = req.monthly_income_rub * 0.40 * max_term
        if product_kind == "credit" and req.amount_rub > max_affordable:
            offered_amount = round(max_affordable / 10000) * 10000
        elif pti > 0.40:
            offered_amount = req.amount_rub * 0.75
        else:
            offered_amount = req.amount_rub

        # Индивидуальный срок: premium получает максимум, mass — пропорционально скору
        if req.segment == "premium":
            offered_term = max_term
        elif score >= 70:
            offered_term = max_term
        elif score >= 55:
            offered_term = max(12, int(max_term * 0.75))
        else:
            offered_term = max(12, int(max_term * 0.5))

        product_name = product.get("name", "кредит") if product else "кредит"
        reason = (
            f"Заявка одобрена. Скоринговый балл {score} из 100. "
            f"Продукт: {product_name}. "
            f"Индивидуальные условия для вас: {offered_amount:,.0f} ₽ "
            f"на {offered_term} мес. по ставке {offered_rate:.1f}% годовых "
            f"(базовая ставка по продукту — {base_rate:.1f}%)."
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
                            offered_term: Optional[int],
                            req: Optional["ScoringRequest"] = None,
                            breakdown: Optional[dict] = None) -> str:
    """Просим ИИ переформулировать решение живым человеческим языком."""
    from src.llm import ask_llm, LLMError
    if decision == "approved":
        base_rate = req.score_breakdown.get("base_rate") if req and hasattr(req, "score_breakdown") else None
        product_name = ""
        if req:
            p = next((p for p in PRODUCTS if p["id"] == getattr(req, "product_id", "")), None)
            product_name = p.get("name", "") if p else ""
            base_rate = p.get("rate_pct") if p else None
        rate_note = ""
        if base_rate and offered_rate and offered_rate < base_rate:
            rate_note = f" (это на {base_rate - offered_rate:.1f}% ниже стандартной ставки по этому продукту — ваш хороший скоринговый балл позволил снизить её специально для вас)"
        prompt = (
            f"Ты сотрудник банка. Напиши клиенту тёплое сообщение об одобрении кредита. "
            f"Продукт: {product_name or 'кредит'}. "
            f"Индивидуальные условия: сумма {offered_amount:,.0f} ₽, срок {offered_term} мес., "
            f"ставка {offered_rate:.1f}% годовых{rate_note}. "
            f"Скоринговый балл клиента: {score} из 100. "
            f"Подчеркни, что условия рассчитаны персонально на основе его финансового профиля, "
            f"а не стандартные. Пиши тепло, без канцелярита. "
            f"Два-три предложения. Только русский язык."
        )
    else:
        # Собираем конкретные цифры клиента для полезного объяснения
        details = []
        if req:
            monthly_payment = req.amount_rub / max(offered_term or 12, 1)
            pti = monthly_payment / max(req.monthly_income_rub, 1)
            details.append(f"запрошенная сумма: {req.amount_rub:,.0f} ₽")
            details.append(f"доход клиента: {req.monthly_income_rub:,.0f} ₽/мес.")
            details.append(f"ежемесячный платёж составил бы {monthly_payment:,.0f} ₽ ({pti*100:.0f}% от дохода, допустимо до 40%)")
            details.append(f"возраст: {req.age} лет")
            if req.credit_history:
                max_overdue = max((h.max_overdue_days for h in req.credit_history), default=0)
                overdue_count = sum(1 for h in req.credit_history if h.max_overdue_days > 0)
                if max_overdue > 0:
                    details.append(f"в кредитной истории: {overdue_count} случай(ев) просрочки, максимум {max_overdue} дней")
            details.append(f"внешний риск-скор: {req.risk_score:.0f} из 100 (чем выше — тем хуже)")
        if breakdown:
            weak = [k for k, v in breakdown.items() if v < 8]
            if weak:
                labels = {"debt_load": "долговая нагрузка", "credit_history": "кредитная история",
                          "risk_score": "риск-скор", "age": "возраст", "segment": "сегмент"}
                details.append("слабые места: " + ", ".join(labels.get(w, w) for w in weak))

        context = "; ".join(details)
        prompt = (
            f"Ты сотрудник банка. Напиши клиенту вежливое сообщение об отказе в кредите. "
            f"Вот конкретные данные по этой заявке: {context}. "
            f"Скоринговый балл: {score} из 100 (минимум для одобрения — 45). "
            f"Объясни конкретно и по-человечески: что именно не позволило одобрить заявку и "
            f"что клиент может сделать, чтобы увеличить шансы в следующий раз — например, "
            f"запросить меньшую сумму или улучшить кредитную историю. "
            f"Без жаргона и канцелярита. Два-три абзаца. Только русский язык."
        )
    try:
        import asyncio
        return await asyncio.wait_for(
            ask_llm(prompt, max_tokens=250, temperature=0.5),
            timeout=5.0,
        )
    except (LLMError, Exception):
        return reason


@app.post("/scoring", summary="Скоринг заявки на кредит")
async def scoring(req: ScoringRequest) -> ScoringResponse:
    result = _score_request(req)
    human_reason = await _humanize_reason(
        result.decision, result.reason, result.score_total,
        result.offered_amount_rub, result.offered_rate_pct, result.offered_term_months,
        req=req, breakdown=result.score_breakdown,
    )
    return result.model_copy(update={"reason": human_reason})


# ── Ручка кредитного решения (для retail и симулятора) ────────────────────

class DecideRequest(BaseModel):
    client_id: str
    amount_rub: float
    term_months: int = 12


@app.post("/credit/decide", summary="Кредитное решение по заявке")
async def credit_decide(req: DecideRequest) -> dict:
    # Оба запроса в backend параллельно, таймаут 3 сек каждый
    async def _fetch_client(c: httpx.AsyncClient) -> dict:
        try:
            r = await c.get(f"{BACKEND_URL}/clients/{req.client_id}")
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    async def _fetch_history(c: httpx.AsyncClient) -> list:
        try:
            r = await c.get(f"{BACKEND_URL}/clients/{req.client_id}/credit-history")
            return r.json().get("items", []) if r.status_code == 200 else []
        except Exception:
            return []

    import asyncio
    async with httpx.AsyncClient(timeout=3.0) as client:
        client_data, history_items = await asyncio.gather(
            _fetch_client(client), _fetch_history(client)
        )

    credit_history: list[CreditHistoryItem] = []
    for item in history_items:
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
        term_months=req.term_months,
        product_id="credit-consumer",
        segment=client_data.get("segment", "mass"),
        monthly_income_rub=float(client_data.get("income_rub", req.amount_rub / 6)),
        age=int(client_data.get("age", 35)),
        existing_products=client_data.get("products", []),
        risk_score=float(client_data.get("risk_score", 0.4)) * 100,
        credit_history=credit_history,
    )
    # Скоринг мгновенный — запускаем ИИ-объяснение параллельно с формированием ответа
    result = _score_request(score_req)
    human_explanation = await _humanize_reason(
        result.decision, result.reason, result.score_total,
        result.offered_amount_rub, result.offered_rate_pct, result.offered_term_months,
        req=score_req, breakdown=result.score_breakdown,
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


# ── Инвестиционные рекомендации ───────────────────────────────────────────

class InvestRecommendRequest(BaseModel):
    amount_rub: float               # сумма для вложения
    horizon_months: int = 12        # горизонт вложения в месяцах
    risk_level: str = "medium"      # low | medium | high
    segment: str = "mass"           # mass | premium | corporate
    client_id: Optional[str] = None # опционально — для персонализации


class InvestProduct(BaseModel):
    id: str
    name: str
    product_type: str
    min_amount_rub: float
    expected_return_pct: Optional[float] = None
    why: str                        # объяснение почему подходит


class InvestRecommendResponse(BaseModel):
    amount_rub: float
    horizon_months: int
    risk_level: str
    recommendations: list[InvestProduct]
    summary: str                    # общий совет от ИИ


def _filter_invest_products(amount: float, horizon: int,
                             risk: str, segment: str) -> list[dict]:
    """Подбираем подходящие инвестиционные продукты по параметрам клиента."""
    invest = [p for p in PRODUCTS if p.get("kind") == "investment"]

    # Фильтр по минимальной сумме
    invest = [p for p in invest if p.get("min_amount_rub", 0) <= amount]

    # Фильтр по сегменту
    invest = [p for p in invest if p.get("segment") == segment
              or p.get("segment") == "mass"]

    # Фильтр по риску и горизонту
    if risk == "low":
        # Консервативные: ОФЗ, ПИФ облигаций, ИИС
        preferred = ["invest-bonds-retail", "invest-pif-bonds", "invest-iis"]
    elif risk == "high":
        # Агрессивные: смешанный ПИФ, структурные ноты, доверительное управление
        preferred = ["invest-pif-mixed", "invest-structured-note", "invest-trust-mgmt", "invest-corp-portfolio"]
    else:
        # Средний риск: всё кроме агрессивных корп-продуктов
        preferred = ["invest-iis", "invest-pif-mixed", "invest-pif-bonds",
                     "invest-structured-note", "invest-bonds-retail"]

    # Короткий горизонт — избегаем долгосрочных продуктов
    if horizon < 6:
        preferred = [p for p in preferred if p not in
                     ["invest-trust-mgmt", "invest-corp-portfolio"]]

    # Сортируем: сначала предпочтительные, потом остальные
    result = sorted(invest, key=lambda p: (
        0 if p["id"] in preferred else 1,
        -(p.get("expected_return_pct") or 0)
    ))
    return result[:3]


def _why(product: dict, amount: float, horizon: int, risk: str) -> str:
    """Короткое объяснение почему продукт подходит — без ИИ, как запасной вариант."""
    pid = product["id"]
    ret = product.get("expected_return_pct")
    earn = round(amount * (ret or 0) / 100 * horizon / 12) if ret else None
    base = f"Ожидаемая доходность {ret}% годовых" if ret else "Надёжный инструмент сбережений"
    if earn:
        base += f" — за {horizon} мес. примерно +{earn:,.0f} ₽ к вложенной сумме"
    if pid == "invest-iis":
        base += f". Плюс налоговый вычет до 52 000 ₽ в год от государства"
    if pid == "invest-bonds-retail":
        base += ". Государственные облигации — минимальный риск"
    if pid == "invest-structured-note":
        base += ". Капитал защищён на 100% — даже при падении рынка вы не потеряете вложенное"
    return base


@app.post("/invest/recommend", summary="Подбор инвестиционных продуктов")
async def invest_recommend(req: InvestRecommendRequest) -> InvestRecommendResponse:
    from src.llm import ask_llm, LLMError
    import asyncio

    suited = _filter_invest_products(req.amount_rub, req.horizon_months,
                                      req.risk_level, req.segment)

    # Формируем список продуктов с объяснениями
    recs = [
        InvestProduct(
            id=p["id"],
            name=p["name"],
            product_type=p.get("product_type", "Инвестиционные"),
            min_amount_rub=p.get("min_amount_rub", 0),
            expected_return_pct=p.get("expected_return_pct"),
            why=_why(p, req.amount_rub, req.horizon_months, req.risk_level),
        )
        for p in suited
    ]

    # Просим ИИ написать общий совет
    risk_label = {"low": "низкий", "medium": "средний", "high": "высокий"}.get(req.risk_level, "средний")
    products_text = "; ".join(p.name for p in recs)
    prompt = (
        f"Ты финансовый консультант банка. Клиент хочет вложить {req.amount_rub:,.0f} ₽ "
        f"на {req.horizon_months} месяцев, отношение к риску — {risk_label}. "
        f"Мы подобрали ему: {products_text}. "
        f"Напиши короткий дружелюбный совет: почему этот набор подходит именно этому клиенту, "
        f"на что обратить внимание. Без жаргона, по-человечески, два-три предложения. Только русский язык."
    )
    try:
        summary = await asyncio.wait_for(
            ask_llm(prompt, max_tokens=200, temperature=0.5), timeout=5.0
        )
    except (LLMError, Exception):
        earn_example = recs[0].expected_return_pct if recs else None
        summary = (
            f"Для суммы {req.amount_rub:,.0f} ₽ на {req.horizon_months} мес. "
            f"с {risk_label} уровнем риска мы подобрали {len(recs)} продукта. "
            + (f"Потенциальный доход по лучшему варианту — до {earn_example}% годовых." if earn_example else "")
        )

    return InvestRecommendResponse(
        amount_rub=req.amount_rub,
        horizon_months=req.horizon_months,
        risk_level=req.risk_level,
        recommendations=recs,
        summary=summary,
    )


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
