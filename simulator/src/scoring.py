"""Чистая логика клиентской базы — модель «запаса и потока». Без I/O.

Клиентская база команды — это запас. Каждый коммит-раунд и каждый тик застоя
дают дельту, которая прибавляется к текущей базе:

* Коммит-раунд. Дельта = изменение «ценности банка для клиента» с прошлого
  раунда (`perceived_value`). Пока кредитная фича не работает сквозь все три
  блока (`feature_state != "working"`), её критерии не двигают базу ни вверх,
  ни вниз — клиенты просто не видят полуготовую функцию. Когда фича работает,
  её вес умножается на фактор удобства: удобно — клиенты приходят, криво —
  уходят.
* Тик застоя. Команда давно не выпускала обновлений — клиенты постепенно
  утекают к конкурентам (`compute_decay`).

Дельты коммитов телескопируются (зависят только от текущей ценности), поэтому
качество всегда честно отражено в базе; а спад от застоя накапливается отдельно
и следующим коммитом не стирается.

Внутри всё считается во float — база округляется только на границе (БД, табло,
события). Пороги застоя env-настраиваемые; значения по умолчанию подобраны под
3-часовой воркшоп.
"""
from __future__ import annotations

import os

B0 = 500              # стартовая клиентская база каждой команды
FLOOR = 50.0          # база не опускается ниже
CEIL = 1000.0         # и не поднимается выше (страховка от дрейфа формулы)
RUBRIC_MAX = 20       # 10 критериев по 2 балла

# C1..C9 — кредитная фича (9 критериев), C10 — регрессия переводов.
CREDIT_CRITERIA = 9

# Вес одного балла кредитной рубрики в клиентах и цена сломанных переводов.
CLIENTS_PER_POINT = 16.0
REGRESSION_COST = 120.0

# Стационарный поток: на каждый коммит-раунд база сдвигается ещё и на долю
# текущей ценности банка. Это значит «качество фичи продолжает работать»:
# даже если score не изменился, удобная работающая фича медленно приводит
# новых клиентов, а неудобная или плохая — медленно их отпугивает. Без него
# дельта на стационаре всегда 0 и LLM-оценка не отражается на табло.
STATIONARY_FLOW = float(os.environ.get("STATIONARY_FLOW", "0.15"))

# Застой: сколько секунд прощаем простой и как быстро потом утекают клиенты.
STAGNATION_GRACE_S = float(os.environ.get("STAGNATION_GRACE_S", "3600"))  # 60 мин
STAGNATION_RATE_PER_MIN = float(os.environ.get("STAGNATION_RATE_PER_MIN", "1.5"))

UNREACHABLE_FACTOR = 0.8  # множитель базы, когда весь банк недоступен

# Стадии жизни кредитной фичи (классифицирует judge.classify_feature).
FEATURE_STATES = ("absent", "frontend_only", "partial", "working")


def rubric_total(scores: list[int]) -> int:
    """Сумма баллов рубрики, зажатая в [0, RUBRIC_MAX]. Сводка для табло."""
    return max(0, min(RUBRIC_MAX, sum(int(x) for x in scores)))


def convenience_factor(convenience: float) -> float:
    """Удобство 0–10 → множитель ценности рабочей кредитной фичи.

    Точка безразличия — 4 балла: ниже клиенты уходят (фича работает, но
    неудобна), выше — приходят. Минус поджат до −0.5, плюс полный до +1.0.
    """
    c = max(0.0, min(10.0, float(convenience)))
    return max(-0.5, (c - 4.0) / 6.0)


def perceived_value(scores: list[int], feature_state: str,
                    convenience: float) -> float:
    """Ценность банка для клиента в «клиентах» — куда тянет клиентскую базу.

    Кредитные критерии C1..C9 учитываются, только когда фича работает сквозь
    все три блока (`feature_state == "working"`): иначе клиенты её не видят и
    база не двигается. Регрессия переводов (C10) бьёт всегда — это базовая
    функция банка, не зависящая от кредитной фичи.
    """
    safe = [max(0, min(2, int(x))) for x in scores] + [0] * 10
    credit = sum(safe[:CREDIT_CRITERIA])      # 0..18
    regression = safe[CREDIT_CRITERIA]        # C10: 0..2

    value = 0.0
    if feature_state == "working":
        value += CLIENTS_PER_POINT * credit * convenience_factor(convenience)
    value -= REGRESSION_COST * (2 - regression) / 2.0
    return value


def compute_commit_round(value_now: float, value_prev: float,
                         client_base: float, *,
                         stationary_flow: float = STATIONARY_FLOW) -> dict:
    """Коммит-раунд: дельта = изменение ценности + доля её текущего уровня.

    Телескопическая часть быстро реагирует на улучшение/ухудшение фичи. Доля
    `stationary_flow * value_now` — медленный поток, продолжающий двигать базу
    даже когда оценка не изменилась: рабочая удобная фича постепенно приводит
    клиентов, плохая или сломанная — постепенно отпугивает.
    """
    telescoping = value_now - value_prev
    flow = stationary_flow * value_now
    target = max(FLOOR, min(CEIL, client_base + telescoping + flow))
    return {"delta": target - client_base, "client_base": target,
            "value": value_now}


def compute_decay(client_base: float, idle_seconds: float,
                  slice_seconds: float, *,
                  grace_s: float = STAGNATION_GRACE_S,
                  rate_per_min: float = STAGNATION_RATE_PER_MIN) -> dict:
    """Тик застоя: клиенты утекают, если команда давно не коммитила.

    `idle_seconds` — сколько команда уже без нового коммита; `slice_seconds` —
    интервал с прошлого тика, только он и утекает за этот вызов (поэтому сон
    Render не оборачивается разовым обвалом). Спад начинается после `grace_s`
    секунд прощённого простоя.
    """
    if idle_seconds <= grace_s or slice_seconds <= 0:
        return {"delta": 0.0, "client_base": client_base, "changed": False}
    # за этот вызов утекает лишь та часть среза, что лежит за гранью прощения
    active = min(slice_seconds, idle_seconds - grace_s)
    leak = rate_per_min * (active / 60.0)
    target = max(FLOOR, client_base - leak)
    return {"delta": target - client_base, "client_base": target,
            "changed": target != client_base}


def compute_unreachable(client_base: float) -> dict:
    """Весь банк недоступен — клиенты не могут войти, база падает."""
    target = max(FLOOR, client_base * UNREACHABLE_FACTOR)
    return {"delta": target - client_base, "client_base": target}
