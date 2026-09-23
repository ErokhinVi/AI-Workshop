#!/usr/bin/env python3
"""tools/setup/hosts_kit.py: содержимое приватного репозитория ведущих (HOSTS_REPO в teams.conf).

Пишет в указанную папку (клон репозитория ведущих):
  README.md       табло и сервисы, пульт в Actions оркестратора, что делать если сломалось
  installer/      установщик ноутбука из папки секретов (make-bootstrap.py)

Секретов в README нет: ключи лежат в секретах оркестратора, ведущие пользуются
ими через workflow Workshop ops. В установщике приватные ключи команд, поэтому
репозиторий ведущих приватный. Скрипт ничего не пушит, это делает команда
installer в Workshop ops или человек.

Использование:
  python3 tools/setup/hosts_kit.py <папка>
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from workshop_conf import ROOT, Conf, ConfError, load_conf

SERVICES_CONF = ROOT / "tools/setup/render-services.conf"
PROXY_CONF = ROOT / "tools/setup/proxy-urls.conf"
INSTALLERS = ("raif-workshop-setup.applescript", "raif-workshop-setup.cmd")


def table(path: Path, url_column: int) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) > url_column and not line.startswith("#"):
                found[(parts[0], parts[1])] = parts[url_column]
    return found


def readme(conf: Conf) -> str:
    render = table(SERVICES_CONF, 3)
    proxy = table(PROXY_CONF, 2)
    orchestrator = f"https://github.com/{conf.owner}/{conf.orchestrator_repo}"
    ops = f"{orchestrator}/actions/workflows/workshop-ops.yml"
    installer = f"https://github.com/{conf.owner}/{conf.hosts_repo}/blob/main/installer"
    board = proxy.get(("sim", "simulator")) or render.get(("sim", "simulator"), "адрес у организатора")
    example_folder = f"~/{conf.teams[0][1]}"

    def row(what: str, letter: str, block: str) -> str:
        return f"| {what} | {proxy.get((letter, block), 'нет')} | {render.get((letter, block), 'нет')} |"

    rows = [row("Табло", "sim", "simulator")]
    rows += [row(f"Команда {n}, retail", letter, "retail") for n, (letter, _repo) in enumerate(conf.teams, 1)]
    repos = ", ".join(f"[{repo}](https://github.com/{conf.owner}/{repo})" for _letter, repo in conf.teams)
    return f"""# Воркшоп {conf.workshop_id}: для ведущих

Все в GitHub, своих ключей не нужно. Нужна роль соавтора: приглашение от организатора приходит на почту, его надо принять.
Собрано {time.strftime('%Y-%m-%d %H:%M')} командой `installer`.

## 1. Ноутбук участника, 5 минут

Номер команды = номер стола. Ноутбук уже готов: в Claude на вкладке Code агент создает файлы. Не так: к организатору.

1. На ноутбуке участника открой установщик: [Mac]({installer}/raif-workshop-setup.applescript), [Windows]({installer}/raif-workshop-setup.cmd). Войди в свой GitHub.
2. Справа над кодом кнопка со стрелкой вниз, Download raw file.
3. Двойной клик по файлу в Загрузках, откроется Script Editor. Жми ▶ или Cmd+R. На Windows просто двойной клик по `.cmd`.
   - macOS спросит про файл из интернета: Открыть. Про управление Terminal: OK
4. Три окна: номер стола → блок участника → имя и фамилия, можно по-русски → Go.
   - над полем имени видно «Team 4 · Retail»: сверь. Ошибся: Cancel и заново
5. Terminal пройдет 9 шагов и напишет ALL SET. Встал на ошибке: раздел «Если сломалось».
6. Claude → вкладка Code → новая сессия → папка, которую Terminal показал в конце, например `{example_folder}`. Codex: та же папка во вкладке Codex в ChatGPT.
7. Участник пишет агенту «привет». Агент попросит запустить `tools/cowork-onboard.py`: разрешить.
   - агент называет блок участника и говорит, что изоляция на месте. Так и надо
8. Установщик в Корзину, в нем ключи всех команд. Из своего GitHub на этом ноутбуке выйди.

Сменить блок между этапами: агент сохраняет работу → установщик еще раз с новым блоком → новая сессия Claude. Старая сессия помнит старый блок.

## 2. Запуск воркшопа

