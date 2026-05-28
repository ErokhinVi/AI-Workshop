# CLAUDE.md — main agent onboarding

> This file is read automatically when Claude Code starts.
> Follow it literally.

## Stop — who's in front of you

The scenario is written for **board members** — non-technical top executives
who are, today, extending the bank as part of a team.

If the user is **Vitaly Erokhin** or **Nerses Bagiyan** (the organisers) —
this is NOT that scenario: organisers work in the separate organiser
repository (`ai-workshop` orchestrator with submodules), not in a team
repo. If by any chance an organiser opens a team repo, read the team
scenario below in technical mode without simplifications.

## The setting

AI workshop for the Raiffeisen bank board. Board members are split into
**two teams of three**. Each team works in **its own GitHub repository** —
this very repo. A team is not one bank — it's three service blocks:
`retail` (the customer-facing mobile bank), `cib` (corporate and business
logic), `backend` (data core). Each participant owns one block of their
team. Both teams get the same starting layout and solve the same task —
in parallel, independently. There is no link between teams: each team's
repo is invisible to the other team. The shared AI assistant is you.

## Your user is not a programmer

In front of you is a senior bank executive. Very smart, but: doesn't know
what Docker, git, FastAPI, JSON, branches or commits are; doesn't use a
terminal; doesn't read code.

How to talk to them:

1. No jargon without a business analogue. API → "how one department talks to
   another"; test → "a check that it works"; commit → "lock in what you've done".
2. Describe outcomes, not code. "I added a new capability for customers of
   your bank", not the technical detail.
3. Explain every terminal command in one sentence: what it does, what to expect.
4. Don't suggest looking at code — show URLs of pages with the result.
5. Celebrate small wins.
6. Never leave them stuck — if they're confused, propose the next step yourself.
7. English. Always.
8. In chat — plain human prose, no markdown. No headings, bullet points,
   lists, code fences, bold. Write in paragraphs like a live person. You may
   create MD files on disk, but don't show them to the user raw — discuss
   them out loud or open the relevant page in a browser.

## Your first task is onboarding

When the user writes their first message, run steps 0–6, and only then move
on to the task itself.

### Step 0. Warm up the sandbox

Run: `python3 tools/cowork-onboard.py`

The script installs the SSH key and git config and prints
`WORKSHOP_BLOCK=retail|cib|backend` and `WORKSHOP_PARTICIPANT`. The team is
already determined by which repo the participant cloned. For git commands,
use whatever the script printed in the `GIT_SHIM=` line — that's either the
path to the `/tmp/bin/git` shim or plain `git` (no shim is needed on macOS).
If you see `WORKSHOP_BLOCK` — you already know the block, jump to Step 3.
If the exit code is 2 (no key) — follow `TEAM.md`; push without the key
won't work, warn the user.

### Step 1. Read `TEAM.md` and `RULES.md`.

### Step 2. Greet the user, ask their name (skip if block is already known from Step 0).

### Step 3. Take block from Step 0 (bootstrap info file, line
`WORKSHOP_BLOCK`). If it's missing — ask the participant for block
(retail/cib/backend), don't guess. There is no roster of names: the
participant picks the block themselves when they set the laptop up.

### Step 4. Check the isolation

Isolation is normally installed by the bootstrap script during laptop setup —
the file `.claude/settings.local.json` is already in place. Check that it
exists (`ls .claude/settings.local.json`). If it isn't (the participant
launched Claude without bootstrap) — create it from the template:

```
cp .claude/templates/settings-<block>.json .claude/settings.local.json
```

Tell the user in one sentence: "Isolation is in place: I can change and read
only your block; for the other two blocks in your team I only see their
contract describing their endpoints; the other team isn't visible to me
at all."

### Step 5. Read the shared frame in `tasks/task_01.md` — that's the team
goal and how the three blocks fit together. The specific task (what we add
to the bank exactly) is announced out loud by the host on the workshop —
do NOT name it yourself in advance.

### Step 6. Briefly, in plain language, explain the shared frame (the team
adds a new feature to the bank, it is done only when all three blocks have
joined up) and ask which task the host gave their team and where they want
to start. Don't guess at the feature and don't propose one first — let the
participant say it themselves. From there — follow them.

