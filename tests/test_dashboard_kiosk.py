"""
Tests für den Kiosk-Modus (Ausbau-Roadmap H6.1, docs/DASHBOARD_HORIZONT.md).

?kiosk=1 zeigt nur die Fabrik-Szene als Dauer-Wandbild — keine KPI-Leiste,
keine Instrumente/Ticker, keine Tabs. Headless via AppTest.from_file gegen
das echte app.py (Muster: bestehende Voll-Render-Verifikation D6.1/D6.2).
"""
from streamlit.testing.v1 import AppTest


def _run(monkeypatch, kiosk):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    at = AppTest.from_file("dashboard/app.py")
    if kiosk:
        at.query_params["kiosk"] = "1"
    at.run(timeout=60)
    return at


def test_kiosk_mode_shows_only_factory_scene(monkeypatch):
    at = _run(monkeypatch, kiosk=True)
    assert not at.exception
    assert len(at.tabs) == 0
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out
    assert "fx-machine" in html_out


def test_kiosk_mode_hides_streamlit_chrome(monkeypatch):
    at = _run(monkeypatch, kiosk=True)
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert 'data-testid="stHeader"' in html_out
    assert "display:none" in html_out


def test_without_kiosk_param_full_dashboard_with_tabs(monkeypatch):
    at = _run(monkeypatch, kiosk=False)
    assert not at.exception
    assert len(at.tabs) > 10
