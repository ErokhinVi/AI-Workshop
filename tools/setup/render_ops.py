#!/usr/bin/env python3
"""tools/setup/render_ops.py: Render и симулятор воркшопа.

Создает и обслуживает все, что воркшоп держит на Render: Postgres, симулятор с
табло и по три сервиса на команду (backend, cib, retail). Команды, имена и
тарифы берет из tools/setup/teams.conf. Секреты из окружения или из файла
~/AI-Workshop-secrets/<WORKSHOP_ID>/workshop.env, окружение важнее файла:

  RENDER_API_KEY   ключ Render API (Account Settings, API Keys)
  OPENAI_API_KEY   ключ OpenAI-совместимого LLM: судья симулятора и блок cib
  ADMIN_TOKEN      пароль админки симулятора, его создает init-secrets
  RENDER_OWNER_ID  нужен, только если ключ видит несколько workspace

Из корпоративной сети api.render.com и *.onrender.com закрыты. Оттуда те же
команды запускаются в GitHub Actions: workflow Workshop ops (SETUP.md).

Команды:
  init-secrets               создать workshop.env: ADMIN_TOKEN и пустые ключи
  check                      ключ Render, workspace, что из воркшопа уже есть
  provision [--dry-run]      Postgres, сервисы команд, симулятор, env; повтор безопасен
  env [--dry-run]            привести env сервисов к teams.conf, изменившиеся передеплоить
  services                   записать tools/setup/render-services.conf
  status                     последний деплой и /health каждого сервиса
  deploy [цель ...]          передеплой: sim, a (три блока команды), a:cib; без целей все
  plan <тариф>               сменить тариф всех web-сервисов: free, starter
  suspend | resume           усыпить или разбудить web-сервисы воркшопа
  teardown --confirm DELETE  удалить сервисы и Postgres воркшопа
  drop <имя ...> --confirm DELETE   удалить чужие сервисы workspace (лимит Hobby 25)
  sim state|start|stop|reset|evaluate   табло и админка симулятора

Коды выхода: 0 ок, 1 ошибка, 75 Render ограничил частоту запросов (повтори позже).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets as pysecrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workshop_conf import ROOT, Conf, ConfError, load_conf, load_secrets  # noqa: E402

API_BASE = "https://api.render.com/v1"
PAGE = 100
BLOCKS = ("backend", "cib", "retail")  # backend первым: cib и retail получают его URL
SIM_ACTIONS = ("state", "start", "stop", "reset", "evaluate")
EX_TEMPFAIL = 75

CORP_HINT = ("Из корпоративной сети api.render.com и *.onrender.com закрыты: запусти то же "
             "через GitHub Actions, workflow Workshop ops (SETUP.md).")
CREATE_HINT = ("Если ошибка про репозиторий: репозитории должны быть публичными, либо подключи "
               "GitHub в Render (Account Settings, Git Credentials). Если про оплату или тариф: "
               "в Render нужна карта (Workspace Settings, Billing).")
NEXT_STEPS = """
Дальше:
  tools/setup/github-access.sh render        RENDER_API_KEY и RENDER_SID_* в репозитории команд
  tools/setup/sync-team-repos.sh             настоящие URL сервисов в TEAM.md команд
  python3 tools/setup/render_ops.py status   через 5-10 минут у всех /health 200
