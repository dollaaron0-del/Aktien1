"""
Regressionstests für das Trade-Pfad-Audit vom 11.7.2026.

Befund damals: EarningsStrategy und HedgeStrategy (beide LIVE im Runner
verdrahtet) buchten Trades ins Portfolio, ohne die Broker-Order zu prüfen —
der Earnings-Zwangs-Exit und der Hedge-Exit platzierten sogar GAR KEINE
Broker-Order. Diese Tests nageln das gefixte Verhalten fest:

- Jeder Exit löst eine echte Broker-SELL-Order aus.
- Gebucht wird NUR bei bestätigtem Fill; bei Fehlschlag bleibt die Position
  unverändert offen.
- Entries buchen den echten Fill-Preis, nicht den angefragten Kurs.
- Rebalancer-REDUCE führt das Buch nach (vorher: Broker-Verkauf ohne Buchung).
"""
import types
from datetime import datetime, timedelta, timezone

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.executor import _NullNotifier


class FakeBroker:
    """Broker-Doppelgänger: füllt immer (oder nie), zählt Aufrufe."""

    def __init__(self, fill=True, price=100.0):
        self.fill = fill
        self.price = price
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

    def get_price(self, ticker):
        return self.price

    def get_prices(self, tickers):
        return {t: self.price for t in tickers}


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def _position(ticker="AAPL", shares=10.0, entry=100.0, rationale="Test"):
    return Position(
        ticker=ticker, shares=shares, entry_price=entry,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry * 0.95, take_profit=entry * 1.10,
        target_hold_days=10, rationale=rationale,
    )


# ── EarningsStrategy: Zwangs-Exit vor Earnings ──────────────────────────────

def _make_earnings(portfolio, broker, strategy=None):
    from strategy.earnings_strategy import EarningsStrategy
    es = EarningsStrategy(portfolio, broker, strategy=strategy)
    es._executor._notifier = _NullNotifier()
    # Earnings morgen → Exit-Bedingung erfüllt
    es._ef = types.SimpleNamespace(
        next_earnings=lambda t: datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    )
    return es


def test_earnings_exit_reports_pnl_to_circuit_breaker(tmp_path, monkeypatch):
    """Regression (gefixt 11.7.2026): EarningsStrategy baute ihren TradeExecutor
    ohne Strategy-Referenz – ein Verlust-Exit vor Earnings erreichte nie den
    Tagesverlust-Circuit-Breaker der SwingStrategy, obwohl dieselbe Kasse betroffen ist."""
    class _FakeStrategy:
        def __init__(self):
            self.losses = []

        def record_loss(self, pnl):
            self.losses.append(pnl)

    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("NVDA", entry=100.0, rationale="EARNINGS_PRE: 7d, Beat-Score 0.8"))
    fake_strategy = _FakeStrategy()
    broker = FakeBroker(fill=True, price=90.0)  # Verlust ggü. Entry 100.0
    es = _make_earnings(p, broker, strategy=fake_strategy)

    es.check_pre_earnings_exits()

    assert fake_strategy.losses == [pytest.approx(10.0 * (90.0 - 100.0))]


def test_earnings_exit_places_broker_order(tmp_path, monkeypatch):
    """Regression: Der Earnings-Exit schloss früher NUR das Buch (kein sell)."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("NVDA", rationale="EARNINGS_PRE: 7d, Beat-Score 0.8"))
    broker = FakeBroker(fill=True)
    es = _make_earnings(p, broker)

    actions = es.check_pre_earnings_exits()

    assert len(broker.sells) == 1 and broker.sells[0][0] == "NVDA"
    assert p.get_position("NVDA") is None
    assert any("VERKAUFT" in a for a in actions)


def test_earnings_exit_keeps_position_on_failed_order(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("NVDA", rationale="EARNINGS_PRE: 7d, Beat-Score 0.8"))
    broker = FakeBroker(fill=False)
    es = _make_earnings(p, broker)

    actions = es.check_pre_earnings_exits()

    assert len(broker.sells) == 1          # Order wurde versucht …
    assert p.get_position("NVDA") is not None  # … aber Buch unverändert
    assert any("fehlgeschlagen" in a for a in actions)


def test_earnings_exit_ignores_normal_positions(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("AAPL", rationale="Swing-Kauf"))
    broker = FakeBroker(fill=True)
    es = _make_earnings(p, broker)

    es.check_pre_earnings_exits()

    assert broker.sells == []
    assert p.get_position("AAPL") is not None


# ── HedgeStrategy: Entry + Exit ──────────────────────────────────────────────

def _make_hedge(portfolio, broker):
    from strategy.hedge_strategy import HedgeStrategy
    h = HedgeStrategy(
        portfolio, broker,
        tracker=types.SimpleNamespace(record_outcome=lambda **k: None),
        journal=types.SimpleNamespace(log_entry=lambda **k: None,
                                      log_exit=lambda **k: None),
        detector=None,
    )
    h._notifier = _NullNotifier()
    return h


_REGIME = {"regime": "BEAR", "recession_score": 0.7}
_HEDGE = {"ticker": "SH", "allocation_pct": 0.10,
          "reason": "Test-Regime", "description": "Inverse S&P"}


def test_hedge_open_books_only_on_fill(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    h = _make_hedge(p, FakeBroker(fill=True, price=40.0))

    actions = h._open_hedges([dict(_HEDGE)], _REGIME)

    pos = p.get_position("SH")
    assert pos is not None and pos.entry_price == 40.0
    assert any("HEDGE ERÖFFNET" in a for a in actions)


def test_hedge_open_skips_booking_on_failed_order(tmp_path, monkeypatch):
    """Regression: Hedge-Kauf buchte früher auch ohne Fill."""
    p = make_portfolio(tmp_path, monkeypatch)
    broker = FakeBroker(fill=False, price=40.0)
    h = _make_hedge(p, broker)

    actions = h._open_hedges([dict(_HEDGE)], _REGIME)

    assert len(broker.buys) == 1
    assert p.get_position("SH") is None
    assert any("fehlgeschlagen" in a for a in actions)


def test_hedge_close_places_broker_order(tmp_path, monkeypatch):
    """Regression: _close_hedge rief früher NIE broker.sell."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("SH", shares=20.0, entry=40.0, rationale="[HEDGE] Test"))
    broker = FakeBroker(fill=True, price=42.0)
    h = _make_hedge(p, broker)
    h._hedge_tickers.add("SH")

    pnl = h._close_hedge("SH", p.get_position("SH"), 42.0, "Regime besser", 3)

    assert len(broker.sells) == 1 and broker.sells[0][0] == "SH"
    assert pnl == pytest.approx(20.0 * 2.0)
    assert p.get_position("SH") is None


