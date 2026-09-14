#!/usr/bin/env python3
"""tools/setup/make-bootstrap.py: собрать установщики ноутбука по командам.

Берет мастер-скрипты tools/bootstrap/raif-workshop-setup.{applescript,cmd} и для
каждой команды из tools/setup/teams.conf собирает пару, где:
  - выбора команды нет, команда прошита;
  - клонируется репозиторий этой команды;
  - вшит приватный deploy key этой команды. Ключ свой на каждый репозиторий:
    GitHub не дает повесить один deploy key на два репозитория. Ключ аккаунта
    в скрипт класть нельзя: он открывает все репозитории аккаунта.

Ключи и готовые скрипты лежат ВНЕ репозитория, в папке секретов
(~/AI-Workshop-secrets/<WORKSHOP_ID> или $WORKSHOP_SECRETS): в них приватный
ключ с правом push. В репозиторий не коммитить (CI проверяет
tools/setup/check_no_keys.py), раздавать лично: AirDrop, флешка.

Использование:
  python3 tools/setup/make-bootstrap.py                 # все команды
  python3 tools/setup/make-bootstrap.py --only a c      # часть команд
  python3 tools/setup/make-bootstrap.py --scrub-master  # вычистить ключи из скриптов в репо

Нет ключа команды: создается ssh-keygen ed25519. Повесить публичные ключи на
репозитории команд: tools/setup/github-access.sh keys.
"""

from __future__ import annotations

import argparse
import base64
import re
import subprocess
import tempfile
from pathlib import Path

from workshop_conf import ConfError, load_conf

ROOT = Path(__file__).resolve().parents[2]
MASTER_AS = ROOT / "tools/bootstrap/raif-workshop-setup.applescript"
MASTER_CMD = ROOT / "tools/bootstrap/raif-workshop-setup.cmd"
SCRUB_GLOBS = ("tools/bootstrap/*.applescript", "tools/bootstrap/*.cmd",
               "_archive/tools/bootstrap/*.applescript", "_archive/tools/bootstrap/*.cmd")

KEY_PLACEHOLDER = "__PRIVATE_KEY_HERE__"          # мастер bash сам падает на нем
KEY_B64_PLACEHOLDER = "__PRIVATE_KEY_B64_HERE__"
B64_RE = re.compile(r'(set bashB64 to ")([A-Za-z0-9+/=]+)(")')
HEREDOC_RE = re.compile(r"(<<'WORKSHOP_PRIVATE_KEY_EOF'\n)(.*?)(\nWORKSHOP_PRIVATE_KEY_EOF\n)", re.S)
CMD_KEY_RE = re.compile(r"(\$PrivateKeyB64 = ')([^']*)(')")


class BuildError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, where: str) -> str:
    count = text.count(old)
    if count != 1:
        raise BuildError(f"{where}: ожидал ровно одно вхождение {old!r}, нашел {count}")
    return text.replace(old, new)


def sub_once(pattern: re.Pattern[str], repl: str, text: str, where: str) -> str:
    result, count = pattern.subn(lambda _m: repl, text)
    if count != 1:
        raise BuildError(f"{where}: ожидал ровно одно совпадение {pattern.pattern!r}, нашел {count}")
    return result


def read_utf16(path: Path) -> tuple[str, bytes]:
    raw = path.read_bytes()
    bom = raw[:2] if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else b""
    encoding = "utf-16-be" if bom == b"\xfe\xff" else "utf-16-le"
    return raw[len(bom):].decode(encoding), bom


def write_utf16(path: Path, text: str, bom: bytes) -> None:
    encoding = "utf-16-be" if bom == b"\xfe\xff" else "utf-16-le"
    path.write_bytes(bom + text.encode(encoding))


def fingerprint_of_private(key_text: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "k"
        key.write_text(key_text if key_text.endswith("\n") else key_text + "\n")
        key.chmod(0o600)
        pub = subprocess.run(["ssh-keygen", "-y", "-f", str(key)],
                             check=True, capture_output=True, text=True).stdout
        (Path(tmp) / "k.pub").write_text(pub)
        out = subprocess.run(["ssh-keygen", "-lf", str(Path(tmp) / "k.pub")],
                             check=True, capture_output=True, text=True).stdout
    return out.split()[1]


def ensure_key(keys_dir: Path, letter: str, comment: str) -> Path:
    key = keys_dir / f"team_{letter}"
    if not key.exists():
        keys_dir.mkdir(parents=True, exist_ok=True)
        keys_dir.chmod(0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)],
                       check=True)
    key.chmod(0o600)
    return key


# ── macOS ────────────────────────────────────────────────────────────────────

