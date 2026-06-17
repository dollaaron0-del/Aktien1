"""
Tests für den deterministischen BUY-Boden (_enforce_buy_floor).

Hintergrund: Vor FOMC/CPI-Terminen stufte das Modell überzeugende Kaufsignale
pauschal auf HOLD herab (Doppelzählung der Makro-Vorsicht → 0 BUYs trotz Risk-On).
Der Boden erzwingt BUY für ein klares bullishes Signal; das eigentliche
Risiko-Overlay (Kaufschwelle inkl. Makro-/Regime-Aufschlag) greift downstream
in der Strategie-Schicht (swing_strategy.evaluate) separat.
"""
import config as _config_mod
from analyzers.claude_analyzer import ClaudeAnalyzer, AnalysisResult


def _result(**kw) -> AnalysisResult:
    base = dict(
        ticker="TEST",
        sentiment_score=0.80,
        direction="BULLISH",
        confidence="HIGH",
        recommendation="HOLD",
    )
    base.update(kw)
    return AnalysisResult(**base)


def test_floor_flips_strong_signal_to_buy():
    """BULLISH + HIGH + Score >= Schwelle: HOLD wird zu BUY erzwungen."""
    r = ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="HOLD"))
    assert r.recommendation == "BUY"


def test_floor_also_flips_skip():
    r = ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="SKIP", confidence="MEDIUM"))
    assert r.recommendation == "BUY"


def test_floor_keeps_below_threshold():
    """Score unter der Kaufschwelle bleibt HOLD – kein erzwungenes BUY."""
    r = ClaudeAnalyzer._enforce_buy_floor(
        _result(recommendation="HOLD", sentiment_score=_config_mod.config.buy_threshold - 0.05)
    )
    assert r.recommendation == "HOLD"


def test_floor_respects_low_confidence():
    """LOW-Konfidenz wird nicht zu BUY erzwungen."""
    r = ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="HOLD", confidence="LOW"))
    assert r.recommendation == "HOLD"


def test_floor_respects_non_bullish():
    """Nur BULLISH wird erzwungen; NEUTRAL/BEARISH bleiben unangetastet."""
    r = ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="HOLD", direction="NEUTRAL"))
    assert r.recommendation == "HOLD"


def test_floor_does_not_touch_existing_buy_or_sell():
    """BUY bleibt BUY; eine SELL/andere Empfehlung wird nicht angefasst."""
    assert ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="BUY")).recommendation == "BUY"
    assert ClaudeAnalyzer._enforce_buy_floor(_result(recommendation="SELL")).recommendation == "SELL"


def test_floor_handles_bad_score_gracefully():
    """Nicht-numerischer Score darf nicht crashen – Ergebnis bleibt unverändert."""
    r = _result(recommendation="HOLD")
    r.sentiment_score = None  # type: ignore[assignment]
    out = ClaudeAnalyzer._enforce_buy_floor(r)
    assert out.recommendation == "HOLD"
