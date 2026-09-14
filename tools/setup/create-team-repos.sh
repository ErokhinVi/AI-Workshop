#!/usr/bin/env bash
# tools/setup/create-team-repos.sh: завести репозитории команд из teams.conf.
#
# Использование:
#   tools/setup/create-team-repos.sh            # создать недостающие, снять архив с архивных
#   tools/setup/create-team-repos.sh --dry-run  # только показать план
#
# Репозитории публичные: симулятор читает CONTRACT.md команд через
# raw.githubusercontent.com без токена. Существующие репо не трогаются, кроме
# снятия архивации. Содержимое заливает sync-team-repos.sh.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

for entry in "${TEAMS[@]}"; do
  letter="${entry%%:*}"
  repo="${entry#*:}"
  full="${GH_OWNER}/${repo}"
  if archived="$(gh repo view "$full" --json isArchived --jq .isArchived 2>/dev/null)"; then
    if [ "$archived" = "true" ]; then
      echo "~ ${full}: в архиве, снимаю архив"
      [ "$DRY_RUN" = 1 ] || gh repo unarchive "$full" --yes
    else
      echo "= ${full}: уже есть"
    fi
  else
    echo "+ ${full}: создаю публичный репозиторий (team_${letter})"
    [ "$DRY_RUN" = 1 ] || gh repo create "$full" --public \
      --description "AI workshop: repository of team_${letter}"
  fi
done

[ "$DRY_RUN" = 1 ] && echo "(dry-run: ничего не менял)"
echo "Дальше: tools/setup/sync-team-repos.sh"
