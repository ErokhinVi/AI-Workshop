#!/usr/bin/env python3
"""
cowork-onboard.py — sandbox-side onboarding for Claude Code in Cowork mode.

The agent runs this first at session start (see root CLAUDE.md, Step 0).

This is the **team-repo** version: each team works in its own GitHub
repository, so there is no team picker. The script only cares about which
block (retail / cib / backend) the participant owns. The team identity is
implicit — it's whichever team repo was cloned.

What it does:
  1. Reads the workshop SSH key from .git/raif-workshop-key.
  2. Installs it under $HOME/.ssh/ inside Claude's sandbox.
  3. Tests the GitHub connection.
  4. Sets git config user.name / user.email from .git/raif-workshop-info.
  5. If the repo is mounted via virtiofs (participant on Windows), promotes
     a copy of the git-dir to ext4 (/tmp/raif-git) and installs a git shim
     at /tmp/bin/git so git's .lock files land where unlink works. On macOS
     and native Linux there is no virtiofs — the shim is not installed and
     we use the regular git.
  6. Best-effort cleanup of .git/*.lock on the Windows mount (from earlier
     runs under an unstable git).
  7. Prints a machine-readable summary to stdout.

Idempotent — repeat runs do not break anything.

If the key is missing (older bootstrap) — exits with code 2.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_common_git_dir(repo_root: Path) -> Path:
    """Common git-dir of the main clone — one for the main tree and worktrees.

    Claude Code App opens every session in a separate git-worktree
    (.claude/worktrees/<name>/); in that case repo_root/.git is a pointer
    file, not a directory, and the workshop key is not there. The bootstrap
    drops the key and info-file into the .git/ of the main clone.
    `git rev-parse --git-common-dir` returns that directory from any tree;
    for the main clone it's simply .git.
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return (repo_root / ".git").resolve()
    if res.returncode == 0 and res.stdout.strip():
        common = Path(res.stdout.strip())
        if not common.is_absolute():
            common = repo_root / common
        return common.resolve()
    return (repo_root / ".git").resolve()


REPO_ROOT = Path(os.environ.get("WORKSHOP_REPO_ROOT") or Path(__file__).resolve().parents[1])
COMMON_GIT_DIR = _resolve_common_git_dir(REPO_ROOT)
WIN_GIT_DIR = REPO_ROOT / ".git"
KEY_SRC = COMMON_GIT_DIR / "raif-workshop-key"
INFO_SRC = COMMON_GIT_DIR / "raif-workshop-info"

HOME = Path.home()
SSH_DIR = HOME / ".ssh"
KEY_DST = SSH_DIR / "raif_workshop"
SSH_CONFIG = SSH_DIR / "config"
KNOWN_HOSTS = SSH_DIR / "known_hosts"

# Linux-side git-dir and shim. /tmp is ext4 — unlink always works.
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
    ("push.default", "upstream"),
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
            "Workshop SSH key not found at .git/raif-workshop-key. "
            "Either the bootstrap was not run, or the participant is on an "
            "older version. Without the key push to GitHub from the sandbox "
            "won't work.",
            code=2,
        )
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile(KEY_SRC, KEY_DST)
    KEY_DST.chmod(0o600)
    ok(f"Key: {KEY_DST}")

    cfg = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    if SSH_CONFIG_MARKER not in cfg:
        with SSH_CONFIG.open("a") as f:
            f.write(SSH_CONFIG_BLOCK)
        SSH_CONFIG.chmod(0o600)
        ok(f"github.com entry appended to {SSH_CONFIG}")
    else:
        ok(f"{SSH_CONFIG} already has a github.com entry")

    res = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519,ecdsa,rsa", "github.com"],
        capture_output=True, text=True, timeout=10,
    )
    if res.returncode == 0 and res.stdout:
        KNOWN_HOSTS.write_text(res.stdout)
        KNOWN_HOSTS.chmod(0o600)
        ok(f"known_hosts updated ({len(res.stdout.splitlines())} entries)")
    else:
        warn("ssh-keyscan returned no keys; relying on accept-new")


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
        warn("info file has no WORKSHOP_GIT_NAME/EMAIL — leaving git config alone")
        return
    subprocess.run(["git", "config", "--global", "user.name", name], check=True)
    subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    ok(f"git config global: {name} <{email}>")


def _fallback_plain_git(message: str) -> str:
    """Fall back to the regular git, removing any stale shim from PATH."""
    warn(message)
    try:
        SHIM_PATH.unlink()
        ok(f"removed stale shim {SHIM_PATH}")
    except FileNotFoundError:
        pass
    except OSError as exc:
        warn(f"could not remove {SHIM_PATH}: {exc}")
    return "git"


def _is_valid_git_dir(path: Path) -> bool:
    res = subprocess.run(
        ["git", "--git-dir", str(path), "rev-parse", "--git-dir"],
        check=False, capture_output=True,
    )
    return res.returncode == 0


