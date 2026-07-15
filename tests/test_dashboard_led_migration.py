"""
Tests für die LED-Migration restlicher Tabs (D6.1-Nachzug, 15.7.2026).

Deckt genau die Stellen ab, die von rohen Status-Emoji auf theme.led()
umgestellt wurden — nur dort, wo die Streamlit-Umgebung (st.markdown /
Spalten-.markdown) tatsächlich HTML rendert. Muster wie
test_dashboard_live_tab.py: AppTest.from_string mit minimalem Ctx-Stub,
echte Analyzer-Klassen per monkeypatch durch Fakes ersetzt.
"""
from streamlit.testing.v1 import AppTest

import analyzers.analysis_log as alog_mod
import analyzers.user_request_queue as urq_mod


class _FakeAnalysisLog:
    def get_current_stats(self):
        return {"total": 1, "buys": 1, "skips": 0, "holds": 0, "avg_score": 0.8}

    def get_stats(self):
        return {"total": 1, "buys": 1, "skips": 0, "holds": 0, "avg_score": 0.8}

    def get_last_cycle_tickers(self):
        return []

    def source_health(self, days=30):
        return {
            "healthy": ["yfinance"], "weak": ["reddit"], "dead": ["stocktwits"],
            "days": days, "reliable": True, "n_analyses": 50,
        }

    def get_recent(self, limit=2000, ticker=None):
        return [{
            "ticker": "AAPL", "recommendation": "BUY", "direction": "BULLISH",
            "sentiment_score": 0.8, "confidence": "HIGH",
            "analyzed_at": "2026-07-15T10:00:00", "entry_rationale": "Solide Zahlen",
            "bull_case": "Starkes Wachstum", "bear_case": "Bewertung hoch",
            "debate_winner": "BULL",
        }]

    def get_latest_per_ticker(self, limit=50):
        return self.get_recent()

    def get_prev_recommendation(self, ticker):
        return None


class _Ctx:
    _ALL_NAMES = {}
    _SOURCE_NAMES = {}

    def ticker_label(self, t):
        return t

    def _get_ticker_news(self, t):
        return []

    def render_sources_breakdown(self, breakdown, total=None):
        pass


class _NoNewsSentimentCtx(_Ctx):
    """MIXED ist kein bekannter Sentiment-Status — vorher zeigte das Log gar
    kein Icon, das muss auch nach der LED-Migration so bleiben."""
    def _get_ticker_news(self, t):
        return [{"title": "x", "publisher": "y", "overallSentiment": "MIXED"}]


_LOG_SCRIPT = """
from tests.test_dashboard_led_migration import _Ctx
from dashboard.tabs import log
log.render(_Ctx())
"""

_LOG_SCRIPT_UNKNOWN_SENTIMENT = """
from tests.test_dashboard_led_migration import _NoNewsSentimentCtx
from dashboard.tabs import log
log.render(_NoNewsSentimentCtx())
"""


def _prep(monkeypatch, tmp_path, theme_env):
    monkeypatch.setattr(alog_mod, "AnalysisLog", _FakeAnalysisLog)
    monkeypatch.setattr(urq_mod, "_FILE", str(tmp_path / "user_requests.json"))
    if theme_env is not None:
        monkeypatch.setenv("DASHBOARD_THEME", theme_env)
    else:
        monkeypatch.delenv("DASHBOARD_THEME", raising=False)


def test_source_health_uses_led_dots_in_pixel_mode(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path, "pixel")
    at = AppTest.from_string(_LOG_SCRIPT)
    at.run(timeout=30)
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert 'px-led px-led--ok' in html_out
    assert 'px-led px-led--warn' in html_out
    assert 'px-led px-led--err' in html_out
    # Gesund + Bull-Case sind beide "ok", Tot + Bear-Case beide "err":
    assert html_out.count('px-led px-led--ok') >= 2
    assert html_out.count('px-led px-led--err') >= 2


def test_source_health_falls_back_to_emoji_in_plain_mode(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path, "plain")
    at = AppTest.from_string(_LOG_SCRIPT)
    at.run(timeout=30)
    assert not at.exception
    md_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-led" not in md_out
    assert "🟢" in md_out and "🟡" in md_out and "🔴" in md_out


def test_unknown_sentiment_shows_no_led(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path, "pixel")
    at = AppTest.from_string(_LOG_SCRIPT_UNKNOWN_SENTIMENT)
    at.run(timeout=30)
    assert not at.exception
