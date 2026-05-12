# tools/bootstrap — стартовые скрипты для ноутбуков участников

Эти два файла раздаются членам правления перед воркшопом, чтобы их ноутбуки за пару минут оказались настроены: SSH-ключ воркшопа, git identity, склонированный репо и `.git/raif-workshop-info`, который читает `tools/cowork-onboard.py` при первом запуске Claude в Cowork.

## Что внутри

| Файл | Платформа | Как запускается |
|---|---|---|
| `raif-workshop-setup.applescript` | macOS | Дабл-клик → Script Editor → Run (Cmd+R) → выбор участника → автоматически открывается Terminal с bootstrap-скриптом. |
| `raif-workshop-setup.cmd` | Windows 10/11 | Дабл-клик → SmartScreen «Подробнее → Выполнить в любом случае» → выбор участника в WinForms-меню → всё крутится в одном консольном окне. |

Оба скрипта делают одну и ту же работу:

1. Кладут встроенный SSH-ключ в `~/.ssh/raif_workshop` с правами только текущего пользователя.
2. Дописывают блок в `~/.ssh/config` с маркером `# raif-workshop-2026`, чтобы GitHub использовал этот ключ.
3. Прописывают `git config --global user.name` и `user.email` под выбранного участника.
4. Стучатся `ssh -T git@github.com` и ждут `successfully authenticated`.
5. Клонируют или ребейзят `~/AI-Workshop` (или `%USERPROFILE%\AI-Workshop`).
6. Копируют ключ в `.git/raif-workshop-key` и пишут `.git/raif-workshop-info` с `WORKSHOP_PARTICIPANT/BLOCK/GIT_NAME/GIT_EMAIL` — это то, что подцепит Claude в Cowork при первом сообщении.

## Список участников (одинаковый в обоих скриптах)

| № | ФИО | Блок | Папка |
|---|---|---|---|
| 1 | Сергей Монин | CEO Office | `ceo/` |
| 2 | Никита Патрахин | CIB | `cib/` |
| 3 | Иван Курочкин | Розница | `retail/` |
| 4 | Александр Ложечкин | IT / Платформа | `it/` |
| 5 | Герт Хебенштрайт | Финансы и Опс | `finance/` |
| 6 | Роланд Васс | Управление рисками | `risk/` |
| 7 | Виталий Ерохин | host (ведущий) | `host/` |

## Зависимости на ноутбуке участника

**macOS.** Xcode Command Line Tools (git и ssh оттуда). Если нет — `xcode-select --install`. Скрипт сам это проверит и подскажет.

**Windows.** Git for Windows ([git-scm.com](https://git-scm.com/download/win)) и OpenSSH Client (в Windows 10/11 идёт из коробки; если в корпоративном образе вырезан — Settings → Apps → Optional Features → Add → OpenSSH Client). Скрипт сам проверит и подскажет команду.

## Как раздавать

- На Mac удобнее всего через AirDrop. Топ ловит файл в Downloads и сразу дабл-кликает.
- На Windows — через корпоративный мессенджер / OneDrive / флешка. Двойной клик из Downloads.

## Что делать после воркшопа

Удалить deploy key на GitHub:

```
Repo → Settings → Deploy keys → "raif-workshop-2026" → Delete
```

После этого встроенные в скрипты ключи становятся бесполезными — что и нужно.

## Если скрипт упал

`tools/cowork-onboard.py` умеет работать и без bootstrap-файлов (старая схема через имя в TEAM.md). Так что в крайнем случае участник всё равно сможет работать — просто без подписи коммитов от своего имени и без push в общий GitHub.
