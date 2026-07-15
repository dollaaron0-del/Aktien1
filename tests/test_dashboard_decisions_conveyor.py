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
