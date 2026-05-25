"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import hashlib
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
    {
        "id": "credit-casino-slot",
        "kind": "credit",
        "name": "Кредит для слот-машины",
        "purpose": "casino_slot",
        "min_amount_rub": 10_000,
        "max_amount_rub": 300_000,
        "min_term_months": 3,
        "max_term_months": 24,
        "max_stake_rub": 10_000,
        "session_limit_pct": 0.25,
        "risk_level": "high",
    },
    {
        "id": "credit-investment-securities",
        "kind": "credit",
        "name": "Кредит для покупки ценных бумаг",
        "purpose": "investment_securities",
        "min_amount_rub": 50_000,
        "max_amount_rub": 1_000_000,
        "min_term_months": 6,
        "max_term_months": 36,
        "allowed_tickers": ["SBRF", "VTBR", "ROSN", "SIBN"],
        "risk_level": "medium",
    },
    {
        "id": "raifcoin-tap",
        "kind": "loyalty_game",
        "asset": "RaifCoin",
        "name": "RaifCoin Tap",
        "description": "Мини-игра с тапами и виртуальной валютой без блокчейна",
        "rules_endpoint": "/raifcoin/rules",
        "score_endpoint": "/raifcoin/tap/score",
        "blockchain": False,
        "external_transfer": False,
        "credit_rating_boost": True,
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
CASINO_ALLOWED_STAKES_RUB = [100, 500, 1000, 5000, 10_000]
CASINO_SYMBOLS = ["raif", "coin", "safe", "star", "seven"]
CASINO_PAYOUT_TABLE = {
    "three_seven": 12.0,
    "three_raif": 8.0,
    "three_same": 5.0,
    "two_same": 1.5,
    "none": 0.0,
}
RAIFCOIN_DAILY_LIMIT = 10_000
RAIFCOIN_MAX_TAP_RATE_PER_SEC = 16.0
RAIFCOIN_SPEED_MULTIPLIERS = [
    {"max_tap_rate_per_sec": 4.0, "multiplier": 1.0},
    {"max_tap_rate_per_sec": 8.0, "multiplier": 1.4},
    {"max_tap_rate_per_sec": 12.0, "multiplier": 1.8},
    {"max_tap_rate_per_sec": RAIFCOIN_MAX_TAP_RATE_PER_SEC, "multiplier": 2.2},
]

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


class CasinoSpinRequest(BaseModel):
    client_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    stake_rub: int = Field(gt=0)


class RaifCoinTapScoreRequest(BaseModel):
    client_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    tap_count: int = Field(ge=0)
    duration_ms: int = Field(gt=0)


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


async def _get_investment_cash_balance(client_id: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{BACKEND_URL}/investment-portfolio/{client_id}")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    if not isinstance(data, dict):
        return None
    try:
        return float(data.get("cash_balance_rub"))
    except (TypeError, ValueError):
        return None


def _monthly_payment(amount_rub: int, term_months: int, rate_pct: float) -> int:
    monthly_rate = rate_pct / 100 / 12
    if monthly_rate <= 0:
        return round(amount_rub / term_months)
    factor = (1 + monthly_rate) ** term_months
    return round(amount_rub * monthly_rate * factor / (factor - 1))


def _choose_rate(client: dict[str, Any]) -> float:
    risk_score = max(0.05, float(client.get("risk_score") or 0.35) - _raifcoin_rating_boost(client))
    segment = str(client.get("segment") or "mass")
    rate = 13.9 + risk_score * 10
    if segment in {"premium", "private"}:
        rate -= 2.0
    elif segment == "sme":
        rate -= 0.8
    if client.get("has_overdue_history"):
        rate += 4.0
    return round(max(11.9, min(rate, 27.5)), 1)


def _raifcoin_rating_boost(client: dict[str, Any]) -> float:
    credit_profile = (
        client.get("credit_profile")
        if isinstance(client.get("credit_profile"), dict)
        else {}
    )
    raw_boost = client.get("raifcoin_rating_boost", credit_profile.get("raifcoin_rating_boost", 0))
    try:
        boost = float(raw_boost or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(boost, 0.08))


def _normalise_purpose(purpose: str | None) -> str | None:
    value = (purpose or "").strip().lower()
    if not value:
        return None
    if value not in {"casino_slot", "investment_securities"}:
        raise HTTPException(
            status_code=400,
            detail="доступные цели кредита: casino_slot, investment_securities",
        )
    return value


def _purpose_terms(
    purpose: str | None,
    requested_amount: int,
    base_status: str,
    base_approved_amount: int,
    payment_to_income: float,
    risk_score: float,
    has_overdue: bool,
) -> dict[str, Any]:
    if purpose == "casino_slot":
        session_limit = min(requested_amount, 75_000)
        max_stake = min(10_000, max(100, session_limit // 5))
        approved_amount = min(base_approved_amount, 300_000)
        if base_status == "approved" and (
            has_overdue or risk_score > 0.35 or payment_to_income > 0.25
        ):
            base_status = "counter_offer"
            approved_amount = min(approved_amount, 100_000)
        if base_status == "counter_offer":
            session_limit = min(session_limit, approved_amount, 50_000)
            max_stake = min(max_stake, 5_000)
        if base_status == "declined":
            approved_amount = 0
            session_limit = 0
            max_stake = 0
        return {
            "purpose": purpose,
            "status": base_status,
            "approved_amount_rub": approved_amount,
            "purpose_details": {
                "label": "игра в слот-машину",
                "max_stake_rub": max_stake,
                "session_limit_rub": session_limit,
                "allowed_stakes_rub": CASINO_ALLOWED_STAKES_RUB,
                "requires_backend_session": True,
                "next_endpoint": "/casino/slot-rules",
            },
        }
    if purpose == "investment_securities":
        approved_amount = min(base_approved_amount, 1_000_000)
        if base_status == "approved" and (risk_score > 0.55 or payment_to_income > 0.4):
            base_status = "counter_offer"
            approved_amount = min(approved_amount, 500_000)
        if base_status == "declined":
            approved_amount = 0
        return {
            "purpose": purpose,
            "status": base_status,
            "approved_amount_rub": approved_amount,
            "purpose_details": {
                "label": "покупка ценных бумаг",
                "allowed_tickers": ["SBRF", "VTBR", "ROSN", "SIBN"],
                "requires_investment_quote": True,
                "next_endpoint": "/investments/quote",
            },
        }
    return {
        "purpose": None,
        "status": base_status,
        "approved_amount_rub": base_approved_amount,
        "purpose_details": None,
    }


def _decide_credit(payload: CreditDecisionRequest, client: dict[str, Any]) -> dict[str, Any]:
    purpose = _normalise_purpose(payload.purpose)
    income = int(client.get("income_rub") or 0)
    balance = int(client.get("balance_rub") or 0)
    raifcoin_rating_boost = _raifcoin_rating_boost(client)
    risk_score = max(0.05, float(client.get("risk_score") or 0.5) - raifcoin_rating_boost)
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

    purpose_terms = _purpose_terms(
        purpose,
        payload.amount_rub,
        status,
        approved_amount,
        payment_to_income,
        risk_score,
        has_overdue,
    )
    status = str(purpose_terms["status"])
    approved_amount = int(purpose_terms["approved_amount_rub"])
    if purpose == "casino_slot":
        details = purpose_terms["purpose_details"]
        if approved_amount:
            explanation += (
                f" Цель кредита — игра в слот-машину. Для защиты клиента CIB ограничивает "
                f"сессию суммой {details['session_limit_rub']:,} ₽ и максимальной ставкой "
                f"{details['max_stake_rub']:,} ₽; backend должен создать отдельную игровую "
                "сессию, а retail не должен принимать ставку без одобренного кредита."
            )
        else:
            explanation += (
                " Цель кредита — игра в слот-машину, поэтому решение особенно строгое: "
                "при текущей долговой нагрузке клиенту нельзя увеличивать риск потери денег."
            )
    elif purpose == "investment_securities":
        if approved_amount:
            explanation += (
                " Цель кредита — покупка ценных бумаг. После одобрения клиент должен увидеть "
                "расчёт по SBRF, VTBR, ROSN или SIBN через инвестиционный контракт CIB и "
                "сохранить сделку в backend."
            )
        else:
            explanation += (
                " Цель кредита — покупка ценных бумаг, но при текущих параметрах безопаснее "
                "не увеличивать кредитную и рыночную нагрузку одновременно."
            )
    if raifcoin_rating_boost > 0:
        explanation += (
            f" В профиле учтён RaifCoin-буст кредитного рейтинга "
            f"{raifcoin_rating_boost:.3f}: он снижает оценку риска только в пределах "
            "внутреннего лимита и не заменяет проверку дохода, долга и просрочек."
        )

    monthly_payment_for_return = (
        _monthly_payment(approved_amount, payload.term_months, rate_pct) if approved_amount else 0
    )
    return {
        "application_id": f"cr-{payload.client_id}-{payload.amount_rub}-{payload.term_months}",
        "client_id": payload.client_id,
        "purpose": purpose,
        "status": status,
        "decision": status,
        "amount_rub": payload.amount_rub,
        "approved_amount_rub": approved_amount,
        "term_months": payload.term_months,
        "rate_pct": rate_pct,
        "monthly_payment_rub": monthly_payment_for_return,
        "payment_to_income_pct": round(payment_to_income * 100, 1),
        "amount_to_income_pct": amount_to_income_pct,
        "purpose_details": purpose_terms["purpose_details"],
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
            "raifcoin_rating_boost": raifcoin_rating_boost,
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
            "trade_rules": _investment_trade_rules(instrument),
        }
        for instrument in INVESTMENT_INSTRUMENTS
    ]


def _investment_instrument_payload(instrument: dict[str, Any]) -> dict[str, Any]:
    return {**instrument, "trade_rules": _investment_trade_rules(instrument)}


def _investment_trade_rules(instrument: dict[str, Any]) -> dict[str, Any]:
    lot_size = int(instrument["lot_size"])
    return {
        "allowed_sides": ["buy"],
        "quantity_unit": "share",
        "min_quantity": 1,
        "quantity_step": 1,
        "lot_size": lot_size,
        "supports_fractional": False,
        "commission_rate": INVESTMENT_COMMISSION_RATE,
        "settlement_currency": instrument["currency"],
    }


def _money(value: int | float) -> str:
    numeric = float(value)
    precision = 3 if 0 < abs(numeric) < 1 else 2
    rounded = round(numeric, precision)
    if rounded.is_integer():
        return f"{int(rounded):,}".replace(",", " ")
    whole, fraction = f"{rounded:,.{precision}f}".split(".")
    fraction = fraction.rstrip("0")
    return f"{whole.replace(',', ' ')}.{fraction}"


def _quote_investment(
    payload: InvestmentQuoteRequest,
    client: dict[str, Any],
    investment_cash_balance: float | None,
) -> dict[str, Any]:
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
    balance = (
        investment_cash_balance
        if investment_cash_balance is not None
        else float(client.get("balance_rub") or 0)
    )
    balance_source = "investment_portfolio" if investment_cash_balance is not None else "client_balance"
    client_name = str(client.get("name") or payload.client_id)
    enough_cash = balance >= total_rub
    explanation = (
        f"Покупка {quantity} шт. {ticker} ({instrument['name']}) рассчитана по цене "
        f"{_money(price_rub)} ₽ за бумагу. Сумма сделки {_money(amount_rub)} ₽, комиссия "
        f"{_money(commission_rub)} ₽, всего к списанию {_money(total_rub)} ₽. "
    )
    if enough_cash:
        explanation += (
            f"У клиента {client_name} достаточно свободных средств для инвестиций: "
            f"доступно {_money(balance)} ₽. "
            "После подтверждения retail может сохранить сделку в backend и показать её в портфеле."
        )
    else:
        explanation += (
            f"У клиента {client_name} сейчас свободно для инвестиций {_money(balance)} ₽, "
            "этого меньше суммы покупки. "
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
        "instrument": _investment_instrument_payload(instrument),
        "trade_rules": _investment_trade_rules(instrument),
        "client_cash_balance_rub": balance,
        "cash_balance_source": balance_source,
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


def _casino_slot_rules() -> dict[str, Any]:
    return {
        "game": "slot_machine",
        "currency": "RUB",
        "allowed_stakes_rub": CASINO_ALLOWED_STAKES_RUB,
        "min_stake_rub": min(CASINO_ALLOWED_STAKES_RUB),
        "max_stake_rub": max(CASINO_ALLOWED_STAKES_RUB),
        "symbols": CASINO_SYMBOLS,
        "reels": 3,
        "payout_table": CASINO_PAYOUT_TABLE,
        "requires_approved_credit_purpose": "casino_slot",
        "backend_must_track_session_limit": True,
    }


def _slot_symbols(client_id: str, session_id: str, stake_rub: int) -> list[str]:
    seed = f"{client_id}:{session_id}:{stake_rub}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    return [CASINO_SYMBOLS[digest[i] % len(CASINO_SYMBOLS)] for i in range(3)]


def _slot_payout_multiplier(symbols: list[str]) -> tuple[str, float]:
    if symbols == ["seven", "seven", "seven"]:
        return "three_seven", CASINO_PAYOUT_TABLE["three_seven"]
    if symbols == ["raif", "raif", "raif"]:
        return "three_raif", CASINO_PAYOUT_TABLE["three_raif"]
    if symbols[0] == symbols[1] == symbols[2]:
        return "three_same", CASINO_PAYOUT_TABLE["three_same"]
    if len(set(symbols)) == 2:
        return "two_same", CASINO_PAYOUT_TABLE["two_same"]
    return "none", CASINO_PAYOUT_TABLE["none"]


def _resolve_casino_spin(payload: CasinoSpinRequest, client: dict[str, Any]) -> dict[str, Any]:
    if payload.stake_rub not in CASINO_ALLOWED_STAKES_RUB:
        allowed = ", ".join(str(stake) for stake in CASINO_ALLOWED_STAKES_RUB)
        raise HTTPException(status_code=400, detail=f"доступные ставки: {allowed} ₽")

    symbols = _slot_symbols(payload.client_id, payload.session_id, payload.stake_rub)
    payout_code, multiplier = _slot_payout_multiplier(symbols)
    win_rub = round(payload.stake_rub * multiplier, 2)
    net_result_rub = round(win_rub - payload.stake_rub, 2)
    outcome = "win" if win_rub > payload.stake_rub else "loss"
    if win_rub == payload.stake_rub:
        outcome = "break_even"
    client_name = str(client.get("name") or payload.client_id)
    explanation = (
        f"Ставка {payload.stake_rub:,} ₽ для клиента {client_name}: выпали символы "
        f"{', '.join(symbols)}. Таблица выплат дала множитель {multiplier:g}, "
        f"выигрыш {win_rub:g} ₽, чистый результат {net_result_rub:g} ₽. "
        "Backend должен сохранить ставку и обновить остаток лимита игровой сессии."
    )
    return {
        "status": "resolved",
        "client_id": payload.client_id,
        "session_id": payload.session_id,
        "stake_rub": payload.stake_rub,
        "symbols": symbols,
        "payout_code": payout_code,
        "payout_multiplier": multiplier,
        "win_rub": win_rub,
        "net_result_rub": net_result_rub,
        "outcome": outcome,
        "currency": "RUB",
        "session_status": "spin_resolved",
        "remaining_limit_delta_rub": net_result_rub,
        "explanation": explanation,
        "reason": explanation,
        "rules": _casino_slot_rules(),
    }


def _raifcoin_rules() -> dict[str, Any]:
    return {
        "asset": "RaifCoin",
        "kind": "virtual_crypto",
        "blockchain": False,
        "external_transfer": False,
        "unit": "RC",
        "daily_limit": RAIFCOIN_DAILY_LIMIT,
        "max_tap_rate_per_sec": RAIFCOIN_MAX_TAP_RATE_PER_SEC,
        "speed_multipliers": RAIFCOIN_SPEED_MULTIPLIERS,
        "rating_boost": {
            "max_delta_per_session": 0.05,
            "max_total_boost": 0.08,
            "formula": "min(0.05, raifcoin_earned / 100000)",
        },
        "backend_contract": {
            "session_endpoint": "/raifcoin/sessions",
            "tap_endpoint": "/raifcoin/taps",
            "balance_endpoint": "/raifcoin/balance/{client_id}",
            "profile_field": "raifcoin_rating_boost",
            "required_tap_fields": [
                "client_id",
                "session_id",
                "tap_count",
                "duration_ms",
                "tap_rate_per_sec",
                "raifcoin_earned",
                "rating_delta",
                "fraud_flag",
            ],
            "credit_profile_update": "accumulate rating_delta up to max_total_boost",
            "fraud_policy": "do not credit RaifCoin when fraud_flag is true",
        },
        "antifraud": {
            "min_duration_ms": 1000,
            "max_tap_count": 1000,
            "max_tap_rate_per_sec": RAIFCOIN_MAX_TAP_RATE_PER_SEC,
            "fraud_reward": 0,
        },
        "explanation": (
            "RaifCoin — виртуальная криптовалюта банка без блокчейна. Клиент получает "
            "монеты за нормальную tap-активность; нереалистичная скорость считается "
            "антифродом и не повышает кредитный рейтинг."
        ),
    }


def _raifcoin_multiplier(tap_rate_per_sec: float) -> float:
    for rule in RAIFCOIN_SPEED_MULTIPLIERS:
        if tap_rate_per_sec <= rule["max_tap_rate_per_sec"]:
            return float(rule["multiplier"])
    return 0.0


def _score_raifcoin_taps(
    payload: RaifCoinTapScoreRequest,
    client: dict[str, Any],
) -> dict[str, Any]:
    duration_sec = payload.duration_ms / 1000
    tap_rate = round(payload.tap_count / duration_sec, 2)
    fraud_reasons: list[str] = []
    if payload.duration_ms < 1000:
        fraud_reasons.append("слишком короткая сессия")
    if payload.tap_count > 1000:
        fraud_reasons.append("слишком много тапов за одну сессию")
    if tap_rate > RAIFCOIN_MAX_TAP_RATE_PER_SEC:
        fraud_reasons.append("нереалистичная скорость тапов")
    fraud_flag = bool(fraud_reasons)
    multiplier = 0.0 if fraud_flag else _raifcoin_multiplier(tap_rate)
    raifcoin_earned = 0 if fraud_flag else min(
        RAIFCOIN_DAILY_LIMIT,
        int(round(payload.tap_count * multiplier)),
    )
    rating_delta = 0.0 if fraud_flag else round(min(0.05, raifcoin_earned / 100_000), 4)
    client_name = str(client.get("name") or payload.client_id)
    if fraud_flag:
        explanation = (
            f"Сессия RaifCoin для клиента {client_name} не засчитана: "
            f"{'; '.join(fraud_reasons)}. RaifCoin не начислен, кредитный рейтинг не повышен."
        )
    else:
        explanation = (
            f"Клиент {client_name} сделал {payload.tap_count} тапов за "
            f"{duration_sec:.1f} сек., скорость {tap_rate} тап/сек. Множитель "
            f"{multiplier:g}, начислено {raifcoin_earned} RC. Внутренний кредитный "
            f"рейтинг можно повысить на {rating_delta:.4f}; backend должен сохранить "
            "баланс и передать накопленный raifcoin_rating_boost в профиль клиента."
        )
    return {
        "status": "scored",
        "client_id": payload.client_id,
        "session_id": payload.session_id,
        "tap_count": payload.tap_count,
        "duration_ms": payload.duration_ms,
        "tap_rate_per_sec": tap_rate,
        "multiplier": multiplier,
        "raifcoin_earned": raifcoin_earned,
        "currency": "RC",
        "rating_delta": rating_delta,
        "fraud_flag": fraud_flag,
        "fraud_reasons": fraud_reasons,
        "blockchain": False,
        "external_transfer": False,
        "backend_contract": _raifcoin_rules()["backend_contract"],
        "explanation": explanation,
        "reason": explanation,
        "rules": _raifcoin_rules(),
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
    items = [_investment_instrument_payload(instrument) for instrument in INVESTMENT_INSTRUMENTS]
    return {"total": len(items), "items": items}


@app.post("/investments/quote")
async def investment_quote(payload: InvestmentQuoteRequest) -> dict:
    client = await _get_client(payload.client_id)
    investment_cash_balance = await _get_investment_cash_balance(payload.client_id)
    return _quote_investment(payload, client, investment_cash_balance)


@app.get("/casino/slot-rules")
async def casino_slot_rules() -> dict:
    return _casino_slot_rules()


@app.post("/casino/spin/resolve")
async def casino_spin_resolve(payload: CasinoSpinRequest) -> dict:
    client = await _get_client(payload.client_id)
    return _resolve_casino_spin(payload, client)


@app.get("/raifcoin/rules")
async def raifcoin_rules() -> dict:
    return _raifcoin_rules()


@app.post("/raifcoin/tap/score")
async def raifcoin_tap_score(payload: RaifCoinTapScoreRequest) -> dict:
    client = await _get_client(payload.client_id)
    return _score_raifcoin_taps(payload, client)


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