1. За 30 минут: [пульт]({ops}) → `status`, все 200. Табло на экран: {board}
2. Старт: пульт → `sim-start`. Или кнопка «Начать воркшоп» на табло, она просит админ-токен, токен у организатора.
   - старт идет около 30 секунд: судья смотрит все банки. Второй раз не жми
3. У всех 500 клиентов и одинаковый балл. Команда успела сделать фичу до старта: балл выше, клиентов столько же.
4. Между этапами табло не трогай, счет копится весь день. В конце `sim-stop` и скриншот табло.

Участник сохранил работу: табло отреагирует через 3-5 минут. 2-4 минуты сборка блока, до 30 секунд табло замечает, около 20 секунд судья.

Сценарий по минутам и какие фичи объявлять: {orchestrator}/blob/main/RUNBOOK.md

## Табло и сервисы

Сеть банка режет `*.onrender.com`, адреса `workers.dev` открываются отовсюду.
С ноутбуков воркшопа (там VPN) и из дома работают оба.

| Что | Из сети банка | Напрямую Render |
|---|---|---|
{chr(10).join(rows)}

Блоки cib и backend: тот же адрес, `cib` или `backend` вместо `retail`.
Репозитории команд: {repos}.

## Пульт

{ops} → Run workflow → `command`, при нужде `args` и `confirm`. Результат в Summary рана через минуту-две.

| Что нужно | command | args | confirm |
|---|---|---|---|
| здоровье всех сервисов | `status` | | |
| начать воркшоп, всем по 500 клиентов | `sim-start` | | |
| счет и последние события | `sim-state` | | |
| пересчитать сейчас, не ждать | `sim-evaluate` | | |
| заморозить табло | `sim-stop` | | |
| пересобрать блок команды | `deploy` | `3:cib` или `sim` | |
| логи блока: почему упал или не собрался | `logs` | `3:cib` или `3:cib build` | |
| вернуть репозиторий команды к старту, только по просьбе команды | `team-reset` | `3` | `RESET` |
| починить ключи и доступ к Render в репозиториях команд | `team-access` | | |
| пересобрать прокси workers.dev | `proxy` | | |
| пересобрать установщик в этом репозитории | `installer` | | |
| после воркшопа: усыпить сервисы | `suspend` | | |
| после воркшопа: снять ключи команд | `revoke` | | `DELETE` |

## Если сломалось

Установщик:

- `GitHub did not accept the key`: пульт → `team-access`, потом установщик еще раз
- `Script is unsigned` или `Unknown team`: файл не отсюда или старый, скачай заново из этого репо
- просит поставить Command Line Tools: Install, 5-10 минут, потом установщик еще раз

По ходу:

- сохранение падает с `Permission denied (publickey)`: пульт → `team-access`
- блок не обновился за 5 минут: репозиторий команды → Actions. Красный run: `logs` с args `3:cib build`, текст ошибки отдай агенту участника
- табло стоит: `sim-state`, воркшоп идет ? Нет: `sim-start`. Идет: `sim-evaluate`
- табло не открывается: `status`, потом `deploy` с args `sim`. Счет лежит в базе, не пропадет
- объяснения в ленте табло однотипные: кончился баланс OpenRouter, табло считает запасной формулой
- команда все сломала и просит начать заново: `team-reset` с номером команды и confirm `RESET`, работа команды уйдет

Полная таблица поломок: {orchestrator}/blob/main/RUNBOOK.md

Без организатора не сделать: пополнить OpenRouter, поменять ключи Render, OpenRouter и Cloudflare, выдать доступ новому ведущему.
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    out = Path(sys.argv[1]).expanduser()
    conf = load_conf()
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(readme(conf), encoding="utf-8")
    (out / "installer").mkdir(exist_ok=True)
    missing = []
    for name in INSTALLERS:
        source = conf.secrets_dir / name
        if source.is_file():
            shutil.copy2(source, out / "installer" / name)
        else:
            missing.append(name)
    print(f"собрал {out}: README.md, installer/")
    if missing:
        print(f"  нет установщика ({', '.join(missing)}): python3 tools/setup/make-bootstrap.py")
        return 1
    if not PROXY_CONF.is_file():
        print("  нет адресов workers.dev: python3 tools/setup/cf_proxy.py conf")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfError as exc:
        raise SystemExit(f"hosts_kit: {exc}") from exc
