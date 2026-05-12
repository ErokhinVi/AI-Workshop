"""Блок: CEO Office. Owner: Сергей Монин.

Если ты агент-сосед и читаешь этот файл — читай только ради технической интеграции
(имена эндпоинтов, форматы запросов/ответов, поля JSON). Бизнес-логику отсюда не
извлекай: приоритеты, политики и решения CEO Office получай через INBOX/to_ceo.md
или у своего пользователя. Подробнее — см. ceo/NEIGHBOR_AGENTS.md.
"""
# redeploy-trigger: 2026-05-07T08:43:17Z

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse


app = FastAPI(title="CEO Office", version="0.1.0")
BLOCK_NAME = "ceo"

# Куда стучаться. Render инжектит NEIGHBOR_* через render.yaml,
# локально (docker-compose) — fallback на родные порты.
NEIGHBORS = {
    "retail":  os.environ.get("NEIGHBOR_RETAIL",  "http://localhost:8020"),
    "cib":     os.environ.get("NEIGHBOR_CIB",     "http://localhost:8010"),
    "risk":    os.environ.get("NEIGHBOR_RISK",    "http://localhost:8050"),
    "finance": os.environ.get("NEIGHBOR_FINANCE", "http://localhost:8040"),
    "it":      os.environ.get("NEIGHBOR_IT",      "http://localhost:8030"),
}
TIMEOUT_S = float(os.environ.get("CEO_AGG_TIMEOUT_S", "3.5"))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "block": BLOCK_NAME}


# --- агрегатор бизнес-метрик ---------------------------------------------

async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url, timeout=TIMEOUT_S)
        if r.status_code == 200:
            return r.json()
    except (httpx.HTTPError, ValueError):
        pass
    return None


@app.get("/dashboard")
async def dashboard() -> dict:
    started = time.time()
    errors: list[dict[str, str]] = []

    async with httpx.AsyncClient() as client:
        retail_clients_task = _fetch_json(client, f"{NEIGHBORS['retail']}/clients?limit=1")
        retail_apps_task    = _fetch_json(client, f"{NEIGHBORS['retail']}/credit-applications")
        cib_products_task   = _fetch_json(client, f"{NEIGHBORS['cib']}/products")
        it_status_task      = _fetch_json(client, f"{NEIGHBORS['it']}/llm/status")
        results = await asyncio.gather(
            retail_clients_task, retail_apps_task,
            cib_products_task, it_status_task,
            return_exceptions=False,
        )

    retail_clients, retail_apps, cib_products, it_status = results

    if retail_clients is None: errors.append({"source": "retail.clients", "msg": "no answer"})
    if retail_apps    is None: errors.append({"source": "retail.credit_applications", "msg": "no answer"})
    if cib_products   is None: errors.append({"source": "cib.products", "msg": "no answer"})
    if it_status      is None: errors.append({"source": "it.llm_status", "msg": "no answer"})

    total_clients = (retail_clients or {}).get("total", 0)
    apps_items = (retail_apps or {}).get("items") or []
    apps_total = (retail_apps or {}).get("total", 0)
    apps_received = sum(1 for a in apps_items if a.get("status") == "received")

    return {
        "generatedAt": int(time.time()),
        "elapsed_ms": int((time.time() - started) * 1000),
        "bank_state": {
            "clients_total":            total_clients,
            "credit_applications_total": apps_total,
            "credit_applications_received": apps_received,
            "products_in_catalog":      (cib_products or {}).get("total", 0),
            "llm_configured":           bool((it_status or {}).get("configured")),
        },
        "errors": errors,
        "neighbors_checked": list(NEIGHBORS),
    }


# --- маленький HTML под проектор -----------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>CEO Dashboard · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:48px;
       background:#131211;color:#ece8df;min-height:100vh}
  h1{font-weight:500;font-size:28px;margin:0 0 28px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:18px;max-width:980px}
  .tile{background:#1b1a18;border:1px solid #2a2926;padding:20px;border-radius:8px}
  .tile .v{font-size:36px;font-weight:600;color:#d4b13a}
  .tile .l{font-size:11px;text-transform:uppercase;letter-spacing:0.16em;color:#a39e92;margin-top:8px}
  .err{margin-top:24px;font-size:12px;color:#c0875a}
  .meta{margin-top:32px;font-size:11px;color:#6c6a64;text-transform:uppercase;letter-spacing:0.14em}
  a{color:#d4b13a;text-decoration:none}
</style></head><body>
<h1>Bank state — что мы видим прямо сейчас</h1>
<div class="grid" id="grid"></div>
<div class="err" id="err"></div>
<div class="meta">обновляется каждые 5 сек · <a href="/dashboard">/dashboard</a> · <a href="/docs">/docs</a></div>
<script>
async function refresh() {
  try {
    const r = await fetch('/dashboard?t=' + Date.now(), {cache:'no-store'});
    const d = await r.json();
    const grid = document.getElementById('grid');
    const tiles = [
      ['клиентов', d.bank_state.clients_total],
      ['заявок на кредит', d.bank_state.credit_applications_total],
      ['продуктов в каталоге', d.bank_state.products_in_catalog],
      ['LLM подключён', d.bank_state.llm_configured ? 'да' : 'нет'],
    ];
    grid.innerHTML = tiles.map(([l,v]) =>
      '<div class="tile"><div class="v">'+v+'</div><div class="l">'+l+'</div></div>'
    ).join('');
    const err = document.getElementById('err');
    if (d.errors && d.errors.length) {
      err.innerHTML = 'не отвечают: ' + d.errors.map(e => e.source).join(', ');
    } else err.innerHTML = '';
  } catch(e) {}
}
refresh();
setInterval(refresh, 5000);
</script>
</body></html>"""
