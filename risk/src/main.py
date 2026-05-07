"""Блок: Управление рисками. Owner: Роланд Васс."""
# redeploy-trigger: 2026-05-07T08:43:17Z

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse


BLOCK_NAME = "risk"

_clients_by_id: dict[str, dict[str, Any]] = {}
_credit_history: list[dict[str, Any]] = []
_credit_history_by_client: dict[str, list[dict[str, Any]]] = {}

def _find_seed_dir() -> Path | None:
    """Ищем cases/_seed/ — работает и в Docker (/app/cases/_seed), и локально."""
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent / "cases" / "_seed",         # Docker: /app/cases/_seed
        here.parents[3] / "cases" / "_seed" if len(here.parents) >= 4 else None,
        here.parents[2] / "cases" / "_seed" if len(here.parents) >= 3 else None,
    ):
        if candidate and candidate.exists():
            return candidate
    return None


SEED_DIR = _find_seed_dir()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    if SEED_DIR is None:
        yield
        return
    if not _clients_by_id:
        for c in _load_jsonl(SEED_DIR / "clients.jsonl"):
            _clients_by_id[c["id"]] = c
    if not _credit_history:
        rows = _load_jsonl(SEED_DIR / "credit_history.jsonl")
        if rows:
            _credit_history.extend(rows)
            for r in rows:
                _credit_history_by_client.setdefault(r["client_id"], []).append(r)
    yield


app = FastAPI(title="Управление рисками", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "clients_loaded": len(_clients_by_id),
        "credit_records_loaded": len(_credit_history),
    }


@app.post("/_seed/load")
async def seed_load(payload: dict) -> dict:
    if "clients" in payload:
        global _clients_by_id
        _clients_by_id = {c["id"]: c for c in payload["clients"]}
    if "credit_history" in payload:
        global _credit_history, _credit_history_by_client
        _credit_history = list(payload["credit_history"])
        idx: dict[str, list[dict[str, Any]]] = {}
        for r in _credit_history:
            idx.setdefault(r["client_id"], []).append(r)
        _credit_history_by_client = idx
    return {
        "status": "ok",
        "clients": len(_clients_by_id),
        "credit_records": len(_credit_history),
    }


@app.get("/score/{client_id}")
async def get_score(client_id: str) -> dict:
    c = _clients_by_id.get(client_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    history = _credit_history_by_client.get(client_id, [])
    has_overdue = any(r.get("overdue_days_max", 0) >= 30 for r in history)
    return {
        "client_id": client_id,
        "risk_score": c.get("risk_score"),
        "segment": c.get("segment"),
        "has_overdue_history": c.get("has_overdue_history", False),
        "had_severe_overdue": has_overdue,
        "credit_records_count": len(history),
        "method": "seed_lookup",
    }


@app.get("/credit-history/{client_id}")
async def get_credit_history(client_id: str) -> dict:
    if client_id not in _clients_by_id:
        raise HTTPException(status_code=404, detail=f"client {client_id} not found")
    return {
        "client_id": client_id,
        "items": _credit_history_by_client.get(client_id, []),
    }


@app.get("/scores")
async def list_scores(
    segment: str | None = None,
    limit: int = 100,
) -> dict:
    items = list(_clients_by_id.values())
    if segment:
        items = [c for c in items if c.get("segment") == segment]
    items = sorted(items, key=lambda c: float(c.get("risk_score") or 0), reverse=True)[:limit]
    out = []
    for c in items:
        history = _credit_history_by_client.get(c["id"], [])
        out.append({
            "id": c["id"],
            "name": c.get("name"),
            "segment": c.get("segment"),
            "risk_score": c.get("risk_score"),
            "has_overdue_history": c.get("has_overdue_history", False),
            "credit_records_count": len(history),
        })
    return {"total": len(_clients_by_id), "items": out}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>Управление рисками · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:32px;
       background:#170f10;color:#ece2e2;min-height:100vh}
  h1{font-weight:500;font-size:24px;margin:0 0 6px}
  h2{font-weight:500;font-size:15px;margin:32px 0 12px;color:#a89a9a;
     text-transform:uppercase;letter-spacing:0.14em}
  .meta{font-size:12px;color:#7a6a6a;margin-bottom:24px}
  .filters{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
  .filters button{background:#231818;border:1px solid #2e2222;color:#ece2e2;
     padding:6px 12px;border-radius:5px;font-size:12px;cursor:pointer}
  .filters button.active{background:#FFE600;color:#000;border-color:#FFE600}
  table{width:100%;max-width:980px;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 14px;color:#a89a9a;font-weight:500;
     border-bottom:1px solid #2e2222;text-transform:uppercase;letter-spacing:0.1em;font-size:10.5px}
  td{padding:10px 14px;border-bottom:1px solid #221718}
  tr:hover td{background:#1d1313}
  .num{font-variant-numeric:tabular-nums;text-align:right}
  .score{display:inline-block;padding:3px 8px;border-radius:4px;font-size:11.5px;
         font-variant-numeric:tabular-nums;font-weight:600}
  .score-low{background:#1f3622;color:#7ee787}
  .score-mid{background:#3a3320;color:#f5d76e}
  .score-hi{background:#3a1f1f;color:#ff8c8c}
  .tag{font-size:11px;color:#a89a9a}
  .warn{color:#ff8c8c;font-size:11px}
  a{color:#FFE600;text-decoration:none}
</style></head><body>
<h1>Управление рисками</h1>
<div class="meta">риск-скоры и кредитная история · порт 8050 · <a href="/docs">/docs</a></div>

<h2>Скоры по клиентам</h2>
<div class="filters" id="filters">
  <button data-seg="" class="active">все</button>
  <button data-seg="mass">mass</button>
  <button data-seg="mass_affluent">mass_affluent</button>
  <button data-seg="premium">premium</button>
  <button data-seg="private">private</button>
  <button data-seg="sme">sme</button>
</div>
<table id="scores"><thead><tr>
  <th>ID</th><th>Имя</th><th>Сегмент</th><th class="num">Скор</th>
  <th class="num">Кред. записи</th><th>Просрочки</th></tr></thead><tbody></tbody></table>

<script>
let curSeg = '';
function band(s) {
  if (s == null) return '';
  if (s >= 0.66) return 'score-hi';
  if (s >= 0.33) return 'score-mid';
  return 'score-low';
}
async function load() {
  const url = '/scores?limit=50' + (curSeg ? '&segment='+curSeg : '');
  const r = await fetch(url); const d = await r.json();
  const tb = document.querySelector('#scores tbody'); tb.innerHTML = '';
  d.items.forEach(c => {
    const s = c.risk_score == null ? '—' : (c.risk_score).toFixed(2);
    const cls = band(c.risk_score);
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${c.id}</td><td>${c.name||'—'}</td>
       <td class="tag">${c.segment||'—'}</td>
       <td class="num"><span class="score ${cls}">${s}</span></td>
       <td class="num tag">${c.credit_records_count}</td>
       <td>${c.has_overdue_history ? '<span class="warn">есть</span>' : '<span class="tag">—</span>'}</td></tr>`);
  });
}
document.querySelectorAll('#filters button').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('#filters button').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    curSeg = b.dataset.seg;
    load();
  });
});
load();
</script>
</body></html>"""
