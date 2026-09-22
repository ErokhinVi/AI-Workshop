#!/usr/bin/env bash
#
# raif-workshop-setup.sh: sets up a participant's Mac for the Raiffeisen AI
# workshop. Runs inside the AppleScript wrapper raif-workshop-setup.applescript,
# which asks for the team, the block and the name and passes them here:
#
#     bash raif-workshop-setup.sh team_c retail 'Ivan Petrov'
#
# One script for every team. It carries the deploy key of each team repo and
# drops only the key of the picked team: a deploy key opens one repo only.
#
# ─────────────────────────────────────────────────────────────────────────
#  NOTE for the organiser:
#  This is the master copy, without keys and without the team list. The
#  script handed to participants is built by tools/setup/make-bootstrap.py
#  and lives outside the repo: it carries private keys. Hand it out only
#  privately (AirDrop, USB). After the workshop revoke the keys:
#  tools/setup/github-access.sh revoke
# ─────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ────── parameters ──────
SSH_KEY_PATH="${HOME}/.ssh/raif_workshop"
SSH_CONFIG="${HOME}/.ssh/config"
SSH_CONFIG_MARKER="# raif-workshop-2026"

# >>> teams: tools/setup/make-bootstrap.py fills this in from tools/setup/teams.conf
# One line per team: <team code> <owner/repo> <label shown to people>
TEAM_TABLE='
__TEAMS_HERE__
'
# <<< teams

# >>> keys: tools/setup/make-bootstrap.py puts every team's deploy key here
write_team_key() {  # write_team_key <team code> <file>: that team's private key → file
  case "$1" in
    *) return 1 ;;
  esac
}
# <<< keys

# ────── visuals ──────
# Colours only when STDOUT is a terminal. They work in Terminal.app and turn
# themselves off in pipes/files so we don't dump escape codes into logs.
if [[ -t 1 ]]; then
  C_HEAD=$'\033[1;36m'   # bold cyan — big headers
  C_STEP=$'\033[1;34m'   # bold blue — step header
  C_OK=$'\033[0;32m'     # green     — ✓
  C_BAD=$'\033[0;31m'    # red       — ✗
  C_DIM=$'\033[0;90m'    # gray      — details
  C_RST=$'\033[0m'
else
  C_HEAD=""; C_STEP=""; C_OK=""; C_BAD=""; C_DIM=""; C_RST=""
fi

TOTAL_STEPS=9
CURRENT_STEP=0
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

banner() {
  printf "\n%s╔══════════════════════════════════════════════════════════════╗%s\n" "$C_HEAD" "$C_RST"
  printf   "%s║  Raif AI Workshop · laptop setup                             ║%s\n" "$C_HEAD" "$C_RST"
  printf   "%s║  raif-workshop-setup.sh                                      ║%s\n" "$C_HEAD" "$C_RST"
  printf   "%s╚══════════════════════════════════════════════════════════════╝%s\n" "$C_HEAD" "$C_RST"
  printf "  %sstarted:%s  %s\n"   "$C_DIM" "$C_RST" "$STARTED_AT"
  printf "  %sMac:%s      %s\n"   "$C_DIM" "$C_RST" "$(scutil --get ComputerName 2>/dev/null || hostname)"
  printf "  %suser:%s     %s\n"   "$C_DIM" "$C_RST" "${USER}"
  printf "  %sHOME:%s     %s\n\n" "$C_DIM" "$C_RST" "${HOME}"
}

step() {
  CURRENT_STEP=$((CURRENT_STEP+1))
  printf "\n%s━━━━━━[ %d/%d ]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━%s\n" \
         "$C_STEP" "$CURRENT_STEP" "$TOTAL_STEPS" "$C_RST"
  printf   "%s  %s%s\n\n" "$C_STEP" "$1" "$C_RST"
}

