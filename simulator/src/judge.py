"""Судья: рубрика, скриптовый fallback и парсинг ответа LLM.

LLM-вызов — в judge_round() ниже; чистые функции (fallback_rubric,
parse_judge_response) вынесены отдельно и покрыты тестами.
"""
from __future__ import annotations

import json

from src.llm import LLMError, ask_llm

RUBRIC_CRITERIA: list[str] = [
    "C1. Вкладка «Кредиты» присутствует в интерфейсе банка.",
    "C2. Заявка на кредит отправляется, сервер отвечает без ошибки (HTTP 200).",
    "C3. Решение по заявке синхронное — приходит быстрее 5 секунд.",
    "C4. Решение опирается на данные клиента: сильный и слабый заявители "
    "получают разные вердикты.",
    "C5. Отказ сопровождается осмысленным человеческим объяснением.",
    "C6. Нет регрессии: вкладка «Переводы» и сам перевод по-прежнему работают.",
]


def fallback_rubric(snapshot: dict) -> tuple[list[int], str]:
    """Механически вывести 6 баллов из probe-снапшота, без LLM.

    Возвращает (scores, reason). Применяется, когда LLM недоступен.
    """
    c = snapshot.get("checks", {})
    status = c.get("credit_apply_status")
    latency = c.get("credit_apply_latency_ms") or 0
    apply_ok = status == 200

    c1 = 2 if c.get("credit_mentioned_in_ui") else 0
    c2 = 2 if apply_ok else 0
    if apply_ok and latency < 5000:
        c3 = 2
    elif apply_ok and latency < 10000:
        c3 = 1
    else:
        c3 = 0
    c4 = 2 if c.get("decision_is_discriminating") else 0
    c5 = 2 if c.get("credit_response_has_explanation") else 0
    c6 = 2 if c.get("transfer_regression_ok") else 0
    scores = [c1, c2, c3, c4, c5, c6]

    parts: list[str] = []
    parts.append("вкладка кредитов есть" if c1 else "вкладки кредитов нет")
    if c2:
        parts.append(f"заявка обрабатывается за {latency} мс")
    else:
        parts.append("заявка не обрабатывается")
    if c5:
        parts.append("отказ объясняют по-человечески")
    if not c6:
        parts.append("переводы сломаны")
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
    "Ты — рыночный аналитик розничного банка. Тебе дают результаты "
    "технической проверки двух версий банковского приложения (команда A и "
    "команда B), которые независимо решают одну задачу — добавить функцию "
    "«Кредиты». Оцени каждую команду по 6 критериям, каждый строго 0, 1 или "
    "2 балла. Будь одинаково строг к обеим. Верни СТРОГО JSON без пояснений."
)


def _build_judge_prompt(snap_a: dict, snap_b: dict) -> str:
    criteria = "\n".join(RUBRIC_CRITERIA)
    return (
        f"Критерии оценки (каждый 0, 1 или 2 балла):\n{criteria}\n\n"
        f"Проверка команды A:\n{json.dumps(snap_a.get('checks', {}), ensure_ascii=False)}\n"
        f"reachable={snap_a.get('reachable')}\n\n"
        f"Проверка команды B:\n{json.dumps(snap_b.get('checks', {}), ensure_ascii=False)}\n"
        f"reachable={snap_b.get('reachable')}\n\n"
        'Верни JSON вида: {"team_a": {"scores": [c1,c2,c3,c4,c5,c6], '
        '"reason": "одно-два предложения по-русски, привязанные к фактам"}, '
        '"team_b": {"scores": [...], "reason": "..."}}'
    )


async def judge_round(snap_a: dict, snap_b: dict) -> dict:
    """Оценить обе команды одним вызовом LLM.

    Возвращает {"team_a": {"scores": [...], "reason": str, "judge": "llm"|"fallback"},
    "team_b": {...}}. При сбое LLM каждая команда получает скриптовый fallback.
    """
    result: dict[str, dict] = {}
    try:
        raw = await ask_llm(
            _build_judge_prompt(snap_a, snap_b),
            system=_JUDGE_SYSTEM,
            max_tokens=500,
            temperature=0.0,
        )
        parsed = parse_judge_response(raw)
        for team in ("team_a", "team_b"):
            block = parsed.get(team) or {}
            scores = block.get("scores")
            if not (isinstance(scores, list) and len(scores) == 6):
                raise ValueError(f"судья не дал 6 баллов для {team}")
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