def build_bash(bash: str, owner: str, letter: str, repo: str, key_text: str) -> str:
    where = "bash-пейлоад"
    upper = letter.upper()
    match = HEREDOC_RE.search(bash)
    if not match:
        raise BuildError(f"{where}: не нашел heredoc с ключом")
    bash = bash[:match.start(2)] + key_text.rstrip("\n") + bash[match.end(2):]
    bash = replace_once(bash, 'team_a|team_b|host) TEAM="${TEAM_ARG}" ;;',
                        f'team_{letter}|host) TEAM="${{TEAM_ARG}}" ;;', where)
    bash = replace_once(bash, "Expected: team_a | team_b | host.",
                        f"Expected: team_{letter} | host.", where)
    bash = replace_once(
        bash,
        '  team_a) REPO_URL="git@github.com:ErokhinVi/team_1.git"; REPO_DIR="${HOME}/team_1" ;;\n'
        '  team_b) REPO_URL="git@github.com:ErokhinVi/team_2.git"; REPO_DIR="${HOME}/team_2" ;;\n',
        f'  team_{letter}) REPO_URL="git@github.com:{owner}/{repo}.git"; REPO_DIR="${{HOME}}/{repo}" ;;\n',
        where)
    bash = replace_once(bash, '  team_a) TEAM_HUMAN="Team A" ;;\n  team_b) TEAM_HUMAN="Team B" ;;\n',
                        f'  team_{letter}) TEAM_HUMAN="Team {upper}" ;;\n', where)
    return bash


def build_applescript(master: Path, out: Path, owner: str, letter: str, repo: str,
                      key_text: str) -> str:
    text, bom = read_utf16(master)
    where = master.name
    match = B64_RE.search(text)
    if not match:
        raise BuildError(f"{where}: не нашел set bashB64")
    bash = base64.b64decode(match.group(2)).decode("utf-8")
    bash = build_bash(bash, owner, letter, repo, key_text)
    new_b64 = base64.b64encode(bash.encode("utf-8")).decode("ascii")
    text = text[:match.start(2)] + new_b64 + text[match.end(2):]

    team_block = re.compile(r"-- 1\. Team\r?\n.*?end if", re.S)
    newline = "\r\n" if "\r\n" in text else "\n"
    text = sub_once(team_block, f'-- 1. Team: fixed in this script{newline}\tset teamCode to "team_{letter}"',
                    text, where)
    text = replace_once(text, 'set dlgTitle to "Raif AI Workshop — laptop setup"',
                        f'set dlgTitle to "Raif AI Workshop — Team {letter.upper()} laptop setup"', where)
    text = text.replace("Pick your team and block", "Pick your block")
    write_utf16(out, text, bom)
    return bash


# ── Windows ──────────────────────────────────────────────────────────────────

def build_cmd(master: Path, out: Path, owner: str, letter: str, repo: str, key_text: str) -> None:
    text = master.read_bytes().decode("utf-8")
    where = master.name
    nl = "\r\n" if "\r\n" in text else "\n"
    upper = letter.upper()
    key_b64 = base64.b64encode(key_text.encode("utf-8")).decode("ascii")
    text = sub_once(CMD_KEY_RE, f"$PrivateKeyB64 = '{key_b64}'", text, where)
    text = replace_once(text, "$teamA.Text     = 'Team A'", f"$teamA.Text     = 'Team {upper}'", where)
    team_b_block = re.compile(r"  \$teamB = New-Object Windows\.Forms\.RadioButton.*?"
                              r"\$form\.Controls\.Add\(\$teamB\)\r?\n\r?\n", re.S)
    text = sub_once(team_b_block, "", text, where)
    text = replace_once(text, "$team = if ($teamA.Checked) { 'team_a' } else { 'team_b' }",
                        f"$team = 'team_{letter}'", where)
    text = replace_once(
        text,
        "  'team_a' { $RepoUrl = 'git@github.com:ErokhinVi/team_1.git'; $RepoDir = Join-Path $env:USERPROFILE 'team_1' }" + nl
        + "  'team_b' { $RepoUrl = 'git@github.com:ErokhinVi/team_2.git'; $RepoDir = Join-Path $env:USERPROFILE 'team_2' }" + nl,
        f"  'team_{letter}' {{ $RepoUrl = 'git@github.com:{owner}/{repo}.git'; $RepoDir = Join-Path $env:USERPROFILE '{repo}' }}" + nl,
        where)
    text = replace_once(text, "@{ 'team_a' = 'Team A'; 'team_b' = 'Team B'; 'host' = 'Host' }",
                        f"@{{ 'team_{letter}' = 'Team {upper}'; 'host' = 'Host' }}", where)
    text = text.replace("Pick your team and block", "Pick your block")
    out.write_bytes(text.encode("utf-8"))


# ── проверки ────────────────────────────────────────────────────────────────

