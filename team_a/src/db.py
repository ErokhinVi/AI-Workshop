"""Postgres-слой для блока Розница.

Если задан env DATABASE_URL — на старте создаём pool, схему,
сидим из jsonl при первом запуске. Все mutations (переводы,
заявки на кредит) пишутся в БД и переживают перезапуски контейнера.

Если DATABASE_URL не задан — pool=None, main.py откатывается на
in-memory режим (auto-seed-on-startup из jsonl).

Используем asyncpg напрямую (без ORM) — для нашего объёма (500 клиентов,
~5000 транзакций) этого больше чем достаточно, и код прозрачнее.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS retail_clients (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    segment      TEXT,
    balance_rub  BIGINT NOT NULL,
    income_rub   BIGINT,
    has_overdue_history BOOLEAN,
    data         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retail_clients_segment
    ON retail_clients(segment);
CREATE INDEX IF NOT EXISTS idx_retail_clients_overdue
    ON retail_clients(has_overdue_history);

CREATE TABLE IF NOT EXISTS retail_transactions (
    id           TEXT PRIMARY KEY,
    client_id    TEXT NOT NULL,
    type         TEXT NOT NULL,
    amount_rub   BIGINT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    counterparty TEXT
);
CREATE INDEX IF NOT EXISTS idx_retail_tx_client
    ON retail_transactions(client_id, ts DESC);

CREATE TABLE IF NOT EXISTS retail_credit_applications (
    id           BIGSERIAL PRIMARY KEY,
    client_id    TEXT NOT NULL,
    amount_rub   BIGINT,
    term_months  INT,
    status       TEXT NOT NULL DEFAULT 'received',
    decision     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_retail_ca_client
    ON retail_credit_applications(client_id, created_at DESC);
"""


