# Общий план команды A

## Цель

Выиграть игру в рамках правил судьи: удерживать кредитную функцию в состоянии
`working`, закрывать все 10 критериев рубрики и не ломать переводы.

Сейчас команда A уже достигала 20/20. Главный риск дальше — регрессия одного из
трёх блоков или потеря содержательного объяснения отказа.

## Как пользоваться этим планом

Это общий план для всех участников команды A: backend, CIB и retail. Даже если
файл лежит в папке CIB, он описывает командный результат и договорённости между
тремя блоками.

Правила работы:

- каждый участник меняет только свой блок;
- перед изменениями сверяется с разделом своего блока и разделом проверки;
- если меняется контракт между блоками, обновляется этот файл;
- после значимого изменения команда проверяет табло и быстрые curl-проверки
  ниже;
- максимум держится только когда все три блока одновременно проходят критерии
  судьи.

## Как судья проверяет команду

Судья смотрит на три блока вместе:

- backend хранит клиентов и заявки;
- CIB отдаёт продукт и кредитное решение;
- retail проводит клиентский путь и сохраняет заявку.

Проверочные клиенты:

- сильный клиент: `c-01394`, сумма 300000, срок 12 месяцев -> одобрение;
- рискованный клиент: `c-01434`, сумма 900000, срок 6 месяцев -> отказ или
  безопасное встречное предложение с длинным объяснением.

Критерии на максимум:

- backend: `GET /clients/c-01394` работает;
- backend: `POST /credit-applications` принимает заявку;
- backend: `GET /credit-applications` отдаёт список с `items`;
- CIB: `GET /products` содержит кредитный продукт;
- CIB: `POST /credit/decide` отвечает 200;
- CIB: сильный и слабый клиенты получают разные вердикты;
- retail: на главной странице есть кредитный сценарий;
- retail: `POST /api/credit-apply` доходит до реального решения;
- retail: отказ содержит связное русское объяснение длиннее 120 символов;
- retail: `POST /api/transfer` продолжает работать.

## Backend

Backend должен отдавать профиль клиента:

`GET /clients/{client_id}`

Важные поля:

- `id`;
- `name`;
- `segment`;
- `income_rub`;
- `balance_rub`;
- `risk_score`;
- `has_overdue_history`;
- `credit_profile`, если доступен.

Backend должен хранить заявки:

`POST /credit-applications`

```json
{
  "client_id": "c-01394",
  "amount_rub": 300000,
  "term_months": 12,
  "status": "approved",
  "approved_amount_rub": 300000,
  "rate_pct": 15.2,
  "monthly_payment_rub": 27000,
  "explanation": "Доход и кредитный профиль позволяют предложить эту сумму."
}
```

`GET /credit-applications`

Должен возвращать:

```json
{
  "total": 1,
  "items": []
}
```

## CIB

CIB должен отдавать кредитный продукт:

`GET /products`

В продукте должен быть признак `credit` или `кредит`.

CIB должен принимать решение:

`POST /credit/decide`

```json
{
  "client_id": "c-01394",
  "amount_rub": 300000,
  "term_months": 12
}
```

Ответ должен содержать:

- `status` и `decision`: `approved`, `counter_offer` или `declined`;
- `approved_amount_rub`;
- `rate_pct`;
- `monthly_payment_rub`;
- `explanation`;
- `reason`;
- `title`;
- `next_step`;
- `client_snapshot`.

Для отказа слабому клиенту CIB должен возвращать длинное объяснение с цифрами:
доход, платёж, долговая нагрузка, просрочки, риск-скор и рекомендация.

## Retail

Retail должен:

- показывать кредитный сценарий в интерфейсе;
- принимать `term_months = 6`, потому что судья проверяет слабого клиента именно
  на 6 месяцев;
- отправлять заявку в CIB через `POST /credit/decide`;
- возвращать клиенту `explanation` из CIB без сокращения;
- сохранять итог в backend через `POST /credit-applications`;
- не ломать переводы.

## Быстрая проверка перед релизом

Проверить внешние адреса команды A:

```bash
curl -fsS https://raif-a-cib.onrender.com/products
curl -fsS -X POST https://raif-a-cib.onrender.com/credit/decide \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"c-01394","amount_rub":300000,"term_months":12}'
curl -fsS -X POST https://raif-a-cib.onrender.com/credit/decide \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"c-01434","amount_rub":900000,"term_months":6}'
curl -fsS -X POST https://raif-a-retail.onrender.com/api/credit-apply \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"c-01434","amount_rub":900000,"term_months":6}'
```

На табло должно быть:

- team A score: `20/20`;
- feature state: `working`;
- комментарий судьи: объяснения отказов содержательные и полезные.