ok()    { printf "  %s✓%s %s\n" "$C_OK"  "$C_RST" "$*"; }
info()  { printf "  %s·%s %s\n" "$C_DIM" "$C_RST" "$*"; }
note()  { printf "      %s%s%s\n" "$C_DIM" "$*" "$C_RST"; }
warn()  { printf "  %s!%s %s\n" "$C_BAD" "$C_RST" "$*"; }
die()   {
  printf "\n  %s✗ %s%s\n"                                      "$C_BAD" "$*" "$C_RST" >&2
  printf "\n%sSetup aborted. Show the host the message above.%s\n\n" "$C_BAD" "$C_RST" >&2
  exit 1
}

banner

# ────── arguments: team, block, name ──────
TEAM="${1:-}"
BLOCK="${2:-}"
GIT_NAME="${3:-}"
if [ -z "${TEAM}" ] || [ -z "${BLOCK}" ] || [ -z "${GIT_NAME}" ]; then
  die "Missing arguments. Run via the AppleScript wrapper."
fi
if grep -qx "__TEAMS_HERE__" <<<"${TEAM_TABLE}"; then
  die "Script is unsigned: no teams and keys inside. Organiser: build it with tools/setup/make-bootstrap.py."
fi
TEAM_ROW="$(awk -v t="${TEAM}" '$1 == t' <<<"${TEAM_TABLE}")"
if [ -z "${TEAM_ROW}" ]; then
  die "Unknown team '${TEAM}'. Expected one of: $(awk 'NF { printf "%s ", $1 }' <<<"${TEAM_TABLE}")"
fi
REPO_SLUG="$(awk '{ print $2 }' <<<"${TEAM_ROW}")"
TEAM_HUMAN="$(cut -d' ' -f3- <<<"${TEAM_ROW}")"
REPO_URL="git@github.com:${REPO_SLUG}.git"
REPO_DIR="${HOME}/${REPO_SLUG#*/}"
case "${BLOCK}" in
  retail)  BLOCK_HUMAN="Retail — customer mobile bank" ;;
  cib)     BLOCK_HUMAN="CIB — corporate and business logic" ;;
  backend) BLOCK_HUMAN="Backend — bank data core" ;;
  *) die "Unknown block '${BLOCK}'. Expected: retail | cib | backend." ;;
esac

# ────── 1. sanity ──────
step "Checking the environment"
[[ "$(uname)" == "Darwin" ]] || die "This script is for Mac. You are on $(uname). Stopping."

# Full tool sweep: show what's already on the machine, so when the script is
# handed out in the room we can spot from the log who is missing what.
mac_ver="$(sw_vers -productVersion 2>/dev/null || echo '?')"
mac_arch="$(uname -m)"
info "OS:        $(uname -sr)  (macOS ${mac_ver}, ${mac_arch})"
info "user:      ${USER}"
info "HOME:      ${HOME}"
info "Team:      ${TEAM_HUMAN} → ${REPO_SLUG}"
info "Block:     ${BLOCK_HUMAN}"

check_tool() {
  # ${2:-} — otherwise under set -u a call without a hint dies on "$2: unbound".
  local name="$1" hint="${2:-}"
  if command -v "${name}" >/dev/null 2>&1; then
    local p; p="$(command -v "${name}")"
    local v=''
    # macOS ssh does not understand --version (prints "illegal option" to
    # stderr) — use -V. Other tools usually accept --version.
    case "${name}" in
      ssh|scp|sftp) v="$("${name}" -V 2>&1 | head -1)" ;;
      *)            v="$("${name}" --version 2>&1 | head -1)" ;;
    esac
    # If we got junk (manual pages, errors), drop the version silently rather
    # than shouting about "illegal option". This is a reference log, not a crime.
    if [[ "${v}" == *"illegal option"* || "${v}" == *"unknown option"* \
       || "${v}" == *"option requires"* || "${v}" == *"usage:"* ]]; then
      v=''
    fi
    if [[ -n "${v}" ]]; then
      info "${name}: ✓  ${p}  (${v})"
    else
      info "${name}: ✓  ${p}"
    fi
    return 0
  else
    if [[ -n "${hint}" ]]; then
      info "${name}: ✗  not installed  (${hint})"
    else
      info "${name}: ✗  not installed"
    fi
    return 1
  fi
}

