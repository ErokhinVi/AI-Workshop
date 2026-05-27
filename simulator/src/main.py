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
    RUBRIC_MAX,
    compute_commit_round,
    compute_decay,
    compute_unreachable,
    perceived_value,
    rubric_total,
)

# Команды и их URL-ы строятся из ENV. На воркшопе каждая команда живёт в
# своём GitHub-репозитории и деплоится своими тремя Render-сервисами;
# симулятор знает только URL-ы трёх блоков на команду.
#
# TEAM_NAMES — список через запятую. Для каждого имени берётся первая буква
# суффикса после "team_" в верхнем регистре (team_a → "A", team_c → "C") и
# по ней читаются три URL: <P>_RETAIL_URL, <P>_CIB_URL, <P>_BACKEND_URL.
# Дефолтные локальные порты разнесены по командам, чтобы docker-compose
# мог поднять все четыре без коллизий.
def _team_prefix(name: str) -> str:
    suffix = name.split("_", 1)[1] if "_" in name else name
    return suffix[0].upper() if suffix else "X"


def _default_port(team: str, block: str, *, base: int) -> int:
    # team_a → 0, team_b → 10, team_c → 20, team_d → 30; retail=+1, cib=+2, backend=+3
    offset = (ord(_team_prefix(team).lower()) - ord("a")) * 10
    block_offset = {"retail": 1, "cib": 2, "backend": 3}[block]
    return base + offset + block_offset


_BASE_PORT = int(os.environ.get("LOCAL_BANK_BASE_PORT", "8000"))

TEAMS: tuple[str, ...] = tuple(
    t.strip() for t in os.environ.get(
        "TEAM_NAMES", "team_a,team_b,team_c,team_d"
    ).split(",") if t.strip()
)


def _bank_urls() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for team in TEAMS:
        p = _team_prefix(team)
        out[team] = {
            block: os.environ.get(
                f"{p}_{block.upper()}_URL",
                f"http://localhost:{_default_port(team, block, base=_BASE_PORT)}",
            ).rstrip("/")
            for block in ("retail", "cib", "backend")
        }
    return out


BANK_URLS = _bank_urls()
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))
# Событие застоя в ленту — не на каждый тик, а когда накопилось столько утечки.
DECAY_EVENT_THRESHOLD = float(os.environ.get("DECAY_EVENT_THRESHOLD", "25"))


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
_workshop_started: bool = False
_workshop_started_at: datetime | None = None


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
    global _workshop_started, _workshop_started_at
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
    # Флаг «воркшоп начат» сохраняется в БД, чтобы переживать рестарты Render.
    started_raw = await dbmod.get_meta(pool, "workshop_started")
    started_at_raw = await dbmod.get_meta(pool, "workshop_started_at")
    _workshop_started = started_raw == "true"
    if started_at_raw:
        try:
            _workshop_started_at = datetime.fromisoformat(started_at_raw)
        except ValueError:
            _workshop_started_at = None


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
        "team": team, "ts": _now().isoformat(), "commit": commit, "delta": delta_i,
        "client_base_after": base_after, "rubric": scores,
        "reason": reason, "judge": judge,
    })
    del _events[60:]


async def _baseline() -> None:
    """Замерить стартовое состояние всех команд по нетронутым блокам."""
    now = _now()
    snaps = dict(zip(
        TEAMS,
        await asyncio.gather(*(probe_team(t, BANK_URLS[t]) for t in TEAMS)),
    ))
    verdict = await judge_round(snaps)
    for team in TEAMS:
        snap = snaps[team]
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


