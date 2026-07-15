"""
Tests für die Kalibrier-Kurve-Einbindung im Tab "Trades & Lernen" (H3.3).
Isoliertes Mini-Skript (Muster: test_dashboard_thesis_board_tab.py)
statt des vollen render(ctx).
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.trades import _render_calibration_curve
_render_calibration_curve()
"""


def test_calibration_curve_empty_state_without_labeled_trades(monkeypatch):
    monkeypatch.setattr(
        "dashboard.calibration_curve.confidence_win_rates", lambda: [
            {"confidence": "HIGH", "n": 0, "wins": 0, "win_rate": None},
            {"confidence": "MEDIUM", "n": 0, "wins": 0, "win_rate": None},
            {"confidence": "LOW", "n": 0, "wins": 0, "win_rate": None},
        ],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Noch keine gelabelten Trades vorhanden" in captions


def test_calibration_curve_renders_chart_and_thin_sample_warning(monkeypatch):
    monkeypatch.setattr(
        "dashboard.calibration_curve.confidence_win_rates", lambda: [
            {"confidence": "HIGH", "n": 7, "wins": 2, "win_rate": 0.2857},
            {"confidence": "MEDIUM", "n": 204, "wins": 76, "win_rate": 0.3725},
            {"confidence": "LOW", "n": 136, "wins": 45, "win_rate": 0.3309},
        ],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    # AppTest kennt keine eigene Element-Kategorie für st.altair_chart —
    # "kein Exception beim Bauen/Rendern" ist hier der erreichbare Check.
    assert not at.exception
    captions = [str(c.value) for c in at.get("caption")]
    thin_warnings = [c for c in captions if "Stichprobe dünn" in c]
    assert len(thin_warnings) == 1
    assert "HIGH" in thin_warnings[0] and "n=7" in thin_warnings[0]


def test_calibration_curve_no_warning_when_all_buckets_have_enough_samples(monkeypatch):
    monkeypatch.setattr(
        "dashboard.calibration_curve.confidence_win_rates", lambda: [
            {"confidence": "HIGH", "n": 25, "wins": 10, "win_rate": 0.4},
            {"confidence": "MEDIUM", "n": 30, "wins": 12, "win_rate": 0.4},
            {"confidence": "LOW", "n": 40, "wins": 15, "win_rate": 0.375},
        ],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Stichprobe dünn" not in captions
