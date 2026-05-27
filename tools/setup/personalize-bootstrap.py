#!/usr/bin/env python3
"""tools/setup/personalize-bootstrap.py

Generate four team-specific bootstrap script pairs from the master pair in
_archive/tools/bootstrap/.

Per-team changes (functional):
  AppleScript:
    - Drop the Team A/B picker dialog; hardcode `teamCode = "team_<x>"`.
    - Re-encode the embedded bash blob after patching it.
  Embedded bash:
    - REPO_URL → git@github.com:<owner>/team_<N>.git
    - Settings template path: settings-${BLOCK}.json (no team prefix).
    - TEAM-arg validation accepts only this team or `host`.
  .cmd (PowerShell-in-batch):
    - Strip the Team A/B radio buttons; hardcode $team = '<team_code>'.
    - Update $teamHuman map and template lookup to match.
    - Swap repo URL.

The private SSH key embedded in the master is reused as-is — under the
"single user-level SSH key" model it grants write access to all four team
repos at once.

Output: tools/setup/personalized/<team_code>/raif-workshop-setup.{applescript,cmd}
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIVE = ROOT / "_archive" / "tools" / "bootstrap"
OUT = ROOT / "tools" / "setup" / "personalized"

GH_OWNER = "ErokhinVi"
TEAMS: list[tuple[str, str, str]] = [
    ("team_a", "team_1", "Team A"),
    ("team_b", "team_2", "Team B"),
    ("team_c", "team_3", "Team C"),
    ("team_d", "team_4", "Team D"),
]


def patch_bash(bash: str, team_code: str, team_repo: str, team_human: str) -> str:
    url = f"git@github.com:{GH_OWNER}/{team_repo}.git"
    out = bash

    if 'REPO_URL="git@github.com:ErokhinVi/AI-Workshop.git"' not in out:
        raise SystemExit("bash master: REPO_URL anchor not found")
    out = out.replace(
        'REPO_URL="git@github.com:ErokhinVi/AI-Workshop.git"',
        f'REPO_URL="{url}"',
    )

    if 'REPO_DIR="${HOME}/AI-Workshop"' not in out:
        raise SystemExit("bash master: REPO_DIR anchor not found")
    out = out.replace(
        'REPO_DIR="${HOME}/AI-Workshop"',
        f'REPO_DIR="${{HOME}}/{team_repo}"',
    )

    if "settings-${TEAM}-${BLOCK}.json" not in out:
        raise SystemExit("bash master: template-path anchor not found")
    out = out.replace(
        "settings-${TEAM}-${BLOCK}.json",
        "settings-${BLOCK}.json",
    )

    if "config-${TEAM}-${BLOCK}.toml" not in out:
        raise SystemExit("bash master: codex template-path anchor not found")
    out = out.replace(
        "config-${TEAM}-${BLOCK}.toml",
        "config-${BLOCK}.toml",
    )

    if "${REPO_DIR}/${TEAM}/${BLOCK}/" not in out:
        raise SystemExit("bash master: block-folder path anchor not found")
    out = out.replace(
        "${REPO_DIR}/${TEAM}/${BLOCK}/",
        "${REPO_DIR}/${BLOCK}/",
    )

    out, n = re.subn(
        r"team_a\|team_b\|host\)",
        f"{team_code}|host)",
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit("bash master: TEAM-case anchor not found")

    out = out.replace(
        "Expected: team_a | team_b | host.",
        f"Expected: {team_code} | host.",
    )

    pattern = (
        r'  team_a\) TEAM_HUMAN="Team A" ;;\n'
        r'  team_b\) TEAM_HUMAN="Team B" ;;'
    )
    replacement = f'  {team_code}) TEAM_HUMAN="{team_human}" ;;'
    out, n = re.subn(pattern, replacement, out, count=1)
    if n != 1:
        raise SystemExit("bash master: TEAM_HUMAN anchor not found")

    return out


def patch_applescript(text: str, team_code: str, bash_b64_new: str) -> str:
    pattern = re.compile(r"\t-- 1\. Team\n.*?end if\n", re.DOTALL)
    replacement = (
        f'\t-- 1. Team (hardcoded — this script is for {team_code})\n'
        f'\tset teamCode to "{team_code}"\n'
    )
    out, n = pattern.subn(replacement, text, count=1)
    if n != 1:
        raise SystemExit("applescript master: team-picker block not found")

    out, n = re.subn(
        r'set bashB64 to "[^"]+"',
        f'set bashB64 to "{bash_b64_new}"',
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit("applescript master: bashB64 anchor not found")

    return out


def patch_cmd(text: str, team_code: str, team_repo: str, team_human: str) -> str:
    url = f"git@github.com:{GH_OWNER}/{team_repo}.git"
    out = text

    if "git@github.com:ErokhinVi/AI-Workshop.git" not in out:
        raise SystemExit(".cmd master: REPO_URL anchor not found")
    out = out.replace("git@github.com:ErokhinVi/AI-Workshop.git", url)

    if "Join-Path $env:USERPROFILE 'AI-Workshop'" not in out:
        raise SystemExit(".cmd master: $RepoDir anchor not found")
    out = out.replace(
        "Join-Path $env:USERPROFILE 'AI-Workshop'",
        f"Join-Path $env:USERPROFILE '{team_repo}'",
    )

    teamA_block = re.compile(
        r"  \$teamA = New-Object Windows\.Forms\.RadioButton\n"
        r"(?:  \$teamA\..+\n){4}"
        r"  \$form\.Controls\.Add\(\$teamA\)\n\n"
    )
    out, n = teamA_block.subn("", out, count=1)
    if n != 1:
        raise SystemExit(".cmd master: teamA block not found")

    teamB_block = re.compile(
        r"  \$teamB = New-Object Windows\.Forms\.RadioButton\n"
        r"(?:  \$teamB\..+\n){3}"
        r"  \$form\.Controls\.Add\(\$teamB\)\n\n"
    )
    out, n = teamB_block.subn("", out, count=1)
    if n != 1:
        raise SystemExit(".cmd master: teamB block not found")

    out, n = re.subn(
        r"\$team = if \(\$teamA\.Checked\) \{ 'team_a' \} else \{ 'team_b' \}",
        f"$team = '{team_code}'",
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit(".cmd master: $team picker line not found")

    out, n = re.subn(
        r"\$teamHuman\s*= @\{ 'team_a' = 'Team A'; 'team_b' = 'Team B'; 'host' = 'Host' \}\[\$cfg\.Team\]",
        f"$teamHuman  = @{{ '{team_code}' = '{team_human}'; 'host' = 'Host' }}[$cfg.Team]",
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit(".cmd master: $teamHuman map not found")

    tpl_old = "'templates\\settings-' + $cfg.Team + '-' + $cfg.Block + '.json'"
    tpl_new = "'templates\\settings-' + $cfg.Block + '.json'"
    if tpl_old not in out:
        raise SystemExit(".cmd master: template-path anchor not found")
    out = out.replace(tpl_old, tpl_new)

    note_old = "'template: settings-' + $cfg.Team + '-' + $cfg.Block + '.json'"
    note_new = "'template: settings-' + $cfg.Block + '.json'"
    out = out.replace(note_old, note_new)

    codex_tpl_old = "'templates\\config-' + $cfg.Team + '-' + $cfg.Block + '.toml'"
    codex_tpl_new = "'templates\\config-' + $cfg.Block + '.toml'"
    if codex_tpl_old not in out:
        raise SystemExit(".cmd master: codex template-path anchor not found")
    out = out.replace(codex_tpl_old, codex_tpl_new)

    codex_note_old = "'template: config-' + $cfg.Team + '-' + $cfg.Block + '.toml'"
    codex_note_new = "'template: config-' + $cfg.Block + '.toml'"
    out = out.replace(codex_note_old, codex_note_new)

    block_path_old = "'Block folder: ' + $cfg.Team + '\\' + $cfg.Block + '\\'"
    block_path_new = "'Block folder: ' + $cfg.Block + '\\'"
    if block_path_old not in out:
        raise SystemExit(".cmd master: block-folder path anchor not found")
    out = out.replace(block_path_old, block_path_new)

    return out


def patch_host_applescript(text: str, bash_b64_new: str) -> str:
    """Strip both team and block pickers; hardcode 'host'/'host'."""
    out, n = re.compile(r"\t-- 1\. Team\n.*?end if\n", re.DOTALL).subn(
        '\t-- 1. Team (hardcoded — organiser script)\n'
        '\tset teamCode to "host"\n',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit("applescript master: team picker block not found (host)")

    out, n = re.compile(r"\t-- 2\. Block\n.*?end if\n", re.DOTALL).subn(
        '\t-- 2. Block (hardcoded — organiser has full repo access)\n'
        '\tset blockCode to "host"\n',
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit("applescript master: block picker block not found (host)")

    out, n = re.subn(
        r'set bashB64 to "[^"]+"',
        f'set bashB64 to "{bash_b64_new}"',
        out,
        count=1,
    )
    if n != 1:
        raise SystemExit("applescript master: bashB64 anchor not found (host)")
    return out


def patch_host_cmd(text: str) -> str:
    """Pre-check $hostBox; the form's host branch overrides team/block."""
    anchor = (
        "  $hostBox.Size     = New-Object Drawing.Size(460, 24)\n"
        "  $form.Controls.Add($hostBox)"
    )
    if anchor not in text:
        raise SystemExit(".cmd master: hostBox anchor not found (host)")
    replacement = (
        "  $hostBox.Size     = New-Object Drawing.Size(460, 24)\n"
        "  $hostBox.Checked  = $true\n"
        "  $form.Controls.Add($hostBox)"
    )
    return text.replace(anchor, replacement)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    asc_bytes = (ARCHIVE / "raif-workshop-setup.applescript").read_bytes()
    asc_master = asc_bytes.decode("utf-16")
    cmd_master = (ARCHIVE / "raif-workshop-setup.cmd").read_text(encoding="utf-8")

    m = re.search(r'set bashB64 to "([^"]+)"', asc_master)
    if not m:
        raise SystemExit("master AppleScript: no bashB64 line")
    bash_master = base64.b64decode(m.group(1)).decode("utf-8")

    for team_code, team_repo, team_human in TEAMS:
        bash_patched = patch_bash(bash_master, team_code, team_repo, team_human)
        bash_b64 = base64.b64encode(bash_patched.encode("utf-8")).decode("ascii")
        asc_patched = patch_applescript(asc_master, team_code, bash_b64)
        cmd_patched = patch_cmd(cmd_master, team_code, team_repo, team_human)

        out_dir = OUT / team_code
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raif-workshop-setup.applescript").write_bytes(
            asc_patched.encode("utf-16")
        )
        (out_dir / "raif-workshop-setup.cmd").write_bytes(
            cmd_patched.encode("utf-8")
        )
        print(f"  + {out_dir.relative_to(ROOT)}/  →  {team_repo}.git → ~/{team_repo}")

    # Organiser (host) variant — clones AI-Workshop, skips isolation, lands in ~/AI-Workshop.
    bash_h_b64 = base64.b64encode(bash_master.encode("utf-8")).decode("ascii")
    asc_h = patch_host_applescript(asc_master, bash_h_b64)
    cmd_h = patch_host_cmd(cmd_master)
    host_dir = OUT / "host"
    host_dir.mkdir(parents=True, exist_ok=True)
    (host_dir / "raif-workshop-setup.applescript").write_bytes(asc_h.encode("utf-16"))
    (host_dir / "raif-workshop-setup.cmd").write_bytes(cmd_h.encode("utf-8"))
    print(f"  + {host_dir.relative_to(ROOT)}/  →  AI-Workshop.git → ~/AI-Workshop (organiser)")

    print()
    print("Distribute each pair via private channel to the matching team.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
