import asyncio

from src import llm
from src.judge import (
    RUBRIC_CRITERIA,
    classify_feature,
    fallback_convenience,
    fallback_rubric,
    judge_round,
    parse_judge_response,
)


def _baseline_team():
    return {
        "team": "team_a",
        "blocks": {
            "backend": {"reachable": True, "checks": {
                "serves_client": True, "accepts_application": False,
                "lists_applications": False}},
            "cib": {"reachable": True, "checks": {
                "has_credit_product": False, "decide_status": 0,
                "decision_is_discriminating": False}},
            "retail": {"reachable": True, "checks": {
                "credit_in_ui": False, "credit_apply_status": 0,
                "credit_apply_decision": None, "credit_apply_explained": False,
                "transfer_ok": True}},
        },
    }


def _done_team():
    return {
        "team": "team_a",
        "blocks": {
            "backend": {"reachable": True, "checks": {
                "serves_client": True, "accepts_application": True,
                "lists_applications": True}},
            "cib": {"reachable": True, "checks": {
                "has_credit_product": True, "decide_status": 200,
                "decision_is_discriminating": True}},
            "retail": {"reachable": True, "checks": {
                "credit_in_ui": True, "credit_apply_status": 200,
                "credit_apply_decision": "approved", "credit_apply_explained": True,
                "transfer_ok": True}},
        },
    }


def _frontend_only_team():
    # вкладка «Кредиты» в UI есть, но за ней нет ни одной работающей ручки
    snap = _baseline_team()
    snap["blocks"]["retail"]["checks"]["credit_in_ui"] = True
    snap["blocks"]["retail"]["checks"]["credit_apply_status"] = 404
    return snap


def _partial_team():
    # backend уже принимает заявки, но сквозь все три блока не собрано
    snap = _baseline_team()
    snap["blocks"]["backend"]["checks"]["accepts_application"] = True
    return snap


def test_rubric_has_ten_criteria():
    assert len(RUBRIC_CRITERIA) == 10


def test_fallback_baseline_scores_four():
    scores, reason = fallback_rubric(_baseline_team())
    assert scores == [2, 0, 0, 0, 0, 0, 0, 0, 0, 2]
    assert isinstance(reason, str) and reason


def test_fallback_done_scores_twenty():
    scores, _ = fallback_rubric(_done_team())
    assert scores == [2] * 10


def test_classify_feature_four_states():
    assert classify_feature(_baseline_team()) == "absent"
    assert classify_feature(_frontend_only_team()) == "frontend_only"
    assert classify_feature(_partial_team()) == "partial"
    assert classify_feature(_done_team()) == "working"


def test_fallback_convenience_in_range():
    for snap in (_baseline_team(), _frontend_only_team(), _done_team()):
        assert 0 <= fallback_convenience(snap) <= 10


def test_fallback_convenience_rewards_quality():
    # объяснение отказа и осмысленное решение поднимают оценку удобства
    assert fallback_convenience(_done_team()) > fallback_convenience(_baseline_team())


def test_fallback_convenience_penalizes_slow_response():
    fast = _done_team()
    fast["blocks"]["retail"]["checks"]["credit_apply_latency_ms"] = 800
    slow = _done_team()
    slow["blocks"]["retail"]["checks"]["credit_apply_latency_ms"] = 9000
    assert fallback_convenience(fast) > fallback_convenience(slow)


def test_parse_judge_response_valid():
    raw = ('{"team_a": {"scores": [2,2,2,2,2,0,0,0,0,2], "reason": "ок"}, '
           '"team_b": {"scores": [2,0,0,0,0,0,0,0,0,2], "reason": "старт"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"][4] == 2
    assert parsed["team_b"]["reason"] == "старт"


def test_parse_judge_response_with_convenience():
    raw = ('{"team_a": {"scores": [2,2,2,2,2,2,2,2,2,2], "convenience": 8, '
           '"reason": "удобно"}, "team_b": {"scores": [2,0,0,0,0,0,0,0,0,2], '
           '"convenience": 5, "reason": "старт"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["convenience"] == 8


def test_parse_judge_response_code_fence():
    raw = '```json\n{"team_a": {"scores": [1,1,1,1,1,1,1,1,1,1], "reason": "x"}}\n```'
    assert parse_judge_response(raw)["team_a"]["scores"] == [1] * 10


def test_parse_judge_response_garbage_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("не json")


def test_judge_round_fallback_without_llm(monkeypatch):
    # без OPENAI_API_KEY весь раунд считается скриптовым fallback
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    verdict = asyncio.run(judge_round(_done_team(), _baseline_team()))
    a, b = verdict["team_a"], verdict["team_b"]
    assert a["judge"] == "fallback"
    assert len(a["scores"]) == 10
    assert 0 <= a["convenience"] <= 10
    assert a["feature_state"] == "working"
    assert b["feature_state"] == "absent"
    assert isinstance(a["reason"], str) and a["reason"]
