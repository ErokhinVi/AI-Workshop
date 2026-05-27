# AGENTS.md — agent onboarding (Codex)

> Codex reads this file automatically on start.
> It's short on purpose: your full scenario lives in `CLAUDE.md`.

## Step 0 (do this first). Read `CLAUDE.md` in full and follow it

`CLAUDE.md` is the workshop's main scenario: who's in front of you, how to
talk to them, what the task is, how the team is arranged, how to save your
work. It's written for another agent (Claude Code), but the scenario, tone
and rules are shared. **Read it in full and work by it.** What follows
below are only the differences that affect you as Codex.

## Who's in front of you (briefly, details in `CLAUDE.md`)

A non-technical senior bank executive at the AI workshop. Organisers work
in a separate orchestrator repo; if you're in this team repo, treat the
user as a participant: English, no jargon, plain live prose without
markdown, celebrate small wins, never leave them stuck.

## Difference 1. Your block isolation lives in a different file

For Claude, isolation is `.claude/settings.local.json`. For you it's a
**permissions profile in `.codex/config.toml`** (at the repo root). It
allows writes only into your block's folder; read of the two neighbouring
blocks of your team is limited to their `CONTRACT.md`. The other teams
live in separate repositories and are not even present in your filesystem.
This is an OS-sandbox constraint of Codex, not my instruction: writes
outside your block physically won't go through.

Check the file is there: `ls .codex/config.toml`. If it's missing
(bootstrap didn't run) — copy the right template and ask the user to
restart Codex:

```
cp .codex/templates/config-<block>.toml .codex/config.toml
```

where `<block>` = `retail`, `cib` or `backend`. Take the block from the
output of `tools/cowork-onboard.py` (line `WORKSHOP_BLOCK`).

One more condition: the repo folder must be marked as trusted in your
personal `~/.codex/config.toml` (`trust_level = "trusted"`), otherwise
Codex won't read the project config. The bootstrap does this too; if
isolation appears not to apply — check this first.

## Difference 2. Launch from the repo folder

The permissions profile is bound to the repo root (Codex finds it via
`.git`). Work inside the cloned team repo folder — then isolation is
active automatically, no matter where within it you started.

## Difference 3. "Pile" (saving work)

Your profile already gives everything needed to save: write into the
`.git` service folder and network access. So saving to the shared pile
works straight from the sandbox; Codex may ask for confirmation on git
commands or network access — that's normal, agree. The save steps
themselves are the same as in `CLAUDE.md` (section "Git and the shared
pile"), including the important port 443 note: on the corporate network,
GitHub is only reachable via `ssh.github.com:443`. Don't mention git,
commits and so on to the user: for them it's "save your work to the
team's shared pile".

## Everything else — as in `CLAUDE.md`

Block boundaries, three-block integration, the feedback loop with the
leaderboard, the ban on technical jargon when talking to the user, the
URLs of this team's blocks and the leaderboard in `TEAM.md` — all of that
lives there. If anything here diverges from `CLAUDE.md` in user-facing
behaviour — go with `CLAUDE.md`; this file is only about Codex specifics.
