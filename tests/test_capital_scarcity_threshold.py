"""
Tests für den Kapitalknappheits-Aufschlag auf buy_threshold (25.7.2026).

Auslöser: eine Kaufwoche band das komplette freie Kapital, weil auch
mittelmäßige Signale noch durchgingen, bis das Cash-Polster abrupt am
Reserve-Boden aufschlug — danach war der Bot für 6+ Tage komplett
handlungsunfähig, bis die ersten Positionen turnusmäßig frei wurden.

Semantik (nach User-Präzisierung, zweite Iteration): keine harten Stufen.
Der Aufschlag ist eine durchgehende Exponentialkurve über die GESAMTE Spanne
von 0-100% Cash: max_adj * 2^(-cash_pct / pivot_pct).
  - cash_pct=0 (kein Cash mehr)          → exakt max_adj (asymptotisches Maximum)
  - cash_pct=pivot_pct ("Kipppunkt")     → exakt max_adj/2
  - cash_pct→100%                        → geht glatt gegen 0, nie hart geklemmt
Eine erste Version hatte noch feste Plateaus (0 oberhalb 20% Cash, Maximum
unterhalb 5%) mit linearer/potenzierter Interpolation nur dazwischen - genau
die harten Grenzen, die der User in der Präzisierung explizit ausschloss.
"""
import types
from datetime import datetime, timezone

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy, _capital_scarcity_adjustment


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", str(tmp_path / "data" / "portfolio.db"))
    return Portfolio(initial_capital=capital)


def make_position(ticker, shares, entry_price):
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def make_strategy(portfolio):
    strat = object.__new__(SwingStrategy)
    strat.portfolio = portfolio
    strat.kelly_sizer = None
    strat.goal_risk_assessor = None
    strat.correlation_checker = None
    strat.signal_queue = None
    strat.earnings_filter = None
    strat._conditional_watcher = None
    strat.focus_ctrl = types.SimpleNamespace(get_max_positions=lambda _v: 20)
    return strat


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import analyzers.macro_context as mc
    monkeypatch.setattr(
        mc, "get_macro_context",
        lambda: types.SimpleNamespace(bias_score=lambda: 0.0, size_modifier=lambda t: 1.0),
    )
    import analyzers.sl_cooldown as slc
    monkeypatch.setattr(
        slc, "StopLossCooldown",
        lambda: types.SimpleNamespace(is_blocked=lambda t: (False, "")),
    )
    import analyzers.liquidity as liq
    monkeypatch.setattr(liq, "check_liquidity",
                        lambda t, p: types.SimpleNamespace(ok=True, reason=""))


