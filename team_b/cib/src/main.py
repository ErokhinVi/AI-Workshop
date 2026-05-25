"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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
