"""Блок retail — клиентский мобильный банк команды.

UI плюс тонкий слой: за данными ходит в backend, за кредитным решением — в cib.
Своих данных не держит. Вкладку «Кредиты» и /api/credit-apply (оркестрацию
cib + backend) добавляет владелец блока в рамках задачи.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")
CIB_URL = os.environ.get("CIB_URL", "http://localhost:8002").rstrip("/")

app = FastAPI(title="retail — мобильный банк", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "retail",
            "commit": COMMIT, "backend_url": BACKEND_URL, "cib_url": CIB_URL}


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


@app.get("/clients")
async def list_clients(request: Request) -> dict:
    return await _backend_get("/clients", dict(request.query_params))


@app.get("/transactions/{client_id}")
async def transactions(client_id: str, request: Request) -> dict:
    return await _backend_get(f"/transactions/{client_id}", dict(request.query_params))


@app.post("/api/transfer")
async def api_transfer(payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{BACKEND_URL}/api/transfer", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:300])
    return r.json()


def _pending_credit(amount_rub: int, term_months: int) -> dict:
    """Ответ вкладки «Кредиты», когда блок cib ещё не отдаёт решение."""
    return {
        "decision": "pending",
        "amount_rub": amount_rub,
        "term_months": term_months,
        "reason": "Эта часть банка ещё готовится — решение по заявке появится совсем скоро.",
    }


@app.post("/api/credit-apply")
async def api_credit_apply(payload: dict) -> dict:
    """Заявка на кредит: спросить решение у cib и сохранить заявку в backend."""
    client_id = (payload.get("client_id") or "").strip()
    amount_rub = int(payload.get("amount_rub") or 0)
    term_months = int(payload.get("term_months") or 0)
    if not client_id or amount_rub <= 0 or term_months <= 0:
        raise HTTPException(status_code=400, detail="укажи клиента, сумму и срок")

    decide_request = {
        "client_id": client_id,
        "amount_rub": amount_rub,
        "term_months": term_months,
    }

    # Решение по кредиту принимает блок cib. Пока его нет — вкладка ждёт, не падает.
    decision_data: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r = await client.post(f"{CIB_URL}/credit/decide", json=decide_request)
        if r.status_code == 200:
            decision_data = r.json()
    except httpx.HTTPError:
        decision_data = None

    if not isinstance(decision_data, dict):
        return _pending_credit(amount_rub, term_months)
    decision = decision_data.get("decision")
    reason = decision_data.get("reason") or ""
    if decision not in ("approve", "decline"):
        return _pending_credit(amount_rub, term_months)

    # Сохранить заявку в backend — best-effort: решение клиент уже получил.
    application = {
        "client_id": client_id,
        "amount_rub": amount_rub,
        "term_months": term_months,
        "decision": decision,
        "reason": reason,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{BACKEND_URL}/credit-applications", json=application)
    except httpx.HTTPError:
        pass

    return {
        "decision": decision,
        "reason": reason,
        "amount_rub": amount_rub,
        "term_months": term_months,
    }