def verify(out_dir: Path, owner: str, letter: str, repo: str, expected_fp: str) -> list[str]:
    notes = []
    text, _ = read_utf16(out_dir / "raif-workshop-setup.applescript")
    bash = base64.b64decode(B64_RE.search(text).group(2)).decode("utf-8")  # type: ignore[union-attr]
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tmp:
        tmp.write(bash)
    subprocess.run(["bash", "-n", tmp.name], check=True)
    Path(tmp.name).unlink()
    mac_fp = fingerprint_of_private(HEREDOC_RE.search(bash).group(2))  # type: ignore[union-attr]
    cmd_text = (out_dir / "raif-workshop-setup.cmd").read_bytes().decode("utf-8")
    win_fp = fingerprint_of_private(
        base64.b64decode(CMD_KEY_RE.search(cmd_text).group(2)).decode("utf-8"))  # type: ignore[union-attr]
    if mac_fp != expected_fp or win_fp != expected_fp:
        raise BuildError(f"team_{letter}: отпечаток ключа в скриптах не совпал с ключом команды")
    if f"git@github.com:{owner}/{repo}.git" not in bash or f"git@github.com:{owner}/{repo}.git" not in cmd_text:
        raise BuildError(f"team_{letter}: в скриптах нет репозитория {owner}/{repo}")
    if 'set teamCode to "team_' + letter + '"' not in text:
        raise BuildError(f"team_{letter}: команда не прошита в AppleScript")
    notes.append(f"bash -n ок, ключ {expected_fp} в обоих скриптах")
    return notes


def scrub_master() -> None:
    for pattern in SCRUB_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if path.suffix == ".applescript":
                text, bom = read_utf16(path)
                match = B64_RE.search(text)
                if not match:
                    print(f"  ? {path.relative_to(ROOT)}: нет bashB64, пропускаю")
                    continue
                bash = base64.b64decode(match.group(2)).decode("utf-8")
                key = HEREDOC_RE.search(bash)
                if not key or key.group(2) == KEY_PLACEHOLDER:
                    print(f"  = {path.relative_to(ROOT)}: ключа нет")
                    continue
                bash = bash[:key.start(2)] + KEY_PLACEHOLDER + bash[key.end(2):]
                new_b64 = base64.b64encode(bash.encode("utf-8")).decode("ascii")
                write_utf16(path, text[:match.start(2)] + new_b64 + text[match.end(2):], bom)
            else:
                text = path.read_bytes().decode("utf-8")
                if not CMD_KEY_RE.search(text) or CMD_KEY_RE.search(text).group(2) == KEY_B64_PLACEHOLDER:  # type: ignore[union-attr]
                    print(f"  = {path.relative_to(ROOT)}: ключа нет")
                    continue
                path.write_bytes(CMD_KEY_RE.sub(lambda m: m.group(1) + KEY_B64_PLACEHOLDER + m.group(3),
                                                text).encode("utf-8"))
            print(f"  - {path.relative_to(ROOT)}: ключ заменен плейсхолдером")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", default=None, help="буквы команд")
    parser.add_argument("--out", type=Path, default=None, help="куда класть ключи и скрипты (по умолчанию папка секретов)")
    parser.add_argument("--scrub-master", action="store_true")
    args = parser.parse_args()

    if args.scrub_master:
        scrub_master()
        return

    conf = load_conf()
    out_dir = args.out or conf.secrets_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    keys_dir = out_dir / "keys"
    lines = ["# deploy keys воркшопа: команда, репозиторий, отпечаток. Повесить: tools/setup/github-access.sh keys"]
    for letter, repo in conf.teams:
        if args.only and letter not in args.only:
            continue
        key = ensure_key(keys_dir, letter, f"workshop-{conf.workshop_id}-team_{letter}")
        key_text = key.read_text()
        fp = fingerprint_of_private(key_text)
        team_dir = out_dir / f"team_{letter}"
        team_dir.mkdir(exist_ok=True)
        build_applescript(MASTER_AS, team_dir / "raif-workshop-setup.applescript", conf.owner, letter, repo, key_text)
        build_cmd(MASTER_CMD, team_dir / "raif-workshop-setup.cmd", conf.owner, letter, repo, key_text)
        notes = verify(team_dir, conf.owner, letter, repo, fp)
        print(f"team_{letter} → {conf.owner}/{repo}: {'; '.join(notes)}")
        lines.append(f"team_{letter} {conf.owner}/{repo} {fp}")
    (out_dir / "deploy-keys.txt").write_text("\n".join(lines) + "\n")
    print(f"\nГотово: {out_dir}\nПовесить deploy keys: tools/setup/github-access.sh keys")


if __name__ == "__main__":
    try:
        main()
    except (BuildError, ConfError) as exc:
        raise SystemExit(f"сборка остановлена: {exc}") from exc
