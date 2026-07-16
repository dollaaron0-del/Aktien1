"""
Tests für dashboard/tabs/dossier.py (Design-Roadmap L1.3 — Kartei-Tab).

Isoliertes Mini-Skript (Muster test_dashboard_thesis_board_tab.py). Nutzt
eine synthetische Analyse-Historie, damit der Tab ohne echte Produktions-
daten testbar bleibt.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.dossier import render
render(None)
"""


def _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB1", n=3):
    import analyzers.analysis_log as alog_mod
    db_path = str(tmp_path / "analysis_log_test.db")
    monkeypatch.setattr(alog_mod, "DB_PATH", db_path)
    alog = alog_mod.AnalysisLog()
    for i in range(n):
        alog._conn.execute(
            "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
            "sentiment_score, confidence) VALUES (?,?,?,?,?,?)",
            (f"2026-07-{10+i:02d}T09:00:00", ticker, "BUY", "BULLISH",
             0.5 + i * 0.1, "HIGH"),
        )
    alog._conn.commit()
    return db_path


def test_empty_state_when_no_analyses(monkeypatch, tmp_path):
    import analyzers.analysis_log as alog_mod
    monkeypatch.setattr(alog_mod, "DB_PATH", str(tmp_path / "empty.db"))
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    assert "füllt sich" in "".join(str(i.value) for i in at.get("info"))


def test_shows_akte_for_known_ticker(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB2", n=3)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("subheader"))
    assert "ZTAB2" in md


def test_selectbox_shows_analysis_count(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB3", n=5)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    sb = at.selectbox(key="dossier_select")
    assert "5 Analysen" in sb.options[0]


def test_deep_link_preselects_ticker(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB4", n=1)
    _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB5", n=2)
    at = AppTest.from_string(_SCRIPT)
    at.query_params["dossier"] = "ztab5"  # Kleinschreibung muss gehen
    at.run()
    assert not at.exception
    sb = at.selectbox(key="dossier_select")
    assert sb.value.startswith("ZTAB5")


def test_metrics_reflect_trade_bilanz(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, ticker="ZTAB6", n=1)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    metric_labels = [m.label for m in at.get("metric")]
    assert "Analysen" in metric_labels
    assert "Gelabelte Trades" in metric_labels
