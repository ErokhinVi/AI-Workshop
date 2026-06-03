"""Канонический baseline (нулевая точка) воркшопа — детерминированный, встроенный.

Все команды форкнулись из одного шаблона (`team-template/`), поэтому нулевая
точка для оценки diff'а — это набор ручек шаблона. Судья ВСЕГДА сравнивает
текущее состояние банка с этим эталоном, а не с probe-снимком, который жил в
памяти и терялся при каждом редеплое симулятора (из-за чего оценка съезжала на
пустой baseline и табло дёргалось).

Тем самым diff команды («что добавили поверх шаблона») считается корректно при
любом старте процесса: ни рестарт Render, ни потеря in-memory состояния больше
не меняют точку отсчёта.

Источник истины — `team-template/{block}/CONTRACT.md` этого репозитория; ручки
ниже сняты оттуда. Если шаблон меняют — обнови этот список (тест-страж
`test_baseline_matches_template` сверяет его при наличии team-template рядом).
"""
from __future__ import annotations

# Ручки шаблона по блокам — заголовки `### METHOD /path` из
# team-template/{block}/CONTRACT.md (нулевая точка, от которой стартуют команды).
# Корень `GET /` сознательно НЕ включён: общий парсер ручек (`(/\S+)`) его не
# ловит, и для diff он не используется — главный экран retail оценивается по
# изменению HTML (ось ui_polish), а не как endpoint.
TEMPLATE_ENDPOINTS: dict[str, tuple[tuple[str, str], ...]] = {
    "backend": (
        ("GET", "/health"),
        ("GET", "/clients"),
        ("GET", "/clients/{client_id}"),
        ("GET", "/transactions/{client_id}"),
        ("POST", "/api/transfer"),
    ),
    "cib": (
        ("GET", "/health"),
        ("GET", "/products"),
    ),
    "retail": (
        ("GET", "/health"),
        ("GET", "/clients"),
        ("GET", "/transactions/{client_id}"),
        ("POST", "/api/transfer"),
    ),
}

BLOCKS = ("backend", "cib", "retail")


def baseline_contract(block: str) -> str:
    """CONTRACT.md нулевой точки для блока — только заголовки ручек шаблона.

    Этого достаточно и для diff'а ручек (`### METHOD /path`), и для подачи LLM как
    baseline_contract: модель видит «в шаблоне были эти ручки, сейчас — эти плюс
    новые». Прозу шаблона не тащим намеренно — оценивается добавленное, не базовое.
    """
    lines = [f"# {block} — базовый шаблон банка (нулевая точка воркшопа)"]
    lines += [f"### {method} {path}" for method, path in TEMPLATE_ENDPOINTS[block]]
    return "\n".join(lines) + "\n"


def baseline_snapshot(team: str) -> dict:
    """Детерминированный baseline-снимок команды из шаблона. Без сети и состояния.

    Совместим по форме с probe-снимком (`{team, blocks: {block: {...}}}`), чтобы
    судья и feature_probe работали без изменений. retail несёт пустой html: любое
    изменение главного экрана относительно «ничего» честно читается как косметика.
    """
    blocks: dict[str, dict] = {}
    for name in BLOCKS:
        entry: dict = {
            "reachable": True,
            "commit": "template",
            "contract": baseline_contract(name),
            "checks": {},
        }
        if name == "retail":
            entry["html"] = ""
        blocks[name] = entry
    return {"team": team, "blocks": blocks}
