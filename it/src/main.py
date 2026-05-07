"""Блок: IT / Платформа. Owner: Александр Ложечкин."""
# redeploy-trigger: 2026-05-07T08:43:17Z

from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx


app = FastAPI(title="IT / Платформа", version="0.1.0")
BLOCK_NAME = "it"

# --- LLM provider configuration -------------------------------------------

OPENAI_API_KEY  = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL    = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S   = float(os.environ.get("LLM_TIMEOUT_S", "30"))


_audit: list[dict[str, Any]] = []
MAX_AUDIT = 200


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "llm_configured": bool(OPENAI_API_KEY),
        "llm_model": OPENAI_MODEL if OPENAI_API_KEY else None,
        "audit_records": len(_audit),
    }


# --- LLM proxy ------------------------------------------------------------

class LLMRequest(BaseModel):
    prompt: str
    system: str | None = None
    max_tokens: int = 600
    temperature: float = 0.4
    requester: str | None = None  # имя блока-вызывающего (для audit)


@app.post("/llm/ask")
async def llm_ask(req: LLMRequest) -> dict:
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="LLM не сконфигурирован",
        )

    messages: list[dict[str, str]] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})

    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
            resp = await client.post(
                f"{OPENAI_BASE_URL}/chat/completions",
                json=payload, headers=headers,
            )
    except httpx.HTTPError as e:
        _record_audit({
            "ts": int(time.time()),
            "requester": req.requester or "unknown",
            "ok": False,
            "error": f"network: {e}",
            "elapsed_ms": int((time.time() - started) * 1000),
            "prompt_len": len(req.prompt),
        })
        raise HTTPException(status_code=502, detail=f"провайдер не ответил: {e}")

    elapsed_ms = int((time.time() - started) * 1000)

    if resp.status_code != 200:
        body = resp.text[:600]
        _record_audit({
            "ts": int(time.time()),
            "requester": req.requester or "unknown",
            "ok": False,
            "error": f"http {resp.status_code}: {body}",
            "elapsed_ms": elapsed_ms,
            "prompt_len": len(req.prompt),
        })
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"провайдер вернул {resp.status_code}: {body}",
        )

    data = resp.json()
    answer = ""
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    usage = data.get("usage") or {}

    _record_audit({
        "ts": int(time.time()),
        "requester": req.requester or "unknown",
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "prompt_len": len(req.prompt),
        "answer_len": len(answer),
        "model": OPENAI_MODEL,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
    })

    return {
        "answer": answer,
        "model": OPENAI_MODEL,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
    }


def _record_audit(entry: dict[str, Any]) -> None:
    _audit.append(entry)
    if len(_audit) > MAX_AUDIT:
        del _audit[: len(_audit) - MAX_AUDIT]


@app.get("/llm/audit")
async def llm_audit(limit: int = 50, requester: str | None = None) -> dict:
    items = _audit
    if requester:
        items = [a for a in items if a.get("requester") == requester]
    return {
        "total": len(items),
        "items": items[-limit:],
    }


