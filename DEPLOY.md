# Deploying to Render

How code reaches production after `git push`. For organisers and team
agents. The workshop runs 13 web services (4 teams × 3 blocks +
simulator) plus Postgres — across **five GitHub repositories**.

## Where code lives → where it deploys

| Repo | Deploys |
|---|---|
| `ai-workshop` (this orchestrator) | `raif-simulator` + Postgres |
| `ai-workshop-team-a` | `raif-a-{backend,cib,retail}` |
| `ai-workshop-team-b` | `raif-b-{backend,cib,retail}` |
| `ai-workshop-team-c` | `raif-c-{backend,cib,retail}` |
| `ai-workshop-team-d` | `raif-d-{backend,cib,retail}` |

Postgres `raif-workshop-db` (free) is used only by the simulator: it
stores all four teams' customer base and the event log.

## How deployment works

In each team repo:

```
git push origin main
        ↓
GitHub Action "Deploy services via Render Deploy Hooks"
        ↓
        git diff → which folders changed (backend/, cib/, retail/)
        ↓
        curl POST to the Render Deploy Hook of the touched service
        ↓
Render builds a Docker image from the changed folder (~2-4 minutes)
```

In the orchestrator repo:

```
git push origin main (with changes under simulator/ or render.yaml)
        ↓
GitHub Action "Deploy simulator via Render Deploy Hook"
        ↓
        curl POST to RENDER_HOOK_SIMULATOR
        ↓
Render builds raif-simulator
```

| Change in | Deploys |
|---|---|
| `backend/**` (in a team repo) | that team's `raif-<t>-backend` |
| `cib/**` (in a team repo) | that team's `raif-<t>-cib` |
| `retail/**` (in a team repo) | that team's `raif-<t>-retail` |
| `seed/**` (in a team repo) | that team's backend block |
| `render.yaml` (in a team repo) | all three of that team's services |
| `simulator/**` (orchestrator) | `raif-simulator` |
| `seed/**`, `tasks/**`, `docs/**` (orchestrator) | nothing (the orchestrator does not deploy these) |

Note: the seed dataset lives in **both** the orchestrator (for the
simulator) and in each team repo (for the team's backend block). If you
want to evolve the seed during the workshop, push the same change to all
five repos — or copy from `team-template/seed/` and re-sync.

## Deploy hooks

Each team repo's Action is `.github/workflows/deploy-render.yml`.
Triggers: `push` to `main` and manual `workflow_dispatch`. Three secrets
per team repo (Settings → Secrets and variables → Actions):

- `RENDER_HOOK_BACKEND`
- `RENDER_HOOK_CIB`
- `RENDER_HOOK_RETAIL`

The orchestrator repo's Action wants one secret:

- `RENDER_HOOK_SIMULATOR`

The hook URL for each service: Render → service → Settings → Deploy Hook.
If a secret isn't set — the Action prints a warning and skips the service
without failing.

## Environment variables

Env group `ai-workshop-shared` (set once in the Render UI; same group can
be reused across all five Blueprints):

- `OPENAI_API_KEY` — key for the LLM (cib — credit decline explanation;
  simulator — judge). `OPENAI_BASE_URL`, `OPENAI_MODEL` — defaults are in
  `render.yaml`.
- `ADMIN_TOKEN` — token for the simulator's `/admin/*`.

Per team service (in each team repo's `render.yaml`):

- `backend` block — `TEAM_NAME` (`team_a` / `team_b`);
- `cib` block — `TEAM_NAME` and `BACKEND_URL` (the team's own backend);
- `retail` block — `TEAM_NAME`, `BACKEND_URL` and `CIB_URL`.

Simulator (in the orchestrator's `render.yaml`):

- `TEAM_NAMES=team_a,team_b`
- 12 URLs: `A_BACKEND_URL`, `A_CIB_URL`, `A_RETAIL_URL`, … `D_RETAIL_URL`
- `ACTIVE_TASK=credit`
- `DATABASE_URL` (from the Postgres `raif-workshop-db`)

`RENDER_GIT_COMMIT` is filled in by Render itself — each block returns it
from `/health`, and the simulator uses it to catch deploys.

## Re-evaluation after deploy

The simulator is **not** poked by the GitHub Action. It pulls — every
~30 seconds it polls `/health` of all twelve team blocks; once it sees a
new git commit on any of a team's blocks → it probes that team's three
blocks and recomputes the team's customer base. This is more robust:
Render free instances take 20-30 seconds to wake from a cold start, and
a push trigger would catch the old version.

## If a deploy didn't pick up

1. GitHub → Actions → last run → is there a warning about a missing
   secret (in the right repo — the team repo for team services, the
   orchestrator for the simulator).
2. Render → service → Events → is a new build recorded; if not — Manual
   Deploy → Deploy latest commit.
3. Build failed — Events shows the stack trace (common causes: syntax in
   `main.py`, a package not found).

## Free plan

All services and Postgres are free. Instances sleep after 15 minutes idle
(first request +20-30 seconds). The free Postgres lives for 90 days —
create a fresh one before the workshop. 13 web services may hit the
free-plan concurrent web service cap — see the risk note in
`ORGANIZER.md`.
