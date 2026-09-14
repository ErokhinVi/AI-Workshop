#!/usr/bin/env bash
# tools/setup/ops.sh: выполнить команду render_ops.py в GitHub Actions и дождаться.
#
# Для компьютеров, откуда api.render.com и *.onrender.com недоступны
# (корпоративная сеть). Запускает workflow Workshop ops в оркестраторе, ждет
# конца, печатает лог и скачивает render-services.conf, если run его выложил.
#
# Использование: tools/setup/ops.sh <команда> [аргументы]
#   tools/setup/ops.sh status
#   tools/setup/ops.sh provision
#   tools/setup/ops.sh deploy a:cib sim
#   tools/setup/ops.sh sim start        (или sim-start)
#   tools/setup/ops.sh teardown DELETE
# Нужны секреты оркестратора: tools/setup/github-access.sh ops-secrets.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"
REPO="$GH_OWNER/$ORCHESTRATOR_REPO"
WORKFLOW="workshop-ops.yml"

if [ $# -lt 1 ]; then
  sed -n '2,15p' "$0"
  exit 2
fi
command="$1"
shift
if [ "$command" = sim ]; then
  command="sim-${1:?укажи state, start, stop, reset или evaluate}"
  shift
fi
confirm=""
if [ "$command" = teardown ]; then
  confirm="${1:-}"
  [ $# -gt 0 ] && shift
fi
args="$*"

latest_run() {
  gh run list -R "$REPO" -w "$WORKFLOW" -L 1 --json databaseId --jq '.[0].databaseId // 0'
}

before="$(latest_run)"
gh workflow run "$WORKFLOW" -R "$REPO" -f command="$command" -f args="$args" -f confirm="$confirm"
echo "запустил $command в $REPO, жду run"
run="$before"
for _ in $(seq 1 45); do
  sleep 2
  run="$(latest_run)"
  if [ "$run" != "$before" ] && [ "$run" != 0 ]; then
    break
  fi
done
if [ "$run" = "$before" ]; then
  echo "run не появился за 90 секунд, смотри https://github.com/$REPO/actions" >&2
  exit 1
fi
echo "https://github.com/$REPO/actions/runs/$run"

status=0
gh run watch "$run" -R "$REPO" --interval 5 --exit-status >/dev/null 2>&1 || status=$?

# Лог только шага render_ops.py, без префиксов job/step и времени.
gh run view "$run" -R "$REPO" --log 2>/dev/null \
  | awk -F '\t' '$2 ~ /render_ops/ { line = $3; sub(/^[0-9T:.-]+Z ?/, "", line); print line }' || true

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if gh run download "$run" -R "$REPO" -n render-services -D "$tmp" >/dev/null 2>&1 \
   && [ -f "$tmp/render-services.conf" ]; then
  mv "$tmp/render-services.conf" "$ROOT/tools/setup/render-services.conf"
  echo "обновил tools/setup/render-services.conf"
fi

if [ "$status" -ne 0 ]; then
  echo "run завершился с ошибкой: https://github.com/$REPO/actions/runs/$run" >&2
fi
exit "$status"
