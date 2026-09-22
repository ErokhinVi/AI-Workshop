#!/usr/bin/env bash
# tools/bootstrap/raif-workshop-rollback.sh: вернуть мак как было после
# репетиции с установщиком ноутбука (raif-workshop-setup.applescript).
#
#   bash raif-workshop-rollback.sh save      до установщика: снимок
#   bash raif-workshop-rollback.sh status    что поменялось с момента снимка
#   bash raif-workshop-rollback.sh restore   вернуть как было в снимке
#
# Установщик трогает ровно это, restore возвращает только это:
#   ~/.gitconfig            user.name и user.email
#   ~/.ssh/raif_workshop    ключ команды поверх прежнего файла
#   ~/.ssh/raif_workshop.pub  его публичная половина
#   ~/.ssh/config           блок "# raif-workshop-2026", если его не было
#   ~/.codex/config.toml    доверие папке команды
#   ~/<репо команды>        клон, в нем .git/raif-workshop-info
#   /tmp/raif_bootstrap.sh  расшифрованный установщик с ключами всех команд
# ~/.ssh/known_hosts не откатывается: там максимум ключ хоста ssh.github.com.
#
# Чужие правки в этих файлах после снимка не теряются: git и codex правятся
# точечно, ssh config возвращается из снимка, только если к нему дописан
# блок установщика. Папка команды уезжает в Корзину, не удаляется.

set -euo pipefail

SNAP="${HOME}/.raif-workshop-rollback"
KEY="${HOME}/.ssh/raif_workshop"
SSH_CFG="${HOME}/.ssh/config"
CODEX_CFG="${HOME}/.codex/config.toml"
MARKER="# raif-workshop-2026"
TMP_INSTALLER="${RAIF_BOOTSTRAP_TMP:-/tmp/raif_bootstrap.sh}"

die()  { echo "ошибка: $*" >&2; exit 1; }
same() { echo "  = $*"; }
diff_() { echo "  ≠ $*"; DIFFS=$((DIFFS + 1)); }

