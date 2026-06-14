#!/usr/bin/env python3
"""
scripts/monday_cycle_check.py – Gegencheck des ersten Analyse-Vollzyklus nach
dem Crash-Fix (geplant für Mo 15.06.2026, nach dem 07:30-Zyklus).

Prüft fünf Punkte und meldet das Ergebnis per Telegram (--dry-run = nur Konsole):
  1. Prediction-Tracking: füllt sich performance.db.predictions seit Sonntag?
  2. Snapshots: hat der neueste portfolio_snapshot positions_value/n_positions > 0
     (wenn Positionen offen sind)?
  3. Portfolio-Integrität: check_integrity muss OK sein.
  4. Collector-Beiträge: get_source_contributions(days=3) – Null-Quellen als
     Abschalt-Kandidaten auflisten.
  5. 0-Tage-Bug: keine neuen Trades mit "Max. Haltedauer (0d)".

Aufruf:
  venv/bin/python scripts/monday_cycle_check.py            # sendet Telegram
  venv/bin/python scripts/monday_cycle_check.py --dry-run  # nur Konsole
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DATA = os.path.join(PROJECT_ROOT, "data")


def _check_predictions() -> str:
    """Neue Vorhersagen seit gestern → Tracking lebt."""
    since = (datetime.utcnow() - timedelta(days=2)).isoformat()
    try:
        con = sqlite3.connect(os.path.join(DATA, "performance.db"))
        total = con.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        recent = con.execute(
            "SELECT COUNT(*) FROM predictions WHERE predicted_at > ?", (since,)
        ).fetchone()[0]
        con.close()
    except Exception as e:
        return f"❓ predictions nicht lesbar: {e}"
    if recent > 0:
        return f"✅ Prediction-Tracking aktiv: {recent} neue (gesamt {total})"
    if total > 0:
        return f"⚠️ predictions vorhanden ({total}) aber keine neuen seit 48h"
    return "❌ predictions weiterhin LEER – Tracking greift nicht (Wiring prüfen)"


def _check_snapshots() -> str:
    try:
        con = sqlite3.connect(os.path.join(DATA, "performance.db"))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT total_value, positions_value, n_positions, recorded_at "
            "FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        con.close()
    except Exception as e:
        return f"❓ snapshots nicht lesbar: {e}"
    if not row:
        return "❌ keine Snapshots vorhanden"
    pv = row["positions_value"] or 0
    n = row["n_positions"] or 0
    when = (row["recorded_at"] or "")[:16]
    if n > 0 and pv > 0:
        return f"✅ Snapshot OK ({when}): {n} Pos., positions_value ${pv:,.0f}"
    if n == 0:
        return f"➖ Snapshot ({when}): 0 Positionen (positions_value 0 ist dann korrekt)"
    return f"⚠️ Snapshot ({when}): n_positions={n} aber positions_value={pv} – prüfen"


def _check_integrity() -> str:
    try:
        from portfolio.integrity import check_integrity
        from config import config
        rep = check_integrity(os.path.join(DATA, "portfolio.db"), config.initial_capital)
        return ("✅ Portfolio-Integrität OK" if rep.ok
                else "⚠️ Integrität: " + "; ".join(rep.issues))
    except Exception as e:
        return f"❓ Integritätscheck-Fehler: {e}"


def _check_sources() -> str:
    try:
        from analyzers.analysis_log import AnalysisLog
        contrib = AnalysisLog().get_source_contributions(days=3)
    except Exception as e:
        return f"❓ Collector-Auswertung-Fehler: {e}"
    if not contrib:
        return "➖ Noch keine Breakdown-Daten (kein Vollzyklus seit Deploy?)"
    dead = [c["source"] for c in contrib if c["total_hits"] == 0]
    top = [f"{c['source']}({c['total_hits']})" for c in contrib[:5] if c["total_hits"] > 0]
    out = f"📊 Top-Quellen: {', '.join(top)}"
    if dead:
        out += f"\n🔌 0-Treffer (Abschalt-Kandidaten): {', '.join(dead)}"
    return out


def _check_zero_day() -> str:
    since = (datetime.utcnow() - timedelta(days=2)).isoformat()
    try:
        con = sqlite3.connect(os.path.join(DATA, "portfolio.db"))
        rows = con.execute(
            "SELECT ticker FROM trades WHERE action='SELL' AND reason LIKE '%Haltedauer (0d)%' "
            "AND timestamp > ?", (since,)
        ).fetchall()
        con.close()
    except Exception as e:
        return f"❓ trades nicht lesbar: {e}"
    if rows:
        return f"❌ 0-Tage-Bug WIEDER aufgetreten: {', '.join(r[0] for r in rows)}"
    return "✅ Kein 0-Tage-Sofort-Exit"


def build_report() -> str:
    parts = [
        "🔍 <b>Montags-Gegencheck (erster Vollzyklus nach Crash-Fix)</b>",
        "",
        "1. " + _check_predictions(),
        "2. " + _check_snapshots(),
        "3. " + _check_integrity(),
        "4. " + _check_sources(),
        "5. " + _check_zero_day(),
    ]
    return "\n".join(parts)


def main() -> int:
    report = build_report()
    print(report.replace("<b>", "").replace("</b>", ""))
    if "--dry-run" not in sys.argv:
        try:
            from notifier.telegram_notifier import TelegramNotifier
            TelegramNotifier().send(report)
        except Exception as e:
            print(f"[Telegram fehlgeschlagen: {e}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
