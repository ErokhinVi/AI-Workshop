# SETUP.md — one-off setup for the four-team multi-repo workshop

> For the workshop organiser. Run once, before the workshop.

The workshop now lives across **five GitHub repositories**:

- `ai-workshop` — this orchestrator (simulator, leaderboard, seed, tasks,
  team-template, organiser docs, submodule pointers to the four teams);
- `ai-workshop-team-a`, `-team-b`, `-team-c`, `-team-d` — one per team,
  each containing three service blocks (`retail`, `cib`, `backend`).

What follows are the steps **I (Claude) cannot do for you** in this
session, plus the helper scripts that take the bulk of the grunt work
off your plate.

## Step 1 — Create the five GitHub repositories (manual)

Create them as **empty private repos** under the organisation account
you used before (e.g. `erokhinvi`):

- `ai-workshop` — public/private, your call. This is the orchestrator
  repo (already exists; you're reading SETUP.md from it).
- `ai-workshop-team-a`
- `ai-workshop-team-b`
- `ai-workshop-team-c`
- `ai-workshop-team-d`

Do NOT add a README, .gitignore or license on creation — leave them
truly empty so `sync-team-repos.sh` can push without fighting an
initial commit.

## Step 2 — Generate one workshop SSH key pair (manual)

Generate a single ed25519 keypair (you decided "one shared deploy key for
all four team repos"). Locally:

```
ssh-keygen -t ed25519 -C "raif-workshop-2026" -f ~/.ssh/raif_workshop -N ""
```

This produces `~/.ssh/raif_workshop` (private) and
`~/.ssh/raif_workshop.pub` (public).

In each of the four **team** repos on GitHub: Settings → Deploy keys →
Add deploy key:

- Title: `raif-workshop-2026`
- Key: paste the contents of `raif_workshop.pub`
- **Allow write access:** ✓ (mandatory — participants commit through this key)

Do NOT add the deploy key to the orchestrator repo — participants don't
push there.

After the workshop: delete the deploy key in all four team repos. Once
deleted, the embedded private key in distributed bootstrap scripts is
useless — which is the point.

## Step 3 — Push team-template content to the four team repos

In this orchestrator repo on your laptop, run:

```
tools/setup/sync-team-repos.sh \
    git@github.com:erokhinvi/ai-workshop-team-a.git \
    git@github.com:erokhinvi/ai-workshop-team-b.git \
    git@github.com:erokhinvi/ai-workshop-team-c.git \
    git@github.com:erokhinvi/ai-workshop-team-d.git
```

Substitute your own org name if not `erokhinvi`. The script clones each
empty team repo, mirrors `team-template/` into it, commits and pushes to
`main`. After this all four team repos have identical starting state.

## Step 4 — Wire the four team repos in as submodules

Still in this orchestrator repo:

```
tools/setup/add-submodules.sh \
    git@github.com:erokhinvi/ai-workshop-team-a.git \
    git@github.com:erokhinvi/ai-workshop-team-b.git \
    git@github.com:erokhinvi/ai-workshop-team-c.git \
    git@github.com:erokhinvi/ai-workshop-team-d.git

git commit -m "wire four team submodules"
git push origin HEAD:main
```

`.gitmodules` will be populated with the four submodule entries; the
two directories `team_a/`, `team_b/` will appear
at the orchestrator root.

## Step 5 — Personalise and distribute the laptop bootstrap (manual)

The previous workshop's bootstrap scripts —
`raif-workshop-setup.applescript` (macOS) and `raif-workshop-setup.cmd`
(Windows) — are preserved in `_archive/tools/bootstrap/` and are the
master source. They are NOT in `team-template/tools/bootstrap/` on
purpose: they embed the workshop SSH private key and the clone URL of
the (then) single repo, both of which need to be replaced per team.

To personalise per team:

1. Copy the masters from `_archive/tools/bootstrap/`:
   `raif-workshop-setup.applescript` and `raif-workshop-setup.cmd`.
2. **Remove the team picker.** In the AppleScript, delete the "Pick your
   team" dialog block (the one that sets `teamCode` to `team_a` /
   `team_b`). In the .cmd, remove the `$teamA` / `$teamB` radio buttons
   and the `$cfg.Team` derivation.
3. Replace path operations that include `team_a/<block>` with `<block>`
   (`team_a/retail/` → `retail/`, etc.).
4. Replace the Claude / Codex template names from
   `settings-team_a-<block>.json` → `settings-<block>.json` and
   `config-team_a-<block>.toml` → `config-<block>.toml`.
5. Replace the clone URL with the team-specific one (e.g.
   `git@github.com:erokhinvi/ai-workshop-team-a.git`) and the local
   destination folder name (e.g. `AI-Workshop-Team-A` to disambiguate
   between teams if a participant ends up with several).
6. Swap in the new workshop SSH **private** key into the heredoc /
   `$PrivateKeyB64` block.

Produce **four pairs** of scripts — one per team. Distribute via private
channel (AirDrop / corporate messenger) to that team's three members
only.

This step is fiddly and the .applescript file is UTF-16 — I (Claude) did
not attempt to mutate the master bootstrap automatically because corrupting
the encoding or the embedded base64 key would silently break participants'
laptops on the day. If you want me to make a Python generator that does
the per-team substitution in a follow-up session, ask.

## Step 6 — Render Blueprints (manual, in Render UI)

Create **one shared env group** once:

- New → Environment Group → name `ai-workshop-shared`
- Set `OPENAI_API_KEY` and `ADMIN_TOKEN`
- (Already in `render.yaml`: `OPENAI_BASE_URL`, `OPENAI_MODEL`.)

Then **five Blueprints** (one Render Blueprint per repo):

1. **Orchestrator.** New → Blueprint → connect `ai-workshop`. It picks up
   the orchestrator `render.yaml` → creates `raif-simulator` +
   `raif-workshop-db`.
2. **Each team repo.** New → Blueprint → connect the team repo. Before
   applying, edit `render.yaml` in the team repo to replace
   `<TEAM_SLUG>` placeholders with the team letter (`a`, `b`, `c`, `d`):

   ```
   sed -i 's/<TEAM_SLUG>/a/g' render.yaml      # in ai-workshop-team-a
   git commit -am 'set team slug' && git push
   ```

   Then apply the Blueprint → 3 web services per team.

After all four team Blueprints are applied, the Render URLs of all 12
team services will be `https://raif-<a|b|c|d>-<retail|cib|backend>.onrender.com`,
which is exactly what the orchestrator's `render.yaml` expects.

## Step 7 — Deploy hook secrets (manual)

In **each team repo** on GitHub → Settings → Secrets and variables →
Actions:

- `RENDER_HOOK_BACKEND` — copy from Render → that team's backend service → Settings → Deploy Hook
- `RENDER_HOOK_CIB`
- `RENDER_HOOK_RETAIL`

In the **orchestrator** repo on GitHub:

- `RENDER_HOOK_SIMULATOR` — copy from Render → `raif-simulator` → Settings → Deploy Hook

## Step 8 — Smoke check

1. From your laptop, in each team repo, make a trivial commit (e.g. touch
   a comment in `retail/src/main.py`) and push. The team repo's Action
   runs and triggers the right Render service. After ~2-4 minutes,
   `https://raif-<t>-retail.onrender.com/health` returns 200 with the
   new commit SHA.
2. Visit `https://raif-simulator.onrender.com/` (the leaderboard). The
   four team cards should be present.
3. POST `/admin/start` with the admin token — the simulator baselines
   all four teams and starts polling.

## Step 9 — On the workshop day

Hand each team member their personalised bootstrap. They double-click,
pick a block (`retail` / `cib` / `backend`), type their name, and the
laptop is ready. The AI assistant follows the participant onboarding
from each team repo's `CLAUDE.md` automatically.

The leaderboard URL is the same for everyone. Show it on a big screen.

## After the workshop

- Delete the workshop SSH deploy key in all four team repos.
- Optionally delete the four team repos (or archive them for keepsakes).
- Drop the free Postgres in Render (or wait for the 90-day auto-expire).

## What I (Claude) did vs what's left for you

Done in this session (committed in this orchestrator repo):

- Moved the old two-team monorepo content into `_archive/`.
- Created `team-template/` with a clean single-team layout: three blocks
  with paths stripped of `team_a/` prefix, isolation templates without
  `team_b` deny rules, single-team `CLAUDE.md` / `AGENTS.md` / `TEAM.md`
  / `RULES.md` / `README.md`, single-team `render.yaml` and
  `docker-compose.yml`, per-team `deploy-render.yml`, and
  `tools/cowork-onboard.py` that no longer asks for a team picker.
- Generalised `simulator/` to N teams via `TEAM_NAMES` env var; all
  hardcoded `team_a/team_b` removed in code, tests updated and passing
  (39 tests green).
- Updated the orchestrator's `render.yaml`, `docker-compose.yml`,
  `.github/workflows/deploy-render.yml`, `README.md`, `CLAUDE.md`,
  `ORGANIZER.md`, `DEPLOY.md` for the four-team multi-repo layout.
- Added `.gitmodules` placeholder and two helper scripts under
  `tools/setup/`.

Manual steps (you):

- **Steps 1, 2, 6, 7** — creating GitHub repos, generating SSH key,
  setting up Render Blueprints, configuring secrets. Web-UI work I can't
  do.
- **Steps 3, 4** — running the two helper scripts I wrote (one-line
  invocations).
- **Step 5** — personalising the four pairs of bootstrap scripts. I
  deliberately did not touch the master .applescript/.cmd because the
  UTF-16 / embedded base64 key is risky to mutate without verifying
  byte-by-byte. The diff is small (team picker out, paths shortened,
  template names changed) — comfortable for a focused 1-2 hour session.
  If you want a generator for it, that's a clean follow-up task.
- **Step 8** — smoke check before the workshop.
