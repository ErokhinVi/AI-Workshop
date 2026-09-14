#!/usr/bin/env bash
# tools/setup/github-access.sh: доступы и настройки GitHub для воркшопа.
#
# Использование:
#   tools/setup/github-access.sh keys        [--dry-run]
#       deploy key каждой команды (право записи) в ее репозиторий. Публичные
#       ключи берутся из <секреты>/keys/team_<буква>.pub, их создает make-bootstrap.py.
#   tools/setup/github-access.sh render      [--dry-run]
#       репозитории команд: секрет RENDER_API_KEY и переменные RENDER_SID_BACKEND,
#       RENDER_SID_CIB, RENDER_SID_RETAIL из tools/setup/render-services.conf.
#       Без них workflow деплоя в репозитории команды ничего не деплоит.
#   tools/setup/github-access.sh ops-secrets [--dry-run]
#       оркестратор: секреты RENDER_API_KEY, OPENAI_API_KEY, ADMIN_TOKEN
#       (и RENDER_OWNER_ID, если задан) для workflow Workshop ops.
#   tools/setup/github-access.sh revoke      [--dry-run]
#       после воркшопа: снять deploy keys воркшопа и RENDER_API_KEY у команд.
#
# Секреты: из окружения, иначе из <секреты>/workshop.env, где <секреты> это
# $WORKSHOP_SECRETS или ~/AI-Workshop-secrets/<WORKSHOP_ID>. Значения не печатаются.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"

COMMAND="${1:-}"
DRY_RUN=0
if [ "${2:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi
SECRETS_DIR="${WORKSHOP_SECRETS:-$HOME/AI-Workshop-secrets/$WORKSHOP_ID}"
ENV_FILE="$SECRETS_DIR/workshop.env"
SERVICES_CONF="${RENDER_SERVICES_CONF:-$ROOT/tools/setup/render-services.conf}"
KEY_TITLE="workshop-${WORKSHOP_ID}"

die() {
  echo "$*" >&2
  exit 1
}

secret() {  # secret <ИМЯ>: значение из окружения, иначе из workshop.env
  local value=""
  eval "value=\${$1:-}"
  if [ -z "$value" ] && [ -f "$ENV_FILE" ]; then
    value="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -1)"
    value="${value%\"}"
    value="${value#\"}"
  fi
  printf '%s' "$value"
}

set_secret() {  # set_secret <owner/repo> <ИМЯ> <значение>
  if [ "$DRY_RUN" = 1 ]; then
    echo "  (dry-run) секрет $2 → $1"
    return
  fi
  printf '%s' "$3" | gh secret set "$2" -R "$1" >/dev/null
  echo "  секрет $2 → $1"
}

set_variable() {  # set_variable <owner/repo> <ИМЯ> <значение>
  if [ "$DRY_RUN" = 1 ]; then
    echo "  (dry-run) переменная $2=$3 → $1"
    return
  fi
  gh variable set "$2" -R "$1" --body "$3" >/dev/null
  echo "  переменная $2=$3 → $1"
}

case "$COMMAND" in
  keys|render|revoke)
    if [ "$COMMAND" = render ]; then
      RENDER_KEY="$(secret RENDER_API_KEY)"
      [ -n "$RENDER_KEY" ] || die "нет RENDER_API_KEY: заполни $ENV_FILE или задай в окружении"
      [ -f "$SERVICES_CONF" ] || die "нет $SERVICES_CONF: python3 tools/setup/render_ops.py services или артефакт render-services из workflow Workshop ops"
    fi
    for entry in "${TEAMS[@]}"; do
      letter="${entry%%:*}"
      full="${GH_OWNER}/${entry#*:}"
      case "$COMMAND" in
        keys)
          pub="${SECRETS_DIR}/keys/team_${letter}.pub"
          [ -f "$pub" ] || die "нет ${pub}: сначала python3 tools/setup/make-bootstrap.py"
          key_body="$(awk '{print $1" "$2}' "$pub")"
          if ! existing="$(gh api "repos/${full}/keys" --jq '.[].key' 2>/dev/null)"; then
            [ "$DRY_RUN" = 1 ] || die "нет доступа к ${full}: репозиторий создан ? (tools/setup/create-team-repos.sh)"
            echo "! ${full}: репозитория нет или нет доступа"
            continue
          fi
          case "$existing" in
            *"$key_body"*)
              echo "= ${full}: deploy key team_${letter} уже есть"
              continue ;;
          esac
          echo "+ ${full}: deploy key team_${letter} (write)"
          [ "$DRY_RUN" = 1 ] || gh api "repos/${full}/keys" -f title="$KEY_TITLE" \
            -f key="$key_body" -F read_only=false --jq '.id' >/dev/null
          ;;
        render)
          echo "~ ${full}"
          for block in backend cib retail; do
            sid="$(awk -v l="$letter" -v b="$block" '$1 == l && $2 == b { print $3; exit }' "$SERVICES_CONF")"
            [ -n "$sid" ] || die "в $SERVICES_CONF нет сервиса $letter $block: сначала render_ops.py provision"
            set_variable "$full" "RENDER_SID_$(printf '%s' "$block" | tr '[:lower:]' '[:upper:]')" "$sid"
          done
          set_secret "$full" RENDER_API_KEY "$RENDER_KEY"
          ;;
        revoke)
          ids="$(gh api "repos/${full}/keys" --jq ".[] | select(.title == \"${KEY_TITLE}\") | .id")"
          for id in $ids; do
            echo "- ${full}: deploy key ${id}"
            [ "$DRY_RUN" = 1 ] || gh api -X DELETE "repos/${full}/keys/${id}" >/dev/null
          done
          secrets_list="$(gh secret list -R "$full")"
          case "$secrets_list" in
            RENDER_API_KEY*|*"
RENDER_API_KEY"*)
              echo "- ${full}: секрет RENDER_API_KEY"
              [ "$DRY_RUN" = 1 ] || gh secret delete RENDER_API_KEY -R "$full" ;;
          esac
          ;;
      esac
    done
    ;;
  ops-secrets)
    repo="${GH_OWNER}/${ORCHESTRATOR_REPO}"
    for name in RENDER_API_KEY OPENAI_API_KEY ADMIN_TOKEN RENDER_OWNER_ID; do
      value="$(secret "$name")"
      if [ -z "$value" ]; then
        [ "$name" = RENDER_OWNER_ID ] && continue
        die "нет $name: заполни $ENV_FILE или задай в окружении"
      fi
      set_secret "$repo" "$name" "$value"
    done
    ;;
  *)
    sed -n '2,20p' "$0"
    exit 2
    ;;
esac

if [ "$DRY_RUN" = 1 ]; then
  echo "(dry-run: ничего не менял)"
fi
