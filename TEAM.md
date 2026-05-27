# TEAM.md — who is in which team and which block

> Reference for agents. Used during onboarding to map a participant to a
> (team, block) pair.

## Team composition

The workshop is two teams of three. Each team is three service blocks
(`retail`, `cib`, `backend`), one participant per block. Both teams solve
the same task, in parallel and independently.

There is no fixed roster of "who is in which team and block" here, on
purpose. Each participant picks team (A or B) and block
(retail / cib / backend) themselves and types their name when setting the
laptop up — in `tools/bootstrap/raif-workshop-setup.applescript` (macOS)
or `raif-workshop-setup.cmd` (Windows). The choice is written into
`.git/raif-workshop-info` (`WORKSHOP_TEAM`, `WORKSHOP_BLOCK`,
`WORKSHOP_PARTICIPANT`), where `tools/cowork-onboard.py` reads it when the
agent starts.

> The only place that ships a pre-baked assignment is the separate file
> `tools/bootstrap/raif-workshop-setup-board.*` (the version handed to
> board members). Only the organiser touches that one.

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

## How the agent learns the team and block

The participant's team and block come from `.git/raif-workshop-info` —
written by the bootstrap based on the participant's own choice, and read
by the agent through `tools/cowork-onboard.py` (lines `WORKSHOP_TEAM` /
`WORKSHOP_BLOCK`). Don't guess from the name. If the info file is missing
(bootstrap wasn't run) — ask the participant for team (A or B), block
(retail / cib / backend) and name, don't guess.

## Organisers (not team participants)

| Name | Role |
|---|---|
| Vitaly Erokhin | Workshop organiser, GitHub @ErokhinVi |
| Nerses Bagiyan | Co-organiser, CDO Total Bank |

If the user introduces themselves as one of them — that's not the
participant scenario: read `ORGANIZER.md`. Aliases: "Vitaly", "Erokhin",
"organiser", "host", "Nerses", "Bagiyan", "CDO" → organiser.

## Services and ports

| Block | Local | On Render |
|---|---|---|
| A · retail | `http://localhost:8001` | `https://raif-a-retail.onrender.com` |
| A · cib | `http://localhost:8002` | `https://raif-a-cib.onrender.com` |
| A · backend | `http://localhost:8003` | `https://raif-a-backend.onrender.com` |
| B · retail | `http://localhost:8011` | `https://raif-b-retail.onrender.com` |
| B · cib | `http://localhost:8012` | `https://raif-b-cib.onrender.com` |
| B · backend | `http://localhost:8013` | `https://raif-b-backend.onrender.com` |
| Leaderboard (simulator) | `http://localhost:8000` | `https://raif-simulator.onrender.com` |

Show the participant their team's retail block — that's the bank the
customer sees. The leaderboard shows the two teams' score head to head.
