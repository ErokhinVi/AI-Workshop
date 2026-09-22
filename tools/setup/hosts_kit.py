#!/usr/bin/env python3
"""tools/setup/hosts_kit.py: собрать папку для ведущих: ссылки, пульт, ключи, установщик.

Пишет в указанную папку (обычно клон приватного репозитория ведущих):
  README.md                     табло и сервисы, пульт, ключи, что делать если сломалось
  workshop.env                  секреты воркшопа из папки секретов
  installer/raif-workshop-setup.{applescript,cmd}   установщик ноутбука (make-bootstrap.py)

В папке ключи: только приватный репозиторий, не публичный и не чаты. Скрипт
ничего не пушит, пушит человек или агент по его просьбе.

Использование:
  python3 tools/setup/hosts_kit.py <папка>
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from workshop_conf import ROOT, SECRET_KEYS, Conf, ConfError, load_conf, load_secrets

SERVICES_CONF = ROOT / "tools/setup/render-services.conf"
PROXY_CONF = ROOT / "tools/setup/proxy-urls.conf"
INSTALLERS = ("raif-workshop-setup.applescript", "raif-workshop-setup.cmd")
KEY_ROLES = {
    "RENDER_API_KEY": "все сервисы воркшопа на Render",
    "OPENAI_API_KEY": "OpenRouter: судья табло и блок cib",
    "ADMIN_TOKEN": "админка табло, кнопка «Начать воркшоп»",
    "CLOUDFLARE_API_TOKEN": "прокси workers.dev",
    "RENDER_OWNER_ID": "workspace Render, если ключ видит несколько",
}


def table(path: Path, url_column: int) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) > url_column and not line.startswith("#"):
                found[(parts[0], parts[1])] = parts[url_column]
    return found


def readme(conf: Conf, secrets: dict[str, str]) -> str:
    render = table(SERVICES_CONF, 3)
    proxy = table(PROXY_CONF, 2)
    orchestrator = f"https://github.com/{conf.owner}/{conf.orchestrator_repo}"

    def row(what: str, letter: str, block: str) -> str:
        return f"| {what} | {proxy.get((letter, block), 'нет')} | {render.get((letter, block), 'нет')} |"

    rows = [row("Табло", "sim", "simulator")]
    rows += [row(f"Команда {n}, retail", letter, "retail") for n, (letter, _repo) in enumerate(conf.teams, 1)]
    repos = ", ".join(f"[{repo}](https://github.com/{conf.owner}/{repo})" for _letter, repo in conf.teams)
    keys = "\n".join(f"| {name} | {role} | {'есть' if secrets.get(name) else 'пусто'} |"
                     for name, role in KEY_ROLES.items()
                     if name in SECRET_KEYS and (secrets.get(name) or name != "RENDER_OWNER_ID"))
    return f"""# Воркшоп {conf.workshop_id}: доступы ведущих

Здесь ключи воркшопа. Файлы не пересылать и не выкладывать.
Собрано {time.strftime('%Y-%m-%d %H:%M')} скриптом `tools/setup/hosts_kit.py` из {orchestrator}.

## Табло и сервисы

Сеть банка режет `*.onrender.com`. Адреса `workers.dev` идут через прокси и открываются отовсюду.
С ноутбуков воркшопа (там VPN) и из дома работают оба.

| Что | Из сети банка | Напрямую Render |
|---|---|---|
{chr(10).join(rows)}

Блоки cib и backend: тот же адрес, `cib` или `backend` вместо `retail`.
Номер команды = номер стола. Репозитории команд: {repos}.

## Пульт

{orchestrator}/actions/workflows/workshop-ops.yml → Run workflow → поле `command`.
Нужен доступ на запись в {conf.owner}/{conf.orchestrator_repo}. Результат в Summary рана через минуту-две.

| Что нужно | command | args |
|---|---|---|
| здоровье всех сервисов | `status` | |
| начать воркшоп, всем по 500 клиентов | `sim-start` | |
| счет и последние события | `sim-state` | |
| пересчитать сейчас, не ждать | `sim-evaluate` | |
| заморозить табло | `sim-stop` | |
| пересобрать блок команды | `deploy` | `3:cib` (команда 3, блок cib) или `sim` |

Без GitHub, из дома или с VPN: склонировать {orchestrator}, положить `workshop.env` в
`~/AI-Workshop-secrets/{conf.workshop_id}/` и запускать `python3 tools/setup/render_ops.py status`.
Команды те же, `sim start` вместо `sim-start`.

## Ключи: workshop.env

| Ключ | Что открывает | Сейчас |
|---|---|---|
{keys}

## Установщик ноутбука

`installer/raif-workshop-setup.applescript` для Mac, `.cmd` для Windows.
Двойной клик → Run → номер команды, блок, имя. Дальше скрипт все делает сам в Terminal.
В файле ключи всех команд: только на ноутбуки воркшопа, не в чаты.
Сменить блок: попросить агента сохранить работу и запустить установщик еще раз.

## Если сломалось

Полная таблица в {orchestrator}/blob/main/RUNBOOK.md

- блок не обновился за 5 минут: репозиторий команды на GitHub → Actions. Красный run: открыть лог
- табло стоит: `sim-state`, воркшоп идет ? Нет: `sim-start`. Идет: `sim-evaluate`
- табло не открывается: `status`, потом `deploy` с args `sim`. Счет лежит в базе, не пропадет
- объяснения в ленте табло однотипные: кончился баланс OpenRouter, табло считает запасной формулой

## После воркшопа

`sim-stop` и скриншот табло. Дальше организатор снимает ключи команд
(`tools/setup/github-access.sh revoke`), усыпляет сервисы (`suspend`) и удаляет ключи Render,
OpenRouter и Cloudflare.
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    out = Path(sys.argv[1]).expanduser()
    conf = load_conf()
    secrets = load_secrets(conf)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(readme(conf, secrets), encoding="utf-8")
    env = "".join(f"{name}={secrets.get(name, '')}\n" for name in SECRET_KEYS)
    (out / "workshop.env").write_text(f"# секреты воркшопа {conf.workshop_id}, {time.strftime('%Y-%m-%d')}\n{env}")
    (out / "workshop.env").chmod(0o600)
    (out / "installer").mkdir(exist_ok=True)
    missing = []
    for name in INSTALLERS:
        source = conf.secrets_dir / name
        if source.is_file():
            shutil.copy2(source, out / "installer" / name)
        else:
            missing.append(name)
    print(f"собрал {out}: README.md, workshop.env, installer/")
    if missing:
        print(f"  нет установщика ({', '.join(missing)}): python3 tools/setup/make-bootstrap.py")
    if not PROXY_CONF.is_file():
        print("  нет адресов workers.dev: python3 tools/setup/cf_proxy.py deploy")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfError as exc:
        raise SystemExit(f"hosts_kit: {exc}") from exc
