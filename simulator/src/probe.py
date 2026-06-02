"""Probe — снятие generic-снимка трёх блоков команды. Без кредит-хардкода.

Снимок собирает то, по чему судья оценивает ЛЮБУЮ добавленную фичу:

* `reachable`, `commit` — из `/health` блока;
* `contract` — текст `CONTRACT.md` блока с `raw.githubusercontent.com` на текущем
  коммите (что команда заявила/добавила); при недоступности — "";
* retail дополнительно `html` — тело `GET /` (что видит клиент, усечено).

Плюс детерминированный якорь регрессии базовых функций (без LLM): переводы
(`transfer_ok`) и отдача данных клиента (`serves_client`). `assess_regression`
вынесена в чистую функцию — она читает только готовый снимок и тестируема без
сети.

`SAMPLE_CLIENT` — любой существующий из seed/clients.jsonl клиент: используется
лишь для проверки, что `GET /clients/{id}` по-прежнему отдаёт данные.
"""
from __future__ import annotations

import json

import httpx

SAMPLE_CLIENT = "c-01394"   # любой существующий клиент — для проверки serves_client
PROBE_TIMEOUT_S = 20.0
HTML_LIMIT = 8000
CONTRACT_LIMIT = 6000


def _safe_json(resp: httpx.Response) -> dict:
    try:
        d = resp.json()
        return d if isinstance(d, dict) else {"_list": d}
    except (json.JSONDecodeError, ValueError):
        return {}


async def _fetch_contract(client: httpx.AsyncClient, repo: str | None,
                          commit: str | None, block: str) -> str:
    """Текст CONTRACT.md блока на текущем коммите. Любая ошибка → "".

    Репозитории команд публичные — читаем через raw.githubusercontent.com без
    токена. Если repo/commit не заданы или raw недоступен — деградируем мягко.
    """
    if not repo or not commit:
        return ""
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{block}/CONTRACT.md"
    try:
        r = await client.get(url)
    except httpx.HTTPError:
        return ""
    return r.text[:CONTRACT_LIMIT] if r.status_code == 200 else ""


async def _probe_health(client: httpx.AsyncClient, url: str) -> dict:
    """Базовый снимок блока: reachable + commit из /health."""
    snap: dict = {"reachable": False, "commit": None, "contract": "", "checks": {}}
    try:
        h = await client.get(f"{url}/health")
        snap["reachable"] = h.status_code == 200
        if h.status_code == 200:
            snap["commit"] = _safe_json(h).get("commit")
    except httpx.HTTPError:
        pass
    return snap


async def _probe_backend(client: httpx.AsyncClient, url: str) -> dict:
    """Backend: /health + регрессионная проверка отдачи данных клиента."""
    snap = await _probe_health(client, url)
    if not snap["reachable"]:
        return snap
    try:
        r = await client.get(f"{url}/clients/{SAMPLE_CLIENT}")
        snap["checks"]["serves_client"] = (
            r.status_code == 200 and "id" in _safe_json(r))
    except httpx.HTTPError:
        snap["checks"]["serves_client"] = False
    return snap


async def _probe_cib(client: httpx.AsyncClient, url: str) -> dict:
    """CIB: /health (контракт читается отдельно, кредит-проверок больше нет)."""
    return await _probe_health(client, url)


async def _probe_retail(client: httpx.AsyncClient, url: str) -> dict:
    """Retail: /health + html главной + регрессионная проверка переводов."""
    snap = await _probe_health(client, url)
    if not snap["reachable"]:
        return snap
    c = snap["checks"]
    try:
        root = await client.get(f"{url}/")
        snap["html"] = root.text[:HTML_LIMIT] if root.status_code == 200 else ""
    except httpx.HTTPError:
        snap["html"] = ""
    # Базовая функция: перевод между двумя реальными клиентами по-прежнему идёт.
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


def assess_regression(snap: dict) -> dict:
    """Что в банке СЛОМАНО на этом коммите — детерминированно из снимка, без LLM.

    Якорь честности: даже при красивом CONTRACT.md сломанная база бьёт по
    ценности. Считает только регрессию БАЗОВЫХ функций (переводы, отдача данных
    клиента) и недоступность блоков; недоделанная новая фича (404) не штрафуется.

    Возвращает ``{"unreachable_blocks", "transfers_broken", "serves_client_broken",
    "labels"}``: первое — количество для `scoring.outage_cost`, `labels` —
    человеческие ярлыки для обоснования. Чистая функция: читает готовый снимок.
    """
    blocks = snap.get("blocks", {})
    unreachable = [name for name in ("backend", "cib", "retail")
                   if not blocks.get(name, {}).get("reachable", False)]
    retail_checks = blocks.get("retail", {}).get("checks", {})
    backend = blocks.get("backend", {})
    backend_checks = backend.get("checks", {})

    transfers_broken = retail_checks.get("transfer_ok") is False
    serves_broken = bool(backend.get("reachable")
                         and backend_checks.get("serves_client") is False)

    labels = [f"блок {name} недоступен" for name in unreachable]
    if transfers_broken:
        labels.append("переводы (базовая функция) не работают")
    if serves_broken:
        labels.append("данные клиента (/clients) не отдаются")

    return {
        "unreachable_blocks": len(unreachable),
        "transfers_broken": transfers_broken,
        "serves_client_broken": serves_broken,
        "labels": labels,
    }


async def probe_team(team: str, urls: dict, repo: str | None = None) -> dict:
    """Снять generic-снимок трёх блоков команды. urls = {retail, cib, backend}.

    Возвращает ``{team, blocks: {backend, cib, retail}, regression: {...}}``.
    Каждый блок — ``{reachable, commit, contract, checks, (retail: html)}``.
    `repo` (``owner/name`` на GitHub) нужен для чтения CONTRACT.md через raw;
    при отсутствии контракт пуст, снимок деградирует мягко. Никогда не бросает.
    """
    out: dict = {"team": team, "blocks": {}}
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        out["blocks"]["backend"] = await _probe_backend(
            client, urls["backend"].rstrip("/"))
        out["blocks"]["cib"] = await _probe_cib(client, urls["cib"].rstrip("/"))
        out["blocks"]["retail"] = await _probe_retail(
            client, urls["retail"].rstrip("/"))
        for block in ("backend", "cib", "retail"):
            commit = out["blocks"][block].get("commit")
            out["blocks"][block]["contract"] = await _fetch_contract(
                client, repo, commit, block)
    out["regression"] = assess_regression(out)
    return out
