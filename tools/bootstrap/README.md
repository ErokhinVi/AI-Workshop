# tools/bootstrap — starter scripts for participants' laptops

These files are handed to board members before the workshop so their laptops
are configured in a couple of minutes: workshop SSH key, git identity, cloned
repo and `.git/raif-workshop-info` (read by `tools/cowork-onboard.py` on the
agent's first launch). The agent can be Claude Code (reads `CLAUDE.md`) or
Codex (reads `AGENTS.md`) — block isolation is installed for both.

## What's inside

| File | Platform | How it runs |
|---|---|---|
| `raif-workshop-setup.applescript` | macOS | Double-click → Script Editor → Run (Cmd+R) → pick team and block, type your name → Terminal opens automatically with the bootstrap script. |
| `raif-workshop-setup.cmd` | Windows 10/11 | Double-click → SmartScreen "More info → Run anyway" → pick team, block and type your name in the WinForms window → everything happens in one console window. |
| `raif-workshop-setup-board.{applescript,cmd}` | macOS / Windows | Byte-identical copies of the files above, distributed only to board members through the private channel that ships the deploy SSH key. |

There is no hard-coded roster anywhere in these scripts: each participant
picks team (`team_a` or `team_b`) and block (`retail` / `cib` / `backend`)
themselves and types their name. The slug used for the git email and the
participant id is derived from the typed name (lowercased ASCII letters,
digits and dashes); for non-ASCII input the slug is roughly transliterated.

The "Workshop host" checkbox (Windows) and choosing the host option in the
AppleScript skip block isolation entirely — used by the workshop organiser.

The script:

1. Drops the embedded SSH key into `~/.ssh/raif_workshop` with current-user-only permissions.
2. Appends a block to `~/.ssh/config` (marker `# raif-workshop-2026`) so GitHub uses this key and routes through port 443 (`HostName ssh.github.com`, `Port 443`) — the corporate network blocks plain SSH port 22, otherwise push/pull would hang on a timeout.
3. Sets `git config --global user.name` and `user.email` to the picked participant.
4. Calls `ssh -T git@github.com` and waits for `successfully authenticated`.
5. Clones or rebases `~/AI-Workshop` (or `%USERPROFILE%\AI-Workshop`).
6. Copies the key into `.git/raif-workshop-key` and writes `.git/raif-workshop-info` with `WORKSHOP_PARTICIPANT/TEAM/BLOCK/GIT_NAME/GIT_EMAIL` — this is what Claude picks up in Cowork on the first message.
7. Installs block isolation for both agents: copies `.claude/templates/settings-<team>-<block>.json` → `.claude/settings.local.json` (Claude) and `.codex/templates/config-<team>-<block>.toml` → `.codex/config.toml` (Codex), and also marks the repo folder as trusted in `~/.codex/config.toml` — otherwise Codex won't load the project config. Host mode (`host`) skips isolation.

## Tool dependencies on the participant's laptop

The script installs everything itself, with no admin rights and no
Artifactory — only public sources:

- **macOS**: if `git` is missing — calls `xcode-select --install` (Apple's GUI popup). If `node` is missing — downloads the Node 22 LTS tarball from nodejs.org into `~/.raif-workshop/tools/` and appends the PATH update to `~/.zshenv`.
- **Windows**: if `git`/`ssh` are missing — downloads the MinGit 2.54.0 zip from github.com. If `node` is missing — Node 22 LTS zip from nodejs.org. If `python` is missing — Python 3.12.7 embeddable zip from python.org (plus a copy `python.exe` → `python3.exe`). Everything lands in `%LOCALAPPDATA%\raif-workshop\tools\`, User-PATH is updated via `[Environment]::SetEnvironmentVariable(...)`.

After running the bootstrap on Windows, **Claude Code App must be fully
restarted** (including the tray) for the new PATH to be picked up.

## How to distribute

- On Mac, AirDrop is most convenient. The participant catches the file in Downloads and double-clicks it.
- On Windows — corporate messenger / OneDrive / USB drive. Double-click from Downloads.

## After the workshop

Delete the deploy key on GitHub:

```
Repo → Settings → Deploy keys → "raif-workshop-2026" → Delete
```

Once that's done the keys embedded in the scripts are useless — which is the point.

## If the script fails

`tools/cowork-onboard.py` can also work without the bootstrap files (the
older flow via the name in `TEAM.md`). So in the worst case the participant
will still be able to work — just without their commits being signed with
their own name and without pushing to the shared GitHub.

## NOTE — security

These scripts embed a private SSH key (in the `WORKSHOP_PRIVATE_KEY_EOF` heredoc
on macOS, in `$PrivateKeyB64` on Windows). Distribute only via private
channels: AirDrop, direct message, USB hand-to-hand. Do not push them to a
public repo and do not post them in shared chats. The current repo copy
includes a key only for the duration of the workshop and is deleted right
after — see "After the workshop" above.
