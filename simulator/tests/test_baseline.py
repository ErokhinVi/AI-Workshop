"""Тесты детерминированного baseline (нулевой точки из шаблона)."""
from __future__ import annotations

import re
from pathlib import Path

from src import baseline as bl
from src import feature_probe as fp
from src.judge import _new_endpoints

_TEAM_TEMPLATE = Path(__file__).resolve().parents[2] / "team-template"
_ENDPOINT_RE = re.compile(r"^###\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
                          re.MULTILINE)


def test_baseline_snapshot_shape():
    snap = bl.baseline_snapshot("team_a")
    assert snap["team"] == "team_a"
    assert set(snap["blocks"]) == {"backend", "cib", "retail"}
    for name, block in snap["blocks"].items():
        assert block["reachable"] is True
        assert block["commit"] == "template"
        assert block["contract"].strip()
    # retail несёт html (пустой) — для оси ui_polish
    assert snap["blocks"]["retail"]["html"] == ""


def test_baseline_contract_parses_to_template_endpoints():
    # contract нулевой точки должен парситься ровно в встроенный список ручек
    for block, eps in bl.TEMPLATE_ENDPOINTS.items():
        parsed = fp.parse_endpoints(bl.baseline_contract(block))
        assert parsed == {(m, p) for m, p in eps}


def test_team_addition_is_seen_as_new_vs_baseline():
    # команда добавила кредит-ручку поверх шаблона → ровно она в diff
    base = bl.baseline_snapshot("team_a")
    cur = {"team": "team_a", "blocks": {
        n: {"contract": base["blocks"][n]["contract"]} for n in bl.BLOCKS}}
    cur["blocks"]["retail"]["contract"] += "### POST /api/credit-apply\nкредит\n"
    new = _new_endpoints(cur, base)
    assert new["retail"] == {("POST", "/api/credit-apply")}
    assert new["backend"] == set() and new["cib"] == set()


def test_embedded_endpoints_match_team_template():
    # страж: встроенный список не должен разъезжаться с team-template/*/CONTRACT.md
    if not _TEAM_TEMPLATE.exists():
        return  # в образе симулятора team-template нет — проверяем только в репо
    for block, eps in bl.TEMPLATE_ENDPOINTS.items():
        f = _TEAM_TEMPLATE / block / "CONTRACT.md"
        text = f.read_text(encoding="utf-8")
        from_file = {(m.group(1).upper(), m.group(2).rstrip("/"))
                     for m in _ENDPOINT_RE.finditer(text)}
        assert from_file == {(m, p.rstrip("/")) for m, p in eps}, \
            f"{block}: встроенный baseline разошёлся с team-template"