def _config(**over):
    base = dict(
        buy_threshold=0.65, min_sources=1,
        learning_filter_enabled=False, earnings_filter_enabled=False,
        capital_scarcity_threshold_enabled=True,
        capital_scarcity_max_adj=0.15,
        capital_scarcity_pivot_pct=0.10,
        max_position_pct=0.20, max_single_position_pct=1.0,
        conviction_max_bonus=0.6, cash_reserve_pct=0.0,
        cash_reserve_hard_pct=0.0, reflow_sizing_enabled=False,
        reflow_lookahead_days=5,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


_PARAMS = types.SimpleNamespace(
    buy_threshold_adj=0.0, position_size_mult=1.0,
    sl_pct=0.06, tp_pct=0.22, hold_days_mult=1.0,
)


def _analysis(sentiment):
    return types.SimpleNamespace(
        sentiment_score=sentiment, confidence="HIGH", direction="BULLISH",
        recommendation="BUY", ticker="NVDA", sources_used=5,
        suggested_hold_days=14, entry_rationale="", key_catalysts=[],
    )


def _evaluate(strat, sentiment, config):
    return strat._evaluate_new("NVDA", _analysis(sentiment), 100.0, _PARAMS,
                               "BULL", False, None, config)


# ── Reine Kurvenform (_capital_scarcity_adjustment direkt) ─────────────────

def test_zero_cash_hits_exact_max():
    """Kein Cash mehr → exakt das asymptotische Maximum."""
    assert _capital_scarcity_adjustment(0.0, max_adj=0.15, pivot_pct=0.10) == pytest.approx(0.15)


def test_pivot_point_is_exactly_half_max():
    """Der Kipppunkt ist per Definition dort, wo der Aufschlag die Hälfte
    des Maximums erreicht hat."""
    adj = _capital_scarcity_adjustment(0.10, max_adj=0.15, pivot_pct=0.10)
    assert adj == pytest.approx(0.075)


def test_full_cash_is_negligible_but_not_zero():
    """100 % Cash: der Aufschlag ist praktisch nicht spürbar, aber KEIN
    hartes Plateau bei exakt 0 - anders als die erste Implementierung."""
    adj = _capital_scarcity_adjustment(1.0, max_adj=0.15, pivot_pct=0.10)
    assert 0.0 < adj < 0.001


def test_curve_has_no_plateau_strictly_monotonic():
    """Über die gesamte Spanne streng monoton fallend - kein Bereich, in dem
    der Aufschlag konstant bleibt (das wäre wieder eine harte Stufe)."""
    cash_levels = [0.0, 0.01, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50, 0.80, 1.0]
    adjustments = [_capital_scarcity_adjustment(c, 0.15, 0.10) for c in cash_levels]
    for a, b in zip(adjustments, adjustments[1:]):
        assert a > b, f"Kurve muss überall streng fallen: {adjustments}"


def test_smaller_pivot_decays_faster():
    """Kleinerer Kipppunkt-Wert → die Kurve fällt schon bei mehr Cash stark ab."""
    adj_tight = _capital_scarcity_adjustment(0.10, max_adj=0.15, pivot_pct=0.05)
    adj_loose = _capital_scarcity_adjustment(0.10, max_adj=0.15, pivot_pct=0.20)
    assert adj_tight < adj_loose


def test_invalid_pivot_returns_zero():
    assert _capital_scarcity_adjustment(0.10, max_adj=0.15, pivot_pct=0.0) == 0.0
    assert _capital_scarcity_adjustment(0.10, max_adj=0.15, pivot_pct=-1.0) == 0.0


def test_negative_cash_pct_clamped_to_zero_not_exceeding_max():
    """Ein (eigentlich unmögliches) negatives cash_pct darf den Aufschlag
    nicht über max_adj hinaustreiben - Sicherheitsnetz, keine Policy-Grenze."""
    adj = _capital_scarcity_adjustment(-0.05, max_adj=0.15, pivot_pct=0.10)
    assert adj == pytest.approx(0.15)


# ── End-to-End über _evaluate_new ───────────────────────────────────────────

def test_full_cash_normal_signal_still_buys(tmp_path, monkeypatch):
    """All-Cash-Portfolio: der winzige Aufschlag ändert am Kaufverhalten
    nichts spürbares."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)
    cfg = _config()
    res = _evaluate(strat, 0.66, cfg)
    assert res.action == "BUY"


def test_scarce_cash_blocks_mediocre_signal(tmp_path, monkeypatch):
    """Cash nahe null → nahe voller Aufschlag; ein Sentiment, das bei
    normaler Schwelle gekauft hätte, wird jetzt geskippt."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 990, 100.0))   # Cash 1_000 (1%)
    strat = make_strategy(p)
    cfg = _config()

    res = _evaluate(strat, 0.66, cfg)
    assert res.action == "SKIP"
    assert "Schwelle" in res.reason


def test_scarce_cash_still_buys_on_strong_signal(tmp_path, monkeypatch):
    """Der Aufschlag blockt nicht kategorisch – ein wirklich starkes Signal
    kommt trotz knappem Cash noch durch."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 990, 100.0))
    strat = make_strategy(p)
    cfg = _config()
    res = _evaluate(strat, 0.90, cfg)
    assert res.action == "BUY"


def test_pivot_point_end_to_end(tmp_path, monkeypatch):
    """Am Kipppunkt (10 % Cash) ist die Schwelle exakt 0.65+0.075=0.725."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 900, 100.0))    # Cash 10_000 (10%)
    strat = make_strategy(p)
    cfg = _config()

    res_below = _evaluate(strat, 0.72, cfg)
    assert res_below.action == "SKIP"
    res_above = _evaluate(strat, 0.73, cfg)
    assert res_above.action == "BUY"


def test_disabled_flag_ignores_cash_level(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 990, 100.0))    # 1% Cash
    strat = make_strategy(p)
    cfg = _config(capital_scarcity_threshold_enabled=False)

    res = _evaluate(strat, 0.66, cfg)
    assert res.action == "BUY"


def test_zero_equity_does_not_crash(tmp_path, monkeypatch):
    """Randfall: keine sinnvolle Equity (sollte praktisch nie vorkommen) darf
    nicht crashen – fail-open."""
    p = make_portfolio(tmp_path, monkeypatch, capital=0.0)
    strat = make_strategy(p)
    cfg = _config()
    res = _evaluate(strat, 0.90, cfg)
    assert res.action in ("BUY", "SKIP")   # kein Crash


def test_scarcity_and_macro_headwind_stack(tmp_path, monkeypatch):
    """Beide Aufschläge sind unabhängige, additive Verschärfungen."""
    import analyzers.macro_context as mc
    monkeypatch.setattr(
        mc, "get_macro_context",
        lambda: types.SimpleNamespace(bias_score=lambda: -0.7, size_modifier=lambda t: 1.0),
    )
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 990, 100.0))    # 1% Cash → nahe max_adj
    strat = make_strategy(p)
    cfg = _config()

    # 0.65 (Basis) + 0.08 (Makro <= -0.6) + ~0.145 (Cash, nahe Maximum) ≈ 0.875
    res = _evaluate(strat, 0.85, cfg)
    assert res.action == "SKIP"
    res2 = _evaluate(strat, 0.90, cfg)
    assert res2.action == "BUY"
