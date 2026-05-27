#!/usr/bin/env bash
# tools/setup/sync-team-repos.sh — push team-template/ to each of the four
# team GitHub repositories.
#
# Usage:
#   tools/setup/sync-team-repos.sh URL_A URL_B URL_C URL_D
#
# Example:
#   tools/setup/sync-team-repos.sh \
#       git@github.com:erokhinvi/ai-workshop-team-a.git \
#       git@github.com:erokhinvi/ai-workshop-team-b.git \
#       git@github.com:erokhinvi/ai-workshop-team-c.git \
#       git@github.com:erokhinvi/ai-workshop-team-d.git
#
# What it does:
#   For each URL in turn —
#     1. Clone the (presumably empty) team repo into a temp dir.
#     2. Mirror team-template/ contents into the working tree
#        (overwrites everything except .git).
#     3. Commit and push to main.
#
# Re-running this overwrites every team repo's main with team-template/. Use
# only for the initial population OR for a full reset of all teams. For
# incremental syncs, edit team-template/ and use a more surgical update.

set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 URL_A URL_B URL_C URL_D" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$ROOT/team-template"
if [ ! -d "$TEMPLATE" ]; then
  echo "team-template/ not found at $TEMPLATE" >&2
  exit 1
fi

LABELS=("team_a" "team_b" "team_c" "team_d")
URLS=("$@")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

for i in 0 1 2 3; do
  LABEL="${LABELS[$i]}"
  URL="${URLS[$i]}"
  echo
  echo "=== ${LABEL}  ←  ${URL} ==="

  DEST="$WORK/$LABEL"
  if ! git clone "$URL" "$DEST" 2>&1 | tail -5; then
    echo "  ! could not clone $URL — does the repo exist on GitHub?" >&2
    exit 1
  fi

  pushd "$DEST" >/dev/null

  # Wipe everything except .git
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

  # Copy template content
  cp -R "$TEMPLATE/." .

  # If the team repo is brand-new, the default branch may not be main yet.
  # Force the new branch to be 'main'.
  git symbolic-ref HEAD refs/heads/main 2>/dev/null || true

  git add -A
  if git diff --cached --quiet; then
    echo "  + no changes"
  else
    git commit -m "Initial team repo content from team-template/" >/dev/null
    git push -u origin main
    echo "  + pushed to main"
  fi

  popd >/dev/null
done

echo
echo "Done. Now run tools/setup/add-submodules.sh to wire them into this orchestrator."
