# Редизайн: каждая команда — три блока (retail · cib · backend)

- **Дата:** 2026-05-17
- **Автор:** Claude (по заданию Виталия Ерохина)
- **Статус:** дизайн одобрен в чате, ожидает финального вычитывания спеки
- **Базируется на:** одно-банковой версии редизайна (`main` @ `1189e9a`, тег `workshop-baseline`)

## 1. Контекст и проблема

После первого редизайна у каждой команды один банк-монолит (`team_a/`, `team_b/`) —
FastAPI-приложение с UI, данными и логикой в одном сервисе. Симулятор оценивает
этот один сервис.

Новое требование: внутри команды три человека, и у каждого своя зона. Команда
становится не одним банком, а **тремя сервисами-блоками**: `retail`, `cib`,
`backend`. За каждый отвечает один из трёх участников. Фича готова, только когда
все трое сделали свою часть и состыковали блоки. Симулятор оценивает команду
интегрально — по всем трём блокам.

## 2. Цели и не-цели

### Цели

1. Каждая команда — три сервиса: `retail`, `cib`, `backend`, по одному на участника.
2. Блоки идентичны между командами на старте (`team_a/retail` == `team_b/retail` и т.д.).
3. На старте работает только база (переводы end-to-end через retail↔backend);
   кредитная фича отсутствует во всех трёх блоках — это задача воркшопа.
4. Симулятор оценивает команду интегрально: probe всех трёх блоков, счёт — сумма
   по блокам.
5. Онбординг и права под пару (команда, блок); агент видит свою команду целиком,
   правит только свой блок, чужую команду не видит.

### Не-цели (YAGNI)

- Корпоративные счета и корп-платежи в cib (старый CIB-функционал) — не нужны,
  cib для задачи 1 это сервис кредитного решения.
- Восстановление старого `cib/`-кода — cib пишется с нуля под новый контракт.
- Persistence банков в БД — блоки in-memory на seed (как и раньше).
- probe и рубрика задачи 2 («Инвестиции») — фаст-фоллоу.

## 3. Структура репозитория

```
team_a/retail/    team_a/cib/    team_a/backend/
team_b/retail/    team_b/cib/    team_b/backend/
simulator/        — переписанный probe + scoring
seed/             — без изменений (clients, transactions, credit_history)
tasks/            — переписанные брифы
docs/superpowers/ — спеки и планы
render.yaml       — 7 сервисов
docker-compose.yml
.github/workflows/deploy-render.yml
.claude/templates/ — 6 шаблонов permissions (команда × блок)
CLAUDE.md, TEAM.md, RULES.md, README.md, ORGANIZER.md, DEPLOY.md
tools/cowork-onboard.py, tools/bootstrap/
```

Каждый блок — отдельный сервис: `Dockerfile`, `pyproject.toml`, `src/`.

## 4. Блоки

### 4.1 backend — ядро данных

Владеет данными банка. UI нет. In-memory seed из `seed/*.jsonl`.

API на старте:
- `GET /health` → `{status, team, block:"backend", commit, clients_loaded}`
- `GET /clients?limit=&segment=&has_overdue=&min_income=` → `{total, items}`
- `GET /clients/{id}` → объект клиента (404 если нет)
- `GET /transactions/{client_id}?limit=` → `{total, items}`
- `POST /api/transfer` `{from_client_id, to, amount_rub}` → `{status, kind, new_balance_rub, ...}`

API, который добавляет участник в рамках задачи:
- `POST /credit-applications` `{client_id, amount_rub, term_months, decision, ...}` → сохранённая запись
- `GET /credit-applications` → `{total, items}`

Это извлечённый слой данных нынешнего банка-монолита: загрузка seed, клиенты,
транзакции, перевод. Кредитного хранилища на старте нет.

### 4.2 cib — корпоративный блок и бизнес-логика

Сервис кредитного решения и каталог продуктов. Минимальный статус-UI (список
продуктов). In-memory. Ходит в backend по `BACKEND_URL`.

API на старте:
- `GET /health` → `{status, team, block:"cib", commit}`
- `GET /products` → `{items}` — базовый каталог (карта, депозит — без кредитного продукта)
- `GET /` → минимальная статус-страница

API, который добавляет участник в рамках задачи:
- `POST /credit/decide` `{client_id, amount_rub, term_months}` → `{decision:"approved"|"rejected", explanation, ...}`.
  Внутри: `GET {BACKEND_URL}/clients/{client_id}` за данными клиента, расчёт
  решения по данным клиента и правилам продукта, человеческое объяснение через LLM.

