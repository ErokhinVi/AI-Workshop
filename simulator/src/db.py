"""Postgres-хранение симулятора: счёт команд и журнал событий.

Если DATABASE_URL не задан — init_pool возвращает None, и main.py
переходит на in-memory режим (для локального запуска без БД).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import asyncpg

# CREATE — для свежей БД; ALTER ... IF NOT EXISTS — догоняет БД прошлых
# прогонов воркшопа новыми колонками модели застоя.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sim_state (
    team           TEXT PRIMARY KEY,
    client_base    INT NOT NULL,
    last_commit    TEXT,
    baseline_score INT,
    last_score     INT,
    last_commit_ts TIMESTAMPTZ,
    last_eval_ts   TIMESTAMPTZ,
    last_value     DOUBLE PRECISION,
    updated_at     TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sim_events (
    id                BIGSERIAL PRIMARY KEY,
    team              TEXT,
    ts                TIMESTAMPTZ,
    commit            TEXT,
    delta             INT,
    client_base_after INT,
    rubric            JSONB,
    reason            TEXT,
    snapshot          JSONB,
    judge             TEXT
);
ALTER TABLE sim_state ADD COLUMN IF NOT EXISTS last_commit_ts TIMESTAMPTZ;
ALTER TABLE sim_state ADD COLUMN IF NOT EXISTS last_eval_ts   TIMESTAMPTZ;
ALTER TABLE sim_state ADD COLUMN IF NOT EXISTS last_value     DOUBLE PRECISION;
"""


async def init_pool() -> asyncpg.Pool | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    return await asyncpg.create_pool(url, min_size=1, max_size=4)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def get_state(pool: asyncpg.Pool) -> dict[str, dict]:
    """Вернуть состояние обеих команд: {team: {client_base, last_commit, ...}}."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM sim_state")
    return {
        r["team"]: {
            "client_base": r["client_base"],
            "last_commit": r["last_commit"],
            "baseline_score": r["baseline_score"],
            "last_score": r["last_score"],
            "last_commit_ts": r["last_commit_ts"],
            "last_eval_ts": r["last_eval_ts"],
            "last_value": r["last_value"],
        }
        for r in rows
    }


async def upsert_state(pool: asyncpg.Pool, team: str, client_base: float,
                       last_commit: str | None, baseline_score: int | None,
                       last_score: int | None, last_commit_ts: datetime | None,
                       last_eval_ts: datetime | None, last_value: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO sim_state(team, client_base, last_commit,
                   baseline_score, last_score, last_commit_ts, last_eval_ts,
                   last_value, updated_at)
               VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (team) DO UPDATE SET
                   client_base=$2, last_commit=$3, baseline_score=$4,
                   last_score=$5, last_commit_ts=$6, last_eval_ts=$7,
                   last_value=$8, updated_at=$9""",
            team, int(round(client_base)), last_commit, baseline_score,
            last_score, last_commit_ts, last_eval_ts, float(last_value),
            datetime.now(timezone.utc),
        )


async def add_event(pool: asyncpg.Pool, team: str, commit: str | None,
                    delta: float, client_base_after: float, rubric: list[int],
                    reason: str, snapshot: dict, judge: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO sim_events(team, ts, commit, delta,
                   client_base_after, rubric, reason, snapshot, judge)
               VALUES($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9)""",
            team, datetime.now(timezone.utc), commit, int(round(delta)),
            int(round(client_base_after)), json.dumps(rubric), reason,
            json.dumps(snapshot, ensure_ascii=False), judge,
        )


async def recent_events(pool: asyncpg.Pool, limit: int = 30) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT team, ts, commit, delta, client_base_after, rubric, "
            "reason, judge FROM sim_events ORDER BY id DESC LIMIT $1",
            limit,
        )
    return [
        {
            "team": r["team"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "commit": r["commit"],
            "delta": r["delta"],
            "client_base_after": r["client_base_after"],
            "rubric": json.loads(r["rubric"]) if r["rubric"] else [],
            "reason": r["reason"],
            "judge": r["judge"],
        }
        for r in rows
    ]


async def reset(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE sim_events")
        await conn.execute("TRUNCATE sim_state")