async def evaluate_round(snapshots: dict[str, dict] | None = None,
                         committed: set | None = None) -> dict:
    """Коммит-раунд: probe + один параллельный вызов судьи + сдвиг базы команд.

    `committed` — какие команды двигать (по их новому коммиту); None — все
    (ручной /admin/evaluate). Снапшоты можно передать готовыми (dict
    {team_name: snap}), чтобы не снимать probe дважды за тик опроса.
    """
    async with _get_lock():
        if snapshots is None:
            snapshots = {}
        for team in TEAMS:
            if team not in snapshots:
                snapshots[team] = await probe_team(team, BANK_URLS[team])
        if committed is None:
            committed = set(TEAMS)
        verdict = await judge_round({t: snapshots[t] for t in TEAMS})
        now = _now()
        out: dict[str, dict] = {}
        for team in TEAMS:
            if team not in committed:
                continue
            snap = snapshots[team]
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
    """Фон: раз в POLL_INTERVAL_S ловить деплой команд и точить застой.

    Пока организатор не нажал «Начать воркшоп», цикл крутится вхолостую:
    ни probe-запросов к банкам, ни обращений к LLM-судье. Это нужно для
    предстартового состояния, когда участники ещё не на местах, а Render
    уже поднял симулятор после деплоя.
    """
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        if not _workshop_started:
            continue
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
                await evaluate_round(snaps, committed)
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
        # Baseline-замер делаем только если воркшоп уже шёл и почему-то нет
        # снимка стартовых баллов. Если организатор ещё не нажал «Начать
        # воркшоп», ничего не трогаем — фон вхолостую крутится.
        if _workshop_started and _state[TEAMS[0]]["baseline_score"] is None:
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
    now = _now()
    teams_out: dict[str, dict] = {}
    for t, s in _state.items():
        team_events = [e for e in _events if e["team"] == t]
        releases = [e for e in team_events
                    if e["judge"] not in ("stagnation", "unreachable")]
        stagnations = [e for e in team_events if e["judge"] == "stagnation"]
        last_commit_ts = s["last_commit_ts"]
        idle_s = ((now - last_commit_ts).total_seconds()
                  if last_commit_ts else None)
        teams_out[t] = {
            "client_base": round(s["client_base"]),
            "client_base_start": B0,
            "delta_from_start": round(s["client_base"]) - B0,
            "last_score": s["last_score"],
            "baseline_score": s["baseline_score"],
            "rubric_max": RUBRIC_MAX,
            "feature_state": s["feature_state"],
            "releases": len(releases),
            "stagnations": len(stagnations),
            "idle_seconds": int(idle_s) if idle_s is not None else None,
            "last_commit_ts": (last_commit_ts.isoformat()
                               if last_commit_ts else None),
        }
    return {
        "now": now.isoformat(),
        "workshop_started": _workshop_started,
        "workshop_started_at": (_workshop_started_at.isoformat()
                                if _workshop_started_at else None),
        "teams": teams_out,
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
    if not _workshop_started:
        raise HTTPException(status_code=409,
                            detail="воркшоп ещё не начат — нажми «Начать воркшоп»")
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
    if _workshop_started:
        await _baseline()
    return {"status": "reset",
            "workshop_started": _workshop_started,
            "teams": {t: round(_state[t]["client_base"]) for t in TEAMS}}


@app.post("/admin/start")
async def admin_start(x_admin_token: str | None = Header(default=None)) -> dict:
    """Начать воркшоп: свежий baseline и опрос банков начинают работать."""
    _check_admin(x_admin_token)
    global _workshop_started, _workshop_started_at
    if _workshop_started:
        return {"status": "already_running",
                "started_at": (_workshop_started_at.isoformat()
                               if _workshop_started_at else None)}
    pool = _pool()
    if pool is not None:
        await dbmod.reset(pool)
    _events.clear()
    for team in TEAMS:
        _state[team] = _fresh_state()
    _workshop_started = True
    _workshop_started_at = _now()
    if pool is not None:
        await dbmod.set_meta(pool, "workshop_started", "true")
        await dbmod.set_meta(pool, "workshop_started_at",
                             _workshop_started_at.isoformat())
    await _baseline()
    return {"status": "started",
            "started_at": _workshop_started_at.isoformat(),
            "teams": {t: round(_state[t]["client_base"]) for t in TEAMS}}


@app.post("/admin/stop")
async def admin_stop(x_admin_token: str | None = Header(default=None)) -> dict:
    """Остановить воркшоп: опрос замирает, состояние сохраняется как есть."""
    _check_admin(x_admin_token)
    global _workshop_started
    _workshop_started = False
    pool = _pool()
    if pool is not None:
        await dbmod.set_meta(pool, "workshop_started", "false")
    return {"status": "stopped"}
