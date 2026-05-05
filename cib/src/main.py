"""
Блок: CIB
Owner: Никита Патрахин

Стартовая точка для воркшопа правления. На дне старта здесь:
 - стартовый каталог банковских продуктов (`/products`),
 - заглушка для invest-рекомендаций (полный pipeline — задача case_02_invest).

Что блок будет делать дальше (Capital Markets, инвест-кабинет, корпы)
— решает Никита вместе со своим AI-помощником.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse


app = FastAPI(title="CIB", version="0.1.0")
BLOCK_NAME = "cib"


# Стартовый каталог. Только базовые продукты — никаких инвест-инструментов
# на старте. Расширение каталога инвест-продуктами — задача case_02_invest.
CATALOG = [
    {
        "id": "deb-classic", "kind": "card",
        "name": "Дебетовая карта Classic",
        "min_balance_rub": 0, "monthly_fee_rub": 0,
        "available_to": ["mass", "mass_affluent", "premium", "private"],
    },
    {
        "id": "deb-premium", "kind": "card",
        "name": "Дебетовая карта Premium",
        "min_balance_rub": 1_500_000, "monthly_fee_rub": 2900,
        "available_to": ["premium", "private"],
    },
    {
        "id": "savings-flex", "kind": "savings",
        "name": "Накопительный счёт Flex",
        "rate_pct": 8.5, "min_balance_rub": 0,
        "available_to": ["mass", "mass_affluent", "premium", "private"],
    },
    {
        "id": "deposit-12m", "kind": "deposit",
        "name": "Вклад на 12 месяцев",
        "rate_pct": 11.5, "min_amount_rub": 50_000, "term_months": 12,
        "available_to": ["mass_affluent", "premium", "private"],
    },
    {
        "id": "credit-consumer", "kind": "credit",
        "name": "Потребительский кредит",
        "rate_range_pct": [11.5, 24.0],
        "max_term_months": 60,
        "available_to": ["mass", "mass_affluent", "premium"],
    },
]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "block": BLOCK_NAME, "products": len(CATALOG)}


@app.get("/products")
async def list_products(kind: str | None = None, segment: str | None = None) -> dict:
    out = CATALOG
    if kind:
        out = [p for p in out if p["kind"] == kind]
    if segment:
        out = [p for p in out if segment in p.get("available_to", [])]
    return {"total": len(out), "items": out}


@app.get("/products/{product_id}")
async def get_product(product_id: str) -> dict:
    for p in CATALOG:
        if p["id"] == product_id:
            return p
    raise HTTPException(status_code=404, detail=f"product {product_id} not found")


@app.get("/invest/recommend")
async def invest_recommend(client_id: str | None = None) -> JSONResponse:
    """Заглушка для case_02_invest — стартовый каталог не содержит
    инвест-инструментов, рекомендация невозможна. Расширить каталог
    и реализовать рекомендации — задача воркшопа."""
    return JSONResponse(
        status_code=501,
        content={
            "detail": "инвест-каталог ещё не собран",
            "hint": "задача кейса case_02_invest — добавить минимум 5 "
                    "инвест-инструментов и собрать ручку рекомендаций",
            "client_id": client_id,
        },
    )
