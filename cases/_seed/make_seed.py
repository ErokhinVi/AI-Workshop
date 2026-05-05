"""
Генерация стартовой синтетики банка для воркшопа правления.

500 клиентов, ~5000 транзакций, ~1500 записей кредитной истории.
Детерминированно (random.seed=42) — у разных топов на разных
ноутах будет одинаковая стартовая картина.

Запуск:
    python cases/_seed/make_seed.py

Кладёт результат рядом с собой в cases/_seed/*.jsonl
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 42
N_CLIENTS = 500
N_TRANSACTIONS = 5000
N_CREDIT_RECORDS = 1500

OUT = Path(__file__).resolve().parent

# --- словари для синтетики ---
FIRST_NAMES_M = [
    "Александр", "Андрей", "Алексей", "Дмитрий", "Иван", "Сергей", "Михаил",
    "Николай", "Павел", "Артём", "Кирилл", "Максим", "Олег", "Игорь", "Виктор",
    "Юрий", "Владимир", "Антон", "Тимур", "Илья",
]
FIRST_NAMES_F = [
    "Анна", "Мария", "Ольга", "Татьяна", "Екатерина", "Светлана", "Юлия",
    "Наталья", "Ирина", "Александра", "Виктория", "Дарья", "Елена", "Полина",
    "София", "Ксения", "Анастасия", "Карина", "Маргарита", "Алина",
]
LAST_NAMES = [
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Морозов", "Петров", "Соколов",
    "Лебедев", "Козлов", "Новиков", "Захаров", "Виноградов", "Богданов",
    "Воробьёв", "Фёдоров", "Романов", "Орлов", "Беляев", "Антонов", "Никитин",
]
SEGMENTS = [
    ("mass",            0.55, (40_000,   90_000),   (5_000,   200_000)),
    ("mass_affluent",   0.25, (90_000,   250_000),  (200_000, 1_500_000)),
    ("premium",         0.12, (250_000,  600_000),  (1_500_000, 8_000_000)),
    ("private",         0.05, (600_000,  2_000_000),(8_000_000, 50_000_000)),
    ("sme",             0.03, (200_000,  800_000),  (500_000, 20_000_000)),
]
PRODUCT_POOL = ["debit", "savings", "deposit", "mortgage", "auto_credit",
                "consumer_credit", "credit_card"]


def _pick_segment(rng: random.Random) -> tuple[str, tuple[int, int], tuple[int, int]]:
    r = rng.random()
    cumulative = 0.0
    for seg, w, income, balance in SEGMENTS:
        cumulative += w
        if r <= cumulative:
            return seg, income, balance
    seg, _, income, balance = SEGMENTS[-1]
    return seg, income, balance


def _client_name(rng: random.Random) -> tuple[str, str]:
    if rng.random() < 0.5:
        first = rng.choice(FIRST_NAMES_M)
        last = rng.choice(LAST_NAMES)
    else:
        first = rng.choice(FIRST_NAMES_F)
        last = rng.choice(LAST_NAMES) + "а"
    return first, last


def _pick_products(segment: str, rng: random.Random) -> list[str]:
    n = {"mass": 1, "mass_affluent": 2, "premium": 3, "private": 3, "sme": 2}[segment]
    return rng.sample(PRODUCT_POOL, n)


def _risk_score(segment: str, has_overdue: bool, rng: random.Random) -> float:
    base = {"mass": 0.32, "mass_affluent": 0.22, "premium": 0.15,
            "private": 0.10, "sme": 0.28}[segment]
    if has_overdue:
        base += rng.uniform(0.10, 0.30)
    return round(min(0.95, base + rng.uniform(-0.06, 0.06)), 3)


def gen_clients(rng: random.Random) -> list[dict]:
    today = datetime(2026, 5, 1)
    clients: list[dict] = []
    for i in range(N_CLIENTS):
        cid = f"c-{i + 1000:05d}"
        first, last = _client_name(rng)
        seg, income_range, balance_range = _pick_segment(rng)
        income = rng.randint(*income_range)
        balance = rng.randint(*balance_range)
        age = rng.randint(22, 72)
        products = _pick_products(seg, rng)
        has_overdue = rng.random() < {"mass": 0.18, "mass_affluent": 0.08,
                                      "premium": 0.04, "private": 0.02,
                                      "sme": 0.12}[seg]
        joined_days_ago = rng.randint(30, 365 * 5)
        clients.append({
            "id": cid,
            "first_name": first,
            "last_name": last,
            "name": f"{first} {last}",
            "age": age,
            "segment": seg,
            "income_rub": income,
            "balance_rub": balance,
            "products": products,
            "risk_score": _risk_score(seg, has_overdue, rng),
            "has_overdue_history": has_overdue,
            "joined_at": (today - timedelta(days=joined_days_ago)).date().isoformat(),
        })
    return clients


def gen_transactions(clients: list[dict], rng: random.Random) -> list[dict]:
    today = datetime(2026, 5, 1)
    types = ["transfer_out", "transfer_in", "card_purchase",
             "atm_withdraw", "salary", "utility_payment"]
    txs: list[dict] = []
    weights_by_seg = {"mass": 6, "mass_affluent": 12, "premium": 22,
                      "private": 30, "sme": 25}
    pool: list[str] = []
    for c in clients:
        pool.extend([c["id"]] * weights_by_seg[c["segment"]])
    for i in range(N_TRANSACTIONS):
        cid = rng.choice(pool)
        client = next(c for c in clients if c["id"] == cid)
        ttype = rng.choice(types)
        days_ago = rng.randint(0, 90)
        if ttype == "salary":
            amount = client["income_rub"]
            sign = 1
        elif ttype == "transfer_in":
            amount = rng.randint(500, 200_000)
            sign = 1
        else:
            amount = rng.randint(200, 80_000)
            sign = -1
        txs.append({
            "id": f"t-{i + 100000:08d}",
            "client_id": cid,
            "type": ttype,
            "amount_rub": sign * amount,
            "ts": (today - timedelta(days=days_ago,
                                     hours=rng.randint(0, 23),
                                     minutes=rng.randint(0, 59))).isoformat(),
        })
    txs.sort(key=lambda t: t["ts"])
    return txs


def gen_credit_history(clients: list[dict], rng: random.Random) -> list[dict]:
    today = datetime(2026, 5, 1)
    rows: list[dict] = []
    eligible = [c for c in clients
                if any(p in c["products"] for p in
                       ("mortgage", "auto_credit", "consumer_credit", "credit_card"))]
    for client in eligible:
        n_records = rng.randint(1, 6)
        for j in range(n_records):
            opened = today - timedelta(days=rng.randint(60, 365 * 4))
            term_months = rng.choice([6, 12, 24, 36, 60])
            principal = rng.randint(50_000, 5_000_000) if "mortgage" in client["products"] else \
                        rng.randint(20_000, 800_000)
            if client["has_overdue_history"]:
                overdue_days_max = rng.choice([0, 0, 5, 12, 30, 60, 90])
                status = "closed_with_overdue" if overdue_days_max > 0 else "active"
            else:
                overdue_days_max = 0
                status = rng.choice(["active", "closed_clean"])
            rows.append({
                "id": f"ch-{len(rows) + 1:06d}",
                "client_id": client["id"],
                "product": rng.choice([p for p in client["products"]
                                       if p in ("mortgage", "auto_credit",
                                                "consumer_credit", "credit_card")]),
                "principal_rub": principal,
                "term_months": term_months,
                "rate_pct": round(rng.uniform(7.5, 24.0), 2),
                "opened_at": opened.date().isoformat(),
                "status": status,
                "overdue_days_max": overdue_days_max,
            })
            if len(rows) >= N_CREDIT_RECORDS:
                return rows
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def main() -> None:
    rng = random.Random(SEED)
    clients = gen_clients(rng)
    transactions = gen_transactions(clients, rng)
    credit_history = gen_credit_history(clients, rng)

    write_jsonl(OUT / "clients.jsonl", clients)
    write_jsonl(OUT / "transactions.jsonl", transactions)
    write_jsonl(OUT / "credit_history.jsonl", credit_history)

    by_seg: dict[str, int] = {}
    for c in clients:
        by_seg[c["segment"]] = by_seg.get(c["segment"], 0) + 1
    print(f"clients: {len(clients)}  by segment: {by_seg}")
    print(f"transactions:   {len(transactions)}")
    print(f"credit_history: {len(credit_history)}")
    print(f"out: {OUT}")


if __name__ == "__main__":
    main()
