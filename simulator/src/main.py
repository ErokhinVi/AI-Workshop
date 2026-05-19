"""Симулятор клиентов AI-воркшопа — три блока на команду.

Pull-моделью опрашивает /health всех 6 банк-сервисов. Клиентская база каждой
команды — это запас, который двигают два потока:

* коммит-раунд — на новый git-коммит любого блока команды снимается probe всех
  трёх блоков, судья (LLM + fallback) оценивает рубрику и удобство, а формула
  переводит изменение «ценности банка для клиента» в дельту базы;
* тик застоя — если команда давно не выпускала обновлений, клиенты постепенно
  утекают к конкурентам.

Подробности модели — в src/scoring.py.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src import db as dbmod
from src.judge import judge_round
from src.probe import probe_team
from src.scoring import (
    B0,
    compute_commit_round,
    compute_decay,
    compute_unreachable,
    perceived_value,
    rubric_total,
)

BANK_URLS = {
    "team_a": {
        "retail": os.environ.get("A_RETAIL_URL", "http://localhost:8001").rstrip("/"),
        "cib": os.environ.get("A_CIB_URL", "http://localhost:8002").rstrip("/"),
        "backend": os.environ.get("A_BACKEND_URL", "http://localhost:8003").rstrip("/"),
    },
    "team_b": {
        "retail": os.environ.get("B_RETAIL_URL", "http://localhost:8011").rstrip("/"),
        "cib": os.environ.get("B_CIB_URL", "http://localhost:8012").rstrip("/"),
        "backend": os.environ.get("B_BACKEND_URL", "http://localhost:8013").rstrip("/"),
    },
}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))
# Событие застоя в ленту — не на каждый тик, а когда накопилось столько утечки.
DECAY_EVENT_THRESHOLD = float(os.environ.get("DECAY_EVENT_THRESHOLD", "10"))

TEAMS = ("team_a", "team_b")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_state() -> dict:
    """Состояние команды до первой оценки."""
    return {
        "client_base": float(B0),  # запас клиентов (float, округляем на границе)
        "last_commit": None,       # отпечаток коммитов трёх блоков
        "last_commit_ts": None,    # когда отпечаток последний раз менялся
        "last_eval_ts": None,      # когда последний раз применяли дельту
        "baseline_score": None,    # балл рубрики на старте (и признак инициализации)
        "last_score": None,        # балл рубрики прошлого раунда — для табло
        "last_value": 0.0,         # ценность банка прошлого раунда — для дельты
        "feature_state": None,     # стадия кредитной фичи — для табло
        "decay_pending": 0.0,      # накопленная утечка, ещё не показанная событием
    }


_state: dict[str, dict] = {t: _fresh_state() for t in TEAMS}
_events: list[dict] = []
_eval_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Лениво создать lock внутри работающего event loop (важно для тестов)."""
    global _eval_lock
    if _eval_lock is None:
        _eval_lock = asyncio.Lock()
    return _eval_lock


def _pool():
    return getattr(app.state, "pool", None)


def _commit_fingerprint(snapshot: dict) -> str:
    """Склейка git-коммитов трёх блоков — меняется на любой деплой команды."""
    blocks = snapshot.get("blocks", {})
    return "|".join(
        str(blocks.get(name, {}).get("commit")) for name in ("backend", "cib", "retail")
    )


async def _load_state() -> None:
    pool = _pool()
    if pool is None:
        return
    saved = await dbmod.get_state(pool)
    for team in TEAMS:
        if team in saved:
            row = _fresh_state()
            for key, val in saved[team].items():
                if val is not None:
                    row[key] = val
            _state[team] = row
    # Защита холодного старта Render: простой считаем с момента, как симулятор
    # снова поднялся, а не задним числом за весь сон сервиса.
    now = _now()
    for team in TEAMS:
        _state[team]["last_eval_ts"] = now
        if _state[team]["last_commit_ts"] is None:
            _state[team]["last_commit_ts"] = now
    _events.clear()
    _events.extend(await dbmod.recent_events(pool, limit=50))


async def _save_state(team: str) -> None:
    pool = _pool()
    if pool is None:
        return
    st = _state[team]
    await dbmod.upsert_state(
        pool, team, st["client_base"], st["last_commit"], st["baseline_score"],
        st["last_score"], st["last_commit_ts"], st["last_eval_ts"],
        st["last_value"],
    )


async def _emit_event(team: str, commit: str, delta: float, scores: list[int],
                      reason: str, judge: str, snapshot: dict | None = None) -> None:
    """Записать событие в журнал БД и в память (для табло)."""
    st = _state[team]
    base_after = round(st["client_base"])
    delta_i = round(delta)
    pool = _pool()
    if pool is not None:
        await dbmod.add_event(pool, team, commit, delta_i, base_after,
                              scores, reason, snapshot or {}, judge)
    _events.insert(0, {
        "team": team, "ts": None, "commit": commit, "delta": delta_i,
        "client_base_after": base_after, "rubric": scores,
        "reason": reason, "judge": judge,
    })
    del _events[60:]


