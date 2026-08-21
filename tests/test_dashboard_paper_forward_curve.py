"""
Tests für dashboard/paper_forward_curve.py (Ausbau-Roadmap H4.3 —
Paper-Forward-Fieberkurve).
"""
import json

from dashboard.paper_forward_curve import equity_curve


def _position(strategy="baseline_swing", ticker="AAPL", status="CLOSED",
              exit_date="2026-01-05", return_pct=0.1, weight=1.0):
    return {
        "strategy": strategy, "ticker": ticker, "signal_date": "2026-01-01",
        "weight": weight, "cfg": {}, "status": status,
        "entry_date": "2026-01-02", "entry_price": 100.0,
        "tp1_hit": False, "tp2_hit": False,
        "exit_date": exit_date, "exit_price": 110.0,
        "return_pct": return_pct, "exit_reason": "time_stop",
        "last_checked": exit_date,
    }


def _write_ledger(tmp_path, positions):
    path = tmp_path / "paper_forward_test.json"
    path.write_text(json.dumps({"created_at": "2026-01-01T00:00:00", "positions": positions}))
    return str(path)


def test_equity_curve_empty_without_ledger(tmp_path):
    assert equity_curve(str(tmp_path / "nope.json")) == []


def test_equity_curve_empty_when_no_closed_positions(tmp_path):
    path = _write_ledger(tmp_path, [_position(status="OPEN")])
    assert equity_curve(path) == []


def test_equity_curve_sorted_chronologically_by_exit_date():
    positions = [
        _position(ticker="B", exit_date="2026-01-10", return_pct=0.05),
        _position(ticker="A", exit_date="2026-01-05", return_pct=0.1),
    ]
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "ledger.json")
        with open(path, "w") as fh:
            json.dump({"positions": positions}, fh)
        rows = equity_curve(path)
    assert [r["ticker"] for r in rows] == ["A", "B"]


def test_equity_curve_cumulative_return_compounds(tmp_path):
    positions = [
        _position(ticker="A", exit_date="2026-01-05", return_pct=0.10, weight=1.0),
        _position(ticker="B", exit_date="2026-01-10", return_pct=-0.05, weight=1.0),
    ]
    path = _write_ledger(tmp_path, positions)
    rows = equity_curve(path)
    assert rows[0]["cum_return"] == 0.1
    expected_second = round(1.1 * 0.95 - 1.0, 6)
    assert rows[1]["cum_return"] == expected_second


def test_equity_curve_applies_position_weight(tmp_path):
    positions = [_position(ticker="A", return_pct=0.10, weight=0.5)]
    path = _write_ledger(tmp_path, positions)
    rows = equity_curve(path)
    assert rows[0]["cum_return"] == 0.05  # 0.10 * 0.5 Gewicht


def test_equity_curve_ignores_positions_without_exit_date(tmp_path):
    positions = [_position(exit_date=None), _position(ticker="B", exit_date="2026-01-05")]
    path = _write_ledger(tmp_path, positions)
    rows = equity_curve(path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "B"


def test_equity_curve_fail_open_on_corrupt_ledger(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    assert equity_curve(str(path)) == []