def test_hedge_close_keeps_position_on_failed_order(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("SH", shares=20.0, entry=40.0, rationale="[HEDGE] Test"))
    broker = FakeBroker(fill=False, price=42.0)
    h = _make_hedge(p, broker)
    h._hedge_tickers.add("SH")

    pnl = h._close_hedge("SH", p.get_position("SH"), 42.0, "Regime besser", 3)

    assert pnl is None
    assert p.get_position("SH") is not None


# ── Rebalancer: REDUCE muss das Buch nachziehen ──────────────────────────────

def _reduce_plan(ticker="AAPL"):
    return [{"ticker": ticker, "action": "REDUCE", "sektor": "Tech",
             "diff_pct": 6.0, "current_pct": 12.0}]


def test_rebalancer_reduce_updates_book(tmp_path, monkeypatch):
    """Regression: REDUCE verkaufte beim Broker, ließ das Buch aber unverändert."""
    from portfolio.rebalancer import PortfolioRebalancer
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("AAPL", shares=10.0, entry=100.0))
    cash_before = p.cash
    broker = FakeBroker(fill=True, price=110.0)

    msgs = PortfolioRebalancer().apply_rebalance(p, broker, _reduce_plan())

    # reduce_ratio = min(6/12, 0.5) = 0.5 → 5 Stück verkauft
    assert broker.sells == [("AAPL", 5.0, 110.0)]
    assert p.get_position("AAPL").shares == pytest.approx(5.0)
    assert p.cash == pytest.approx(cash_before + 5.0 * 110.0)
    assert any("REDUCE AAPL" in m for m in msgs)


def test_rebalancer_reduce_keeps_book_on_failed_order(tmp_path, monkeypatch):
    from portfolio.rebalancer import PortfolioRebalancer
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("AAPL", shares=10.0, entry=100.0))
    cash_before = p.cash
    broker = FakeBroker(fill=False, price=110.0)

    msgs = PortfolioRebalancer().apply_rebalance(p, broker, _reduce_plan())

    assert p.get_position("AAPL").shares == pytest.approx(10.0)
    assert p.cash == pytest.approx(cash_before)
    assert any("FEHLER REDUCE" in m for m in msgs)


def test_rebalancer_plan_reads_cash_without_crash(tmp_path, monkeypatch):
    """Regression: _calc_portfolio_value rief portfolio.get_cash() auf – die
    Methode gibt es nicht (nur die `cash`-Property) – jeder Aufruf von
    check_rebalance_needed()/get_rebalance_plan() crashte mit AttributeError."""
    from portfolio.rebalancer import PortfolioRebalancer
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(_position("AAPL", shares=100.0, entry=100.0))  # Technology
    p.open_position(_position("JPM", shares=50.0, entry=100.0))    # Finance
    p.open_position(_position("XOM", shares=50.0, entry=100.0))    # Energy
    prices = {"AAPL": 100.0, "JPM": 100.0, "XOM": 100.0}
    reb = PortfolioRebalancer()

    needed = reb.check_rebalance_needed(p, prices)
    plan = reb.get_rebalance_plan(p, prices)

    assert isinstance(needed, bool)
    assert isinstance(plan, list)


# ── Portfolio: force=True für bereits am Broker bestätigte Fills ───────────

def test_open_position_force_bypasses_cash_gate(tmp_path, monkeypatch):
    """Regression: der IBKR-Fill-Check-Job bucht bestätigte Limit-Order-Fills
    direkt via portfolio.open_position(). Reicht das Buch-Cash nicht (z.B. weil
    zwischenzeitlich ein anderer Trade das Cash verbraucht hat), blockte der
    Cash-Gate die Buchung für immer – obwohl der Broker das Geld bereits
    ausgegeben hat. force=True muss trotzdem buchen."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100.0)  # zu wenig für die Position unten
    pos = _position("NVDA", shares=10.0, entry=150.0)  # entry_value = 1500 > 100 Cash

    with pytest.raises(ValueError):
        p.open_position(pos)  # ohne force: Cash-Gate blockt weiterhin

    p.open_position(pos, force=True)

    assert p.get_position("NVDA") is not None
    assert p.get_position("NVDA").shares == 10.0
