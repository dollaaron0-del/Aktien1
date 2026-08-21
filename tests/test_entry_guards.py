"""
Tests für zwei Einstiegs-Guards, die am 24.7.2026 gefehlt haben.

1. Cross-Listing: SAP.DE lag mit 6 % Gewicht im Depot, der Momentum-Scanner
   triggerte auf das US-ADR "SAP" — und die Strategie bewertete das als
   brandneue Firma (_evaluate_new). Sizing, Einzelpositions-Deckel und
   Korrelations-Check sahen zwei getrennte Werte. canonical() kannte das
   Mapping SAP.DE → SAP längst, war aber nirgends im Handelspfad verdrahtet.

2. Sub-1-Stück: Weil der Cash-Reserve-Boden fast erreicht war, kam eine
   Positionsgröße von 0,86 Stück heraus. Die Order ging trotzdem raus, scheiterte
   im Broker (IBKR handelt keine Teilaktien) und löste einen lauten
   "BUY-Order fehlgeschlagen"-Alarm aus — obwohl nur das Budget zu klein war.
"""
import types

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy, _is_fractional_asset


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", str(tmp_path / "data" / "portfolio.db"))
    return Portfolio(initial_capital=capital)


def make_position(ticker, shares, entry_price):
    from datetime import datetime, timezone
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def make_strategy(portfolio):
    """SwingStrategy ohne den schweren Konstruktor – nur was die geprüften
    Pfade brauchen."""
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
    """Makro-Kontext und SL-Cooldown neutralisieren – diese Tests prüfen die
    Guards, nicht die Netzwerk-Pfade (und sollen ohne Netz schnell laufen)."""
    import analyzers.macro_context as mc
    monkeypatch.setattr(
        mc, "get_macro_context",
        lambda: types.SimpleNamespace(bias_score=lambda: 0.0,
                                      size_modifier=lambda t: 1.0),
    )
    import analyzers.sl_cooldown as slc
    monkeypatch.setattr(
        slc, "StopLossCooldown",
        lambda: types.SimpleNamespace(is_blocked=lambda t: (False, "")),
    )


# ── 1. Cross-Listing ─────────────────────────────────────────────────────────

def test_finds_position_held_under_other_listing(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("SAP.DE", 406, 138.64))
    strat = make_strategy(p)
    assert strat._held_under_other_listing("SAP") == "SAP.DE"


def test_same_ticker_is_not_reported_as_cross_listing(tmp_path, monkeypatch):
    """Der eigene Ticker zählt nicht – dafür ist _evaluate_existing zuständig."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("SAP.DE", 406, 138.64))
    strat = make_strategy(p)
    assert strat._held_under_other_listing("SAP.DE") is None


def test_unrelated_ticker_is_not_blocked(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("SAP.DE", 406, 138.64))
    strat = make_strategy(p)
    assert strat._held_under_other_listing("MSFT") is None


def test_evaluate_skips_buy_for_cross_listed_company(tmp_path, monkeypatch):
    """Der eigentliche Regressionstest: mit SAP.DE im Depot darf ein BUY auf
    SAP nicht mehr als Neuposition durchlaufen."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("SAP.DE", 406, 138.64))
    strat = make_strategy(p)
    monkeypatch.setattr(SwingStrategy, "_circuit_breaker_active", lambda self, cfg: False)

    def _fail(*_a, **_kw):
        pytest.fail("_evaluate_new darf für ein Cross-Listing nicht laufen")

    monkeypatch.setattr(SwingStrategy, "_evaluate_new", _fail)

    analysis = types.SimpleNamespace(
        sentiment_score=0.83, confidence="HIGH", direction="BULLISH",
        recommendation="BUY", ticker="SAP", sources_used=12,
    )
    res = strat._evaluate_inner("SAP", analysis, 160.0, "BULL", False, None)

    assert res.action == "SKIP"
    assert "SAP.DE" in res.reason and "Cross-Listing" in res.reason


def test_cross_listing_check_fails_open(tmp_path, monkeypatch):
    """Kaputtes Mapping darf den Handel nicht blockieren."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("SAP.DE", 406, 138.64))
    strat = make_strategy(p)
    import analyzers.stock_relations as sr

    def _boom(_t):
        raise RuntimeError("Mapping kaputt")

    monkeypatch.setattr(sr, "canonical", _boom)
    assert strat._held_under_other_listing("SAP") is None


# ── 2. Sub-1-Stück ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,fractional", [
    ("BTC/USD", True),
    ("ETH-USD", True),
    ("SAP.DE",  False),
    ("AAPL",    False),
])
def test_fractional_asset_detection(ticker, fractional):
    assert _is_fractional_asset(ticker) is fractional


def test_buy_skipped_when_budget_below_one_share(tmp_path, monkeypatch):
    """0,86 Stück → sauberer SKIP statt garantiert scheiternder Order."""
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    # Nur noch ~138 USD einsetzbar, Kurs 160 → 0.8618 Stück (der reale Fall).
    monkeypatch.setattr(SwingStrategy, "_calc_position_size",
                        lambda self, *a, **kw: 137.89)
    monkeypatch.setattr(SwingStrategy, "_has_insider_confluence", lambda self, t: False)
    import analyzers.liquidity as liq
    monkeypatch.setattr(liq, "check_liquidity",
                        lambda t, p: types.SimpleNamespace(ok=True, reason=""))

    config = types.SimpleNamespace(
        buy_threshold=0.70, min_sources=3,
        learning_filter_enabled=False, earnings_filter_enabled=False,
    )
    params = types.SimpleNamespace(
        buy_threshold_adj=0.0, position_size_mult=1.0,
        sl_pct=0.06, tp_pct=0.22, hold_days_mult=1.0,
    )
    analysis = types.SimpleNamespace(
        sentiment_score=0.83, confidence="HIGH", direction="BULLISH",
        recommendation="BUY", ticker="SAP", sources_used=12,
        suggested_hold_days=14, entry_rationale="", key_catalysts=[],
    )

    res = strat._evaluate_new("SAP", analysis, 160.0, params, "BULL", False, None, config)

    assert res.action == "SKIP"
    assert "< 1" in res.reason and "0.86" in res.reason


def test_buy_proceeds_when_budget_covers_a_whole_share(tmp_path, monkeypatch):
    """Gegenprobe: ab einer ganzen Aktie läuft der Kauf normal weiter."""
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    monkeypatch.setattr(SwingStrategy, "_calc_position_size",
                        lambda self, *a, **kw: 3_200.0)
    monkeypatch.setattr(SwingStrategy, "_has_insider_confluence", lambda self, t: False)
    import analyzers.liquidity as liq
    monkeypatch.setattr(liq, "check_liquidity",
                        lambda t, p: types.SimpleNamespace(ok=True, reason=""))

    config = types.SimpleNamespace(
        buy_threshold=0.70, min_sources=3,
        learning_filter_enabled=False, earnings_filter_enabled=False,
    )
    params = types.SimpleNamespace(
        buy_threshold_adj=0.0, position_size_mult=1.0,
        sl_pct=0.06, tp_pct=0.22, hold_days_mult=1.0,
    )
    analysis = types.SimpleNamespace(
        sentiment_score=0.83, confidence="HIGH", direction="BULLISH",
        recommendation="BUY", ticker="SAP", sources_used=12,
        suggested_hold_days=14, entry_rationale="", key_catalysts=[],
    )

    res = strat._evaluate_new("SAP", analysis, 160.0, params, "BULL", False, None, config)

    assert res.action == "BUY"
    assert res.shares == pytest.approx(20.0)
