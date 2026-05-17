from src.scoring import compute_round, compute_unreachable, rubric_total


def test_rubric_total_clamps():
    assert rubric_total([2] * 10) == 20
    assert rubric_total([2] * 11) == 20  # сверху зажато
    assert rubric_total([-5, 0, 0, 0, 0, 0, 0, 0, 0, 0]) == 0  # снизу зажато


def test_no_change_gives_zero_delta():
    r = compute_round(s_now=10, s_prev=10, s_base=4, client_base=600)
    assert r == {"delta": 0, "client_base": 600, "target": 600, "changed": False}


def test_full_completion_from_baseline():
    # S 4 -> 20: improvement = 16/20 = 0.8, target = round(500 * 1.48) = 740
    r = compute_round(s_now=20, s_prev=4, s_base=4, client_base=500)
    assert r["target"] == 740
    assert r["delta"] == 240
    assert r["changed"] is True


def test_partial_progress():
    # S 4 -> 12: improvement = 8/20 = 0.4, target = round(500 * 1.24) = 620
    r = compute_round(s_now=12, s_prev=4, s_base=4, client_base=500)
    assert r["target"] == 620
    assert r["delta"] == 120


def test_regression_below_baseline_loses_clients():
    # S 12 -> 0: improvement = (0-4)/20 = -0.2, target = round(500 * 0.88) = 440
    r = compute_round(s_now=0, s_prev=12, s_base=4, client_base=620)
    assert r["target"] == 440
    assert r["delta"] == -180


def test_unreachable_team_drops_base():
    r = compute_unreachable(client_base=700)
    assert r["target"] == 560
    assert r["delta"] == -140
