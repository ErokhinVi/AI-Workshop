# CLAUDE.md — organiser orchestrator

> This file is read automatically when Claude Code starts in the organiser
> repository. The user here is Vitaly Erokhin or Nerses Bagiyan — the
> workshop organisers. Talk technically, no simplifications.

## What this repo is

The orchestrator for the Raiffeisen AI workshop:

- four team submodules at `team_a/`, `team_b/`, `team_c/`, `team_d/` —
  each pointing to a separate GitHub repository of one team;
- the customer simulator + leaderboard in `simulator/`;
- the seed dataset and the task briefs;
- the canonical team-repo template in `team-template/`;
- helper scripts in `tools/setup/`.

Participant onboarding lives in each team repo (see
`team-template/CLAUDE.md`), not here. If by mistake a participant ends up
in this orchestrator repo, point them at their team's repo.

## Quick references

- `ORGANIZER.md` — full workshop format, four teams, multi-repo layout
- `DEPLOY.md` — Render deploy flow (per-team-repo + simulator)
- `SETUP.md` — manual one-off steps to set up the four team repos and the simulator
- `docs/superpowers/specs/` — design specs (still describe the previous two-team layout; refresh if needed)
- `docs/superpowers/plans/` — implementation plans

## When the organiser asks for changes

- Changes to **participant-facing** content (block code, isolation
  templates, bootstrap, team-side docs) → edit `team-template/`, then push
  to each of the four team repositories via `tools/setup/sync-team-repos.sh`
  (or via direct `git push` from each working clone).
- Changes to **simulator / leaderboard / seed / tasks / organiser docs** →
  edit here; `git push` to `main` triggers the Render deploy hook of
  `raif-simulator` automatically.
- Adding a fifth team or renaming a team → edit `.gitmodules`,
  `render.yaml` (env var `TEAM_NAMES` + URL set) and `simulator/` if needed
  (it's parametric on TEAMS).

## Submodules quick reference

```
git submodule update --init --recursive    # first init after clone
git submodule update --remote              # pull latest team commits
git add team_a team_b team_c team_d && git commit -m "bump team submodules"
```

The simulator is **not** dependent on the submodule state being current
— it polls team services over the network by their Render URLs. The
submodule pointers in this repo are mostly documentation: they let an
organiser-side agent jump between team codebases without re-cloning.
