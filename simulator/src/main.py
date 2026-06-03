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

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src import db as dbmod
from src import feature_probe as fpmod
from src.baseline import baseline_snapshot
from src.judge import feature_family, judge_round
from src.probe import assess_regression, probe_team
from src.scoring import (
    B0,
    RUBRIC_MAX,
    compute_commit_round,
    compute_decay,
    compute_unreachable,
    dead_feature_cost,
    feature_value,
    outage_cost,
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


def _bank_repos() -> dict[str, str]:
    """GitHub-репозиторий каждой команды из env `<P>_REPO` (например team_1).

    Нужен probe, чтобы читать CONTRACT.md через raw.githubusercontent.com. Если
    не задан — снимок деградирует мягко: контракт пустой, судья опирается на
    HTML + регрессию.
    """
    return {team: os.environ.get(f"{_team_prefix(team)}_REPO", "").strip()
            for team in TEAMS}


BANK_URLS = _bank_urls()
BANK_REPOS = _bank_repos()
# Пример задачи, который ведущий озвучивает командам (например «кредитная фича»).
# Подсказка судье, НЕ переключатель логики: команда может сделать что угодно.
ACTIVE_TASK = os.environ.get("ACTIVE_TASK", "").strip()
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
POLL_INTERVAL_S = float(os.environ.get("POLL_INTERVAL_S", "30"))
# Событие застоя в ленту — не на каждый тик, а когда накопилось столько утечки.
DECAY_EVENT_THRESHOLD = float(os.environ.get("DECAY_EVENT_THRESHOLD", "25"))
_NON_RELEASE_JUDGES = {"stagnation", "unreachable", "admin-set-base"}


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
        "feature_state": None,     # стадия добавленной фичи — для табло
        "decay_pending": 0.0,      # накопленная утечка, ещё не показанная событием
        "frozen": False,           # балл закреплён организатором — не переоцениваем
    }


_state: dict[str, dict] = {t: _fresh_state() for t in TEAMS}
# Baseline-снимки (нулевые точки) на команду: их diff показывает судье, что
# команда добавила. Снимаются в `_baseline()`, хранятся в памяти; при рестарте
# Render пересобираются при следующем старте/ресете воркшопа.
_baselines: dict[str, dict] = {}
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


def _axes_of(verdict: dict) -> tuple[int, int, int]:
    """Тройка осей ценности из вердикта судьи."""
    return (int(verdict.get("new_functionality", 0)),
            int(verdict.get("client_value", 0)),
            int(verdict.get("completeness", 0)))


def _regression_penalty(reg: dict) -> float:
    """Штраф за регрессию базовых функций в клиентах (через `outage_cost`).

    Сломанные базовые функции (переводы, отдача данных клиента) считаются как
    «выкаченные, но падающие ручки»; недоступные блоки — отдельно. Недоделанная
    новая фича (404) сюда не попадает — `assess_regression` её не помечает.
    """
    broken = int(bool(reg.get("transfers_broken"))) \
        + int(bool(reg.get("serves_client_broken")))
    return outage_cost(reg.get("unreachable_blocks", 0), broken)


def _probe_feature_families(fp_probe: dict | None) -> set[str]:
    """Сколько независимых семейств фич увидел feature_probe в diff контрактов."""
    endpoints = (fp_probe or {}).get("new_endpoints") or []
    families: set[str] = set()
    for endpoint in endpoints:
        if isinstance(endpoint, dict):
            path = str(endpoint.get("path", "") or "")
            if path:
                families.add(feature_family(path))
    return families


def _feature_live_for_scoring(fp_probe: dict | None) -> bool | None:
    """Smoke-check одной ручки не должен обнулять весь multi-feature релиз."""
    raw = (fp_probe or {}).get("feature_live")
    if raw is False and len(_probe_feature_families(fp_probe)) >= 2:
        return None
    return raw


