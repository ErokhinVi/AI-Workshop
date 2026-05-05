"""
Сканер репо: собирает live-снимок состояния воркшопа в один JSON-объект.

Только stdlib — нет ни fastapi, ни uvicorn. Это нужно чтобы:
- server.py мог импортировать build_state и обернуть его в FastAPI;
- build_static.py мог импортировать тот же build_state и сгенерировать
  state.json для GitHub Pages, не таща за собой fastapi на CI-runner.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# --- Конфигурация блоков ---------------------------------------------------

BLOCKS: dict[str, dict[str, Any]] = {
    "ceo": {
        "title": "CEO Office",
        "owner": "Сергей Монин",
        "color": "#FFE600",
        "port": 8000,
        "serviceUrl": "https://raif-ceo.onrender.com",
    },
    "cib": {
        "title": "CIB",
        "owner": "Никита Патрахин",
        "color": "#00A0DC",
        "port": 8010,
        "serviceUrl": "https://raif-cib.onrender.com",
    },
    "retail": {
        "title": "Розница",
        "owner": "Иван Курочкин",
        "color": "#E6007E",
        "port": 8020,
        "serviceUrl": "https://raif-retail.onrender.com",
    },
    "it": {
        "title": "IT / Платформа",
        "owner": "Александр Ложечкин",
        "color": "#7A29A2",
        "port": 8030,
        "serviceUrl": "https://raif-it.onrender.com",
    },
    "finance": {
        "title": "Финансы и Опс",
        "owner": "Герт Хебенштрайт",
        "color": "#00B26E",
        "port": 8040,
        "serviceUrl": "https://raif-finance.onrender.com",
    },
    "risk": {
        "title": "Риски",
        "owner": "Роланд Васс",
        "color": "#F26522",
        "port": 8050,
        "serviceUrl": "https://raif-risk.onrender.com",
    },
}

BLOCK_KEYS = list(BLOCKS.keys())

STATUS_ORDER = ["open", "replied", "agreed", "rejected", "done"]


# --- Сканер ----------------------------------------------------------------

@dataclass
class ScannerConfig:
    repo: Path
    do_fetch: bool = True


def _run(cmd: list[str], cwd: Path, timeout: int = 10) -> str:
    """Тихо запускает команду, возвращает stdout (или пустую строку)."""
    try:
        out = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return out.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def git_fetch(repo: Path) -> None:
    _run(["git", "fetch", "--all", "--quiet"], repo, timeout=15)


def _git_log(repo: Path, limit: int = 200) -> list[dict[str, Any]]:
    """git log --all --name-only — список коммитов с затронутыми файлами."""
    sep = "<<<COMMIT>>>"
    fmt = f"{sep}%H|%an|%ae|%at|%s"
    raw = _run(
        ["git", "log", "--all", f"-{limit}", f"--pretty=format:{fmt}", "--name-only"],
        repo,
    )
    commits: list[dict[str, Any]] = []
    if not raw:
        return commits
    for chunk in raw.split(sep):
        chunk = chunk.strip()
        if not chunk:
            continue
        head, *files = chunk.split("\n")
        try:
            sha, author, email, ts, *rest = head.split("|")
        except ValueError:
            continue
        subject = "|".join(rest)
        commits.append({
            "sha": sha[:8],
            "author": author,
            "email": email,
            "ts": int(ts) if ts.isdigit() else 0,
            "subject": subject,
            "files": [f for f in files if f.strip()],
        })
    return commits


def _block_of_path(path: str) -> str | None:
    """Понять, к какому блоку относится путь."""
    parts = path.split("/", 1)
    if not parts:
        return None
    head = parts[0]
    if head in BLOCKS:
        return head
    if head == "INBOX" and len(parts) > 1:
        m = re.match(r"to_([a-z]+)\.md", parts[1])
        if m and m.group(1) in BLOCKS:
            return f"inbox:{m.group(1)}"
    if head == "contracts":
        return "contracts"
    return None


def _block_stats(repo: Path) -> dict[str, dict[str, int]]:
    """Файлы / строки на блок (текущее состояние рабочей копии)."""
    out: dict[str, dict[str, int]] = {}
    for key in BLOCK_KEYS:
        folder = repo / key
        if not folder.exists():
            out[key] = {"files": 0, "lines": 0}
            continue
        files = 0
        lines = 0
        for p in folder.rglob("*"):
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts:
                files += 1
                try:
                    lines += sum(1 for _ in p.open("rb"))
                except OSError:
                    pass
        out[key] = {"files": files, "lines": lines}
    return out


_INBOX_MSG_RE = re.compile(
    r"---\s*\nfrom:\s*(?P<from>\w+)\s*\nto:\s*(?P<to>\w+)\s*\n"
    r"date:\s*(?P<date>[^\n]+)\nstatus:\s*(?P<status>\w+)\s*\n---\s*\n(?P<body>.*?)"
    r"(?=\n---\s*\nfrom:|\Z)",
    re.DOTALL,
)


def _inbox_messages(repo: Path) -> list[dict[str, Any]]:
    """Парсим все INBOX/to_*.md в плоский список сообщений.

    Возвращаем ПОЛНЫЕ тела (markdown как есть). Рендер делает фронт.
    """
    inbox = repo / "INBOX"
    if not inbox.exists():
        return []
    messages: list[dict[str, Any]] = []
    for f in sorted(inbox.glob("to_*.md")):
        m = re.match(r"to_(\w+)\.md", f.name)
        if not m:
            continue
        recipient = m.group(1)
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for hit in _INBOX_MSG_RE.finditer(text):
            messages.append({
                "from": hit.group("from"),
                "to": hit.group("to") or recipient,
                "date": hit.group("date").strip(),
                "status": hit.group("status").strip().lower(),
                "body": hit.group("body").strip(),  # full markdown
                "ts": _parse_date_safe(hit.group("date").strip()),
            })
    return messages


def _contracts(repo: Path) -> list[dict[str, Any]]:
    """Список файлов в contracts/ + последний автор по git log."""
    contracts_dir = repo / "contracts"
    if not contracts_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for p in sorted(contracts_dir.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        rel = p.relative_to(repo).as_posix()
        last = _run(
            ["git", "log", "-1", "--pretty=format:%an|%at|%s", "--", rel],
            repo,
        ).strip()
        author, ts, subject = "", 0, ""
        if last:
            try:
                a, t, *s = last.split("|")
                author, ts, subject = a, int(t) if t.isdigit() else 0, "|".join(s)
            except ValueError:
                pass
        items.append({
            "path": rel,
            "author": author,
            "ts": ts,
            "subject": subject,
        })
    return items


def _events(repo: Path) -> list[dict[str, Any]]:
    """Опциональный лог бизнес-событий: dashboard/events.jsonl."""
    f = repo / "dashboard" / "events.jsonl"
    if not f.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")


def _parse_date_safe(s: str) -> int:
    m = _DATE_RE.search(s or "")
    if not m:
        return 0
    try:
        return int(dt.datetime(*[int(x) for x in m.groups()]).timestamp())
    except (TypeError, ValueError):
        return 0


def _fetch_bank_state() -> dict[str, Any] | None:
    """Опционально — потянуть бизнес-метрики с живого CEO.

    Управляется env-переменной BANK_STATE_URL (например
    https://raif-ceo.onrender.com/dashboard). Если не задана или сервис
    не отвечает — возвращаем None, фронт просто не покажет этот блок.
    """
    url = os.environ.get("BANK_STATE_URL", "").strip()
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "raif-dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            if r.status != 200:
                return None
            data = json.loads(r.read(8192).decode("utf-8", errors="replace"))
            return data.get("bank_state") or None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None


def build_state(cfg: ScannerConfig) -> dict[str, Any]:
    """Собрать полный снимок для frontend-а."""
    if cfg.do_fetch:
        git_fetch(cfg.repo)

    commits = _git_log(cfg.repo, limit=300)
    stats = _block_stats(cfg.repo)
    inbox = _inbox_messages(cfg.repo)
    contracts = _contracts(cfg.repo)
    events = _events(cfg.repo)

    # Активность по блокам из коммитов: какие файлы менялись.
    block_commits: dict[str, int] = Counter()
    block_last_ts: dict[str, int] = defaultdict(int)
    activity: list[dict[str, Any]] = []
    contracts_touched_by: dict[str, set[str]] = defaultdict(set)

    for c in commits:
        touched_blocks: set[str] = set()
        for path in c["files"]:
            tag = _block_of_path(path)
            if tag is None:
                continue
            if tag.startswith("inbox:"):
                continue  # INBOX считаем отдельно через парсинг
            if tag == "contracts":
                contracts_touched_by[path].add(c["author"])
                continue
            touched_blocks.add(tag)
        for b in touched_blocks:
            block_commits[b] += 1
            if c["ts"] > block_last_ts[b]:
                block_last_ts[b] = c["ts"]
        if touched_blocks:
            activity.append({
                "kind": "commit",
                "ts": c["ts"],
                "author": c["author"],
                "subject": c["subject"],
                "blocks": sorted(touched_blocks),
                "sha": c["sha"],
            })

    for m in inbox:
        activity.append({
            "kind": "message",
            "ts": m.get("ts") or _parse_date_safe(m["date"]),
            "from": m["from"],
            "to": m["to"],
            "status": m["status"],
            "body": m["body"],
            "date": m["date"],
        })

    for e in events:
        activity.append({
            "kind": "event",
            "ts": e.get("ts", 0),
            "block": e.get("block", ""),
            "type": e.get("kind", ""),
            "text": e.get("text", ""),
        })

    activity.sort(key=lambda x: x.get("ts", 0), reverse=True)

    nodes: list[dict[str, Any]] = []
    for key, meta in BLOCKS.items():
        s = stats.get(key, {"files": 0, "lines": 0})
        nodes.append({
            "id": key,
            "title": meta["title"],
            "owner": meta["owner"],
            "color": meta["color"],
            "port": meta["port"],
            "serviceUrl": meta.get("serviceUrl"),
            "files": s["files"],
            "lines": s["lines"],
            "commits": block_commits.get(key, 0),
            "lastCommitTs": block_last_ts.get(key, 0),
        })

    edge_acc: dict[tuple[str, str], dict[str, Any]] = {}
    for m in inbox:
        a, b = m["from"], m["to"]
        if a not in BLOCKS or b not in BLOCKS:
            continue
        key = (a, b)
        e = edge_acc.setdefault(key, {
            "source": a, "target": b,
            "count": 0,
            "by_status": Counter(),
            "messages": [],
            "last_ts": 0,
        })
        e["count"] += 1
        e["by_status"][m["status"]] += 1
        e["messages"].append({
            "from": m["from"], "to": m["to"],
            "date": m["date"], "status": m["status"],
            "body": m["body"],
            "ts": m.get("ts") or _parse_date_safe(m["date"]),
        })
        if (m.get("ts") or 0) >= e["last_ts"]:
            e["last_ts"] = m.get("ts") or 0

    edges = []
    for (a, b), e in edge_acc.items():
        # сортируем тред по времени, свежие — первые
        msgs = sorted(e["messages"], key=lambda x: x["ts"], reverse=True)
        edges.append({
            "source": a,
            "target": b,
            "count": e["count"],
            "byStatus": dict(e["by_status"]),
            "messages": msgs,
            "lastTs": e["last_ts"],
            "lastBody": msgs[0]["body"] if msgs else "",
        })

    total_loc = sum(n["lines"] for n in nodes)
    total_files = sum(n["files"] for n in nodes)
    total_commits = len(commits)
    msg_total = len(inbox)
    msg_open = sum(1 for m in inbox if m["status"] == "open")
    msg_agreed = sum(1 for m in inbox if m["status"] in ("agreed", "done"))
    contracts_total = len(contracts)

    # % договорённостей от всех сообщений — единственная цифра
    # которая правда отражает «как работает правление».
    cooperation_pct = round(100 * msg_agreed / msg_total) if msg_total else 0

    # Активные блоки = были коммиты или сообщения за последние 10 минут.
    now = int(time.time())
    active: set[str] = set()
    for c in commits:
        if c["ts"] and now - c["ts"] < 600:
            for path in c["files"]:
                tag = _block_of_path(path)
                if tag in BLOCKS:
                    active.add(tag)
    for m in inbox:
        ts = m.get("ts") or _parse_date_safe(m["date"])
        if ts and now - ts < 600:
            if m["from"] in BLOCKS:
                active.add(m["from"])
            if m["to"] in BLOCKS:
                active.add(m["to"])

    return {
        "generatedAt": int(time.time()),
        "nodes": nodes,
        "edges": edges,
        "activity": activity[:60],
        "metrics": {
            "totalLoc": total_loc,
            "totalFiles": total_files,
            "totalCommits": total_commits,
            "inboxTotal": msg_total,
            "inboxOpen": msg_open,
            "inboxAgreed": msg_agreed,
            "contractsTotal": contracts_total,
            "cooperationPct": cooperation_pct,
            "activeBlocks": len(active),
            "totalBlocks": len(BLOCKS),
        },
        "bankState": _fetch_bank_state(),
        "contracts": contracts,
        "blocks": BLOCKS,
    }
