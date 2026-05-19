"""Приёмочный тест симулятора — четыре правила правдоподобия клиентов:

1. фронтендер выкатил вкладку, апишек нет → база не двигается;
2. команда давно не коммитит → клиенты утекают;
3. фича работает, но сделана криво → клиенты уходят;
4. фича работает и удобна → клиенты приходят.

Проверяется и путь с LLM-судьёй (мок), и скриптовый fallback (без LLM) — чтобы
воркшоп пережил отсутствующий или мёртвый OPENAI_API_KEY.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from src import llm
from src import main as m
from src.scoring import B0

FULL = [2] * 10


def _reset(now: datetime) -> None:
    """Обе команды — в стартовом состоянии, оценённом раунд назад."""
    m._eval_lock = None          # пересоздать lock в loop текущего asyncio.run
    m._events.clear()
    for team in m.TEAMS:
        st = m._fresh_state()
        st["last_commit"] = "old"
        st["last_commit_ts"] = now
        st["last_eval_ts"] = now
        st["baseline_score"] = 4
        st["last_score"] = 4
        st["last_value"] = 0.0
        m._state[team] = st


def _mock_snap(team: str) -> dict:
    """Достижимый снапшот; содержимое checks неважно — вердикт даёт мок судьи."""
    return {"team": team, "blocks": {
        n: {"reachable": True, "commit": "c1", "checks": {}}
        for n in ("backend", "cib", "retail")}}


def _patch_judge(monkeypatch, feature_state: str, convenience: int,
                 scores: list) -> None:
    block = {"scores": scores, "convenience": convenience,
             "feature_state": feature_state, "reason": "тест", "judge": "llm"}
    verdict = {"team_a": dict(block), "team_b": dict(block)}

    async def fake_judge(_a, _b):
        return verdict

    monkeypatch.setattr(m, "judge_round", fake_judge)


def _run_commit(team: str = "team_a") -> dict:
    out = asyncio.run(m.evaluate_round(
        _mock_snap("team_a"), _mock_snap("team_b"), {team}))
    return out[team]


# --- путь с LLM-судьёй (мок) -------------------------------------------------

def test_rule1_frontend_only_no_movement(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "frontend_only", 5, FULL)
    res = _run_commit()
    assert res["delta"] == 0
    assert round(m._state["team_a"]["client_base"]) == B0


def test_rule3_clunky_working_feature_loses_clients(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 1, FULL)   # работает, но криво
    res = _run_commit()
    assert res["delta"] < 0
    assert m._state["team_a"]["client_base"] < B0


def test_rule4_convenient_working_feature_gains_clients(monkeypatch):
    _reset(datetime.now(timezone.utc))
    _patch_judge(monkeypatch, "working", 9, FULL)   # работает и удобно
    res = _run_commit()
    assert res["delta"] > 0
    assert m._state["team_a"]["client_base"] > B0


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

def _real_snap(team: str, *, credit_in_ui: bool = False, e2e: bool = False,
               backend_api: bool = False, explained: bool = False,
               discriminating: bool = False, latency: int = None,
               transfer_ok: bool = True) -> dict:
    """Снапшот с настоящими probe-checks — feature_state и оценку выведут
    judge.classify_feature / fallback_* без всякого LLM."""
    retail = {
        "credit_in_ui": credit_in_ui,
        "credit_apply_status": 200 if e2e else 404,
        "credit_apply_decision": "rejected" if e2e else None,
        "credit_apply_explained": explained,
        "transfer_ok": transfer_ok,
    }
    if latency is not None:
        retail["credit_apply_latency_ms"] = latency
    return {"team": team, "blocks": {
        "backend": {"reachable": True, "commit": "c", "checks": {
            "serves_client": True, "accepts_application": backend_api,
            "lists_applications": backend_api}},
        "cib": {"reachable": True, "commit": "c", "checks": {
            "has_credit_product": e2e, "decide_status": 200 if e2e else 0,
            "decision_is_discriminating": discriminating}},
        "retail": {"reachable": True, "commit": "c", "checks": retail}}}


def test_fallback_path_covers_all_four_rules(monkeypatch):
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")   # без LLM — только fallback
    now = datetime.now(timezone.utc)

    # Правило 1: только витрина (вкладка есть, апишек нет) — база не двигается
    _reset(now)
    fo = _real_snap("team_a", credit_in_ui=True)
    out = asyncio.run(m.evaluate_round(fo, _real_snap("team_b"), {"team_a"}))
    assert out["team_a"]["judge"] == "fallback"
    assert out["team_a"]["feature_state"] == "frontend_only"
    assert out["team_a"]["delta"] == 0

    # Правило 4: фича работает и удобна (быстро, с объяснением) — клиенты приходят
    _reset(now)
    good = _real_snap("team_a", credit_in_ui=True, e2e=True, backend_api=True,
                      explained=True, discriminating=True, latency=700)
    out = asyncio.run(m.evaluate_round(good, _real_snap("team_b"), {"team_a"}))
    assert out["team_a"]["feature_state"] == "working"
    assert out["team_a"]["delta"] > 0

    # Правило 3: фича работает, но криво (медленно, без объяснения) — клиенты уходят
    _reset(now)
    bad = _real_snap("team_a", credit_in_ui=True, e2e=True, backend_api=True,
                     explained=False, discriminating=False, latency=9000)
    out = asyncio.run(m.evaluate_round(bad, _real_snap("team_b"), {"team_a"}))
    assert out["team_a"]["feature_state"] == "working"
    assert out["team_a"]["delta"] < 0

    # Правило 2: застой — клиенты утекают вообще без участия LLM
    _reset(now)
    m._state["team_a"]["last_commit_ts"] = now - timedelta(hours=2)
    m._state["team_a"]["last_eval_ts"] = now - timedelta(minutes=1)
    before = m._state["team_a"]["client_base"]
    asyncio.run(m._decay_tick("team_a", now))
    assert m._state["team_a"]["client_base"] < before