В блоке лежит хелпер `llm.py` (`ask_llm`) — для объяснения решения.

### 4.3 retail — клиентский мобильный банк

Телефонный UI с вкладками. Данных у себя не держит. Ходит в backend по
`BACKEND_URL` и в cib по `CIB_URL`. UI — статика из `src/static/index.html`
(перенесённый UI нынешнего банка).

API на старте:
- `GET /` → мобильный UI (вкладка «Переводы»)
- `GET /health` → `{status, team, block:"retail", commit}`
- `GET /clients?limit=` → проксирует `backend/clients`
- `GET /transactions/{id}` → проксирует `backend/transactions`
- `POST /api/transfer` → проксирует `backend/api/transfer`

API, который добавляет участник в рамках задачи:
- `POST /api/credit-apply` `{client_id, amount_rub, term_months}` → оркестрация:
  `POST {CIB_URL}/credit/decide` → затем `POST {BACKEND_URL}/credit-applications`
  → возврат `{decision, explanation}` в UI
- вкладка «Кредиты» в UI

## 5. Связи между блоками

Выбор пользователя — оба независимо ходят в backend:

- `retail → backend` — клиенты, транзакции, переводы, хранение заявок.
- `retail → cib` — кредитное решение.
- `cib → backend` — данные клиента для решения.
- `backend` — никого не зовёт, это ядро.

Поток оформления кредита (фича — задача воркшопа):
1. retail: вкладка «Кредиты», форма (сумма + срок), submit.
2. retail → `POST cib/credit/decide`.
3. cib → `GET backend/clients/{id}` — данные клиента.
4. cib считает решение и формирует объяснение, возвращает retail.
5. retail → `POST backend/credit-applications` — сохранить заявку.
6. retail показывает решение клиенту.

Фича готова, только когда все три блока сделали свою часть — «нужны все трое».

URL-ы соседей блок получает из env: retail — `BACKEND_URL`, `CIB_URL`; cib —
`BACKEND_URL`. Локально — адреса docker-compose-сервисов; на Render — публичные
URL сервисов своей команды.

## 6. Что в блоках на старте

Блоки идентичны между командами. В каждом — только база:
- backend: клиенты, транзакции, перевод. Кредитного хранилища нет.
- retail: мобильный UI, вкладка «Переводы», проксирование в backend. Вкладки
  «Кредиты» и `/api/credit-apply` нет.
- cib: `/health`, `/products` (без кредитного продукта), статус-UI. `/credit/decide` нет.

Сквозной переводный поток (UI retail → backend) работает с первой минуты — это
рабочий пример интеграции, который команда расширяет. Вся кредитная фича через
три блока — это задача.

## 7. Симулятор

Симулятор опрашивает **шесть** банк-сервисов (2 команды × 3 блока). `BANK_URLS`
— вложенная структура `{team: {retail, cib, backend}}`.

### 7.1 Probe — закрытый список проверок (задача «Кредиты»)

Фиксированные клиенты из `seed/clients.jsonl`: сильный `c-01394` (premium, доход
589 545 ₽), слабый `c-01434` (mass, доход 40 358 ₽, просрочки).

**backend** (по URL backend команды):
- P-B1 `GET /health` → reachable, commit.
- P-B2 `GET /clients/c-01394` → 200, объект клиента.
- P-B3 `POST /credit-applications` `{client_id:"c-01394", amount_rub:300000, term_months:12, decision:"approved"}` → статус 200/201 (не 404/501).
- P-B4 `GET /credit-applications` → 200, в ответе список.

**cib** (по URL cib команды):
- P-C1 `GET /health` → reachable, commit.
- P-C2 `GET /products` → 200; есть продукт с признаком кредита (в названии/виде «кредит»/«credit»).
- P-C3 `POST /credit/decide` `{client_id:"c-01394", amount_rub:300000, term_months:12}` → 200 (не 501/404); латентность; в ответе вердикт.
- P-C4 `POST /credit/decide` для слабого `c-01434` → вердикт отличается от сильного (решение опирается на данные клиента из backend).

**retail** (по URL retail команды):
- P-R1 `GET /health` → reachable, commit.
- P-R2 `GET /` → HTML содержит (регистронезависимо) «кредит»; содержит «перевод».
- P-R3 `POST /api/credit-apply` `{client_id:"c-01394", ...}` → 200, сквозное решение (проходит цепочку retail→cib→backend).
- P-R4 `POST /api/credit-apply` для слабого `c-01434` → отказ с непустым человеческим объяснением (> 40 символов).
- P-R5 `POST /api/transfer` между двумя клиентами из `GET /clients?limit=2` → 200 (регрессия переводов).

