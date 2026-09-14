#!/usr/bin/env bash
# tools/setup/doctor.sh: готов ли этот компьютер и аккаунты к сетапу воркшопа.
#
# Ничего не меняет. Проверяет инструменты, вход в gh и его права, репозитории
# из teams.conf, файл секретов и сеть. В конце говорит, как запускать команды
# Render: локально или через GitHub Actions (из корпоративной сети Render закрыт).
#
# Использование: tools/setup/doctor.sh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=teams.conf
source "$ROOT/tools/setup/teams.conf"
SECRETS_DIR="${WORKSHOP_SECRETS:-$HOME/AI-Workshop-secrets/$WORKSHOP_ID}"
FAILS=0
RENDER_LOCAL=0

ok()   { printf '  ok    %s\n' "$1"; }
warn() { printf '  warn  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
http_code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$1" 2>/dev/null || true; }

echo "Инструменты"
for tool in git gh python3 ssh-keygen perl curl awk; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; else bad "$tool не установлен"; fi
done
if python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  ok "python3 3.9+"
else
  bad "нужен python3 3.9 или новее"
fi
if python3 "$ROOT/tools/setup/render_ops.py" --help >/dev/null 2>&1 && \
   python3 -c "import sys; sys.path.insert(0, '$ROOT/tools/setup'); import workshop_conf; workshop_conf.load_conf()" 2>/dev/null; then
  ok "teams.conf читается: GH_OWNER=$GH_OWNER, команд ${#TEAMS[@]}, префикс $RENDER_PREFIX"
else
  bad "teams.conf не читается: python3 tools/setup/render_ops.py check покажет ошибку"
fi

echo "GitHub"
if ! gh auth status >/dev/null 2>&1; then
  bad "gh не залогинен: gh auth login -s repo,workflow"
else
  login="$(gh api user --jq .login 2>/dev/null)"
  ok "gh: $login"
  scopes="$(gh api -i user 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1) == "x-oauth-scopes" { print $2 }')"
  if [ -z "$scopes" ]; then
    warn "не вижу права токена gh (fine-grained токен ?): нужны repo, workflow, secrets, variables"
  else
    for scope in repo workflow; do
      case ", $scopes," in
        *", $scope,"*) ok "у токена gh есть право $scope" ;;
        *) bad "у токена gh нет права $scope: gh auth refresh -s $scope" ;;
      esac
    done
  fi

  if [ "$GH_OWNER" = "$login" ]; then
    ok "GH_OWNER=$GH_OWNER: это ты"
  else
    role="$(gh api "orgs/$GH_OWNER/memberships/$login" --jq .role 2>/dev/null || true)"
    case "$role" in
      admin) ok "GH_OWNER=$GH_OWNER: ты admin организации" ;;
      "") bad "GH_OWNER=$GH_OWNER не твой аккаунт и не твоя организация: поменяй GH_OWNER в teams.conf" ;;
      *) warn "в организации $GH_OWNER ты $role: создать репозитории и секреты может не получиться" ;;
    esac
  fi

  orch="$GH_OWNER/$ORCHESTRATOR_REPO"
  if visibility="$(gh repo view "$orch" --json visibility --jq .visibility 2>/dev/null)"; then
    if [ "$visibility" = PUBLIC ]; then ok "$orch есть, публичный"; else warn "$orch $visibility: Render без подключенного GitHub собирает только публичные репозитории"; fi
    actions="$(gh api "repos/$orch/actions/permissions" --jq .enabled 2>/dev/null || true)"
    case "$actions" in
      true) ok "Actions в $orch включены" ;;
      false) bad "Actions в $orch выключены: Settings, Actions, Allow all actions" ;;
      *) warn "не смог проверить Actions в $orch (нужны права admin)" ;;
    esac
  else
    bad "$orch не найден: скопируй оркестратор к себе (SETUP.md, шаг 2)"
  fi

  missing=0 archived=0 private=0
  for entry in "${TEAMS[@]}"; do
    info="$(gh repo view "$GH_OWNER/${entry#*:}" --json visibility,isArchived --jq '.visibility + " " + (.isArchived | tostring)' 2>/dev/null)" || { missing=$((missing + 1)); continue; }
    case "$info" in *" true") archived=$((archived + 1)) ;; esac
    case "$info" in PUBLIC*) ;; *) private=$((private + 1)) ;; esac
  done
  if [ "$missing$archived$private" = 000 ]; then
    ok "репозитории команд: все ${#TEAMS[@]} есть, публичные"
  else
    warn "репозитории команд из ${#TEAMS[@]}: нет $missing, в архиве $archived, не публичных $private (tools/setup/create-team-repos.sh)"
  fi
fi

echo "Секреты ($SECRETS_DIR)"
env_file="$SECRETS_DIR/workshop.env"
if [ -f "$env_file" ]; then
  perms="$(stat -f %Lp "$env_file" 2>/dev/null || stat -c %a "$env_file" 2>/dev/null)"
  if [ "$perms" = 600 ]; then ok "workshop.env, права 600"; else warn "workshop.env: права $perms, поставь chmod 600"; fi
  for name in RENDER_API_KEY OPENAI_API_KEY ADMIN_TOKEN; do
    if grep -Eq "^${name}=.+" "$env_file"; then ok "$name заполнен"; else bad "$name пуст в workshop.env"; fi
  done
else
  bad "нет workshop.env: python3 tools/setup/render_ops.py init-secrets"
fi
keys=$(ls "$SECRETS_DIR"/keys/team_*.pub 2>/dev/null | wc -l | tr -d ' ')
if [ "$keys" -ge "${#TEAMS[@]}" ]; then
  ok "deploy-ключи команд: $keys"
else
  warn "deploy-ключей $keys из ${#TEAMS[@]}: python3 tools/setup/make-bootstrap.py"
fi

echo "Сеть"
for host in github.com api.github.com raw.githubusercontent.com; do
  code="$(http_code "https://$host/")"
  case "$code" in 000|"") bad "$host недоступен" ;; *) ok "$host отвечает ($code)" ;; esac
done
code="$(http_code https://api.render.com/v1/owners)"
if [ "$code" = 401 ]; then
  ok "api.render.com доступен"
  RENDER_LOCAL=1
else
  warn "api.render.com недоступен (код $code): команды Render только через GitHub Actions"
fi
code="$(http_code "https://${RENDER_PREFIX}-simulator.onrender.com/health")"
case "$code" in
  000|"") warn "*.onrender.com недоступен: табло с этого компьютера не открыть" ;;
  30?) warn "*.onrender.com отвечает редиректом ($code), похоже на фильтр сети: табло отсюда не открыть" ;;
  *) ok "*.onrender.com отвечает ($code)" ;;
esac

echo
if [ "$FAILS" -gt 0 ]; then
  echo "Итог: проблем $FAILS, их надо закрыть до сетапа"
else
  echo "Итог: можно начинать"
fi
if [ "$RENDER_LOCAL" = 1 ]; then
  echo "Render: команды можно запускать отсюда: python3 tools/setup/render_ops.py <команда>"
else
  echo "Render: только через GitHub Actions: gh workflow run workshop-ops.yml -R $GH_OWNER/$ORCHESTRATOR_REPO -f command=<команда>"
fi
[ "$FAILS" -eq 0 ]
