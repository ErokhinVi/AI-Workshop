"""
Локальный live-дашборд (FastAPI). Фоновый git fetch каждые 5 сек,
эндпоинты /api/state и /state.json.

Сканер сам по себе живёт в scan.py и не зависит от fastapi — это нужно
чтобы build_static.py мог собирать статику для GitHub Pages, не таща
fastapi на CI-runner.

Запуск:
    cd raif-bank-sandbox
    pip install fastapi 'uvicorn[standard]'
    python dashboard/server.py [--repo PATH] [--port 9000] [--no-fetch]
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scan import ScannerConfig, build_state, git_fetch


CONFIG: ScannerConfig | None = None
_FETCH_LOCK = asyncio.Lock()


async def _periodic_fetch() -> None:
    while True:
        if CONFIG and CONFIG.do_fetch:
            async with _FETCH_LOCK:
                await asyncio.to_thread(git_fetch, CONFIG.repo)
        await asyncio.sleep(5)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_periodic_fetch())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Raif Board AI Workshop Dashboard", lifespan=_lifespan)


async def _state() -> dict[str, Any]:
    assert CONFIG is not None
    return await asyncio.to_thread(build_state, CONFIG)


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(await _state())


@app.get("/state.json")
async def state_json() -> JSONResponse:
    """То же, что /api/state — нужно для парности с GitHub Pages-сборкой,
    где state.json лежит рядом с index.html как статический файл."""
    return JSONResponse(await _state())


@app.get("/")
async def root() -> FileResponse:
    here = Path(__file__).parent
    return FileResponse(here / "static" / "index.html")


def _mount_static() -> None:
    here = Path(__file__).parent
    static = here / "static"
    if static.exists():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")


_mount_static()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Путь к корню репо raif-bank-sandbox",
    )
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Не делать git fetch в фоне (для оффлайн-режима)",
    )
    args = parser.parse_args()

    global CONFIG
    CONFIG = ScannerConfig(repo=args.repo.resolve(), do_fetch=not args.no_fetch)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
