"""
Tests: ein BUY-Signal, das nur an geschlossener Börse scheitert (Order
während des vorbörslichen Analyse-Zyklus, NYSE noch zu), wird vorgemerkt
und bei Marktöffnung frisch neu geprüft statt zu verpuffen (27.7.2026).

Auslöser: der User bemerkte, dass am Morgen trotz eines starken BUY-Signals
(AMD) nichts gekauft wurde. Befund: der Kaufversuch lief während des
vorbörslichen Zyklus (07:30 CEST = 01:30 New York), IBKR lehnte die Order
mit "außerhalb der Handelszeit" ab — und dieser Fehlschlag landete, anders
als der strukturell gleiche Kapitalmangel-/Max-Positionen-Fall, NICHT in der
Signal-Queue. Das Signal war beim nächsten Zyklus einfach weg.

Anders als beim Kapitalmangel-Fall (dort wird das alte, eingefrorene
Sentiment per strategy.evaluate() neu bewertet) verlangt der User explizit
eine ECHTE Neu-Prüfung: bei Marktöffnung wird der Ticker über eine frische
Einzel-Analyse (escalate_fn, derselbe Pfad wie Headline-/Momentum-
Eskalation) komplett neu bewertet — nur was JETZT noch gilt, wird gekauft.
"""
import types

import pytest

import portfolio.portfolio as port_mod
import portfolio.signal_queue as sq_mod
from portfolio.portfolio import Portfolio
from portfolio.signal_queue import SignalQueue
from strategy.executor import TradeExecutor, _NullNotifier
from strategy.swing_strategy import StrategyResult
from bot.scheduler_risk import market_closed_signal_job


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", str(tmp_path / "data" / "portfolio.db"))
    return Portfolio(initial_capital=capital)


def make_signal_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(sq_mod, "DB_PATH", str(tmp_path / "signal_queue.db"))
    return SignalQueue()


def _analysis(ticker="AMD", sentiment=0.83):
    return types.SimpleNamespace(
        ticker=ticker, sentiment_score=sentiment, confidence="HIGH",
        direction="BULLISH", recommendation="BUY", suggested_hold_days=14,
        entry_rationale="Starkes Momentum", key_catalysts=["Analyst-Upgrade"],
        risk_factors=[], target_price=180.0, sources_used=6,
        sources_breakdown={"yahoo": 6},
    )


def _result(ticker="AMD", shares=10.0, price=170.0, hold_days=14):
    return StrategyResult(
        action="BUY", ticker=ticker, reason="Starkes Signal",
        shares=shares, price=price, hold_days=hold_days,
    )


class _MarketClosedBroker:
    """Simuliert IBKR: BUY schlägt IMMER mit 'Handelszeit' fehl."""
    def buy(self, ticker, shares, price, stop_loss=None):
        return {"status": "error", "reason": f"NYSE/NASDAQ außerhalb der Handelszeit"}


class _OtherFailureBroker:
    """Simuliert einen ECHTEN Fehlschlag (Broker nicht verbunden) — muss NICHT
    in die Queue wandern, das ist keine reine Zeitfrage."""
    def buy(self, ticker, shares, price, stop_loss=None):
        return {"status": "error", "reason": "IBKR nicht verbunden"}


def _executor_with(broker, portfolio, signal_queue):
    strat = types.SimpleNamespace(signal_queue=signal_queue)
    ex = TradeExecutor(portfolio, broker, journal=None, notifier=_NullNotifier(), strategy=strat)
    return ex


# ── _execute_buy: enqueue nur bei ECHTEM Markt-geschlossen-Fehlschlag ──────

