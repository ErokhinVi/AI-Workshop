"""
Загрузить seed-данные в живые блоки банка.

Для каждого блока вызывает специальную ручку `/_seed/load`, которая
принимает соответствующий jsonl-файл и сохраняет его в памяти процесса.

Запуск (когда блоки уже подняты — локально через docker-compose
или после Render-деплоя):

    python cases/_seed/load_into_blocks.py
    python cases/_seed/load_into_blocks.py --base https://raif-{block}.onrender.com

Скрипт идемпотентен — можно запускать сколько угодно раз.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent

# что куда грузим: блок → список (имя файла, ключ-нагрузка)
LOADS = {
    "retail":  [("clients.jsonl", "clients"),
                ("transactions.jsonl", "transactions")],
    "risk":    [("clients.jsonl", "clients"),
                ("credit_history.jsonl", "credit_history")],
    "finance": [("clients.jsonl", "clients")],
}


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _post(url: str, payload: dict, timeout: int = 30) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_resp = ""
        try:
            body_resp = e.read(1024).decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body_resp
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base",
        default="http://localhost:{port}",
        help=("Шаблон базового URL. По умолчанию — локальные порты "
              "докер-композ (8000-8050). Для Render укажи "
              "https://raif-{block}.onrender.com"),
    )
    args = parser.parse_args()

    ports = {"retail": 8020, "risk": 8050, "finance": 8040,
             "cib": 8010, "it": 8030, "ceo": 8000}

    failed = 0
    for block, files in LOADS.items():
        # {block} подставляется как чистое имя блока (retail/cib/...).
        # Если хочешь Render — пиши '--base https://raif-{block}.onrender.com'.
        # Если локально — '--base http://localhost:{port}'.
        base = args.base.format(block=block, port=ports[block])
        print(f"[{block}] {base}")
        for fname, key in files:
            rows = _read_jsonl(SEED_DIR / fname)
            url = f"{base}/_seed/load"
            code, body = _post(url, {key: rows}, timeout=60)
            mark = "OK" if code == 200 else "FAIL"
            print(f"  {mark}  {fname:>22}  ({len(rows)} rows)  HTTP {code}")
            if code != 200:
                failed += 1

    if failed:
        print(f"\n{failed} ручек не приняли seed. Поднял ли блок ручку /_seed/load?")
        return 1
    print("\nseed загружен в блоки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
