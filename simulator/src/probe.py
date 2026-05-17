"""Probe — снятие состояния трёх блоков команды. Закрытый список проверок.

Фиксированные клиенты из seed/clients.jsonl: сильный c-01394 (premium),
слабый c-01434 (mass, просрочки).
"""
from __future__ import annotations

import json
import time

import httpx

STRONG_APPLICANT = "c-01394"
WEAK_APPLICANT = "c-01434"
PROBE_TIMEOUT_S = 20.0

_APPROVE = ("approv", "одобр", "выдан", "accept", "положительн")
_REJECT = ("reject", "отказ", "decline", "denied", "отрицательн")


def _safe_json(resp: httpx.Response) -> dict:
    try:
        d = resp.json()
        return d if isinstance(d, dict) else {"_list": d}
    except (json.JSONDecodeError, ValueError):
        return {}


def _decision(body: dict) -> str | None:
    """Вердикт из ответа независимо от формы. → approved|rejected|None."""
    if not isinstance(body, dict):
        return None
    for k in ("decision", "status", "verdict", "result", "approved"):
        if k in body:
            v = str(body[k]).lower()
            if v in ("true", "ok") or any(w in v for w in _APPROVE):
                return "approved"
            if v == "false" or any(w in v for w in _REJECT):
                return "rejected"
    blob = json.dumps(body, ensure_ascii=False).lower()
    a, r = any(w in blob for w in _APPROVE), any(w in blob for w in _REJECT)
    if a and not r:
        return "approved"
    if r and not a:
        return "rejected"
    return None


def _explanation(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    for k in ("explanation", "reason", "message", "comment", "text", "detail"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


async def _probe_backend(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        r = await client.get(f"{url}/clients/{STRONG_APPLICANT}")
        c["serves_client"] = r.status_code == 200 and "id" in _safe_json(r)
    except httpx.HTTPError:
        c["serves_client"] = False
    try:
        r = await client.post(
            f"{url}/credit-applications",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000,
                  "term_months": 12, "decision": "approved"},
        )
        c["accepts_application"] = r.status_code in (200, 201)
    except httpx.HTTPError:
        c["accepts_application"] = False
    try:
        r = await client.get(f"{url}/credit-applications")
        c["lists_applications"] = (
            r.status_code == 200 and isinstance(_safe_json(r).get("items"), list)
        )
    except httpx.HTTPError:
        c["lists_applications"] = False
    return snap


async def _probe_cib(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        r = await client.get(f"{url}/products")
        blob = json.dumps(_safe_json(r).get("items", []), ensure_ascii=False).lower()
        c["has_credit_product"] = r.status_code == 200 and (
            "кредит" in blob or "credit" in blob
        )
    except httpx.HTTPError:
        c["has_credit_product"] = False
    t0 = time.time()
    try:
        r = await client.post(
            f"{url}/credit/decide",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000, "term_months": 12},
        )
        c["decide_status"] = r.status_code
        c["decide_latency_ms"] = int((time.time() - t0) * 1000)
        c["decision_strong"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["decide_status"] = 0
        c["decide_latency_ms"] = -1
        c["decision_strong"] = None
    try:
        r = await client.post(
            f"{url}/credit/decide",
            json={"client_id": WEAK_APPLICANT, "amount_rub": 900000, "term_months": 6},
        )
        c["decision_weak"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["decision_weak"] = None
    ds, dw = c.get("decision_strong"), c.get("decision_weak")
    c["decision_is_discriminating"] = ds is not None and dw is not None and ds != dw
    return snap


async def _probe_retail(client: httpx.AsyncClient, url: str) -> dict:
    snap: dict = {"reachable": False, "commit": None, "checks": {}}
    c = snap["checks"]
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        return snap
    try:
        root = await client.get(f"{url}/")
        html = root.text.lower() if root.status_code == 200 else ""
    except httpx.HTTPError:
        html = ""
    c["credit_in_ui"] = "кредит" in html
    c["transfer_in_ui"] = "перевод" in html
    try:
        r = await client.post(
            f"{url}/api/credit-apply",
            json={"client_id": STRONG_APPLICANT, "amount_rub": 300000, "term_months": 12},
        )
        c["credit_apply_status"] = r.status_code
        c["credit_apply_decision"] = _decision(_safe_json(r))
    except httpx.HTTPError:
        c["credit_apply_status"] = 0
        c["credit_apply_decision"] = None
    try:
        r = await client.post(
            f"{url}/api/credit-apply",
            json={"client_id": WEAK_APPLICANT, "amount_rub": 900000, "term_months": 6},
        )
        expl = _explanation(_safe_json(r))
        c["credit_apply_explained"] = bool(expl) and len(expl) > 40
    except httpx.HTTPError:
        c["credit_apply_explained"] = False
    try:
        cl = await client.get(f"{url}/clients?limit=2")
        ids = [x["id"] for x in _safe_json(cl).get("items", []) if "id" in x]
        if len(ids) >= 2:
            rt = await client.post(
                f"{url}/api/transfer",
                json={"from_client_id": ids[0], "to": ids[1], "amount_rub": 1000},
            )
            c["transfer_ok"] = rt.status_code == 200
        else:
            c["transfer_ok"] = False
    except httpx.HTTPError:
        c["transfer_ok"] = False
    return snap


async def probe_team(team: str, urls: dict) -> dict:
    """Снять снапшот трёх блоков команды. urls = {retail, cib, backend}.

    Возвращает {team, blocks: {backend, cib, retail}}, каждый блок —
    {reachable, commit, checks}. Никогда не бросает.
    """
    out: dict = {"team": team, "blocks": {}}
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        out["blocks"]["backend"] = await _probe_backend(
            client, urls["backend"].rstrip("/"))
        out["blocks"]["cib"] = await _probe_cib(client, urls["cib"].rstrip("/"))
        out["blocks"]["retail"] = await _probe_retail(client, urls["retail"].rstrip("/"))
    return out
