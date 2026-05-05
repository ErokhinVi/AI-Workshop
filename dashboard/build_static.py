"""
Сборка статической версии дашборда для GitHub Pages.

Делает то же самое, что server.py /api/state — но один раз, на момент
запуска, и кладёт результат в `_site/state.json` рядом со скопированной
страницей `_site/index.html`.

Запускается из GitHub Action на каждый push в main:
    python dashboard/build_static.py --out _site

Frontend (`static/index.html`) фетчит `state.json` относительным путём,
так что одна и та же страница работает и из Pages (статика), и из
локального сервера `server.py` (там тот же роут).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Корень репо (по умолчанию — родитель папки dashboard/)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("_site"),
        help="Куда положить готовый сайт (по умолчанию ./_site)",
    )
    args = parser.parse_args()

    # Импортируем сканер из scan.py — модуль на чистом stdlib,
    # никакого fastapi/uvicorn для CI-runner-а не нужно.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scan import build_state, ScannerConfig  # noqa: E402

    repo = args.repo.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    # 1. Снимок состояния. do_fetch=False — внутри Action мы и так на
    #    свежем чекауте, и git fetch не нужен (и часто запрещён).
    state = build_state(ScannerConfig(repo=repo, do_fetch=False))

    state_path = out / "state.json"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    # 2. Копируем статику.
    static_src = Path(__file__).resolve().parent / "static"
    for item in static_src.iterdir():
        target = out / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    # 3. .nojekyll — чтобы Pages не делал из имён файлов с _ что-то странное.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    m = state["metrics"]
    print(
        f"Built dashboard:\n"
        f"  state.json    {state_path.stat().st_size:>7} bytes\n"
        f"  index.html    {(out / 'index.html').stat().st_size:>7} bytes\n"
        f"  nodes         {len(state['nodes'])}\n"
        f"  edges         {len(state['edges'])}\n"
        f"  activity      {len(state['activity'])}\n"
        f"  cooperation   {m.get('cooperationPct', 0)}%  ({m.get('inboxAgreed',0)}/{m.get('inboxTotal',0)})\n"
        f"  active blocks {m.get('activeBlocks', 0)}/{m.get('totalBlocks', 0)}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