# Tools the script explicitly depends on:
check_tool git    "we will install via Xcode Command Line Tools" || true
check_tool ssh    "should be at /usr/bin/ssh — strange for Mac" || true
# For reference — what else is on the system (Claude Code App is not probed:
# it's already installed on the workshop Macs and runs the agent inside).
check_tool python3 || true

# On a fresh Mac belonging to a non-technical user Xcode Command Line Tools
# usually aren't present — and without them there is neither git nor (an up
# to date) python3. We open the system install popup ourselves and ask the
# user to run the script again once the installation finishes. This is a
# one-time operation: after CLT is installed git/python3 stay forever.
# xcode-select --install is the only official way to install CLI tools on
# Mac without requiring Xcode.app or an admin password.
if ! command -v git >/dev/null 2>&1; then
  if xcode-select -p >/dev/null 2>&1; then
    die "git not found, but Command Line Tools are installed. Show the host: 'xcode-select -p' = $(xcode-select -p)"
  fi
  printf "\n  %s!%s git not found — opening the system Command Line Tools install popup.\n" "$C_BAD" "$C_RST"
  /usr/bin/xcode-select --install >/dev/null 2>&1 || true
  printf "\n  A window 'The xcode-select command requires...' appeared in the top right:\n"
  printf "    1. Press 'Install'.\n"
  printf "    2. Agree to the license.\n"
  printf "    3. Wait 5–10 minutes until the install finishes.\n"
  printf "    4. Double-click this script again.\n\n"
  printf "  %sSetup not finished — Command Line Tools must be installed first.%s\n\n" "$C_BAD" "$C_RST"
  exit 1
fi
info "git: $(git --version)"

command -v ssh >/dev/null    || die "SSH not found — strange for Mac. Show the host."
info "ssh: $(ssh -V 2>&1 | head -1)"
ok "Environment looks good"

# ────── 2. SSH key of the picked team ──────
step "Dropping the SSH key of ${TEAM_HUMAN}"
mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
info "Folder: ${HOME}/.ssh  (mode 700)"

rm -f "${SSH_KEY_PATH}"
if ! (umask 077; write_team_key "${TEAM}" "${SSH_KEY_PATH}"); then
  rm -f "${SSH_KEY_PATH}"
  die "No key for ${TEAM_HUMAN} inside this script. Organiser: rebuild it with tools/setup/make-bootstrap.py."
fi
chmod 600 "${SSH_KEY_PATH}"
SSH_FP="$(ssh-keygen -lf "${SSH_KEY_PATH}" 2>/dev/null | awk '{print $2, $4}' || echo '?')"
ok "File: ${SSH_KEY_PATH}  (mode 600)"
note "fingerprint: ${SSH_FP}"

# ────── 3. SSH config ──────
step "Configuring ssh to use this key for GitHub"
touch "${SSH_CONFIG}"
chmod 600 "${SSH_CONFIG}"
info "File: ${SSH_CONFIG}  (mode 600)"

if grep -qF "${SSH_CONFIG_MARKER}" "${SSH_CONFIG}" 2>/dev/null; then
  ok "github.com entry already present — leaving as is"
else
  cat >> "${SSH_CONFIG}" <<EOF

${SSH_CONFIG_MARKER}
# GitHub via port 443 — port 22 is blocked on the corporate network
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ${SSH_KEY_PATH}
  IdentitiesOnly yes
EOF
  ok "Added Host github.com block → IdentityFile=${SSH_KEY_PATH}"
fi

# ────── 4. git identity ──────
step "Participant identity for commit signatures"

