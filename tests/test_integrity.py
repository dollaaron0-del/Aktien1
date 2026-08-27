"""
Tests für portfolio/integrity.py — Buchhaltungs-Konsistenzprüfung.
"""
import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from portfolio.integrity import check_integrity, reconcile, reconcile_against_broker


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_position(ticker="AAPL", shares=10, entry_price=150.0):
    from datetime import datetime, timezone
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def test_clean_portfolio_is_ok(tmp_path, monkeypatch):
    """Frisches Portfolio mit einer sauberen Position ist konsistent."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("AAPL", 10, 150.0))
    rep = check_integrity(p._conn, 100_000.0)
    assert rep.ok, rep.summary()
    assert rep.orphan_trades == {}
    assert rep.ghost_positions == []


def test_detects_orphan_trade(tmp_path, monkeypatch):
    """BUY im Log ohne Position (wie nach DELETE FROM positions) → Orphan."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("AAPL", 10, 150.0))
    # Simuliere fehlerhaften Reset: Position weg, BUY-Trade bleibt
    p._conn.execute("DELETE FROM positions")
    p._conn.commit()
    rep = check_integrity(p._conn, 100_000.0)
    assert not rep.ok
    assert "AAPL" in rep.orphan_trades
    assert rep.orphan_trades["AAPL"] == pytest.approx(10.0)


def test_detects_ghost_position(tmp_path, monkeypatch):
    """Position ohne Netto-Long im Trade-Log → Geister-Position."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("AAPL", 10, 150.0))
    # Trade-Log leeren, Position bleibt
    p._conn.execute("DELETE FROM trades")
    p._conn.commit()
    rep = check_integrity(p._conn, 100_000.0)
    assert not rep.ok
    assert "AAPL" in rep.ghost_positions


def test_reconcile_clears_orphans(tmp_path, monkeypatch):
    """reconcile() bucht Gegen-SELLs, danach ist das Log flach und konsistent."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("AAPL", 10, 150.0))
    p.open_position(make_position("MSFT", 5, 400.0))
    cash_before = p.cash
    # Fehlerhafter Reset: nur AAPL-Position bleibt verwaist
    p._conn.execute("DELETE FROM positions WHERE ticker='AAPL'")
    p._conn.commit()

    rep = check_integrity(p._conn, 100_000.0)
    assert "AAPL" in rep.orphan_trades

    fixed = reconcile(p._conn, reason="Test-Reset")
    assert "AAPL" in fixed
    # Cash unverändert (Reconcile bucht nur das Log, nicht den Cash)
    assert p.cash == pytest.approx(cash_before)

    rep2 = check_integrity(p._conn, 100_000.0)
    assert rep2.orphan_trades == {}


def test_reset_marker_suppresses_cash_drift(tmp_path, monkeypatch):
    """Mit reset_at-Marker wird die durch einen Reset erzeugte Cash-Drift nicht als Fehler gewertet."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("AAPL", 10, 150.0))
    p.close_position("AAPL", 160.0, reason="test")
    # Simuliere Reset: Cash hart setzen + Marker
    p._conn.execute("INSERT OR REPLACE INTO portfolio_meta (key,value) VALUES ('cash','100000')")
    p._conn.execute("INSERT OR REPLACE INTO portfolio_meta (key,value) VALUES ('reset_at',?)",
                    ("2999-01-01T00:00:00",))
    p._conn.execute("INSERT OR REPLACE INTO portfolio_meta (key,value) VALUES ('epoch_cash','100000')")
    p._conn.commit()
    rep = check_integrity(p._conn, 100_000.0)
    # Keine Trades nach dem (zukünftigen) Reset-Marker → keine Orphans/Drift
    assert rep.ok, rep.summary()


# ── Broker-Abgleich (Buch ↔ IBKR) ────────────────────────────────────────────

def test_broker_reconcile_books_out_phantom(tmp_path, monkeypatch):
    """Buch-Position, die IBKR gar nicht hält → cash-neutral ausgebucht."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("MSFT", 57.91, 417.35))
    cash_before = p.cash
    rep = reconcile_against_broker(p._conn, broker_positions={})  # IBKR flach
    assert "MSFT" in rep.reconciled
    assert p.get_position("MSFT") is None            # Position weg
    assert p.cash == pytest.approx(cash_before)      # Cash unverändert (Buchungs-SELL)
    # Trade-Log bleibt netto flach → kein Orphan
    assert check_integrity(p._conn, 100_000.0).orphan_trades == {}


