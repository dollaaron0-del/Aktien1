#!/usr/bin/env python3
"""
Edge−Kosten-Report — die selbsttragende Ökonomie pro Entscheidung (Ziel 5).

Beantwortet: übersteigt die BRUTTO-Kante pro Trade die ihr zurechenbaren
API-Kosten? Nur wenn Edge − Kosten > 0 ist, ist Live-Kapital rational.

Einheiten-Problem, ehrlich gelöst: die Kante steckt in % (pnl_pct), die API-Kosten
in absoluten EUR pro Analyse (global getrackt, api_cost_tracker). Umrechnung über
eine Positionsgröße (Default: initial_capital × max_position_pct). Zusätzlich die
positionsgrößen-UNABHÄNGIGE Kennzahl: die Break-even-Positionsgröße, ab der die
Ø-Kante die Kosten deckt.

Kosten-Quelle, in dieser Reihenfolge:
  1. echte, pro Entscheidung attribuierte Kosten aus decision_log.cost_eur
     (die neue Naht; gefüllt, sobald der Bot live via DecisionLog.add_cost loggt);
  2. Schätzung aus dem globalen api_cost_tracker (Gesamtkosten / Analysen bzw.
     / realisierte Trades) — klar als Schätzung markiert.

Read-only, offline; greift NICHT in den Live-Pfad ein. Nutzt die Track-Record-
Statistik (scripts.track_record) als Single Source für Bootstrap-CI/Summary.

Usage:
  python -m scripts.cost_attribution
  python -m scripts.cost_attribution --position-eur 2000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore  # noqa: E402
from analyzers.decision_log import DecisionLog  # noqa: E402
from analyzers.api_cost_tracker import APICostTracker  # noqa: E402
from scripts.track_record import _bootstrap_mean_ci, _summary, _load_trades  # noqa: E402

SEED = 20260707


def _default_position_eur() -> float:
    """Typische Positionsgröße = initial_capital × max_position_pct (Fallback 2000€)."""
    try:
        from config import config
        return float(config.initial_capital) * float(config.max_position_pct)
    except Exception:
        return 2000.0


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description="Edge−Kosten-Report (Ziel 5)")
    ap.add_argument("--position-eur", type=float, default=None,
                    help="Positionsgröße für die %-Umrechnung (Default: aus config)")
    args = ap.parse_args()

    position_eur = args.position_eur or _default_position_eur()
    rng = np.random.default_rng(SEED)

    store = ExperienceStore()
    trades = _load_trades(store, {"backfill", "live"})  # echte Entscheidungen
    store.close()

    _p("═" * 64)
    _p(" Edge−Kosten-Report  —  Ziel 5 (selbsttragende Ökonomie)")
    _p("═" * 64)

    if not trades:
        _p("\n(keine gelabelten Entscheidungen — erst backfill_outcomes / Live-Trades)")
        return

    returns = [t["pnl_pct"] for t in trades]
    n = len(returns)
    gross = _summary(returns)
    gross_ci = _bootstrap_mean_ci(returns, rng)

    # ── Kosten pro Entscheidung ermitteln ───────────────────────────────────
    dlog = DecisionLog()
    cs = dlog.cost_stats()
    dlog.close()

    cost_source = ""
    cost_per_trade = None
    if cs.get("n_with_cost", 0) > 0 and cs.get("avg_cost_eur"):
        cost_per_trade = float(cs["avg_cost_eur"])
        cost_source = (f"echt attribuiert (decision_log.cost_eur, "
                       f"{cs['n_with_cost']}/{cs['n_decisions']} Entscheidungen)")
    else:
        # Schätzung aus dem globalen Tracker.
        summ = APICostTracker().summary()
        total_cost = float(summ.get("total_cost_eur") or 0.0)
        total_analyses = int(summ.get("total_analyses") or 0)
        per_analysis = total_cost / total_analyses if total_analyses else 0.0
        per_trade_alloc = total_cost / n if n else 0.0
        # konservativ: gesamte API-Ausgaben den realisierten Trades zurechnen.
        cost_per_trade = per_trade_alloc
        cost_source = (f"GESCHÄTZT: {total_cost:.2f}€ Gesamtkosten / {n} Trades "
                       f"= {per_trade_alloc:.4f}€/Trade  (zum Vergleich "
                       f"{per_analysis:.5f}€/Analyse über {total_analyses} Analysen)")

    cost_pct = cost_per_trade / position_eur * 100 if position_eur > 0 else 0.0

    _p(f"Trades           : {n}   ·   Positionsgröße {position_eur:,.0f}€")
    _p(f"Kosten-Quelle    : {cost_source}")

    # ── Brutto-Kante ────────────────────────────────────────────────────────
    _p("\n── Brutto (vor Kosten) " + "─" * 37)
    lo = gross_ci.get("lo", float("nan"))
    hi = gross_ci.get("hi", float("nan"))
    ci_txt = f"95%-CI [{lo:+.3f}, {hi:+.3f}]" if np.isfinite(lo) else "CI n/a"
    _p(f"  Ø-Kante/Trade    : {gross['mean']:+.3f}%   {ci_txt}")

    # ── Kosten ──────────────────────────────────────────────────────────────
    _p("\n── Kosten pro Trade " + "─" * 40)
    _p(f"  API-Kosten/Trade : {cost_per_trade:.4f}€   =   {cost_pct:+.4f}% "
       f"der Position ({position_eur:,.0f}€)")

    # ── Netto-Kante (Edge − Kosten) ─────────────────────────────────────────
    net_returns = [r - cost_pct for r in returns]
    net = _summary(net_returns)
    net_ci = _bootstrap_mean_ci(net_returns, rng)
    _p("\n── Netto (Edge − Kosten) " + "─" * 35)
    nlo, nhi = net_ci.get("lo", float("nan")), net_ci.get("hi", float("nan"))
    nci_txt = f"95%-CI [{nlo:+.3f}, {nhi:+.3f}]" if np.isfinite(nlo) else "CI n/a"
    _p(f"  Ø-Netto/Trade    : {net['mean']:+.3f}%   {nci_txt}")

    # Kostenanteil an der (Betrags-)Kante + Break-even-Positionsgröße.
    if gross["mean"] > 0:
        share = cost_pct / gross["mean"] * 100
        breakeven = cost_per_trade / (gross["mean"] / 100.0)
        _p(f"  Kostenanteil     : {share:.2f}% der Brutto-Kante")
        _p(f"  Break-even-Größe : ab {breakeven:,.0f}€ Position deckt die Ø-Kante die Kosten")
    else:
        _p("  Break-even-Größe : n/a — Brutto-Kante ≤ 0, keine Positionsgröße macht "
           "das selbsttragend")
        _p("  → Das Problem ist die Kante selbst, NICHT die API-Kosten.")

    # ── Gate ────────────────────────────────────────────────────────────────
    _p("\n" + "═" * 64)
    _p(" SELBSTTRAGEND-GATE (Netto-Kante nach Kosten signifikant > 0)")
    _p("═" * 64)
    ok = np.isfinite(nlo) and nlo > 0
    detail = (f"Netto-CI-Untergrenze {nlo:+.3f}%" if np.isfinite(nlo)
              else "zu wenig Daten für CI")
    _p(f"  [{'✓ PASS' if ok else '✗ FAIL'}]  Edge − Kosten > 0")
    _p(f"           → {detail}")
    _p("")
    if ok:
        _p("  ►► SELBSTTRAGEND — die Kante deckt die Kosten (mit Konfidenz).")
    else:
        _p("  ►► NICHT SELBSTTRAGEND — kein rationaler Live-Kapital-Einsatz.")
    _p("")
    if cost_source.startswith("GESCHÄTZT"):
        _p("Hinweis: Kosten geschätzt (global). Echte Pro-Entscheidungs-Kosten füllt "
           "der Bot künftig via DecisionLog.add_cost → dann exakt.")


if __name__ == "__main__":
    main()
