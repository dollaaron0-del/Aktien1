"""
Tests für dashboard/thesis_board.py (Ausbau-Roadmap H4.1 — Thesen-Board).

Isoliert über THESIS_REGISTRY_PATH (dieselbe Env-Var-Override-Mechanik,
die analyzers/thesis_verdict.py selbst für Tests vorsieht — siehe
_registry_path()).
"""
from datetime import date, timedelta

from dashboard.thesis_board import default_criteria, thesis_rows


def _isolate_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry_test.json"))


def test_default_criteria_matches_module_constants():
    from analyzers.thesis_verdict import DEFAULT_N_MIN, DEFAULT_TIME_BUDGET_MONTHS
    crit = default_criteria()
    assert crit == {"n_min": DEFAULT_N_MIN, "time_budget_months": DEFAULT_TIME_BUDGET_MONTHS}


def test_thesis_rows_empty_without_registry_file(tmp_path, monkeypatch):
    _isolate_registry(tmp_path, monkeypatch)
    assert thesis_rows() == []


def test_thesis_rows_reflects_pending_thesis_time_progress(tmp_path, monkeypatch):
    _isolate_registry(tmp_path, monkeypatch)
    from analyzers.thesis_verdict import register_thesis

    started = (date.today() - timedelta(days=182)).isoformat()  # ~6 Monate
    register_thesis("mechanical_baseline", n_min=150, time_budget_months=12,
                    description="Rein mechanische Swing-Strategie",
                    started_at=started)

    rows = thesis_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "mechanical_baseline"
    assert row["status"] == "PENDING"
    assert row["n_min"] == 150
    assert row["time_budget_months"] == 12
    assert 5.5 < row["months_elapsed"] < 6.5
    assert 0.4 < row["time_progress"] < 0.6
    assert row["verdict_reason"] == ""


def test_thesis_rows_time_progress_clamped_to_one_after_budget_expires(tmp_path, monkeypatch):
    _isolate_registry(tmp_path, monkeypatch)
    from analyzers.thesis_verdict import register_thesis

    started = (date.today() - timedelta(days=900)).isoformat()  # weit über Budget
    register_thesis("overdue_thesis", n_min=50, time_budget_months=6, started_at=started)

    row = thesis_rows()[0]
    assert row["time_progress"] == 1.0


def test_thesis_rows_shows_persisted_verdict_reason_for_decided_thesis(tmp_path, monkeypatch):
    _isolate_registry(tmp_path, monkeypatch)
    from analyzers.thesis_verdict import (
        ABANDONED,
        load_registry,
        register_thesis,
        save_registry,
    )

    register_thesis("abandoned_thesis", n_min=10, time_budget_months=6,
                    started_at=(date.today() - timedelta(days=200)).isoformat())
    registry = load_registry()
    registry["abandoned_thesis"].status = ABANDONED
    registry["abandoned_thesis"].verdict_reason = "Zeit-Budget abgelaufen, 4/10 Trades"
    save_registry(registry)

    row = thesis_rows()[0]
    assert row["status"] == "ABANDONED"
    assert row["verdict_reason"] == "Zeit-Budget abgelaufen, 4/10 Trades"


def test_thesis_rows_sorted_by_name_and_fail_open_on_corrupt_file(tmp_path, monkeypatch):
    _isolate_registry(tmp_path, monkeypatch)
    from analyzers.thesis_verdict import register_thesis

    register_thesis("zeta_thesis", started_at=date.today().isoformat())
    register_thesis("alpha_thesis", started_at=date.today().isoformat())
    rows = thesis_rows()
    assert [r["name"] for r in rows] == ["alpha_thesis", "zeta_thesis"]


def test_thesis_rows_fail_open_on_corrupt_registry_file(tmp_path, monkeypatch):
    path = tmp_path / "thesis_registry_test.json"
    path.write_text("{not valid json")
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(path))
    assert thesis_rows() == []
