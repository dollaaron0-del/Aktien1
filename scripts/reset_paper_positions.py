"""
reset_paper_positions.py

Löscht alle Paper-Positionen aus der Portfolio-Datenbank, stellt das
Cash auf INITIAL_CAPITAL zurück und trägt die Ticker zur Analyse-Queue
ein, damit der Bot sie neu bewertet.

Aufruf (auf dem Server):
    cd /opt/Aktien && source venv/bin/activate
    python scripts/reset_paper_positions.py
    python main.py --once
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join("data", "portfolio.db")


def main():
    if not os.path.exists(DB_PATH):
        print(f"Portfolio-DB nicht gefunden: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Aktuelle Cash-Situation anzeigen
    cur.execute("SELECT value FROM portfolio_meta WHERE key='cash'")
    row = cur.fetchone()
    current_cash = float(row[0]) if row else 0.0
    print(f"\nAktuelles Portfolio-Cash in DB: ${current_cash:,.2f}")

    # Positionen lesen
    cur.execute("SELECT ticker, shares, entry_price, entry_date FROM positions")
    positions = cur.fetchall()

    if not positions:
        print("Keine offenen Positionen in der DB.")
    else:
        print(f"\nGefundene Paper-Positionen ({len(positions)}):")
        for ticker, shares, price, date in positions:
            print(f"  {ticker}: {shares} Stück @ ${price:.2f} (seit {date[:10]})")

    # Ziel-Cash bestimmen
    try:
        from config import config
        new_cash = config.initial_capital
    except Exception:
        new_cash = 10000.0
    cash_source = f"INITIAL_CAPITAL ${new_cash:,.2f}"

    print(f"\nGeplante Aktionen:")
    print(f"  • {len(positions)} Positionen löschen")
    print(f"  • Cash auf {cash_source} setzen")
    if positions:
        tickers = [r[0] for r in positions]
        print(f"  • Ticker zur Analyse-Queue: {', '.join(tickers)}")

    confirm = input("\nFortfahren? (ja/nein): ").strip().lower()
    if confirm not in ("ja", "j", "yes", "y"):
        print("Abgebrochen.")
        conn.close()
        return

    # Für jede gelöschte Position einen Gegen-SELL ins Trade-Log buchen, damit
    # keine Phantom-Trades (BUY ohne SELL) zurückbleiben. Sonst driften Trade-Log
    # und Positions-Tabelle auseinander (siehe portfolio/integrity.py).
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    for ticker, shares, price, _date in positions:
        cur.execute(
            """
            INSERT INTO trades (ticker, action, shares, price, timestamp, pnl, reason)
            VALUES (?, 'SELL', ?, ?, ?, 0.0, 'Portfolio-Reset')
            """,
            (ticker, shares, price, now),
        )

    # Positionen löschen
    cur.execute("DELETE FROM positions")

    # Cash setzen + Reset-Marker für die Cash-Identitätsprüfung
    cur.execute(
        "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('cash', ?)",
        (str(new_cash),),
    )
    cur.execute(
        "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('reset_at', ?)",
        (now,),
    )
    cur.execute(
        "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('epoch_cash', ?)",
        (str(new_cash),),
    )
    conn.commit()
    conn.close()

    print(f"\n✅ Positionen gelöscht.")
    print(f"✅ Portfolio-Cash gesetzt auf ${new_cash:,.2f} ({cash_source})")

    # Ticker in Analyse-Queue eintragen
    if positions:
        tickers = [r[0] for r in positions]
        try:
            from analyzers import user_request_queue as _urq
            for t in tickers:
                _urq.add_ticker(t)
            print(f"✅ {len(tickers)} Ticker zur Analyse-Queue: {', '.join(tickers)}")
        except Exception as e:
            print(f"⚠️  Queue-Eintrag fehlgeschlagen: {e}")

    print("\n→ Jetzt ausführen:")
    print("  python main.py --once")


if __name__ == "__main__":
    main()
