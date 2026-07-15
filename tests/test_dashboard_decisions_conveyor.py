"""
Tests für die Förderband-Einbindung im Entscheidungen-Tab (Design D4.2).

Headless via streamlit.testing.v1 AppTest. DecisionLog() bindet ihren
Default-Pfad beim ERSTEN Modulimport an DECISION_LOG_PATH (siehe
conftest.py) — daher hier bewusst KEIN eigener Pfad-Override (der würde
wegen der bereits gebundenen Default-Parameter-Bindung ohnehin ignoriert),
sondern derselbe von conftest.py gesetzte Temp-Pfad wie überall sonst im
Testlauf, den auch decisions.py intern über `_DecisionLog()` verwendet.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = '''
class _Ctx:
    def ticker_label(self, t):
        return t
    def render_sources_breakdown(self, *a, **k):
        pass

from dashboard.tabs import decisions
decisions.render(_Ctx())
'''


def _seed():
    from analyzers.decision_log import DecisionLog
    dlog = DecisionLog()
    dlog.log({"ticker": "AAPL", "action": "BUY", "reason": "Starkes Signal",
              "recommendation": "BUY", "sentiment_score": 0.8})
    dlog.log({"ticker": "MSFT", "action": "SKIP", "reason": "Sentiment < Schwelle",
              "recommendation": "HOLD", "sentiment_score": 0.5})
    dlog.log({"ticker": "NVDA", "action": "SKIP", "reason": "Sektor-Korrelation zu hoch",
              "recommendation": "SKIP", "sentiment_score": 0.4})


def test_conveyor_svg_appears_in_pixel_mode(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out
    assert "BUY" in html_out


def test_conveyor_svg_absent_in_plain_mode(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" not in html_out
    # Der alte Fortschrittsbalken-Pfad bleibt der einzige Weg
    assert any("Warum wurde übersprungen" in str(m.value) for m in at.get("markdown"))


# ── H2.4: Zeitraum-Vergleich ──────────────────────────────────────────────────

def test_period_compare_expander_renders_table():
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    expander_labels = [e.label for e in at.get("expander")]
    assert "📊 Zeitraum-Vergleich" in expander_labels
    assert len(at.get("dataframe")) >= 1


def test_period_compare_renders_without_any_decisions():
    """Kein Seed -> darf nicht crashen, Vergleichs-Tabelle bleibt nur mit
    Nullen statt einer Exception."""
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception


# ── H3.1: "Warum nicht?"-Explorer ─────────────────────────────────────────────

def _select_seed_day(at):
    """_seed() loggt ohne explizites decided_at (= heute) — die geteilte
    Test-Decision-Log-DB sammelt über die ganze Session Einträge aus
    VIELEN Testdateien mit unterschiedlichen Tagen an (bewusst geteilter
    Pfad, siehe Moduldoc oben); der Tag-Selectbox-Default (neuester Tag)
    ist darum NICHT verlässlich "heute". Deshalb hier explizit wählen."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_sb = next(s for s in at.get("selectbox") if s.key == "dec_day_select")
    if day_sb.value != today:
        day_sb.set_value(today)
        at.run()
    return at


def test_why_not_explorer_renders_gate_trail_svg():
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    _select_seed_day(at)
    assert not at.exception
    expander_labels = [e.label for e in at.get("expander")]
    assert "🔎 Warum nicht? Explorer" in expander_labels
    # Ticker-Auswahl per Selectbox muss die geloggten Ticker anbieten
    # (Superset-Check: andere Tests können denselben Tag mitbelegen):
    sb = next(s for s in at.get("selectbox") if s.key == "why_not_ticker")
    assert {"AAPL", "MSFT", "NVDA"} <= set(sb.options)


def test_why_not_explorer_shows_blocked_gate_for_selected_ticker(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    _select_seed_day(at)
    sb = next(s for s in at.get("selectbox") if s.key == "why_not_ticker")
    sb.set_value("NVDA")
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Sektor-Korrelation zu hoch" in html_out


def test_why_not_explorer_plain_mode_shows_text_trail(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    _seed()
    at = AppTest.from_string(_SCRIPT)
    at.run()
    _select_seed_day(at)
    sb = next(s for s in at.get("selectbox") if s.key == "why_not_ticker")
    sb.set_value("MSFT")
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "Sentiment < Schwelle" in caption_out
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" not in html_out
