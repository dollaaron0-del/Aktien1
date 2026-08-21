"""
Tests: ein BUY-Signal, das nur an Kapitalmangel scheitert (Positionsgröße = 0
nach _calc_position_size), wird jetzt vorgemerkt statt zu verpuffen (25.7.2026).

Auslöser: der User bemerkte, dass ein starkes Signal (Score 0.9) bei erreichtem
Cash-Reserve-Boden ersatzlos verworfen wurde – anders als beim strukturell
gleichen "Max Positionen erreicht"-Fall, der bereits in die Signal-Queue
eingereiht wird (bot/scheduler_risk.py drained sie stündlich UND sofort nach
jedem SL/TP-Exit, also genau dann, wenn Kapital frei wird). SwingStrategy._
enqueue_signal() ist jetzt der gemeinsame Helper für beide Skip-Gründe.
"""
import threading
import types
from datetime import datetime, timezone

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from portfolio.signal_queue import SignalQueue
import portfolio.signal_queue as sq_mod
from strategy.swing_strategy import SwingStrategy


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", str(tmp_path / "data" / "portfolio.db"))
    return Portfolio(initial_capital=capital)


def make_signal_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(sq_mod, "DB_PATH", str(tmp_path / "signal_queue.db"))
    return SignalQueue()


def make_position(ticker, shares, entry_price):
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def make_strategy(portfolio, signal_queue=None, max_pos=12):
    strat = object.__new__(SwingStrategy)
    strat.portfolio = portfolio
    strat.kelly_sizer = None
    strat.goal_risk_assessor = None
    strat.correlation_checker = None
    strat.signal_queue = signal_queue
    strat.earnings_filter = None
    strat._conditional_watcher = None
    strat.focus_ctrl = types.SimpleNamespace(get_max_positions=lambda _v: max_pos)
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
        capital_scarcity_threshold_enabled=False,   # isoliert: nur Sizing-Skip testen
        max_position_pct=0.20, max_single_position_pct=1.0,
        conviction_max_bonus=0.6, cash_reserve_pct=0.10,
        cash_reserve_hard_pct=0.05, reflow_sizing_enabled=False,
        reflow_lookahead_days=5,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


_PARAMS = types.SimpleNamespace(
    buy_threshold_adj=0.0, position_size_mult=1.0,
    sl_pct=0.06, tp_pct=0.22, hold_days_mult=1.0,
)


def _analysis(sentiment=0.9, ticker="NVDA"):
    return types.SimpleNamespace(
        sentiment_score=sentiment, confidence="HIGH", direction="BULLISH",
        recommendation="BUY", ticker=ticker, sources_used=5,
        suggested_hold_days=14, entry_rationale="Starkes Signal", key_catalysts=["X"],
        risk_factors=[], target_price=150.0, sources_breakdown={"yahoo": 5},
    )


def _evaluate(strat, sentiment, config, ticker="NVDA"):
    return strat._evaluate_new(ticker, _analysis(sentiment, ticker), 100.0, _PARAMS,
                               "BULL", False, None, config)


# ── Kernverhalten: Kapitalmangel-Skip wird vorgemerkt ───────────────────────

def test_zero_position_size_enqueues_signal(tmp_path, monkeypatch):
    """Cash exakt am Reserve-Boden → Positionsgröße 0, aber starkes Signal
    (0.9) landet jetzt in der Queue statt zu verpuffen."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 900, 100.0))   # Cash = 10_000 = exakt der Boden
    sq = make_signal_queue(tmp_path, monkeypatch)
    strat = make_strategy(p, signal_queue=sq)
    cfg = _config()

    res = _evaluate(strat, 0.9, cfg)

    assert res.action == "SKIP"
    assert "Positionsgröße = 0" in res.reason
    assert "Queue" in res.reason

    pending = sq.get_pending()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"
    assert pending[0]["sentiment_score"] == pytest.approx(0.9)
    assert pending[0]["confidence"] == "HIGH"
    assert pending[0]["target_price"] == pytest.approx(150.0)


def test_sufficient_cash_does_not_enqueue(tmp_path, monkeypatch):
    """Gegenprobe: genug Cash → normaler Kauf, keine Warteschlange nötig."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    sq = make_signal_queue(tmp_path, monkeypatch)
    strat = make_strategy(p, signal_queue=sq)
    cfg = _config()

    res = _evaluate(strat, 0.9, cfg)

    assert res.action == "BUY"
    assert sq.get_pending() == []


def test_no_signal_queue_configured_does_not_crash(tmp_path, monkeypatch):
    """signal_queue=None (z.B. in Tests/leichten Aufrufkontexten) darf nicht
    crashen – einfach ohne Vormerkung skippen."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 900, 100.0))
    strat = make_strategy(p, signal_queue=None)
    cfg = _config()

    res = _evaluate(strat, 0.9, cfg)
    assert res.action == "SKIP"
    assert "Positionsgröße = 0" in res.reason


def test_max_positions_enqueue_still_works_after_refactor(tmp_path, monkeypatch):
    """Regression: der bestehende Max-Positionen-Fall nutzt jetzt denselben
    _enqueue_signal-Helper wie der neue Kapitalmangel-Fall – muss weiter
    identisch funktionieren."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    sq = make_signal_queue(tmp_path, monkeypatch)
    strat = make_strategy(p, signal_queue=sq, max_pos=0)   # sofort "voll"
    cfg = _config()

    res = _evaluate(strat, 0.9, cfg)

    assert res.action == "SKIP"
    assert "Max Positionen" in res.reason
    pending = sq.get_pending()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"


def test_requeue_supersedes_previous_pending_entry(tmp_path, monkeypatch):
    """Zwei aufeinanderfolgende Kapitalmangel-Skips für denselben Ticker
    (z.B. beim stündlichen Queue-Drain, solange noch kein Kapital frei ist)
    erzeugen keinen Duplikat-Stau."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100_000.0)
    p.open_position(make_position("AAPL", 900, 100.0))
    sq = make_signal_queue(tmp_path, monkeypatch)
    strat = make_strategy(p, signal_queue=sq)
    cfg = _config()

    _evaluate(strat, 0.9, cfg)
    _evaluate(strat, 0.92, cfg)

    pending = sq.get_pending()
    assert len(pending) == 1
    assert pending[0]["sentiment_score"] == pytest.approx(0.92)
