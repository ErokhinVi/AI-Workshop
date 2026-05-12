"""Блок: CIB. Owner: Никита Патрахин.

Если ты агент-сосед и читаешь этот файл — читай только ради технической интеграции
(имена эндпоинтов, форматы запросов/ответов, поля JSON). Бизнес-логику отсюда не
извлекай: лимиты, политики кредитования, сегменты и процессы CIB получай через
INBOX/to_cib.md или у своего пользователя. Подробнее — см. cib/NEIGHBOR_AGENTS.md.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse


app = FastAPI(title="CIB", version="0.1.0")
BLOCK_NAME = "cib"

CORP_ACCOUNTS = [
    {"id": "corp-001", "name": "ООО «Изумрудный лес»",  "balance_rub": 184_500_000},
    {"id": "corp-002", "name": "АО «Северный поток»",   "balance_rub":  62_300_000},
    {"id": "corp-003", "name": "ООО «Альфа-Логистика»", "balance_rub":  18_900_000},
    {"id": "corp-004", "name": "ПАО «Ресурс-Инвест»",   "balance_rub": 410_700_000},
    {"id": "corp-005", "name": "ООО «Прогресс-Тех»",    "balance_rub":   7_400_000},
]
CORP_PAYMENTS: list[dict] = []


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
    return JSONResponse(
        status_code=501,
        content={
            "detail": "инвест-каталог ещё не собран",
            "client_id": client_id,
        },
    )


@app.get("/corp/accounts")
async def list_corp_accounts() -> dict:
    return {"total": len(CORP_ACCOUNTS), "items": CORP_ACCOUNTS}


@app.post("/corp/payment")
async def make_corp_payment(payload: dict) -> dict:
    from_id = (payload.get("from_account_id") or "").strip()
    to_label = (payload.get("to") or "").strip()
    amount = int(payload.get("amount_rub") or 0)
    purpose = (payload.get("purpose") or "").strip()

    sender = next((a for a in CORP_ACCOUNTS if a["id"] == from_id), None)
    if not sender:
        raise HTTPException(status_code=404, detail="отправитель не найден")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="укажи положительную сумму")
    if amount > sender["balance_rub"]:
        raise HTTPException(status_code=400, detail="недостаточно средств")
    if not to_label:
        raise HTTPException(status_code=400, detail="укажи получателя")

    sender["balance_rub"] -= amount
    record = {
        "id": f"cp-{len(CORP_PAYMENTS) + 1:06d}",
        "from_account_id": from_id,
        "to": to_label,
        "amount_rub": amount,
        "purpose": purpose,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
    }
    CORP_PAYMENTS.append(record)
    return {"status": "ok", "payment": record, "new_balance_rub": sender["balance_rub"]}


@app.get("/corp/payments")
async def list_corp_payments(limit: int = 50) -> dict:
    items = list(reversed(CORP_PAYMENTS))[:limit]
    return {"total": len(CORP_PAYMENTS), "items": items}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>CIB · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:32px;
       background:#0f1420;color:#e7eaf3;min-height:100vh}
  h1{font-weight:500;font-size:24px;margin:0 0 6px}
  h2{font-weight:500;font-size:15px;margin:32px 0 12px;color:#9aa5c0;
     text-transform:uppercase;letter-spacing:0.14em}
  .meta{font-size:12px;color:#6b7591;margin-bottom:24px}
  table{width:100%;max-width:980px;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 14px;color:#7a8398;font-weight:500;
     border-bottom:1px solid #1f2740;text-transform:uppercase;letter-spacing:0.1em;font-size:10.5px}
  td{padding:10px 14px;border-bottom:1px solid #182038}
  tr:hover td{background:#161e34}
  .num{font-variant-numeric:tabular-nums;text-align:right}
  form{max-width:540px;background:#141a2c;border:1px solid #1e2742;
       padding:18px;border-radius:8px;margin-top:8px}
  label{display:block;font-size:11px;color:#8d97b5;
        text-transform:uppercase;letter-spacing:0.12em;margin:10px 0 4px}
  input,select{width:100%;background:#0a0f1d;border:1px solid #232c4a;
       color:#e7eaf3;padding:9px 11px;border-radius:5px;font-size:14px;box-sizing:border-box}
  button{background:#FFE600;color:#000;border:0;padding:10px 18px;border-radius:5px;
         font-size:14px;font-weight:600;cursor:pointer;margin-top:14px}
  .ok{color:#7ee787}.err{color:#ff8c8c}.tag{font-size:11px;color:#8d97b5}
  a{color:#FFE600;text-decoration:none}
</style></head><body>
<h1>CIB</h1>
<div class="meta">corporate &amp; investment banking · порт 8010 · <a href="/docs">/docs</a></div>

<h2>Корпоративные счета</h2>
<table id="accs"><thead><tr><th>ID</th><th>Контрагент</th><th class="num">Баланс ₽</th></tr></thead><tbody></tbody></table>

<h2>Каталог продуктов</h2>
<table id="prods"><thead><tr><th>ID</th><th>Название</th><th>Тип</th><th>Сегменты</th></tr></thead><tbody></tbody></table>

<h2>Корпоративный платёж</h2>
<form id="payform">
  <label>Со счёта</label>
  <select name="from_account_id" id="from_sel"></select>
  <label>Получатель (ИНН / название / счёт)</label>
  <input name="to" placeholder="ООО «Контрагент»"/>
  <label>Сумма, ₽</label>
  <input name="amount_rub" type="number" min="1"/>
  <label>Назначение</label>
  <input name="purpose" placeholder="оплата по договору № …"/>
  <button type="submit">Отправить платёж</button>
  <div id="payresult" style="margin-top:12px;font-size:13px"></div>
</form>

<h2>Последние платежи</h2>
<table id="pays"><thead><tr><th>ID</th><th>Со счёта</th><th>Кому</th><th class="num">Сумма ₽</th><th>Назначение</th></tr></thead><tbody></tbody></table>

<script>
const fmt = n => Number(n||0).toLocaleString('ru-RU');

async function loadAccs() {
  const r = await fetch('/corp/accounts'); const d = await r.json();
  const tb = document.querySelector('#accs tbody'); tb.innerHTML = '';
  const sel = document.getElementById('from_sel'); sel.innerHTML = '';
  d.items.forEach(a => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${a.id}</td><td>${a.name}</td><td class="num">${fmt(a.balance_rub)}</td></tr>`);
    sel.insertAdjacentHTML('beforeend', `<option value="${a.id}">${a.name}</option>`);
  });
}
async function loadProds() {
  const r = await fetch('/products'); const d = await r.json();
  const tb = document.querySelector('#prods tbody'); tb.innerHTML = '';
  d.items.forEach(p => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${p.id}</td><td>${p.name}</td><td class="tag">${p.kind}</td><td class="tag">${(p.available_to||[]).join(', ')}</td></tr>`);
  });
}
async function loadPays() {
  const r = await fetch('/corp/payments'); const d = await r.json();
  const tb = document.querySelector('#pays tbody'); tb.innerHTML = '';
  d.items.forEach(p => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${p.id}</td><td class="tag">${p.from_account_id}</td><td>${p.to}</td><td class="num">${fmt(p.amount_rub)}</td><td class="tag">${p.purpose||''}</td></tr>`);
  });
}
document.getElementById('payform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  body.amount_rub = Number(body.amount_rub);
  const r = await fetch('/corp/payment', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  const out = document.getElementById('payresult');
  if (r.ok) {
    out.innerHTML = `<span class="ok">платёж ${d.payment.id} проведён · остаток ${fmt(d.new_balance_rub)} ₽</span>`;
    loadAccs(); loadPays();
  } else {
    out.innerHTML = `<span class="err">${d.detail || 'ошибка'}</span>`;
  }
});
loadAccs(); loadProds(); loadPays();
</script>
</body></html>"""