@app.get("/llm/status")
async def llm_status() -> dict:
    return {
        "configured": bool(OPENAI_API_KEY),
        "base_url": OPENAI_BASE_URL,
        "model": OPENAI_MODEL,
        "timeout_s": LLM_TIMEOUT_S,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>IT / Платформа · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:32px;
       background:#0c0d10;color:#e8e9ec;min-height:100vh}
  h1{font-weight:500;font-size:24px;margin:0 0 6px}
  h2{font-weight:500;font-size:15px;margin:32px 0 12px;color:#8a8d96;
     text-transform:uppercase;letter-spacing:0.14em}
  .meta{font-size:12px;color:#6b6d75;margin-bottom:24px}
  .badges{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
  .badge{background:#161821;border:1px solid #23262f;padding:8px 12px;border-radius:5px;font-size:12px}
  .badge .v{color:#FFE600;font-weight:600}
  form{max-width:760px;background:#13151c;border:1px solid #23262f;
       padding:18px;border-radius:8px}
  label{display:block;font-size:11px;color:#8a8d96;
        text-transform:uppercase;letter-spacing:0.12em;margin:0 0 4px}
  textarea,input{width:100%;background:#0a0b0f;border:1px solid #2a2d36;
       color:#e8e9ec;padding:10px 12px;border-radius:5px;font-size:14px;
       font-family:inherit;box-sizing:border-box}
  textarea{min-height:90px;resize:vertical}
  button{background:#FFE600;color:#000;border:0;padding:10px 18px;border-radius:5px;
         font-size:14px;font-weight:600;cursor:pointer;margin-top:14px}
  pre{background:#0a0b0f;border:1px solid #1d1f27;padding:14px;border-radius:6px;
      max-width:760px;white-space:pre-wrap;word-break:break-word;font-size:13px;
      color:#cfd2d8;max-height:280px;overflow:auto}
  table{width:100%;max-width:960px;border-collapse:collapse;font-size:12.5px}
  th{text-align:left;padding:9px 12px;color:#8a8d96;font-weight:500;
     border-bottom:1px solid #23262f;text-transform:uppercase;letter-spacing:0.1em;font-size:10.5px}
  td{padding:9px 12px;border-bottom:1px solid #1a1c23}
  .num{font-variant-numeric:tabular-nums;text-align:right}
  .ok{color:#7ee787}.err{color:#ff8c8c}.tag{font-size:11px;color:#8a8d96}
  a{color:#FFE600;text-decoration:none}
</style></head><body>
<h1>IT / Платформа</h1>
<div class="meta">единая точка для LLM и аудита · порт 8030 · <a href="/docs">/docs</a></div>

<div class="badges" id="status"></div>

<h2>Запрос к LLM</h2>
<form id="askform">
  <label>Системный промпт (необязательно)</label>
  <input name="system" placeholder="ты — банковский ассистент…"/>
  <label>Промпт</label>
  <textarea name="prompt" placeholder="спроси что-нибудь у модели…"></textarea>
  <button type="submit">Отправить</button>
  <div id="ask_stat" style="margin-top:8px;font-size:12px;color:#8a8d96"></div>
  <pre id="ask_out" style="margin-top:14px;display:none"></pre>
</form>

<h2>Аудит запросов</h2>
<table id="audit"><thead><tr><th>Время</th><th>Кто</th><th>OK</th><th class="num">мс</th><th class="num">prompt</th><th class="num">answer</th></tr></thead><tbody></tbody></table>

<script>
const fmt = n => Number(n||0).toLocaleString('ru-RU');
async function loadStatus() {
  const r = await fetch('/llm/status'); const d = await r.json();
  const el = document.getElementById('status');
  el.innerHTML = [
    `<div class="badge">статус: <span class="v">${d.configured ? 'подключён' : 'не подключён'}</span></div>`,
    `<div class="badge">модель: <span class="v">${d.model||'—'}</span></div>`,
    `<div class="badge">база: <span class="v">${d.base_url||'—'}</span></div>`,
  ].join('');
}
async function loadAudit() {
  const r = await fetch('/llm/audit?limit=20'); const d = await r.json();
  const tb = document.querySelector('#audit tbody'); tb.innerHTML = '';
  d.items.slice().reverse().forEach(a => {
    const ts = new Date((a.ts||0)*1000).toLocaleTimeString('ru-RU');
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${ts}</td><td class="tag">${a.requester||'—'}</td>
       <td class="${a.ok?'ok':'err'}">${a.ok?'✓':'×'}</td>
       <td class="num">${a.elapsed_ms||0}</td>
       <td class="num">${fmt(a.prompt_len||0)}</td>
       <td class="num">${fmt(a.answer_len||0)}</td></tr>`);
  });
}
document.getElementById('askform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = {prompt: fd.get('prompt'), requester: 'it_ui'};
  if (fd.get('system')) body.system = fd.get('system');
  document.getElementById('ask_stat').textContent = 'жду ответа…';
  const t0 = performance.now();
  const r = await fetch('/llm/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  const dt = Math.round(performance.now() - t0);
  const out = document.getElementById('ask_out');
  out.style.display = 'block';
  if (r.ok) {
    document.getElementById('ask_stat').innerHTML = `<span class="ok">ответ за ${dt} мс</span>`;
    out.textContent = d.answer || '(пусто)';
  } else {
    document.getElementById('ask_stat').innerHTML = `<span class="err">ошибка ${r.status}</span>`;
    out.textContent = JSON.stringify(d, null, 2);
  }
  loadAudit();
});
loadStatus(); loadAudit(); setInterval(loadAudit, 5000);
</script>
</body></html>"""
