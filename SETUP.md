# SETUP.md: развернуть воркшоп с нуля

Инструкция для агента (Claude Code, Codex), который помогает организатору.
Человек заводит аккаунты, платит и выпускает ключи. Агент делает остальное и
проверяет каждый шаг. Шаги и проверки не пропускать: в день воркшопа авторов
рядом не будет, проводят ведущие по `RUNBOOK.md`.

Время: 2-3 часа работы плюс ожидание сборок. Начинать за неделю до
воркшопа, генеральный прогон за 1-2 дня.

## Что получится

- GitHub: этот оркестратор и по публичному репозиторию на команду.
- Render: Postgres, симулятор с табло и по три сервиса на команду (retail,
  cib, backend). 7 команд: 22 сервиса.
- Установщики ноутбуков: пара скриптов (macOS, Windows) на команду со своим
  deploy key. Лежат вне репозитория.
- Табло: `https://<RENDER_PREFIX>-simulator.onrender.com`.

Как это устроено внутри: `ORGANIZER.md`. День воркшопа: `RUNBOOK.md`.

## Правила для агента

- Аккаунты, карту, покупки и выпуск API-ключей делает человек. Ты говоришь,
  где нажать, и ждешь.
- Секреты живут в `~/AI-Workshop-secrets/<WORKSHOP_ID>/workshop.env` и в
  секретах GitHub. Не печатай их, не вставляй в команды и коммиты. Ключи
  человек вписывает в файл сам. Прислал ключ в чат: запиши в файл и напомни
  перевыпустить его после воркшопа.
- Скрипты `tools/setup` можно запускать повторно. Упало на середине: исправь
  причину и запусти еще раз, ничего не задвоится.
- `teardown`, `revoke` и сброс репозиториев команд только по прямой просьбе.
- Во время воркшопа в репозитории команд не пушить.

## Шаг 0. Спросить у человека

Ответы не придумывать, они уйдут в `tools/setup/teams.conf`.

1. Сколько команд. По умолчанию 7 команд по 3 человека. При 20 участниках
   в одной команде двое, третий блок берет ведущий стола.
2. Где будут репозитории: GitHub-аккаунт или организация. Нужны права
   создавать репозитории, deploy keys и секреты. В чужом личном аккаунте не
   выйдет: коллаборатор не может ставить секреты и deploy keys.
3. Кто платит за Render. Нужен аккаунт Render с картой организаторов, лучше
   отдельный под воркшоп: его API-ключ попадет в репозитории команд.
4. LLM для судьи и блока cib: OpenAI или любой OpenAI-совместимый провайдер
   (base URL, модель), ключ с балансом.
5. С какого компьютера работаем. Корпоративная сеть режет api.render.com и
   *.onrender.com. Это не мешает: команды Render пойдут через GitHub Actions.

## Шаг 1. Инструменты

```bash
gh auth login -s repo,workflow
tools/setup/doctor.sh
```

Нужны git, gh, python3 3.9+, ssh-keygen, perl, curl. `doctor.sh` ничего не
меняет. На этом шаге FAIL про workshop.env и warn про репозитории команд
нормальны.

## Шаг 2. Свой оркестратор

Репозитории будут не у ErokhinVi: скопировать оркестратор к себе. Копия, а
не fork: в форке Actions выключены до ручного включения.

```bash
gh repo create <OWNER>/AI-Workshop --public
git clone https://github.com/ErokhinVi/AI-Workshop.git
cd AI-Workshop
git remote rename origin upstream
git remote add origin https://github.com/<OWNER>/AI-Workshop.git
```

Уже работаешь в клоне: хватит двух последних команд `git remote`.

Поправить `tools/setup/teams.conf`:

- `GH_OWNER`: аккаунт или организация;
- `TEAMS`: `буква:репозиторий` на каждую команду;
- `WORKSHOP_ID`: имя прогона, например `offsite-2026`;
- `RENDER_PREFIX`: уникальный префикс сервисов, латиница и дефис;
- `LLM_BASE_URL`, `LLM_MODEL`: если LLM не OpenAI.

Закоммитить и запушить: workflow в GitHub Actions читает `teams.conf` из
репозитория.

