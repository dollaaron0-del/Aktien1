"""
Tests für die Handy-Kompaktansicht (Ausbau-Roadmap H6.2,
docs/DASHBOARD_HORIZONT.md).

?mobile=1 zeigt die 5 wichtigsten Zahlen + Ampel + Mini-Fabrik +
Terminal-Feed untereinander — keine KPI-Leiste, keine Instrumente/
Ticker, keine Tabs. Headless via AppTest.from_file gegen das echte
app.py (gleiche Testform wie H6.1, tests/test_dashboard_kiosk.py).
"""
from streamlit.testing.v1 import AppTest


def _run(monkeypatch, mobile):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    at = AppTest.from_file("dashboard/app.py")
    if mobile:
        at.query_params["mobile"] = "1"
    at.run(timeout=60)
    return at


def test_mobile_mode_shows_depot_metric_and_factory_scene(monkeypatch):
    at = _run(monkeypatch, mobile=True)
    assert not at.exception
    assert len(at.tabs) == 0
    metric_labels = [m.label for m in at.get("metric")]
    assert "Depotwert" in metric_labels
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out
    assert "fx-machine" in html_out


def test_mobile_mode_hides_streamlit_chrome(monkeypatch):
    at = _run(monkeypatch, mobile=True)
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert 'data-testid="stHeader"' in html_out
    assert "display:none" in html_out


def test_mobile_mode_skips_kpi_strip_and_instruments(monkeypatch):
    at = _run(monkeypatch, mobile=True)
    assert not at.exception
    metric_labels = [m.label for m in at.get("metric")]
    # Depotwert (Handy-Zweig) ja, aber keine der vielen KPI-Strip-Metriken:
    assert "Depotwert" in metric_labels
    assert len(metric_labels) == 1


def test_without_mobile_param_full_dashboard_with_tabs(monkeypatch):
    at = _run(monkeypatch, mobile=False)
    assert not at.exception
    assert len(at.tabs) >= 5  # Tab-Umbau 18.7.2026: 5 Tabs statt >10
