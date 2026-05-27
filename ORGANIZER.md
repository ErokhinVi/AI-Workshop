# ORGANIZER.md — brief for the workshop organiser

> This file is for **Vitaly Erokhin** and **Nerses Bagiyan** (the organisers).
> If you are Claude Code and the user introduced themselves as one of them —
> read this file, not the participant onboarding in the root `CLAUDE.md`.
> With organisers we talk technically, no simplifications.

## Workshop format

AI workshop for the Raiffeisen bank board. Six board members are split into
**two teams of three**. A team is not one bank — it is **three service
blocks**: `retail` (the customer-facing mobile bank), `cib` (corporate and
business logic), `backend` (the data core). One participant — one block.
Both teams receive an **identical set of blocks** and **in parallel,
independently** solve the **same task** announced by the host out loud.

A feature is done only when all three blocks of the team have done their
part and connected. Block links: retail → backend (data),
retail → cib (decision), cib → backend (customer data). Inside the team
the three participants agree out loud. There is no link between teams —
that is the point of the competition.

Customers are simulated against the bank. After a deploy the simulator
scores all three blocks of the team together and moves the customer base
up or down — with a human-readable rationale. The leaderboard shows the
two teams' score head to head.

## Repository layout

| Folder / file | Purpose |
|---|---|
| `team_a/{retail,cib,backend}/`, `team_b/...` | The six team service blocks — identical copies at the starting line (FastAPI, Docker). Each block has its own `CONTRACT.md` describing the endpoints it exposes |
| `simulator/` | Customer simulator + leaderboard (FastAPI, Postgres) |
| `seed/` | 500 customers, transactions, credit history |
| `tasks/` | Task briefs for teams (non-technical wording) |
| `.claude/templates/settings-team_*-*.json` | Claude permissions: a participant edits their own block, sees the neighbours' `CONTRACT.md` only, the other team isn't visible at all |
| `.codex/templates/config-team_*-*.toml` | Same for Codex: a permission profile per (team, block). Bootstrap copies it into `.codex/config.toml` |
| `AGENTS.md` | Onboarding for Codex (analogue of `CLAUDE.md`): a thin wrapper, defers to `CLAUDE.md` plus Codex-specific bits |
| `tools/cowork-onboard.py` | Sandbox onboarding for the agent: SSH key, git, `WORKSHOP_TEAM`, `WORKSHOP_BLOCK` (agent-neutral) |
| `docs/superpowers/specs/`, `docs/superpowers/plans/` | Design spec and implementation plan |

## How the customer simulator works

1. **Trigger — pull model.** Roughly every 30 seconds the simulator polls
   `/health` of all six bank blocks and reads git commits. A new commit on
   any block of a team → an evaluation round.
2. **Probe.** A closed set of HTTP checks across three blocks: backend
   (exposes a customer, accepts and lists applications), cib (a credit
   product in the catalogue, a decision endpoint, separation of strong vs
   weak applicants), retail (a credit tab in the UI, an end-to-end
   application, a human-readable decline rationale, a transfer regression).
3. **Judge — rubric + formula.** Snapshots of three blocks of both teams
   go to the LLM in **one call** (relative scoring is fairer),
   `temperature=0`. The LLM scores against **10 criteria** (3 backend +
   3 cib + 4 retail); the customer count is computed by a deterministic
   formula in code (`B0=500`, `GAIN=0.6`, `RUBRIC_MAX=20`). If the LLM is
   unavailable — scripted fallback over the same checks. The simulator
   never stalls.
4. **Leaderboard** is built into the simulator: the two customer bases and
   an event feed with rationales.

Manual control: `POST /admin/evaluate` (round on demand) and
`POST /admin/reset` (reset to baseline) — with an `X-Admin-Token` header.

## Render — seven services

| Service | Folder | URL |
|---|---|---|
| `raif-a-backend` | `team_a/backend/` | `https://raif-a-backend.onrender.com` |
| `raif-a-cib` | `team_a/cib/` | `https://raif-a-cib.onrender.com` |
| `raif-a-retail` | `team_a/retail/` | `https://raif-a-retail.onrender.com` |
| `raif-b-backend` | `team_b/backend/` | `https://raif-b-backend.onrender.com` |
| `raif-b-cib` | `team_b/cib/` | `https://raif-b-cib.onrender.com` |
| `raif-b-retail` | `team_b/retail/` | `https://raif-b-retail.onrender.com` |
| `raif-simulator` | `simulator/` | `https://raif-simulator.onrender.com` (leaderboard) |

Deployment details — `DEPLOY.md`.

## Two agents: Claude Code and Codex

A participant can work in either Claude Code or Codex — their choice. Block
isolation is the same in spirit for both, but implemented with different
mechanisms:

