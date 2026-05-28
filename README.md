# Raiffeisen Bank — AI workshop, organiser orchestrator

This is the **organiser** repository for the Raiffeisen AI workshop. It
orchestrates four independent team repos via git submodules, hosts the
customer simulator + leaderboard and the workshop documentation.

Repository layout

| Path | What it is |
|---|---|
| `team_a/`, `team_b/` | submodules pointing to each team's GitHub repository (see `.gitmodules`) |
| `simulator/` | customer simulator + leaderboard (FastAPI + Postgres) |
| `seed/` | 500 customers, transactions, credit history (shared seed for all four teams) |
| `tasks/` | task briefs (read-only reference) |
| `docs/` | full design specs and implementation plans |
| `team-template/` | canonical contents of one team repo — what gets pushed to each of the four team GitHub repos |
| `tools/setup/` | helper scripts for the organiser to set up the four team repos |
| `_archive/` | the old two-team layout, preserved as reference |

The workshop format and the participant scenario live in the team repos
(see `team-template/` for the canonical version). The organiser-facing
documentation is in this repo:

- `ORGANIZER.md` — workshop format, four teams, multi-repo layout
- `DEPLOY.md` — how code reaches Render (per-team-repo deploy hooks + simulator)
- `SETUP.md` — the one-off manual steps to set up the four team repos and the orchestrator before the workshop

For organisers — start with `SETUP.md`.
