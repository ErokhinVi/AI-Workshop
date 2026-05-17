from src.scoring import compute_round, compute_unreachable, rubric_total


def test_rubric_total_clamps():
    assert rubric_total([2, 2, 2, 2, 2, 2]) == 12
    assert rubric_total([2, 2, 2, 2, 2, 3]) == 12  # сверху зажато
    assert rubric_total([-5, 0, 0, 0, 0, 0]) == 0  # снизу зажато


def test_no_change_gives_zero_delta():
    r = compute_round(s_now=8, s_prev=8, s_base=2, client_base=650)
    assert r == {"delta": 0, "client_base": 650, "target": 650, "changed": False}


def test_full_completion_from_baseline():
    # S 2 -> 12: improvement = 10/12, target = round(500 * (1 + 0.6*0.8333)) = 750
    r = compute_round(s_now=12, s_prev=2, s_base=2, client_base=500)
    assert r["target"] == 750
    assert r["delta"] == 250
    assert r["client_base"] == 750
    assert r["changed"] is True


def test_partial_progress():
    # S 2 -> 8: improvement = 6/12 = 0.5, target = round(500 * 1.3) = 650
    r = compute_round(s_now=8, s_prev=2, s_base=2, client_base=500)
    assert r["target"] == 650
    assert r["delta"] == 150


def test_regression_below_baseline_loses_clients():
    # S 8 -> 0: improvement = (0-2)/12 = -0.1667, target = round(500 * 0.9) = 450
    r = compute_round(s_now=0, s_prev=8, s_base=2, client_base=650)
    assert r["target"] == 450
    assert r["delta"] == -200


def test_unreachable_bank_drops_base():
    r = compute_unreachable(client_base=700)
    assert r["target"] == 560  # round(700 * 0.8)
    assert r["delta"] == -140
    assert r["changed"] is True
