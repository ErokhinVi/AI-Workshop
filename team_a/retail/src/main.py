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
CIB_URL = os.environ.get("CIB_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="retail - мобильный банк", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"

INVESTMENT_INSTRUMENTS = [
    {
        "ticker": "SBRF",
        "name": "Сбербанк",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 290.5,
        "lot_size": 1,
        "risk_level": "medium",
    },
    {
        "ticker": "VTBR",
        "name": "ВТБ",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 0.025,
        "lot_size": 1000,
        "risk_level": "high",
    },
    {
        "ticker": "ROSN",
        "name": "Роснефть",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 580.0,
        "lot_size": 1,
        "risk_level": "medium",
    },
    {
        "ticker": "SIBN",
        "name": "Газпром нефть",
        "kind": "stock",
        "currency": "RUB",
        "price_rub": 760.0,
        "lot_size": 1,
        "risk_level": "medium",
    },
]
INVESTMENT_BY_TICKER = {item["ticker"]: item for item in INVESTMENT_INSTRUMENTS}


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "team": TEAM_NAME,
        "block": "retail",
        "commit": COMMIT,
        "backend_url": BACKEND_URL,
        "cib_url": CIB_URL,
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


async def _cib_get(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{CIB_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"cib недоступен: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


async def _cib_post(path: str, payload: dict[str, Any]) -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"{CIB_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"cib недоступен: {exc}")
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


def _rub(amount: int | float | None) -> str:
    return f"{int(amount or 0):,}".replace(",", " ")


def _risk_text(client: dict[str, Any]) -> str:
    risk = float(client.get("risk_score") or 0)
    overdue = "есть прошлые просрочки" if client.get("has_overdue_history") else "просрочек не видно"
    return f"риск-профиль {round(risk * 100, 1)}%, {overdue}"


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
            "По текущему профилю мы не рекомендуем брать новый кредит: видим прошлые просрочки, "
            "повышенный риск и недостаточный запас по регулярному доходу. Чтобы повысить шанс "
            "одобрения, сначала снизьте долговую нагрузку, внесите несколько платежей без задержек "
            "и вернитесь к заявке через 2-3 месяца. Так клиент не получит платеж, который может "
            "стать тяжелым для бюджета."
        )
    elif amount <= max_amount:
        status = "approved"
        approved_amount = amount
        title = "Бизнес-лимит доступен" if is_business else "Персональный лимит доступен"
        explanation = (
            "Заявка одобрена: обороты, остаток на счете и текущий риск позволяют дать бизнесу "
            "запас ликвидности без перегрузки денежного потока. Лимит можно использовать на "
            "закупку, аренду, налоги или сезонный разрыв, а ежемесячный платеж уже рассчитан "
            "так, чтобы его было удобно планировать."
            if is_business
            else "Заявка одобрена: регулярный доход, баланс и кредитный профиль позволяют "
            "предложить эту сумму без лишней нагрузки на бюджет. Мы сразу показываем ставку, "
            "ежемесячный платеж и срок, чтобы клиент понимал условия до подтверждения заявки."
        )
    else:
        status = "counter_offer"
        approved_amount = max_amount
        title = "Предлагаем безопасную сумму"
        explanation = (
            "Запрошенный лимит выше комфортного уровня для текущих оборотов. Вместо отказа "
            "предлагаем безопасную сумму: она сохраняет запас ликвидности, не перегружает "
            "денежный поток и дает понятный ежемесячный платеж для планирования бизнеса."
            if is_business
            else "Запрошенная сумма выше комфортного уровня для текущего бюджета. Вместо отказа "
            "предлагаем лимит, который выглядит устойчивым: платеж остается прогнозируемым, "
            "а клиент сохраняет запас на регулярные расходы и непредвиденные покупки."
        )

    rate = (14.4 if is_business else 12.9) + risk * 10 + (1.8 if has_overdue else 0)
    monthly_payment = 0
    if approved_amount > 0:
        monthly_rate = rate / 100 / 12
        monthly_payment = int(approved_amount * monthly_rate / (1 - (1 + monthly_rate) ** (-months)))

    return {
        "status": status,
        "status_label": {
            "approved": "одобрено",
            "counter_offer": "встречное предложение",
            "declined": "не рекомендуем сейчас",
        }[status],
        "title": title,
        "requested_amount_rub": amount,
        "approved_amount_rub": approved_amount,
        "max_amount_rub": max_amount,
        "term_months": months,
        "rate_pct": round(rate, 1),
        "monthly_payment_rub": monthly_payment,
        "explanation": explanation,
        "conditions": {
            "product_name": "Бизнес-лимит на оборот" if is_business else "Персональный кредитный лимит",
            "product_type": "credit",
            "currency": "RUB",
            "decision_valid_days": 7,
            "next_step": (
                "Заявка сохранена. Менеджер может связаться с клиентом для выдачи лимита."
                if is_business and approved_amount > 0
                else "Заявка сохранена. Клиент может вернуться к ней из истории заявок."
            ),
        },
        "product_kind": "business_limit" if is_business else "personal_limit",
        "source": "retail_credit_limit",
    }


def _decorate_credit_decision(
    decision: dict[str, Any],
    client: dict[str, Any],
    amount: int,
    months: int,
    source: str,
) -> dict[str, Any]:
    if isinstance(decision.get("decision"), dict):
        decision = decision["decision"]
    is_business = client.get("segment") == "sme"
    status = decision.get("status") or decision.get("decision") or "counter_offer"
    approved_amount = int(decision.get("approved_amount_rub") or 0)
    if status == "approved" and approved_amount <= 0:
        approved_amount = amount
    rate = decision.get("rate_pct")
    rate = round(float(rate), 1) if rate is not None else None
    monthly_payment = int(decision.get("monthly_payment_rub") or 0)
    if approved_amount > 0 and rate and monthly_payment <= 0:
        monthly_rate = rate / 100 / 12
        monthly_payment = int(approved_amount * monthly_rate / (1 - (1 + monthly_rate) ** (-months)))

    title = decision.get("title")
    if not title:
        title = {
            "approved": "Бизнес-лимит доступен" if is_business else "Персональный лимит доступен",
            "counter_offer": "Предлагаем безопасную сумму",
            "declined": "Сейчас лучше не увеличивать нагрузку",
        }.get(status, "Решение по заявке")

    requested_payment = monthly_payment
    if requested_payment <= 0 and rate:
        monthly_rate = rate / 100 / 12
        requested_payment = int(amount * monthly_rate / (1 - (1 + monthly_rate) ** (-months)))
    income = int(client.get("income_rub") or 0)
    balance = int(client.get("balance_rub") or 0)
    payment_share = round((requested_payment / income) * 100, 1) if income > 0 and requested_payment else 0
    base_explanation = (decision.get("explanation") or decision.get("reason") or "").strip()
    explanation = (
        f"{title}. Запрошено {_rub(amount)} ₽ на {months} мес.; ориентировочный платеж "
        f"{_rub(requested_payment)} ₽, это {payment_share}% от регулярного дохода "
        f"{_rub(income)} ₽. На счете {_rub(balance)} ₽, {_risk_text(client)}. "
    )
    if status == "approved":
        explanation += (
            "Поэтому условия выглядят комфортными: у клиента остается запас на повседневные "
            "расходы, а ставка и срок понятны до подтверждения заявки. "
        )
    elif status == "counter_offer":
        explanation += (
            "Полная сумма может перегрузить бюджет, поэтому предлагаем меньший лимит с более "
            "устойчивым платежом и сохраняем заявку, чтобы клиент мог вернуться к условиям. "
        )
    else:
        explanation += (
            "Мы не рекомендуем выдавать новый долг на таких условиях: сначала лучше снизить "
            "нагрузку или выбрать меньшую сумму, чтобы не ухудшить финансовое положение клиента. "
        )
    if base_explanation:
        explanation += base_explanation

    return {
        "status": status,
        "status_label": {
            "approved": "одобрено",
            "counter_offer": "встречное предложение",
            "declined": "не рекомендуем сейчас",
        }.get(status, "решение"),
        "title": title,
        "requested_amount_rub": amount,
        "approved_amount_rub": approved_amount,
        "max_amount_rub": int(decision.get("max_amount_rub") or approved_amount or 0),
        "term_months": months,
        "rate_pct": rate,
        "monthly_payment_rub": monthly_payment,
        "explanation": explanation,
        "conditions": {
            "product_name": decision.get("product_name")
            or ("Бизнес-лимит на оборот" if is_business else "Персональный кредитный лимит"),
            "product_type": decision.get("product_type") or "credit",
            "currency": "RUB",
            "decision_valid_days": int(decision.get("decision_valid_days") or 7),
            "next_step": (
                decision.get("next_step")
                or "Заявка сохранена. Клиент может вернуться к ней из истории заявок."
            ),
        },
        "product_kind": "business_limit" if is_business else "personal_limit",
        "source": source,
    }


async def _credit_decision(client: dict[str, Any], amount: int, months: int) -> dict[str, Any]:
    payload = {"client_id": client["id"], "amount_rub": amount, "term_months": months}
    try:
        cib_decision = await _cib_post("/credit/decide", payload)
        return _decorate_credit_decision(cib_decision, client, amount, months, "cib")
    except HTTPException as exc:
        if exc.status_code not in {404, 405, 502}:
            raise
        decision = _local_credit_decision(client, amount, months)
        decision["source"] = "retail_fallback_waiting_for_cib"
        decision["integration_note"] = (
            "Retail уже умеет отправлять заявку в CIB, но CIB пока не отдал /credit/decide."
        )
        return decision


async def _try_save_credit_application(application: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return await _backend_post("/credit-applications", application)
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return None
        raise


async def _try_get_credit_history(client_id: str) -> dict[str, Any]:
    try:
        return await _backend_get(f"/credit-applications/{client_id}")
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"client_id": client_id, "total": 0, "items": [], "storage": "not_ready"}
        raise


def _local_investment_quote(client_id: str, ticker: str, quantity: int) -> dict[str, Any]:
    instrument = INVESTMENT_BY_TICKER.get(ticker)
    if not instrument:
        raise HTTPException(status_code=400, detail="выберите доступный тикер")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="укажите количество")
    lot_size = int(instrument["lot_size"])
    lots = quantity if ticker != "VTBR" else max(1, quantity)
    effective_quantity = lots * lot_size if ticker == "VTBR" else quantity
    price = float(instrument["price_rub"])
    amount = round(effective_quantity * price, 2)
    commission = round(max(amount * 0.003, 1.0), 2)
    total = round(amount + commission, 2)
    return {
        "status": "quoted",
        "client_id": client_id,
        "ticker": ticker,
        "name": instrument["name"],
        "side": "buy",
        "quantity": effective_quantity,
        "lots": lots if ticker == "VTBR" else quantity,
        "price_rub": price,
        "amount_rub": amount,
        "commission_rub": commission,
        "total_rub": total,
        "risk_level": instrument["risk_level"],
        "explanation": (
            f"Покупка {effective_quantity} шт. {ticker} рассчитана по ориентировочной "
            f"цене {price} ₽. Сумма сделки {amount} ₽, комиссия 0.3% — {commission} ₽, "
            f"итого к списанию {total} ₽. Инструмент относится к уровню риска "
            f"{instrument['risk_level']}; перед подтверждением клиент видит цену, "
            "комиссию и полный итог."
        ),
        "source": "retail_fallback_waiting_for_cib",
    }


async def _investment_instruments() -> dict[str, Any]:
    try:
        data = await _cib_get("/investments/instruments")
        items = data.get("items", [])
        if isinstance(items, list) and items:
            return {"total": len(items), "items": items, "source": "cib"}
    except HTTPException as exc:
        if exc.status_code not in {404, 405, 502}:
            raise
    return {
        "total": len(INVESTMENT_INSTRUMENTS),
        "items": INVESTMENT_INSTRUMENTS,
        "source": "retail_fallback_waiting_for_cib",
    }


async def _investment_quote(client_id: str, ticker: str, quantity: int) -> dict[str, Any]:
    payload = {"client_id": client_id, "ticker": ticker, "side": "buy", "quantity": quantity}
    try:
        quote = await _cib_post("/investments/quote", payload)
        if quote.get("ticker") and quote.get("amount_rub") is not None:
            quote.setdefault("source", "cib")
            quote.setdefault("explanation", "Расчет покупки получен из CIB.")
            return quote
    except HTTPException as exc:
        if exc.status_code not in {404, 405, 502}:
            raise
    return _local_investment_quote(client_id, ticker, quantity)


async def _try_save_investment_order(order: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return await _backend_post("/investment-orders", order)
    except HTTPException as exc:
        if exc.status_code in {400, 404, 405, 502}:
            return None
        raise


async def _try_get_investment_portfolio(client_id: str) -> dict[str, Any]:
    try:
        return await _backend_get(f"/investment-portfolio/{client_id}")
    except HTTPException as exc:
        if exc.status_code in {400, 404, 405, 502}:
            return {
                "client_id": client_id,
                "cash_balance_rub": 0,
                "positions": [],
                "storage": "backend_not_ready",
            }
        raise


async def _try_get_investment_orders(client_id: str) -> dict[str, Any]:
    try:
        return await _backend_get("/investment-orders", {"client_id": client_id, "limit": 5})
    except HTTPException as exc:
        if exc.status_code in {400, 404, 405, 502}:
            return {"client_id": client_id, "total": 0, "items": [], "storage": "backend_not_ready"}
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
    cib_ready = False
    try:
        products = await _cib_get("/products")
        cib_ready = any(
            "credit" in str(p.get("kind", "")).lower()
            or "credit" in str(p.get("id", "")).lower()
            or "кредит" in str(p.get("name", "")).lower()
            for p in products.get("items", [])
        )
    except HTTPException:
        pass
    return {
        "client": _client_brief(client),
        "offer": decision,
        "history": history,
        "integration": {"cib_credit_product_ready": cib_ready},
    }


@app.post("/api/credit-apply")
async def api_credit_apply(payload: dict) -> dict:
    client_id = payload.get("client_id")
    amount = int(payload.get("amount_rub") or 0)
    months = int(payload.get("term_months") or 0)
    if not client_id:
        raise HTTPException(status_code=400, detail="клиент не выбран")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="укажи сумму")
    if months < 3 or months > 84:
        raise HTTPException(status_code=400, detail="выбери срок от 3 до 84 месяцев")

    client = await _backend_get(f"/clients/{client_id}")
    decision = await _credit_decision(client, amount, months)
    application = {
        "client_id": client_id,
        "amount_rub": amount,
        "term_months": months,
        "status": decision["status"],
        "approved_amount_rub": decision["approved_amount_rub"],
        "rate_pct": decision["rate_pct"],
        "monthly_payment_rub": decision["monthly_payment_rub"],
        "explanation": decision["explanation"],
        "product_type": decision["conditions"]["product_type"],
        "product_name": decision["conditions"]["product_name"],
    }
    saved = await _try_save_credit_application(application)
    return {
        "client": _client_brief(client),
        "status": decision["status"],
        "explanation": decision["explanation"],
        "reason": decision["explanation"],
        "decision": decision,
        "saved_application": saved,
        "storage": "saved" if saved else "backend_not_ready",
    }


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    return await _backend_post("/api/transfer", payload)


@app.get("/api/investments/instruments")
async def api_investment_instruments() -> dict:
    return await _investment_instruments()


@app.get("/api/investments/portfolio/{client_id}")
async def api_investment_portfolio(client_id: str) -> dict:
    return await _try_get_investment_portfolio(client_id)


@app.get("/api/investments/orders/{client_id}")
async def api_investment_orders(client_id: str) -> dict:
    return await _try_get_investment_orders(client_id)


@app.post("/api/investments/quote")
async def api_investment_quote(payload: dict) -> dict:
    client_id = str(payload.get("client_id") or "").strip()
    ticker = str(payload.get("ticker") or "").strip().upper()
    quantity = int(payload.get("quantity") or 0)
    if not client_id:
        raise HTTPException(status_code=400, detail="клиент не выбран")
    await _backend_get(f"/clients/{client_id}")
    return await _investment_quote(client_id, ticker, quantity)


@app.post("/api/investments/buy")
async def api_investment_buy(payload: dict) -> dict:
    client_id = str(payload.get("client_id") or "").strip()
    ticker = str(payload.get("ticker") or "").strip().upper()
    quantity = int(payload.get("quantity") or 0)
    if not client_id:
        raise HTTPException(status_code=400, detail="клиент не выбран")
    await _backend_get(f"/clients/{client_id}")
    quote = await _investment_quote(client_id, ticker, quantity)
    order = {
        "client_id": client_id,
        "ticker": quote["ticker"],
        "side": "buy",
        "quantity": int(quote["quantity"]),
        "price_rub": quote["price_rub"],
        "amount_rub": quote["amount_rub"],
        "commission_rub": quote["commission_rub"],
        "status": "executed",
        "explanation": quote["explanation"],
    }
    saved = await _try_save_investment_order(order)
    return {
        "status": "executed",
        "explanation": quote["explanation"],
        "quote": quote,
        "saved_order": saved,
        "storage": "saved" if saved else "backend_not_ready",
    }
