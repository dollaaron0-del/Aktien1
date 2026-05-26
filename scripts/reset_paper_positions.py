"""
reset_paper_positions.py

Löscht alle Paper-Positionen aus der Portfolio-Datenbank und stellt die
Ticker in die Analyse-Queue damit der Bot sie beim nächsten Lauf via
Alpaca neu kauft (sofern die Signale noch gültig sind).

Aufruf (auf dem Server):
    cd /opt/Aktien && source venv/bin/activate
    python scripts/reset_paper_positions.py
    python main.py --once
"""
import os
import sqlite3
import sys

# Projektpfad zum Python-Path hinzufügen
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join("data", "portfolio.db")


def main():
    if not os.path.exists(DB_PATH):
        print(f"Portfolio-DB nicht gefunden: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT ticker, shares, entry_price, entry_date FROM positions")
    positions = cur.fetchall()

    if not positions:
        print("Keine offenen Positionen in der DB – nichts zu tun.")
        conn.close()
        return

    print(f"\nGefundene Paper-Positionen ({len(positions)}):")
    tickers = []
    for ticker, shares, price, date in positions:
        print(f"  {ticker}: {shares} Stück @ ${price:.2f} (seit {date[:10]})")
        tickers.append(ticker)

    print(f"\nDiese {len(tickers)} Positionen werden aus der DB gelöscht.")
    confirm = input("Fortfahren? (ja/nein): ").strip().lower()
    if confirm not in ("ja", "j", "yes", "y"):
        print("Abgebrochen.")
        conn.close()
        return

    cur.execute("DELETE FROM positions")
    conn.commit()
    conn.close()
    print(f"✅ {len(tickers)} Positionen gelöscht.")

    # Ticker in Analyse-Queue eintragen
    try:
        from analyzers import user_request_queue as _urq
        for t in tickers:
            _urq.add_ticker(t)
        print(f"✅ {len(tickers)} Ticker zur Analyse-Queue hinzugefügt: {', '.join(tickers)}")
        print("\nJetzt ausführen:")
        print("  python main.py --once")
        print("\nDer Bot analysiert alle Ticker neu und kauft sie via Alpaca wenn die Signale noch stimmen.")
    except Exception as e:
        print(f"⚠️  Queue-Eintrag fehlgeschlagen: {e}")
        print(f"Ticker manuell eintragen: {', '.join(tickers)}")


if __name__ == "__main__":
    main()