# Slug: lowercase ASCII letters, digits and dashes only. Good enough for an
# email local part and a PARTICIPANT slug. macOS iconv can't transliterate
# Cyrillic (it fails, and under pipefail the whole setup stopped silently),
# so Russian letters are mapped by hand first; iconv -c only strips accents.
ru_to_latin() {
  sed -e 's/щ/shch/g; s/Щ/Shch/g; s/ж/zh/g; s/Ж/Zh/g; s/х/kh/g; s/Х/Kh/g; s/ц/ts/g; s/Ц/Ts/g' \
      -e 's/ч/ch/g; s/Ч/Ch/g; s/ш/sh/g; s/Ш/Sh/g; s/ю/yu/g; s/Ю/Yu/g; s/я/ya/g; s/Я/Ya/g' \
      -e 's/а/a/g; s/А/A/g; s/б/b/g; s/Б/B/g; s/в/v/g; s/В/V/g; s/г/g/g; s/Г/G/g; s/д/d/g; s/Д/D/g' \
      -e 's/е/e/g; s/Е/E/g; s/ё/e/g; s/Ё/E/g; s/з/z/g; s/З/Z/g; s/и/i/g; s/И/I/g; s/й/y/g; s/Й/Y/g' \
      -e 's/к/k/g; s/К/K/g; s/л/l/g; s/Л/L/g; s/м/m/g; s/М/M/g; s/н/n/g; s/Н/N/g; s/о/o/g; s/О/O/g' \
      -e 's/п/p/g; s/П/P/g; s/р/r/g; s/Р/R/g; s/с/s/g; s/С/S/g; s/т/t/g; s/Т/T/g; s/у/u/g; s/У/U/g' \
      -e 's/ф/f/g; s/Ф/F/g; s/ы/y/g; s/Ы/Y/g; s/э/e/g; s/Э/E/g; s/ъ//g; s/Ъ//g; s/ь//g; s/Ь//g'
}
PARTICIPANT="$(printf '%s' "${GIT_NAME}" | ru_to_latin \
  | { iconv -c -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null || true; } \
  | tr '[:upper:]' '[:lower:]' \
  | sed -e 's/[^a-z0-9]\{1,\}/-/g' -e 's/^-//' -e 's/-$//')"
if [ -z "${PARTICIPANT}" ]; then PARTICIPANT="anonymous"; fi
GIT_EMAIL="${PARTICIPANT}@raif-workshop.local"

info "Participant: ${GIT_NAME}"
info "Email:       ${GIT_EMAIL}"
info "Team:        ${TEAM_HUMAN} (${TEAM})"
info "Block:       ${BLOCK_HUMAN}"
info "Block folder: ${REPO_DIR}/${BLOCK}/"

git config --global user.name  "${GIT_NAME}"
git config --global user.email "${GIT_EMAIL}"
ok "Global git signature: ${GIT_NAME} <${GIT_EMAIL}>"
note "file: ~/.gitconfig"

# ────── 5. verify GitHub auth ──────
step "Checking GitHub access with this key"
info "ssh -T git@github.com  (BatchMode, StrictHostKeyChecking=accept-new)"
SSH_OUT="$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new git@github.com 2>&1 || true)"
if grep -q "successfully authenticated" <<<"${SSH_OUT}"; then
  GH_USER="$(printf '%s' "${SSH_OUT}" | sed -nE 's/^Hi ([^!]+)!.*/\1/p')"
  ok "GitHub recognised us${GH_USER:+ as ${GH_USER}}"
  note "response: $(printf '%s' "${SSH_OUT}" | head -1)"
else
  printf "%s\n" "${SSH_OUT}"
  die "GitHub did not accept the key. Show the host the output above."
fi

# ────── 6. clone or update ──────
step "Preparing the project folder ${REPO_DIR}"
if [ -d "${REPO_DIR}/.git" ]; then
  info "Folder already exists — pulling fresh changes"
  git -C "${REPO_DIR}" remote set-url origin "${REPO_URL}"
  git -C "${REPO_DIR}" fetch origin --prune 2>&1 | sed 's/^/      /'
  # For existing clones force-align main with origin
  git -C "${REPO_DIR}" checkout main 2>/dev/null || true
  git -C "${REPO_DIR}" reset --hard origin/main 2>&1 | sed 's/^/      /'
  ok "Pulled and aligned main"