def _convenience_bonus(verdict: dict) -> int:
    """Удобство 0..10 -> отдельный бонус 0..6 к сводной рубрике."""
    if verdict.get("feature_state") == "absent":
        return 0
    scored_keys = (
        "new_functionality", "client_value", "completeness", "cross_block",
        "backend_persistence", "feature_breadth", "ui_polish",
    )
    if not any(int(verdict.get(key, 0)) > 0 for key in scored_keys):
        return 0
    value = verdict.get("convenience", 5)
    if isinstance(value, bool):
        value = 5
    try:
        convenience = int(round(float(value)))
    except (TypeError, ValueError):
        convenience = 5
    convenience = max(0, min(10, convenience))
    return min(6, (convenience * 6 + 9) // 10)


def _rubric_of(verdict: dict) -> list[int]:
    """Сводка осей для табло/журнала."""
    a = _axes_of(verdict)
    return [
        a[0], a[1], a[2],
        int(verdict.get("cross_block", 0)),
        int(verdict.get("backend_persistence", 0)),
        int(verdict.get("feature_breadth", 0)),
        int(verdict.get("ui_polish", 0)),
        _convenience_bonus(verdict),
    ]


def _event_counts_from(events: list[dict]) -> dict[str, dict[str, int]]:
    """Посчитать релизы/застои по тем же правилам, что и SQL-агрегат."""
    release_commits: dict[str, set[str]] = {team: set() for team in TEAMS}
    counts: dict[str, dict[str, int]] = {
        team: {"releases": 0, "stagnations": 0} for team in TEAMS
    }
    for event in events:
        team = event.get("team")
        if team not in counts:
            continue
        judge = str(event.get("judge") or "")
        if judge == "stagnation":
            counts[team]["stagnations"] += 1
        if judge in _NON_RELEASE_JUDGES:
            continue
        commit = str(event.get("commit") or "").strip()
        if commit:
            release_commits[team].add(commit)
    for team, commits in release_commits.items():
        counts[team]["releases"] = len(commits)
    return counts


# Порог «значимого» сдвига ценности в клиентах — ниже считаем коммит
# функционально нейтральным и не выдумываем причину движения.
_VALUE_EPS = 1.0


def _compose_reason(*, prev_fs: str | None, cur_fs: str,
                    value_prev: float, value_now: float,
                    outage_labels: list[str], feature_reason: str,
                    feature_live: bool | None = None) -> str:
    """Собрать обоснование коммит-раунда для табло — человеческим языком.

    Основной текст — живое объяснение судьи (`feature_reason`) про саму фичу:
    что команда добавила и почему клиенты пришли или ушли. Детерминированные
    якоря остаются ради честности: на коммите без сдвига ценности и без смены
    стадии так и пишем «ничего не изменилось» (фичу впустую не нахваливаем), а
    факт поломки базовой функции (`outage_labels` из probe — судья её надёжно не
    видит) дописываем всегда. Аудитория — нетехнические руководители банка.

    `feature_live is False` (новую ручку дёрнули — она доказанно НЕ работает)
    перебивает любой хвалебный текст судьи: на табло честно пишем, что
    возможность показали, но воспользоваться ей нельзя.
    """
    moved_up = value_now > value_prev + _VALUE_EPS
    moved_down = value_now < value_prev - _VALUE_EPS
    state_changed = cur_fs != prev_fs
    if not (moved_up or moved_down or state_changed):
        return ("С прошлого шага в банке для клиентов ничего не изменилось — "
                "клиентская база на месте.")

    # Доказанно мёртвая новая фича (витрина без функциональности): клиент видит
    # возможность, но воспользоваться не может — независимо от того, что нахвалил
    # LLM по тексту контракта.
    if feature_live is False and cur_fs in ("working", "partial"):
        parts = ["Клиенты увидели новую возможность в приложении, но "
                 "воспользоваться ей пока нельзя — она не работает."]
        if outage_labels:
            parts.append("Важно: сломалось то, чем клиенты пользуются каждый "
                         "день — " + "; ".join(outage_labels) + ".")
        return " ".join(parts)

    # Подробный позитивный текст судьи показываем ТОЛЬКО когда база выросла —
    # иначе он противоречил бы оттоку («стало удобнее, и они ушли»). На спаде
    # ведём корректной по направлению фразой.
    gained = (cur_fs == "working" and prev_fs != "working") or moved_up
    lost = (prev_fs == "working" and cur_fs != "working") or moved_down
    human = feature_reason.strip()
    parts: list[str] = []
    if gained and not lost and human and human != "(без обоснования)":
        parts.append(human)
    elif cur_fs == "working" and prev_fs != "working":
        parts.append("Новая возможность заработала по-настоящему — клиенты пришли.")
    elif prev_fs == "working" and cur_fs != "working":
        parts.append("Ключевая возможность перестала работать — клиенты уходят.")
    elif moved_up:
        parts.append("Клиентам стало удобнее — их прибавилось.")
    else:
        parts.append("Клиентам стало неудобно, и часть из них ушла.")

    if outage_labels:
        parts.append("Важно: сломалось то, чем клиенты пользуются каждый день — "
                     + "; ".join(outage_labels) + ".")
    return " ".join(parts)


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
    # Закреплённый организатором балл переживает рестарт (флаг не в sim_state).
    for team in TEAMS:
        if (await dbmod.get_meta(pool, f"frozen_{team}")) == "true":
            _state[team]["frozen"] = True
    # Нулевая точка для оценки diff'а — детерминированный шаблон (см. src/baseline),
    # а НЕ probe-снимок: тот жил только в памяти и терялся при каждом редеплое, и
    # судья съезжал на пустой baseline (отсюда дёрганье табло). Встроенный шаблон
    # гарантирует корректный diff «что команда добавила поверх шаблона» при любом
    # старте процесса, без сети и состояния.
    for team in TEAMS:
        _baselines[team] = baseline_snapshot(team)
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
    """Зафиксировать нулевую точку (шаблон) и стартовое состояние всех команд.

    Нулевая точка — детерминированный шаблон (`baseline.baseline_snapshot`), от
    которого форкнулись все команды, а НЕ probe-снимок текущих банков: так diff
    «что команда добавила поверх шаблона» считается одинаково при любом старте
    процесса и переживает редеплой. Текущее состояние всё равно снимаем — чтобы
    зафиксировать стартовую точку каждой команды относительно шаблона
    (`baseline_score`, `last_value`): выкаченное ДО старта воркшопа в дельты потом
    не засчитывается (delta телескопируется от стартовой ценности).
    """
    now = _now()
    _baselines.clear()
    for team in TEAMS:
        _baselines[team] = baseline_snapshot(team)
    snaps = dict(zip(
        TEAMS,
        await asyncio.gather(*(
            probe_team(t, BANK_URLS[t], BANK_REPOS.get(t)) for t in TEAMS)),
    ))
    verdict = await judge_round(snaps, _baselines, active_task=ACTIVE_TASK)
    for team in TEAMS:
        snap = snaps[team]
        v = verdict[team]
        st = _fresh_state()
        st["last_commit"] = _commit_fingerprint(snap)
        st["last_commit_ts"] = now
        st["last_eval_ts"] = now
        st["baseline_score"] = rubric_total(_rubric_of(v))
        st["last_score"] = st["baseline_score"]
        reg = assess_regression(snap)
        st["last_value"] = feature_value(
            _axes_of(v), v["cross_block"], v["convenience"], v["feature_state"],
            outage_penalty=_regression_penalty(reg),
            backend_persistence=v.get("backend_persistence", 0),
            feature_breadth=v.get("feature_breadth", 0),
            ui_polish=v.get("ui_polish", 0))
        st["feature_state"] = v["feature_state"]
        _state[team] = st
        await _save_state(team)


async def _ensure_feature_probe(team: str, snap: dict) -> None:
    """Досчитать в снимок вердикт работоспособности новой фичи (`feature_probe`).

    Дорогую проверку (реальный вызов ручки + LLM-синтез тела) запускаем ТОЛЬКО
    когда в контракте появилась новая ручка vs baseline — иначе ставим `None`
    (поведение как сейчас). Если снимок уже несёт `feature_probe` (передан тестом
    или посчитан ранее) — не трогаем. Никогда не бросает: ошибка → `None`, чтобы
    сбой нашей проверки не штрафовал команду.
    """
    if "feature_probe" in snap:
        return
    snap["feature_probe"] = None
    baseline = _baselines.get(team)
    try:
        if not fpmod.discover_new_endpoints(snap, baseline):
            return
        async with httpx.AsyncClient(timeout=fpmod.LIVENESS_TIMEOUT_S) as client:
            snap["feature_probe"] = await fpmod.assess_feature_liveness(
                client, snap, baseline, BANK_URLS[team])
    except Exception as exc:  # noqa: BLE001 — fail-safe → None, без штрафа
        snap["feature_probe"] = None
        print(f"[simulator] feature-probe error ({team}): {exc!r}")


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
                snapshots[team] = await probe_team(
                    team, BANK_URLS[team], BANK_REPOS.get(team))
        if committed is None:
            committed = set(TEAMS)
        # Якорь «фича живая» — только для команд с реально новым коммитом:
        # дёргаем новую ручку и кладём вердикт в снимок до оценки судьёй. Тот же
        # коммит (ручной /admin/evaluate без деплоя) пропускаем — его всё равно
        # отсечёт no-op-гейт ниже, незачем жечь LLM и слать лишний вызов.
        for team in committed:
            snap = snapshots.get(team)
            if snap is None:
                continue
            unchanged = _commit_fingerprint(snap) == _state[team]["last_commit"]
            reachable = any(snap["blocks"].get(b, {}).get("reachable")
                            for b in ("backend", "cib", "retail"))
            if unchanged and reachable:
                continue
            await _ensure_feature_probe(team, snap)
        verdict = await judge_round({t: snapshots[t] for t in TEAMS},
                                    _baselines, active_task=ACTIVE_TASK)
        now = _now()
        out: dict[str, dict] = {}
        for team in TEAMS:
            if team not in committed:
                continue
            if _state[team].get("frozen"):
                continue   # балл закреплён организатором — не переоцениваем
            snap = snapshots[team]
            st = _state[team]
            v = verdict[team]
            rubric = _rubric_of(v)
            judge = v["judge"]
            # reason собираем ниже из фактов коммита (не из текста LLM):
            # пустой коммит — «без изменений», иначе — что именно поменялось/сломалось.
            reason = ""
            fp = _commit_fingerprint(snap)
            all_down = all(
                not snap["blocks"].get(b, {}).get("reachable")
                for b in ("backend", "cib", "retail")
            )
            if not all_down and fp == st["last_commit"]:
                # Повторная оценка ТОГО ЖЕ коммита (например, ручной
                # /admin/evaluate без нового деплоя): база НЕ двигается. Иначе
                # неизбежный шум LLM на идентичном снимке телескопировался бы в
                # скачки вверх-вниз (+168, потом −96 на том же коммите). Событие
                # не пишем и idle-таймер не сбрасываем — это не новый релиз.
                out[team] = {
                    "delta": 0,
                    "client_base": round(st["client_base"]),
                    "reason": ("С прошлого шага в банке для клиентов ничего не "
                               "изменилось — клиентская база на месте."),
                    "judge": judge,
                    "feature_state": st["feature_state"],
                }
                continue
            if all_down:
                r = compute_unreachable(st["client_base"])
                reason = "Все три блока банка недоступны — клиенты не могут войти."
                judge = "unreachable"
                # ценность не пересчитываем — измерить нечем
            else:
                # Регрессию базовых функций считаем детерминированно из probe —
                # не доверяем это LLM: сломанное (переводы/данные клиента,
                # недоступный блок) штрафуем, недоделанную новую фичу (404) — нет.
                reg = assess_regression(snap)
                # Якорь «фича живая»: если новую ручку дёрнули и она доказанно НЕ
                # работает (витрина), оси не дают ценности, а 5xx-витрина ещё и
                # штрафуется. None (проверить не смогли) — поведение как сейчас.
                fp_probe = snap.get("feature_probe") or {}
                feature_live = _feature_live_for_scoring(fp_probe)
                dead_pen = (dead_feature_cost(fp_probe.get("status"))
                            if feature_live is False else 0.0)
                value_now = feature_value(
                    _axes_of(v), v["cross_block"], v["convenience"],
                    v["feature_state"],
                    outage_penalty=_regression_penalty(reg) + dead_pen,
                    feature_live=feature_live,
                    backend_persistence=v.get("backend_persistence", 0),
                    feature_breadth=v.get("feature_breadth", 0),
                    ui_polish=v.get("ui_polish", 0))
                value_prev = st["last_value"]
                prev_fs = st["feature_state"]
                r = compute_commit_round(value_now, value_prev,
                                         st["client_base"])
                # Обоснование для табло: основной текст — живое человеческое
                # объяснение судьи про саму фичу (v["reason"]); детерминированные
                # якоря (no-op, мёртвая фича, поломка базовой функции) — поверх.
                reason = _compose_reason(
                    prev_fs=prev_fs, cur_fs=v["feature_state"],
                    value_prev=value_prev, value_now=value_now,
                    outage_labels=reg["labels"],
                    feature_reason=v["reason"], feature_live=feature_live)
                st["last_value"] = value_now
                st["last_score"] = rubric_total(rubric)
                st["feature_state"] = v["feature_state"]
            st["client_base"] = r["client_base"]
            st["last_commit"] = fp
            st["last_commit_ts"] = now
            st["last_eval_ts"] = now
            st["decay_pending"] = 0.0
            await _save_state(team)
            await _emit_event(team, fp, r["delta"], rubric, reason, judge, snap)
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
                if _state[team].get("frozen"):
                    continue   # балл закреплён организатором — не трогаем
                snap = await probe_team(team, BANK_URLS[team], BANK_REPOS.get(team))
                snaps[team] = snap
                fp = _commit_fingerprint(snap)
                if "local" not in fp and "None" not in fp \
                        and fp != _state[team]["last_commit"]:
                    committed.add(team)
            if committed:
                await evaluate_round(snaps, committed)
            for team in TEAMS:
                if _state[team].get("frozen") or team in committed:
                    continue
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


app = FastAPI(title="Симулятор клиентов", version="3.5.2-convbonus", lifespan=lifespan)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "simulator", "version": app.version,
            "db": _pool() is not None, "banks": BANK_URLS}


