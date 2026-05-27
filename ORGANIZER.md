# ORGANIZER.md — brief for the workshop organiser

> This file is for **Vitaly Erokhin** and **Nerses Bagiyan** (the
> organisers). The participant onboarding lives in each team's repo (in
> `CLAUDE.md` / `AGENTS.md` of `team-template/`). With organisers we talk
> technically, no simplifications.

## Workshop format

AI workshop for the Raiffeisen bank board. Twelve board members are split
into **four teams of three**. A team is not one bank — it is **three
service blocks**: `retail` (the customer-facing mobile bank), `cib`
(corporate and business logic), `backend` (the data core). One participant
— one block. All four teams receive an **identical set of blocks** and
**in parallel, independently** solve the **same task** announced by the
host out loud.

A feature is done only when all three blocks of the team have done their
part and connected. Block links: retail → backend (data),
retail → cib (decision), cib → backend (customer data). Inside the team
the three participants agree out loud. There is no link between teams —
that is the point of the competition.

Customers are simulated against each bank. After a deploy the simulator
scores all three blocks of the team together and moves the customer base
up or down — with a human-readable rationale. The leaderboard shows the
four teams' scores head to head.

## Multi-repository layout

Each team works in its **own** GitHub repository (so one team's commits,
PRs and issues never leak to another). The organiser orchestrates the
whole thing from this repo:

```
ai-workshop                       (this orchestrator repo)
├── team_a/        submodule → ai-workshop-team-a
├── team_b/        submodule → ai-workshop-team-b
├── team_c/        submodule → ai-workshop-team-c
├── team_d/        submodule → ai-workshop-team-d
├── simulator/     customer simulator + leaderboard (deployed from here)
├── seed/          shared customer base for all four teams
├── tasks/         task briefs (the host announces which one)
├── docs/          design specs and implementation plans
├── team-template/ canonical contents of ONE team repo — the source of truth
└── tools/setup/   sync-team-repos.sh, add-submodules.sh
```

Each team repo is structurally identical at the starting line. The
content of `team-template/` is the single source of truth — when something
participant-facing changes, edit there and propagate to all four team
repos via `tools/setup/sync-team-repos.sh`.

## Repository layout (per team repo)

| Path | Purpose |
|---|---|
| `retail/`, `cib/`, `backend/` | three service blocks (FastAPI, Docker), each with `CONTRACT.md` declaring exposed endpoints |
| `seed/` | local copy of the customer dataset, used by the team's backend block |
| `tasks/` | task briefs |
| `.claude/templates/settings-{retail,cib,backend}.json` | Claude permission profiles, one per block |
| `.codex/templates/config-{retail,cib,backend}.toml` | Codex permission profiles, one per block |
| `CLAUDE.md`, `AGENTS.md`, `TEAM.md`, `RULES.md`, `README.md` | participant-facing docs |
| `tools/cowork-onboard.py` | sandbox onboarding for the agent (SSH key, git config, WORKSHOP_BLOCK) |
| `tools/bootstrap/raif-workshop-setup.{applescript,cmd}` | laptop setup scripts (regenerated per workshop, see SETUP.md) |
| `render.yaml` | Render Blueprint for the team's three services |
| `.github/workflows/deploy-render.yml` | per-team deploy hook on push to main |
| `docker-compose.yml` | local dev: the team's three blocks |

## How the customer simulator works

1. **Trigger — pull model.** Roughly every 30 seconds the simulator polls
   `/health` of all twelve bank blocks (3 × 4 teams) and reads git commits
   from the responses. A new commit on any block of a team → an evaluation
   round for that team.
2. **Probe.** A closed set of HTTP checks across three blocks of the
   committing team: backend (exposes a customer, accepts and lists
   applications), cib (a credit product in the catalogue, a decision
   endpoint, separation of strong vs weak applicants), retail (a credit
   tab in the UI, an end-to-end application, a human-readable decline
   rationale, a transfer regression).
3. **Judge — rubric + formula.** Each team is judged in an **independent
   LLM call** (parallel `asyncio.gather`), `temperature=0`. The LLM scores
   the team against **10 criteria** (3 backend + 3 cib + 4 retail); the
   customer count is computed by a deterministic formula in code (`B0=500`,
   `GAIN=0.6`, `RUBRIC_MAX=20`). If the LLM is unavailable — scripted
   fallback over the same checks. The simulator never stalls.
4. **Leaderboard** is built into the simulator: four customer bases and an
   event feed with rationales.

Manual control: `POST /admin/start`, `POST /admin/evaluate` (round on
demand), `POST /admin/stop`, `POST /admin/reset` (reset to baseline) —
with an `X-Admin-Token` header.

Generalising to a different number of teams: change the env var
`TEAM_NAMES` (comma-separated list) and provide three `<PREFIX>_*_URL`
env vars per team (prefix = first letter of the team suffix, uppercase).
Code in `simulator/src/main.py` and `judge.py` is fully parametric.

## Render — 13 web services (4 × 3 + simulator) + Postgres

Each team repo deploys its own three services from its own `render.yaml`.
The orchestrator repo deploys only the simulator.

