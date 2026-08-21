"""
Tests für broker/order_log.py (Roadmap 1.5f: Order-Lifecycle-Ansicht).
"""
import broker.order_log as ol_mod
from broker.order_log import OrderLog, log_order
from broker.order_result import OrderResult


def make_log(tmp_path):
    return OrderLog(db_path=str(tmp_path / "order_log.db"))


def test_init_creates_table(tmp_path):
    log = make_log(tmp_path)
    cols = {r[1] for r in log._conn.execute("PRAGMA table_info(orders)")}
    assert cols == {"id", "ts", "ticker", "action", "mode", "status",
                     "shares", "fill_price", "order_id", "partial", "reason",
                     "market_price"}


def test_migration_adds_market_price_to_pre_existing_db(tmp_path):
    """Alte order_log.db ohne market_price-Spalte (Stand vor Roadmap 5.3)
    muss beim nächsten Start klaglos migriert werden, Alt-Zeilen bleiben
    erhalten mit market_price=NULL statt eines Fehlers."""
    import sqlite3
    db_path = str(tmp_path / "legacy_order_log.db")
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            ticker TEXT, action TEXT, mode TEXT, status TEXT,
            shares REAL, fill_price REAL, order_id INTEGER,
            partial INTEGER, reason TEXT
        );
    """)
    con.execute(
        "INSERT INTO orders (ts, ticker, action, mode, status, shares, fill_price) "
        "VALUES ('2026-01-01T00:00:00', 'ALT', 'BUY', 'ibkr', 'filled', 1, 10.0)"
    )
    con.commit()
    con.close()

    log = OrderLog(db_path=db_path)
    rows = log.recent(10)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ALT"
    assert rows[0]["market_price"] is None


def test_record_persists_market_price(tmp_path):
    log = make_log(tmp_path)
    result = OrderResult.filled(
        "AAPL", 3, 101.05, mode="ibkr", extra={"market_price": 100.50}
    )
    log.record(result, "BUY")
    rows = log.recent(10)
    assert rows[0]["market_price"] == 100.50


def test_record_without_market_price_stores_null(tmp_path):
    log = make_log(tmp_path)
    log.record(OrderResult.filled("AAPL", 3, 101.05, mode="paper"), "BUY")
    rows = log.recent(10)
    assert rows[0]["market_price"] is None


def test_record_and_recent_filled_order(tmp_path):
    log = make_log(tmp_path)
    result = OrderResult.filled("AAPL", 3, 101.05, mode="paper")
    row_id = log.record(result, "BUY")
    assert row_id == 1
    rows = log.recent(10)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["action"] == "BUY"
    assert rows[0]["status"] == "filled"
    assert rows[0]["fill_price"] == 101.05


def test_record_error_order_keeps_reason(tmp_path):
    log = make_log(tmp_path)
    result = OrderResult.error(ticker="NVDA", reason="IBKR nicht verbunden", mode="ibkr")
    log.record(result, "SELL")
    rows = log.recent(10)
    assert rows[0]["status"] == "error"
    assert rows[0]["reason"] == "IBKR nicht verbunden"
    assert rows[0]["mode"] == "ibkr"


def test_recent_newest_first(tmp_path):
    log = make_log(tmp_path)
    log.record(OrderResult.filled("A", 1, 10.0, mode="paper"), "BUY")
    log.record(OrderResult.filled("B", 1, 20.0, mode="paper"), "SELL")
    rows = log.recent(10)
    assert [r["ticker"] for r in rows] == ["B", "A"]


def test_record_is_fail_open(tmp_path):
    log = make_log(tmp_path)
    log._conn.close()
    assert log.record(OrderResult.filled("X", 1, 1.0), "BUY") is None
    assert log.recent(10) == []


def test_recent_is_fail_open_on_closed_connection(tmp_path):
    log = make_log(tmp_path)
    log._conn.close()
    assert log.recent(10) == []


# ── log_order-Decorator ──────────────────────────────────────────────────────

class _DummyBroker:
    def __init__(self):
        self._lock = None

    @log_order("BUY")
    def buy(self, ticker, shares, price, stop_loss=None):
        return OrderResult.filled(ticker, shares, price, mode="dummy")

    @log_order("SELL")
    def sell_fails(self, ticker, shares, price):
        return OrderResult.error(ticker=ticker, reason="kaputt", mode="dummy")


def test_log_order_decorator_records_and_returns_result_unchanged(tmp_path, monkeypatch):
    log = make_log(tmp_path)
    monkeypatch.setattr(ol_mod, "_instance", log)
    b = _DummyBroker()
    result = b.buy("MSFT", 2, 50.0)
    assert result["status"] == "filled"   # Rückgabe unverändert
    rows = log.recent(10)
    assert rows[0]["ticker"] == "MSFT" and rows[0]["action"] == "BUY"


def test_log_order_decorator_records_error_paths_too(tmp_path, monkeypatch):
    log = make_log(tmp_path)
    monkeypatch.setattr(ol_mod, "_instance", log)
    b = _DummyBroker()
    result = b.sell_fails("MSFT", 2, 50.0)
    assert result["status"] == "error"
    rows = log.recent(10)
    assert rows[0]["status"] == "error" and rows[0]["reason"] == "kaputt"


def test_log_order_decorator_preserves_signature_for_inspect():
    """executor._broker_accepts_stop() prüft inspect.signature(broker.buy) auf
    stop_loss – der Decorator darf diese Introspektion nicht verstecken."""
    import inspect
    b = _DummyBroker()
    assert "stop_loss" in inspect.signature(b.buy).parameters


def test_log_order_decorator_is_fail_open_if_logging_broken(monkeypatch):
    """Ein kaputtes Log-Backend darf die Order selbst nie beeinflussen."""
    class _BrokenLog:
        def record(self, *a, **k):
            raise RuntimeError("Log kaputt")
    monkeypatch.setattr(ol_mod, "_instance", _BrokenLog())
    b = _DummyBroker()
    result = b.buy("MSFT", 2, 50.0)
    assert result["status"] == "filled"


# ── Verkabelung: PaperBroker ist tatsächlich instrumentiert ──────────────────

def test_paper_broker_buy_and_sell_are_recorded(tmp_path, monkeypatch):
    from broker.paper_broker import PaperBroker
    log = make_log(tmp_path)
    monkeypatch.setattr(ol_mod, "_instance", log)
    monkeypatch.setattr(
        "broker.paper_broker._cached_price", lambda t: 100.0, raising=False
    )
    b = PaperBroker()
    b.buy("AAPL", 2, 100.0)
    b.sell("AAPL", 2, 100.0)
    rows = log.recent(10)
    assert [r["action"] for r in rows] == ["SELL", "BUY"]
    assert all(r["mode"] == "paper" for r in rows)
    assert all(r["status"] == "filled" for r in rows)