@app.get("/state")
async def state() -> dict:
    now = _now()
    teams_out: dict[str, dict] = {}
    pool = _pool()
    counts = (await dbmod.event_counts(pool)
              if pool is not None else _event_counts_from(_events))
    for t, s in _state.items():
        event_counts = counts.get(t, {"releases": 0, "stagnations": 0})
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
            "releases": event_counts["releases"],
            "stagnations": event_counts["stagnations"],
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


@app.post("/admin/set-base")
async def admin_set_base(team: str, base: float | None = None,
                         freeze: bool = True,
                         x_admin_token: str | None = Header(default=None)) -> dict:
    """Закрепить итоговый балл команды: выставить клиентскую базу и (по умолчанию)
    заморозить её — фоновый опрос и переоценка эту команду больше не трогают."""
    _check_admin(x_admin_token)
    if team not in _state:
        raise HTTPException(status_code=404, detail=f"неизвестная команда {team}")
    st = _state[team]
    old = round(st["client_base"])
    if base is not None:
        st["client_base"] = float(base)
    st["frozen"] = bool(freeze)
    st["last_commit_ts"] = _now()
    st["last_eval_ts"] = _now()
    await _save_state(team)
    pool = _pool()
    if pool is not None:
        await dbmod.set_meta(pool, f"frozen_{team}", "true" if freeze else "false")
    new = round(st["client_base"])
    if base is not None and new != old:
        reason = ("Клиенты высоко оценили новые возможности банка — за последнее "
                  "время клиентская база команды заметно выросла.")
        await _emit_event(team, st.get("last_commit") or "", new - old,
                          [2, 2, 2, 2, 2, 2, 2, 6], reason, "admin-set-base")
    return {"status": "ok", "team": team, "client_base": new,
            "frozen": st["frozen"]}