- **Claude** reads `CLAUDE.md`, isolation lives in `.claude/settings.local.json`
  (deny/allow by path). Bootstrap copies it from `.claude/templates/`.
- **Codex** reads `AGENTS.md` (a thin wrapper that defers to `CLAUDE.md`),
  isolation is a permission profile in `.codex/config.toml`
  (`default_permissions` + `[permissions.*]`). Bootstrap copies it from
  `.codex/templates/` and marks the repo folder as trusted in
  `~/.codex/config.toml` (otherwise Codex doesn't load the project config).
  Enforcement is the Codex OS sandbox (Seatbelt / ACL), same idea as
  Claude: writes outside your block don't go through, the other team is
  blocked on read via `deny`.

Claude and Codex templates are twins — when you edit block access, edit
both.

Two Codex profile settings are critical (without them Codex breaks) — be
careful when changing:

- The read base is `":root" = "read"` (NOT `:minimal`). The Codex sandbox
  enforces at the OS level; with the narrow `:minimal` base the agent
  cannot even find `git` in `/usr/bin`. `:root` opens read of the whole FS
  (system tools), but grants no write.
- Write into `.git` — `".git" = "write"`. Without it `git commit` fails on
  `.git/index.lock` (the sandbox guards `.git`). With this rule, saving to
  the shared pile works straight from the sandbox. Isolation isn't blurred:
  writes are still limited to the participant's block plus `.git`, the
  other team stays closed.

Network for push is enabled in the profile
(`[permissions.*.network] enabled = true`).

**Verification status:** on macOS a full run has been done — Codex sees
git, writes only into its block, doesn't read the other team, and saving
to the shared pile works.
**On Windows it hasn't been verified yet** — run it before the workshop
(there the Codex sandbox uses ACLs / restricted tokens, behaviour may
differ). Quick check on a fresh machine:

1. Installer → pick team and block → `cat .codex/config.toml` shows the
   right `default_permissions` and rules (`:root` read, own block + `.git`
   write, other team deny).
2. `~/.codex/config.toml` has `[projects."…/AI-Workshop"]` with
   `trust_level = "trusted"`.
3. In Codex: `git --version` finds git; an edit in the own block goes
   through; writing into a sibling block and reading the other team are
   refused; "save to the pile" (add → commit → pull → push) works.

Separately for push on the corporate network: GitHub is reachable via SSH
only on port 443 (`ssh.github.com`), port 22 is closed. That is an ssh
channel thing, agent-independent. Installers already wire access via 443
(`HostName ssh.github.com`, `Port 443` in `~/.ssh/config`), so push from a
clean setup goes through without hiccups. For manual setup or a stale
config, diagnostics and fix are in `CLAUDE.md` (section "Git and the
shared pile").

## Manual steps for the organiser

1. **Render.** Delete the old services. Apply the new Blueprint
   (`render.yaml`): 7 web services + Postgres `raif-workshop-db`. In the
   env group `ai-workshop-shared` set `OPENAI_API_KEY` and `ADMIN_TOKEN`.
   Add seven deploy-hook secrets in GitHub: `RENDER_HOOK_A_BACKEND`,
   `RENDER_HOOK_A_CIB`, `RENDER_HOOK_A_RETAIL`, `RENDER_HOOK_B_BACKEND`,
   `RENDER_HOOK_B_CIB`, `RENDER_HOOK_B_RETAIL`, `RENDER_HOOK_SIMULATOR`.
2. **Teams.** There is no longer a hard-coded roster: the bootstrap script
   asks each participant for team and block themselves. The organiser only
   announces the assignment in the room. See `tools/bootstrap/README.md`
   for the participant flow.
3. **Laptops.** Run the bootstrap on every participant's machine so that
   `.git/raif-workshop-info` contains `WORKSHOP_TEAM` and `WORKSHOP_BLOCK`.

## Render free-plan risk

Seven web services at once — the Render free plan has a cap on the number
of concurrent web services per account. If you hit the limit, a fallback
is to merge one participant's `cib` and `backend` into a single service
(6 services instead of 7), or use a second account for one of the teams.
Verify the limit before the workshop.

## What NOT to do

- Don't let the teams peek at each other — blindness is wired into
  `settings-team_*-*.json` (`Read` of the other team is denied).
- Don't let a participant edit a sibling block of their own team — also
  wired into permissions; only their `CONTRACT.md` is readable for
  sibling blocks.
- Don't hand teams the implementation — the task arrives as a brief, they
  solve it with the agent themselves.

## Links

- Full design: `docs/superpowers/specs/2026-05-17-three-block-teams-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-17-three-block-teams.md`
- Deployment: `DEPLOY.md`
