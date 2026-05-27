#!/usr/bin/env bash
# tools/setup/add-submodules.sh — wire the four team repos into this
# orchestrator as submodules at team_a/, team_b/, team_c/, team_d/.
#
# Usage:
#   tools/setup/add-submodules.sh URL_A URL_B URL_C URL_D
#
# Idempotent: if a submodule already points at the same URL, it is left
# alone; otherwise it is removed and re-added.

set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "usage: $0 URL_A URL_B URL_C URL_D" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

LABELS=("team_a" "team_b" "team_c" "team_d")
URLS=("$@")

for i in 0 1 2 3; do
  LABEL="${LABELS[$i]}"
  URL="${URLS[$i]}"
  echo "=== $LABEL  ←  $URL ==="

  if [ -e "$LABEL" ] || git config -f .gitmodules --get "submodule.$LABEL.url" >/dev/null 2>&1; then
    # Already present in some form. If URL differs, deinit and remove.
    CURRENT_URL="$(git config -f .gitmodules --get "submodule.$LABEL.url" 2>/dev/null || true)"
    if [ "$CURRENT_URL" = "$URL" ]; then
      echo "  + already wired with the same URL — skipping"
      continue
    fi
    echo "  ~ removing existing $LABEL (was: ${CURRENT_URL:-not in .gitmodules})"
    git submodule deinit -f "$LABEL" 2>/dev/null || true
    git rm -rf "$LABEL" 2>/dev/null || rm -rf "$LABEL"
    rm -rf ".git/modules/$LABEL"
  fi

  git submodule add -b main "$URL" "$LABEL"
done

# Refresh .gitmodules formatting and stage
git add .gitmodules team_a team_b team_c team_d

echo
echo "Submodules added. Review with 'git status' and commit when satisfied:"
echo "  git commit -m 'wire four team submodules'"
echo "  git push origin HEAD:main"
