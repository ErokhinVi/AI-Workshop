# Deploying to Render

How code reaches production after `git push`. For organisers and team
agents.

## Seven services

| Service | Repo folder | URL |
|---|---|---|
| `raif-a-backend` | `team_a/backend/` | `https://raif-a-backend.onrender.com` |
| `raif-a-cib` | `team_a/cib/` | `https://raif-a-cib.onrender.com` |
| `raif-a-retail` | `team_a/retail/` | `https://raif-a-retail.onrender.com` |
| `raif-b-backend` | `team_b/backend/` | `https://raif-b-backend.onrender.com` |
| `raif-b-cib` | `team_b/cib/` | `https://raif-b-cib.onrender.com` |
| `raif-b-retail` | `team_b/retail/` | `https://raif-b-retail.onrender.com` |
| `raif-simulator` | `simulator/` | `https://raif-simulator.onrender.com` |

Plus Postgres `raif-workshop-db` (free) — used only by the simulator: it
stores the teams' customer base and the event log.

## How deployment works

```
git push origin main
        ↓
GitHub Action "Deploy services via Render Deploy Hooks"
        ↓
        git diff → which folders changed
        ↓
        curl POST to the Render Deploy Hook of the touched service
        ↓
Render builds a Docker image from the changed folder (~2-4 minutes)
```

| Change in | Deploys |
|---|---|
| `team_a/backend/**` | `raif-a-backend` |
| `team_a/cib/**` | `raif-a-cib` |
| `team_a/retail/**` | `raif-a-retail` |
| `team_b/backend/**` | `raif-b-backend` |
| `team_b/cib/**` | `raif-b-cib` |
| `team_b/retail/**` | `raif-b-retail` |
| `simulator/**` | `raif-simulator` |
| `seed/**` | both backend blocks |
| `render.yaml` | all seven |
| `tasks/`, `docs/`, `.github/` | nothing |

## Deploy hooks

The Action is `.github/workflows/deploy-render.yml`. Triggers: `push` to
`main` and manual `workflow_dispatch` (services chosen via comma-separated
list or `all`). Seven secrets are required in GitHub (Settings → Secrets
and variables → Actions):

- `RENDER_HOOK_A_BACKEND`
- `RENDER_HOOK_A_CIB`
- `RENDER_HOOK_A_RETAIL`
- `RENDER_HOOK_B_BACKEND`
- `RENDER_HOOK_B_CIB`
- `RENDER_HOOK_B_RETAIL`
- `RENDER_HOOK_SIMULATOR`

The hook URL for each service: Render → service → Settings → Deploy Hook.
If a secret isn't set — the Action prints a warning and skips the service
without failing.

## Environment variables

Env group `ai-workshop-shared` (set once in the Render UI):

- `OPENAI_API_KEY` — key for the LLM (cib — credit decline explanation;
  simulator — judge). `OPENAI_BASE_URL`, `OPENAI_MODEL` — defaults are in
  `render.yaml`.
- `ADMIN_TOKEN` — token for the simulator's `/admin/*`.

Per service (in `render.yaml`):

- `backend` blocks — `TEAM_NAME` (`team_a` / `team_b`);
- `cib` blocks — `TEAM_NAME` and `BACKEND_URL` (their team's backend);
- `retail` blocks — `TEAM_NAME`, `BACKEND_URL` and `CIB_URL`;
- simulator — six `*_URL`s (`A_BACKEND_URL`, `A_CIB_URL`, `A_RETAIL_URL`
  and the three for team B), `ACTIVE_TASK`, `DATABASE_URL` (from the DB
  `raif-workshop-db`).

`RENDER_GIT_COMMIT` is filled in by Render itself — the block returns it
from `/health`, and the simulator uses it to catch deploys.

## Re-evaluation after deploy

The simulator is **not** poked by the GitHub Action. It pulls — every
~30 seconds it polls `/health` of all six blocks; once it sees a new git
commit on any of a team's blocks → it probes the three blocks and
recomputes the team's customer base. This is more robust: Render free
instances take 20-30 seconds to wake from a cold start, and a push trigger
would catch the old version.

## If a deploy didn't pick up

1. GitHub → Actions → last run → is there a warning about a missing secret.
2. Render → service → Events → is a new build recorded; if not —
   Manual Deploy → Deploy latest commit.
3. Build failed — Events shows the stack trace (common causes: syntax
   in `main.py`, a package not found).

## Free plan

All services and Postgres are free. Instances sleep after 15 minutes idle
(first request +20-30 seconds). The free Postgres lives for 90 days —
create a fresh one before the workshop. Seven web services may hit the
free-plan concurrent web service cap — see the risk note in
`ORGANIZER.md`.
