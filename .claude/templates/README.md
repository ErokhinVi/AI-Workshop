# .claude/templates/

Templates of `settings.local.json` for every (team, block) pair. Names:
`settings-team_<a|b>-<retail|cib|backend>.json`.

## Why

This is **hard isolation** — Claude Code physically cannot edit files
outside your block. The error comes from the built-in permissions engine,
not my instructions, so it cannot be "talked into" bypassing it.

## When to apply it

The template is installed by the laptop bootstrap script
(`tools/bootstrap/raif-workshop-setup-board.{applescript,cmd}`)
into `.claude/settings.local.json`. If that somehow did not run, the agent
copies the right template itself during onboarding.

Manually (just in case):

```bash
# for team A retail
cp .claude/templates/settings-team_a-retail.json .claude/settings.local.json
```

After the swap — restart Claude Code App, otherwise it won't re-read the
permissions.

## What's inside each template

Allowed for the participant:
- edit and read **only their own block** (`team_<X>/<own block>/**`);
- read the `CONTRACT.md` of the two other blocks in their team — the
  neighbour's storefront listing their endpoints;
- read `tasks/` and `seed/`;
- run `docker`, `docker-compose`, `git`, `python`, `curl`, `ls`,
  `cat`, `grep`, `find`.

Denied:
- edit or read `src/`, `pyproject.toml`, `Dockerfile` of neighbouring
  blocks of the team — the seam goes **through `CONTRACT.md` only**;
- any contact with the other team (`team_<other>/**` — even read);
- edit `simulator/`, `seed/`, `render.yaml`, `.github/`.

## Why `CONTRACT.md` specifically

Earlier the participant could read all of their team's code — handy for
the agent (you can see how a neighbour built an endpoint) but it blurs the
ownership boundary and is pedagogically weaker. Now every block publishes
its own storefront in `team_<X>/<block>/CONTRACT.md`, and neighbours read
only that. The result:

- the retail block agent physically cannot "peek" into cib's code — only
  into `team_<X>/cib/CONTRACT.md`;
- if CIB added an endpoint and didn't update its CONTRACT.md, retail
  doesn't learn about it — the right pressure toward a careful contract.

If a neighbour's `CONTRACT.md` is missing or empty — that's a signal the
neighbour hasn't fixed their API yet. Agree out loud and write it in.

## What the templates do NOT close (known)

**Bash bypass is scoped to the own block**: `Bash(cat:team_<X>/<block>/**)`,
`Bash(grep:team_<X>/<block>/**)`, `Bash(find:team_<X>/<block>/**)`. Via
these the agent cannot read either the neighbour's code or the neighbour's
CONTRACT.md (for the neighbour's CONTRACT.md there is a separate Read-allow
and the Read tool). `Bash(ls:*)` is left wide — folder structure is
visible, content is not. If something breaks the workflow (say, the agent
needs `cat tasks/...`), the cleanest expansion is via the Read tool, not
Bash.

**Files of a neighbour outside `src/`**: the read-deny on neighbouring
blocks is currently enumerated explicitly — `Read(team_a/cib/src/**)`,
`Read(team_a/cib/pyproject.toml)`, `Read(team_a/cib/Dockerfile)`. If
someone drops a file into `team_a/cib/` that isn't covered (e.g.
`notes.md` or `config.yaml`), it falls into ask mode — Claude will ask
the user for permission to read, and a non-technical user will probably
agree. If a new file type lands next to `src/`, add its name or extension
to deny.

**Empirical check of the CONTRACT.md model**: the "folder-deny + targeted
file-allow on CONTRACT.md" scheme relies on the rule "deny matches → block,
allow matches → pass, otherwise ask". That's the standard Claude Code
behaviour, but it's worth verifying in this new combination of rules. Test
after each template swap: in a fresh session under the participant profile
(`cp templates/... settings.local.json` + restart the app) try to read
`team_<own>/<neighbour>/CONTRACT.md` (should open) and
`team_<own>/<neighbour>/src/main.py` (should deny). If CONTRACT.md is
denied too — move to a different scheme (for example, keep the storefronts
in a separate folder `team_<X>/contracts/`, where permissions are simpler).
