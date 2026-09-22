#!/usr/bin/env bash
# tools/setup/ops-dispatch.sh: команды пульта Workshop ops внутри GitHub Actions.
#
# Вход из workflow .github/workflows/workshop-ops.yml: OPS_COMMAND, OPS_ARGS,
# OPS_CONFIRM и секреты репозитория в окружении. Руками не запускать: для
# терминала есть tools/setup/ops.sh, он запускает этот workflow.
#
# Команды render_ops.py (status, deploy, logs, sim-*, ...) плюс:
#   proxy, proxy-check  прокси workers.dev (tools/setup/cf_proxy.py)
#   team-reset          вернуть репозитории команд к шаблону, confirm RESET
#   team-access         deploy keys и доступ к Render в репозиториях команд
#   installer           собрать установщик и README в репозиторий ведущих
#   revoke              после воркшопа снять ключи команд, confirm DELETE

set -euo pipefail
set -f

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=teams.conf
source tools/setup/teams.conf

fail() {
  echo "::error::$*"
  echo "ошибка: $*" >&2
  exit 1
}

case "${OPS_ARGS:-}" in
  *[!a-z0-9:_\ -]*) fail "args: только латиница, цифры, двоеточие и пробел" ;;
esac
read -r -a ARGS <<<"${OPS_ARGS:-}"

need() {  # need <секрет>...: без него команда не работает
  local name
  for name in "$@"; do
    [ -n "${!name:-}" ] || fail "нет секрета $name: владелец ставит его tools/setup/github-access.sh ops-secrets"
  done
}

confirm() {
  [ "${OPS_CONFIRM:-}" = "$1" ] || fail "$OPS_COMMAND меняет репозитории команд: впиши $1 в поле confirm"
}

use_gh_token() {  # PAT владельца: репозитории команд и ведущих
  need WORKSHOP_GH_TOKEN
  export GH_TOKEN="$WORKSHOP_GH_TOKEN"
  export COMMIT_NAME="${COMMIT_NAME:-Workshop ops}"
  export COMMIT_EMAIL="${COMMIT_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
}

restore_keys() {  # ключи команд из секрета: для установщика и deploy keys
  need TEAM_DEPLOY_KEYS
  export WORKSHOP_SECRETS="${RUNNER_TEMP:-/tmp}/workshop-secrets"
  mkdir -p "$WORKSHOP_SECRETS"
  chmod 700 "$WORKSHOP_SECRETS"
  printf '%s' "$TEAM_DEPLOY_KEYS" | base64 -d | tar -xzf - -C "$WORKSHOP_SECRETS"
}

render_ops() {
  python3 tools/setup/render_ops.py "$@"
}

services() {  # render-services.conf: его читают остальные скрипты
  render_ops services
}

proxy_conf() {  # proxy-urls.conf: адреса workers.dev для TEAM.md и README ведущих
  if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    python3 tools/setup/cf_proxy.py conf
  else
    echo "нет CLOUDFLARE_API_TOKEN: адреса workers.dev не впишутся"
  fi
}

git_gh() {  # git с авторизацией через GH_TOKEN
  git -c credential.helper= -c 'credential.helper=!gh auth git-credential' "$@"
}

case "$OPS_COMMAND" in
  sim-*)
    render_ops sim "${OPS_COMMAND#sim-}" ;;
  teardown|drop)
    render_ops "$OPS_COMMAND" ${ARGS[@]+"${ARGS[@]}"} --confirm "${OPS_CONFIRM:-}" ;;
  status|deploy|logs|check|provision|env|services|plan|suspend|resume)
    render_ops "$OPS_COMMAND" ${ARGS[@]+"${ARGS[@]}"} ;;
  proxy)
    need CLOUDFLARE_API_TOKEN
    services
    python3 tools/setup/cf_proxy.py deploy
    python3 tools/setup/cf_proxy.py check ;;
  proxy-check)
    need CLOUDFLARE_API_TOKEN
    services
    python3 tools/setup/cf_proxy.py conf
    python3 tools/setup/cf_proxy.py check ;;
  team-reset)
    confirm RESET
    [ ${#ARGS[@]} -gt 0 ] || fail "team-reset: впиши в args номера команд (3 или 3 5) или all"
    use_gh_token
    services
    proxy_conf
    if [ "${ARGS[*]}" = all ]; then
      tools/setup/sync-team-repos.sh
    else
      tools/setup/sync-team-repos.sh "${ARGS[@]}"
    fi ;;
  team-access)
    use_gh_token
    restore_keys
    services
    tools/setup/github-access.sh keys
    tools/setup/github-access.sh render ;;
  installer)
    [ -n "${HOSTS_REPO:-}" ] || fail "в teams.conf не задан HOSTS_REPO"
    use_gh_token
    restore_keys
    services
    proxy_conf
    python3 tools/setup/make-bootstrap.py --keys-must-exist
    dest="${RUNNER_TEMP:-/tmp}/hosts-repo"
    rm -rf "$dest"
    git_gh clone -q "https://github.com/${GH_OWNER}/${HOSTS_REPO}.git" "$dest"
    python3 tools/setup/hosts_kit.py "$dest"
    git -C "$dest" add -A
    if git -C "$dest" diff --cached --quiet; then
      echo "в ${GH_OWNER}/${HOSTS_REPO} все свежее"
    else
      git -C "$dest" -c user.name="$COMMIT_NAME" -c user.email="$COMMIT_EMAIL" \
        commit -q -m "Установщик ноутбука и README ведущих"
      git_gh -C "$dest" push -q origin HEAD
      echo "обновил https://github.com/${GH_OWNER}/${HOSTS_REPO}"
    fi ;;
  revoke)
    confirm DELETE
    use_gh_token
    tools/setup/github-access.sh revoke ;;
  *)
    fail "не знаю команду $OPS_COMMAND" ;;
esac