| Service | Repo | URL |
|---|---|---|
| `raif-a-{backend,cib,retail}` | ai-workshop-team-a | `https://raif-a-*.onrender.com` |
| `raif-b-{backend,cib,retail}` | ai-workshop-team-b | `https://raif-b-*.onrender.com` |
| `raif-c-{backend,cib,retail}` | ai-workshop-team-c | `https://raif-c-*.onrender.com` |
| `raif-d-{backend,cib,retail}` | ai-workshop-team-d | `https://raif-d-*.onrender.com` |
| `raif-simulator` | ai-workshop (this repo) | `https://raif-simulator.onrender.com` |

Plus Postgres `raif-workshop-db` (free) — used only by the simulator: it
stores the four teams' customer base and the event log.

Deployment details — `DEPLOY.md`. Free-plan concurrent web service cap is
a real risk at 13 services; see "Risk: Render free-plan cap" below.

## Two agents: Claude Code and Codex

A participant can work in either Claude Code or Codex — their choice.
Block isolation is the same in spirit for both, but implemented with
different mechanisms:

- **Claude** reads `CLAUDE.md`, isolation lives in
  `.claude/settings.local.json` (deny/allow by path). Bootstrap copies
  it from `.claude/templates/settings-<block>.json`.
- **Codex** reads `AGENTS.md` (a thin wrapper that defers to `CLAUDE.md`),
  isolation is a permission profile in `.codex/config.toml`. Bootstrap
  copies it from `.codex/templates/config-<block>.toml` and marks the
  repo folder as trusted in `~/.codex/config.toml` (otherwise Codex
  doesn't load the project config). Enforcement is the Codex OS sandbox
  (Seatbelt / ACL).

Since each team lives in a separate GitHub repository, the "other team is
invisible" property is provided **by repo separation itself**, not by
deny rules — the participant doesn't have the other team's code on disk
at all. The templates only have to police the boundaries between blocks
of the same team.

Claude and Codex templates are twins — when you edit block access, edit
both.

Two Codex profile settings are critical (without them Codex breaks):

- The read base is `":root" = "read"` (NOT `:minimal`). The Codex sandbox
  enforces at the OS level; with the narrow `:minimal` base the agent
  cannot even find `git` in `/usr/bin`.
- Write into `.git` — `".git" = "write"`. Without it `git commit` fails
  on `.git/index.lock`.

Network for push is enabled in the profile (`[permissions.*.network]
enabled = true`).

**Verification status:** on macOS a full run has been done — Codex sees
git, writes only into its block, doesn't read the other team, and saving
to the shared pile works. **On Windows it hasn't been verified yet** —
run it before the workshop (there the Codex sandbox uses ACLs / restricted
tokens, behaviour may differ).

Separately for push on the corporate network: GitHub is reachable via SSH
only on port 443 (`ssh.github.com`), port 22 is closed. That is an ssh
channel thing, agent-independent. Installers already wire access via 443
(`HostName ssh.github.com`, `Port 443` in `~/.ssh/config`), so push from
a clean setup goes through without hiccups. For manual setup or a stale
config, diagnostics and fix are in `team-template/CLAUDE.md` (section
"Git and the shared pile").

## One-off setup (manual steps for the organiser)

See `SETUP.md` for the full checklist. In short:

1. Create five GitHub repositories: `ai-workshop`, `ai-workshop-team-a`,
   `-team-b`, `-team-c`, `-team-d`.
2. Generate one workshop SSH key pair; add the **public** key as a
   **deploy key with write access** in each of the four team repos.
3. Push `team-template/` content to each of the four team repos via
   `tools/setup/sync-team-repos.sh`.
4. From this orchestrator repo, run `tools/setup/add-submodules.sh` to
   wire the four team repos in as submodules.
5. Personalise the master bootstrap scripts (one variant per team) and
   distribute to participants.
6. Create one Render Blueprint per team repo (3 services each) and one
   Blueprint for the orchestrator (simulator + Postgres). Set
   `OPENAI_API_KEY` and `ADMIN_TOKEN` in the shared env group.
7. Add deploy-hook secrets: per team repo —
   `RENDER_HOOK_{BACKEND,CIB,RETAIL}`; in the orchestrator —
   `RENDER_HOOK_SIMULATOR`.

## Risk: Render free-plan cap

13 web services at once will likely hit the free-plan concurrent web
service cap on a single account. Fallbacks:

- merge `cib` and `backend` into a single service per team — drops it to
  9 services (still 3 over the typical cap),
- use a second Render account for two of the four teams,
- pay for a Starter plan for the workshop day (cheapest reliable path).

Verify the actual limit on your account before the workshop — this is
non-trivial at 4 × 3 + 1 services.

## What NOT to do

- Don't let the teams peek at each other — repo separation is the main
  guarantee, the deny rules inside templates are a secondary belt for
  same-team sibling blocks only.
- Don't let a participant edit a sibling block of their own team — wired
  into permissions; only their `CONTRACT.md` is readable.
- Don't hand teams the implementation — the task arrives as a brief, they
  solve it with the agent themselves.

## Links

- Full design (older, two-team): `docs/superpowers/specs/2026-05-17-three-block-teams-design.md`
- Implementation plan (older, two-team): `docs/superpowers/plans/2026-05-17-three-block-teams.md`
- Deployment: `DEPLOY.md`
- One-off setup: `SETUP.md`
