#!/usr/bin/env bash
# tools/setup/add-submodules.sh: подключить репозитории команд из
# tools/setup/teams.conf сабмодулями team_<буква>/ в организаторском репо.
#
# Использование:
#   tools/setup/add-submodules.sh
#
# Идемпотентен: сабмодуль с тем же URL не трогает, с другим URL переподключает.
# Запускать ПОСЛЕ sync-team-repos.sh: пустой репозиторий git submodule add не
# подключит.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"
cd "$ROOT"

labels=()
for entry in "${TEAMS[@]}"; do
  letter="${entry%%:*}"
  repo="${entry#*:}"
  label="team_${letter}"
  url="https://github.com/${GH_OWNER}/${repo}.git"
  labels+=("$label")
  echo "=== ${label} ← ${url} ==="

  current="$(git config -f .gitmodules --get "submodule.${label}.url" 2>/dev/null || true)"
  if [ "$current" = "$url" ]; then
    echo "  = уже подключен с этим URL"
    continue
  fi
  if [ -n "$current" ] || [ -e "$label" ]; then
    echo "  ~ был ${current:-не в .gitmodules}, переподключаю"
    git submodule deinit -f "$label" 2>/dev/null || true
    git rm -rf --cached "$label" >/dev/null 2>&1 || true
    rm -rf "$label" ".git/modules/$label"
    git config -f .gitmodules --remove-section "submodule.${label}" 2>/dev/null || true
  fi
  git submodule add -b main "$url" "$label"
done

git add .gitmodules "${labels[@]}"
echo
echo "Сабмодули подключены. Проверь git status и закоммить:"
echo "  git commit -m 'wire team submodules'"
