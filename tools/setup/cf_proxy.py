#!/usr/bin/env python3
"""tools/setup/cf_proxy.py: адреса сервисов, которые открываются из корпоративной сети.

Корпоративная сеть режет *.onrender.com, а *.workers.dev пропускает. Скрипт
ставит на Cloudflare Workers прокси на каждый сервис из render-services.conf
(код прокси: tools/proxy/worker.js):
  https://simulator.<поддомен>.workers.dev     → табло
  https://team1-retail.<поддомен>.workers.dev  → retail команды 1 (team_a)
Поддомен workers.dev у аккаунта один, по умолчанию RENDER_PREFIX из teams.conf.

Использование:
  python3 tools/setup/cf_proxy.py deploy [--subdomain имя]  # поддомен, воркеры, proxy-urls.conf
  python3 tools/setup/cf_proxy.py check                     # /health каждого сервиса через прокси
  python3 tools/setup/cf_proxy.py urls                      # адреса для людей
  python3 tools/setup/cf_proxy.py delete --confirm DELETE   # после воркшопа

Токен CLOUDFLARE_API_TOKEN (шаблон «Edit Cloudflare Workers») берется из
workshop.env или окружения, значение не печатается. Бесплатный план Workers:
100 тысяч запросов в сутки на аккаунт, табло на экране съедает около 20 тысяч.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from workshop_conf import ROOT, Conf, ConfError, load_conf, load_secrets

API = "https://api.cloudflare.com/client/v4"
WORKER_JS = ROOT / "tools/proxy/worker.js"
SERVICES_CONF = ROOT / "tools/setup/render-services.conf"
PROXY_CONF = ROOT / "tools/setup/proxy-urls.conf"
COMPATIBILITY_DATE = "2026-09-01"
NO_SUBDOMAIN = 10007  # у аккаунта еще нет поддомена workers.dev


class CfError(RuntimeError):
    def __init__(self, message: str, codes: tuple[int, ...] = ()):
        super().__init__(message)
        self.codes = codes


class Cloudflare:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, body: bytes | None = None,
             content_type: str = "application/json") -> dict:
        request = urllib.request.Request(f"{API}{path}", data=body, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            request.add_header("Content-Type", content_type)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as exc:
                payload = exc.read()
                if exc.code == 429 and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                try:
                    errors = json.loads(payload).get("errors") or []
                except ValueError:
                    errors = []
                text = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors) or payload[:200].decode(errors="replace")
                raise CfError(f"{method} {path}: HTTP {exc.code} {text}",
                              tuple(int(e.get("code", 0)) for e in errors)) from None
        raise CfError(f"{method} {path}: 429 не прошел")

    def json(self, method: str, path: str, data: dict) -> dict:
        return self.call(method, path, json.dumps(data).encode(), "application/json")


def services() -> list[tuple[str, str, str]]:
    """(буква|sim, блок, url на Render) из render-services.conf."""
    if not SERVICES_CONF.is_file():
        raise ConfError(f"нет {SERVICES_CONF}: tools/setup/ops.sh services")
    out = []
    for line in SERVICES_CONF.read_text().splitlines():
        parts = line.split()
        if len(parts) == 4 and not line.startswith("#"):
            out.append((parts[0], parts[1], parts[3].rstrip("/")))
    if not out:
        raise ConfError(f"{SERVICES_CONF} пуст")
    return out


def worker_name(letter: str, block: str) -> str:
    if letter == "sim":
        return "simulator"
    return f"team{ord(letter) - ord('a') + 1}-{block}"


def multipart(metadata: dict, module: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"; filename=\"metadata.json\"\r\n"
        f"Content-Type: application/json\r\n\r\n{json.dumps(metadata)}\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\n"
        f"Content-Type: application/javascript+module\r\n\r\n{module}\r\n",
        f"--{boundary}--\r\n",
    ]
    return "".join(parts).encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def account_id(cf: Cloudflare) -> str:
    accounts = cf.call("GET", "/accounts").get("result") or []
    if not accounts:
        raise CfError("токен не видит ни одного аккаунта: при создании выбери аккаунт в Account Resources")
    return accounts[0]["id"]


def ensure_subdomain(cf: Cloudflare, account: str, wanted: str) -> str:
    try:
        current = cf.call("GET", f"/accounts/{account}/workers/subdomain").get("result") or {}
        if current.get("subdomain"):
            return current["subdomain"]
    except CfError as exc:
        if NO_SUBDOMAIN not in exc.codes:
            raise
    cf.json("PUT", f"/accounts/{account}/workers/subdomain", {"subdomain": wanted})
    print(f"+ поддомен {wanted}.workers.dev (DNS и сертификат появляются за несколько минут)")
    return wanted


def read_proxy_conf() -> list[tuple[str, str, str, str]]:
    if not PROXY_CONF.is_file():
        raise ConfError(f"нет {PROXY_CONF}: сначала cf_proxy.py deploy")
    rows = []
    for line in PROXY_CONF.read_text().splitlines():
        parts = line.split()
        if len(parts) == 4 and not line.startswith("#"):
            rows.append((parts[0], parts[1], parts[2], parts[3]))
    return rows


def deploy(cf: Cloudflare, conf: Conf, wanted: str) -> None:
    account = account_id(cf)
    sub = ensure_subdomain(cf, account, wanted)
    module = WORKER_JS.read_text(encoding="utf-8")
    rows = []
    for letter, block, target in services():
        name = worker_name(letter, block)
        body, content_type = multipart({
            "main_module": "worker.js",
            "compatibility_date": COMPATIBILITY_DATE,
            "bindings": [{"type": "plain_text", "name": "TARGET", "text": target}],
        }, module)
        cf.call("PUT", f"/accounts/{account}/workers/scripts/{name}", body, content_type)
        cf.json("POST", f"/accounts/{account}/workers/scripts/{name}/subdomain",
                {"enabled": True, "previews_enabled": False})
        url = f"https://{name}.{sub}.workers.dev"
        rows.append((letter, block, url, target))
        print(f"  {name:<16} {url}  → {target}")
    header = f"# буква блок адрес-из-корпсети адрес-на-Render; cf_proxy.py, {time.strftime('%Y-%m-%d %H:%M')}\n"
    PROXY_CONF.write_text(header + "".join(" ".join(row) + "\n" for row in rows))
    where = PROXY_CONF.relative_to(ROOT) if PROXY_CONF.is_relative_to(ROOT) else PROXY_CONF
    print(f"записал {where}: прокси {len(rows)}")


def check() -> int:
    bad = 0
    for letter, block, url, _target in read_proxy_conf():
        # User-Agent Python-urllib режется по дороге (403), с обычным проходит.
        probe = urllib.request.Request(f"{url}/health", headers={"User-Agent": "raif-workshop-check/1.0"})
        try:
            with urllib.request.urlopen(probe, timeout=30) as response:
                code = response.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except (urllib.error.URLError, TimeoutError) as exc:
            code = f"нет связи ({getattr(exc, 'reason', exc)})"
        bad += code != 200
        print(f"  {letter}:{block:<9} {url}/health  {code}")
    print("все прокси отвечают 200" if not bad else f"не отвечают: {bad}")
    return 1 if bad else 0


def urls() -> None:
    for letter, block, url, _target in read_proxy_conf():
        who = "табло" if letter == "sim" else f"команда {ord(letter) - ord('a') + 1}, {block}"
        print(f"  {who:<22} {url}")


def delete(cf: Cloudflare) -> None:
    account = account_id(cf)
    for letter, block, _target in services():
        name = worker_name(letter, block)
        try:
            cf.call("DELETE", f"/accounts/{account}/workers/scripts/{name}?force=true")
            print(f"- {name}")
        except CfError as exc:
            print(f"? {name}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_deploy = sub.add_parser("deploy")
    p_deploy.add_argument("--subdomain", default=None)
    sub.add_parser("check")
    sub.add_parser("urls")
    p_delete = sub.add_parser("delete")
    p_delete.add_argument("--confirm", default="")
    args = parser.parse_args()

    conf = load_conf()
    if args.command == "check":
        return check()
    if args.command == "urls":
        urls()
        return 0
    token = load_secrets(conf).get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise ConfError(f"нет CLOUDFLARE_API_TOKEN: впиши в {conf.secrets_dir / 'workshop.env'}")
    cf = Cloudflare(token)
    if args.command == "deploy":
        deploy(cf, conf, args.subdomain or conf.prefix)
    elif args.command == "delete":
        if args.confirm != "DELETE":
            raise ConfError("delete удаляет все прокси: добавь --confirm DELETE")
        delete(cf)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CfError, ConfError) as exc:
        raise SystemExit(f"cf_proxy: {exc}") from exc
