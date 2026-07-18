"""
Tests für die Paper-Forward-Fieberkurve-Einbindung im Tab "Trades &
Lernen" (H4.3). Isoliertes Mini-Skript (Muster:
test_dashboard_calibration_curve_tab.py) statt des vollen render(ctx).
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.trades_panel import _render_paper_forward_curve
_render_paper_forward_curve()
"""


def test_paper_forward_curve_empty_state_without_positions(monkeypatch):
    monkeypatch.setattr("dashboard.paper_forward_curve.equity_curve", lambda: [])
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Noch keine abgeschlossenen Paper-Forward-Positionen" in captions


def test_paper_forward_curve_shows_thin_sample_warning_under_30(monkeypatch):
    rows = [
        {"date": f"2026-01-{i:02d}", "ticker": "AAPL", "strategy": "baseline_swing",
         "return_pct": 0.01, "cum_return": 0.01 * i}
        for i in range(1, 6)
    ]
    monkeypatch.setattr("dashboard.paper_forward_curve.equity_curve", lambda: rows)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Bilanz statistisch dünn (n=5)" in captions


def test_paper_forward_curve_no_warning_with_enough_positions(monkeypatch):
    rows = [
        {"date": f"2026-{(i % 12) + 1:02d}-01", "ticker": "AAPL",
         "strategy": "baseline_swing", "return_pct": 0.01, "cum_return": 0.01 * i}
        for i in range(1, 35)
    ]
    monkeypatch.setattr("dashboard.paper_forward_curve.equity_curve", lambda: rows)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "statistisch dünn" not in captions
