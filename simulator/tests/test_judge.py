from src.judge import RUBRIC_CRITERIA, fallback_rubric, parse_judge_response


def _baseline_snapshot():
    return {
        "reachable": True,
        "checks": {
            "credit_mentioned_in_ui": False,
            "credit_apply_status": 501,
            "credit_apply_latency_ms": 120,
            "decision_is_discriminating": False,
            "credit_response_has_explanation": False,
            "transfer_regression_ok": True,
        },
    }


def _done_snapshot():
    return {
        "reachable": True,
        "checks": {
            "credit_mentioned_in_ui": True,
            "credit_apply_status": 200,
            "credit_apply_latency_ms": 1500,
            "decision_is_discriminating": True,
            "credit_response_has_explanation": True,
            "transfer_regression_ok": True,
        },
    }


def test_rubric_has_six_criteria():
    assert len(RUBRIC_CRITERIA) == 6


def test_fallback_baseline_scores_two():
    scores, reason = fallback_rubric(_baseline_snapshot())
    assert scores == [0, 0, 0, 0, 0, 2]
    assert isinstance(reason, str) and reason


def test_fallback_done_scores_twelve():
    scores, reason = fallback_rubric(_done_snapshot())
    assert scores == [2, 2, 2, 2, 2, 2]


def test_fallback_slow_decision_partial_c3():
    snap = _done_snapshot()
    snap["checks"]["credit_apply_latency_ms"] = 7000
    scores, _ = fallback_rubric(snap)
    assert scores[2] == 1  # C3: 5-10 c -> 1 балл


def test_parse_judge_response_valid():
    raw = ('{"team_a": {"scores": [2,2,2,0,0,2], "reason": "ок"}, '
           '"team_b": {"scores": [0,0,0,0,0,2], "reason": "пусто"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"] == [2, 2, 2, 0, 0, 2]
    assert parsed["team_b"]["reason"] == "пусто"


def test_parse_judge_response_with_code_fence():
    raw = '```json\n{"team_a": {"scores": [1,1,1,1,1,1], "reason": "x"}}\n```'
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"] == [1, 1, 1, 1, 1, 1]


def test_parse_judge_response_garbage_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("это не json")
