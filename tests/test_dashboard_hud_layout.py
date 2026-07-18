"""
Tests für die schwebende HUD-Leiste + Vollbild-Szene (Vision W8, 18.7.2026).

User-Vorgabe wörtlich: "die Fabrik soll das Einzigste sein [...] Zusatz-
informationen werden am Rand eingeblendet ähnlich wie bei einem Base-Bau-
Spiel auf dem Handy." Headless via AppTest.from_file gegen das echte
app.py (Muster: test_dashboard_kiosk.py/test_dashboard_mobile.py).
"""
from streamlit.testing.v1 import AppTest


def _run(monkeypatch, theme):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    if theme:
        monkeypatch.setenv("DASHBOARD_THEME", theme)
    else:
        monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    return at


def test_pixel_mode_wraps_header_in_hud_bar_and_hides_chrome(monkeypatch):
    at = _run(monkeypatch, theme=None)
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert '<div class="px-hud-bar">' in html_out
    assert html_out.count('<div class="px-hud-bar">') == 1
    # Div muss auch wieder geschlossen werden (kein offenes Tag, das den
    # Rest der Seite versehentlich mit einwickelt).
    assert html_out.index('<div class="px-hud-bar">') < html_out.index("</div>")
    assert 'data-testid="stHeader"' in html_out
    assert "display: none;" in html_out


def test_pixel_mode_main_scene_uses_fullscreen_wrapper(monkeypatch):
    at = _run(monkeypatch, theme=None)
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert '<div class="px-scene-wrap">' in html_out


def test_plain_mode_has_no_hud_bar_or_scene_wrapper(monkeypatch):
    """D6.2-Notausstieg: Plain bleibt bei den originalen Streamlit-Rändern
    — keine der W8.1-Klassen darf hier auftauchen."""
    at = _run(monkeypatch, theme="plain")
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-hud-bar" not in html_out
    assert "px-scene-wrap" not in html_out
