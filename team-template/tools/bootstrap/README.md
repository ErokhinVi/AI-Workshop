# tools/bootstrap — starter scripts for participants' laptops

These files configure a board member's laptop for the Raiffeisen AI
workshop in a couple of minutes: workshop SSH key, git identity, cloned
repo and `.git/raif-workshop-info` (read by `tools/cowork-onboard.py` on
the agent's first launch).

> The actual `.applescript` / `.cmd` scripts are produced by the organiser
> from the master scripts in the orchestrator repo and dropped into this
> folder before the workshop. They are not committed here as templates
> because they embed an SSH private key (rotated per workshop) and a
> bootstrap clone URL of this specific team repo.

## What the scripts do

| File | Platform | How it runs |
|---|---|---|
| `raif-workshop-setup.applescript` | macOS | Double-click → Script Editor → Run (Cmd+R) → pick block, type your name → Terminal opens automatically with the bootstrap script. |
| `raif-workshop-setup.cmd` | Windows 10/11 | Double-click → SmartScreen "More info → Run anyway" → pick block and type your name in the WinForms window → everything happens in one console window. |

There is no team picker in these scripts: the team identity is determined
by which team repo they were generated for. Each participant picks
**block** (`retail` / `cib` / `backend`) themselves and types their name.
The slug used for the git email and the participant id is derived from the
typed name (lowercased ASCII letters, digits and dashes); for non-ASCII
input the slug is roughly transliterated.

The script:

1. Drops the embedded SSH key into `~/.ssh/raif_workshop` with current-user-only permissions.
2. Appends a block to `~/.ssh/config` (marker `# raif-workshop-2026`) so GitHub uses this key and routes through port 443 (`HostName ssh.github.com`, `Port 443`) — the corporate network blocks plain SSH port 22, otherwise push/pull would hang on a timeout.
3. Sets `git config --global user.name` and `user.email` to the picked participant.
4. Calls `ssh -T git@github.com` and waits for `successfully authenticated`.
5. Clones or rebases this team's repository (clone URL is baked into the script).
6. Copies the key into `.git/raif-workshop-key` and writes `.git/raif-workshop-info` with `WORKSHOP_PARTICIPANT/BLOCK/GIT_NAME/GIT_EMAIL` — this is what Claude / Codex picks up in Cowork on the first message.
7. Installs block isolation for both agents: copies `.claude/templates/settings-<block>.json` → `.claude/settings.local.json` (Claude) and `.codex/templates/config-<block>.toml` → `.codex/config.toml` (Codex), and marks the repo folder as trusted in `~/.codex/config.toml`.

## How the scripts are generated (organiser-side)

The orchestrator repo ships a master pair of scripts and a small generator
that:

1. takes the workshop SSH **private** key (the one with deploy-write access
   to all four team repositories),
2. takes one team's clone URL (e.g. `git@github.com:erokhinvi/ai-workshop-team-a.git`),
3. produces a customised `.applescript` and `.cmd` for that team.

See the orchestrator repo's `SETUP.md` for the generator invocation.

## Tool dependencies on the participant's laptop

The script installs everything itself, with no admin rights and no
Artifactory — only public sources:

- **macOS**: if `git` is missing — calls `xcode-select --install`. If
  `node` is missing — downloads the Node 22 LTS tarball from nodejs.org
  into `~/.raif-workshop/tools/` and appends the PATH update to
  `~/.zshenv`.
- **Windows**: if `git`/`ssh` are missing — downloads the MinGit 2.54.0
  zip from github.com. If `node` is missing — Node 22 LTS zip from
  nodejs.org. If `python` is missing — Python 3.12.7 embeddable zip from
  python.org. Everything lands in `%LOCALAPPDATA%\raif-workshop\tools\`,
  User-PATH is updated via `[Environment]::SetEnvironmentVariable(...)`.

After running the bootstrap on Windows, **Claude Code App must be fully
restarted** (including the tray) for the new PATH to be picked up.

## How to distribute

- On Mac, AirDrop is most convenient. The participant catches the file in
  Downloads and double-clicks it.
- On Windows — corporate messenger / OneDrive / USB drive. Double-click
  from Downloads.

## After the workshop

Delete the deploy key on GitHub (in every team repo it was added to):

```
Repo → Settings → Deploy keys → "raif-workshop-2026" → Delete
```

Once that's done the key embedded in the scripts is useless — which is
the point.

## NOTE — security

These scripts embed a private SSH key. Distribute only via private
channels: AirDrop, direct message, USB hand-to-hand. Do not push them to
a public repo and do not post them in shared chats. The current repo
state should not contain the personalised scripts; only this README is
committed.
