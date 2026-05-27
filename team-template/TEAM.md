# TEAM.md — what each block does and how to talk between blocks

> Reference for agents. Used during onboarding to remind everyone what the
> three blocks own and where they live.

## The team

This GitHub repository is one team's territory. The team is three service
blocks (`retail`, `cib`, `backend`), one participant per block. The other
three teams each have their own separate repository and aren't visible
from here.

There is no fixed roster of "who is in which block" here, on purpose.
Each participant picks the block themselves and types their name when
setting their laptop up — in `tools/bootstrap/raif-workshop-setup.applescript`
(macOS) or `raif-workshop-setup.cmd` (Windows). The choice is written into
`.git/raif-workshop-info` (`WORKSHOP_BLOCK`, `WORKSHOP_PARTICIPANT`),
where `tools/cowork-onboard.py` reads it when the agent starts. The team
identity is determined by which repo the participant cloned — not by a
picker.

## What each block does

- **retail** — the customer-facing mobile bank: UI and a thin layer. Asks
  backend for data, asks cib for the decision on a request. Holds no data
  of its own.
- **cib** — corporate and business logic: product catalogue and decision
  logic. Asks backend for customer data.
- **backend** — data core: stores customers, transactions, balances;
  exposes the basic API. No UI.

Block links: retail → backend, retail → cib, cib → backend. A feature is
done only when all three blocks of the team have done their part and
connected.

## How the agent learns the block

The participant's block comes from `.git/raif-workshop-info` — written by
the bootstrap based on the participant's own choice, and read by the
agent through `tools/cowork-onboard.py` (line `WORKSHOP_BLOCK`). Don't
guess from the name. If the info file is missing (bootstrap wasn't run) —
ask the participant for the block (retail / cib / backend) and their
name, don't guess.

## Organisers

| Name | Role |
|---|---|
| Vitaly Erokhin | Workshop organiser, GitHub @ErokhinVi |
| Nerses Bagiyan | Co-organiser, CDO Total Bank |

Organisers work in the separate orchestrator repository
(`ai-workshop` with team submodules), not in this team repo. If an
organiser somehow opens this repo by mistake — they want technical mode;
defer to the scenario in `CLAUDE.md` but skip the "non-technical user"
guard.

## Services and URLs

The exact Render URLs of this team's three services and the shared
leaderboard URL are filled in during workshop setup — see the section
below. If the placeholders are still in place, ask the organiser for the
final URLs and update this file.

| Block | Local | On Render |
|---|---|---|
| retail | `http://localhost:8001` | `https://raif-<TEAM_SLUG>-retail.onrender.com` |
| cib | `http://localhost:8002` | `https://raif-<TEAM_SLUG>-cib.onrender.com` |
| backend | `http://localhost:8003` | `https://raif-<TEAM_SLUG>-backend.onrender.com` |
| Leaderboard (organiser's simulator) | — | `https://raif-simulator.onrender.com` |

Show the participant their team's retail block — that's the bank the
customer sees. The leaderboard shows all four teams' scores head to head.
