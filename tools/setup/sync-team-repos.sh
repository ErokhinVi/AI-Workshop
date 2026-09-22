#!/usr/bin/env bash
# tools/setup/sync-team-repos.sh: залить team-template/ в репозитории команд.
#
# Использование:
#   tools/setup/sync-team-repos.sh            # все команды из teams.conf
#   tools/setup/sync-team-repos.sh c d        # только team_c и team_d
#   tools/setup/sync-team-repos.sh --dry-run  # собрать и показать, не пушить
#
# Для каждой команды:
#   1. клонирует репозиторий по HTTPS (авторизация через gh auth git-credential);
#   2. заменяет все, кроме .git, содержимым team-template/;
#   3. подставляет в TEAM.md букву команды и URL ее сервисов и табло: из
#      render-services.conf, а без него по схеме https://<prefix>-<буква>-<блок>.onrender.com,
#      и адреса через прокси workers.dev из proxy-urls.conf (tools/setup/cf_proxy.py);
#   4. коммитит поверх истории и пушит в main. История не переписывается.
#
# Повторный запуск возвращает main каждой команды к шаблону: это и есть сброс
# между прогонами. Во время воркшопа не запускать: откатит работу участников.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"
TEMPLATE="$ROOT/team-template"
SERVICES_CONF="${RENDER_SERVICES_CONF:-$ROOT/tools/setup/render-services.conf}"
PROXY_CONF="${PROXY_URLS_CONF:-$ROOT/tools/setup/proxy-urls.conf}"
GIT=(git -c credential.helper= -c 'credential.helper=!gh auth git-credential')

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
  shift
fi
ONLY=" $* "

AUTHOR_NAME="${COMMIT_NAME:-$(git config user.name || true)}"
AUTHOR_EMAIL="${COMMIT_EMAIL:-$(git config user.email || true)}"
if [ -z "$AUTHOR_NAME" ] || [ -z "$AUTHOR_EMAIL" ]; then
  echo "нет подписи коммитов: задай COMMIT_NAME и COMMIT_EMAIL (teams.conf или окружение)" >&2
  exit 1
fi

url_for() {  # url_for <буква|sim> <блок>
  local found=""
  if [ -f "$SERVICES_CONF" ]; then
    found="$(awk -v l="$1" -v b="$2" '$1 == l && $2 == b { print $4; exit }' "$SERVICES_CONF")"
  fi
  if [ -n "$found" ]; then
    echo "$found"
  elif [ "$1" = sim ]; then
    echo "https://${RENDER_PREFIX}-simulator.onrender.com"
  else
    echo "https://${RENDER_PREFIX}-$1-$2.onrender.com"
  fi
}

proxy_for() {  # proxy_for <буква|sim> <блок>: адрес через workers.dev (tools/setup/cf_proxy.py)
  local found=""
  if [ -f "$PROXY_CONF" ]; then
    found="$(awk -v l="$1" -v b="$2" '$1 == l && $2 == b { print $3; exit }' "$PROXY_CONF")"
  fi
  echo "${found:-not set up, ask the organiser}"
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

for entry in "${TEAMS[@]}"; do
  letter="${entry%%:*}"
  repo="${entry#*:}"
  if [ "$ONLY" != "  " ] && [[ "$ONLY" != *" $letter "* ]]; then
    continue
  fi
  url="https://github.com/${GH_OWNER}/${repo}.git"
  dest="$WORK/$repo"
  echo
  echo "=== team_${letter} ← ${url} ==="

  "${GIT[@]}" clone -q "$url" "$dest" 2>&1 | grep -v "empty repository" || true
  if [ ! -d "$dest/.git" ]; then
    echo "  ! не склонировался ${url}: репозиторий создан ? (tools/setup/create-team-repos.sh)" >&2
    exit 1
  fi
  cd "$dest"
  git symbolic-ref HEAD refs/heads/main

  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -R "$TEMPLATE/." .

  L="$letter" \
  UR="$(url_for "$letter" retail)" UC="$(url_for "$letter" cib)" UB="$(url_for "$letter" backend)" \
  US="$(url_for sim simulator)" \
  PR="$(proxy_for "$letter" retail)" PC="$(proxy_for "$letter" cib)" PB="$(proxy_for "$letter" backend)" \
  PS="$(proxy_for sim simulator)" \
    perl -pi -e 's/<TEAM_SLUG>/$ENV{L}/g; s/<URL_RETAIL>/$ENV{UR}/g; s/<URL_CIB>/$ENV{UC}/g;
                 s/<URL_BACKEND>/$ENV{UB}/g; s/<URL_SIMULATOR>/$ENV{US}/g;
                 s/<PROXY_RETAIL>/$ENV{PR}/g; s/<PROXY_CIB>/$ENV{PC}/g;
                 s/<PROXY_BACKEND>/$ENV{PB}/g; s/<PROXY_SIMULATOR>/$ENV{PS}/g' TEAM.md

  git add -A
  if git diff --cached --quiet; then
    echo "  = совпадает с шаблоном, пушить нечего"
  elif [ "$DRY_RUN" = 1 ]; then
    git diff --cached --stat | tail -1
    echo "  (dry-run: не пушу)"
  else
    git -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" \
      commit -q -m "Reset team repo to team-template (team_${letter})"
    "${GIT[@]}" push -q origin HEAD:main
    echo "  + main = $(git rev-parse --short HEAD)"
  fi
  cd "$ROOT"
done

echo
echo "Готово."
