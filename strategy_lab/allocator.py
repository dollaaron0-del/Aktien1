"""
Meta-Allokator (Roadmap Phase 5) – "speist die Gewinner additiv in den Bot".

Liest die Promotion-Registry (Phase 4) und macht daraus eine konkrete,
read-only Allokations-Entscheidung:

  1. weight_plan()      – aktive (ROBUST) Strategien → gekappte, renormierte
                          Gewichte (Diversifikations-Guardrail: keine Strategie
                          dominiert). Risiko-Budget-Anteil je Strategie.
  2. current_signals()  – welche aktive Strategie feuert HEUTE für welche Ticker
                          (über die Phase-5-Signal-Naht der Familien).
  3. combine_signals()  – kombiniert die Heute-Signale gewichtet zu einer
                          Ticker→Konviktion-Karte. GENAU die Naht, die der Bot
                          später additiv konsultieren würde.

WICHTIG: Dieses Modul greift NICHT in den Live-Pfad ein. Es ist die
Entscheidungs-Schicht; die Verdrahtung in bot/runner.py erfolgt erst auf
ausdrücklichen Wunsch (Bot ist bewusst pausiert). Rein deterministisch,
kein LLM, kein Netz (Loader injizierbar).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from strategy_lab import promotion
from strategy_lab.strategies import get


def _cap_and_renormalize(weights: Dict[str, float], max_weight: float) -> Dict[str, float]:
    """Wasser-Füll-Kappung: kein Eintrag über max_weight; Überschuss proportional
    auf die ungekappten verteilen, bis stabil. Summe bleibt 1 (sofern möglich)."""
    if not weights:
        return {}
    n = len(weights)
    # Untergrenze: gleichgewichtet, falls max_weight die Verteilung erdrückt.
    max_weight = max(max_weight, 1.0 / n)
    w = dict(weights)
    for _ in range(n + 1):
        over = {k: v for k, v in w.items() if v > max_weight + 1e-12}
        if not over:
            break
        excess = sum(v - max_weight for v in over.values())
        for k in over:
            w[k] = max_weight
        free = [k for k in w if k not in over]
        free_total = sum(w[k] for k in free)
        if free_total <= 0:
            break
        for k in free:
            w[k] += excess * (w[k] / free_total)
    total = sum(w.values())
    return {k: round(v / total, 4) for k, v in w.items()} if total > 0 else w


def weight_plan(max_weight: float = 0.6, risk_budget: float = 1.0) -> List[Dict]:
    """Allokations-Plan aus der Registry: aktive Strategien mit gekappten,
    renormierten Gewichten und Risiko-Anteil (weight * risk_budget)."""
    active = promotion.active_strategies()
    if not active:
        return []
    raw = {e["strategy"]: float(e.get("weight", 0.0)) for e in active}
    if sum(raw.values()) <= 0:  # Registry ohne Gewichte → gleichgewichten
        raw = {k: 1.0 / len(raw) for k in raw}
    capped = _cap_and_renormalize(raw, max_weight)
    by_name = {e["strategy"]: e for e in active}
    plan = [{
        "strategy": name,
        "params": by_name[name].get("params", {}),
        "weight": w,
        "risk_fraction": round(w * risk_budget, 4),
    } for name, w in capped.items()]
    plan.sort(key=lambda e: -e["weight"])
    return plan


def fires_today(strategy_name: str, df, params: Optional[Dict] = None) -> Optional[bool]:
    """Feuert die Strategie HEUTE (letzter Balken)? None, wenn die Strategie
    keine Heute-Abfrage unterstützt (z.B. baseline_swing)."""
    strat = get(strategy_name)
    if strat.signal is None:
        return None
    return bool(strat.signal(df, params or {}))


def current_signals(
    universe: List[str],
    loader: Callable[[str, int], object],
    plan: Optional[List[Dict]] = None,
    years: int = 2,
) -> Dict[str, List[str]]:
    """Pro aktiver Strategie die Ticker, die HEUTE feuern. Loader injizierbar
    (netzfrei testbar). Strategien ohne Signal-Naht werden übersprungen."""
    plan = plan if plan is not None else weight_plan()
    out: Dict[str, List[str]] = {}
    for entry in plan:
        strat = get(entry["strategy"])
        if strat.signal is None:
            continue
        fired: List[str] = []
        for t in universe:
            df = loader(t, years)
            if df is None or len(df) < 60:
                continue
            if fires_today(entry["strategy"], df, entry.get("params")):
                fired.append(t)
        out[entry["strategy"]] = fired
    return out


def combine_signals(
    fired_by_strategy: Dict[str, List[str]],
    plan: Optional[List[Dict]] = None,
) -> Dict[str, float]:
    """Gewichtete Konviktion je Ticker: Summe der Gewichte aller aktiven
    Strategien, die den Ticker heute feuern. Sortiert (höchste zuerst).
    Das ist die additive Naht für den Bot – ein Ticker, den mehrere robuste
    Strategien gleichzeitig feuern, bekommt mehr Konviktion."""
    plan = plan if plan is not None else weight_plan()
    weights = {e["strategy"]: e["weight"] for e in plan}
    conviction: Dict[str, float] = {}
    for strat, tickers in fired_by_strategy.items():
        w = weights.get(strat, 0.0)
        for t in tickers:
            conviction[t] = round(conviction.get(t, 0.0) + w, 4)
    return dict(sorted(conviction.items(), key=lambda kv: (-kv[1], kv[0])))
