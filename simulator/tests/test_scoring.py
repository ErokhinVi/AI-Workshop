"""Тесты формулы клиентской базы — модель «запаса и потока»."""
from src.scoring import (
    BROKEN_ENDPOINT_COST,
    CEIL,
    FLOOR,
    compute_commit_round,
    compute_decay,
    compute_unreachable,
    convenience_factor,
    cross_block_mult,
    dead_feature_cost,
    feature_value,
    outage_cost,
    rubric_total,
)

FULL_AXES = (2, 2, 2)   # полная сквозная фича: все три оси максимальны
NO_AXES = (0, 0, 0)     # фичи нет


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


# --- cross_block_mult --------------------------------------------------------

def test_cross_block_mult_bonus_for_three_blocks():
    assert cross_block_mult(0) == 1.0
    assert cross_block_mult(2) > 1.0          # бонус за сквозную фичу
    assert cross_block_mult(1) == 1.0 + (cross_block_mult(2) - 1.0) / 2.0
    assert cross_block_mult(99) == cross_block_mult(2)   # зажато сверху


# --- feature_value -----------------------------------------------------------

def test_feature_value_zero_when_absent():
    assert feature_value(axes=(2, 2, 2), cross_block=2, convenience=9,
                         feature_state="absent", outage_penalty=0.0) == 0.0


def test_feature_value_positive_when_working_and_convenient():
    v = feature_value(axes=(2, 2, 2), cross_block=2, convenience=9,
                      feature_state="working", outage_penalty=0.0)
    assert v > 0


def test_feature_value_counts_partial_feature():
    # частично доступная фича клиенту видна — она тоже двигает базу
    assert feature_value((2, 2, 2), 2, 9, "partial") > 0


def test_feature_value_regression_penalty_always_applies():
    # absent + сломанная база → отрицательно (штраф вне зависимости от фичи)
    v = feature_value(axes=(0, 0, 0), cross_block=0, convenience=5,
                      feature_state="absent", outage_penalty=120.0)
    assert v == -120.0


def test_feature_value_bad_convenience_reduces_working():
    good = feature_value((2, 2, 2), 2, 9, "working", outage_penalty=0.0)
    bad = feature_value((2, 2, 2), 2, 1, "working", outage_penalty=0.0)
    assert bad < good


def test_feature_value_negative_when_working_but_clunky():
    # фича работает, но сделана криво и неудобно — клиенты уходят
    assert feature_value(FULL_AXES, 2, 1, "working") < 0


def test_feature_value_cross_block_bonus_raises_value():
    flat = feature_value(FULL_AXES, 0, 9, "working")
    cross = feature_value(FULL_AXES, 2, 9, "working")
    assert cross > flat


def test_feature_value_structural_axes_raise_saturated_working_feature():
    # Даже когда базовые оси уже максимальны, новая глубина backend и ширина
    # продуктовой линейки должны давать измеримый прирост.
    base = feature_value(FULL_AXES, 2, 8, "working")
    richer = feature_value(
        FULL_AXES, 2, 8, "working",
        backend_persistence=2, feature_breadth=2,
    )
    assert richer > base


def test_feature_value_structural_axes_do_not_reward_dead_feature():
    v = feature_value(
        FULL_AXES, 2, 9, "working", feature_live=False,
        backend_persistence=2, feature_breadth=2,
    )
    assert v == 0.0


def test_feature_value_ui_polish_adds_client_value():
    base = feature_value(FULL_AXES, 2, 8, "working")
    polished = feature_value(FULL_AXES, 2, 8, "working", ui_polish=2)
    assert polished > base


# --- якорь «фича живая»: feature_live ----------------------------------------

def test_feature_value_dead_feature_gives_no_axis_value():
    # ручку дёрнули — НЕ работает: высокие оси LLM не дают ценности (витрина)
    v = feature_value(FULL_AXES, 2, 9, "working", feature_live=False)
    assert v == 0.0


def test_feature_value_dead_feature_with_outage_only_penalty():
    # мёртвая фича + штраф 5xx → строго отрицательно, без вклада осей
    v = feature_value(FULL_AXES, 2, 9, "working",
                      outage_penalty=BROKEN_ENDPOINT_COST, feature_live=False)
    assert v == -BROKEN_ENDPOINT_COST


def test_feature_value_live_true_unchanged():
    # доказанно работает → как сейчас (равно вызову без feature_live)
    assert (feature_value(FULL_AXES, 2, 9, "working", feature_live=True)
            == feature_value(FULL_AXES, 2, 9, "working"))


def test_feature_value_live_none_does_not_penalize():
    # проверить не удалось (None) → не наказываем, ценность как обычно
    assert (feature_value(FULL_AXES, 2, 9, "working", feature_live=None)
            == feature_value(FULL_AXES, 2, 9, "working"))


def test_dead_feature_cost_only_for_5xx():
    assert dead_feature_cost(500) == BROKEN_ENDPOINT_COST
    assert dead_feature_cost(503) == BROKEN_ENDPOINT_COST
    assert dead_feature_cost(404) == 0.0     # ручки нет — без штрафа
    assert dead_feature_cost(None) == 0.0


# --- outage_cost / штраф за сломанные ручки ----------------------------------

def test_outage_cost_prices_blocks_and_endpoints():
    assert outage_cost(0, 0) == 0.0
    assert outage_cost(1, 0) == 90.0          # один недоступный блок
    assert outage_cost(0, 2) == 120.0         # две падающие ручки
    assert outage_cost(2, 1) == 2 * 90.0 + 60.0
    assert outage_cost(-3, -3) == 0.0         # отрицательные зажаты в ноль


def test_outage_penalty_subtracts_even_from_working_feature():
    clean = feature_value(FULL_AXES, 2, 9, "working")
    with_outage = feature_value(FULL_AXES, 2, 9, "working",
                                outage_penalty=outage_cost(1, 0))
    assert with_outage == clean - 90.0


def test_commit_no_change_no_movement_by_default():
    # дефолтный STATIONARY_FLOW=0 → коммит без изменения ценности не двигает базу
    r = compute_commit_round(value_now=120.0, value_prev=120.0, client_base=640.0)
    assert r["delta"] == 0.0
    assert r["client_base"] == 640.0


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
