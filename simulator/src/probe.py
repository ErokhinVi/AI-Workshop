"""Probe — снятие состояния живого банка. Закрытый список проверок P1–P6.

Фиксированные клиенты выбраны из seed/clients.jsonl:
  STRONG_APPLICANT — премиум, высокий доход, без просрочек;
  WEAK_APPLICANT   — масса, низкий доход, история просрочек.
"""
from __future__ import annotations

import json
import time

import httpx

STRONG_APPLICANT = "c-01394"  # София Лебедева, premium, доход 589 545 ₽
WEAK_APPLICANT = "c-01434"    # Карина Воробьёва, mass, доход 40 358 ₽, просрочки

PROBE_TIMEOUT_S = 20.0

_APPROVE_WORDS = ("approv", "одобр", "выдан", "accept", "положительн")
_REJECT_WORDS = ("reject", "отказ", "decline", "denied", "отрицательн")


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"_list": data}
    except (json.JSONDecodeError, ValueError):
        return {}


def _extract_decision(body: dict) -> str | None:
    """Достать вердикт из ответа банка независимо от формы. → approved|rejected|None."""
    if not isinstance(body, dict):
        return None
    for key in ("decision", "status", "verdict", "result", "approved"):
        if key in body:
            v = str(body[key]).lower()
            if v in ("true", "ok") or any(w in v for w in _APPROVE_WORDS):
                return "approved"
            if v == "false" or any(w in v for w in _REJECT_WORDS):
                return "rejected"
    blob = json.dumps(body, ensure_ascii=False).lower()
    has_app = any(w in blob for w in _APPROVE_WORDS)
    has_rej = any(w in blob for w in _REJECT_WORDS)
    if has_app and not has_rej:
        return "approved"
    if has_rej and not has_app:
        return "rejected"
    return None


def _extract_explanation(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for key in ("explanation", "reason", "message", "comment", "text", "detail"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def probe_bank(bank_url: str) -> dict:
    """Снять снапшот банка. Никогда не бросает: при недоступности reachable=False."""
    bank_url = bank_url.rstrip("/")
    snap: dict = {"bank_url": bank_url, "commit": None, "reachable": False,
                  "checks": {}, "raw": {}}
    checks = snap["checks"]
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            # P1 — /health
            try:
                h = await client.get(f"{bank_url}/health")
                snap["reachable"] = h.status_code == 200
                if h.status_code == 200:
                    snap["commit"] = _safe_json(h).get("commit")
            except httpx.HTTPError:
                return snap  # банк недоступен — дальше нет смысла

            # P2 — / (UI)
            try:
                root = await client.get(f"{bank_url}/")
                html = root.text.lower() if root.status_code == 200 else ""
            except httpx.HTTPError:
                html = ""
            checks["credit_mentioned_in_ui"] = "кредит" in html
            checks["transfer_mentioned_in_ui"] = "перевод" in html

            # P3 — credit-apply, сильный заявитель
            t0 = time.time()
            try:
                r3 = await client.post(
                    f"{bank_url}/api/credit-apply",
                    json={"client_id": STRONG_APPLICANT, "amount_rub": 300000,
                          "term_months": 12},
                )
                checks["credit_apply_status"] = r3.status_code
                checks["credit_apply_latency_ms"] = int((time.time() - t0) * 1000)
                body3 = _safe_json(r3)
                snap["raw"]["strong"] = body3
                checks["decision_strong"] = _extract_decision(body3)
                checks["credit_response_has_decision"] = checks["decision_strong"] is not None
            except httpx.HTTPError:
                checks["credit_apply_status"] = 0
                checks["credit_apply_latency_ms"] = -1
                checks["decision_strong"] = None
                checks["credit_response_has_decision"] = False

            # P4 — credit-apply, слабый заявитель
            try:
                r4 = await client.post(
                    f"{bank_url}/api/credit-apply",
                    json={"client_id": WEAK_APPLICANT, "amount_rub": 900000,
                          "term_months": 6},
                )
                body4 = _safe_json(r4)
                snap["raw"]["weak"] = body4
                checks["decision_weak"] = _extract_decision(body4)
                expl = _extract_explanation(body4)
                checks["credit_response_has_explanation"] = bool(expl) and len(expl) > 40
            except httpx.HTTPError:
                checks["decision_weak"] = None
                checks["credit_response_has_explanation"] = False

            ds, dw = checks.get("decision_strong"), checks.get("decision_weak")
            checks["decision_is_discriminating"] = (
                ds is not None and dw is not None and ds != dw
            )

            # P5 — /credit-applications
            try:
                r5 = await client.get(f"{bank_url}/credit-applications")
                items = _safe_json(r5).get("items")
                checks["credit_applications_listed"] = (
                    r5.status_code == 200 and isinstance(items, list)
                )
            except httpx.HTTPError:
                checks["credit_applications_listed"] = False

            # P6 — регрессия: перевод между двумя клиентами
            try:
                cl = await client.get(f"{bank_url}/clients?limit=2")
                ids = [c["id"] for c in _safe_json(cl).get("items", []) if "id" in c]
                if len(ids) >= 2:
                    rt = await client.post(
                        f"{bank_url}/api/transfer",
                        json={"from_client_id": ids[0], "to": ids[1],
                              "amount_rub": 1000},
                    )
                    checks["transfer_regression_ok"] = rt.status_code == 200
                else:
                    checks["transfer_regression_ok"] = False
            except httpx.HTTPError:
                checks["transfer_regression_ok"] = False
    except httpx.HTTPError:
        pass
    return snap