Снапшот команды — `{team, blocks:{backend:{reachable,commit,checks}, cib:{...}, retail:{...}}}`.

### 7.2 Рубрика и формула

10 критериев, по 0–2 балла, максимум 20:

| Блок | Критерий |
|---|---|
| backend | C1. Отдаёт данные клиента (`/clients/{id}`) |
| backend | C2. Принимает заявку на кредит (`POST /credit-applications` отвечает 200) |
| backend | C3. Отдаёт список заявок (`GET /credit-applications`) |
| cib | C4. В каталоге есть кредитный продукт |
| cib | C5. `/credit/decide` работает (200, не 501) |
| cib | C6. Решение опирается на данные клиента (сильный/слабый — разный вердикт) |
| retail | C7. Вкладка «Кредиты» присутствует в UI |
| retail | C8. Сквозная подача заявки доходит до реального решения |
| retail | C9. Отказ сопровождается человеческим объяснением |
| retail | C10. Нет регрессии — переводы работают |

LLM-судья одним вызовом оценивает обе команды по 10 критериям; `temperature=0`.
Скриптовый fallback выводит баллы механически из probe-checks. Счёт команды —
сумма 10 баллов.

Формула баллы→клиенты — как в одно-банковой версии, но `RUBRIC_MAX = 20`:
`B0 = 500`, `GAIN = 0.6`, `target = round(B0 × (1 + GAIN × clamp((S − S_base)/20, −1, 1)))`,
`delta = target − client_base`. Дельта 0, если рубрика не изменилась.
`S_base` симулятор замеряет на старте по нетронутым блокам (ожидаемо ≈ 4:
backend отдаёт клиентов C1=2, переводы работают C10=2, остальное 0). Пример:
команда полностью собрала кредитную фичу — `S=20`, `I=(20−4)/20=0.8`,
`target=round(500×1.48)=740`, дельта +240 клиентов.

### 7.3 Триггер, хранение, табло

- Pull-цикл раз в ~30 с опрашивает `/health` всех 6 блоков; новый git-коммит у
  любого блока команды → раунд оценки (probe всех 6, один вызов судьи).
- Postgres: `sim_state` (счёт команды) и `sim_events` (журнал). Структура — как в
  одно-банковой версии, плюс в событии — разбивка баллов по блокам.
- Табло: две команды, клиентская база, лента событий; в обосновании события
  видно, какой блок продвинул или сломал команду.
- `POST /admin/evaluate`, `POST /admin/reset` — токен `ADMIN_TOKEN`.

## 8. Деплой

Семь сервисов на Render: `raif-a-retail`, `raif-a-cib`, `raif-a-backend`,
`raif-b-retail`, `raif-b-cib`, `raif-b-backend`, `raif-simulator`. Плюс Postgres
`raif-workshop-db` (только симулятору).

`render.yaml` инжектит каждому блоку URL-ы соседей своей команды:
- `raif-a-retail` ← `BACKEND_URL=https://raif-a-backend.onrender.com`, `CIB_URL=https://raif-a-cib.onrender.com`
- `raif-a-cib` ← `BACKEND_URL=https://raif-a-backend.onrender.com`
- аналогично для команды B.

