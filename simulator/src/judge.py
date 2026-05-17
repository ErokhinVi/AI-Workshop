"""Судья: рубрика трёх блоков, скриптовый fallback, парсинг ответа LLM.

10 критериев (3 backend + 3 cib + 4 retail). Чистые функции fallback_rubric и
parse_judge_response покрыты тестами; LLM-вызов — в judge_round.
"""
from __future__ import annotations

import json

from src.llm import LLMError, ask_llm

RUBRIC_CRITERIA: list[str] = [
    "C1 (backend). Отдаёт данные клиента по запросу.",
    "C2 (backend). Принимает заявку на кредит на хранение.",
    "C3 (backend). Отдаёт список поданных заявок.",
    "C4 (cib). В каталоге продуктов есть кредитный продукт.",
    "C5 (cib). Ручка решения по заявке работает (отвечает без ошибки).",
    "C6 (cib). Решение опирается на данные клиента: сильный и слабый "
    "заявители получают разные вердикты.",
    "C7 (retail). Вкладка «Кредиты» присутствует в интерфейсе.",
    "C8 (retail). Сквозная подача заявки доходит до реального решения.",
    "C9 (retail). Отказ сопровождается человеческим объяснением.",
    "C10 (retail). Нет регрессии: переводы по-прежнему работают.",
]


def fallback_rubric(team_snapshot: dict) -> tuple[list[int], str]:
    """Механически вывести 10 баллов из probe-снапшота команды, без LLM."""
    blocks = team_snapshot.get("blocks", {})
    b = blocks.get("backend", {}).get("checks", {})
    ci = blocks.get("cib", {}).get("checks", {})
    r = blocks.get("retail", {}).get("checks", {})
    scores = [
        2 if b.get("serves_client") else 0,
        2 if b.get("accepts_application") else 0,
        2 if b.get("lists_applications") else 0,
        2 if ci.get("has_credit_product") else 0,
        2 if ci.get("decide_status") == 200 else 0,
        2 if ci.get("decision_is_discriminating") else 0,
        2 if r.get("credit_in_ui") else 0,
        2 if (r.get("credit_apply_status") == 200
              and r.get("credit_apply_decision") is not None) else 0,
        2 if r.get("credit_apply_explained") else 0,
        2 if r.get("transfer_ok") else 0,
    ]
    done = sum(1 for s in scores if s == 2)
    parts = [f"готово критериев: {done} из 10"]
    if scores[6] and scores[7]:
        parts.append("вкладка кредитов и сквозная заявка работают")
    if not scores[9]:
        parts.append("переводы сломаны")
    if scores[0] and not scores[1]:
        parts.append("backend ещё не принимает заявки")
    reason = "Автооценка: " + ", ".join(parts) + "."
    return scores, reason


def parse_judge_response(raw: str) -> dict:
    """Разобрать JSON-ответ LLM-судьи. Бросает ValueError при мусоре."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"в ответе судьи нет JSON-объекта: {raw[:200]}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ судьи — не валидный JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("ответ судьи — не объект")
    return data


_JUDGE_SYSTEM = (
    "Ты — рыночный аналитик розничного банка. Тебе дают результаты технической "
    "проверки двух команд; каждая команда — три блока (backend, cib, retail), "
    "которые вместе строят функцию «Кредиты». Оцени каждую команду по 10 "
    "критериям, каждый строго 0, 1 или 2 балла. Будь одинаково строг к обеим. "
    "Верни СТРОГО JSON без пояснений."
)


def _block_checks(team_snapshot: dict) -> dict:
    return {
        name: team_snapshot.get("blocks", {}).get(name, {})
        for name in ("backend", "cib", "retail")
    }


def _build_judge_prompt(snap_a: dict, snap_b: dict) -> str:
    criteria = "\n".join(RUBRIC_CRITERIA)
    return (
        f"Критерии (каждый 0, 1 или 2 балла):\n{criteria}\n\n"
        f"Команда A, блоки:\n{json.dumps(_block_checks(snap_a), ensure_ascii=False)}\n\n"
        f"Команда B, блоки:\n{json.dumps(_block_checks(snap_b), ensure_ascii=False)}\n\n"
        'Верни JSON: {"team_a": {"scores": [c1..c10], "reason": "1-2 предложения '
        'по-русски про факты"}, "team_b": {"scores": [...], "reason": "..."}}'
    )


async def judge_round(snap_a: dict, snap_b: dict) -> dict:
    """Оценить обе команды одним вызовом LLM. При сбое — скриптовый fallback."""
    result: dict[str, dict] = {}
    try:
        raw = await ask_llm(
            _build_judge_prompt(snap_a, snap_b),
            system=_JUDGE_SYSTEM, max_tokens=600, temperature=0.0,
        )
        parsed = parse_judge_response(raw)
        for team in ("team_a", "team_b"):
            block = parsed.get(team) or {}
            scores = block.get("scores")
            if not (isinstance(scores, list) and len(scores) == 10):
                raise ValueError(f"судья не дал 10 баллов для {team}")
            result[team] = {
                "scores": [int(x) for x in scores],
                "reason": str(block.get("reason", "")).strip() or "(без обоснования)",
                "judge": "llm",
            }
        return result
    except (LLMError, ValueError, KeyError, TypeError):
        for team, snap in (("team_a", snap_a), ("team_b", snap_b)):
            scores, reason = fallback_rubric(snap)
            result[team] = {"scores": scores, "reason": reason, "judge": "fallback"}
        return result
