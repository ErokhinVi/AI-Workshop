# TEAM.md — what each block does and how to talk between blocks

> Reference for agents. Used during onboarding to remind everyone what the
> three blocks own and where they live.

## The team

This GitHub repository is one team's territory. The team is three service
blocks (`retail`, `cib`, `backend`), one participant per block. Every
other team has its own separate repository and isn't visible from here.

There is no fixed roster of "who is in which block" here, on purpose.
Each participant picks the team, the block and types their name when
setting their laptop up — in `raif-workshop-setup.applescript` (macOS) or
`raif-workshop-setup.cmd` (Windows), one installer for every team. The
installer clones only the picked team's repo, so the team identity is
the repo the agent works in. The block choice is written into
`.git/raif-workshop-info` (`WORKSHOP_BLOCK`, `WORKSHOP_PARTICIPANT`),
where `tools/cowork-onboard.py` reads it when the agent starts. A
participant may switch blocks between stages: save the work, run the
installer again and pick the new block.

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

The workshop is run by moderators, one per team table. If something is
broken beyond what you can fix from this block (the bank doesn't rebuild,
the laptop can't reach the shared pile), ask the participant to call the
moderator at their table.

Organisers and moderators work in the separate orchestrator repository,
not in this team repo. If an organiser opens this repo by mistake, they
want technical mode: defer to the scenario in `CLAUDE.md` but skip the
"non-technical user" guard.

## Services and URLs

The exact Render URLs of this team's three services and the shared
leaderboard URL are filled in during workshop setup — see the section
below. If the placeholders are still in place, ask the organiser for the
final URLs and update this file.

| Block | Local | On Render |
|---|---|---|
| retail | `http://localhost:8001` | `<URL_RETAIL>` |
| cib | `http://localhost:8002` | `<URL_CIB>` |
| backend | `http://localhost:8003` | `<URL_BACKEND>` |
| Leaderboard (organiser's simulator) | — | `<URL_SIMULATOR>` |

Show the participant their team's retail block — that's the bank the
customer sees. The leaderboard shows every team's score head to head.