```bash
git add tools/setup/teams.conf
git commit -m "Configure workshop"
git push -u origin main
```

Проверка: `doctor.sh` пишет, что оркестратор есть, публичный, Actions включены.

## Шаг 3. Секреты

```bash
python3 tools/setup/render_ops.py init-secrets
```

Создаст `workshop.env` с правами 600 и готовым `ADMIN_TOKEN` (пароль админки
табло). Два ключа человек вписывает сам:

- `RENDER_API_KEY`. На render.com: зарегистрироваться, в Workspace
  Settings, Billing добавить карту. Потом Account Settings, API Keys,
  Create API Key.
- `OPENAI_API_KEY`: ключ LLM-провайдера.

Проверка: в разделе «Секреты» у `doctor.sh` нет FAIL.

## Шаг 4. Репозитории команд

```bash
tools/setup/create-team-repos.sh --dry-run
tools/setup/create-team-repos.sh
tools/setup/sync-team-repos.sh
```

`create-team-repos.sh` создает недостающие публичные репозитории и снимает
архив со старых. `sync-team-repos.sh` заливает в каждый `team-template/`.
Деплоя пока нет: workflow в репозиториях команд пишет notice «нет переменной
RENDER_SID_*», это нормально.

Проверка: `doctor.sh` пишет «репозитории команд: все N есть, публичные».

## Шаг 5. Render

Последняя строка `doctor.sh` говорит, как отсюда ходить в Render. Дальше
`R <команда>` означает одно из двух:

- api.render.com доступен: `python3 tools/setup/render_ops.py <команда>`;
- недоступен: `tools/setup/ops.sh <команда>`. Скрипт выполняет ту же
  команду в GitHub Actions, ждет, печатает лог и скачивает
  `render-services.conf`. Перед первым запуском один раз:
  `tools/setup/github-access.sh ops-secrets`.

```bash
R check
R provision --dry-run
R provision
```

`check` показывает workspace. Их несколько: вписать нужный id в
`RENDER_OWNER_ID` и для ops.sh повторить `github-access.sh ops-secrets`.

`provision` создает Postgres, сервисы команд и симулятор, ставит им env,
запускает первые сборки и пишет `tools/setup/render-services.conf`.
Занимает 5-15 минут.

Если упал:

- код выхода 75: Render ограничил частоту запросов, повторить через
  указанное время;
- ошибка про repo: репозитории должны быть публичными; не помогло, человек
  подключает GitHub в Render (Account Settings, Git Credentials);
- ошибка про оплату или тариф: в workspace нет карты.
- `Hobby Tier is limited to 25 services`: в workspace есть чужие сервисы,
  выключенные тоже считаются. `R check` их перечислит. Удалить по прямой
  просьбе человека: `R drop <имена> --confirm DELETE` (через ops.sh:
  `drop DELETE <имена>`), либо перейти на тариф Professional.

Проверка через 10 минут:

```bash
R status
```

У всех сервисов `/health` 200, у симулятора «БД есть». Сборка упала
(`build_failed`): лог в Render, сервис, Logs.

## Шаг 6. Связать команды с Render

```bash
tools/setup/github-access.sh render
tools/setup/sync-team-repos.sh
```

`render` кладет в каждый репозиторий команды секрет `RENDER_API_KEY` и
переменные `RENDER_SID_*`: без них сохранения участников не деплоятся.
`sync-team-repos.sh` вписывает настоящие URL сервисов и табло в `TEAM.md`
команд, эти адреса агенты показывают участникам.

Проверка деплоя из репозитория команды (подставь репозиторий первой команды):

```bash
gh workflow run deploy-render.yml -R <OWNER>/team_1 -f services=all
gh run list -R <OWNER>/team_1 -w deploy-render.yml -L 1
```

Run зеленый, в логе каждого блока «Render принял деплой». Через 5 минут
`R status`: у `a:*` свежий деплой в статусе `live`.

Проверка симулятора:

```bash
R sim start
R sim state
R sim stop
```

У всех команд 500 клиентов, на табло та же картина. `sim stop` замораживает
табло до дня воркшопа.

## Шаг 7. Установщики и ключи ноутбуков