else
  info "Cloning ${REPO_URL}"
  git clone "${REPO_URL}" "${REPO_DIR}" 2>&1 | sed 's/^/      /'
  ok "Cloned into ${REPO_DIR}"
fi
HEAD_LINE="$(git -C "${REPO_DIR}" log -1 --pretty='%h %s' 2>/dev/null || echo '?')"
BRANCH_LINE="$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
note "branch: ${BRANCH_LINE}"
note "HEAD:   ${HEAD_LINE}"

# ────── 7. block isolation for Claude and Codex ──────
step "Installing block isolation — edits restricted to your block"
CLAUDE_DIR="${REPO_DIR}/.claude"
SETTINGS_TEMPLATE="${CLAUDE_DIR}/templates/settings-${BLOCK}.json"
if [ -f "${SETTINGS_TEMPLATE}" ]; then
  cp "${SETTINGS_TEMPLATE}" "${CLAUDE_DIR}/settings.local.json"
  ok "Claude isolation active: .claude/settings.local.json"
  note "template: settings-${BLOCK}.json"
else
  warn "Template not found: ${SETTINGS_TEMPLATE}"
  note "Claude will warn about this during onboarding"
fi

# Same block protection for participants who use Codex instead of Claude.
CODEX_DIR="${REPO_DIR}/.codex"
CODEX_TEMPLATE="${CODEX_DIR}/templates/config-${BLOCK}.toml"
if [ -f "${CODEX_TEMPLATE}" ]; then
  cp "${CODEX_TEMPLATE}" "${CODEX_DIR}/config.toml"
  ok "Codex isolation active: .codex/config.toml"
  note "template: config-${BLOCK}.toml"
  # Trust the repository folder — otherwise Codex won't load the project
  # config. If the path format doesn't match, Codex will simply ask for trust
  # on its next start.
  CODEX_HOME="${HOME}/.codex"
  CODEX_CFG="${CODEX_HOME}/config.toml"
  mkdir -p "${CODEX_HOME}"
  if [ -f "${CODEX_CFG}" ] && grep -qF "[projects.\"${REPO_DIR}\"]" "${CODEX_CFG}" 2>/dev/null; then
    note "Folder already trusted by Codex"
  else
    printf '\n[projects."%s"]\ntrust_level = "trusted"\n' "${REPO_DIR}" >> "${CODEX_CFG}"
    note "Folder marked as trusted in ${CODEX_CFG}"
  fi
else
  warn "Codex template not found: ${CODEX_TEMPLATE}"
  note "Codex will install isolation itself during onboarding (see AGENTS.md)"
fi

# ────── 8. inject key + info into .git/ for Claude Code App ──────
step "Preparing sandbox onboarding for Claude (.git/raif-workshop-*)"

# .git/ is not tracked by git, so the key never ends up in a commit
cp "${SSH_KEY_PATH}" "${REPO_DIR}/.git/raif-workshop-key"
chmod 600 "${REPO_DIR}/.git/raif-workshop-key"
ok "Sandbox key: .git/raif-workshop-key  (mode 600)"

cat > "${REPO_DIR}/.git/raif-workshop-info" <<EOF
# raif-workshop-2026 — participant meta-info for Claude Code App.
# Read by tools/cowork-onboard.py on Claude's first launch (only relevant
# when the agent runs inside a Linux sandbox; ignored on a Win/Mac host).
WORKSHOP_PARTICIPANT=${PARTICIPANT}
WORKSHOP_TEAM=${TEAM}
WORKSHOP_BLOCK=${BLOCK}
WORKSHOP_GIT_NAME=${GIT_NAME}
WORKSHOP_GIT_EMAIL=${GIT_EMAIL}
EOF
chmod 600 "${REPO_DIR}/.git/raif-workshop-info"
ok "Info file: .git/raif-workshop-info"
note "WORKSHOP_PARTICIPANT=${PARTICIPANT}"
note "WORKSHOP_TEAM=${TEAM}"
note "WORKSHOP_BLOCK=${BLOCK}"