async def init_pool() -> asyncpg.Pool | None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    return await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=5,
        statement_cache_size=0,  # совместимость с pgbouncer / managed PG
        command_timeout=15,
    )


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool, seed_dir: Path) -> dict[str, int]:
    """Если таблицы пустые — заполнить из jsonl. Идемпотентно."""
    summary = {"clients": 0, "transactions": 0}
    async with pool.acquire() as conn:
        cnt = await conn.fetchval("SELECT COUNT(*) FROM retail_clients")
        if cnt == 0 and (seed_dir / "clients.jsonl").exists():
            rows = []
            with (seed_dir / "clients.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    c = json.loads(line)
                    rows.append((
                        c["id"],
                        c.get("name"),
                        c.get("segment"),
                        int(c.get("balance_rub", 0)),
                        int(c.get("income_rub", 0)) if c.get("income_rub") else None,
                        bool(c.get("has_overdue_history", False)),
                        json.dumps(c, ensure_ascii=False),
                    ))
            await conn.executemany(
                "INSERT INTO retail_clients(id, name, segment, balance_rub, "
                "income_rub, has_overdue_history, data) "
                "VALUES($1, $2, $3, $4, $5, $6, $7::jsonb)",
                rows,
            )
            summary["clients"] = len(rows)

        cnt = await conn.fetchval("SELECT COUNT(*) FROM retail_transactions")
        if cnt == 0 and (seed_dir / "transactions.jsonl").exists():
            rows = []
            with (seed_dir / "transactions.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    t = json.loads(line)
                    rows.append((
                        t["id"], t["client_id"], t["type"],
                        int(t["amount_rub"]),
                        datetime.fromisoformat(t["ts"]),
                        t.get("counterparty"),
                    ))
            # ON CONFLICT DO NOTHING — на случай повторных стартов
            await conn.executemany(
                "INSERT INTO retail_transactions(id, client_id, type, "
                "amount_rub, ts, counterparty) "
                "VALUES($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                rows,
            )
            summary["transactions"] = len(rows)
    return summary


# ----- helpers для main.py -----

async def list_clients(
    pool: asyncpg.Pool,
    segment: str | None = None,
    has_overdue: bool | None = None,
    min_income: int | None = None,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    where: list[str] = []
    args: list[Any] = []
    if segment is not None:
        where.append(f"segment = ${len(args) + 1}")
        args.append(segment)
    if has_overdue is not None:
        where.append(f"has_overdue_history = ${len(args) + 1}")
        args.append(has_overdue)
    if min_income is not None:
        where.append(f"income_rub >= ${len(args) + 1}")
        args.append(min_income)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM retail_clients{where_sql}", *args)
        # SELECT data — содержит весь объект клиента
        rows = await conn.fetch(
            f"SELECT data FROM retail_clients{where_sql} ORDER BY id LIMIT {limit}",
            *args,
        )
        items = [json.loads(r["data"]) for r in rows]
        # обновим balance_rub в data из текущей колонки (на случай дрейфа)
        # — в нашем pipeline это не должно быть нужно, но безопаснее
        return total, items


async def get_client(pool: asyncpg.Pool, client_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT data, balance_rub FROM retail_clients WHERE id = $1",
            client_id,
        )
        if not row:
            return None
        c = json.loads(row["data"])
        c["balance_rub"] = int(row["balance_rub"])
        return c


async def get_transactions(
    pool: asyncpg.Pool, client_id: str, limit: int = 20,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, client_id, type, amount_rub, ts, counterparty "
            "FROM retail_transactions WHERE client_id = $1 "
            "ORDER BY ts DESC LIMIT $2",
            client_id, limit,
        )
        return [
            {
                "id": r["id"],
                "client_id": r["client_id"],
                "type": r["type"],
                "amount_rub": int(r["amount_rub"]),
                "ts": r["ts"].isoformat(),
                "counterparty": r["counterparty"],
            }
            for r in rows
        ]


async def transfer(
    pool: asyncpg.Pool,
    from_id: str,
    to_query: str,
    amount: int,
) -> dict[str, Any]:
    """Атомарный перевод. Кидает ValueError для бизнес-ошибок."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            sender = await conn.fetchrow(
                "SELECT id, name, balance_rub FROM retail_clients "
                "WHERE id = $1 FOR UPDATE",
                from_id,
            )
            if not sender:
                raise ValueError("отправитель не найден")
            if amount <= 0:
                raise ValueError("укажи положительную сумму")
            if amount > int(sender["balance_rub"]):
                raise ValueError(
                    f"недостаточно средств: на счёте {sender['balance_rub']} ₽, "
                    f"запрошено {amount} ₽"
                )

            # ищем получателя
            receiver = None
            if to_query and to_query != from_id:
                receiver = await conn.fetchrow(
                    "SELECT id, name, balance_rub FROM retail_clients "
                    "WHERE id = $1 OR LOWER(name) = LOWER($2) "
                    "OR LOWER(name) LIKE '%' || LOWER($2) || '%' "
                    "LIMIT 1 FOR UPDATE",
                    to_query, to_query,
                )
                if receiver and receiver["id"] == from_id:
                    receiver = None

            now = datetime.now()
            new_sender_balance = int(sender["balance_rub"]) - amount
            await conn.execute(
                "UPDATE retail_clients SET balance_rub = $1, "
                "data = jsonb_set(data, '{balance_rub}', to_jsonb($2::bigint)) "
                "WHERE id = $3",
                new_sender_balance, new_sender_balance, from_id,
            )

            # счётчик для уникальных id транзакций
            tx_count = await conn.fetchval("SELECT COUNT(*) FROM retail_transactions")
            out_id = f"t-{100000 + tx_count + 1:08d}"

            counterparty = receiver["name"] if receiver else to_query
            await conn.execute(
                "INSERT INTO retail_transactions(id, client_id, type, "
                "amount_rub, ts, counterparty) "
                "VALUES($1, $2, 'transfer_out', $3, $4, $5)",
                out_id, from_id, -amount, now, counterparty,
            )

            kind = "external"
            if receiver:
                new_recv_balance = int(receiver["balance_rub"]) + amount
                await conn.execute(
                    "UPDATE retail_clients SET balance_rub = $1, "
                    "data = jsonb_set(data, '{balance_rub}', to_jsonb($2::bigint)) "
                    "WHERE id = $3",
                    new_recv_balance, new_recv_balance, receiver["id"],
                )
                in_id = f"t-{100000 + tx_count + 2:08d}"
                await conn.execute(
                    "INSERT INTO retail_transactions(id, client_id, type, "
                    "amount_rub, ts, counterparty) "
                    "VALUES($1, $2, 'transfer_in', $3, $4, $5)",
                    in_id, receiver["id"], amount, now, sender["name"],
                )
                kind = "internal"
                recipient_label = receiver["name"]
            else:
                recipient_label = to_query

            return {
                "status": "ok",
                "kind": kind,
                "amount_rub": amount,
                "to": recipient_label,
                "from_client_id": from_id,
                "new_balance_rub": new_sender_balance,
                "tx_id": out_id,
                "ts": now.replace(microsecond=0).isoformat(),
            }


async def add_credit_application(
    pool: asyncpg.Pool,
    client_id: str,
    amount_rub: int | None,
    term_months: int | None,
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO retail_credit_applications(client_id, amount_rub, term_months) "
            "VALUES($1, $2, $3) RETURNING id, status, created_at",
            client_id, amount_rub, term_months,
        )
        return {
            "id": row["id"],
            "client_id": client_id,
            "amount_rub": amount_rub,
            "term_months": term_months,
            "status": row["status"],
            "created_at": row["created_at"].isoformat(),
        }


async def list_credit_applications(pool: asyncpg.Pool) -> tuple[int, list[dict[str, Any]]]:
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM retail_credit_applications")
        rows = await conn.fetch(
            "SELECT id, client_id, amount_rub, term_months, status, "
            "decision, created_at "
            "FROM retail_credit_applications ORDER BY created_at DESC LIMIT 100"
        )
        return total, [
            {
                "id": r["id"],
                "client_id": r["client_id"],
                "amount_rub": r["amount_rub"],
                "term_months": r["term_months"],
                "status": r["status"],
                "decision": r["decision"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
