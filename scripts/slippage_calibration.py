#!/usr/bin/env python3
"""
Slippage-Kalibrierung aus echten IBKR-Paper-Fills — Roadmap 5.3.

Backtests (analyzers/backtester.py, analyzers/walk_forward_backtester.py)
nehmen bisher pauschal slippage_pct=0.1% pro Trade an — eine Annahme, nie
gegen echte Fills geprüft. broker/ibkr_broker.py::_place_order() reicht seit
9.8.2026 den Referenzpreis (Kurs zum Entscheidungszeitpunkt) als
market_price durch order_log.py in data/order_log.db durch; dieses Skript
vergleicht ihn mit dem tatsächlichen fill_price echter IBKR-Fills.

Bewusst NUR mode='ibkr' (echte Broker-Mechanik, Paper-Account) — der interne
PaperBroker simuliert Slippage bereits selbst (_calc_slippage) und hat kein
market_price in order_log (kein reference_price-Wiring dort, andere
Fragestellung). Retroaktiv nicht möglich: ältere Fills vor der
Instrumentierung haben market_price=NULL und werden ausgeschlossen — das
Skript liefert erst ab neuen Fills verwertbare Zahlen.

Vorzeichen-Konvention: slippage_pct > 0 heißt IMMER "schlechter als
erwartet" (BUY teurer als market_price, SELL billiger als market_price).

Usage:
  python -m scripts.slippage_calibration
  python -m scripts.slippage_calibration --min-n 20
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.track_record import _bootstrap_mean_ci  # noqa: E402

DB_PATH = Path(__file__).parent.parent / "data" / "order_log.db"
CURRENT_ASSUMPTION_PCT = 0.1  # analyzers/backtester.py::slippage_pct=0.001


def load_slippage_rows(db_path: Path = DB_PATH) -> List[Dict]:
    """Signierte Slippage in % je verwertbarem echten IBKR-Fill.

    Verwertbar: status='filled', mode='ibkr', market_price gesetzt und > 0.
    Teilausführungen zählen mit (Slippage bezieht sich auf den gefüllten
    Anteil, unabhängig von der ursprünglich georderten Menge)."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT ts, ticker, action, fill_price, market_price, partial "
            "FROM orders WHERE status='filled' AND mode='ibkr' "
            "AND market_price IS NOT NULL AND market_price > 0"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    out = []
    for r in rows:
        raw_pct = (r["fill_price"] - r["market_price"]) / r["market_price"] * 100.0
        signed_pct = raw_pct if r["action"] == "BUY" else -raw_pct
        out.append({
            "ts": r["ts"], "ticker": r["ticker"], "action": r["action"],
            "fill_price": r["fill_price"], "market_price": r["market_price"],
            "partial": bool(r["partial"]), "slippage_pct": signed_pct,
        })
    return out


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-n", type=int, default=10,
                     help="Mindest-Fills für ein Verdikt (Default 10, Ehrlichkeits-Gate)")
    args = ap.parse_args()

    rows = load_slippage_rows()
    _p("── Slippage-Kalibrierung (Roadmap 5.3) " + "─" * 20)
    _p(f"Aktuelle Backtest-Annahme: {CURRENT_ASSUMPTION_PCT:.2f}% pro Trade "
       f"(analyzers/backtester.py, analyzers/walk_forward_backtester.py)")
    _p(f"Verwertbare echte IBKR-Fills mit market_price: {len(rows)}")

    if len(rows) < args.min_n:
        _p(f"\nUnter Mindest-n ({args.min_n}) — kein Verdikt, nur Rohzahl. "
           "Die Instrumentierung ist seit 9.8.2026 aktiv; ältere Fills haben "
           "kein market_price. Erneut laufen lassen, sobald mehr echte "
           "IBKR-Fills vorliegen.")
        return

    rng = np.random.default_rng(20260809)
    overall = _bootstrap_mean_ci([r["slippage_pct"] for r in rows], rng)
    _p(f"\nGesamt: n={overall['n']} Ø={overall['mean']:+.3f}% "
       f"95%-CI [{overall['lo']:+.3f}, {overall['hi']:+.3f}] "
       f"P(≤0)={overall['p_le0']*100:.0f}%")

    for action in ("BUY", "SELL"):
        sub = [r["slippage_pct"] for r in rows if r["action"] == action]
        if len(sub) < args.min_n:
            _p(f"{action}: n={len(sub)} — unter Mindest-n, kein CI")
            continue
        ci = _bootstrap_mean_ci(sub, rng)
        _p(f"{action}: n={ci['n']} Ø={ci['mean']:+.3f}% "
           f"95%-CI [{ci['lo']:+.3f}, {ci['hi']:+.3f}]")

    verdict = "zu niedrig" if overall["lo"] > CURRENT_ASSUMPTION_PCT else (
        "zu hoch" if overall["hi"] < CURRENT_ASSUMPTION_PCT else "im Rahmen der Unsicherheit")
    _p(f"\nVerdikt: Backtest-Annahme ({CURRENT_ASSUMPTION_PCT:.2f}%) liegt "
       f"{verdict} verglichen mit dem echten CI.")


if __name__ == "__main__":
    main()
