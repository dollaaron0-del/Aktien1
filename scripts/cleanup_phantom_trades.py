"""
cleanup_phantom_trades.py

Zeigt alle offenen Positionen und lässt dich einzelne löschen die nie
wirklich in IBKR ausgeführt wurden (Phantom-Trades).

Das Cash wird für jede gelöschte Position korrekt zurückgebucht.
Journal- und Performance-Einträge werden ebenfalls bereinigt.

Aufruf (auf dem Server):
    cd /opt/Aktien && source venv/bin/activate
    python scripts/cleanup_phantom_trades.py
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORTFOLIO_DB   = os.path.join("data", "portfolio.db")
JOURNAL_DB     = os.path.join("data", "trade_journal.db")
PERFORMANCE_DB = os.path.join("data", "performance.db")


def _open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def list_positions(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT ticker, shares, entry_price, entry_date, stop_loss, take_profit "
        "FROM positions ORDER BY entry_date"
    ).fetchall()
    return [dict(r) for r in rows]


def get_cash(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM portfolio_meta WHERE key='cash'").fetchone()
    return float(row["value"]) if row else 0.0


def set_cash(conn: sqlite3.Connection, cash: float):
    conn.execute(
        "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('cash', ?)",
        (str(cash),),
    )


def delete_position(portfolio_conn, journal_conn, perf_conn, ticker: str) -> float:
    """
    Löscht eine Position aus allen Datenbanken.
    Gibt den zurückgebuchten Betrag (shares * entry_price) zurück.
    """
    row = portfolio_conn.execute(
        "SELECT shares, entry_price FROM positions WHERE ticker=?", (ticker,)
    ).fetchone()

    refund = 0.0
    if row:
        refund = float(row["shares"]) * float(row["entry_price"])
        portfolio_conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))

    # Trade-Journal: alle Events für diesen Ticker löschen die noch als offen gelten
    if journal_conn:
        try:
            journal_conn.execute(
                "DELETE FROM trade_events WHERE ticker=? AND position_open=1", (ticker,)
            )
        except Exception:
            pass

    # Performance-Tracker: offene Prediction löschen
    if perf_conn:
        try:
            perf_conn.execute(
                "DELETE FROM predictions WHERE ticker=? AND sell_date IS NULL", (ticker,)
            )
        except Exception:
            pass

    return refund


def main():
    for db in (PORTFOLIO_DB,):
        if not os.path.exists(db):
            print(f"Datenbank nicht gefunden: {db}")
            print("Starte den Bot mindestens einmal bevor du diesen Script ausführst.")
            sys.exit(1)

    port_conn = _open(PORTFOLIO_DB)
    jour_conn = _open(JOURNAL_DB) if os.path.exists(JOURNAL_DB) else None
    perf_conn = _open(PERFORMANCE_DB) if os.path.exists(PERFORMANCE_DB) else None

    positions = list_positions(port_conn)
    cash = get_cash(port_conn)

    print(f"\n{'='*55}")
    print(f"  Phantom-Trade Cleanup")
    print(f"{'='*55}")
    print(f"\nAktuelles Cash in DB: ${cash:,.2f}")
    print(f"Offene Positionen: {len(positions)}\n")

    if not positions:
        print("Keine offenen Positionen — nichts zu tun.")
        port_conn.close()
        return

    print(f"{'#':<4} {'Ticker':<10} {'Stück':>8} {'Kurs':>10} {'Wert':>12} {'Seit'}")
    print("-" * 55)
    for i, p in enumerate(positions, 1):
        wert = p["shares"] * p["entry_price"]
        datum = (p["entry_date"] or "")[:10]
        print(f"{i:<4} {p['ticker']:<10} {p['shares']:>8.4f} ${p['entry_price']:>9.2f} ${wert:>11.2f} {datum}")

    print(f"\n{'='*55}")
    print("Welche Positionen sollen gelöscht werden?")
    print("  Eingabe: Nummern getrennt durch Komma (z.B. 1,3)")
    print("  'alle'  – alle Positionen löschen")
    print("  'exit'  – abbrechen")
    print()

    auswahl = input("Auswahl: ").strip().lower()

    if auswahl in ("exit", "q", ""):
        print("Abgebrochen.")
        port_conn.close()
        return

    if auswahl == "alle":
        to_delete = positions
    else:
        try:
            indices = [int(x.strip()) - 1 for x in auswahl.split(",")]
            to_delete = [positions[i] for i in indices if 0 <= i < len(positions)]
        except (ValueError, IndexError):
            print("Ungültige Eingabe — abgebrochen.")
            port_conn.close()
            return

    if not to_delete:
        print("Keine gültige Auswahl — abgebrochen.")
        port_conn.close()
        return

    print(f"\nFolgende {len(to_delete)} Position(en) werden gelöscht:")
    total_refund = 0.0
    for p in to_delete:
        wert = p["shares"] * p["entry_price"]
        total_refund += wert
        print(f"  ✗  {p['ticker']:10} ${wert:,.2f}")

    print(f"\nCash-Rückbuchung: +${total_refund:,.2f}")
    print(f"Neues Cash nach Bereinigung: ${cash + total_refund:,.2f}")

    confirm = input("\nFortfahren? (ja/nein): ").strip().lower()
    if confirm not in ("ja", "j", "yes", "y"):
        print("Abgebrochen.")
        port_conn.close()
        return

    actual_refund = 0.0
    for p in to_delete:
        refund = delete_position(port_conn, jour_conn, perf_conn, p["ticker"])
        actual_refund += refund
        print(f"  ✓  {p['ticker']} gelöscht (${refund:,.2f} zurückgebucht)")

    new_cash = cash + actual_refund
    set_cash(port_conn, new_cash)

    port_conn.commit()
    if jour_conn:
        jour_conn.commit()
        jour_conn.close()
    if perf_conn:
        perf_conn.commit()
        perf_conn.close()
    port_conn.close()

    print(f"\n✅ {len(to_delete)} Position(en) bereinigt")
    print(f"✅ Cash: ${cash:,.2f} → ${new_cash:,.2f} (+${actual_refund:,.2f})")
    print(f"\n→ Dashboard neu laden um die Änderungen zu sehen.")


if __name__ == "__main__":
    main()
