"""
Tests für SwingStrategy._calc_position_size — Sizing auf Gesamt-Equity.

Regression: Früher rechnete die Basis auf `portfolio.cash`. Dadurch wurden
spätere Käufe im selben Zyklus zu klein, weil das Cash mit jedem Buy sinkt.
Korrekt ist ein Anteil der Gesamt-Equity (Cash + Positionswert).
"""
import types

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_position(ticker, shares, entry_price):
    from datetime import datetime, timezone
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def make_strategy(portfolio, kelly_sizer=None, goal_risk_assessor=None):
    """Baut eine SwingStrategy ohne den schweren Konstruktor — nur die
    Attribute, die _calc_position_size tatsächlich benötigt."""
    strat = object.__new__(SwingStrategy)
    strat.portfolio = portfolio
    strat.kelly_sizer = kelly_sizer
    strat.goal_risk_assessor = goal_risk_assessor
    return strat


@pytest.fixture(autouse=True)
def neutral_macro(monkeypatch):
    """Makro- UND ATR-Modifier neutralisieren, damit das Sizing deterministisch ist."""
    import analyzers.macro_context as mc
    fake = types.SimpleNamespace(size_modifier=lambda ticker: 1.0)
    monkeypatch.setattr(mc, "get_macro_context", lambda: fake)
    # ATR-Vol-Sizing über fail-open neutralisieren (calculate→None ⇒ Faktor 1.0).
    import analyzers.technical_indicators as ti
    monkeypatch.setattr(
        ti, "TechnicalIndicators",
        lambda: types.SimpleNamespace(calculate=lambda t: None),
    )


_PARAMS = types.SimpleNamespace(position_size_mult=1.0)
_CONFIG = types.SimpleNamespace(max_position_pct=0.20)
_ANALYSIS = types.SimpleNamespace(confidence="HIGH", ticker="NVDA")


def test_sizing_uses_equity_not_cash(tmp_path, monkeypatch):
    """Bei gehaltenen Positionen basiert die Größe auf Equity, nicht auf Rest-Cash."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    # 30k in eine Position → cash 70k, Equity bleibt 100k (Bewertung zu Einstand)
    p.open_position(make_position("AAPL", 200, 150.0))  # 30_000
    assert p.cash == pytest.approx(70_000.0)
    assert p.total_value({}) == pytest.approx(100_000.0)

    strat = make_strategy(p)
    size = strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG)

    # Equity-basiert: 100k * 0.20 * 1.0 (HIGH) = 20_000
    # (Cash-basiert wäre fälschlich 70k * 0.20 = 14_000)
    assert size == pytest.approx(20_000.0)


def test_empty_portfolio_unchanged(tmp_path, monkeypatch):
    """Ohne Positionen ist Equity == Cash → kein Verhaltensunterschied."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)
    size = strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG)
    assert size == pytest.approx(20_000.0)


def test_confluence_bumps_size(tmp_path, monkeypatch):
    """Congress×CEO-Confluence hebt die Positionsgröße um den Multiplikator an."""
    import strategy.swing_strategy as sw
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)

    base = strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG, confluence=False)
    boosted = strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG, confluence=True)

    assert base == pytest.approx(20_000.0)
    assert boosted == pytest.approx(20_000.0 * sw._CONFLUENCE_SIZE_MULT)


def test_confluence_default_is_off(tmp_path, monkeypatch):
    """Ohne explizites Flag bleibt das Sizing unverändert (rückwärtskompatibel)."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)
    assert strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG) == pytest.approx(20_000.0)


def _fake_ti(atr, price):
    """Liefert ein TechnicalIndicators-Stub, dessen calculate() ATR/Preis setzt."""
    return lambda: types.SimpleNamespace(
        calculate=lambda t: types.SimpleNamespace(atr_14=atr, price=price)
    )


def test_atr_multiplier_clamps(monkeypatch):
    """Ruhige Aktie → CAP, wilde → FLOOR, normale → 1.0."""
    import analyzers.technical_indicators as ti
    import strategy.swing_strategy as sw

    # ruhig: ATR 1% → 2.5/1.0 = 2.5 → auf CAP gedeckelt
    monkeypatch.setattr(ti, "TechnicalIndicators", _fake_ti(1.0, 100.0))
    assert sw.SwingStrategy._atr_vol_multiplier("KO", 100.0) == pytest.approx(sw._ATR_SIZE_CAP)

    # wild: ATR 5% → 2.5/5.0 = 0.5 → genau FLOOR
    monkeypatch.setattr(ti, "TechnicalIndicators", _fake_ti(5.0, 100.0))
    assert sw.SwingStrategy._atr_vol_multiplier("NVDA", 100.0) == pytest.approx(sw._ATR_SIZE_FLOOR)

    # normal: ATR 2.5% → 1.0
    monkeypatch.setattr(ti, "TechnicalIndicators", _fake_ti(2.5, 100.0))
    assert sw.SwingStrategy._atr_vol_multiplier("SPY", 100.0) == pytest.approx(1.0)


def test_atr_missing_is_failopen(monkeypatch):
    """Kein ATR-Snapshot ⇒ Faktor 1.0, nie ein Block."""
    import analyzers.technical_indicators as ti
    import strategy.swing_strategy as sw
    monkeypatch.setattr(ti, "TechnicalIndicators",
                        lambda: types.SimpleNamespace(calculate=lambda t: None))
    assert sw.SwingStrategy._atr_vol_multiplier("XYZ", 100.0) == pytest.approx(1.0)


def test_atr_scales_position_size(tmp_path, monkeypatch):
    """Im Sizing-Pfad skaliert der ATR-Faktor die Dollar-Größe (hier ruhige Aktie → CAP)."""
    import analyzers.technical_indicators as ti
    import strategy.swing_strategy as sw
    monkeypatch.setattr(ti, "TechnicalIndicators", _fake_ti(1.0, 100.0))  # überschreibt autouse-Stub

    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    strat = make_strategy(p)
    size = strat._calc_position_size(_ANALYSIS, 100.0, _PARAMS, _CONFIG)
    # 20_000 Basis × CAP, gedeckelt durch cash*0.40 = 40_000 (greift hier nicht)
    assert size == pytest.approx(20_000.0 * sw._ATR_SIZE_CAP)


def test_liquidity_guardrail_still_caps_on_cash(tmp_path, monkeypatch):
    """Die 40%-Cash-Grenze verhindert weiterhin ein Überziehen des Cash."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    # 95k investiert → nur 5k Cash, Equity 100k
    p.open_position(make_position("AAPL", 950, 100.0))  # 95_000
    assert p.cash == pytest.approx(5_000.0)

    strat = make_strategy(p)
    size = strat._calc_position_size(_ANALYSIS, 50.0, _PARAMS, _CONFIG)

    # Equity-Basis wäre 20_000, aber Liquiditätsgrenze cash*0.40 = 2_000 greift
    assert size == pytest.approx(2_000.0)
