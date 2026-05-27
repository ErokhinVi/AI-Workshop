# RULES.md — workshop rules

> Read once at the start of the day. All agents follow these rules.

## The headline

The workshop is four teams of three. Each team works in its own GitHub
repository. A team is three service blocks (`retail`, `cib`, `backend`),
one participant per block. All four teams solve the same task — in
parallel, independently of one another.

## The three team blocks

- **retail** — customer-facing mobile bank: UI and a thin layer.
- **cib** — corporate and business logic: product catalogue and decision logic.
- **backend** — data core: customers, transactions, balances, the base API.

Block links: retail → backend (data), retail → cib (decisions),
cib → backend (customer data). A feature is done only when all three blocks
have done their part and connected. Inside a team the three participants
agree among themselves out loud — how the blocks talk to each other via API.

## Repo zones

- Your block (`<own block>/`) — your territory, edit freely.
- The two other blocks of your team — visible **only** through their
  `CONTRACT.md`: the neighbour writes the endpoints they expose into that
  file. The neighbour's actual code (`src/`, `pyproject.toml`, `Dockerfile`)
  is denied — connect via the contract, not by peeking at internals.
- The other three teams live in **separate repositories** — they don't
  exist in your filesystem at all. This protects the competition: if the
  teams peek at each other's solutions, all banks become the same and the
  comparison loses its point.
- `seed/` — read-only by default; the backend block may write here if the
  task requires evolving the data model.
- `tasks/` — task briefs, read-only.
- `render.yaml`, `.github/` — don't touch.

Isolation is wired into `.claude/settings.local.json` (copied from the
template `settings-<block>.json` during onboarding). If the agent is asked
to climb into a sibling block, the system will refuse. That's correct.

## No link between the teams

There are no inter-team contracts. Teams are independent. Inside a team,
three people agree among themselves out loud.

## Customer simulator and the leaderboard

Customers are simulated against the bank. When a team ships a change, the
simulator (running in the organiser's orchestrator) snapshots the state of
all three of the team's blocks, scores them together against 10 criteria
and moves the customer base: customers arrive or leave — with a rationale.
The leaderboard shows all four teams' scores head to head.

## Shared branch

The team's three participants commit to the same shared branch of this
repository. There are no conflicts — every participant has their own
block. Before sending work up, the agent always pulls the fresh commits in
(`git pull --rebase --autostash`).

## With the user

The user is a non-technical board member. No jargon without a business
analogue. In chat — live prose without markdown formatting. English.