Env-группа `ai-workshop-shared`: `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`OPENAI_MODEL`, `ADMIN_TOKEN`. `DATABASE_URL` симулятору — из БД.

GitHub Action: diff папок → деплой-хук тронутого блока. Секреты деплой-хуков —
семь: `RENDER_HOOK_A_RETAIL`, `RENDER_HOOK_A_CIB`, `RENDER_HOOK_A_BACKEND`,
`RENDER_HOOK_B_RETAIL`, `RENDER_HOOK_B_CIB`, `RENDER_HOOK_B_BACKEND`,
`RENDER_HOOK_SIMULATOR`.

Локально (docker-compose): порты — simulator 8000; team_a retail/cib/backend
8001/8002/8003; team_b 8011/8012/8013.

## 9. Онбординг и права

Info-файл (`.git/raif-workshop-info`) теперь содержит два поля: `WORKSHOP_TEAM`
(`team_a`/`team_b`) и `WORKSHOP_BLOCK` (`retail`/`cib`/`backend`).
`tools/cowork-onboard.py` читает оба, печатает оба в сводке.

`.claude/templates/` — шесть шаблонов permissions: `settings-team_a-retail.json`,
`-team_a-cib.json`, `-team_a-backend.json`, и три для `team_b`. Каждый:
- `allow` Edit/Write только своего блока (`team_a/retail/**`);
- `allow` Read всей своей команды (`team_a/**`) — нужно знать API соседних блоков;
- `deny` Read/Edit/Write другой команды (`team_b/**`) полностью;
- `deny` Edit `simulator/`, `seed/`, `render.yaml`, корневые файлы.

`CLAUDE.md` — онбординг: Шаг 0 даёт `WORKSHOP_TEAM` + `WORKSHOP_BLOCK`; Шаг 3 —
сопоставление с парой (команда, блок); Шаг 4 — копирование
`settings-<team>-<block>.json`; Шаг 5 — бриф своего блока в задаче.

`tools/bootstrap/raif-workshop-setup.cmd` — `$Members` получает поле `Block`,
info-файл пишет `WORKSHOP_BLOCK`. Распределение участников по (команда, блок) —
организатор перед воркшопом; в репозитории дефолтная раскладка.

## 10. Брифы задач

`tasks/task_01_credit.md` переписывается: общая цель плюс три части —
- backend: завести хранилище заявок на кредит (`POST/GET /credit-applications`);
- cib: построить логику решения (`POST /credit/decide` — данные клиента из
  backend, вердикт, человеческое объяснение), добавить кредитный продукт;
- retail: вкладка «Кредиты», форма, `/api/credit-apply` — оркестрация cib и backend.
Подчёркнуто: фича готова только когда три части состыкованы.

`tasks/task_02_invest.md` — аналогично по трём блокам (фаст-фоллоу для probe).

## 11. Версионирование

Тег `workshop-baseline` сейчас указывает на одно-банковую версию (`1189e9a`) —
оставляем как точку отката на неё. После слияния трёхблочной версии в `main`
ставится новый тег `workshop-baseline-v2` на её коммит.

## 12. План по фазам

- **Фаза 0** — структура: `team_a/{retail,cib,backend}` ×2 из нынешнего банка.
- **Фаза 1** — backend: извлечь слой данных, `/health`, без UI.
- **Фаза 2** — retail: UI + проксирование в backend, переводы end-to-end.
- **Фаза 3** — cib: новый сервис, `/health`, `/products`, статус-UI, `llm.py`.
- **Фаза 4** — симулятор: probe 6 сервисов, рубрика 10 критериев, интегральный счёт.
- **Фаза 5** — инфраструктура: `render.yaml` (7 сервисов), Action, docker-compose.
- **Фаза 6** — онбординг: 6 шаблонов permissions, cowork-onboard и bootstrap
  (TEAM+BLOCK), `CLAUDE.md`, `TEAM.md`, `RULES.md`.
- **Фаза 7** — брифы задач.
- **Фаза 8** — тест-прогон (docker-compose / uvicorn), тег `workshop-baseline-v2`.

## 13. Ручные шаги организатора

- Render: удалить старые сервисы (`raif-team-a`, `raif-team-b`, `raif-simulator`),
  применить новый Blueprint (7 сервисов + Postgres), задать `OPENAI_API_KEY` и
  `ADMIN_TOKEN` в env-группе, завести 7 секретов деплой-хуков на GitHub.
- Распределить 6 участников по парам (команда, блок) — `TEAM.md` и bootstrap.
- Прогнать обновлённый bootstrap на ноутбуках.

## 14. Риски

- **Render free-план — 7 сервисов.** Бесплатный план Render ограничен по числу
  одновременных web-сервисов. Семь могут упереться в лимит. Организатор должен
  проверить лимит в своём аккаунте Render до применения Blueprint. Запасной
  вариант — объединить `cib` и `backend` в один сервис (5 сервисов вместо 7),
  оставив `retail` отдельно; «один сервис, три папки» как fallback не
  рассматривается — он размывает урок интеграции.
- **Холодный старт Render free** — probe и pull-цикл терпят таймауты и 502/503.
- **Цепочка вызовов** — если backend недоступен, ломаются cib и retail; probe
  это видит и обнуляет соответствующие критерии, симулятор не падает.
- **Дедлайн** — фазовый план: Фазы 0–7 = рабочее состояние, Фаза 8 — приёмка;
  probe задачи 2 — фаст-фоллоу.
