"""
Tests für strategy/executor.py – führt StrategyResult-Entscheidungen aus.

Deckt die kritischen Ausführungs-Feinheiten ab, die beim unvollständigen
SwingStrategy-Umbau (7.6.2026) verloren gingen:
- Neukauf öffnet Position + Broker-Order
- Scale-in (Position existiert) überschreibt NICHT (open_position ist REPLACE)
- Voll-Exit schließt Position
- Partial-TP löst nur Broker-Order aus (Buch schon von Engine mutiert)
- Order-Fehlschlag öffnet keine Position
"""
import types

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import StrategyResult
from strategy.executor import TradeExecutor, _NullNotifier


class FakeBroker:
    """Broker-Doppelgänger: füllt immer (oder nie), zählt Aufrufe."""
    def __init__(self, fill=True):
        self.fill = fill
        self.buys = []
        self.sells = []

    def _result(self, ticker, shares, price):
        if not self.fill:
            return {"status": "rejected", "error": "Testfehler"}
        return {"status": "filled", "ticker": ticker, "shares": shares,
                "fill_price": price, "market_price": price}

    def buy(self, ticker, shares, price):
        self.buys.append((ticker, shares, price))
        return self._result(ticker, shares, price)

    def sell(self, ticker, shares, price):
        self.sells.append((ticker, shares, price))
        return self._result(ticker, shares, price)


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_exec(portfolio, fill=True):
    broker = FakeBroker(fill=fill)
    ex = TradeExecutor(portfolio, broker, journal=None, notifier=_NullNotifier())
    return ex, broker


_ANALYSIS = types.SimpleNamespace(
    sentiment_score=0.9, confidence="HIGH", direction="BULLISH",
    entry_rationale="Test-Kauf", key_catalysts=["k1"], risk_factors=["r1"],
    target_price=180.0,
)


def test_buy_opens_position(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    ex, broker = make_exec(p)
    res = StrategyResult("BUY", "AAPL", "Test-Kauf", shares=10, price=150.0,
                         stop_loss=139.5, take_profit=180.0, hold_days=14)
    out = ex.execute(res, analysis=_ANALYSIS)
    assert "GEKAUFT" in out
    assert broker.buys == [("AAPL", 10, 150.0)]
    pos = p.get_position("AAPL")
    assert pos is not None and pos.shares == 10
    assert pos.target_hold_days == 14
    assert p.cash == pytest.approx(100_000.0 - 10 * 150.0)


class RoundingBroker(FakeBroker):
    """IBKR-Doppelgänger: rundet Aktien auf ganze Stück (Teilaktien nicht
    API-fähig) und meldet die TATSÄCHLICH georderte Menge im Fill zurück."""
    def buy(self, ticker, shares, price):
        whole = float(int(shares))
        self.buys.append((ticker, whole, price))
        return {"status": "filled", "ticker": ticker, "shares": whole,
                "fill_price": price, "market_price": price}


def test_buy_books_filled_shares_not_requested(tmp_path, monkeypatch):
    """Bruchteils-BUY: Broker füllt 57 statt 57.91 → Buch MUSS 57 führen,
    sonst entsteht 0.91 Dust/Phantom, das nie verkäuflich ist."""
    p = make_portfolio(tmp_path, monkeypatch)
    broker = RoundingBroker()
    ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())
    res = StrategyResult("BUY", "MSFT", "Conditional Entry", shares=57.91,
                         price=417.35, stop_loss=392.0, take_profit=475.0, hold_days=8)
    out = ex.execute(res, analysis=_ANALYSIS)
    assert "GEKAUFT" in out
    assert broker.buys == [("MSFT", 57.0, 417.35)]
    pos = p.get_position("MSFT")
    assert pos is not None and pos.shares == 57.0      # ganze Stück, kein 57.91
    assert p.cash == pytest.approx(100_000.0 - 57.0 * 417.35)


