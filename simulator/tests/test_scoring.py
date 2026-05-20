"""Тесты формулы клиентской базы — модель «запаса и потока»."""
from src.scoring import (
    CEIL,
    FLOOR,
    compute_commit_round,
    compute_decay,
    compute_unreachable,
    convenience_factor,
    perceived_value,
    rubric_total,
)

FULL_CREDIT = [2] * 9 + [2]   # все 9 кредитных критериев + переводы целы
NO_CREDIT = [0] * 9 + [2]     # кредитной фичи нет, переводы целы
BROKEN = [0] * 9 + [0]        # фичи нет и переводы сломаны


def test_rubric_total_clamps():
    assert rubric_total([2] * 10) == 20
    assert rubric_total([2] * 11) == 20                       # сверху зажато
    assert rubric_total([-5, 0, 0, 0, 0, 0, 0, 0, 0, 0]) == 0  # снизу зажато


# --- convenience_factor ------------------------------------------------------

def test_convenience_factor_break_even_at_four():
    assert convenience_factor(4) == 0.0
    assert convenience_factor(10) == 1.0
    assert convenience_factor(0) == -0.5            # минус поджат


def test_convenience_factor_monotone_and_signed():
    assert convenience_factor(8) > convenience_factor(6) > convenience_factor(4)
    assert convenience_factor(7) > 0 > convenience_factor(3)


# --- perceived_value ---------------------------------------------------------

def test_value_zero_when_feature_not_working():
    # фронтендер выкатил вкладку, апишек ещё нет — клиенты функцию не видят
    for fs in ("absent", "frontend_only", "partial"):
        assert perceived_value(FULL_CREDIT, fs, 9) == 0.0


def test_value_positive_when_working_and_convenient():
    assert perceived_value(FULL_CREDIT, "working", 9) > 0


def test_value_negative_when_working_but_clunky():
    # фича работает, но сделана криво и неудобно — клиенты уходят
    assert perceived_value(FULL_CREDIT, "working", 1) < 0


def test_convenience_drives_direction_for_working_feature():
    convenient = perceived_value(FULL_CREDIT, "working", 9)
    clunky = perceived_value(FULL_CREDIT, "working", 2)
    assert convenient > 0 > clunky


def test_regression_costs_clients_regardless_of_feature():
    # сломанные переводы бьют всегда — даже когда кредитной фичи нет
    assert perceived_value(BROKEN, "absent", 5) < 0
    assert perceived_value(BROKEN, "working", 9) < perceived_value(
        FULL_CREDIT, "working", 9)


# --- compute_commit_round ----------------------------------------------------

def test_commit_delta_is_value_change_plus_flow():
    # телескоп: (200-0) = 200, плюс стационарная доля 0.15*200 = 30 → 230
    r = compute_commit_round(value_now=200.0, value_prev=0.0,
                             client_base=500.0, stationary_flow=0.15)
    assert r["delta"] == 230.0
    assert r["client_base"] == 730.0


def test_commit_stationary_flow_keeps_moving_steady_value():
    # ценность не изменилась, но качество остаётся: стационар двигает базу
    r = compute_commit_round(value_now=120.0, value_prev=120.0,
                             client_base=640.0, stationary_flow=0.15)
    assert r["delta"] == 18.0          # 0.15 * 120
    assert r["client_base"] == 658.0


def test_commit_stationary_flow_disabled_means_pure_telescope():
    # с flow=0 поведение прежнее — дельта строго от изменения ценности
    r = compute_commit_round(value_now=120.0, value_prev=120.0,
                             client_base=640.0, stationary_flow=0.0)
    assert r["delta"] == 0.0
    assert r["client_base"] == 640.0


def test_commit_clamps_to_ceiling_and_floor():
    assert compute_commit_round(9999.0, 0.0, 500.0)["client_base"] == CEIL
    assert compute_commit_round(-9999.0, 0.0, 500.0)["client_base"] == FLOOR


# --- compute_decay -----------------------------------------------------------

def test_decay_silent_within_grace():
    r = compute_decay(600.0, idle_seconds=600, slice_seconds=60,
                      grace_s=1800, rate_per_min=1.5)
    assert r["changed"] is False
    assert r["delta"] == 0.0


def test_decay_silent_when_slice_is_zero():
    # защита холодного старта: срез нулевой → утечки нет, даже если простой огромен
    r = compute_decay(600.0, idle_seconds=99999, slice_seconds=0,
                      grace_s=1800, rate_per_min=1.5)
    assert r["changed"] is False
    assert r["delta"] == 0.0


def test_decay_leaks_past_grace():
    # простой 40 мин, срез 60 c — за гранью прощения вытекает 1.5×1 мин
    r = compute_decay(600.0, idle_seconds=2400, slice_seconds=60,
                      grace_s=1800, rate_per_min=1.5)
    assert r["changed"] is True
    assert r["delta"] == -1.5
    assert r["client_base"] == 598.5


def test_decay_only_counts_slice_past_grace():
    # только что пересекли грань: idle−grace=20 c, срез 60 c → активны лишь 20 c
    r = compute_decay(600.0, idle_seconds=1820, slice_seconds=60,
                      grace_s=1800, rate_per_min=1.5)
    assert r["delta"] == -1.5 * (20 / 60)


def test_decay_accumulates_across_ticks():
    base = 600.0
    for _ in range(4):
        base = compute_decay(base, idle_seconds=3600, slice_seconds=60,
                             grace_s=1800, rate_per_min=1.5)["client_base"]
    assert base == 600.0 - 4 * 1.5   # утечка копится, следующим тиком не сбрасывается


def test_decay_floors_base():
    r = compute_decay(FLOOR, idle_seconds=99999, slice_seconds=99999,
                      grace_s=1800, rate_per_min=1.5)
    assert r["client_base"] == FLOOR


# --- compute_unreachable -----------------------------------------------------

def test_unreachable_drops_base():
    r = compute_unreachable(client_base=700.0)
    assert r["client_base"] == 560.0
    assert r["delta"] == -140.0
