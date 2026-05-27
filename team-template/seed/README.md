# Стартовая среда банка

Здесь лежат синтетические данные, на которые опираются все кейсы воркшопа: 500 клиентов, ~5000 транзакций, ~1300 записей кредитной истории.

## Файлы

| Файл | Что внутри |
|---|---|
| `clients.jsonl` | 500 клиентов с полями `id`, `name`, `age`, `segment`, `income_rub`, `balance_rub`, `products`, `risk_score`, `has_overdue_history`, `joined_at` |
| `transactions.jsonl` | 5000 транзакций с `client_id`, `type`, `amount_rub`, `ts` |
| `credit_history.jsonl` | ~1300 кредитных записей: `client_id`, `product`, `principal_rub`, `term_months`, `rate_pct`, `status`, `overdue_days_max` |

Распределение по сегментам:
- `mass` — 280 клиентов (масовый сегмент)
- `mass_affluent` — 129
- `premium` — 48 (баланс 1.5–8 млн ₽)
- `private` — 26 (баланс 8–50 млн ₽)
- `sme` — 17 (юрлица)

## Регенерация

Если данные потерялись или нужна другая база:

```bash
python cases/_seed/make_seed.py
```

`random.seed=42` зашит — у разных топов на разных ноутах будет одинаковая стартовая картина.

## Загрузка в блоки

Перед началом воркшопа кто-то один (обычно команда поддержки) запускает:

```bash
python cases/_seed/load_into_blocks.py
```

Скрипт делает HTTP-запросы к стартовым ручкам всех 6 блоков и подкачивает им данные:
- Retail получает `clients.jsonl` и `transactions.jsonl`.
- Risk получает `clients.jsonl` (для скоринга) и `credit_history.jsonl`.
- Finance получает balance из `clients.jsonl`.
- CIB / IT / CEO ничего не загружают — у них своя стартовая логика.

После загрузки каждый блок отвечает на свои базовые ручки реальными данными из seed (`/clients`, `/score/{id}`, `/balance/{id}` и т.д.).

## Как агенту работать с этими данными

Агент топа может в любой момент прочитать seed напрямую:

```python
import json
clients = [json.loads(line) for line in open('cases/_seed/clients.jsonl')]
premium = [c for c in clients if c['segment'] == 'premium']
```

Или через ручку соседа:

```python
import httpx, os
r = httpx.get(f"{os.environ['NEIGHBOR_RETAIL']}/clients?segment=premium")
```

Любой подход рабочий. Ручка соседа предпочтительнее — она показывает что между блоками идёт реальное общение, и это видно на live-табло воркшопа.
