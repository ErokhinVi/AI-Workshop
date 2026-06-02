"""Чистая логика клиентской базы — модель «запаса и потока». Без I/O.

Клиентская база команды — это запас. Каждый коммит-раунд и каждый тик застоя
дают дельту, которая прибавляется к текущей базе:

* Коммит-раунд. Дельта = ИЗМЕНЕНИЕ «ценности банка для клиента» с прошлого
  раунда (`feature_value`) — чисто телескопическое. Если коммит ничего не
  поменял в наблюдаемом поведении банка, ценность та же и дельта = 0: база не
  двигается. Это сознательно — иначе каждый, даже пустой, коммит дёргал бы
  табло вверх-вниз. Ценность складывается из двух частей:
    - добавленная фича (ЛЮБАЯ, не только кредит): оси оценки судьи
      (new_functionality, client_value, completeness) учитываются, когда фича
      хотя бы частично доступна клиенту (`feature_state in (working, partial)`),
      и тогда их вес умножается на фактор удобства и бонус за сквозную работу
      через все три блока — удобно клиенты приходят, криво уходят; пустой банк
      базу не двигает;
    - работоспособность ручек: сломанные (5xx) или недоступные ручки/блоки и
      регрессия базовых функций бьют по ценности ВСЕГДА, на любой стадии фичи
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
RUBRIC_MAX = 20       # верхняя граница сводного балла для табло

# Веса generic-оценки. Оси new_functionality + client_value + completeness ∈ 0..6
# (сумма трёх осей по 0/1/2). AXIS_WEIGHT подобран так, чтобы полная сквозная фича
# (6 осей, convenience 9, 3 блока) давала ~+250 клиентов — заметный, но не
# мгновенно-максимальный сдвиг базы за одну хорошо собранную фичу.
AXIS_WEIGHT = 30.0
CROSS_BLOCK_BONUS = 0.4   # cross_block=2 → множитель 1.4

# Цена регрессии базовой функции (например, сломанных переводов) в клиентах.
# Сохранена как именованная константа; конкретный штраф собирает main через
# `outage_cost` по фактам `probe.assess_regression`.
REGRESSION_COST = 120.0

# Цена выкаченной, но падающей ручки (5xx) и недоступного блока — в клиентах.
# Бьёт независимо от стадии фичи: «сделано криво» отпугивает клиентов, тогда как
# «ещё не сделано» (404) — нет (за это `outage_cost` не штрафует, см. probe).
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

# Стадии жизни добавленной фичи (классифицирует judge.classify_feature).
FEATURE_STATES = ("absent", "partial", "working")


def rubric_total(scores: list[int]) -> int:
    """Сумма баллов рубрики, зажатая в [0, RUBRIC_MAX]. Сводка для табло."""
    return max(0, min(RUBRIC_MAX, sum(int(x) for x in scores)))


def convenience_factor(convenience: float) -> float:
    """Удобство 0–10 → множитель ценности рабочей фичи.

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
    в `probe.assess_regression`; здесь только перевод количеств в клиентов.
    """
    return (UNREACHABLE_BLOCK_COST * max(0, int(unreachable_blocks))
            + BROKEN_ENDPOINT_COST * max(0, int(broken_endpoints)))


def cross_block_mult(cross_block: int) -> float:
    """Бонус-множитель за сквозную фичу через все три блока.

    cross_block ∈ {0,1,2}: 0 — фича в одном блоке (множитель 1.0), 2 — фича
    согласованно задействует все три блока (множитель 1.0 + CROSS_BLOCK_BONUS).
    Сквозность мягкая: это бонус, а не обязательное условие ценности.
    """
    cb = max(0, min(2, int(cross_block)))
    return 1.0 + CROSS_BLOCK_BONUS * (cb / 2.0)


def feature_value(axes: tuple[int, int, int], cross_block: int,
                  convenience: float, feature_state: str, *,
                  outage_penalty: float = 0.0,
                  feature_live: bool | None = None) -> float:
    """Ценность банка для клиента в «клиентах» — куда тянет клиентскую базу.

    Кредит больше не привилегирован: ценность даёт ЛЮБАЯ добавленная фича. Оси
    `axes = (new_functionality, client_value, completeness)` ∈ {0,1,2} (сумма
    0..6) учитываются, когда фича хотя бы частично доступна клиенту
    (`feature_state in (working, partial)`); их вес умножается на фактор
    удобства и бонус за сквозную работу. При `feature_state == "absent"`
    ценности нет — учитываются только штрафы.

    `feature_live` — якорь работоспособности новой ручки (см.
    `feature_probe.assess_feature_liveness`): `False` означает «фичу дёрнули, она
    доказанно НЕ работает» (витрина без функциональности) — тогда оси НЕ дают
    ценности, как при `absent`, сколько бы LLM ни поставил по тексту контракта.
    `True`/`None` (работает либо проверить не удалось) — поведение прежнее: не
    наказываем за то, что не смогли проверить.

    Регрессия базовых функций (`outage_penalty` — цена сломанных/недоступных
    ручек, см. `outage_cost`) бьёт ВСЕГДА, на любой стадии фичи — это про
    работоспособность банка, не про стадию фичи.
    """
    a = [max(0, min(2, int(x))) for x in axes]
    value = 0.0
    feature_counts = (feature_state in ("working", "partial")
                      and feature_live is not False)
    if feature_counts:
        base = AXIS_WEIGHT * sum(a)           # 0..180
        value = base * convenience_factor(convenience) * cross_block_mult(cross_block)
    value -= max(0.0, float(outage_penalty))
    return value


def dead_feature_cost(status: int | None) -> float:
    """Цена доказанно мёртвой, но клиенту видимой новой ручки — в «клиентах».

    Применяется только когда liveness-проверка дала вердикт «не работает»
    (см. `feature_probe`). `5xx` (выкачено и падает на глазах клиента) штрафуем
    как «сделано криво» — клиент попробовал и обманулся. Чистый `404` (ручки
    нет вовсе) не штрафуем: действия клиент не получает, но и обмана нет — ноль
    ценности от осей уже отражает это (см. `feature_value`).
    """
    if status is not None and 500 <= int(status) <= 599:
        return BROKEN_ENDPOINT_COST
    return 0.0


def compute_commit_round(value_now: float, value_prev: float,
                         client_base: float, *,
                         stationary_flow: float = STATIONARY_FLOW) -> dict:
    """Коммит-раунд: дельта = изменение ценности (+ опциональный поток).

    Телескопическая часть `value_now - value_prev` — единственный штатный
    источник дельты (значения ценности считает `feature_value`): реагирует на
    реальное улучшение/ухудшение банка, а на коммите без изменений даёт 0 (база
    стоит). Доля `stationary_flow * value_now`
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