def test_scale_in_does_not_overwrite(tmp_path, monkeypatch):
    """BUY bei offener Position wird übersprungen – kein REPLACE."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    ex, broker = make_exec(p)
    res = StrategyResult("BUY", "AAPL", "Scale-in", shares=5, price=160.0)
    out = ex.execute(res, analysis=_ANALYSIS)
    assert "GEKAUFT" not in out
    assert broker.buys == []                       # keine Order
    assert p.get_position("AAPL").shares == 10     # unverändert


def test_full_sell_closes_position(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    ex, broker = make_exec(p)
    res = StrategyResult("SELL", "AAPL", "Take-Profit erreicht @ $170.00",
                         shares=10, price=170.0)
    out = ex.execute(res, days_held=5)
    assert "VERKAUFT" in out
    assert broker.sells == [("AAPL", 10, 170.0)]
    assert p.get_position("AAPL") is None          # geschlossen


class FakeStrategy:
    """Spy für strategy.record_loss() – prüft die Circuit-Breaker-Verdrahtung."""
    def __init__(self):
        self.losses = []

    def record_loss(self, pnl):
        self.losses.append(pnl)


def test_full_sell_reports_pnl_to_circuit_breaker(tmp_path, monkeypatch):
    """Regression: TradeExecutor kannte die Strategy-Instanz nicht → record_loss()
    (Grundlage von SwingStrategy._circuit_breaker_active) wurde im Live-Pfad NIE
    aufgerufen, der Tagesverlust-Circuit-Breaker griff daher nie."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    broker = FakeBroker(fill=True)
    fake_strategy = FakeStrategy()
    ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier(), strategy=fake_strategy)
    res = StrategyResult("SELL", "AAPL", "Stop-Loss ausgelöst @ $140.00",
                         shares=10, price=140.0)

    ex.execute(res, days_held=2)

    assert fake_strategy.losses == [pytest.approx(10 * (140.0 - 150.0))]


def test_partial_tp_sells_without_closing(tmp_path, monkeypatch):
    """Partial-TP: Engine hat das Buch schon reduziert → nur Broker-Order, kein close."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    ex, broker = make_exec(p)
    res = StrategyResult("SELL", "AAPL", "Partial-TP Stufe 1: 35% @ +10.0%",
                         shares=3.5, price=165.0)
    out = ex.execute(res)
    assert "VERKAUFT" not in out                    # darf Vorhersage nicht schließen
    assert "Partial-TP" in out
    assert broker.sells == [("AAPL", 3.5, 165.0)]
    assert p.get_position("AAPL") is not None        # Position bleibt offen


class RecordingNotifier(_NullNotifier):
    def __init__(self):
        self.sent = []

    def send(self, msg, level=None):
        self.sent.append((msg, level))


def test_partial_tp_failed_order_sends_only_critical_alert(tmp_path, monkeypatch):
    """Regression: bei fehlgeschlagener Partial-TP-Order wurde nach dem
    kritischen Fehlalarm zusätzlich eine widersprüchliche Erfolgsmeldung
    ("📊 Partial-TP: ... @ $result.price") verschickt – mit dem angefragten
    statt einem echten Fill-Preis. Jetzt: nur der Fehlalarm, klare Fehler-Rückgabe."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    broker = FakeBroker(fill=False)
    notifier = RecordingNotifier()
    ex = TradeExecutor(p, broker, journal=None, notifier=notifier)
    res = StrategyResult("SELL", "AAPL", "Partial-TP Stufe 1: 35% @ +10.0%",
                         shares=3.5, price=165.0)

    out = ex.execute(res)

    assert "fehlgeschlagen" in out
    assert "Partial-TP" not in out.split("fehlgeschlagen")[-1]  # keine Erfolgsmeldung angehängt
    levels = [lvl for _, lvl in notifier.sent]
    assert levels == ["critical"]        # KEINE zusätzliche "trade"-Erfolgsmeldung
    assert p.get_position("AAPL").shares == 10  # Buch (von der Engine reduziert) unverändert durch Executor