def test_market_closed_failure_enqueues_signal(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    sq = make_signal_queue(tmp_path, monkeypatch)
    ex = _executor_with(_MarketClosedBroker(), p, sq)

    import analyzers.market_schedule as ms
    monkeypatch.setattr(ms, "market_closed_reason", lambda t: "NYSE außerhalb der Handelszeit")

    out = ex.execute(_result(), analysis=_analysis(), sources_breakdown={"yahoo": 6})

    assert "vorgemerkt" in out
    pending = sq.get_pending()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "AMD"
    assert pending[0]["reason"] == "market_closed"
    assert pending[0]["sentiment_score"] == pytest.approx(0.83)
    assert p.get_position("AMD") is None, "kein Kauf gebucht – nur vorgemerkt"


def test_market_closed_enqueue_with_dict_sources_used(tmp_path, monkeypatch):
    """Regression: der ClaudeAnalyzer setzt `analysis.sources_used` als
    Dict[str,int] (Quelle→Anzahl). _enqueue_if_market_closed rief blind
    int() darauf auf → TypeError, das BUY-Signal verpuffte statt vorgemerkt
    zu werden (144× im Log seit 29.7., überlebte den cycle_analysis-Umbau).
    Jetzt wird der Dict wie überall sonst zur Quellenzahl summiert."""
    p = make_portfolio(tmp_path, monkeypatch)
    sq = make_signal_queue(tmp_path, monkeypatch)
    ex = _executor_with(_MarketClosedBroker(), p, sq)

    import analyzers.market_schedule as ms
    monkeypatch.setattr(ms, "market_closed_reason", lambda t: "NYSE außerhalb der Handelszeit")

    analysis = _analysis()
    analysis.sources_used = {"yahoo": 7, "newsapi": 13, "sec": 1}

    out = ex.execute(_result(), analysis=analysis, sources_breakdown=analysis.sources_used)

    assert "vorgemerkt" in out
    pending = sq.get_pending()
    assert len(pending) == 1
    assert pending[0]["sources_used"] == 21


def test_other_failure_does_not_enqueue(tmp_path, monkeypatch):
    """Gegenprobe: scheitert der Kauf aus einem ANDEREN Grund während die
    Börse eigentlich offen ist, darf nichts vorgemerkt werden."""
    p = make_portfolio(tmp_path, monkeypatch)
    sq = make_signal_queue(tmp_path, monkeypatch)
    ex = _executor_with(_OtherFailureBroker(), p, sq)

    import analyzers.market_schedule as ms
    monkeypatch.setattr(ms, "market_closed_reason", lambda t: None)  # Börse offen

    out = ex.execute(_result(), analysis=_analysis(), sources_breakdown={"yahoo": 6})

    assert "vorgemerkt" not in out
    assert sq.get_pending() == []


def test_no_signal_queue_configured_does_not_crash(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    strat = types.SimpleNamespace(signal_queue=None)
    ex = TradeExecutor(p, _MarketClosedBroker(), journal=None, notifier=_NullNotifier(), strategy=strat)

    out = ex.execute(_result(), analysis=_analysis(), sources_breakdown={})
    assert "BUY-Order fehlgeschlagen" in out


# ── market_closed_signal_job: drained bei Marktöffnung, sonst wartet ──────

def test_drain_escalates_when_market_open(tmp_path, monkeypatch):
    sq = make_signal_queue(tmp_path, monkeypatch)
    sq.enqueue(
        ticker="AMD", sentiment_score=0.83, confidence="HIGH", target_price=180.0,
        direction="BULLISH", entry_rationale="x", key_catalysts=[], risk_factors=[],
        sources_used=6, sources_breakdown={}, suggested_hold_days=14,
        reason="market_closed",
    )

    import analyzers.market_schedule as ms
    monkeypatch.setattr(ms, "market_closed_reason", lambda t: None)  # jetzt offen

    calls = []
    market_closed_signal_job(sq, lambda tickers, reason="": calls.append((tickers, reason)))

    assert calls == [(["AMD"], "Marktöffnung (zurückgestelltes BUY-Signal)")]
    history = sq.get_history(5)
    assert history[0]["status"] == "rechecked"


def test_drain_waits_while_market_still_closed(tmp_path, monkeypatch):
    sq = make_signal_queue(tmp_path, monkeypatch)
    sq.enqueue(
        ticker="AMD", sentiment_score=0.83, confidence="HIGH", target_price=180.0,
        direction="BULLISH", entry_rationale="x", key_catalysts=[], risk_factors=[],
        sources_used=6, sources_breakdown={}, suggested_hold_days=14,
        reason="market_closed",
    )

    import analyzers.market_schedule as ms
    monkeypatch.setattr(ms, "market_closed_reason", lambda t: "NYSE außerhalb der Handelszeit")

    calls = []
    market_closed_signal_job(sq, lambda tickers, reason="": calls.append(tickers))

    assert calls == []
    pending = sq.get_pending()
    assert len(pending) == 1, "Eintrag bleibt pending, bis die Börse offen ist"


def test_capital_scarcity_entries_are_untouched_by_market_closed_drain(tmp_path, monkeypatch):
    """Der neue Drain darf nur 'market_closed'-Einträge anfassen."""
    sq = make_signal_queue(tmp_path, monkeypatch)
    sq.enqueue(
        ticker="NVDA", sentiment_score=0.9, confidence="HIGH", target_price=150.0,
        direction="BULLISH", entry_rationale="x", key_catalysts=[], risk_factors=[],
        sources_used=5, sources_breakdown={}, suggested_hold_days=14,
        reason="capital_scarcity",
    )

    calls = []
    market_closed_signal_job(sq, lambda tickers, reason="": calls.append(tickers))

    assert calls == []
    assert len(sq.get_pending()) == 1, "Kapitalmangel-Eintrag bleibt unangetastet pending"


# ── process_signal_queue: überspringt market_closed-Einträge ──────────────

def test_process_signal_queue_skips_market_closed_entries(tmp_path, monkeypatch):
    sq = make_signal_queue(tmp_path, monkeypatch)
    sq.enqueue(
        ticker="AMD", sentiment_score=0.83, confidence="HIGH", target_price=180.0,
        direction="BULLISH", entry_rationale="x", key_catalysts=[], risk_factors=[],
        sources_used=6, sources_breakdown={}, suggested_hold_days=14,
        reason="market_closed",
    )
    strat = types.SimpleNamespace(signal_queue=sq)

    from strategy.executor import process_signal_queue
    broker = types.SimpleNamespace(get_price=lambda t: 170.0)
    msgs = process_signal_queue(strat, executor=types.SimpleNamespace(execute=lambda *a, **k: None), broker=broker)

    assert msgs == []
    assert len(sq.get_pending()) == 1, "Eintrag bleibt für market_closed_signal_job liegen"
