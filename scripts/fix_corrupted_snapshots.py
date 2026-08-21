"""
Bereinigt korrupte Portfolio-Snapshots aus performance.db.

Entfernt Einträge die mehr als 5× den Minimalwert betragen
(typischer Fehler: Portfolio-Cash auf $1M gesetzt durch falschen Preis).

Verwendung:
    cd /opt/Aktien && python3 scripts/fix_corrupted_snapshots.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "performance.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, snapshot_date, total_value FROM portfolio_snapshots ORDER BY snapshot_date"
).fetchall()

if not rows:
    print("Keine Snapshots gefunden.")
    conn.close()
    exit()

values = [r["total_value"] for r in rows]
min_val = min(values)
threshold = min_val * 5

corrupted = [r for r in rows if r["total_value"] > threshold]
clean = [r for r in rows if r["total_value"] <= threshold]

print(f"Snapshots gesamt:   {len(rows)}")
print(f"Minimum-Wert:       ${min_val:,.2f}")
print(f"Schwelle (×5):      ${threshold:,.2f}")
print(f"Korrupt (>{threshold:,.0f}$): {len(corrupted)}")
print(f"Korrekt:            {len(clean)}")

if not corrupted:
    print("\nKeine korrupten Snapshots gefunden – nichts zu tun.")
    conn.close()
    exit()

print("\nKorrupte Snapshots:")
for r in corrupted[:10]:
    print(f"  ID {r['id']:5}  {r['snapshot_date'][:16]}  ${r['total_value']:>12,.2f}")
if len(corrupted) > 10:
    print(f"  ... und {len(corrupted) - 10} weitere")

confirm = input("\nJetzt bereinigen? (ja/nein): ").strip().lower()
if confirm != "ja":
    print("Abgebrochen.")
    conn.close()
    exit()

ids = [r["id"] for r in corrupted]
conn.execute(f"DELETE FROM portfolio_snapshots WHERE id IN ({','.join('?' * len(ids))})", ids)
conn.commit()
print(f"\n{len(ids)} korrupte Snapshots gelöscht.")

# Portfolio-Cash prüfen und reparieren
portfolio_db = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.db")
if os.path.exists(portfolio_db):
    pconn = sqlite3.connect(portfolio_db)
    cash_row = pconn.execute("SELECT value FROM state WHERE key='cash'").fetchone()
    if cash_row:
        cash = float(cash_row[0])
        if cash > threshold:
            print(f"\nACHTUNG: portfolio.cash = ${cash:,.2f} (korrupt!)")
            reset_cash = input(f"Cash auf ${min_val:,.2f} zurücksetzen? (ja/nein): ").strip().lower()
            if reset_cash == "ja":
                pconn.execute("UPDATE state SET value=? WHERE key='cash'", (str(min_val),))
                pconn.commit()
                print(f"Cash auf ${min_val:,.2f} zurückgesetzt.")
        else:
            print(f"\nPortfolio-Cash: ${cash:,.2f} (OK)")
    pconn.close()

conn.close()
print("\nFertig. Bot neu starten: pkill -f main.py && sleep 2 && nohup python3 main.py > logs/bot.log 2>&1 &")
