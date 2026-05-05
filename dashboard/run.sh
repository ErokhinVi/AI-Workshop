#!/usr/bin/env bash
# Удобный запуск дашборда — один скрипт, без лишних вопросов.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python -c "import fastapi" 2>/dev/null; then
  echo "Ставлю зависимости (один раз)..."
  pip install --quiet fastapi 'uvicorn[standard]'
fi

echo "Дашборд запускается на http://localhost:9000"
exec python dashboard/server.py "$@"
