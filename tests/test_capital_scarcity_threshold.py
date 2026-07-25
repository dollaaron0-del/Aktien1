"""
Tests für den Kapitalknappheits-Aufschlag auf buy_threshold (25.7.2026).

Auslöser: eine Kaufwoche band das komplette freie Kapital, weil auch
mittelmäßige Signale noch durchgingen, bis das Cash-Polster abrupt am
Reserve-Boden aufschlug — danach war der Bot für 6+ Tage komplett
handlungsunfähig, bis die ersten Positionen turnusmäßig frei wurden.

Semantik: sinkt das freie Cash (% der Equity), steigt buy_threshold
asymmetrisch (nur strenger, nie lockerer, wie der bestehende Makro-
Aufschlag). Bei Cash >= capital_scarcity_cash_pct_full (Default 20 %)
unverändert ("100 % Kapital → Hürden wie bisher", User-Vorgabe); bei
Cash <= capital_scarcity_cash_pct_empty (Default 5 %, nahe dem harten
Reserve-Boden) der volle Aufschlag; dazwischen linear interpoliert.
"""
import types
from datetime import datetime, timezone

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy


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
        capital_scarcity_cash_pct_full=0.20,
        capital_scarcity_cash_pct_empty=0.05,
        capital_scarcity_max_adj=0.15,
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


# ── Kernverhalten ────────────────────────────────────────────────────────────

def test_full_cash_no_adjustment(tmp_path, monkeypatch):
    """100 % Kapital verfügbar (all-cash) → Schwelle bleibt wie bisher."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)
    cfg = _config()
    # Sentiment knapp über der unveränderten Schwelle 0.65 → BUY.
    res = _evaluate(strat, 0.66, cfg)
    assert res.action == "BUY"


def test_scarce_cash_raises_threshold_and_blocks_mediocre_signal(tmp_path, monkeypatch):
    """Cash am unteren Rand (5 % von Equity) → voller Aufschlag (+0.15);
    ein Sentiment, das bei normaler Schwelle gekauft hätte, wird jetzt geskippt."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 950, 100.0))   # bindet 95_000, Cash 5_000 (5%)
    strat = make_strategy(p)
    cfg = _config()

    res = _evaluate(strat, 0.66, cfg)
    assert res.action == "SKIP"
    assert "Schwelle" in res.reason
    assert "0.80" in res.reason   # 0.65 + 0.15 voller Aufschlag


def test_scarce_cash_still_buys_on_strong_signal(tmp_path, monkeypatch):
    """Der Aufschlag blockt nicht kategorisch – ein wirklich starkes Signal
    kommt trotz knappem Cash noch durch."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 950, 100.0))
    strat = make_strategy(p)
    cfg = _config()
    res = _evaluate(strat, 0.90, cfg)
    assert res.action == "BUY"


def test_linear_interpolation_between_full_and_empty(tmp_path, monkeypatch):
    """Cash genau in der Mitte (12,5 % von 20/5) → halber Aufschlag (+0.075)."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 875, 100.0))    # Cash 12_500 (12.5%)
    strat = make_strategy(p)
    cfg = _config()

    res = _evaluate(strat, 0.70, cfg)   # 0.65+0.075=0.725 < 0.70 < ohne Aufschlag würde kaufen
    assert res.action == "SKIP"
    assert "0.72" in res.reason or "0.73" in res.reason  # 0.725 gerundet


def test_disabled_flag_ignores_cash_level(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 950, 100.0))    # 5% Cash
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
    p.open_position(make_position("AAPL", 950, 100.0))    # 5% Cash → +0.15
    strat = make_strategy(p)
    cfg = _config()

    # 0.65 (Basis) + 0.08 (Makro <= -0.6) + 0.15 (Cash) = 0.88
    res = _evaluate(strat, 0.85, cfg)
    assert res.action == "SKIP"
    res2 = _evaluate(strat, 0.90, cfg)
    assert res2.action == "BUY"
