"""
Tests für den Verarbeitungs-Trace (Roadmap 1.4c) auf AnalysisResult:
model_route/frugal_reason werden von ClaudeAnalyzer.analyze() an EINER
zentralen Stelle (_stamp_route) gesetzt, direkt vor jedem return – nicht von
den einzelnen Bau-Methoden (_thesis_check, _frugal_local_analysis, ...).
Reine Ergänzung zu test_frugal_routing.py (das die Routing-LOGIK prüft,
hier geht es nur um den zusätzlichen Trace auf dem Ergebnis).
"""
import config as _config_mod
from analyzers.llm_analyzer import ClaudeAnalyzer, AnalysisResult


class _FakePrescreener:
    def __init__(self):
        self.calls = 0
        self.thesis_calls = 0

    def is_available(self):
        return True

    def compress_news(self, ticker, news_items, max_items=20):
        return f"BRIEFING zu {ticker}"

    def full_analysis(self, ticker, news_items, price_data, buy_min_score):
        self.calls += 1
        return {
            "sentiment_score": 0.72, "direction": "BULLISH", "confidence": "HIGH",
            "recommendation": "BUY", "suggested_hold_days": 10,
            "entry_rationale": "lokal begründet", "risk_factors": [],
            "key_catalysts": [], "summary": "",
        }

    def generate(self, prompt, max_tokens=300):
        self.thesis_calls += 1
        return ('{"thesis_valid": false, "thesis_break_reason": "Bruch", '
                '"sentiment_score": 0.3, "recommendation": "SELL"}')


class _AllowTracker:
    def check_daily_limit(self):
        return (True, "")
    def record(self, **k):
        pass


class _BlockTracker:
    def check_daily_limit(self):
        return (False, "Tages-Kostenlimit erreicht")
    def record(self, **k):
        pass


class _Position:
    entry_price = 10.0
    rationale = "ursprüngliche Kaufthese"


def _analyzer(monkeypatch, frugal=True, tracker=None):
    monkeypatch.setattr(_config_mod.config, "frugal_mode", frugal)
    monkeypatch.setattr(_config_mod.config, "claude_result_cache_hours", 0)
    a = ClaudeAnalyzer(api_key="test-key")
    fake = _FakePrescreener()
    monkeypatch.setattr(a, "_get_prescreener", lambda: fake)
    a._cost_tracker = tracker or _AllowTracker()
    return a, fake


def test_empty_news_stamped_as_empty(monkeypatch):
    a, fake = _analyzer(monkeypatch)
    res = a.analyze(ticker="ABCD", news_items=[], price_data={"current_price": 10.0})
    assert res.model_route == "empty"
    assert res.frugal_reason