# ────── 9. local git config — safety net for Claude agent sessions ──
step "Local repo git config — safety net for Claude agent sessions"
# If the agent starts inside its own sandbox with its own $HOME, --global on
# the Mac user is invisible there. Drop the signature and ssh-command into
# the local .git/config: it lives on disk and is visible from anywhere this
# repo is opened.
git -C "${REPO_DIR}" config user.name  "${GIT_NAME}"
git -C "${REPO_DIR}" config user.email "${GIT_EMAIL}"
git -C "${REPO_DIR}" config core.sshCommand \
  "ssh -i '${REPO_DIR}/.git/raif-workshop-key' -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/raif_known_hosts"
ok "user.name       = ${GIT_NAME}"
ok "user.email      = ${GIT_EMAIL}"
ok "core.sshCommand = ssh -i .git/raif-workshop-key (accept-new)"
note "file: ${REPO_DIR}/.git/config"

# ────── SUMMARY ──────
printf "\n%s╔══════════════════════════════════════════════════════════════╗%s\n" "$C_HEAD" "$C_RST"
printf   "%s║  ALL SET. Your Mac is ready for the workshop.                ║%s\n" "$C_HEAD" "$C_RST"
printf   "%s╚══════════════════════════════════════════════════════════════╝%s\n" "$C_HEAD" "$C_RST"

printf "\n  %sProject folder:%s   %s\n"   "$C_DIM" "$C_RST" "${REPO_DIR}"
printf   "  %sSignature:%s        %s <%s>\n" "$C_DIM" "$C_RST" "${GIT_NAME}" "${GIT_EMAIL}"
printf   "  %sTeam:%s             %s (%s)\n" "$C_DIM" "$C_RST" "${TEAM_HUMAN}" "${TEAM}"
printf   "  %sBlock:%s            %s\n"   "$C_DIM" "$C_RST" "${BLOCK_HUMAN}"
printf   "  %sGitHub accepted:%s  %s\n"   "$C_DIM" "$C_RST" "${GH_USER:-yes}"
printf   "  %sCurrent branch:%s   %s\n"   "$C_DIM" "$C_RST" "${BRANCH_LINE}"
printf   "  %sProject HEAD:%s     %s\n"   "$C_DIM" "$C_RST" "${HEAD_LINE}"
printf   "  %sSSH fingerprint:%s  %s\n\n" "$C_DIM" "$C_RST" "${SSH_FP}"

printf "  %sBlock isolation:%s\n" "$C_DIM" "$C_RST"
printf "  %sYou see and edit only your block. Other teams are invisible: you can only reach them through their public sites.%s\n\n" "$C_DIM" "$C_RST"

cat <<EOF
  Files the script created or updated:
    ✓ ${HOME}/.ssh/raif_workshop                            (workshop private key)
    ✓ ${HOME}/.ssh/config                                   (Host github.com block)
    ✓ ${HOME}/.gitconfig                                    (--global user.name/email)
    ✓ ${REPO_DIR}/.git/raif-workshop-key                    (key copy for Claude)
    ✓ ${REPO_DIR}/.git/raif-workshop-info                   (meta-info for Claude)
    ✓ ${REPO_DIR}/.git/config                               (local signature + core.sshCommand)
    ✓ ${REPO_DIR}/.claude/settings.local.json               (Claude block isolation)
    ✓ ${REPO_DIR}/.codex/config.toml                        (Codex block isolation)

  What's next:
    1. Open Claude Code App.
    2. Add ${REPO_DIR} as the working folder.
    3. Write the agent any first message — it will pick up the key
       and read who you are from the info file.

  To switch to another block later: save your work (ask the agent),
  then run this setup again and pick the new block.

  If you use Codex instead of Claude: open the project folder in Codex
  and write a first message — block isolation is already in place
  (.codex/config.toml), and Codex reads the script from AGENTS.md.

EOF
