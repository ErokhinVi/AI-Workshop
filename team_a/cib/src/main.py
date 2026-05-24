"""Блок cib — корпоратив и бизнес-логика банка команды.

Каталог продуктов и (в рамках задачи) логика кредитного решения.
За данными клиента ходит в backend по BACKEND_URL. Логику решения
(POST /credit/decide) и кредитный продукт добавляет владелец блока.
Хелпер src/llm.py — для человеческого объяснения решения.
"""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

TEAM_NAME = os.environ.get("TEAM_NAME", "team")
COMMIT = os.environ.get("RENDER_GIT_COMMIT", "local")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003").rstrip("/")

DECISION_TIMEOUT_S = 10.0

PRODUCTS = [
    {"id": "card-debit", "kind": "card", "name": "Дебетовая карта", "segment": "mass"},
    {"id": "deposit-base", "kind": "deposit", "name": "Срочный депозит", "rate_pct": 14.0},
    {
        "id": "credit-cash-easy",
        "kind": "credit",
        "name": "Кредит наличными Лёгкий",
        "rate_from_pct": 18.9,
        "amount_min_rub": 50_000,
        "amount_max_rub": 1_000_000,
        "term_months_min": 6,
        "term_months_max": 60,
    },
]

app = FastAPI(title="cib — корпоратив и бизнес-логика", version="1.0.0")


class CreditDecisionRequest(BaseModel):
    client_id: str
    amount_rub: int = Field(ge=50_000, le=1_000_000)
    term_months: int = Field(ge=6, le=60)


