#!/usr/bin/env python3
"""
Backfill Regime — labelt historische Entscheidungen mit dem Marktregime.

Die per-Regime-Kalibrierung (analyzers/calibration.py, Dimension 'regime') lernt
nur aus Zeilen, deren regime-Spalte gesetzt ist. Live-Entries bekommen das Label
vom Bot; die historischen Backfill-Zeilen haben regime=NULL. Dieses Skript labelt
sie nach: pro Entscheidungstag wird ein Trailing-Fenster von SPY-Tageskursen mit
strategy_lab.regime.classify_window klassifiziert und in den Label-Raum des Bots
gemappt (BULL/NEUTRAL/BEAR/CRISIS) — derselbe Raum, den Live-Zeilen tragen, damit
die Kalibrierungs-Buckets nicht in zwei inkompatible Welten zerfallen.

EHRLICHE CAVEATS:
  * Das ist ein PREIS-Proxy (SPY-Trailing-Fenster), nicht das originale
    hedge_strategy-Regime des Bots (das auch Makro-News einbezieht). Für
    Bucket-Zwecke konsistent genug; methodisch aber ein Proxy.
  * macro_bias wird bewusst NICHT nachgelabelt — der historische Makro-Snapshot
    (FRED/EIA/News-Mix) lässt sich nicht ehrlich rekonstruieren. Lücke > Fiktion.
  * Idempotent: es werden nur Zeilen mit regime IS NULL angefasst. Nach dem
    Demo-Daten-Rücktausch (siehe Memory) auf der echten DB einfach erneut laufen.

Usage:
  python -m scripts.backfill_regime                # data/experience.db labeln
  python -m scripts.backfill_regime --dry-run      # nur zeigen, nichts schreiben
  python -m scripts.backfill_regime --db PATH      # andere DB (z.B. nach Rücktausch)
  python -m scripts.backfill_regime --window 60    # Trailing-Fenster in Bars
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore, DB_PATH  # noqa: E402
from scripts.backfill_outcomes import _load_bars, _f  # noqa: E402

_PROXY_TICKER = "SPY"      # Markt-Proxy für das Regime-Fenster
_WINDOW_BARS = 60          # Trailing-Fenster (Handelstage) je Entscheidungstag
_HISTORY_PAD_DAYS = 120    # Kalendertage Vorlauf vor der ältesten Entscheidung
_MIN_BARS = 25             # unter so wenig Trailing-Bars kein Urteil (UNKNOWN)

# strategy_lab-Label (TREND_VOL) → Bot-Label-Raum (wie hedge_strategy/Live-Zeilen).
# Konservativ: ein volatiler Bär ist die Krisen-Schublade; ein volatiler Bulle
# bleibt Bulle (Trend schlägt Vola — die Vola steckt schon im strategy_lab-Label).
_LAB_TO_BOT = {
    "BEAR_VOLATILE": "CRISIS",
    "BEAR_CALM": "BEAR",
    "SIDE_VOLATILE": "NEUTRAL",
    "SIDE_CALM": "NEUTRAL",
    "BULL_VOLATILE": "BULL",
    "BULL_CALM": "BULL",
}


def to_bot_label(lab_label: str) -> Optional[str]:
    """Mappt ein strategy_lab-Regime ('BULL_CALM', …) in den Bot-Label-Raum.
    None bei UNKNOWN/unbekannt — dann wird die Zeile NICHT gelabelt."""
    return _LAB_TO_BOT.get((lab_label or "").strip().upper())


def regime_by_date(bars: List[Dict], window: int = _WINDOW_BARS,
                   min_bars: int = _MIN_BARS) -> Dict[str, str]:
    """Pro Handelstag das Bot-Regime aus dem Trailing-Fenster (rein, testbar).

    bars: chronologische Tages-Bars ({date, close, …}) des Markt-Proxys.
    Liefert {date: BULL|NEUTRAL|BEAR|CRISIS} nur für Tage mit genügend Historie
    (>= min_bars Trailing-Bars); frühe Tage fehlen schlicht (kein Raten).
    """
    import pandas as pd
    from strategy_lab.regime import classify_window

    clean = [(b["date"], _f(b.get("close"))) for b in bars
             if b.get("date") and _f(b.get("close"))]
    clean.sort()
    out: Dict[str, str] = {}
    dates = [d for d, _ in clean]
    closes = [c for _, c in clean]
    for i in range(len(clean)):
        lo = max(0, i - window + 1)
        if (i - lo + 1) < min_bars:
            continue
        df = pd.DataFrame({"Close": closes[lo:i + 1]},
                          index=pd.to_datetime(dates[lo:i + 1]))
        bot = to_bot_label(classify_window({_PROXY_TICKER: df}))
        if bot:
            out[dates[i]] = bot
    return out


def label_for(decided_at: str, by_date: Dict[str, str]) -> Optional[str]:
    """Regime für einen Entscheidungszeitpunkt: letzter Handelstag <= decided_at.
    (Wochenende/Feiertag fällt auf den Vortag zurück; nie in die Zukunft.)"""
    d = (decided_at or "")[:10]
    if not d:
        return None
    if d in by_date:
        return by_date[d]
    earlier = [k for k in by_date if k <= d]
    return by_date[max(earlier)] if earlier else None


# ── Orchestrierung ─────────────────────────────────────────────────────────────
def run(db_path: str = DB_PATH, dry_run: bool = False,
        window: int = _WINDOW_BARS) -> Dict:
    store = ExperienceStore(db_path=db_path)   # öffnet + migriert (regime-Spalte)
    rows = store._conn.execute(
        "SELECT id, decided_at, label_source FROM decisions WHERE regime IS NULL "
        "ORDER BY decided_at"
    ).fetchall()
    counts = {"candidates": len(rows), "labeled": 0, "no_regime": 0}
    dist: Dict[str, int] = {}
    if not rows:
        print("Nichts zu tun: alle Zeilen haben bereits ein Regime.")
        store.close()
        return counts

    oldest = rows[0]["decided_at"][:10]
    start = (datetime.fromisoformat(oldest)
             - timedelta(days=_HISTORY_PAD_DAYS)).strftime("%Y-%m-%d")
    print(f"{len(rows)} Zeilen ohne Regime ({oldest} …). "
          f"Lade {_PROXY_TICKER}-Historie ab {start} (Cache/yfinance) …")
    bars = _load_bars(_PROXY_TICKER, start)
    if len(bars) < _MIN_BARS + 1:
        print(f"ABBRUCH: nur {len(bars)} {_PROXY_TICKER}-Bars — zu wenig für ein "
              "ehrliches Trailing-Fenster. Nichts gelabelt (lieber Lücke als Raten).")
        store.close()
        return counts

    by_date = regime_by_date(bars, window=window)
    print(f"Regime-Kalender: {len(by_date)} Handelstage "
          f"({min(by_date)} → {max(by_date)})")

    for r in rows:
        label = label_for(r["decided_at"], by_date)
        if label is None:
            counts["no_regime"] += 1
            continue
        counts["labeled"] += 1
        dist[label] = dist.get(label, 0) + 1
        if not dry_run:
            with store._lock:
                store._conn.execute(
                    "UPDATE decisions SET regime=? WHERE id=? AND regime IS NULL",
                    (label, int(r["id"])),
                )
    if not dry_run:
        store._conn.commit()

    print("\n── Ergebnis ───────────────────────────────")
    print(f"gelabelt : {counts['labeled']} / {counts['candidates']}"
          f"{'  (dry-run – nichts geschrieben)' if dry_run else ''}")
    print(f"ohne Urteil (zu wenig Historie): {counts['no_regime']}")
    print(f"Verteilung: {dist}")
    print("Hinweis: macro_bias bleibt bewusst NULL (nicht rekonstruierbar); "
          "Labels sind ein SPY-Preis-Proxy, kein hedge_strategy-Original.")
    store.close()
    counts["distribution"] = dist
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Regime-Backfill für experience.db")
    ap.add_argument("--db", default=DB_PATH, help="Pfad zur experience.db")
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben")
    ap.add_argument("--window", type=int, default=_WINDOW_BARS,
                    help="Trailing-Fenster in Handelstagen")
    args = ap.parse_args()
    run(db_path=args.db, dry_run=args.dry_run, window=args.window)


if __name__ == "__main__":
    main()