```bash
python3 tools/setup/make-bootstrap.py
tools/setup/github-access.sh keys
```

`make-bootstrap.py` создает по ключу на команду и пару установщиков в
`~/AI-Workshop-secrets/<WORKSHOP_ID>/team_<буква>/`:
`raif-workshop-setup.applescript` (macOS) и `raif-workshop-setup.cmd`
(Windows). `keys` вешает публичные ключи на репозитории команд как deploy
key с правом записи.

Проверка ключа первой команды:

```bash
ssh -i ~/AI-Workshop-secrets/<WORKSHOP_ID>/keys/team_a -o IdentitiesOnly=yes -p 443 -T git@ssh.github.com
```

Ответ: `Hi <OWNER>/team_1! You've successfully authenticated`.

Раздача: пара скриптов команды только ее участникам, лично (AirDrop,
флешка). Не в чаты, не в почту, не в репозиторий: в скрипте ключ с правом
push. CI оркестратора падает, если ключ попал в репозиторий.

## Шаг 8. Ноутбуки участников

На каждом ноутбуке:

- git (на macOS Command Line Tools: `xcode-select --install`);
- Claude Code (десктоп Claude или CLI) или Codex (десктоп ChatGPT или CLI)
  с входом в подписку;
- сеть до claude.ai и anthropic.com или chatgpt.com и openai.com, до
  github.com, ssh.github.com:443, raw.githubusercontent.com, *.onrender.com.

Установщик: двойной клик, выбрать блок, ввести имя. Он кладет ключ,
настраивает SSH через порт 443, клонирует репозиторий команды и включает
изоляцию блока. Дальше участник открывает папку репозитория в Claude или
Codex и пишет «привет»: агент сам проводит онбординг.

Проверка: на ноутбуке открываются табло и retail своей команды, агент в
онбординге называет блок участника.

## Шаг 9. Генеральный прогон

За 1-2 дня до воркшопа:

1. `R status`: все 200.
2. `R sim start`.
3. Три ноутбука одной команды, три человека по блокам, мини-фича за 20-30
   минут, например экран «мой баланс» сквозь три блока.
4. Сохранения доезжают до Render за 2-4 минуты, табло двигается, в ленте
   событий есть объяснение.
5. После: `R sim stop` и сброс репозитория команды
   `tools/setup/sync-team-repos.sh a`.

## Деньги

Порядок цен, перед оплатой сверить на render.com/pricing:

- web-сервис Starter около $7 в месяц, списание пропорционально времени:
  22 сервиса на неделю около $40;
- Postgres basic_256mb около $6 в месяц;
- сборки расходуют build minutes workspace: за 3 часа 7 команд делают сотни
  сборок, лимит проверить в Billing;
- LLM: судья зовется на каждое изменение команды, для gpt-4o-mini это копейки.

Сразу после воркшопа `R suspend`, после разбора `R teardown --confirm DELETE`
(через ops.sh: `teardown DELETE`). Подробно в `RUNBOOK.md`.

## Шпаргалка

| Что | Команда |
|---|---|
| проверить компьютер и аккаунты | `tools/setup/doctor.sh` |
| создать репозитории команд | `tools/setup/create-team-repos.sh` |
| залить или сбросить шаблон команд | `tools/setup/sync-team-repos.sh [буквы]` |
| секреты оркестратора для Actions | `tools/setup/github-access.sh ops-secrets` |
| RENDER_API_KEY и RENDER_SID_* в команды | `tools/setup/github-access.sh render` |
| deploy keys команд | `tools/setup/github-access.sh keys` |
| установщики ноутбуков | `python3 tools/setup/make-bootstrap.py` |
| создать все на Render | `R provision` |
| поменял teams.conf или ключ LLM | `R env` (для ops.sh сначала push и `ops-secrets`) |
| здоровье сервисов | `R status` |
| пересобрать | `R deploy a:cib sim` |
| тариф | `R plan starter` |
| табло | `R sim state`, `start`, `stop`, `reset`, `evaluate` |
| освободить лимит Hobby | `R drop <имена> --confirm DELETE` |
| после воркшопа | `github-access.sh revoke`, `R suspend`, `R teardown --confirm DELETE` |
