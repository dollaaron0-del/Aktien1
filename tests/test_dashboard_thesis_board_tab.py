"""
Tests für die Thesen-Board-Einbindung im Tab "Trades & Lernen"
(Ausbau-Roadmap H4.1). Isoliertes Mini-Skript (Muster:
tests/test_dashboard_auth.py) statt des vollen render(ctx) — die neue
Funktion hängt an keiner der schweren ctx-Abhängigkeiten (acc/portfolio/
tracker/journal/reflection), ein voller Tab-Stub wäre unnötiger Aufwand.
"""
from datetime import date, timedelta

from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.trades_panel import _render_thesis_board
_render_thesis_board()
"""


def test_thesis_board_shows_empty_state_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry_test.json"))
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    infos = [str(i.value) for i in at.get("info")]
    assert any("Noch keine These aktiv" in i and "150 Trades" in i for i in infos)


def test_thesis_board_shows_progress_and_led_for_registered_thesis(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry_test.json"))
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    from analyzers.thesis_verdict import register_thesis
    register_thesis("mechanical_baseline", n_min=150, time_budget_months=24,
                    description="Rein mechanische Swing-Strategie",
                    started_at=(date.today() - timedelta(days=60)).isoformat())

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    assert len(at.get("progress")) == 1
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "mechanical_baseline" in html_out
    assert "px-led" in html_out  # pixel-Modus: LED-Plakette statt Emoji


def test_thesis_board_plain_mode_shows_status_text_without_html(tmp_path, monkeypatch):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry_test.json"))
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    from analyzers.thesis_verdict import register_thesis
    register_thesis("plain_thesis", started_at=date.today().isoformat())

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-led" not in html_out
    assert "Läuft" in html_out