def test_broker_reconcile_keeps_covered_position(tmp_path, monkeypatch):
    """IBKR deckt die Buch-Position (auch mit Suffix-Symbol) → nichts ändern."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("ASML.AS", 15, 1390.0))
    rep = reconcile_against_broker(p._conn, broker_positions={"ASML": 15.0})
    assert rep.ok
    assert p.get_position("ASML.AS") is not None


def test_broker_reconcile_reports_untracked(tmp_path, monkeypatch):
    """Bei IBKR gehalten, aber nicht im Buch → nur gemeldet, nicht adoptiert."""
    p = make_portfolio(tmp_path, monkeypatch)
    rep = reconcile_against_broker(p._conn, broker_positions={"NVDA": 5.0})
    assert rep.untracked == {"NVDA": 5.0}
    assert rep.reconciled == {}


def test_broker_reconcile_sets_sl_cooldown(tmp_path, monkeypatch):
    """Phantom-Ausbuchung (broker-seitiger GTC-Stop während Bot-Downtime
    gefeuert) muss dieselbe SL-Sperre setzen wie ein Clean-SL im Live-Pfad
    (strategy/executor.py) – sonst kann der nächste Zyklus sofort wieder
    denselben, gerade gestoppten Titel kaufen."""
    from analyzers.sl_cooldown import StopLossCooldown

    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("MSFT", 57.91, 417.35))
    rep = reconcile_against_broker(p._conn, broker_positions={})  # IBKR flach
    assert "MSFT" in rep.reconciled

    blocked, reason = StopLossCooldown().is_blocked("MSFT")
    assert blocked, reason


def test_broker_reconcile_books_real_sl_loss(tmp_path, monkeypatch):
    """META-Vorfall 27.7.2026: Phantom-Ausbuchung darf den vermuteten SL-Exit
    nicht mehr zum Einstiegspreis mit pnl=0 verstecken – das täuschte einen
    neutralen Trade vor, obwohl der Stop real unter dem Einstieg lag, und
    ließ das freigewordene Kapital wie unberührt aussehen."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("MSFT", 10, 400.0))  # SL=360.0, TP=480.0
    reconcile_against_broker(p._conn, broker_positions={})  # IBKR flach

    row = p._conn.execute(
        "SELECT price, pnl, reason FROM trades WHERE ticker='MSFT' AND action='SELL'"
    ).fetchone()
    assert row[0] == pytest.approx(360.0)          # Exit zum SL-Preis, nicht Einstieg
    assert row[1] == pytest.approx(-400.0)          # (360-400)*10 = realer Verlust
    assert "vermuteter SL-Exit" in row[2]


def test_broker_reconcile_books_real_tp_gain_after_partial(tmp_path, monkeypatch):
    """Nach Partial-TP gilt der Rest-Exit als TP-getrieben → Buchung zum
    TP-Preis, nicht zum (falschen) Einstiegspreis."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("MSFT", 10, 400.0))  # SL=360.0, TP=480.0
    p._conn.execute("UPDATE positions SET partial_tp_taken=1 WHERE ticker='MSFT'")
    p._conn.commit()
    reconcile_against_broker(p._conn, broker_positions={})

    row = p._conn.execute(
        "SELECT price, pnl FROM trades WHERE ticker='MSFT' AND action='SELL'"
    ).fetchone()
    assert row[0] == pytest.approx(480.0)
    assert row[1] == pytest.approx(800.0)           # (480-400)*10


def test_broker_reconcile_skips_cooldown_after_partial_tp(tmp_path, monkeypatch):
    """War bereits ein Partial-TP genommen, gilt der Rest-Exit nicht als
    'sauberer' Clean-SL (Pendant zur Bedingung in strategy/executor.py) →
    keine Sperre."""
    from analyzers.sl_cooldown import StopLossCooldown

    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(make_position("MSFT", 57.91, 417.35))
    p._conn.execute("UPDATE positions SET partial_tp_taken=1 WHERE ticker='MSFT'")
    p._conn.commit()
    rep = reconcile_against_broker(p._conn, broker_positions={})
    assert "MSFT" in rep.reconciled

    blocked, _ = StopLossCooldown().is_blocked("MSFT")
    assert not blocked


def test_broker_reconcile_rejects_implausible_snapshot(tmp_path, monkeypatch):
    """Buch-Korruption Juli–August 2026: broker.positions() lieferte direkt
    nach dem Connect einen halb gefüllten Cache; reconcile buchte die
    'fehlenden' echten Positionen mit fiktivem Verlust aus. Jetzt: fällt auf
    einen Schlag ein großer Teil des Buchs aus dem IBKR-Stand, wird NICHTS
    ausgebucht – nur gemeldet."""
    p = make_portfolio(tmp_path, monkeypatch, capital=1_000_000.0)
    for tk in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"):
        p.open_position(make_position(tk, 10, 100.0))
    cash_before = p.cash

    # IBKR "zeigt" nur 1 von 6 – klar unvollständiger Schnappschuss
    rep = reconcile_against_broker(p._conn, broker_positions={"AAPL": 10.0})

    assert rep.snapshot_rejected is True
    assert rep.reconciled == {}
    assert not rep.ok
    assert p.cash == pytest.approx(cash_before)
    for tk in ("MSFT", "NVDA", "AMZN", "GOOGL", "META"):
        assert p.get_position(tk) is not None, f"{tk} darf nicht ausgebucht sein"


def test_broker_reconcile_still_books_small_number_of_phantoms(tmp_path, monkeypatch):
    """Gegenprobe: 2 von 6 Positionen weg ist plausibel (Stop/TP gefeuert) →
    ganz normale Ausbuchung."""
    p = make_portfolio(tmp_path, monkeypatch, capital=1_000_000.0)
    for tk in ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"):
        p.open_position(make_position(tk, 10, 100.0))

    held = {tk: 10.0 for tk in ("AAPL", "MSFT", "NVDA", "AMZN")}
    rep = reconcile_against_broker(p._conn, broker_positions=held)

    assert rep.snapshot_rejected is False
    assert set(rep.reconciled) == {"GOOGL", "META"}