async def _backend_get(path: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=DECISION_TIMEOUT_S) as client:
            response = await client.get(f"{BACKEND_URL}{path}", params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"backend недоступен: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text[:300])
    return response.json()


def _normalize_rate(client: dict, amount_rub: int, term_months: int) -> float:
    rate = 18.9
    if client.get("segment") in {"premium", "private"}:
        rate -= 2.0
    elif client.get("segment") == "mass_affluent":
        rate -= 1.0
    if client.get("risk_score", 1) <= 0.2:
        rate -= 1.0
    elif client.get("risk_score", 1) >= 0.45:
        rate += 2.5
    if amount_rub >= 500_000:
        rate += 1.0
    if term_months >= 36:
        rate += 0.5
    return round(max(rate, 13.9), 1)


def _build_decision(client: dict, amount_rub: int, term_months: int) -> dict:
    if client.get("has_overdue_history"):
        return {
            "decision": "declined",
            "approved_amount_rub": 0,
            "rate_pct": None,
            "monthly_payment_rub": None,
            "reason": "Были просрочки по прошлым обязательствам.",
        }

    income_rub = int(client.get("income_rub") or 0)
    if income_rub < 40_000:
        return {
            "decision": "declined",
            "approved_amount_rub": 0,
            "rate_pct": None,
            "monthly_payment_rub": None,
            "reason": "Доход пока слишком низкий для кредита.",
        }

    risk_score = float(client.get("risk_score") or 0)
    approval_limit = min(max(income_rub * 12, 100_000), 1_000_000)
    if risk_score > 0.55:
        approval_limit = min(approval_limit, 150_000)
    elif risk_score > 0.40:
        approval_limit = min(approval_limit, 300_000)

    if amount_rub > approval_limit:
        return {
            "decision": "approved_with_limit",
            "approved_amount_rub": approval_limit,
            "rate_pct": _normalize_rate(client, approval_limit, term_months),
            "monthly_payment_rub": round(approval_limit / max(term_months, 1)),
            "reason": "Одобряем меньшую сумму, чтобы платёж оставался комфортным.",
        }

    return {
        "decision": "approved",
        "approved_amount_rub": amount_rub,
        "rate_pct": _normalize_rate(client, amount_rub, term_months),
        "monthly_payment_rub": round(amount_rub / max(term_months, 1)),
        "reason": "Заявка выглядит комфортной по доходу и риску.",
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "team": TEAM_NAME, "block": "cib",
            "commit": COMMIT, "backend_url": BACKEND_URL, "products": len(PRODUCTS)}


@app.get("/products")
async def products() -> dict:
    return {"total": len(PRODUCTS), "items": PRODUCTS}


@app.get("/credit/clients")
async def credit_clients(limit: int = 12) -> dict:
    clients = await _backend_get("/clients", params={"limit": limit})
    items = [
        {
            "id": c["id"],
            "name": c["name"],
            "income_rub": c.get("income_rub", 0),
            "segment": c.get("segment", "mass"),
        }
        for c in clients.get("items", [])
    ]
    return {"total": len(items), "items": items}


@app.post("/credit/decide")
async def credit_decide(payload: CreditDecisionRequest) -> dict:
    client = await _backend_get(f"/clients/{payload.client_id}")
    decision = _build_decision(client, payload.amount_rub, payload.term_months)
    return {
        "product_id": "credit-cash-easy",
        "client_id": payload.client_id,
        "client_name": client.get("name"),
        "requested_amount_rub": payload.amount_rub,
        "term_months": payload.term_months,
        **decision,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    rows = "".join(
        "<tr>"
        f"<td>{p['id']}</td><td>{p['kind']}</td><td>{p['name']}</td>"
        f"<td>{p.get('rate_from_pct', p.get('rate_pct', '—'))}</td>"
        "</tr>"
        for p in PRODUCTS
    )
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>cib · Райффайзен</title><style>"
        ":root{--bg:#0c0d10;--card:#151821;--line:#2b3040;--text:#e8e9ec;--muted:#9aa3b2;"
        "--accent:#ffe600;--ok:#74d99f;--bad:#ff8c7a}"
        "body{font-family:system-ui;background:linear-gradient(180deg,#0c0d10,#111522);"
        "color:var(--text);padding:24px;margin:0}"
        ".wrap{max-width:980px;margin:0 auto}.grid{display:grid;grid-template-columns:1.2fr .8fr;"
        "gap:20px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;"
        "padding:20px}.eyebrow{color:var(--muted);font-size:12px;text-transform:uppercase;"
        "letter-spacing:.12em}.hero{font-size:34px;line-height:1.05;margin:12px 0 8px}"
        "p{color:var(--muted)}table{width:100%;border-collapse:collapse;margin-top:16px}"
        "td,th{border-top:1px solid var(--line);padding:12px 10px;text-align:left}"
        "th{color:var(--muted);font-weight:500;font-size:13px}label{display:block;font-size:13px;"
        "color:var(--muted);margin:0 0 6px}input,select{width:100%;padding:12px;border-radius:12px;"
        "border:1px solid var(--line);background:#0e1118;color:var(--text);margin-bottom:14px}"
        "button{width:100%;padding:13px 14px;border:0;border-radius:12px;background:var(--accent);"
        "color:#131313;font-weight:700;cursor:pointer}button:hover{filter:brightness(.98)}"
        ".result{margin-top:14px;padding:14px;border-radius:14px;background:#0f1320;border:1px solid var(--line)}"
        ".ok{color:var(--ok)}.bad{color:var(--bad)}"
        "@media (max-width:860px){.grid{grid-template-columns:1fr}}"
        "</style></head><body>"
        "<div class='wrap'><div class='grid'>"
        "<section class='card'>"
        "<div class='eyebrow'>cib команды</div>"
        "<div class='hero'>Кредиты подключены</div>"
        "<p>Здесь живёт простая логика решения по заявке. Она смотрит на доход, риск и просрочки,"
        " а затем даёт одобрение, уменьшенную сумму или отказ.</p>"
        f"<p>Команда: {TEAM_NAME}</p>"
        f"<table><tr><th>id</th><th>вид</th><th>название</th><th>ставка, %</th></tr>{rows}</table>"
        "</section>"
        "<section class='card'>"
        "<div class='eyebrow'>Проверка заявки</div>"
        "<form id='credit-form'>"
        "<label>Клиент</label><select id='client_id' name='client_id'></select>"
        "<label>Сумма, ₽</label><input name='amount_rub' type='number' value='300000' min='50000' max='1000000' step='10000'>"
        "<label>Срок, месяцев</label><input name='term_months' type='number' value='24' min='6' max='60'>"
        "<button type='submit'>Проверить решение</button>"
        "</form><div id='result' class='result'>Выберите клиента и нажмите кнопку.</div>"
        "</section></div></div>"
        "<script>"
        "const fmt=new Intl.NumberFormat('ru-RU');"
        "async function loadClients(){"
        "const r=await fetch('/credit/clients');const d=await r.json();"
        "const sel=document.getElementById('client_id');"
        "sel.innerHTML=(d.items||[]).map(c=>`<option value='${c.id}'>${c.name} · доход ${fmt.format(c.income_rub)} ₽ · ${c.segment}</option>`).join('');"
        "}"
        "document.getElementById('credit-form').addEventListener('submit', async (e)=>{"
        "e.preventDefault();const fd=new FormData(e.target);const payload={client_id:String(fd.get('client_id')||''),"
        "amount_rub:Number(fd.get('amount_rub')||0),term_months:Number(fd.get('term_months')||0)};"
        "const box=document.getElementById('result');box.textContent='Считаю решение...';"
        "try{const r=await fetch('/credit/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});"
        "const d=await r.json();if(!r.ok){box.innerHTML=`<span class='bad'>${d.detail||'Ошибка'}</span>`;return;}"
        "const approved=d.approved_amount_rub?`${fmt.format(d.approved_amount_rub)} ₽`: '0 ₽';"
        "const rate=d.rate_pct?`${d.rate_pct}%`: '—';"
        "const pay=d.monthly_payment_rub?`${fmt.format(d.monthly_payment_rub)} ₽`: '—';"
        "const cls=d.decision==='declined'?'bad':'ok';"
        "box.innerHTML=`<div class='${cls}'><strong>${d.client_name}</strong>: ${d.decision}</div>"
        "<div>Сумма: ${approved}</div><div>Ставка: ${rate}</div><div>Платёж в месяц: ${pay}</div><div>${d.reason}</div>`;"
        "}catch(err){box.innerHTML=\"<span class='bad'>Не удалось получить ответ</span>\";}"
        "});loadClients();"
        "</script>"
        "</body></html>"
    )
