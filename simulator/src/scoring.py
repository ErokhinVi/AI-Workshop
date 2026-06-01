"""Чистая логика клиентской базы — модель «запаса и потока». Без I/O.

Клиентская база команды — это запас. Каждый коммит-раунд и каждый тик застоя
дают дельту, которая прибавляется к текущей базе:

* Коммит-раунд. Дельта = ИЗМЕНЕНИЕ «ценности банка для клиента» с прошлого
  раунда (`perceived_value`) — чисто телескопическое. Если коммит ничего не
  поменял в наблюдаемом поведении банка, ценность та же и дельта = 0: база не
  двигается. Это сознательно — иначе каждый, даже пустой, коммит дёргал бы
  табло вверх-вниз. Ценность складывается из двух частей:
    - кредитная фича: учитывается, только когда работает сквозь все три блока
      (`feature_state == "working"`), и тогда её вес умножается на фактор
      удобства — удобно клиенты приходят, криво уходят; полуготовую фичу клиент
      не видит, она базу не двигает;
    - работоспособность ручек: сломанные (5xx) или недоступные ручки/блоки и
      регрессия переводов бьют по ценности ВСЕГДА, на любой стадии фичи
      (`outage_cost`) — выкаченное и падающее отпугивает клиентов. Недоделанное
      (404 / нет ручки) не штрафуется: его клиент просто не видит.
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

# Цена выкаченной, но падающей ручки (5xx) и недоступного блока — в клиентах.
# Бьёт независимо от стадии фичи: «сделано криво» отпугивает клиентов, тогда как
# «ещё не сделано» (404) — нет (за это `outage_cost` не штрафует, см. judge).
BROKEN_ENDPOINT_COST = 60.0
UNREACHABLE_BLOCK_COST = 90.0

# Стационарный поток ОТКЛЮЧЁН по умолчанию (0.0). Раньше каждый коммит-раунд
# сдвигал базу ещё и на долю текущей ценности — из-за этого даже пустой коммит
# дёргал табло, и обоснования сыпались на каждый деплой. Теперь дельту даёт
# только реальное изменение ценности (телескоп). Параметр сохранён ради
# совместимости и ручной настройки через env, но штатно равен нулю.
STATIONARY_FLOW = float(os.environ.get("STATIONARY_FLOW", "0.0"))

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


def outage_cost(unreachable_blocks: int, broken_endpoints: int) -> float:
    """Цена нерабочих ручек на этом коммите в «клиентах» (всегда ≥ 0).

    `unreachable_blocks` — сколько из трёх блоков не отвечают (частичный простой;
    полную недоступность считает `compute_unreachable`). `broken_endpoints` —
    сколько выкаченных ручек падает с 5xx (т.е. «сделано криво», а не «ещё не
    сделано»). Что именно считать сломанным и как отличить от недоделанного —
    в `judge.assess_outages`; здесь только перевод количеств в клиентов.
    """
    return (UNREACHABLE_BLOCK_COST * max(0, int(unreachable_blocks))
            + BROKEN_ENDPOINT_COST * max(0, int(broken_endpoints)))


def perceived_value(scores: list[int], feature_state: str,
                    convenience: float, *, outage_penalty: float = 0.0) -> float:
    """Ценность банка для клиента в «клиентах» — куда тянет клиентскую базу.

    Кредитные критерии C1..C9 учитываются, только когда фича работает сквозь
    все три блока (`feature_state == "working"`): иначе клиенты её не видят и
    база не двигается. Регрессия переводов (C10) и `outage_penalty` (цена
    сломанных/недоступных ручек, см. `outage_cost`) бьют ВСЕГДА — это про
    работоспособность банка, не про стадию кредитной фичи.
    """
    safe = [max(0, min(2, int(x))) for x in scores] + [0] * 10
    credit = sum(safe[:CREDIT_CRITERIA])      # 0..18
    regression = safe[CREDIT_CRITERIA]        # C10: 0..2

    value = 0.0
    if feature_state == "working":
        value += CLIENTS_PER_POINT * credit * convenience_factor(convenience)
    value -= REGRESSION_COST * (2 - regression) / 2.0
    value -= max(0.0, float(outage_penalty))
    return value


def compute_commit_round(value_now: float, value_prev: float,
                         client_base: float, *,
                         stationary_flow: float = STATIONARY_FLOW) -> dict:
    """Коммит-раунд: дельта = изменение ценности (+ опциональный поток).

    Телескопическая часть `value_now - value_prev` — единственный штатный
    источник дельты: реагирует на реальное улучшение/ухудшение банка, а на
    коммите без изменений даёт 0 (база стоит). Доля `stationary_flow * value_now`
    по умолчанию выключена (`STATIONARY_FLOW == 0.0`) — раньше она двигала базу
    на каждый коммит и засоряла табло; параметр оставлен для ручной настройки.
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