def test_frugal_full_analysis_stamped(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    res = a.analyze(
        ticker="ABCD",
        news_items=[{"source": "Reuters", "title": "ABCD launches new product"}],
        price_data={"current_price": 10.0},
    )
    assert res.model_route == "ollama_frugal_full"
    assert "Katalysator" in res.frugal_reason


def test_frugal_thesis_check_stamped(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    res = a.analyze(
        ticker="POS",
        news_items=[{"source": "Reuters", "title": "POS schlechte News"}],
        price_data={"current_price": 12.0},
        existing_position=_Position(),
    )
    assert res.model_route == "ollama_frugal_thesis"


def test_catalyst_goes_to_claude_stamped(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("CAT", 0.9, "BULLISH", "HIGH", "BUY", entry_rationale="claude")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="CAT",
        news_items=[{"source": "SEC 8-K", "title": "Material event"}],
        price_data={"current_price": 20.0},
    )
    assert res.model_route == "claude"
    assert "Katalysator" in res.frugal_reason


def test_force_claude_stamped_with_force_reason(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("FRC", 0.6, "BULLISH", "MEDIUM", "HOLD", entry_rationale="x")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="FRC",
        news_items=[{"source": "Reuters", "title": "news"}],
        price_data={"current_price": 12.0},
        force_claude=True,
    )
    assert res.model_route == "claude"
    assert res.frugal_reason == "force_claude gesetzt"


def test_frugal_mode_off_stamped_default_path(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=False)
    sentinel = AnalysisResult("OFF", 0.6, "BULLISH", "MEDIUM", "HOLD", entry_rationale="x")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    res = a.analyze(
        ticker="OFF",
        news_items=[{"source": "Reuters", "title": "news"}],
        price_data={"current_price": 12.0},
    )
    assert res.model_route == "claude"
    assert res.frugal_reason == "frugal_mode aus"


def test_budget_exhausted_stamped_ollama_fallback(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True, tracker=_BlockTracker())
    res = a.analyze(
        ticker="CAT",
        news_items=[{"source": "SEC 8-K", "title": "Material event"}],
        price_data={"current_price": 20.0},
    )
    assert res.model_route == "ollama_fallback"
    assert "Tagesbudget" in res.frugal_reason


def test_no_credit_stamped_ollama_fallback(monkeypatch):
    a, fake = _analyzer(monkeypatch, frugal=True)
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kwargs: None)
    res = a.analyze(
        ticker="CAT",
        news_items=[{"source": "SEC 8-K", "title": "x"}],
        price_data={"current_price": 10.0},
    )
    assert res.model_route == "ollama_fallback"
    assert "Guthaben" in res.frugal_reason or "Auth" in res.frugal_reason


def test_claude_analysis_stamps_raw_prompt_and_response(monkeypatch):
    """Roadmap 1.4d: ein echter Claude-Aufruf (_claude_analysis) stempelt
    Modell/System-Prompt/User-Prompt/Antwort direkt aufs Ergebnis."""
    a, fake = _analyzer(monkeypatch, frugal=False)
    captured = {}

    def _fake_call_claude(user_prompt, context_block, model, max_tokens):
        captured["user_prompt"] = user_prompt
        captured["model"] = model
        return ('{"sentiment_score": 0.8, "direction": "BULLISH", "confidence": "HIGH", '
                 '"recommendation": "BUY", "entry_rationale": "x", "risk_factors": [], '
                 '"key_catalysts": []}')

    monkeypatch.setattr(a, "_call_claude", _fake_call_claude)
    res = a.analyze(
        ticker="RAW",
        news_items=[{"source": "Reuters", "title": "news"}],
        price_data={"current_price": 12.0},
    )
    assert res.raw_model == a.model == captured["model"]
    assert res.raw_user_prompt == captured["user_prompt"]
    assert "RAW" in res.raw_user_prompt
    assert res.raw_system_prompt
    assert res.raw_response.startswith("{")


def test_thesis_check_stamps_raw_prompt_and_response(monkeypatch):
    """Roadmap 1.4d: der echte Claude-Thesis-Check stempelt ebenfalls, mit dem
    leichten Modell statt self.model."""
    a, fake = _analyzer(monkeypatch, frugal=False)

    def _fake_call_claude(user_prompt, context_block, model, max_tokens):
        return ('{"thesis_valid": false, "thesis_break_reason": "Bruch", '
                 '"sentiment_score": 0.3, "recommendation": "SELL"}')

    monkeypatch.setattr(a, "_call_claude", _fake_call_claude)
    res = a.analyze(
        ticker="POS",
        news_items=[{"source": "Reuters", "title": "POS schlechte News"}],
        price_data={"current_price": 12.0},
        existing_position=_Position(),
    )
    assert res.raw_model == a._light_model()
    assert res.raw_response.startswith("{")


def test_ollama_route_leaves_raw_fields_empty(monkeypatch):
    """Nur ECHTE Claude-Aufrufe werden archiviert – Ollama-/Frugal-Routen
    liefern bewusst kein raw_response (siehe cycle_analysis.py-Gate)."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    res = a.analyze(
        ticker="ABCD",
        news_items=[{"source": "Reuters", "title": "ABCD launches new product"}],
        price_data={"current_price": 10.0},
    )
    assert res.model_route == "ollama_frugal_full"
    assert res.raw_response == ""
    assert res.raw_system_prompt == ""


def test_dedup_cache_hit_has_empty_raw_fields(monkeypatch, tmp_path):
    """Roadmap 1.4d: ein Cache-Hit ist kein neuer Claude-Aufruf – raw_* muss
    leer bleiben, sonst würde der alte Prompt fälschlich unter einer neuen
    analysis_id erneut archiviert (siehe _result_cache_store)."""
    a, fake = _analyzer(monkeypatch, frugal=True)
    monkeypatch.setattr(_config_mod.config, "claude_result_cache_hours", 1)
    monkeypatch.setattr(a, "_RESULT_CACHE_FILE", str(tmp_path / "cache.json"))

    def _fake_call_claude(user_prompt, context_block, model, max_tokens):
        return ('{"sentiment_score": 0.9, "direction": "BULLISH", "confidence": "HIGH", '
                 '"recommendation": "BUY", "entry_rationale": "x", "risk_factors": [], '
                 '"key_catalysts": []}')

    monkeypatch.setattr(a, "_call_claude", _fake_call_claude)
    news = [{"source": "SEC 8-K", "title": "Material event"}]
    r1 = a.analyze(ticker="CAT", news_items=news, price_data={"current_price": 20.0})
    assert r1.raw_response

    r2 = a.analyze(ticker="CAT", news_items=news, price_data={"current_price": 20.0})
    assert r2.model_route == "claude_dedup_cache"
    assert r2.raw_response == ""
    assert r2.raw_system_prompt == ""
    assert r2.raw_user_prompt == ""


def test_dedup_cache_hit_stamped(monkeypatch, tmp_path):
    a, fake = _analyzer(monkeypatch, frugal=True)
    sentinel = AnalysisResult("CAT", 0.9, "BULLISH", "HIGH", "BUY", entry_rationale="claude")
    monkeypatch.setattr(a, "_claude_analysis", lambda *args, **kw: sentinel)
    monkeypatch.setattr(_config_mod.config, "claude_result_cache_hours", 1)
    # eigene Cache-Datei statt der echten data/claude_result_cache.json — sonst
    # leckt ein Eintrag auf Disk und vergiftet spätere Testläufe (siehe
    # test_frugal_routing.py-Kommentar zum selben Fallstrick).
    monkeypatch.setattr(a, "_RESULT_CACHE_FILE", str(tmp_path / "cache.json"))
    news = [{"source": "SEC 8-K", "title": "Material event"}]
    r1 = a.analyze(ticker="CAT", news_items=news, price_data={"current_price": 20.0})
    assert r1.model_route == "claude"
    r2 = a.analyze(ticker="CAT", news_items=news, price_data={"current_price": 20.0})
    assert r2.model_route == "claude_dedup_cache"
    assert "Cache-TTL" in r2.frugal_reason
