# Деплой на Render

Как код попадает в продакшен после `git push`. Документ для организаторов
и для агентов команд.

## Три сервиса

| Сервис | Папка в репо | URL |
|---|---|---|
| `raif-team-a` | `team_a/` | `https://raif-team-a.onrender.com` |
| `raif-team-b` | `team_b/` | `https://raif-team-b.onrender.com` |
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
| `team_a/**` | `raif-team-a` |
| `team_b/**` | `raif-team-b` |
| `simulator/**` | `raif-simulator` |
| `seed/**` | оба банка |
| `render.yaml` | все три |
| `tasks/`, `docs/`, `.github/` | ничего |

## Деплой-хуки

Action — `.github/workflows/deploy-render.yml`. Триггеры: `push` в `main`
и ручной `workflow_dispatch` (выбор сервисов). В GitHub нужны три секрета
(Settings → Secrets and variables → Actions):

- `RENDER_HOOK_TEAM_A`
- `RENDER_HOOK_TEAM_B`
- `RENDER_HOOK_SIMULATOR`

URL хука каждого сервиса: Render → сервис → Settings → Deploy Hook.
Если секрет не задан — Action печатает warning и пропускает сервис, не падая.

## Переменные окружения

Env-группа `ai-workshop-shared` (задаётся один раз в Render UI):

- `OPENAI_API_KEY` — ключ для LLM (банкам — объяснение отказа по кредиту,
  симулятору — судья). `OPENAI_BASE_URL`, `OPENAI_MODEL` — со значениями
  по умолчанию в `render.yaml`.
- `ADMIN_TOKEN` — токен для `/admin/*` симулятора.

Пер-сервис (в `render.yaml`):

- банки — `TEAM_NAME` (`team_a` / `team_b`);
- симулятор — `BANK_A_URL`, `BANK_B_URL`, `ACTIVE_TASK`, `DATABASE_URL`
  (из БД `raif-workshop-db`).

`RENDER_GIT_COMMIT` Render подставляет сам — банк отдаёт его в `/health`,
по нему симулятор ловит факт деплоя.

## Переоценка после деплоя

Симулятор **не дёргается** из GitHub Action. Он сам, pull-моделью, раз в
~30 секунд опрашивает `/health` банков; увидел новый git-коммит → снимает
probe и пересчитывает клиентскую базу. Так надёжнее: free-инстансы Render
просыпаются с холодного старта 20-30 секунд, и push-триггер ловил бы старую
версию.

## Если деплой не подхватился

1. GitHub → Actions → последний прогон → нет ли warning о незаданном секрете.
2. Render → сервис → Events → зафиксирован ли новый build; если нет —
   Manual Deploy → Deploy latest commit.
3. Упала сборка — Events покажет stack trace (частые причины: синтаксис
   в `main.py`, пакет не нашёлся).

## Free-план

Все сервисы и Postgres — free. Инстансы засыпают после 15 минут простоя
(первый запрос +20-30 секунд). Free Postgres живёт 90 дней — создавать
свежим перед воркшопом.
