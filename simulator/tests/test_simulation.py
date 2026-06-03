"""Приёмочный тест симулятора — правила правдоподобия клиентов (generic):

1. пустой коммит (контракты не менялись) → база не двигается;
2. команда давно не коммитит → клиенты утекают;
3. фича работает, но сделана криво → клиенты уходят;
4. фича работает и удобна → клиенты приходят;
5. регрессия базовой функции → клиенты уходят, даже без новой фичи.

Проверяется и путь с LLM-судьёй (мок), и скриптовый fallback (без LLM) — чтобы
воркшоп пережил отсутствующий или мёртвый OPENAI_API_KEY. Кредит больше не
привилегирован: фича распознаётся по diff контрактов, не по кредитным ручкам.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src import llm
from src import main as m
from src.scoring import B0

FULL_AXES = (2, 2, 2)


def _baseline_snap(team: str, contract: str = "base") -> dict:
    """Нулевая точка команды: одинаковый контракт во всех блоках."""
    return {"team": team, "blocks": {
        n: {"reachable": True, "commit": "c0", "contract": contract,
            "checks": ({"transfer_ok": True} if n == "retail"
                       else {"serves_client": True} if n == "backend" else {}),
            **({"html": ""} if n == "retail" else {})}
        for n in ("backend", "cib", "retail")}}


def _reset(now: datetime, *, baseline_contract: str = "base") -> None:
    """Все команды — в стартовом состоянии, оценённом раунд назад."""
    m._eval_lock = None          # пересоздать lock в loop текущего asyncio.run
    m._events.clear()
    m._baselines.clear()
    for team in m.TEAMS:
        st = m._fresh_state()
        st["last_commit"] = "old"
        st["last_commit_ts"] = now
        st["last_eval_ts"] = now
        st["baseline_score"] = 4
        st["last_score"] = 4
        st["last_value"] = 0.0
        m._state[team] = st
        m._baselines[team] = _baseline_snap(team, baseline_contract)


def _mock_snap(team: str, *, contract: str = "base",
               transfer_ok: bool = True, serves_client: bool = True) -> dict:
    """Достижимый снимок; вердикт даёт мок судьи, регрессия — из checks."""
    return {"team": team, "blocks": {
        "backend": {"reachable": True, "commit": "c1", "contract": contract,
                    "checks": {"serves_client": serves_client}},
        "cib": {"reachable": True, "commit": "c1", "contract": contract,
                "checks": {}},
        "retail": {"reachable": True, "commit": "c1", "contract": contract,
                   "html": "", "checks": {"transfer_ok": transfer_ok}}},
        "regression": {}}


def _patch_judge(monkeypatch, feature_state: str, convenience: int,
                 axes: tuple = FULL_AXES, cross_block: int = 2) -> None:
    block = {"new_functionality": axes[0], "client_value": axes[1],
             "completeness": axes[2], "cross_block": cross_block,
             "convenience": convenience, "feature_state": feature_state,
             "reason": "тест", "judge": "llm"}

    async def fake_judge(snaps, baselines=None, *, active_task=""):
        return {team: dict(block) for team in m.TEAMS}

    monkeypatch.setattr(m, "judge_round", fake_judge)


def _run_commit(team: str = "team_a", **snap_kw) -> dict:
    snaps = {t: _mock_snap(t, **snap_kw) for t in m.TEAMS}
    out = asyncio.run(m.evaluate_round(snaps, {team}))
    return out[team]


# --- путь с LLM-судьёй (мок) -------------------------------------------------

def test_rubric_includes_convenience_bonus_to_reach_twenty():
    verdict = {"new_functionality": 2, "client_value": 2, "completeness": 2,
               "cross_block": 2, "backend_persistence": 2,
               "feature_breadth": 2, "ui_polish": 2, "convenience": 9,
               "feature_state": "working"}
    rubric = m._rubric_of(verdict)
    assert rubric == [2, 2, 2, 2, 2, 2, 2, 6]
    assert m.rubric_total(rubric) == 20


def test_convenience_bonus_is_capped_and_sanitized():
    base = {"new_functionality": 1, "feature_state": "partial"}
    assert m._convenience_bonus({**base, "convenience": -1}) == 0
    assert m._convenience_bonus({**base, "convenience": 4}) == 3
    assert m._convenience_bonus({**base, "convenience": 10}) == 6
    assert m._convenience_bonus({**base, "convenience": True}) == 3
    assert m._convenience_bonus({**base, "convenience": "wat"}) == 3
    assert m._convenience_bonus({"convenience": 10}) == 0
    assert m._convenience_bonus({**base, "feature_state": "absent",
                                 "convenience": 10}) == 0


def test_rule1_empty_commit_no_movement(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "absent", 5, axes=(0, 0, 0), cross_block=0)
    res = _run_commit()
    assert res["delta"] == 0
    assert round(m._state["team_a"]["client_base"]) == B0


def test_rule3_clunky_working_feature_loses_clients(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 1)   # работает, но криво
    res = _run_commit()
    assert res["delta"] < 0
    assert m._state["team_a"]["client_base"] < B0


def test_rule4_convenient_working_feature_gains_clients(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)   # работает и удобно
    res = _run_commit()
    assert res["delta"] > 0
    assert m._state["team_a"]["client_base"] > B0


def test_working_feature_gains_clients(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit()
    assert res["delta"] > 0


def test_llm_feature_reason_shown_on_board(monkeypatch):
    # живое объяснение судьи про фичу попадает на табло как основной текст,
    # а не подменяется генеричной детерминированной фразой
    _reset(datetime.now(timezone.utc))

    async def fake_judge(snaps, baselines=None, *, active_task=""):
        block = {"new_functionality": 2, "client_value": 2, "completeness": 2,
                 "cross_block": 2, "convenience": 9, "feature_state": "working",
                 "reason": "В приложении появилась кредитная карта — клиенты "
                           "оформляют её за минуту, не приходя в отделение.",
                 "judge": "llm"}
        return {team: dict(block) for team in m.TEAMS}

    monkeypatch.setattr(m, "judge_round", fake_judge)
    res = _run_commit()
    assert "кредитная карта" in res["reason"]
    assert "оформляют её за минуту" in res["reason"]


def _run_commit_with_probe(feature_probe: dict | None, team: str = "team_a",
                           **snap_kw) -> dict:
    snaps = {t: _mock_snap(t, **snap_kw) for t in m.TEAMS}
    snaps[team]["feature_probe"] = feature_probe
    out = asyncio.run(m.evaluate_round(snaps, {team}))
    return out[team]


def test_dead_feature_404_gives_no_client_gain(monkeypatch):
    # судья по тексту контракта нахвалил рабочую удобную фичу, но реальный вызов
    # новой ручки дал 404 (витрина) → клиентов НЕ прибавляем
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit_with_probe({"feature_live": False, "status": 404,
                                  "primary": {"method": "POST",
                                              "path": "/api/credit-apply"}})
    assert res["delta"] == 0
    assert round(m._state["team_a"]["client_base"]) == B0
    assert "не работает" in res["reason"]


def test_dead_probe_does_not_suppress_multi_feature_release(monkeypatch):
    # feature_probe дёрнул одну ручку и получил 404, но в diff есть несколько
    # независимых фич. Такой smoke-fail не должен обнулять ценность всего релиза.
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit_with_probe({
        "feature_live": False,
        "status": 404,
        "primary": {"method": "POST", "path": "/api/brokerage/orders"},
        "new_endpoints": [
            {"block": "retail", "method": "POST", "path": "/api/brokerage/orders"},
            {"block": "retail", "method": "POST", "path": "/api/deposit/open"},
            {"block": "retail", "method": "POST", "path": "/api/loan/disburse"},
            {"block": "retail", "method": "POST", "path": "/api/payroll/run"},
        ],
    })
    assert res["delta"] > 0
    assert "не работает" not in res["reason"]


def test_dead_feature_5xx_loses_clients(monkeypatch):
    # ручка выкачена, но падает (5xx) на глазах клиента → база уходит в минус
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit_with_probe({"feature_live": False, "status": 500,
                                  "primary": {"method": "POST",
                                              "path": "/api/credit-apply"}})
    assert res["delta"] < 0
    assert m._state["team_a"]["client_base"] < B0
    assert "не работает" in res["reason"]


def test_feature_live_true_keeps_gain(monkeypatch):
    # ручку дёрнули — реально работает → ценность как обычно, клиенты приходят
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit_with_probe({"feature_live": True, "status": 200,
                                  "primary": {"method": "POST", "path": "/x"}})
    assert res["delta"] > 0


def test_feature_live_none_does_not_suppress_gain(monkeypatch):
    # проверить работоспособность не удалось (None) → НЕ штрафуем, как сейчас
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    res = _run_commit_with_probe({"feature_live": None, "status": None,
                                  "primary": None})
    assert res["delta"] > 0


def test_reeval_same_commit_emits_no_event(monkeypatch):
    # повторная оценка ТОГО ЖЕ коммита (ручной /admin/evaluate без нового
    # деплоя) не двигает базу и не пишет событие — фикс двойного скоринга
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    first = _run_commit()
    assert first["delta"] > 0
    base_after = m._state["team_a"]["client_base"]
    n_events = len([e for e in m._events if e["team"] == "team_a"])
    again = _run_commit()
    assert again["delta"] == 0
    assert m._state["team_a"]["client_base"] == base_after
    assert "ничего не изменилось" in again["reason"]
    assert len([e for e in m._events if e["team"] == "team_a"]) == n_events


def test_reason_on_decline_not_contradictory():
    # на спаде базы НЕ показываем позитивный текст судьи (без «удобно → ушли»)
    r = m._compose_reason(
        prev_fs="working", cur_fs="working", value_prev=200.0, value_now=80.0,
        outage_labels=[], feature_reason="Клиентам очень удобно, они в восторге.")
    assert "в восторге" not in r
    assert "ушл" in r.lower() or "неудоб" in r.lower()


def test_unchanged_commit_does_not_spam_clients(monkeypatch):
    # рабочую удобную фичу коммитят повторно без изменений ценности —
    # первый коммит приводит клиентов, а второй (та же ценность) НЕ двигает базу
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    first = _run_commit()
    assert first["delta"] > 0
    base_after_first = m._state["team_a"]["client_base"]
    second = _run_commit()
    assert second["delta"] == 0
    assert m._state["team_a"]["client_base"] == base_after_first
    assert "ничего не изменилось" in second["reason"]


def test_regression_penalizes_even_working_feature(monkeypatch):
    # фича работает и удобна, но сломались переводы — регрессия бьёт по базе
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    clean = _run_commit(transfer_ok=True)
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9)
    broken = _run_commit(transfer_ok=False)
    assert broken["delta"] < clean["delta"]
    assert "клиенты пользуются каждый день" in broken["reason"]


def test_rule2_stagnation_leaks_clients():
    now = datetime.now(timezone.utc)
    _reset(now)
    # команда не коммитила 90 минут — далеко за гранью прощения
    m._state["team_a"]["last_commit_ts"] = now - timedelta(minutes=90)
    m._state["team_a"]["last_eval_ts"] = now - timedelta(minutes=1)
    before = m._state["team_a"]["client_base"]
    asyncio.run(m._decay_tick("team_a", now))
    assert m._state["team_a"]["client_base"] < before


def test_stagnation_emits_coalesced_event():
    now = datetime.now(timezone.utc)
    _reset(now)
    m._state["team_a"]["last_commit_ts"] = now - timedelta(hours=3)
    moment = now
    for _ in range(40):                         # 40 тиков по 60 c
        m._state["team_a"]["last_eval_ts"] = moment - timedelta(seconds=60)
        asyncio.run(m._decay_tick("team_a", moment))
        moment += timedelta(seconds=60)
    stagnation = [e for e in m._events if e["judge"] == "stagnation"]
    assert stagnation, "ожидалось событие застоя в ленте"
    assert stagnation[0]["delta"] < 0


def test_state_counts_distinct_releases_not_recent_event_noise():
    now = datetime.now(timezone.utc)
    _reset(now)
    m.app.state.pool = None
    m._events.extend([
        {"team": "team_a", "commit": "c1|c1|c1", "judge": "llm"},
        {"team": "team_a", "commit": "c1|c1|c1", "judge": "llm"},
        {"team": "team_a", "commit": "c2|c2|c2", "judge": "llm-guarded"},
        {"team": "team_a", "commit": "c3|c3|c3", "judge": "admin-set-base"},
        {"team": "team_a", "commit": "c4|c4|c4", "judge": "unreachable"},
        {"team": "team_a", "commit": "None|None|None", "judge": "stagnation"},
        {"team": "team_b", "commit": "b1|b1|b1", "judge": "fallback"},
    ])
    out = asyncio.run(m.state())
    assert out["teams"]["team_a"]["releases"] == 2
    assert out["teams"]["team_a"]["stagnations"] == 1
    assert out["teams"]["team_b"]["releases"] == 1


def test_cold_start_guard_no_retroactive_dump():
    # _load_state после сна Render ставит last_eval_ts=now: 6 ч простоя не
    # должны обернуться разовым обвалом — утекает только наблюдаемый срез
    now = datetime.now(timezone.utc)
    _reset(now)
    m._state["team_a"]["last_commit_ts"] = now - timedelta(hours=6)
    m._state["team_a"]["last_eval_ts"] = now
    before = m._state["team_a"]["client_base"]
    asyncio.run(m._decay_tick("team_a", now))
    assert m._state["team_a"]["client_base"] == before


# --- скриптовый fallback (без LLM) -------------------------------------------

def _changed_snap(team: str, *, blocks_changed: int = 3,
                  transfer_ok: bool = True) -> dict:
    """Снимок с изменёнными контрактами — generic_fallback выведет фичу из diff."""
    snap = _mock_snap(team, transfer_ok=transfer_ok)
    names = ("backend", "cib", "retail")
    for name in names[:blocks_changed]:
        snap["blocks"][name]["contract"] = f"NEW {name} ручка"
    return snap


def test_fallback_empty_commit_no_movement(monkeypatch):
    # без LLM, контракты не менялись vs baseline → absent → база не двигается
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    now = datetime.now(timezone.utc)
    _reset(now)
    snaps = {t: _mock_snap(t) for t in m.TEAMS}
    out = asyncio.run(m.evaluate_round(snaps, {"team_a"}))
    assert out["team_a"]["judge"] == "fallback"
    assert out["team_a"]["feature_state"] == "absent"
    assert out["team_a"]["delta"] == 0


def test_fallback_new_feature_in_contract_gains_clients(monkeypatch):
    # без LLM: контракты изменились во всех трёх блоках → partial, база растёт
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    now = datetime.now(timezone.utc)
    _reset(now)
    snaps = {t: _changed_snap(t) for t in m.TEAMS}
    out = asyncio.run(m.evaluate_round(snaps, {"team_a"}))
    assert out["team_a"]["judge"] == "fallback"
    assert out["team_a"]["feature_state"] == "partial"
    assert out["team_a"]["delta"] > 0


def test_fallback_regression_loses_clients(monkeypatch):
    # без LLM: переводы сломаны (регрессия базовой функции) → клиенты уходят
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    now = datetime.now(timezone.utc)
    _reset(now)
    snaps = {t: _mock_snap(t) for t in m.TEAMS}
    snaps["team_a"] = _mock_snap("team_a", transfer_ok=False)
    out = asyncio.run(m.evaluate_round(snaps, {"team_a"}))
    assert out["team_a"]["delta"] < 0
    assert "клиенты пользуются каждый день" in out["team_a"]["reason"]


def test_fallback_stagnation_leaks_clients(monkeypatch):
    # застой утекает вообще без участия LLM
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    now = datetime.now(timezone.utc)
    _reset(now)
    m._state["team_a"]["last_commit_ts"] = now - timedelta(hours=2)
    m._state["team_a"]["last_eval_ts"] = now - timedelta(minutes=1)
    before = m._state["team_a"]["client_base"]
    asyncio.run(m._decay_tick("team_a", now))
    assert m._state["team_a"]["client_base"] < before


def test_unreachable_bank_drops_base(monkeypatch):
    # все три блока недоступны — клиенты не могут войти, база падает
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    now = datetime.now(timezone.utc)
    _reset(now)
    down = {"team": "team_a", "blocks": {
        n: {"reachable": False, "commit": None, "contract": "", "checks": {}}
        for n in ("backend", "cib", "retail")}, "regression": {}}
    snaps = {t: _mock_snap(t) for t in m.TEAMS}
    snaps["team_a"] = down
    out = asyncio.run(m.evaluate_round(snaps, {"team_a"}))
    assert out["team_a"]["judge"] == "unreachable"
    assert out["team_a"]["delta"] < 0
