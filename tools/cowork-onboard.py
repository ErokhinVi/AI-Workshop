#!/usr/bin/env python3
"""
cowork-onboard.py — sandbox-side onboarding для Claude Code в Cowork mode.

Запускается агентом первым делом при старте сессии (см. корневой CLAUDE.md, Шаг 0).

Что делает:
  1. Берёт SSH-ключ воркшопа из .git/raif-workshop-key (его кладёт macbook-овский bootstrap).
  2. Прописывает его в $HOME/.ssh/ внутри sandbox-а Claude.
  3. Поднимает «теневой» git-dir в $HOME/.cache/raif-git/git-dir — копию <repo>/.git
     внутри sandbox-FS, где unlink работает. Все git-операции агент делает через
     него (см. tools/g), mount-овский <repo>/.git не трогается.
  4. Тестирует подключение к GitHub.
  5. Прописывает git config user.name / user.email из .git/raif-workshop-info.
  6. Печатает на stdout machine-readable сводку: WORKSHOP_BLOCK, имя, статус.

Идемпотентен: можно дёргать сколько угодно раз — повторные вызовы ничего не ломают.

Если ключа нет (участник прогнал старую версию bootstrap-а или не прогнал вовсе) —
скрипт мягко завершается с кодом 2 и понятным сообщением. Тогда Claude переходит
к ручному онбордингу (Шаг 1 — спросить имя).

Зачем shadow git-dir: mount-FS Cowork-режима не разрешает sandbox-у unlink файлов
из <repo>/.git. Любой залипший .lock (index.lock, HEAD.lock, *.tmp) убивает все
последующие git-команды, и почистить его агент не может — это должен сделать
владелец на маке. Чтобы топ-менеджер не упирался в это, shadow git-dir живёт в
sandbox-FS, где unlink работает нормально, а mount/.git вообще не используется.

Без зависимостей — только stdlib.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("WORKSHOP_REPO_ROOT") or Path(__file__).resolve().parents[1])
KEY_SRC = REPO_ROOT / ".git" / "raif-workshop-key"
INFO_SRC = REPO_ROOT / ".git" / "raif-workshop-info"

HOME = Path(os.environ["HOME"])
SSH_DIR = HOME / ".ssh"
KEY_DST = SSH_DIR / "raif_workshop"
SSH_CONFIG = SSH_DIR / "config"
KNOWN_HOSTS = SSH_DIR / "known_hosts"

SHADOW_GIT_DIR = Path(
    os.environ.get("RAIF_SHADOW_GIT_DIR") or (HOME / ".cache" / "raif-git" / "git-dir")
)
MOUNT_GIT_DIR = REPO_ROOT / ".git"

SSH_CONFIG_MARKER = "# raif-workshop-2026"
SSH_CONFIG_END_MARKER = "# /raif-workshop-2026"
# В sandbox-е Cowork-режима исходящий tcp:22 наружу часто закрыт корпоративной сетью —
# обычный github.com:22 виснет по таймауту. ssh.github.com:443 работает везде, где
# вообще выпускают наружу https, поэтому всегда садимся на него.
SSH_CONFIG_BLOCK = f"""
{SSH_CONFIG_MARKER}
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile {KEY_DST}
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
{SSH_CONFIG_END_MARKER}
"""


def step(msg: str) -> None:
    print(f"→ {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  ! {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"✗ {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def setup_ssh() -> None:
    if not KEY_SRC.exists():
        die(
            "Ключ воркшопа не найден в .git/raif-workshop-key — похоже, "
            "bootstrap_workshop.sh не запускали или у участника старая версия. "
            "Без ключа push на GitHub из sandbox не пойдёт.",
            code=2,
        )
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(KEY_SRC, KEY_DST)
    KEY_DST.chmod(0o600)
    ok(f"Ключ: {KEY_DST}")

    cfg = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    needs_write = SSH_CONFIG_MARKER not in cfg
    needs_migration = (
        SSH_CONFIG_MARKER in cfg
        and "ssh.github.com" not in cfg.split(SSH_CONFIG_MARKER, 1)[1].split(SSH_CONFIG_END_MARKER, 1)[0]
    )

    if needs_migration:
        # Старый блок (порт 22) — выкусываем и заменяем актуальным.
        before, _, rest = cfg.partition(SSH_CONFIG_MARKER)
        if SSH_CONFIG_END_MARKER in rest:
            _, _, after = rest.partition(SSH_CONFIG_END_MARKER)
        else:
            # Очень старая версия без end-маркера — режем до конца файла.
            after = ""
        cfg = before.rstrip() + "\n"
        SSH_CONFIG.write_text(cfg + after.lstrip())
        SSH_CONFIG.chmod(0o600)
        needs_write = True
        warn("Старый блок github.com (порт 22) удалён — переписываю на ssh.github.com:443")

    if needs_write:
        with SSH_CONFIG.open("a") as f:
            f.write(SSH_CONFIG_BLOCK)
        SSH_CONFIG.chmod(0o600)
        ok(f"Запись для github.com (через ssh.github.com:443) записана в {SSH_CONFIG}")
    else:
        ok(f"{SSH_CONFIG} уже содержит актуальную запись для github.com")

    res = subprocess.run(
        ["ssh-keyscan", "-p", "443", "-t", "ed25519,ecdsa,rsa", "ssh.github.com"],
        capture_output=True, text=True, timeout=10,
    )
    if res.returncode == 0 and res.stdout:
        KNOWN_HOSTS.write_text(res.stdout)
        KNOWN_HOSTS.chmod(0o600)
        ok(f"known_hosts обновлён ({len(res.stdout.splitlines())} записей)")
    else:
        warn("ssh-keyscan не вернул ключи; полагаемся на StrictHostKeyChecking=accept-new")


def _is_valid_git_dir(p: Path) -> bool:
    """Грубая проверка: похоже ли это на живой git-dir."""
    return p.is_dir() and (p / "HEAD").is_file() and (p / "objects").is_dir()


def _clean_locks(p: Path) -> int:
    """Удалить из shadow git-dir все *.lock и tmp_obj_* (которые могли заехать
    из mount/.git вместе с cp -a). В нормальной FS unlink работает."""
    n = 0
    for lock in p.rglob("*.lock"):
        try:
            lock.unlink()
            n += 1
        except OSError:
            pass
    for tmp in p.glob("objects/*/tmp_obj_*"):
        try:
            tmp.unlink()
            n += 1
        except OSError:
            pass
    return n


def setup_shadow_git_dir() -> None:
    """Поднимает shadow git-dir в sandbox-FS. Идемпотентно.

    Если shadow уже есть и валиден — ничего не делаем (даже не пересобираем).
    Это важно: между сессиями там лежит история коммитов агента, которые в
    mount/.git ещё не подтянуты (mount-овский .git обновится только когда
    пользователь сделает git pull со своего мака).
    """
    if _is_valid_git_dir(SHADOW_GIT_DIR):
        cleaned = _clean_locks(SHADOW_GIT_DIR)
        if cleaned:
            ok(f"shadow git-dir на месте, почистил {cleaned} залежавшихся .lock")
        else:
            ok(f"shadow git-dir уже готов: {SHADOW_GIT_DIR}")
        return

    if not _is_valid_git_dir(MOUNT_GIT_DIR):
        die(
            f"В {REPO_ROOT} не вижу .git — не из чего поднимать shadow git-dir.",
            code=1,
        )

    SHADOW_GIT_DIR.parent.mkdir(parents=True, exist_ok=True)
    # cp -a копирует с правами/симлинками; точка в конце — чтобы скопировать
    # содержимое mount/.git, а не вложить его как подпапку.
    res = subprocess.run(
        ["cp", "-a", f"{MOUNT_GIT_DIR}/.", str(SHADOW_GIT_DIR)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        die(
            f"cp .git в shadow упал: {res.stderr.strip() or res.stdout.strip()}",
            code=1,
        )

    cleaned = _clean_locks(SHADOW_GIT_DIR)
    ok(f"shadow git-dir поднят: {SHADOW_GIT_DIR}"
       + (f" (вычистил {cleaned} .lock из mount-копии)" if cleaned else ""))

    # На всякий случай отвяжем shadow от mount-овских worktree-указателей.
    # Один git-dir на оба work-tree корректнее не делать, но фактически мы и
    # пользуемся им только через `git --git-dir=... --work-tree=<mount>`.


def parse_info() -> dict[str, str]:
    if not INFO_SRC.exists():
        return {}
    info: dict[str, str] = {}
    for line in INFO_SRC.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        info[k.strip()] = v.strip().strip('"').strip("'")
    return info


def setup_git_identity(info: dict[str, str]) -> None:
    name = info.get("WORKSHOP_GIT_NAME")
    email = info.get("WORKSHOP_GIT_EMAIL")
    if not name or not email:
        warn("В info-файле нет WORKSHOP_GIT_NAME/EMAIL — git config не трогаю")
        return
    subprocess.run(["git", "config", "--global", "user.name", name], check=True)
    subprocess.run(["git", "config", "--local", "user.name", name], cwd=str(REPO_ROOT), check=False)
    subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    subprocess.run(["git", "config", "--local", "user.email", email], cwd=str(REPO_ROOT), check=False)
    ok(f"git config: {name} <{email}>")


def test_github() -> bool:
    try:
        res = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "git@github.com"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        warn("ssh -T github.com — таймаут (нет сети?)")
        return False
    out = res.stderr + res.stdout
    if "successfully authenticated" in out:
        ok("GitHub принял ключ")
        return True
    warn(f"GitHub не подтвердил ключ: {out.strip()[:200]}")
    return False


def main() -> int:
    step("Проверяю/настраиваю SSH в sandbox-е")
    setup_ssh()

    step("Поднимаю shadow git-dir (mount/.git трогать не будем)")
    setup_shadow_git_dir()

    step("Читаю мета-инфо участника")
    info = parse_info()
    if info:
        ok(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK','?')}  "
           f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT','?')}")
    else:
        warn("info-файла нет — Claude должен будет спросить имя")

    step("Прописываю git identity")
    setup_git_identity(info)

    step("Стучусь к GitHub")
    test_github()

    print("\n=== READY ===")
    print(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK', '')}")
    print(f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '')}")
    print(f"WORKSHOP_GIT_NAME={info.get('WORKSHOP_GIT_NAME', '')}")
    print(f"RAIF_SHADOW_GIT_DIR={SHADOW_GIT_DIR}")
    print("USE_GIT_VIA=tools/g  # никогда не вызывай `git` напрямую в sandbox-е")
    return 0


if __name__ == "__main__":
    sys.exit(main())