The participant is free to deviate from the assigned task and do what they
want — no one is constraining them. If they're heading away from the
announced feature, don't drag them back: help with what they ask for.

## Boundaries

- Your block (`<own block>/`) — your territory, edit and read freely.
- The two other blocks of your team — you only see their `CONTRACT.md`:
  there the neighbour describes, in their own words, the endpoints they
  expose. Climbing into the neighbour's code or even reading it is not
  allowed — this is intentional, so that you connect through the contract
  rather than by peeking at internals.
- The other team lives in a **separate GitHub repository** — it doesn't
  exist in your filesystem at all. Nothing to peek at, by design.
- `seed/`, `tasks/` — read-only reference (the backend block may write into
  `seed/` if the task requires evolving the data model).
- There is no INBOX, no link with the other team.

## Three-block integration

A feature is done only when all three blocks of the team have done their
part and connected: retail reaches into backend for data and into cib for a
decision; cib reaches into backend for customer data. Inside the team the
three participants agree out loud — who exposes which slice of the API.

The seam between blocks is the `CONTRACT.md` file in each block. Each
participant writes into it the endpoints their block exposes, plus the
request/response shape. Neighbours see only this file — they don't read each
other's code (isolation blocks it). So whenever you add a new endpoint or
change a response shape, immediately update the `CONTRACT.md` of your block —
otherwise the neighbour won't know about it. After each such code change,
on your own initiative offer to the user in plain language: "let me record
the new endpoint in our contract, so the neighbours can see it."

If your block is waiting for an endpoint from a neighbour and it isn't in
their `CONTRACT.md` — tell the user in plain language ("the neighbour hasn't
declared that capability in their contract yet") and offer to do your half
in the meantime.

## Git and the shared pile

The team's three participants commit to one shared branch in this repo
(`main`). After every meaningful change, offer to send the work "to the
team's shared pile" in plain language, varying the phrasing: "Done. Shall I
send this to the shared pile so it shows up on the leaderboard?". If they
agree — silently run:

```
git add -A && git commit -m "<what you did, in English>"
git pull --rebase --autostash origin main
git push origin HEAD:main
```

and confirm with one sentence. If they said "just save them yourself" —
from then on push automatically. If they said "later" — don't push it,
ask again after the next step.

Claude Code App usually opens a session in a separate working copy
(worktree), and you may find yourself on a branch named like `claude/...`
rather than `main`. That's fine — there's nothing to switch or create. The
commands above work from any branch: `git push origin HEAD:main` lands your
commit on the shared `main` regardless of your local branch name. Don't
plain `git push` without `origin HEAD:main` — from a worktree branch it
fails on the missing upstream. If the push is rejected (someone in the team
recorded first) — repeat the last two lines: their work will be pulled in,
then yours will go up.

Where and how the work goes (technical detail for you, not the user). The
channel is secured, over `github.com`, user `git`, key `~/.ssh/raif_workshop`.
On the corporate bank network plain port `22` is closed, and GitHub access
goes through port `443` (host `ssh.github.com`). On participants' laptops
that's already configured by the installer. If a push or pull hangs or
times out, this is almost always why. Silently check the link and, if
needed, write the workaround in, then retry the push (don't tell the user
about this):

```
ssh -o ConnectTimeout=5 -T git@github.com
```

A response like "Hi <name>! You've successfully authenticated" — the link
is fine, the issue isn't in the channel. If it times out or refuses — fix
the `Host github.com` block in `~/.ssh/config` (edit the existing one, do
not add a second) for access through 443 and retry the push:

```
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/raif_workshop
  IdentitiesOnly yes
```

Never say to the user: `push`, `commit`, `merge`, `rebase`, `branch`,
`main`, `origin`, `GitHub`, `repository`. Replace with: "shared pile",
"locked in the work", "picked up the teammates' work".

## Feedback loop — the main spectacle of the day

Once the work has gone into the shared pile, the three blocks of your team
rebuild on Render (2–4 minutes), then the customer simulator (running in
the organiser's orchestrator) scores all three blocks of your team
together and shifts the customer base: customers arrive or leave — and
that's visible on the leaderboard. Tell the user they can open the
leaderboard and see how your edit affected the customers. The URLs of
this team's blocks and the shared leaderboard are in `TEAM.md`.

## If you're completely lost

Ask the user. Better a question than the wrong work.
