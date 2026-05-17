"""Симулятор клиентов AI-воркшопа.

Pull-моделью опрашивает /health обоих банков; на новый git-коммит
снимает probe-снапшот, оценивает рубрикой (LLM-судья + fallback) и
детерминированной формулой двигает клиентскую базу команды.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src import db as dbmod
from src.judge import judge_round
from src.probe import probe_bank
from src.scoring import B0, compute_round, compute_unreachable, rubric_total

BANK_URLS = {
    "team_a": os.environ.get("BANK_A_URL", "http://localhost:8001").rstrip("/"),
    "team_b": os.environ.get("BANK_B_URL", "http://localhost:8002").rstrip("/"),
}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))

# in-memory зеркало состояния (используется и как кэш, и как fallback без БД)
_state: dict[str, dict] = {
    "team_a": {"client_base": B0, "last_commit": None, "baseline_score": None,
               "last_score": None},
    "team_b": {"client_base": B0, "last_commit": None, "baseline_score": None,
               "last_score": None},
}
_events: list[dict] = []
_eval_lock = asyncio.Lock()


def _pool():
    return getattr(app.state, "pool", None)


async def _load_state() -> None:
    """Поднять состояние из БД в память (или оставить дефолт)."""
    pool = _pool()
    if pool is None:
        return
    saved = await dbmod.get_state(pool)
    for team, row in saved.items():
        _state[team] = row
    _events.clear()
    _events.extend(await dbmod.recent_events(pool, limit=50))


async def _persist(team: str, snapshot: dict, scores: list[int], reason: str,
                   judge: str, delta: int) -> None:
    st = _state[team]
    pool = _pool()
    if pool is not None:
        await dbmod.upsert_state(pool, team, st["client_base"],
                                 st["last_commit"], st["baseline_score"],
                                 st["last_score"])
        await dbmod.add_event(pool, team, snapshot.get("commit"), delta,
                              st["client_base"], scores, reason, snapshot, judge)
    _events.insert(0, {
        "team": team, "ts": None, "commit": snapshot.get("commit"),
        "delta": delta, "client_base_after": st["client_base"],
        "rubric": scores, "reason": reason, "judge": judge,
    })
    del _events[60:]


async def _baseline() -> None:
    """Первый запуск: снять нетронутые банки и зафиксировать S_base."""
    snap_a = await probe_bank(BANK_URLS["team_a"])
    snap_b = await probe_bank(BANK_URLS["team_b"])
    verdict = await judge_round(snap_a, snap_b)
    for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
        s_base = rubric_total(verdict[team]["scores"])
        _state[team] = {
            "client_base": B0, "last_commit": snap.get("commit"),
            "baseline_score": s_base, "last_score": s_base,
        }
    pool = _pool()
    if pool is not None:
        for team in ("team_a", "team_b"):
            st = _state[team]
            await dbmod.upsert_state(pool, team, st["client_base"],
                                     st["last_commit"], st["baseline_score"],
                                     st["last_score"])


async def evaluate_round() -> dict:
    """Полный раунд: probe обоих банков, один вызов судьи, обновление баз."""
    async with _eval_lock:
        snap_a = await probe_bank(BANK_URLS["team_a"])
        snap_b = await probe_bank(BANK_URLS["team_b"])
        verdict = await judge_round(snap_a, snap_b)
        out: dict[str, dict] = {}
        for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
            st = _state[team]
            s_base = st["baseline_score"]
            if s_base is None:
                s_base = rubric_total(verdict[team]["scores"])
                st["baseline_score"] = s_base
                st["last_score"] = s_base
            scores = verdict[team]["scores"]
            reason = verdict[team]["reason"]
            judge = verdict[team]["judge"]
            if not snap.get("reachable"):
                r = compute_unreachable(st["client_base"])
                reason = "Банк не открывается — клиенты не могут войти."
                s_now = st["last_score"]
            else:
                s_now = rubric_total(scores)
                r = compute_round(s_now, st["last_score"], s_base,
                                  st["client_base"])
            st["client_base"] = r["client_base"]
            st["last_score"] = s_now
            st["last_commit"] = snap.get("commit")
            await _persist(team, snap, scores, reason, judge, r["delta"])
            out[team] = {"delta": r["delta"], "client_base": st["client_base"],
                         "reason": reason, "judge": judge}
        return out


async def _poll_loop() -> None:
    """Фон: раз в POLL_INTERVAL_S смотреть /health банков, ловить новый коммит."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            changed = False
            for team in ("team_a", "team_b"):
                snap = await probe_bank(BANK_URLS[team])
                commit = snap.get("commit")
                if commit and commit not in (None, "local") \
                        and commit != _state[team]["last_commit"]:
                    changed = True
            if changed:
                await evaluate_round()
        except Exception as exc:  # noqa: BLE001 — фон не должен падать
            print(f"[simulator] poll error: {exc!r}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    try:
        pool = await dbmod.init_pool()
        if pool is not None:
            await dbmod.ensure_schema(pool)
        app.state.pool = pool
        await _load_state()
        if _state["team_a"]["baseline_score"] is None:
            await _baseline()
    except Exception as exc:  # noqa: BLE001
        print(f"[simulator] init error: {exc!r}")
        app.state.pool = pool
    task = asyncio.create_task(_poll_loop())
    try:
        yield
    finally:
        task.cancel()
        if pool is not None:
            await pool.close()


app = FastAPI(title="Симулятор клиентов", version="1.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "simulator",
            "db": _pool() is not None, "banks": BANK_URLS}


@app.get("/state")
async def state() -> dict:
    return {
        "teams": {t: {"client_base": s["client_base"],
                      "last_score": s["last_score"],
                      "baseline_score": s["baseline_score"]}
                  for t, s in _state.items()},
        "events": _events[:30],
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    f = STATIC_DIR / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>Симулятор</h1>"


def _check_admin(token: str | None) -> None:
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="нужен корректный admin-токен")


@app.post("/admin/evaluate")
async def admin_evaluate(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    return await evaluate_round()


@app.post("/admin/reset")
async def admin_reset(x_admin_token: str | None = Header(default=None)) -> dict:
    _check_admin(x_admin_token)
    pool = _pool()
    if pool is not None:
        await dbmod.reset(pool)
    _events.clear()
    for team in ("team_a", "team_b"):
        _state[team] = {"client_base": B0, "last_commit": None,
                        "baseline_score": None, "last_score": None}
    await _baseline()
    return {"status": "reset", "state": _state}
