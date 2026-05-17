from src.judge import RUBRIC_CRITERIA, fallback_rubric, parse_judge_response


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


def test_rubric_has_ten_criteria():
    assert len(RUBRIC_CRITERIA) == 10


def test_fallback_baseline_scores_four():
    scores, reason = fallback_rubric(_baseline_team())
    assert scores == [2, 0, 0, 0, 0, 0, 0, 0, 0, 2]
    assert isinstance(reason, str) and reason


def test_fallback_done_scores_twenty():
    scores, _ = fallback_rubric(_done_team())
    assert scores == [2] * 10


def test_parse_judge_response_valid():
    raw = ('{"team_a": {"scores": [2,2,2,2,2,0,0,0,0,2], "reason": "ок"}, '
           '"team_b": {"scores": [2,0,0,0,0,0,0,0,0,2], "reason": "старт"}}')
    parsed = parse_judge_response(raw)
    assert parsed["team_a"]["scores"][4] == 2
    assert parsed["team_b"]["reason"] == "старт"


def test_parse_judge_response_code_fence():
    raw = '```json\n{"team_a": {"scores": [1,1,1,1,1,1,1,1,1,1], "reason": "x"}}\n```'
    assert parse_judge_response(raw)["team_a"]["scores"] == [1] * 10


def test_parse_judge_response_garbage_raises():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("не json")
