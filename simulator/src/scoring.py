"""Чистая логика подсчёта клиентской базы. Без I/O — легко тестируется.

Параметры тюнятся; значения по умолчанию согласованы со спекой.
"""
from __future__ import annotations

B0 = 500          # стартовая клиентская база каждой команды
GAIN = 0.6        # сила влияния качества банка на базу
RUBRIC_MAX = 12   # 6 критериев по 2 балла
FLOOR = 50        # база не опускается ниже этого значения
UNREACHABLE_FACTOR = 0.8  # множитель базы, когда банк недоступен


def rubric_total(scores: list[int]) -> int:
    """Сумма баллов рубрики, зажатая в [0, RUBRIC_MAX]."""
    return max(0, min(RUBRIC_MAX, sum(scores)))


def compute_round(s_now: int, s_prev: int, s_base: int, client_base: int) -> dict:
    """Один раунд оценки команды.

    Возвращает {delta, client_base, target, changed}. Если рубрика не
    изменилась (s_now == s_prev) — дельта 0, база не двигается.
    """
    if s_now == s_prev:
        return {"delta": 0, "client_base": client_base,
                "target": client_base, "changed": False}
    improvement = (s_now - s_base) / RUBRIC_MAX
    improvement = max(-1.0, min(1.0, improvement))
    target = max(FLOOR, round(B0 * (1 + GAIN * improvement)))
    return {"delta": target - client_base, "client_base": target,
            "target": target, "changed": True}


def compute_unreachable(client_base: int) -> dict:
    """Банк не открывается — клиенты не могут войти, база падает."""
    target = max(FLOOR, round(client_base * UNREACHABLE_FACTOR))
    return {"delta": target - client_base, "client_base": target,
            "target": target, "changed": True}
