# Деплой блоков на Render

Этот документ нужен и людям (Виталий, Нерсес, команда поддержки), и агентам топов. Объясняет **как блок попадает в продакшен** после `git push`. Каждый агент должен это понимать чтобы не паниковать когда видит в `dashboard/events.jsonl` запись «deploy started».

## Где живёт каждый блок

| Блок | Владелец | URL Render | Папка в репо |
|---|---|---|---|
| CEO Office | Сергей Монин | `https://raif-ceo.onrender.com` | `/ceo/` |
| CIB | Никита Патрахин | `https://raif-cib.onrender.com` | `/cib/` |
| Розница | Иван Курочкин | `https://raif-retail.onrender.com` | `/retail/` |
| IT / Платформа | Александр Ложечкин | `https://raif-it.onrender.com` | `/it/` |
| Финансы и Опс | Герт Хебенштрайт | `https://raif-finance.onrender.com` | `/finance/` |
| Риски | Роланд Васс | `https://raif-risk.onrender.com` | `/risk/` |

Соседи общаются между собой через `httpx.get(NEIGHBOR_<BLOCK>/...)` — Render инжектит `NEIGHBOR_*` env-переменные в каждый блок (см. `render.yaml`).

## Как работает деплой

Деплой автоматический. Никто ничего не нажимает руками.

```
git push origin main
        ↓
GitHub Action 'Deploy services via Render Deploy Hooks'
        ↓
        смотрит git diff → определяет какие папки тронуты
        ↓
        дёргает curl POST на Render Deploy Hook каждого тронутого сервиса
        ↓
Render собирает Docker-образ из изменённой папки
        ↓
        Через 2-4 минуты сервис обновлён, /openapi.json показывает новые ручки
```

### Что триггерит деплой какого блока

| Изменения в | Деплоятся блоки |
|---|---|
| `ceo/**` | только CEO |
| `cib/**` | только CIB |
| `retail/**` | только Retail |
| `it/**` | только IT |
| `finance/**` | только Finance |
| `risk/**` | только Risk |
| `contracts/**` | **все 6** (общая зона) |
| `render.yaml` | **все 6** (env-переменные могли поменяться) |
| `dashboard/`, `cases/`, `INBOX/`, `.github/` | **никто** (на сервисы не влияют) |

То есть когда CIB-агент пишет код — пересобирается только `raif-cib`, остальные пять продолжают работать без задержек.

## Action и его настройка

Action лежит в `.github/workflows/deploy-render.yml`. Триггеры:

- `push: branches: [main]` — основной канал.
- `workflow_dispatch` с input `services` — ручной запуск через Actions UI: можно выбрать конкретные блоки (`cib,risk`) или `all`.

В репо настроены 6 секретов с URL-ами Deploy Hook-ов, по одному на блок:
- `RENDER_HOOK_CEO`
- `RENDER_HOOK_CIB`
- `RENDER_HOOK_RETAIL`
- `RENDER_HOOK_IT`
- `RENDER_HOOK_FINANCE`
- `RENDER_HOOK_RISK`

Если кто-то из секретов не задан — Action печатает warning и пропускает этот блок, но не падает целиком.

## Что делает агент топа после push

Технически агенту не надо делать **ничего** — деплой автоматический. Но для хорошего UX полезно:

1. После `git push` сказать пользователю: «Сделал коммит, твой блок сейчас передеплоивается, 2-3 минуты».
2. Запустить `python dashboard/wait_deploy.py raif-<твой-блок>` — это пингует `/health` блока пока не получит 200 OK или таймаут. Когда получил — означает что свежая версия уже отвечает.
3. Сказать пользователю: «Готово, твой блок обновился. Открой `https://raif-<блок>.onrender.com/docs` — там новые ручки».
4. Допиши одну строчку в `dashboard/events.jsonl` (если хочется чтобы pulse мелькнул на воркшоп-табло):
   ```bash
   echo '{"block":"<блок>","kind":"deploy","text":"раскатил <что>","ts":'$(date +%s)'}' >> dashboard/events.jsonl
   ```

## Что делать если деплой не подхватился

Большая редкость, но бывает. Признаки: после push прошло 5+ минут, `/openapi.json` показывает старые ручки. Действия:

1. Открыть Actions → последний прогон `Deploy services via Render Deploy Hooks` → есть ли warning о незаданном секрете? Если да — добавить.
2. Открыть `dashboard.render.com` → сервис → вкладка **Events** → проверить что Render зафиксировал новый build. Если нет — webhook не дошёл, нажать **Manual Deploy → Deploy latest commit**.
3. Если упала **сборка** на Render — Events покажет stack trace. Пара частых причин: синтаксическая ошибка в `main.py`, пакет в `pyproject.toml` не нашёлся в индексе.

## OpenAI API key для IT

Для работы `/llm/ask` IT-сервису нужен `OPENAI_API_KEY`. Ставится один раз вручную:

`dashboard.render.com` → `raif-it` → Settings → **Environment** → найти переменную `OPENAI_API_KEY` (есть, пустая) → Edit → вставить `sk-...` → Save.

Render автоматически перезапустит контейнер за ~30 секунд. После этого `curl https://raif-it.onrender.com/llm/status` должен вернуть `configured: true`.

## Почему именно Deploy Hooks, а не встроенный auto-deploy

У нас оба канала включены:
- Render Blueprint в `render.yaml` имеет `autoDeploy: true` — основной канал.
- Action `deploy-render.yml` через хуки — запасной канал.

Опытным путём встроенный auto-deploy у Render иногда теряет webhook от GitHub — после применения Blueprint webhook рассыпается. Хуки — direct call от GitHub Action, мимо webhook-инфраструктуры. Это надёжнее. Один лишний запрос Action в секунду не страшен.
