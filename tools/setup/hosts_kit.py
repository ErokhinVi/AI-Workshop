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

    def row(what: str, letter: str, block: str) -> str:
        return f"| {what} | {proxy.get((letter, block), 'нет')} | {render.get((letter, block), 'нет')} |"

    rows = [row("Табло", "sim", "simulator")]
    rows += [row(f"Команда {n}, retail", letter, "retail") for n, (letter, _repo) in enumerate(conf.teams, 1)]
    repos = ", ".join(f"[{repo}](https://github.com/{conf.owner}/{repo})" for _letter, repo in conf.teams)
    return f"""# Воркшоп {conf.workshop_id}: для ведущих

Все управление воркшопом в GitHub, отдельных ключей на руках не нужно.
Пульт: {ops} → Run workflow. Нужна роль соавтора, ее выдает организатор.
Собрано {time.strftime('%Y-%m-%d %H:%M')} командой `installer`.

## Табло и сервисы

Сеть банка режет `*.onrender.com`, адреса `workers.dev` открываются отовсюду.
С ноутбуков воркшопа (там VPN) и из дома работают оба.

| Что | Из сети банка | Напрямую Render |
|---|---|---|
{chr(10).join(rows)}

Блоки cib и backend: тот же адрес, `cib` или `backend` вместо `retail`.
Номер команды = номер стола. Репозитории команд: {repos}.

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

Кнопка «Начать воркшоп» на самом табло просит админ-токен. Без токена: `sim-start` в пульте, табло оживет само.

## Установщик ноутбука

`installer/raif-workshop-setup.applescript` для Mac, `.cmd` для Windows: открыть файл на GitHub → Download raw file.
На ноутбуке двойной клик → Run → номер команды, блок, имя. Дальше скрипт все делает сам в Terminal.
В файле ключи всех команд: только на ноутбуки воркшопа, не в чаты.
Сменить блок: попросить агента сохранить работу и запустить установщик еще раз.

## Если сломалось

Полная таблица в {orchestrator}/blob/main/RUNBOOK.md

- блок не обновился за 5 минут: репозиторий команды → Actions. Красный run: `logs` с args `3:cib build`
- табло стоит: `sim-state`, воркшоп идет ? Нет: `sim-start`. Идет: `sim-evaluate`
- табло не открывается: `status`, потом `deploy` с args `sim`. Счет лежит в базе, не пропадет
- объяснения в ленте табло однотипные: кончился баланс OpenRouter, табло считает запасной формулой

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