team_repos() {  # клоны установщика: в них .git/raif-workshop-info
  local info
  for info in "${HOME}"/*/.git/raif-workshop-info; do
    if [ -f "$info" ]; then dirname "$(dirname "$info")"; fi
  done
}

new_repos() {  # клоны, которых не было на момент снимка
  local dir
  team_repos | while read -r dir; do
    grep -qxF "$dir" "${SNAP}/repos" || echo "$dir"
  done
}

git_value() {  # git_value name|email: значение --global или (не задано)
  git config --global --get "user.$1" || echo "(не задано)"
}

fingerprint() {  # из самого ключа: соседний .pub установщик не обновляет
  ssh-keygen -y -P '' -f "$1" 2>/dev/null | ssh-keygen -lf - 2>/dev/null | awk '{print $2, $3}' || echo "?"
}

save() {
  if [ -d "$SNAP" ] && [ "${1:-}" != "--force" ]; then
    die "снимок уже есть от $(cat "${SNAP}/saved-at"). Перезаписать: save --force"
  fi
  case "$(git_value email)" in
    *@raif-workshop.local) die "в git уже подпись участника: установщик отработал, снимок снимет не то" ;;
  esac
  rm -rf "$SNAP"
  (umask 077; mkdir -p "$SNAP")
  local name
  for name in name email; do git_value "$name" > "${SNAP}/git-user-${name}"; done
  cp -p "${HOME}/.gitconfig" "${SNAP}/gitconfig" 2>/dev/null || true
  if [ -f "$KEY" ]; then cp -p "$KEY" "${SNAP}/raif_workshop"; fi
  if [ -f "${KEY}.pub" ]; then cp -p "${KEY}.pub" "${SNAP}/raif_workshop.pub"; fi
  if [ -f "$SSH_CFG" ]; then cp -p "$SSH_CFG" "${SNAP}/ssh-config"; fi
  if [ -f "$CODEX_CFG" ]; then cp -p "$CODEX_CFG" "${SNAP}/codex-config.toml"; fi
  team_repos > "${SNAP}/repos"
  date '+%Y-%m-%d %H:%M:%S' > "${SNAP}/saved-at"
  echo "снимок в ${SNAP}:"
  echo "  git: $(cat "${SNAP}/git-user-name") <$(cat "${SNAP}/git-user-email")>"
  if [ -f "${SNAP}/raif_workshop" ]; then echo "  ключ ~/.ssh/raif_workshop: $(fingerprint "$KEY")"; else echo "  ключа ~/.ssh/raif_workshop нет"; fi
  echo "  ssh config, codex config: копии"
  echo "  клоны установщика сейчас: $(tr '\n' ' ' < "${SNAP}/repos")"
  echo "после репетиции: bash $0 restore"
}

status() {
  [ -d "$SNAP" ] || die "снимка нет: сначала save"
  DIFFS=0
  local name was now dir
  echo "сравниваю со снимком от $(cat "${SNAP}/saved-at"):"
  for name in name email; do
    was="$(cat "${SNAP}/git-user-${name}")"
    now="$(git_value "$name")"
    if [ "$was" = "$now" ]; then same "git user.${name}: ${now}"; else diff_ "git user.${name}: было «${was}», сейчас «${now}»"; fi
  done
  if [ -f "${SNAP}/raif_workshop" ] && cmp -s "${SNAP}/raif_workshop" "$KEY"; then
    same "ключ ~/.ssh/raif_workshop прежний"
  elif [ ! -f "${SNAP}/raif_workshop" ] && [ ! -f "$KEY" ]; then
    same "ключа ~/.ssh/raif_workshop нет"
  else
    diff_ "ключ ~/.ssh/raif_workshop другой: $( [ -f "$KEY" ] && fingerprint "$KEY" || echo 'удален')"
  fi
  if cmp -s "${SNAP}/raif_workshop.pub" "${KEY}.pub" 2>/dev/null || { [ ! -f "${SNAP}/raif_workshop.pub" ] && [ ! -f "${KEY}.pub" ]; }; then
    same "~/.ssh/raif_workshop.pub прежний"
  else
    diff_ "~/.ssh/raif_workshop.pub изменен"
  fi
  if cmp -s "${SNAP}/ssh-config" "$SSH_CFG" 2>/dev/null || { [ ! -f "${SNAP}/ssh-config" ] && [ ! -f "$SSH_CFG" ]; }; then
    same "~/.ssh/config прежний"
  else
    diff_ "~/.ssh/config изменен"
  fi
  while read -r dir; do
    [ -n "$dir" ] || continue
    diff_ "новая папка ${dir}"
    if grep -qF "[projects.\"${dir}\"]" "$CODEX_CFG" 2>/dev/null \
       && ! grep -qF "[projects.\"${dir}\"]" "${SNAP}/codex-config.toml" 2>/dev/null; then
      diff_ "codex доверяет ${dir}"
    fi
  done < <(new_repos)
  if [ -f "$TMP_INSTALLER" ]; then diff_ "${TMP_INSTALLER}: установщик с ключами всех команд"; fi
  if [ "$DIFFS" -eq 0 ]; then echo "все как в снимке"; fi
}

restore_ssh_config() {
  python3 - "$SSH_CFG" "${SNAP}/ssh-config" "$MARKER" <<'PY'
import sys
from pathlib import Path

cur_p, snap_p, marker = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
cur = cur_p.read_text() if cur_p.exists() else ""
snap = snap_p.read_text() if snap_p.exists() else ""
if cur_p.exists() == snap_p.exists() and cur == snap:
    print("  ~/.ssh/config прежний")
elif marker not in snap and cur.startswith(snap) and marker in cur[len(snap):]:
    if snap_p.exists():
        cur_p.write_text(snap)
    else:
        cur_p.unlink()
    print("  ~/.ssh/config: убрал блок установщика")
else:
    print(f"  ~/.ssh/config менялся не только установщиком, не трогаю. Копия: {snap_p}")
PY
}

restore_codex() {  # restore_codex <папка>...: убрать доверие папкам установщика
  [ -f "$CODEX_CFG" ] || return 0
  python3 - "$CODEX_CFG" "${SNAP}/codex-config.toml" "$@" <<'PY'
import sys
from pathlib import Path

cfg, snap_p, dirs = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
text = cfg.read_text()
snap = snap_p.read_text() if snap_p.exists() else ""
for d in dirs:
    header = f'[projects."{d}"]'
    block = f'\n{header}\ntrust_level = "trusted"\n'
    if header in snap:
        continue
    if block in text:
        text = text.replace(block, "", 1)
        print(f"  codex больше не доверяет {d}")
    elif header in text:
        print(f"  codex: {header} кто-то поменял, не трогаю")
if not snap_p.exists() and not text.strip():
    cfg.unlink()
else:
    cfg.write_text(text)
PY
}

restore() {
  [ -d "$SNAP" ] || die "снимка нет: откатывать не к чему"
  local name dir repos
  repos="$(new_repos)"
  echo "возвращаю как было на $(cat "${SNAP}/saved-at"):"
  for name in name email; do
    if [ "$(cat "${SNAP}/git-user-${name}")" = "(не задано)" ]; then
      git config --global --unset "user.${name}" || true
    else
      git config --global "user.${name}" "$(cat "${SNAP}/git-user-${name}")"
    fi
  done
  echo "  git: $(git_value name) <$(git_value email)>"
  if [ -f "${SNAP}/raif_workshop" ]; then
    (umask 077; cp "${SNAP}/raif_workshop" "$KEY")
    chmod 600 "$KEY"
    echo "  ключ ~/.ssh/raif_workshop прежний: $(fingerprint "$KEY")"
  elif [ -f "$KEY" ]; then
    rm -f "$KEY"
    echo "  ключ ~/.ssh/raif_workshop убрал: до установщика его не было"
  fi
  if [ -f "${SNAP}/raif_workshop.pub" ]; then
    cp -p "${SNAP}/raif_workshop.pub" "${KEY}.pub"
    echo "  ~/.ssh/raif_workshop.pub прежний"
  elif [ -f "${KEY}.pub" ]; then
    rm -f "${KEY}.pub"
    echo "  ~/.ssh/raif_workshop.pub убрал: до установщика его не было"
  fi
  restore_ssh_config
  if [ -n "$repos" ]; then
    # shellcheck disable=SC2086
    restore_codex $repos
    while read -r dir; do
      mv "$dir" "${HOME}/.Trash/$(basename "$dir") $(date +%H.%M.%S)"
      echo "  ${dir} в Корзине"
    done <<<"$repos"
  fi
  if [ -f "$TMP_INSTALLER" ]; then
    rm -f "$TMP_INSTALLER"
    echo "  ${TMP_INSTALLER} удален"
  fi
  echo
  status
  local done_dir
  done_dir="${SNAP}.restored-$(date +%Y%m%d-%H%M%S)"
  mv "$SNAP" "$done_dir"
  echo "снимок переложил в ${done_dir}: следующий save начнет с чистого листа"
  for dir in "${HOME}"/Downloads/raif-workshop-setup*; do
    if [ -f "$dir" ]; then echo "в Загрузках остался установщик с ключами всех команд: ${dir}"; fi
  done
  echo "репозиторий команды на GitHub откатывает пульт: team-reset, args номер команды, confirm RESET"
}

case "${1:-}" in
  save)    save "${2:-}" ;;
  status)  status ;;
  restore) restore ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
