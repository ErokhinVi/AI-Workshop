#!/usr/bin/env python3
"""
cowork-onboard.py — sandbox-side onboarding для Claude Code в Cowork mode.

Запускается агентом первым делом при старте сессии (см. корневой CLAUDE.md, Шаг 0).

Что делает:
  1. Берёт SSH-ключ воркшопа из .git/raif-workshop-key.
  2. Прописывает его в $HOME/.ssh/ внутри sandbox-а Claude.
  3. Тестирует подключение к GitHub.
  4. Прописывает git config user.name / user.email из .git/raif-workshop-info.
  5. Поднимает Linux-side git-dir в /tmp/raif-git и git-шим в /tmp/bin/git, чтобы
     все git-операции писали .lock-и на Linux ext4 (а не на virtiofs-mount, где
     unlink периодически не проходит и .git залипает).
  6. Best-effort чистит .git/*.lock на Windows-mount-е (если осталось от прежних
     запусков под нестабильным git-ом).
  7. Печатает на stdout machine-readable сводку.

Идемпотентен — повторные вызовы ничего не ломают.

Если ключа нет (старый bootstrap) — выход с кодом 2.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("WORKSHOP_REPO_ROOT") or Path(__file__).resolve().parents[1])
WIN_GIT_DIR = REPO_ROOT / ".git"
KEY_SRC = WIN_GIT_DIR / "raif-workshop-key"
INFO_SRC = WIN_GIT_DIR / "raif-workshop-info"

HOME = Path(os.environ["HOME"])
SSH_DIR = HOME / ".ssh"
KEY_DST = SSH_DIR / "raif_workshop"
SSH_CONFIG = SSH_DIR / "config"
KNOWN_HOSTS = SSH_DIR / "known_hosts"

# Linux-side git-dir и шим. /tmp на ext4 — unlink работает всегда.
LINUX_GIT_DIR = Path("/tmp/raif-git")
SHIM_DIR = Path("/tmp/bin")
SHIM_PATH = SHIM_DIR / "git"

SSH_CONFIG_MARKER = "# raif-workshop-2026"
SSH_CONFIG_BLOCK = f"""
{SSH_CONFIG_MARKER}
Host github.com
  HostName github.com
  User git
  IdentityFile {KEY_DST}
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
"""

GIT_LOCK_FILES = [
    "HEAD.lock", "index.lock", "packed-refs.lock", "config.lock",
    "REBASE_HEAD.lock", "MERGE_HEAD.lock", "FETCH_HEAD.lock",
    "ORIG_HEAD.lock", "shallow.lock", "gc.pid.lock",
    "objects/maintenance.lock",
]

GIT_CONFIG_HARDENING = [
    ("core.autocrlf", "false"),
    ("core.eol", "lf"),
    ("core.fileMode", "false"),
    ("core.fsmonitor", "false"),
    ("core.untrackedCache", "false"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
    ("pull.rebase", "true"),
]


def step(m): print(f"-> {m}", flush=True)
def ok(m):   print(f"  + {m}", flush=True)
def warn(m): print(f"  ! {m}", flush=True)
def die(m, code=1):
    print(f"x {m}", file=sys.stderr, flush=True)
    sys.exit(code)


def setup_ssh() -> None:
    if not KEY_SRC.exists():
        die(
            "SSH-ключ воркшопа не найден в .git/raif-workshop-key. "
            "Bootstrap либо не запускали, либо у участника старая версия. "
            "Без ключа push на GitHub из sandbox-а не пойдёт.",
            code=2,
        )
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile(KEY_SRC, KEY_DST)
    KEY_DST.chmod(0o600)
    ok(f"Ключ: {KEY_DST}")

    cfg = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    if SSH_CONFIG_MARKER not in cfg:
        with SSH_CONFIG.open("a") as f:
            f.write(SSH_CONFIG_BLOCK)
        SSH_CONFIG.chmod(0o600)
        ok(f"Запись для github.com дописана в {SSH_CONFIG}")
    else:
        ok(f"{SSH_CONFIG} уже содержит запись для github.com")

    res = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519,ecdsa,rsa", "github.com"],
        capture_output=True, text=True, timeout=10,
    )
    if res.returncode == 0 and res.stdout:
        KNOWN_HOSTS.write_text(res.stdout)
        KNOWN_HOSTS.chmod(0o600)
        ok(f"known_hosts обновлён ({len(res.stdout.splitlines())} записей)")
    else:
        warn("ssh-keyscan не вернул ключи; полагаемся на accept-new")


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
    subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    ok(f"git config global: {name} <{email}>")


def setup_linux_gitdir() -> None:
    """
    Главная защита от virtiofs-induced .lock-болезни.

    Кладёт копию .git/ в /tmp/raif-git (Linux ext4) и ставит git-шим в /tmp/bin,
    который автоматически перенаправляет любую команду `git ...` на
    `git --git-dir=/tmp/raif-git --work-tree=<windows-mount> ...`.

    Сторона участника на Windows-mount остаётся как есть (там и так нечего
    делать — он не запускает git руками). А все локи теперь живут на /tmp,
    где unlink работает всегда.
    """
    if not WIN_GIT_DIR.exists():
        warn("Windows-mount .git не существует — пропускаю Linux-side git-dir")
        return

    # Свежий /tmp/raif-git — если /tmp пересоздавался (новая сессия sandbox),
    # копия нужна заново. Если уже есть — обновим объекты/refs.
    LINUX_GIT_DIR.mkdir(parents=True, exist_ok=True)

    # rsync был бы идеален, но не везде есть. Используем cp -r с фильтром локов.
    # Сначала — слить актуальные refs/objects из Windows-mount.
    try:
        subprocess.run(
            ["cp", "-r", "--update", f"{WIN_GIT_DIR}/.", str(LINUX_GIT_DIR)],
            check=False, capture_output=True,
        )
    except Exception as exc:
        warn(f"cp .git -> /tmp/raif-git: {exc}")

    # Снести любые .lock в Linux-копии (тут unlink проходит).
    for rel in GIT_LOCK_FILES:
        p = LINUX_GIT_DIR / rel
        if p.exists():
            try: p.unlink()
            except OSError: pass
    for p in (LINUX_GIT_DIR / "refs").rglob("*.lock") if (LINUX_GIT_DIR / "refs").exists() else []:
        try: p.unlink()
        except OSError: pass

    # Шим git → реальный git с --git-dir и --work-tree.
    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    shim = (
        "#!/bin/bash\n"
        "# Авто-сгенерированный шим: уводит .git-метаданные с virtiofs на ext4.\n"
        f'exec /usr/bin/git --git-dir={LINUX_GIT_DIR} '
        f'--work-tree={REPO_ROOT} "$@"\n'
    )
    SHIM_PATH.write_text(shim)
    SHIM_PATH.chmod(0o755)
    ok(f"Linux-side git-dir: {LINUX_GIT_DIR} ({sum(1 for _ in LINUX_GIT_DIR.rglob('*'))} файлов)")
    ok(f"git-шим: {SHIM_PATH}  (PATH=/tmp/bin:$PATH чтобы перехватить, либо вызывай напрямую)")


def harden_git_config() -> None:
    """Repo-local hardening — дублирует Windows-side bootstrap на всякий случай."""
    git = str(SHIM_PATH) if SHIM_PATH.exists() else "git"
    for k, v in GIT_CONFIG_HARDENING:
        subprocess.run([git, "config", "--local", k, v], check=False, capture_output=True)
    subprocess.run([git, "maintenance", "unregister"], check=False, capture_output=True)
    ok("git config: autocrlf=off, fsmonitor=off, gc.auto=0, maintenance=off")


def cleanup_stale_locks_on_mount() -> list[str]:
    """
    Best-effort: пробуем снять .lock на Windows-mount. Чаще всего не дадут
    (virtiofs/Windows-handle), и это OK — мы всё равно используем /tmp/raif-git,
    так что эти локи никого не блокируют.
    """
    if not WIN_GIT_DIR.exists():
        return []
    stuck: list[str] = []
    for rel in GIT_LOCK_FILES:
        p = WIN_GIT_DIR / rel
        if not p.exists(): continue
        try: p.unlink()
        except OSError: stuck.append(rel)
    refs = WIN_GIT_DIR / "refs"
    if refs.exists():
        for p in refs.rglob("*.lock"):
            try: p.unlink()
            except OSError: stuck.append(str(p.relative_to(WIN_GIT_DIR)))
    if stuck:
        warn(f"Локи на Windows-mount не сняли (но это не блокирует, см. /tmp/raif-git): {', '.join(stuck)}")
    return stuck


def test_github() -> bool:
    try:
        res = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "git@github.com"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        warn("ssh -T github.com — таймаут")
        return False
    out = res.stderr + res.stdout
    if "successfully authenticated" in out:
        ok("GitHub принял ключ")
        return True
    warn(f"GitHub не подтвердил ключ: {out.strip()[:200]}")
    return False


def main() -> int:
    step("Настраиваю SSH в sandbox-е")
    setup_ssh()

    step("Читаю мета-инфо участника")
    info = parse_info()
    if info:
        ok(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK','?')}  "
           f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT','?')}")
    else:
        warn("info-файла нет — Claude должен будет спросить имя")

    step("Прописываю git identity")
    setup_git_identity(info)

    step("Поднимаю Linux-side git-dir + шим (защита от virtiofs lock-болезни)")
    setup_linux_gitdir()

    step("Закрепляю repo-local git config")
    harden_git_config()

    step("Best-effort: чищу залипшие локи на Windows-mount")
    cleanup_stale_locks_on_mount()

    step("Стучусь к GitHub")
    test_github()

    print("\n=== READY ===")
    print(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK', '')}")
    print(f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '')}")
    print(f"WORKSHOP_GIT_NAME={info.get('WORKSHOP_GIT_NAME', '')}")
    print(f"GIT_SHIM={SHIM_PATH}")
    print("GIT_USAGE=PATH=/tmp/bin:$PATH перед git-командой, или вызывай /tmp/bin/git напрямую.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