"""
SECRETS_TEMPLATE = """\
# Секреты воркшопа {workshop_id}. Не коммитить, не пересылать в чат. Права 600.
# Render: dashboard.render.com, Account Settings, API Keys, Create API Key
RENDER_API_KEY=
# OpenAI-совместимый ключ: судья симулятора и блок cib. Адрес и модель в teams.conf
OPENAI_API_KEY=
# Пароль админки симулятора, сгенерирован
ADMIN_TOKEN={admin_token}
# Только если render_ops.py check попросит выбрать workspace
RENDER_OWNER_ID=
"""


class OpsError(RuntimeError):
    """Продолжать нельзя. Текст для человека."""


class RateLimited(OpsError):
    pass


def log(message: str = "") -> None:
    print(message, flush=True)


def services_conf_path() -> Path:
    return Path(os.environ.get("RENDER_SERVICES_CONF") or ROOT / "tools/setup/render-services.conf")


def _int_header(headers, name: str) -> int:
    try:
        return int(float(headers.get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _error_text(raw: bytes) -> str:
    try:
        body = json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", errors="replace")[:300]
    return str(body.get("message") or body) if isinstance(body, dict) else str(body)


# ── Render API ───────────────────────────────────────────────────────────────

class Render:
    def __init__(self, api_key: str, base: str = API_BASE, max_wait: int = 90) -> None:
        self.api_key = api_key
        self.base = base.rstrip("/")
        self.max_wait = max_wait

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)

    def call(self, method: str, path: str, body=None, query: dict | None = None):
        url = self.base + path + ("?" + urlencode(query, doseq=True) if query else "")
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method in ("POST", "PUT", "PATCH"):
            data = b""
        for attempt in range(1, 7):
            try:
                with urlopen(Request(url, data=data, method=method, headers=headers), timeout=60) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw.strip() else None
            except HTTPError as exc:
                raw = exc.read()
                if exc.code == 429:
                    wait = (_int_header(exc.headers, "Retry-After")
                            or _int_header(exc.headers, "RateLimit-Reset") or 30)
                    if wait <= self.max_wait and attempt < 6:
                        log(f"  Render: лимит запросов, жду {wait} с")
                        self.sleep(wait + 1)
                        continue
                    raise RateLimited(f"Render ограничил частоту запросов еще на {wait // 60 + 1} мин. "
                                      "Созданное не пропадет: повтори команду позже, повтор ничего не дублирует.")
                if exc.code >= 500 and attempt < 4:
                    self.sleep(5 * attempt)
                    continue
                raise OpsError(f"Render {method} {path}: HTTP {exc.code} {_error_text(raw)}")
            except (URLError, OSError) as exc:
                if attempt < 3:
                    self.sleep(5 * attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                raise OpsError(f"нет связи с {self.base}: {reason}. {CORP_HINT}")
        raise OpsError(f"Render {method} {path}: не ответил после повторов")

    def list_all(self, path: str, wrapper: str, query: dict | None = None) -> list:
        items: list = []
        cursor = None
        while True:
            params = dict(query or {}, limit=PAGE)
            if cursor:
                params["cursor"] = cursor
            page = self.call("GET", path, query=params) or []
            items.extend(entry.get(wrapper, entry) for entry in page)
            cursor = page[-1].get("cursor") if page else None
            if len(page) < PAGE or not cursor:
                return items


def fetch(url: str, *, method: str = "GET", headers: dict | None = None, timeout: int = 60):
    """HTTP к сервисам на onrender.com: (код, JSON или текст). Код 0: нет связи."""
    request = Request(url, data=b"" if method == "POST" else None, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as resp:
            code, raw = resp.status, resp.read()
    except HTTPError as exc:
        code, raw = exc.code, exc.read()
    except (URLError, OSError):
        return 0, None
    try:
        return code, json.loads(raw)
    except ValueError:
        return code, raw.decode("utf-8", errors="replace")[:300]


# ── что должно быть на Render ────────────────────────────────────────────────

@dataclass(frozen=True)
class Target:
    key: str      # sim или a:backend
    letter: str   # пусто у симулятора
    block: str    # simulator, backend, cib, retail
    name: str     # имя сервиса на Render
    repo: str     # owner/repo

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"

    @property
    def dockerfile(self) -> str:
        return f"./{self.block}/Dockerfile"


def build_targets(conf: Conf) -> list[Target]:
    result = [Target(f"{letter}:{block}", letter, block, f"{conf.prefix}-{letter}-{block}",
                     f"{conf.owner}/{repo}")
              for letter, repo in conf.teams for block in BLOCKS]
    result.append(Target("sim", "", "simulator", f"{conf.prefix}-simulator",
                         f"{conf.owner}/{conf.orchestrator_repo}"))
    return result


def print_table(headers: tuple, rows: list) -> None:
    rows = [tuple("" if cell is None else str(cell) for cell in row) for row in rows]
    widths = [max([len(h)] + [len(row[i]) for row in rows]) for i, h in enumerate(headers)]
    for row in [tuple(headers)] + rows:
        log("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n")
            for row in rows:
                out.write("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |\n")
            out.write("\n")


class Workshop:
    def __init__(self, conf: Conf, secrets: dict, render: Render) -> None:
        self.conf = conf
        self.secrets = secrets
        self.render = render
        self.targets = build_targets(conf)
        self.by_key = {t.key: t for t in self.targets}
        self._owner: str | None = None
        self._services: dict | None = None

    # ── общее ──

    def need(self, *keys: str) -> None:
        missing = [k for k in keys if not self.secrets.get(k)]
        if missing:
            where = ("задай секреты репозитория: tools/setup/github-access.sh ops-secrets"
                     if os.environ.get("GITHUB_ACTIONS")
                     else f"заполни {self.conf.secrets_dir / 'workshop.env'}")
            raise OpsError(f"не хватает секретов: {', '.join(missing)}, {where}")

    def need_core(self) -> None:
        """Render и админка обязательны. Без ключа LLM сервисы поднимаются, но судья
        и блок cib молчат, пока ключ не появится и не пройдет env."""
        self.need("RENDER_API_KEY", "ADMIN_TOKEN")
        if not self.secrets.get("OPENAI_API_KEY"):
            log("внимание: OPENAI_API_KEY пуст, судья и cib без LLM. Впиши ключ, потом env")

    def owner_id(self) -> str:
        if self._owner:
            return self._owner
        self.need("RENDER_API_KEY")
        owners = self.render.list_all("/owners", "owner")
        wanted = self.secrets.get("RENDER_OWNER_ID")
        if wanted:
            if not any(o.get("id") == wanted for o in owners):
                raise OpsError(f"ключ Render не видит workspace {wanted}")
            self._owner = wanted
        elif len(owners) == 1:
            self._owner = owners[0]["id"]
        else:
            listing = "\n".join(f"  {o.get('id')}  {o.get('name')}  ({o.get('type')})" for o in owners)
            raise OpsError("ключ Render видит несколько workspace. Выбери тот, где карта организаторов, "
                           "и запиши его id в RENDER_OWNER_ID:\n" + listing)
        return self._owner

    def services(self) -> dict:
        if self._services is None:
            owner = self.owner_id()
            names = {t.name for t in self.targets}
            found = self.render.list_all("/services", "service")
            self._services = {s["name"]: s for s in found
                              if s.get("name") in names and s.get("ownerId") in (None, owner)}
        return self._services

    def url(self, target: Target) -> str:
        service = self.services().get(target.name) or {}
        url = (service.get("serviceDetails") or {}).get("url")
        return (url or f"https://{target.name}.onrender.com").rstrip("/")

    def postgres(self) -> dict | None:
        name = f"{self.conf.prefix}-db"
        owner = self.owner_id()
        for pg in self.render.list_all("/postgres", "postgres"):
            if pg.get("name") == name and pg.get("ownerId") in (None, owner):
                return pg
        return None

    def database_url(self, pg: dict) -> str:
        info = self.render.call("GET", f"/postgres/{pg['id']}/connection-info") or {}
        url = info.get("internalConnectionString")
        if not url:
            raise OpsError(f"Render не отдал internalConnectionString у Postgres {pg['id']}")
        return url

    def env_for(self, target: Target, database_url: str | None = None) -> dict[str, str]:
        conf, secrets = self.conf, self.secrets
        llm = {"OPENAI_BASE_URL": conf.llm_base_url, "OPENAI_MODEL": conf.llm_model}
        if secrets.get("OPENAI_API_KEY"):  # пустой ключ не ставим: env потом допишет
            llm["OPENAI_API_KEY"] = secrets["OPENAI_API_KEY"]
        if target.block == "simulator":
            env = {"TEAM_NAMES": ",".join(f"team_{letter}" for letter, _ in conf.teams),
                   **llm, "ADMIN_TOKEN": secrets.get("ADMIN_TOKEN", "")}
            for letter, repo in conf.teams:
                prefix = letter.upper()
                env[f"{prefix}_REPO"] = f"{conf.owner}/{repo}"
                for block in BLOCKS:
                    env[f"{prefix}_{block.upper()}_URL"] = self.url(self.by_key[f"{letter}:{block}"])
            env.update(conf.sim_env)
            if database_url:
                env["DATABASE_URL"] = database_url
            return env
        env = {"TEAM_NAME": f"team_{target.letter}"}
        if target.block in ("cib", "retail"):
            env["BACKEND_URL"] = self.url(self.by_key[f"{target.letter}:backend"])
        if target.block == "cib":
            env.update(llm)
        if target.block == "retail":
            env["CIB_URL"] = self.url(self.by_key[f"{target.letter}:cib"])
        return env

    def select(self, specs: list[str]) -> list[Target]:
        if not specs:
            return list(self.targets)
        chosen: list[Target] = []
        for spec in specs:
            if spec in ("sim", "simulator"):
                picked = [self.by_key["sim"]]
            elif spec in self.by_key:
                picked = [self.by_key[spec]]
            else:
                picked = [t for t in self.targets if t.letter == spec]
            if not picked:
                raise OpsError(f"не знаю цель {spec!r}: sim, буква команды или буква:блок (a:cib)")
            chosen.extend(t for t in picked if t not in chosen)
        return chosen

    # ── создание ──

    def provision(self, dry_run: bool) -> None:
        self.need_core()
        conf = self.conf
        log(f"workspace {self.owner_id()}: команд {len(conf.teams)}, сервисов {len(self.targets)}, "
            f"тариф {conf.plan}, регион {conf.region}")
        pg = self.ensure_postgres(dry_run)
        for target in self.targets:
            if target.block != "simulator":
                self.ensure_service(target, dry_run)
        database_url = None
        if pg and not dry_run:
            database_url = self.wait_postgres(pg)
        self.ensure_service(self.by_key["sim"], dry_run, database_url)
        if dry_run:
            log("(dry-run: ничего не создавал)")
            return
        self.sync_env(dry_run=False, database_url=database_url)
        self.write_services_conf()
        log(NEXT_STEPS)

    def ensure_postgres(self, dry_run: bool) -> dict | None:
        name = f"{self.conf.prefix}-db"
        pg = self.postgres()
        if pg:
            log(f"= {name}: есть, статус {pg.get('status')}")
            return pg
        if dry_run:
            log(f"+ {name}: создам Postgres {self.conf.db_plan}")
            return None
        pg = self.render.call("POST", "/postgres", {
            "name": name, "ownerId": self.owner_id(), "plan": self.conf.db_plan,
            "region": self.conf.region, "version": "16"}) or {}
        log(f"+ {name}: создан {pg.get('id')}, тариф {pg.get('plan')}")
        return pg

    def wait_postgres(self, pg: dict, timeout_s: int = 900) -> str:
        deadline = time.monotonic() + timeout_s
        status = pg.get("status")
        while status != "available":
            if time.monotonic() > deadline:
                raise OpsError(f"Postgres {pg['id']} за {timeout_s // 60} мин не поднялся (статус {status}). "
                               "Повтори provision позже.")
            log(f"  Postgres: {status}, жду")
            self.render.sleep(20)
            status = (self.render.call("GET", f"/postgres/{pg['id']}") or {}).get("status")
        return self.database_url(pg)

    def ensure_service(self, target: Target, dry_run: bool, database_url: str | None = None) -> None:
        service = self.services().get(target.name)
        if service:
            repo = (service.get("repo") or "").rstrip("/")
            repo = repo[:-4] if repo.endswith(".git") else repo
            note = "" if repo.lower() == target.repo_url.lower() else f" (внимание: собирается из {repo})"
            log(f"= {target.name}: есть{note}")
            return
        if dry_run:
            log(f"+ {target.name}: создам из {target.repo_url}, {target.dockerfile}")
            return
        body = {
            "type": "web_service",
            "name": target.name,
            "ownerId": self.owner_id(),
            "repo": target.repo_url,
            "branch": "main",
            "autoDeploy": "no",
            "rootDir": "",
            "envVars": [{"key": k, "value": v} for k, v in self.env_for(target, database_url).items()],
            "serviceDetails": {
                "runtime": "docker",
                "plan": self.conf.plan,
                "region": self.conf.region,
                "healthCheckPath": "/health",
                "numInstances": 1,
                "envSpecificDetails": {"dockerfilePath": target.dockerfile, "dockerContext": "."},
            },
        }
        try:
            created = self.render.call("POST", "/services", body) or {}
        except RateLimited:
            raise
        except OpsError as exc:
            raise OpsError(f"{exc}\n{CREATE_HINT}") from exc
        service = created.get("service", created)
        self.services()[target.name] = service
        log(f"+ {target.name}: создан {service.get('id')}, {self.url(target)}")

    def sync_env(self, dry_run: bool, database_url: str | None = None) -> list[Target]:
        self.need_core()
        if database_url is None:
            pg = self.postgres()
            if pg and pg.get("status") == "available":
                database_url = self.database_url(pg)
        changed: list[Target] = []
        for target in self.targets:
            service = self.services().get(target.name)
            if not service:
                log(f"? {target.name}: сервиса нет, сначала provision")
                continue
            desired = self.env_for(target, database_url)
            current = {e.get("key"): e.get("value")
                       for e in self.render.list_all(f"/services/{service['id']}/env-vars", "envVar")}
            diff = sorted(k for k, v in desired.items() if current.get(k) != v)
            if not diff:
                continue
            log(f"~ {target.name}: env {', '.join(diff)}")  # только имена, значения не печатаем
            if dry_run:
                continue
            merged = {**current, **desired}
            self.render.call("PUT", f"/services/{service['id']}/env-vars",
                             [{"key": k, "value": v} for k, v in sorted(merged.items())])
            changed.append(target)
        for target in changed:
            self.deploy_one(target)
        if not changed and not dry_run:
            log("env всех сервисов совпадает с teams.conf")
        return changed

    def write_services_conf(self) -> None:
        path = services_conf_path()
        lines = [f"# буква блок service-id url; render_ops.py, {datetime.now():%Y-%m-%d %H:%M}"]
        for target in self.targets:
            service = self.services().get(target.name)
            if service:
                lines.append(f"{target.letter or 'sim'} {target.block} {service['id']} {self.url(target)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"записал {path}: сервисов {len(lines) - 1}")

    # ── эксплуатация ──

    def deploy_one(self, target: Target) -> None:
        service = self.services().get(target.name)
        if not service:
            raise OpsError(f"{target.name}: сервиса на Render нет, сначала provision")
        result = self.render.call("POST", f"/services/{service['id']}/deploys", {"clearCache": "do_not_clear"})
        log(f"^ {target.name}: деплой {(result or {}).get('id') or 'в очереди'}")

    def deploy(self, specs: list[str], skip_missing: bool) -> None:
        for target in self.select(specs):
            if skip_missing and target.name not in self.services():
                log(f"::notice::{target.name}: сервиса на Render нет, деплой пропущен")
                continue
            self.deploy_one(target)

    def status(self) -> int:
        services = self.services()
        present = [t for t in self.targets if t.name in services]
        with ThreadPoolExecutor(max_workers=8) as pool:
            health = dict(zip((t.key for t in present),
                              pool.map(lambda t: fetch(self.url(t) + "/health", timeout=60), present)))
        rows, problems = [], 0
        for target in self.targets:
            service = services.get(target.name)
            if not service:
                rows.append((target.key, target.name, "нет сервиса", "", "", ""))
                problems += 1
                continue
            listing = self.render.call("GET", f"/services/{service['id']}/deploys", query={"limit": 1}) or []
            deploy = (listing[0].get("deploy", listing[0]) if listing else None) or {}
            commit = ((deploy.get("commit") or {}).get("id") or "")[:7]
            details = service.get("serviceDetails") or {}
            plan = details.get("plan", "") + (" suspended" if service.get("suspended") == "suspended" else "")
            code, info = health[target.key]
            verdict = str(code) if code else "нет ответа"
            if target.block == "simulator" and code == 200 and isinstance(info, dict):
                verdict += " БД есть" if info.get("db") else " без БД"
            if code != 200:
                problems += 1
            rows.append((target.key, self.url(target), deploy.get("status", "деплоев нет"), commit, plan, verdict))
        print_table(("цель", "url", "деплой", "коммит", "тариф", "/health"), rows)
        if present:
            self.write_services_conf()
        log(f"проблем: {problems}" if problems else "все сервисы отвечают 200")
        return 1 if problems else 0

    def set_plan(self, plan: str) -> None:
        if not re.fullmatch(r"[a-z0-9_.-]+", plan):
            raise OpsError(f"странный тариф {plan!r}: free, starter, standard")
        for target in self.targets:
            service = self.services().get(target.name)
            if not service:
                continue
            current = (service.get("serviceDetails") or {}).get("plan")
            if current == plan:
                log(f"= {target.name}: уже {plan}")
                continue
            self.render.call("PATCH", f"/services/{service['id']}", {"serviceDetails": {"plan": plan}})
            log(f"~ {target.name}: {current} -> {plan}")

    def suspend(self, action: str) -> None:
        for target in self.targets:
            service = self.services().get(target.name)
            if not service:
                continue
            suspended = service.get("suspended") == "suspended"
            if suspended == (action == "suspend"):
                log(f"= {target.name}: уже {'suspended' if suspended else 'работает'}")
                continue
            self.render.call("POST", f"/services/{service['id']}/{action}")
            log(f"~ {target.name}: {action}")

    def teardown(self, confirm: str) -> None:
        if confirm != "DELETE":
            raise OpsError("teardown безвозвратно удаляет сервисы и базу воркшопа вместе с табло. "
                           "Запусти с --confirm DELETE")
        for target in self.targets:
            service = self.services().get(target.name)
            if service:
                self.render.call("DELETE", f"/services/{service['id']}")
                log(f"- {target.name}")
        pg = self.postgres()
        if pg:
            self.render.call("DELETE", f"/postgres/{pg['id']}")
            log(f"- {pg.get('name')}")
        path = services_conf_path()
        if path.exists():
            path.unlink()

    def drop(self, names: list[str], confirm: str) -> None:
        """Удалить чужие сервисы и базы workspace по именам: освободить лимит Hobby."""
        if confirm != "DELETE":
            raise OpsError("drop безвозвратно удаляет сервисы и базы из workspace. Запусти с --confirm DELETE")
        ours = {t.name for t in self.targets} | {f"{self.conf.prefix}-db"}
        if set(names) & ours:
            raise OpsError(f"{', '.join(sorted(set(names) & ours))}: это воркшоп, для него teardown")
        owner = self.owner_id()
        services = {s.get("name"): s for s in self.render.list_all("/services", "service")
                    if s.get("ownerId") in (None, owner)}
        databases = {p.get("name"): p for p in self.render.list_all("/postgres", "postgres")
                     if p.get("ownerId") in (None, owner)}
        for name in names:
            if name in services:
                self.render.call("DELETE", f"/services/{services[name]['id']}")
                log(f"- {name}")
            elif name in databases:
                self.render.call("DELETE", f"/postgres/{databases[name]['id']}")
                log(f"- {name} (postgres)")
            else:
                log(f"? {name}: в workspace нет")

    def check(self) -> None:
        code, _ = fetch(self.render.base + "/owners", timeout=20)
        if code == 0:
            raise OpsError(f"{self.render.base} недоступен. {CORP_HINT}")
        if code in (401, 403):
            log(f"{self.render.base}: доступен")
        else:
            log(f"{self.render.base}: без ключа ответил {code}, похоже на фильтр сети. {CORP_HINT}")
        self.need("RENDER_API_KEY")
        for owner in self.render.list_all("/owners", "owner"):
            log(f"workspace {owner.get('id')}  {owner.get('name')}  ({owner.get('type')})")
        log(f"работаю в {self.owner_id()}")
        services = self.services()
        log(f"сервисов воркшопа на Render: {len(services)} из {len(self.targets)}")
        missing = [t.name for t in self.targets if t.name not in services]
        if missing:
            log("нет: " + ", ".join(missing))
        pg = self.postgres()
        log(f"Postgres {self.conf.prefix}-db: {pg.get('status') if pg else 'нет'}")
        self.log_foreign()
        for key in ("OPENAI_API_KEY", "ADMIN_TOKEN"):
            log(f"{key}: {'задан' if self.secrets.get(key) else 'НЕТ'}")

    def log_foreign(self) -> None:
        """Чужие сервисы и базы в workspace: на Hobby они съедают общий лимит в 25."""
        owner, ours = self.owner_id(), {t.name for t in self.targets} | {f"{self.conf.prefix}-db"}
        foreign = [(s.get("name"), s.get("type"), "suspended" if s.get("suspended") == "suspended" else "",
                    (s.get("createdAt") or "")[:10])
                   for s in self.render.list_all("/services", "service")
                   if s.get("ownerId") in (None, owner) and s.get("name") not in ours]
        foreign += [(p.get("name"), "postgres", p.get("status") or "", (p.get("createdAt") or "")[:10])
                    for p in self.render.list_all("/postgres", "postgres")
                    if p.get("ownerId") in (None, owner) and p.get("name") not in ours]
        if not foreign:
            log("чужих сервисов в workspace нет")
            return
        log(f"чужих сервисов и баз в workspace: {len(foreign)}, на Hobby они в общем лимите 25:")
        for name, kind, state, created in sorted(foreign):
            log(f"  {name}  {kind}  {state}  создан {created}".rstrip())

    def sim(self, action: str) -> None:
        base = (os.environ.get("SIM_URL") or "").rstrip("/") or self.url(self.by_key["sim"])
        if action == "state":
            code, data = fetch(base + "/state", timeout=90)
            if code != 200 or not isinstance(data, dict):
                hint = f" {CORP_HINT}" if code in (0, 302) or not isinstance(data, dict) else ""
                raise OpsError(f"{base}/state ответил {code}: {str(data)[:200]}.{hint}")
            print_state(data)
            return
        self.need("ADMIN_TOKEN")
        code, data = fetch(f"{base}/admin/{action}", method="POST", timeout=180,
                           headers={"X-Admin-Token": self.secrets["ADMIN_TOKEN"]})
        if code != 200:
            raise OpsError(f"{base}/admin/{action}: HTTP {code} {str(data)[:300]}")
        log(json.dumps(data, ensure_ascii=False, indent=2))


def print_state(data: dict) -> None:
    started = data.get("workshop_started")
    log(f"воркшоп {'идет' if started else 'не идет'}, старт: {data.get('workshop_started_at') or 'не было'}")
    rows = []
    teams = sorted((data.get("teams") or {}).items(), key=lambda kv: -(kv[1].get("client_base") or 0))
    for team, st in teams:
        idle = st.get("idle_seconds")
        delta = st.get("delta_from_start")
        rows.append((team, st.get("client_base"), f"{delta:+d}" if isinstance(delta, int) else "",
                     st.get("feature_state"), st.get("releases"),
                     f"{idle // 60} мин" if isinstance(idle, int) else ""))
    print_table(("команда", "клиенты", "с начала", "фича", "релизы", "простой"), rows)
    events = data.get("events") or []
    if events:
        log("последние события:")
    for event in events[:8]:
        parts = [str(event.get(k)) for k in ("ts", "team", "delta", "reason") if event.get(k) not in (None, "")]
        log("  " + " | ".join(parts)[:240])


def init_secrets(conf: Conf) -> None:
    path = conf.secrets_dir / "workshop.env"
    if path.exists():
        log(f"= {path}: уже есть, не трогаю")
        return
    conf.secrets_dir.mkdir(parents=True, exist_ok=True)
    conf.secrets_dir.chmod(0o700)
    text = SECRETS_TEMPLATE.format(workshop_id=conf.workshop_id, admin_token=pysecrets.token_urlsafe(24))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    log(f"+ {path}: ADMIN_TOKEN сгенерирован, RENDER_API_KEY и OPENAI_API_KEY впиши сам")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="render_ops.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-secrets")
    sub.add_parser("check")
    sub.add_parser("provision").add_argument("--dry-run", action="store_true")
    sub.add_parser("env").add_argument("--dry-run", action="store_true")
    sub.add_parser("services")
    sub.add_parser("status")
    deploy = sub.add_parser("deploy")
    deploy.add_argument("targets", nargs="*")
    deploy.add_argument("--skip-missing", action="store_true")
    sub.add_parser("plan").add_argument("plan")
    sub.add_parser("suspend")
    sub.add_parser("resume")
    sub.add_parser("teardown").add_argument("--confirm", default="")
    drop = sub.add_parser("drop")
    drop.add_argument("names", nargs="+")
    drop.add_argument("--confirm", default="")
    sub.add_parser("sim").add_argument("action", choices=SIM_ACTIONS)
    args = parser.parse_args(argv)

    try:
        conf = load_conf()
        if args.command == "init-secrets":
            init_secrets(conf)
            return 0
        secrets = load_secrets(conf)
        render = Render(secrets.get("RENDER_API_KEY", ""), os.environ.get("RENDER_API_BASE") or API_BASE)
        workshop = Workshop(conf, secrets, render)
        if args.command == "check":
            workshop.check()
        elif args.command == "provision":
            workshop.provision(args.dry_run)
        elif args.command == "env":
            workshop.sync_env(args.dry_run)
        elif args.command == "services":
            workshop.write_services_conf()
        elif args.command == "status":
            return workshop.status()
        elif args.command == "deploy":
            workshop.deploy(args.targets, args.skip_missing)
        elif args.command == "plan":
            workshop.set_plan(args.plan)
        elif args.command in ("suspend", "resume"):
            workshop.suspend(args.command)
        elif args.command == "teardown":
            workshop.teardown(args.confirm)
        elif args.command == "drop":
            workshop.drop(args.names, args.confirm)
        elif args.command == "sim":
            workshop.sim(args.action)
        return 0
    except (OpsError, ConfError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error::{str(exc).splitlines()[0]}", flush=True)
        return EX_TEMPFAIL if isinstance(exc, RateLimited) else 1


if __name__ == "__main__":
    sys.exit(main())
