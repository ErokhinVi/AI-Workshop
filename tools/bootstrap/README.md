# tools/bootstrap — стартовые скрипты для ноутбуков участников

Эти файлы раздаются членам правления перед воркшопом, чтобы их ноутбуки за пару минут оказались настроены: SSH-ключ воркшопа, git identity, склонированный репо и `.git/raif-workshop-info`, который читает `tools/cowork-onboard.py` при первом запуске Claude в Cowork.

## Что внутри

| Файл | Платформа | Как запускается |
|---|---|---|
| `raif-workshop-setup.applescript` | macOS | Дабл-клик → Script Editor → Run (Cmd+R) → выбор команды и блока, ввод имени → автоматически открывается Terminal с bootstrap-скриптом. |
| `raif-workshop-setup.cmd` | Windows 10/11 | Дабл-клик → SmartScreen «Подробнее → Выполнить в любом случае» → выбор команды и блока и ввод имени в WinForms-окне → всё крутится в одном консольном окне. |
| `raif-workshop-setup-board.*` | macOS / Windows | То же самое, но с заранее зашитым составом правления (выбор из списка из 7 человек). Раздаётся только членам правления. |

Скрипт делает:

1. Кладёт встроенный SSH-ключ в `~/.ssh/raif_workshop` с правами только текущего пользователя.
2. Дописывает блок в `~/.ssh/config` с маркером `# raif-workshop-2026`, чтобы GitHub использовал этот ключ.
3. Прописывает `git config --global user.name` и `user.email` под выбранного участника.
4. Стучится `ssh -T git@github.com` и ждёт `successfully authenticated`.
5. Клонирует или ребейзит `~/AI-Workshop` (или `%USERPROFILE%\AI-Workshop`).
6. Копирует ключ в `.git/raif-workshop-key` и пишет `.git/raif-workshop-info` с `WORKSHOP_PARTICIPANT/TEAM/BLOCK/GIT_NAME/GIT_EMAIL` — это то, что подцепит Claude в Cowork при первом сообщении.

## Кто в какой команде

Привязки людей к командам и блокам в основных скриптах нет: участник сам
выбирает команду (`team_a` или `team_b`) и блок (`retail` / `cib` / `backend`)
и вводит имя. Скрипт пишет выбор в `.git/raif-workshop-info`
(`WORKSHOP_TEAM`, `WORKSHOP_BLOCK`, `WORKSHOP_PARTICIPANT`), а email и slug
участника выводит из имени (транслитерация кириллицы).

Версия с заранее зашитым составом правления — отдельные файлы
`raif-workshop-setup-board.applescript` и `raif-workshop-setup-board.cmd`
(там список из 7 человек в `$Members` / меню). Их раздают членам правления,
а generic-версию выше — на тест-прогонах.

## Зависимости на ноутбуке участника

**macOS.** Xcode Command Line Tools (git и ssh оттуда). Если нет — `xcode-select --install`. Скрипт сам это проверит и подскажет.

**Windows.** Git for Windows ([git-scm.com](https://git-scm.com/download/win)) и OpenSSH Client (в Windows 10/11 идёт из коробки; если в корпоративном образе вырезан — Settings → Apps → Optional Features → Add → OpenSSH Client).

## Как раздавать

- На Mac удобнее всего через AirDrop. Топ ловит файл в Downloads и сразу дабл-кликает.
- На Windows — через корпоративный мессенджер / OneDrive / флешку. Двойной клик из Downloads.

## Что делать после воркшопа

Удалить deploy key на GitHub:

```
Repo → Settings → Deploy keys → "raif-workshop-2026" → Delete
```

После этого встроенные в скрипты ключи становятся бесполезными — что и нужно.

## Если скрипт упал

`tools/cowork-onboard.py` умеет работать и без bootstrap-файлов (старая схема через имя в `TEAM.md`). Так что в крайнем случае участник всё равно сможет работать — просто без подписи коммитов от своего имени и без push в общий GitHub.
