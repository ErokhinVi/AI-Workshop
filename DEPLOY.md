# Деплой на Render

Как код попадает в продакшен после `git push`. Документ для организаторов
и для агентов команд.

## Семь сервисов

| Сервис | Папка в репо | URL |
|---|---|---|
| `raif-a-backend` | `team_a/backend/` | `https://raif-a-backend.onrender.com` |
| `raif-a-cib` | `team_a/cib/` | `https://raif-a-cib.onrender.com` |
| `raif-a-retail` | `team_a/retail/` | `https://raif-a-retail.onrender.com` |
| `raif-b-backend` | `team_b/backend/` | `https://raif-b-backend.onrender.com` |
| `raif-b-cib` | `team_b/cib/` | `https://raif-b-cib.onrender.com` |
| `raif-b-retail` | `team_b/retail/` | `https://raif-b-retail.onrender.com` |
| `raif-simulator` | `simulator/` | `https://raif-simulator.onrender.com` |

Плюс Postgres `raif-workshop-db` (free) — им пользуется только симулятор:
хранит клиентскую базу команд и журнал событий.

## Как работает деплой

```
git push origin main
        ↓
GitHub Action "Deploy services via Render Deploy Hooks"
        ↓
        git diff → какие папки тронуты
        ↓
        curl POST на Render Deploy Hook тронутого сервиса
        ↓
Render собирает Docker-образ из изменённой папки (~2-4 минуты)
```

| Изменения в | Деплоится |
|---|---|
| `team_a/backend/**` | `raif-a-backend` |
| `team_a/cib/**` | `raif-a-cib` |
| `team_a/retail/**` | `raif-a-retail` |
| `team_b/backend/**` | `raif-b-backend` |
| `team_b/cib/**` | `raif-b-cib` |
| `team_b/retail/**` | `raif-b-retail` |
| `simulator/**` | `raif-simulator` |
| `seed/**` | оба backend-блока |
| `render.yaml` | все семь |
| `tasks/`, `docs/`, `.github/` | ничего |

## Деплой-хуки

Action — `.github/workflows/deploy-render.yml`. Триггеры: `push` в `main`
и ручной `workflow_dispatch` (выбор сервисов через запятую или `all`).
В GitHub нужны семь секретов (Settings → Secrets and variables → Actions):

- `RENDER_HOOK_A_BACKEND`
- `RENDER_HOOK_A_CIB`
- `RENDER_HOOK_A_RETAIL`
- `RENDER_HOOK_B_BACKEND`
- `RENDER_HOOK_B_CIB`
- `RENDER_HOOK_B_RETAIL`
- `RENDER_HOOK_SIMULATOR`

URL хука каждого сервиса: Render → сервис → Settings → Deploy Hook.
Если секрет не задан — Action печатает warning и пропускает сервис, не падая.

## Переменные окружения

Env-группа `ai-workshop-shared` (задаётся один раз в Render UI):

- `OPENAI_API_KEY` — ключ для LLM (cib — объяснение отказа по кредиту,
  симулятору — судья). `OPENAI_BASE_URL`, `OPENAI_MODEL` — со значениями
  по умолчанию в `render.yaml`.
- `ADMIN_TOKEN` — токен для `/admin/*` симулятора.

Пер-сервис (в `render.yaml`):

- `backend`-блоки — `TEAM_NAME` (`team_a` / `team_b`);
- `cib`-блоки — `TEAM_NAME` и `BACKEND_URL` (адрес backend своей команды);
- `retail`-блоки — `TEAM_NAME`, `BACKEND_URL` и `CIB_URL`;
- симулятор — шесть `*_URL` (`A_BACKEND_URL`, `A_CIB_URL`, `A_RETAIL_URL`
  и три для команды B), `ACTIVE_TASK`, `DATABASE_URL` (из БД
  `raif-workshop-db`).

`RENDER_GIT_COMMIT` Render подставляет сам — блок отдаёт его в `/health`,
по нему симулятор ловит факт деплоя.

## Переоценка после деплоя

Симулятор **не дёргается** из GitHub Action. Он сам, pull-моделью, раз в
~30 секунд опрашивает `/health` всех шести блоков; увидел новый git-коммит
любого блока команды → снимает probe трёх блоков и пересчитывает клиентскую
базу команды. Так надёжнее: free-инстансы Render просыпаются с холодного
старта 20-30 секунд, и push-триггер ловил бы старую версию.

## Если деплой не подхватился

1. GitHub → Actions → последний прогон → нет ли warning о незаданном секрете.
2. Render → сервис → Events → зафиксирован ли новый build; если нет —
   Manual Deploy → Deploy latest commit.
3. Упала сборка — Events покажет stack trace (частые причины: синтаксис
   в `main.py`, пакет не нашёлся).

## Free-план

Все сервисы и Postgres — free. Инстансы засыпают после 15 минут простоя
(первый запрос +20-30 секунд). Free Postgres живёт 90 дней — создавать
свежим перед воркшопом. Семь web-сервисов могут упереться в лимит free-плана
по числу одновременных web-сервисов — см. раздел про риск в `ORGANIZER.md`.