def test_failed_buy_opens_no_position(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    ex, broker = make_exec(p, fill=False)
    res = StrategyResult("BUY", "AAPL", "Test", shares=10, price=150.0,
                         stop_loss=139.5, take_profit=180.0, hold_days=14)
    out = ex.execute(res, analysis=_ANALYSIS)
    assert "fehlgeschlagen" in out
    assert p.get_position("AAPL") is None
    assert p.cash == pytest.approx(100_000.0)        # kein Cash abgebucht


def test_ibkr_down_does_not_paper_fall_back(tmp_path, monkeypatch):
    """IBKR-Order-Fehler (status=error, 'reason') → keine Position, kein Cash-Abzug.
    Kein stiller Paper-Trade als Ersatz."""
    p = make_portfolio(tmp_path, monkeypatch)

    class DownIBKR:
        def buy(self, t, s, pr):
            return {"status": "error", "reason": "IBKR nicht verbunden"}
        def sell(self, t, s, pr):
            return {"status": "error", "reason": "IBKR nicht verbunden"}

    ex = TradeExecutor(p, DownIBKR(), journal=None, notifier=_NullNotifier())
    out = ex.execute(StrategyResult("BUY", "AAPL", "x", shares=10, price=150.0,
                                    stop_loss=139.5, take_profit=180.0, hold_days=14),
                     analysis=_ANALYSIS)
    assert "fehlgeschlagen" in out
    assert "IBKR nicht verbunden" in out          # Grund wird durchgereicht
    assert p.get_position("AAPL") is None
    assert p.cash == pytest.approx(100_000.0)


def test_skip_and_hold_strings(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    ex, _ = make_exec(p)
    assert "übersprungen" in ex.execute(StrategyResult("SKIP", "AAPL", "Sentiment zu niedrig"))
    assert ex.execute(StrategyResult("HOLD", "AAPL", "Position läuft")) == "[AAPL] Position läuft"
    assert ex.execute(None) is None


class FakeBrokerWithPositions(FakeBroker):
    """Broker mit positions()-API (wie IBKR) für den Anti-Short-Guard."""
    def __init__(self, held: dict, fill=True):
        super().__init__(fill=fill)
        self._held = dict(held)

    def positions(self):
        return dict(self._held)


def _exec_with(p, broker, monkeypatch, tmp_path):
    import strategy.executor as ex_mod
    monkeypatch.setattr(ex_mod, "_THROTTLE_FILE", tmp_path / "throttle.json")
    return TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())


def test_short_guard_blocks_sell_when_broker_flat(tmp_path, monkeypatch):
    """Buch hält MSFT, IBKR ist flach → SELL muss blockiert werden (kein Short)."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="MSFT", shares=57.91, entry_price=417.35,
        entry_date="2026-06-06T00:00:00", stop_loss=392.0, take_profit=475.0,
        target_hold_days=8,
    ))
    broker = FakeBrokerWithPositions(held={})            # IBKR hält nichts
    ex = _exec_with(p, broker, monkeypatch, tmp_path)
    out = ex.execute(StrategyResult("SELL", "MSFT", "Stop-Loss", shares=57.91, price=410.0),
                     days_held=9)
    assert "übersprungen" in out and "Desync" in out
    assert broker.sells == []                            # KEINE Order an Broker
    assert p.get_position("MSFT") is not None            # Position unangetastet


def test_sell_proceeds_when_broker_covers(tmp_path, monkeypatch):
    """IBKR deckt die Buch-Position → SELL läuft normal durch."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="AAPL", shares=10, entry_price=150.0,
        entry_date="2026-06-10T00:00:00", stop_loss=135.0, take_profit=180.0,
        target_hold_days=14,
    ))
    broker = FakeBrokerWithPositions(held={"AAPL": 10})
    ex = _exec_with(p, broker, monkeypatch, tmp_path)
    out = ex.execute(StrategyResult("SELL", "AAPL", "Take-Profit @ $170.00",
                                    shares=10, price=170.0), days_held=5)
    assert "VERKAUFT" in out
    assert broker.sells == [("AAPL", 10, 170.0)]
    assert p.get_position("AAPL") is None
