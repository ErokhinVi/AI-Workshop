"""tools/setup/workshop_conf.py: настройки воркшопа из teams.conf и секреты.

Общий модуль render_ops.py и make-bootstrap.py. teams.conf разбирает сам bash
(source), поэтому shell-скрипты и Python видят одно и то же.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONF = ROOT / "tools/setup/teams.conf"
SCALARS = ("GH_OWNER", "ORCHESTRATOR_REPO", "WORKSHOP_ID", "RENDER_PREFIX", "RENDER_REGION",
           "RENDER_PLAN", "RENDER_DB_PLAN", "LLM_BASE_URL", "LLM_MODEL", "COMMIT_NAME", "COMMIT_EMAIL")
SECRET_KEYS = ("RENDER_API_KEY", "OPENAI_API_KEY", "ADMIN_TOKEN", "RENDER_OWNER_ID")

_READER = r'''
source "$1" || exit 3
for k in @SCALARS@; do eval "v=\${$k-}"; printf '%s=%s\n' "$k" "$v"; done
for x in "${TEAMS[@]}"; do printf 'TEAMS=%s\n' "$x"; done
for x in ${SIM_ENV[@]+"${SIM_ENV[@]}"}; do printf 'SIM_ENV=%s\n' "$x"; done
'''


class ConfError(ValueError):
    """teams.conf заполнен неправильно. Текст для человека."""


@dataclass
class Conf:
    path: Path
    owner: str
    orchestrator_repo: str
    workshop_id: str
    prefix: str
    region: str
    plan: str
    db_plan: str
    llm_base_url: str
    llm_model: str
    teams: list = field(default_factory=list)       # [(буква, репозиторий)]
    sim_env: dict = field(default_factory=dict)
    commit_name: str = ""
    commit_email: str = ""

    @property
    def secrets_dir(self) -> Path:
        override = os.environ.get("WORKSHOP_SECRETS")
        if override:
            return Path(override).expanduser()
        return Path.home() / "AI-Workshop-secrets" / self.workshop_id


def load_conf(path: Path | None = None) -> Conf:
    path = Path(path or os.environ.get("WORKSHOP_CONF") or DEFAULT_CONF)
    if not path.is_file():
        raise ConfError(f"нет файла {path}")
    script = _READER.replace("@SCALARS@", " ".join(SCALARS))
    proc = subprocess.run(["bash", "-c", script, "teams.conf", str(path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConfError(f"bash не смог прочитать {path}: {proc.stderr.strip()}")

    values: dict[str, str] = {}
    teams: list[tuple[str, str]] = []
    sim_env: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "TEAMS":
            letter, sep, repo = value.partition(":")
            if not sep or not re.fullmatch(r"[a-z]", letter) or not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
                raise ConfError(f"TEAMS в {path}: ждал буква:репозиторий, получил {value!r}")
            teams.append((letter, repo))
        elif key == "SIM_ENV":
            name, sep, env_value = value.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
                raise ConfError(f"SIM_ENV в {path}: ждал КЛЮЧ=значение, получил {value!r}")
            sim_env[name] = env_value
        else:
            values[key] = value

    if not teams:
        raise ConfError(f"TEAMS в {path} пуст")
    letters = [letter for letter, _ in teams]
    if len(set(letters)) != len(letters):
        raise ConfError(f"TEAMS в {path}: буквы команд повторяются")

    def pick(name: str, pattern: str, default: str = "") -> str:
        value = values.get(name) or default
        if not re.fullmatch(pattern, value):
            raise ConfError(f"{name} в {path}: неверное значение {value!r}")
        return value

    return Conf(
        path=path,
        owner=pick("GH_OWNER", r"[A-Za-z0-9][A-Za-z0-9-]{0,38}"),
        orchestrator_repo=pick("ORCHESTRATOR_REPO", r"[A-Za-z0-9._-]+", "AI-Workshop"),
        workshop_id=pick("WORKSHOP_ID", r"[A-Za-z0-9._-]+"),
        prefix=pick("RENDER_PREFIX", r"[a-z][a-z0-9-]{1,40}"),
        region=pick("RENDER_REGION", r"[a-z]+", "frankfurt"),
        plan=pick("RENDER_PLAN", r"[a-z0-9_.-]+", "starter"),
        db_plan=pick("RENDER_DB_PLAN", r"[a-z0-9_.-]+", "basic_256mb"),
        llm_base_url=pick("LLM_BASE_URL", r"https?://\S+", "https://api.openai.com/v1"),
        llm_model=pick("LLM_MODEL", r"\S+", "gpt-4o-mini"),
        teams=teams,
        sim_env=sim_env,
        commit_name=values.get("COMMIT_NAME", ""),
        commit_email=values.get("COMMIT_EMAIL", ""),
    )


def load_secrets(conf: Conf) -> dict[str, str]:
    """Секреты из workshop.env, поверх них окружение. Пустые значения не считаются."""
    found: dict[str, str] = {}
    env_file = conf.secrets_dir / "workshop.env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key in SECRET_KEYS and value:
                found[key] = value
    for key in SECRET_KEYS:
        if os.environ.get(key):
            found[key] = os.environ[key]
    return found
