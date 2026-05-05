"""
Ждать пока Render додеплоит сервис блока.

Агент любого блока запускает после успешного `git push`:

    python dashboard/wait_deploy.py raif-cib

И уходит ждать. Скрипт каждые 5 секунд дёргает /health сервиса,
пока не получит 200 OK или не упрётся в таймаут (по умолчанию 4 минуты —
покрывает cold start + сборку контейнера).

Печатает читаемый прогресс в stdout — агент его видит и может
сообщить пользователю человеческим языком («ещё минута, собираем»).

Exit codes:
    0 — сервис ответил 200 OK
    1 — таймаут / сервис не поднялся
    2 — неверные аргументы

Опционально: если в окружении есть RENDER_API_TOKEN — после таймаута
скрипт попытается прочитать последние 50 строк лога деплоя через
Render API и распечатать их. Без токена просто скажет «таймаут, иди в UI».

Только stdlib — никаких зависимостей, чтобы работало где угодно.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _http(url: str, timeout: int = 5, headers: dict[str, str] | None = None) -> tuple[int, str]:
    """Тихий GET. Возвращает (status_code, body) либо (-1, str(error))."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4096).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2048).decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e)


def _print_render_logs_if_possible(service_name: str) -> None:
    token = os.environ.get("RENDER_API_TOKEN", "").strip()
    if not token:
        print("    (RENDER_API_TOKEN не задан — лог можно посмотреть в UI Render-а)")
        return
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    code, body = _http(
        f"https://api.render.com/v1/services?name={service_name}&limit=1",
        timeout=8, headers=headers,
    )
    if code != 200:
        print(f"    (не смог получить service id, http {code})")
        return
    try:
        items = json.loads(body)
        sid = items[0]["service"]["id"] if items else None
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        sid = None
    if not sid:
        print(f"    (Render не нашёл сервис {service_name})")
        return
    code, body = _http(
        f"https://api.render.com/v1/services/{sid}/deploys?limit=1",
        timeout=8, headers=headers,
    )
    print("    Последний деплой:")
    print("    " + body[:1200].replace("\n", "\n    "))


def main() -> int:
    p = argparse.ArgumentParser(description="Ждать готовности сервиса блока на Render.")
    p.add_argument("service",
                   help="Имя сервиса как в render.yaml — например, raif-cib")
    p.add_argument("--url", default=None,
                   help="Полный URL для проверки. По умолчанию — https://<service>.onrender.com/health")
    p.add_argument("--timeout", type=int, default=240,
                   help="Сколько секунд ждать до отказа (по умолчанию 240)")
    p.add_argument("--interval", type=int, default=5,
                   help="Период опроса в секундах (по умолчанию 5)")
    args = p.parse_args()

    url = args.url or f"https://{args.service}.onrender.com/health"
    deadline = time.time() + args.timeout
    attempt = 0

    print(f"⏳ Жду пока {args.service} ответит на {url}")
    print(f"   таймаут {args.timeout}с, опрос каждые {args.interval}с")

    last_status: int | None = None
    while time.time() < deadline:
        attempt += 1
        elapsed = int(time.time() - (deadline - args.timeout))
        code, body = _http(url, timeout=args.interval)
        status_changed = code != last_status
        last_status = code

        if code == 200:
            print(f"✅ Готово за {elapsed}с (попытка {attempt}). HTTP 200.")
            print(f"   {body[:200]}")
            return 0

        if status_changed:
            if code == -1:
                print(f"   [{elapsed:>3}с] сервис ещё не отвечает: {body[:80]}")
            elif code in (502, 503, 504):
                print(f"   [{elapsed:>3}с] HTTP {code} — Render будит контейнер")
            elif code == 404:
                print(f"   [{elapsed:>3}с] HTTP 404 — сервис поднят, но /health ещё нет")
            else:
                print(f"   [{elapsed:>3}с] HTTP {code}: {body[:120]}")

        time.sleep(args.interval)

    print(f"❌ Таймаут — {args.service} не ответил 200 OK за {args.timeout}с.")
    _print_render_logs_if_possible(args.service)
    print(f"   Проверь руками: https://dashboard.render.com/  (раздел {args.service})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