async def _baseline() -> None:
    """Замерить стартовое состояние обеих команд по нетронутым блокам."""
    now = _now()
    snap_a = await probe_team("team_a", BANK_URLS["team_a"])
    snap_b = await probe_team("team_b", BANK_URLS["team_b"])
    verdict = await judge_round(snap_a, snap_b)
    for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
        v = verdict[team]
        st = _fresh_state()
        st["last_commit"] = _commit_fingerprint(snap)
        st["last_commit_ts"] = now
        st["last_eval_ts"] = now
        st["baseline_score"] = rubric_total(v["scores"])
        st["last_score"] = st["baseline_score"]
        st["last_value"] = perceived_value(v["scores"], v["feature_state"],
                                           v["convenience"])
        st["feature_state"] = v["feature_state"]
        _state[team] = st
        await _save_state(team)


async def evaluate_round(snap_a: dict | None = None, snap_b: dict | None = None,
                         committed: set | None = None) -> dict:
    """Коммит-раунд: probe + один вызов судьи + сдвиг базы команд.

    `committed` — какие команды двигать (по их новому коммиту); None — обе
    (ручной /admin/evaluate). Снапшоты можно передать готовыми, чтобы не
    снимать probe дважды за тик опроса.
    """
    async with _get_lock():
        if snap_a is None:
            snap_a = await probe_team("team_a", BANK_URLS["team_a"])
        if snap_b is None:
            snap_b = await probe_team("team_b", BANK_URLS["team_b"])
        if committed is None:
            committed = set(TEAMS)
        verdict = await judge_round(snap_a, snap_b)
        now = _now()
        out: dict[str, dict] = {}
        for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
            if team not in committed:
                continue
            st = _state[team]
            v = verdict[team]
            scores = v["scores"]
            reason = v["reason"]
            judge = v["judge"]
            fp = _commit_fingerprint(snap)
            all_down = all(
                not snap["blocks"].get(b, {}).get("reachable")
                for b in ("backend", "cib", "retail")
            )
            if all_down:
                r = compute_unreachable(st["client_base"])
                reason = "Все три блока банка недоступны — клиенты не могут войти."
                judge = "unreachable"
                # ценность не пересчитываем — измерить нечем
            else:
                value_now = perceived_value(scores, v["feature_state"],
                                            v["convenience"])
                r = compute_commit_round(value_now, st["last_value"],
                                         st["client_base"])
                st["last_value"] = value_now
                st["last_score"] = rubric_total(scores)
                st["feature_state"] = v["feature_state"]
            st["client_base"] = r["client_base"]
            st["last_commit"] = fp
            st["last_commit_ts"] = now
            st["last_eval_ts"] = now
            st["decay_pending"] = 0.0
            await _save_state(team)
            await _emit_event(team, fp, r["delta"], scores, reason, judge, snap)
            out[team] = {"delta": round(r["delta"]),
                         "client_base": round(st["client_base"]),
                         "reason": reason, "judge": judge,
                         "feature_state": st["feature_state"]}
        return out


async def _decay_tick(team: str, now: datetime) -> None:
    """Тик застоя для команды без нового коммита: клиенты понемногу утекают."""
    st = _state[team]
    if st["last_commit_ts"] is None:
        st["last_commit_ts"] = now
    if st["last_eval_ts"] is None:
        st["last_eval_ts"] = now
    idle_s = (now - st["last_commit_ts"]).total_seconds()
    slice_s = (now - st["last_eval_ts"]).total_seconds()
    r = compute_decay(st["client_base"], idle_s, slice_s)
    st["last_eval_ts"] = now
    if not r["changed"]:
        return
    st["client_base"] = r["client_base"]
    st["decay_pending"] += r["delta"]
    await _save_state(team)
    # Событие — только когда утечка накопилась заметно: лента не засоряется.
    if st["decay_pending"] <= -DECAY_EVENT_THRESHOLD:
        lost = round(-st["decay_pending"])
        idle_min = int(idle_s // 60)
        reason = (f"Команда {idle_min} мин не выпускала обновлений — "
                  f"{lost} клиентов ушли к конкурентам.")
        await _emit_event(team, _commit_fingerprint({}), st["decay_pending"],
                          [], reason, "stagnation")
        st["decay_pending"] = 0.0


async def _poll_loop() -> None:
    """Фон: раз в POLL_INTERVAL_S ловить деплой команд и точить застой."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            now = _now()
            snaps: dict[str, dict] = {}
            committed: set = set()
            for team in TEAMS:
                snap = await probe_team(team, BANK_URLS[team])
                snaps[team] = snap
                fp = _commit_fingerprint(snap)
                if "local" not in fp and "None" not in fp \
                        and fp != _state[team]["last_commit"]:
                    committed.add(team)
            if committed:
                await evaluate_round(snaps["team_a"], snaps["team_b"], committed)
            for team in TEAMS:
                if team not in committed:
                    await _decay_tick(team, now)
        except Exception as exc:  # noqa: BLE001
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


app = FastAPI(title="Симулятор клиентов", version="3.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "simulator", "db": _pool() is not None,
            "banks": BANK_URLS}


@app.get("/state")
async def state() -> dict:
    return {
        "teams": {t: {"client_base": round(s["client_base"]),
                      "last_score": s["last_score"],
                      "baseline_score": s["baseline_score"],
                      "feature_state": s["feature_state"]}
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
    for team in TEAMS:
        _state[team] = _fresh_state()
    await _baseline()
    return {"status": "reset",
            "teams": {t: round(_state[t]["client_base"]) for t in TEAMS}}