def setup_linux_gitdir() -> str:
    """Workaround for virtiofs-induced .lock pain on Windows participants."""
    if sys.platform != "linux":
        return _fallback_plain_git(
            f"{sys.platform} — no virtiofs issues (not Linux), shim not needed")

    if not WIN_GIT_DIR.is_dir():
        return _fallback_plain_git(
            f"{WIN_GIT_DIR} is not a directory — skipping shim, using regular git")

    try:
        if LINUX_GIT_DIR.exists():
            shutil.rmtree(LINUX_GIT_DIR, ignore_errors=True)
        shutil.copytree(
            WIN_GIT_DIR, LINUX_GIT_DIR, symlinks=True,
            ignore=shutil.ignore_patterns("*.lock"),
        )
    except (OSError, shutil.Error) as exc:
        return _fallback_plain_git(
            f"copy .git -> {LINUX_GIT_DIR} failed ({exc}); using regular git")

    if not _is_valid_git_dir(LINUX_GIT_DIR):
        shutil.rmtree(LINUX_GIT_DIR, ignore_errors=True)
        return _fallback_plain_git(
            f"{LINUX_GIT_DIR} did not assemble into a working repository; using regular git")

    SHIM_DIR.mkdir(parents=True, exist_ok=True)
    SHIM_PATH.write_text(
        "#!/bin/bash\n"
        "# Auto-generated shim: moves .git metadata from virtiofs onto ext4.\n"
        f'exec /usr/bin/git --git-dir={LINUX_GIT_DIR} '
        f'--work-tree={REPO_ROOT} "$@"\n'
    )
    SHIM_PATH.chmod(0o755)
    n_files = sum(1 for _ in LINUX_GIT_DIR.rglob("*"))
    ok(f"Linux-side git-dir: {LINUX_GIT_DIR} ({n_files} files)")
    ok(f"git shim: {SHIM_PATH}  (use PATH=/tmp/bin:$PATH to intercept, or call directly)")

    fetch = subprocess.run(
        [str(SHIM_PATH), "fetch", "origin", "main"],
        check=False, capture_output=True, text=True, timeout=30,
    )
    if fetch.returncode == 0:
        subprocess.run(
            [str(SHIM_PATH), "update-ref", "refs/heads/main", "origin/main"],
            check=False, capture_output=True,
        )
        ok("Linux-side git-dir synced with origin/main")
    else:
        warn(f"Could not fetch origin: {fetch.stderr.strip()[:200]}")
    return str(SHIM_PATH)


def harden_git_config() -> None:
    git = str(SHIM_PATH) if SHIM_PATH.exists() else "git"
    for k, v in GIT_CONFIG_HARDENING:
        subprocess.run([git, "config", "--local", k, v], check=False, capture_output=True)
    subprocess.run([git, "maintenance", "unregister"], check=False, capture_output=True)
    ok("git config: autocrlf=off, fsmonitor=off, gc.auto=0, maintenance=off, push.default=upstream")


def cleanup_stale_locks_on_mount() -> list[str]:
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
        warn(f"Could not remove locks on the Windows mount (not blocking, see /tmp/raif-git): {', '.join(stuck)}")
    return stuck


def test_github() -> bool:
    try:
        res = subprocess.run(
            ["ssh", "-T", "-o", "BatchMode=yes", "git@github.com"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        warn("ssh -T github.com — timed out")
        return False
    out = res.stderr + res.stdout
    if "successfully authenticated" in out:
        ok("GitHub accepted the key")
        return True
    warn(f"GitHub did not confirm the key: {out.strip()[:200]}")
    return False


def main() -> int:
    step("Setting up SSH inside the sandbox")
    setup_ssh()

    step("Reading the participant meta-info")
    info = parse_info()
    if info:
        ok(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK', '?')}  "
           f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '?')}")
    else:
        warn("info file missing — Claude will need to ask for the name and block")

    step("Setting git identity")
    setup_git_identity(info)

    step("Preparing git for the sandbox session")
    git_cmd = setup_linux_gitdir()

    step("Hardening git config")
    harden_git_config()

    step("Cleaning up stale locks on the Windows mount")
    cleanup_stale_locks_on_mount()

    step("Checking GitHub access")
    github_ok = test_github()

    print("=== READY ===", flush=True)
    print(f"WORKSHOP_BLOCK={info.get('WORKSHOP_BLOCK', '')}", flush=True)
    print(f"WORKSHOP_PARTICIPANT={info.get('WORKSHOP_PARTICIPANT', '')}", flush=True)
    print(f"WORKSHOP_GIT_NAME={info.get('WORKSHOP_GIT_NAME', '')}", flush=True)
    print(f"GIT_SHIM={git_cmd}", flush=True)
    print(f"GITHUB_OK={'yes' if github_ok else 'no'}", flush=True)
    return 0 if info else 2


if __name__ == "__main__":
    sys.exit(main())
