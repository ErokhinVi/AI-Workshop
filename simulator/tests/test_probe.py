"""Тесты чистой regression-логики probe — детерминированный якорь честности."""
from src.probe import assess_regression


def _clean_snap() -> dict:
    return {"team": "team_a", "blocks": {
        "backend": {"reachable": True, "checks": {"serves_client": True}},
        "cib": {"reachable": True, "checks": {}},
        "retail": {"reachable": True, "checks": {"transfer_ok": True}}}}


def test_assess_regression_clean_baseline_has_nothing_broken():
    r = assess_regression(_clean_snap())
    assert r["unreachable_blocks"] == 0
    assert r["transfers_broken"] is False
    assert r["serves_client_broken"] is False
    assert r["labels"] == []


def test_assess_regression_flags_broken_transfer():
    snap = _clean_snap()
    snap["blocks"]["retail"]["checks"]["transfer_ok"] = False
    r = assess_regression(snap)
    assert r["transfers_broken"] is True
    assert r["unreachable_blocks"] == 0
    assert any("перевод" in lbl.lower() for lbl in r["labels"])


def test_assess_regression_flags_broken_serves_client():
    snap = _clean_snap()
    snap["blocks"]["backend"]["checks"]["serves_client"] = False
    r = assess_regression(snap)
    assert r["serves_client_broken"] is True
    assert any("/clients" in lbl for lbl in r["labels"])


def test_assess_regression_counts_unreachable():
    snap = _clean_snap()
    snap["blocks"]["backend"]["reachable"] = False
    snap["blocks"]["backend"]["checks"] = {}
    r = assess_regression(snap)
    assert r["unreachable_blocks"] == 1
    assert any("backend" in lbl for lbl in r["labels"])


def test_assess_regression_unreachable_backend_not_double_flagged_as_serves():
    # недоступный backend — это «недоступен», а не «не отдаёт клиента»
    snap = _clean_snap()
    snap["blocks"]["backend"]["reachable"] = False
    snap["blocks"]["backend"]["checks"] = {}
    r = assess_regression(snap)
    assert r["serves_client_broken"] is False


def test_assess_regression_ignores_missing_checks():
    # на старте проверок ещё нет — не считаем это регрессией
    snap = {"team": "team_a", "blocks": {
        "backend": {"reachable": True, "checks": {}},
        "cib": {"reachable": True, "checks": {}},
        "retail": {"reachable": True, "checks": {}}}}
    r = assess_regression(snap)
    assert r["transfers_broken"] is False
    assert r["serves_client_broken"] is False
    assert r["labels"] == []
