#!/usr/bin/env bash
#
# keep-awake.sh — «будильник» для сервисов воркшопа на Render free-плане.
#
# Render усыпляет free-сервис после простоя, и холодный старт занимает
# 30–60 секунд — на воркшопе это раздражает. Скрипт раз в INTERVAL секунд
# (по умолчанию 120) пингует /health всех семи сервисов и держит их в тонусе.
#
# Запуск (держать окно открытым весь воркшоп):
#   ./tools/keep-awake.sh
#
# Один проход и выход (для проверки):
#   ./tools/keep-awake.sh --once
#
# Свой интервал (например 90 секунд):
#   INTERVAL=90 ./tools/keep-awake.sh
#
# Фоном, с логом в файл (не держать окно):
#   nohup ./tools/keep-awake.sh > keep-awake.log 2>&1 &
#   ...остановить:  kill %1   (или  pkill -f keep-awake.sh )
#
# Остановка в обычном режиме — Ctrl+C.

set -u

INTERVAL="${INTERVAL:-120}"   # период между обходами, секунды
TIMEOUT="${TIMEOUT:-90}"      # ждать ответа от сервиса, секунды (хватит на холодный старт)

# Все семь сервисов воркшопа. Пингуем /health — он есть у всех шести блоков
# (симулятор сам их так опрашивает) и у самого симулятора.
URLS=(
  "https://raif-a-retail.onrender.com/health"
  "https://raif-a-cib.onrender.com/health"
  "https://raif-a-backend.onrender.com/health"
  "https://raif-b-retail.onrender.com/health"
  "https://raif-b-cib.onrender.com/health"
  "https://raif-b-backend.onrender.com/health"
  "https://raif-simulator.onrender.com/health"
)

# Короткое читаемое имя из URL: https://raif-a-cib.onrender.com/health -> raif-a-cib
short_name() {
  local u="$1"
  u="${u#https://}"
  echo "${u%%.onrender.com*}"
}

ping_one() {
  local url="$1"
  local name; name="$(short_name "$url")"
  local code
  code="$(curl -sS -o /dev/null -m "$TIMEOUT" -w '%{http_code}' "$url" 2>/dev/null)" || code="000"
  # 2xx/3xx/4xx — сервис ответил (значит проснулся). 000/5xx — проблема.
  if [[ "$code" =~ ^[234] ]]; then
    printf '   OK   %-16s HTTP %s\n' "$name" "$code"
  else
    printf '  FAIL  %-16s (%s)\n' "$name" "${code:-нет ответа}"
  fi
}

sweep() {
  local stamp; stamp="$(date '+%H:%M:%S')"
  echo "[$stamp] обход сервисов…"
  # Пингуем параллельно: если кто-то спит, холодные старты не складываются.
  local tmp; tmp="$(mktemp -d)"
  local i=0
  for url in "${URLS[@]}"; do
    ping_one "$url" > "$tmp/$i" &
    i=$((i + 1))
  done
  wait
  # Печатаем в исходном порядке, чтобы лог был стабильным.
  for ((j = 0; j < i; j++)); do cat "$tmp/$j"; done
  rm -rf "$tmp"
}

echo "keep-awake: пингую ${#URLS[@]} сервиса каждые ${INTERVAL}с (таймаут ${TIMEOUT}с). Ctrl+C — стоп."
echo

if [[ "${1:-}" == "--once" ]]; then
  sweep
  exit 0
fi

while true; do
  sweep
  echo
  sleep "$INTERVAL"
done
