# .codex/templates/

Templates of `.codex/config.toml` for every block — for participants who
work in **Codex** instead of Claude Code.

## Why

This is **hard isolation** of the same kind that `.claude/templates/`
provides for Claude: Codex physically cannot write outside your block's
folder and will not see the other team. Enforcement is done by Codex's OS
sandbox (Seatbelt on macOS, ACL on Windows), not by an instruction to the
agent.

## What's inside

Each file is a Codex permission profile (a `default_permissions` key and
a `[permissions.raif-<team>-<block>]` section) that:

- allows **reading** the whole filesystem (base `":root" = "read"`) — this
  is needed so Codex inside the sandbox can see system tools outside the
  repo (`git`, `bash`, etc.); `:root` grants no write;
- allows **writing** only into the participant's own block folder
  (`team_X/<block>`) and into the service folder `.git` (without it git
  cannot save the work into the shared pile);
- closes the **folders of the two other blocks in your team** via `deny`
  (no read, no write), but opens read on only their `CONTRACT.md` — this is
  the storefront through which blocks connect over the API;
- closes the other team via `deny` (no read, no write);
- enables network so the "pile" (git push/pull) works.

The targeted read on `team_X/<neighbour>/CONTRACT.md` is expected to win
against the wide `deny` on the neighbour's folder thanks to the
"more-specific path wins" rule documented in the top comment of each
`.toml` file (`# More specific path wins; priority deny > write > read`).
This behaviour has **not been empirically verified** in the current Codex
version yet — after template edits, run a manual test from a fresh Codex
session under the participant profile: try to read
`team_<own>/<neighbour>/CONTRACT.md` (should open) and
`team_<own>/<neighbour>/src/main.py` (should deny). If CONTRACT.md is
denied too — file-level allow inside folder-deny is not supported by Codex
and you need a different scheme (for example, keep storefronts in a
separate folder `team_<X>/contracts/`, where permissions are simpler).

Why `":root" = "read"` and `.git = "write"` are mandatory: Codex's sandbox
enforces at the OS level and applies to every process. With a narrow base
(`:minimal`) the agent cannot find `git` at all; without write into `.git`
— `git commit` fails on `.git/index.lock`. Isolation isn't blurred by
this: writes are limited to the participant's block plus `.git`, the other
team stays invisible.

The profile is bound to the repo root automatically: Codex finds the root
via `.git`, so the path doesn't need to be hard-coded — templates are
identical on every machine.

## When to apply

**Bootstrap applies it automatically** during laptop setup: copies the
right file into `.codex/config.toml` and marks the repo folder as trusted
in `~/.codex/config.toml`.

Manually, if bootstrap didn't run:

```bash
# for the CIB block of team A
cp .codex/templates/config-team_a-cib.toml .codex/config.toml
```

After that — restart Codex so it picks up the profile. If isolation
appears not to apply, check that the repo folder is marked
`trust_level = "trusted"` in `~/.codex/config.toml`.

## Relation to Claude

These are twin files of those in `.claude/templates/`. When you edit
access rules for a block — edit both, so Claude and Codex give the same
isolation.
