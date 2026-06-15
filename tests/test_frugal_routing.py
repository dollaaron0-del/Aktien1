"""
Tests für das Frugal-Modus-Routing in ClaudeAnalyzer.

Im Frugal-Modus (Daten-Sammel-Modus) soll die lokale Engine (Ollama/MLX)
deterministisch ALLE normalen Neu-Analysen übernehmen. Claude nur noch bei
echten Katalysatoren (SEC 8-K / Earnings), offenen Positionen oder force_claude.
"""
import config as _config_mod
from analyzers.claude_analyzer import ClaudeAnalyzer, AnalysisResult


class _FakePrescreener:
    def __init__(self):
        self.calls = 0
        self.thesis_calls = 0
        self.compress_calls = 0

    def is_available(self):
        return True

    def compress_news(self, ticker, news_items, max_items=20):
        self.compress_calls += 1
        return f"BRIEFING zu {ticker}"

    def full_analysis(self, ticker, news_items, price_data, buy_min_score):
        self.calls += 1
        return {
            "sentiment_score": 0.72, "direction": "BULLISH", "confidence": "HIGH",
            "recommendation": "BUY", "suggested_hold_days": 10,
            "entry_rationale": "lokal begründet", "risk_factors": ["r1"],
            "key_catalysts": ["c1"], "summary": "lokale Zusammenfassung",
        }

    def generate(self, prompt, max_tokens=300):
        self.thesis_calls += 1
        return ('{"thesis_valid": false, "thesis_break_reason": "Bruch", '
                '"sentiment_score": 0.3, "recommendation": "SELL"}')


def _analyzer(monkeypatch, frugal=True):
    monkeypatch.setattr(_config_mod.config, "frugal_mode", frugal)
    a = ClaudeAnalyzer(api_key="test-key")
    fake = _FakePrescreener()
    monkeypatch.setattr(a, "_get_prescreener", lambda: fake)
    # Claude darf in diesen Tests NICHT laufen, außer explizit erwartet:
    def _boom(*args, **kwargs):
        raise AssertionError("Claude wurde fälschlich aufgerufen")
    monkeypatch.setattr(a, "_claude_analysis", _boom)
    return a, fake


def test_frugal_normal_news_uses_local_engine(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    res = a.analyze(
        ticker="ABCD",
        news_items=[{"source": "Reuters", "title": "ABCD launches new product"}],
        price_data={"current_price": 10.0},
    )
    assert fake.calls == 1                      # lokale Engine lief
    assert res.recommendation == "BUY"
    assert res.entry_rationale == "lokal begründet"
    assert res.sentiment_score == 0.72


def test_frugal_reuters_no_longer_forces_claude(monkeypatch):
    """Reuters/Bloomberg allein darf NICHT mehr zu Claude eskalieren."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    # _claude_analysis = _boom → würde werfen, wenn fälschlich Claude liefe
    res = a.analyze(
        ticker="XYZ",
        news_items=[{"source": "Bloomberg", "title": "XYZ news"}],
        price_data={"current_price": 5.0},
    )
    assert fake.calls == 1
    assert res.recommendation == "BUY"


def test_frugal_real_catalyst_goes_to_claude(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("CAT", 0.9, "BULLISH", "HIGH", "BUY",
                              entry_rationale="claude")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="CAT",
        news_items=[{"source": "SEC 8-K", "title": "Material event"}],
        price_data={"current_price": 20.0},
    )
    assert fake.calls == 0                       # lokale Engine NICHT gelaufen
    assert res.entry_rationale == "claude"       # Claude übernahm


def test_frugal_local_offline_falls_back_to_claude(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)

    class _Offline(_FakePrescreener):
        def is_available(self):
            return False
    monkeypatch.setattr(a, "_get_prescreener", lambda: _Offline())
    sentinel = AnalysisResult("OFF", 0.5, "NEUTRAL", "LOW", "SKIP",
                              entry_rationale="claude-fallback")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="OFF",
        news_items=[{"source": "Reuters", "title": "news"}],
        price_data={"current_price": 8.0},
    )
    assert res.entry_rationale == "claude-fallback"


class _Position:
    entry_price = 10.0
    rationale = "ursprüngliche Kaufthese"


def test_frugal_open_position_uses_local_thesis_check(monkeypatch):
    """Offene Positionen werden im Frugal-Modus lokal geprüft (Paper-Trading)."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    monkeypatch.setattr(a, "_thesis_check", lambda *args, **kw: (_ for _ in ()).throw(
        AssertionError("Claude-Thesis-Check fälschlich aufgerufen")))
    res = a.analyze(
        ticker="POS",
        news_items=[{"source": "Reuters", "title": "POS schlechte News"}],
        price_data={"current_price": 12.0},
        existing_position=_Position(),
    )
    assert fake.thesis_calls == 1
    assert fake.calls == 0                       # full_analysis NICHT (ist Position)
    assert res.thesis_valid is False
    assert res.recommendation == "SELL"


def test_frugal_open_position_with_catalyst_goes_to_claude(monkeypatch):
    """Bei echtem Katalysator geht auch der Thesis-Check an Claude."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("POS", 0.4, "BEARISH", "HIGH", "SELL",
                              entry_rationale="claude-thesis")
    monkeypatch.setattr(a, "_thesis_check", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="POS",
        news_items=[{"source": "SEC 8-K", "title": "Material event"}],
        price_data={"current_price": 12.0},
        existing_position=_Position(),
    )
    assert fake.thesis_calls == 0
    assert res.entry_rationale == "claude-thesis"


def test_frugal_catalyst_claude_gets_compressed_news(monkeypatch):
    """Bei Claude-Fällen dampft Ollama die News vorher lokal zu einem Briefing
    ein (spart Claude-Tokens)."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    captured = {}
    monkeypatch.setattr(
        a, "_claude_analysis",
        lambda ticker, news_text, *a_, **k_: captured.update(news_text=news_text)
        or AnalysisResult(ticker, 0.5, "NEUTRAL", "LOW", "SKIP"),
    )
    a.analyze(
        ticker="CAT",
        news_items=[{"source": "SEC 8-K", "title": "Material event"}],
        price_data={"current_price": 20.0},
    )
    assert fake.compress_calls == 1
    assert "Ollama-komprimiert" in captured["news_text"]
    assert "BRIEFING zu CAT" in captured["news_text"]


def test_force_claude_bypasses_local(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("FRC", 0.6, "BULLISH", "MEDIUM", "HOLD",
                              entry_rationale="claude-forced")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="FRC",
        news_items=[{"source": "Reuters", "title": "news"}],
        price_data={"current_price": 12.0},
        force_claude=True,
    )
    assert fake.calls == 0
    assert res.entry_rationale == "claude-forced"
