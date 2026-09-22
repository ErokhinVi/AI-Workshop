#!/usr/bin/env python3
"""tools/setup/make-bootstrap.py: собрать установщик ноутбука на все команды.

Один установщик на воркшоп, пара файлов: raif-workshop-setup.applescript (macOS)
и raif-workshop-setup.cmd (Windows). Участник выбирает в первом окне команду
из tools/setup/teams.conf, потом блок и имя. В установщик вшиты deploy keys
всех команд, на ноутбук ложится только ключ выбранной: deploy key открывает
один репозиторий. Ключ аккаунта в скрипт класть нельзя: он открывает все
репозитории аккаунта.

Ключи и готовый установщик лежат ВНЕ репозитория, в папке секретов
(~/AI-Workshop-secrets/<WORKSHOP_ID> или $WORKSHOP_SECRETS): в них приватные
ключи с правом push. В репозиторий не коммитить (CI проверяет
tools/setup/check_no_keys.py), раздавать лично: AirDrop, флешка. Кто достанет
ключи из установщика, сможет пушить в репозитории чужих команд. Читать их
можно и так: репозитории публичные, иначе Render и судья их не видят.

Исходники: tools/bootstrap/raif-workshop-setup.sh (bash, его AppleScript
запускает в Terminal), .applescript и .cmd. В мастерах ключей нет.

Использование:
  python3 tools/setup/make-bootstrap.py                   # собрать установщик
  python3 tools/setup/make-bootstrap.py --refresh-master  # вшить .sh в мастер-AppleScript
  python3 tools/setup/make-bootstrap.py --scrub-master    # вычистить ключи из скриптов в репо

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

from workshop_conf import Conf, ConfError, load_conf

ROOT = Path(__file__).resolve().parents[2]
MASTER_SH = ROOT / "tools/bootstrap/raif-workshop-setup.sh"
MASTER_AS = ROOT / "tools/bootstrap/raif-workshop-setup.applescript"
MASTER_CMD = ROOT / "tools/bootstrap/raif-workshop-setup.cmd"
OUT_NAMES = ("raif-workshop-setup.applescript", "raif-workshop-setup.cmd")
SCRUB_GLOBS = ("tools/bootstrap/*.applescript", "tools/bootstrap/*.cmd",
               "_archive/tools/bootstrap/*.applescript", "_archive/tools/bootstrap/*.cmd")

B64_RE = re.compile(r'(set bashB64 to ")([A-Za-z0-9+/=]+)(")')
AS_NAMES_RE = re.compile(r"set teamNames to \{[^}\r\n]*\}")
AS_CODES_RE = re.compile(r"set teamCodes to \{[^}\r\n]*\}")
SH_KEY_RE = re.compile(r"<<'WORKSHOP_KEY_(TEAM_[A-Z])'\n(.*?)\nWORKSHOP_KEY_\1\n", re.S)
CMD_KEY_RE = re.compile(r"'(team_[a-z])' = '([A-Za-z0-9+/=]+)'")
# Старый формат, один ключ на скрипт: только чтобы --scrub-master чистил архив.
LEGACY_HEREDOC_RE = re.compile(r"(<<'WORKSHOP_PRIVATE_KEY_EOF'\n)(.*?)(\nWORKSHOP_PRIVATE_KEY_EOF\n)", re.S)
LEGACY_CMD_KEY_RE = re.compile(r"(\$PrivateKeyB64 = ')([^']*)(')")
LEGACY_KEY_PLACEHOLDER = "__PRIVATE_KEY_HERE__"
LEGACY_KEY_B64_PLACEHOLDER = "__PRIVATE_KEY_B64_HERE__"

SH_TEAMS_EMPTY = "TEAM_TABLE='\n__TEAMS_HERE__\n'\n"
SH_KEYS_EMPTY = ("write_team_key() {  # write_team_key <team code> <file>: that team's private key → file\n"
                 "  case \"$1\" in\n"
                 "    *) return 1 ;;\n"
                 "  esac\n"
                 "}\n")
CMD_TEAMS_EMPTY = "$Teams = @()\n"
CMD_KEYS_EMPTY = "$TeamKeysB64 = @{}\n"


class BuildError(RuntimeError):
    pass


def block_re(name: str) -> re.Pattern[str]:
    """Блок между строками `# >>> name...` и `# <<< name`: его пишет сборщик."""
    return re.compile(rf"(# >>> {name}\b[^\n]*\n)(.*?)(# <<< {name}\n)", re.S)


def set_block(text: str, name: str, body: str, where: str) -> str:
    pattern = block_re(name)
    newline = "\r\n" if "\r\n" in text else "\n"
    flat = text.replace("\r\n", "\n")
    result, count = pattern.subn(lambda m: m.group(1) + body + m.group(3), flat)
    if count != 1:
        raise BuildError(f"{where}: ожидал ровно один блок `# >>> {name}`, нашел {count}")
    return result.replace("\n", newline)


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


def ensure_key(keys_dir: Path, letter: str, comment: str, must_exist: bool = False) -> Path:
    key = keys_dir / f"team_{letter}"
    if not key.exists() and must_exist:
        raise BuildError(f"нет ключа {key}: новый ключ не висит на GitHub, установщик с ним не пустит")
    if not key.exists():
        keys_dir.mkdir(parents=True, exist_ok=True)
        keys_dir.chmod(0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)],
                       check=True)
    key.chmod(0o600)
    return key


def team_label(letter: str) -> str:
    """team_a → Team 1: номер стола. Табло симулятора считает так же."""
    return f"Team {ord(letter) - ord('a') + 1}"


# ── bash (его запускает AppleScript) ────────────────────────────────────────

def build_bash(bash: str, conf: Conf, keys: dict[str, str]) -> str:
    where = MASTER_SH.name
    rows = "".join(f"team_{letter} {conf.owner}/{repo} {team_label(letter)}\n" for letter, repo in conf.teams)
    bash = set_block(bash, "teams", f"TEAM_TABLE='\n{rows}'\n", where)
    cases = []
    for letter, _repo in conf.teams:
        tag = f"WORKSHOP_KEY_TEAM_{letter.upper()}"
        cases.append(f"    team_{letter})\n"
                     f"      cat > \"$2\" <<'{tag}'\n"
                     f"{keys[letter].rstrip(chr(10))}\n"
                     f"{tag}\n"
                     f"      ;;\n")
    body = ("write_team_key() {  # write_team_key <team code> <file>: that team's private key → file\n"
            "  case \"$1\" in\n"
            + "".join(cases)
            + "    *) return 1 ;;\n"
            "  esac\n"
            "}\n")
    return set_block(bash, "keys", body, where)


# ── macOS ────────────────────────────────────────────────────────────────────

def as_list(items: list[str]) -> str:
    return "{" + ", ".join(f'"{item}"' for item in items) + "}"


def build_applescript(master: Path, out: Path, conf: Conf, bash: str) -> None:
    text, bom = read_utf16(master)
    where = master.name
    match = B64_RE.search(text)
    if not match:
        raise BuildError(f"{where}: не нашел set bashB64")
    new_b64 = base64.b64encode(bash.encode("utf-8")).decode("ascii")
    text = text[:match.start(2)] + new_b64 + text[match.end(2):]
    text = sub_once(AS_NAMES_RE, "set teamNames to " + as_list([team_label(l) for l, _ in conf.teams]), text, where)
    text = sub_once(AS_CODES_RE, "set teamCodes to " + as_list([f"team_{l}" for l, _ in conf.teams]), text, where)
    write_utf16(out, text, bom)


# ── Windows ──────────────────────────────────────────────────────────────────

def build_cmd(master: Path, out: Path, conf: Conf, keys: dict[str, str]) -> None:
    text = master.read_bytes().decode("utf-8")
    where = master.name
    teams = "".join(f"  @{{ Code = 'team_{letter}'; Repo = '{conf.owner}/{repo}'; Label = '{team_label(letter)}' }}\n"
                    for letter, repo in conf.teams)
    text = set_block(text, "teams", f"$Teams = @(\n{teams})\n", where)
    keys_b64 = "".join(f"  'team_{letter}' = '{base64.b64encode(keys[letter].encode('utf-8')).decode('ascii')}'\n"
                       for letter, _repo in conf.teams)
    text = set_block(text, "keys", f"$TeamKeysB64 = @{{\n{keys_b64}}}\n", where)
    out.write_bytes(text.encode("utf-8"))


# ── проверки ────────────────────────────────────────────────────────────────

def payload_of(applescript: Path) -> str:
    text, _ = read_utf16(applescript)
    match = B64_RE.search(text)
    if not match:
        raise BuildError(f"{applescript.name}: не нашел set bashB64")
    return base64.b64decode(match.group(2)).decode("utf-8")


def verify(out_dir: Path, conf: Conf, fingerprints: dict[str, str]) -> None:
    bash = payload_of(out_dir / OUT_NAMES[0])
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tmp:
        tmp.write(bash)
    subprocess.run(["bash", "-n", tmp.name], check=True)
    Path(tmp.name).unlink()
    mac = {code.lower(): key for code, key in SH_KEY_RE.findall(bash)}
    cmd_text = (out_dir / OUT_NAMES[1]).read_bytes().decode("utf-8")
    win = {code: base64.b64decode(b64).decode("utf-8") for code, b64 in CMD_KEY_RE.findall(cmd_text)}
    as_text, _ = read_utf16(out_dir / OUT_NAMES[0])
    for letter, repo in conf.teams:
        code = f"team_{letter}"
        for platform, found in (("macOS", mac), ("Windows", win)):
            if code not in found or fingerprint_of_private(found[code]) != fingerprints[letter]:
                raise BuildError(f"{code}: в установщике {platform} нет ключа команды или он чужой")
        row = f"{code} {conf.owner}/{repo} {team_label(letter)}"
        if row not in bash or f"Repo = '{conf.owner}/{repo}'" not in cmd_text:
            raise BuildError(f"{code}: в установщике нет репозитория {conf.owner}/{repo}")
        if f'"{code}"' not in as_text or f'"{team_label(letter)}"' not in as_text:
            raise BuildError(f"{code}: команды нет в списке выбора AppleScript")
    extra = (set(mac) | set(win)) - {f"team_{letter}" for letter, _ in conf.teams}
    if extra:
        raise BuildError(f"в установщике лишние ключи: {', '.join(sorted(extra))}")


# ── мастер-скрипты в репозитории ────────────────────────────────────────────

def refresh_master(conf: Conf) -> None:
    """Вшить tools/bootstrap/raif-workshop-setup.sh в мастер-AppleScript как есть, без ключей."""
    text, bom = read_utf16(MASTER_AS)
    match = B64_RE.search(text)
    if not match:
        raise BuildError(f"{MASTER_AS.name}: не нашел set bashB64")
    new_b64 = base64.b64encode(MASTER_SH.read_bytes()).decode("ascii")
    text = text[:match.start(2)] + new_b64 + text[match.end(2):]
    text = sub_once(AS_NAMES_RE, "set teamNames to " + as_list([team_label(l) for l, _ in conf.teams]), text, MASTER_AS.name)
    text = sub_once(AS_CODES_RE, "set teamCodes to " + as_list([f"team_{l}" for l, _ in conf.teams]), text, MASTER_AS.name)
    write_utf16(MASTER_AS, text, bom)
    print(f"  {MASTER_AS.relative_to(ROOT)}: вшит {MASTER_SH.relative_to(ROOT)}, команд {len(conf.teams)}")


def scrub_bash(bash: str) -> str:
    if block_re("keys").search(bash):
        bash = block_re("keys").sub(lambda m: m.group(1) + SH_KEYS_EMPTY + m.group(3), bash)
    if block_re("teams").search(bash):
        bash = block_re("teams").sub(lambda m: m.group(1) + SH_TEAMS_EMPTY + m.group(3), bash)
    return LEGACY_HEREDOC_RE.sub(lambda m: m.group(1) + LEGACY_KEY_PLACEHOLDER + m.group(3), bash)


def scrub_master() -> None:
    for pattern in SCRUB_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            rel = path.relative_to(ROOT)
            if path.suffix == ".applescript":
                text, bom = read_utf16(path)
                match = B64_RE.search(text)
                if not match:
                    print(f"  ? {rel}: нет bashB64, пропускаю")
                    continue
                bash = base64.b64decode(match.group(2)).decode("utf-8")
                clean = scrub_bash(bash)
                if clean == bash:
                    print(f"  = {rel}: ключа нет")
                    continue
                new_b64 = base64.b64encode(clean.encode("utf-8")).decode("ascii")
                write_utf16(path, text[:match.start(2)] + new_b64 + text[match.end(2):], bom)
            else:
                text = path.read_bytes().decode("utf-8")
                clean = text.replace("\r\n", "\n")
                if block_re("keys").search(clean):
                    clean = block_re("keys").sub(lambda m: m.group(1) + CMD_KEYS_EMPTY + m.group(3), clean)
                clean = LEGACY_CMD_KEY_RE.sub(lambda m: m.group(1) + LEGACY_KEY_B64_PLACEHOLDER + m.group(3), clean)
                if "\r\n" in text:
                    clean = clean.replace("\n", "\r\n")
                if clean == text:
                    print(f"  = {rel}: ключа нет")
                    continue
                path.write_bytes(clean.encode("utf-8"))
            print(f"  - {rel}: ключи заменены плейсхолдерами")


def build(conf: Conf, out_dir: Path, keys_must_exist: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)
    keys_dir = out_dir / "keys"
    keys: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    lines = ["# deploy keys воркшопа: команда, репозиторий, отпечаток. Повесить: tools/setup/github-access.sh keys"]
    for letter, repo in conf.teams:
        key = ensure_key(keys_dir, letter, f"workshop-{conf.workshop_id}-team_{letter}", keys_must_exist)
        keys[letter] = key.read_text()
        fingerprints[letter] = fingerprint_of_private(keys[letter])
        lines.append(f"team_{letter} {conf.owner}/{repo} {fingerprints[letter]}")
    bash = build_bash(MASTER_SH.read_text(encoding="utf-8"), conf, keys)
    build_applescript(MASTER_AS, out_dir / OUT_NAMES[0], conf, bash)
    build_cmd(MASTER_CMD, out_dir / OUT_NAMES[1], conf, keys)
    for name in OUT_NAMES:
        (out_dir / name).chmod(0o600)
    verify(out_dir, conf, fingerprints)
    (out_dir / "deploy-keys.txt").write_text("\n".join(lines) + "\n")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="куда класть ключи и установщик (по умолчанию папка секретов)")
    parser.add_argument("--refresh-master", action="store_true")
    parser.add_argument("--scrub-master", action="store_true")
    parser.add_argument("--keys-must-exist", action="store_true",
                        help="не создавать ключи, упасть без них (Actions: ключи из секрета)")
    args = parser.parse_args()

    if args.scrub_master:
        scrub_master()
        return
    conf = load_conf()
    if args.refresh_master:
        refresh_master(conf)
        return
    out_dir = build(conf, args.out or conf.secrets_dir, args.keys_must_exist)
    for letter, repo in conf.teams:
        print(f"  {team_label(letter)} = team_{letter} → {conf.owner}/{repo}")
    print(f"\nГотово, установщик на {len(conf.teams)} команд, ключи сверены в обоих файлах:\n"
          f"  {out_dir / OUT_NAMES[0]}\n  {out_dir / OUT_NAMES[1]}\n"
          f"Повесить deploy keys: tools/setup/github-access.sh keys")


if __name__ == "__main__":
    try:
        main()
    except (BuildError, ConfError) as exc:
        raise SystemExit(f"сборка остановлена: {exc}") from exc
