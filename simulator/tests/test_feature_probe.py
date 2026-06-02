"""Тесты якоря «фича живая» — реальный вызов новой ручки, не вера контракту."""
from __future__ import annotations

import asyncio

from src import feature_probe as fp

RETAIL_BASE = (
    "# retail\n"
    "### GET /health\nживость\n"
    "### POST /api/transfer\nперевод\n"
)
RETAIL_WITH_CREDIT = RETAIL_BASE + "### POST /api/credit-apply\nоформить кредит\n"


def _snap(contract_retail: str) -> dict:
    return {"team": "team_a", "blocks": {
        "backend": {"contract": "# backend\n### GET /health\n"},
        "cib": {"contract": "# cib\n### GET /health\n"},
        "retail": {"contract": contract_retail}}}


# --- чистые функции ----------------------------------------------------------

def test_parse_endpoints_reads_method_and_path():
    eps = fp.parse_endpoints(RETAIL_WITH_CREDIT)
    assert ("POST", "/api/credit-apply") in eps
    assert ("GET", "/health") in eps


def test_parse_endpoints_empty_on_prose():
    # контракт без заголовков ручек → пусто (не ловим случайные строки)
    assert fp.parse_endpoints("просто текст без ручек") == set()


def test_discover_new_endpoints_diffs_against_baseline():
    new = fp.discover_new_endpoints(_snap(RETAIL_WITH_CREDIT),
                                    _snap(RETAIL_BASE))
    assert [e["path"] for e in new] == ["/api/credit-apply"]
    assert new[0]["block"] == "retail"
    assert new[0]["method"] == "POST"


def test_discover_none_when_contracts_unchanged():
    assert fp.discover_new_endpoints(_snap(RETAIL_BASE), _snap(RETAIL_BASE)) == []


def test_discover_orders_retail_first():
    cur = {"team": "t", "blocks": {
        "backend": {"contract": "### GET /health\n### GET /new-backend\n"},
        "cib": {"contract": "### GET /health\n"},
        "retail": {"contract": "### GET /health\n### POST /new-retail\n"}}}
    base = {"team": "t", "blocks": {
        n: {"contract": "### GET /health\n"} for n in ("backend", "cib", "retail")}}
    new = fp.discover_new_endpoints(cur, base)
    assert new[0]["block"] == "retail"   # самый клиентский — первым


def test_classify_status_buckets():
    assert fp.classify_status(200) is True
    assert fp.classify_status(201) is True
    assert fp.classify_status(404) is False
    assert fp.classify_status(500) is False
    assert fp.classify_status(503) is False
    assert fp.classify_status(422) is None   # валидация — неубедительно
    assert fp.classify_status(403) is None
    assert fp.classify_status(None) is None


# --- оркестратор assess_feature_liveness -------------------------------------

class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _Client:
    """Фейковый httpx-клиент: очередь ответов по порядку вызовов."""
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(self, method, url, json=None):
        self.calls.append((method, url, json))
        if not self._responses:
            raise AssertionError("неожиданный лишний HTTP-вызов")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


URLS = {"retail": "https://r", "cib": "https://c", "backend": "https://b"}


def _assess(client, *, synth=None) -> dict | None:
    return asyncio.run(fp.assess_feature_liveness(
        client, _snap(RETAIL_WITH_CREDIT), _snap(RETAIL_BASE), URLS, synth=synth))


def test_assess_none_when_no_new_endpoints():
    client = _Client()  # не должен делать вызовов
    res = asyncio.run(fp.assess_feature_liveness(
        client, _snap(RETAIL_BASE), _snap(RETAIL_BASE), URLS))
    assert res is None
    assert client.calls == []


def test_assess_dead_on_404_without_llm():
    # ручка задекларирована, но её нет (404) → False уже на Tier 1, без LLM
    res = _assess(_Client(_Resp(404)), synth=_fail_synth())
    assert res["feature_live"] is False
    assert res["tier"] == 1
    assert res["primary"]["path"] == "/api/credit-apply"


def test_assess_dead_on_500():
    res = _assess(_Client(_Resp(500)), synth=_fail_synth())
    assert res["feature_live"] is False
    assert res["status"] == 500


def test_assess_live_on_real_call_after_validation_422():
    # Tier 1 пустым телом → 422 (нужен ввод); Tier 2 с валидным телом → 200
    async def good_synth(snap, primary):
        return {"client_id": "c-01394", "amount": 50000}
    res = _assess(_Client(_Resp(422), _Resp(200, '{"approved": true}')),
                  synth=good_synth)
    assert res["feature_live"] is True
    assert res["tier"] == 2


def test_assess_dead_when_real_call_500s():
    async def good_synth(snap, primary):
        return {"client_id": "c-01394", "amount": 50000}
    res = _assess(_Client(_Resp(422), _Resp(500)), synth=good_synth)
    assert res["feature_live"] is False
    assert res["tier"] == 2


def test_assess_our_fault_synth_crash_degrades_to_none():
    # Tier 1 = 422 (неубедительно), LLM-синтез упал по нашей вине → None, не False
    async def boom_synth(snap, primary):
        raise RuntimeError("LLM сгенерил мусор")
    res = _assess(_Client(_Resp(422)), synth=boom_synth)
    assert res["feature_live"] is None
    assert "синтез" in res["note"]


def test_assess_network_blip_is_none_not_dead():
    # сервис не ответил (наш таймаут/сеть) → None, команду не штрафуем
    res = _assess(_Client(httpx_error()), synth=_fail_synth())
    assert res["feature_live"] is None


def _fail_synth():
    async def _s(snap, primary):
        raise AssertionError("synth не должен вызываться в этом сценарии")
    return _s


def httpx_error():
    import httpx
    return httpx.ConnectError("нет связи")
