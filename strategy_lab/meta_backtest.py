"""
Meta-Backtest des Allokators (Roadmap 4.1) – validiert weight_plan() +
Regime-Faktor SELBST, nicht die einzelnen Strategien.

Der Portfolio-Backtest (2.2) ließ die Allokator-Frage offen: die heutige
Registry hat 0 ACTIVE Strategien, weight_plan() ist leer. Hier wird die
Frage historisch beantwortet — walk-forward auf Meta-Ebene:

  An jedem Stichtag t (rollierend):
    1. Die komplette Selektions-Pipeline NUR mit Daten < t nachstellen:
       run_walk_forward() je Familie → build_registry() (in-memory, die echte
       data/strategy_registry.json bleibt unberührt).
    2. Regime zum Stichtag aus dem trailing Fenster klassifizieren
       (classify_window, gleiche Logik wie der Live-Allokator).
    3. Drei Arme bilden und OUT-OF-SAMPLE auf [t, t+oos] durch den
       Portfolio-Backtest (2.2) fahren:
         allokator         – weight_plan(registry=…)          (ohne Regime)
         allokator_regime  – weight_plan(registry=…, regime=…) (mit Regime-Faktor)
         gleichgewichtung  – equal_weight_plan(alle Familien)  (Baseline)
       Leerer Plan (0 ACTIVE bzw. kein Regime-Fit) → ehrlich flat, kein Trade.

Kein Look-Ahead: die Selektion sieht ausschließlich Kurse vor dem Stichtag
(_cutoff_loader), die OOS-Bewertung ausschließlich das Folgefenster
(_window_loader). Paired-Vergleich je Stichtag + Bootstrap-CI auf die
Differenzen (gleiche _bootstrap_ci wie das Promotion-Verdikt, Roadmap 4.2).

Rein deterministisch, kein LLM, kein Live-Eingriff; Loader injizierbar
(netzfrei testbar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from backtesting import data_loader
from strategy_lab.allocator import weight_plan
from strategy_lab.portfolio_backtest import (
    PortfolioBacktestResult, equal_weight_plan, run_portfolio_backtest,
)
from strategy_lab.promotion import build_registry
from strategy_lab.regime import classify_window
from strategy_lab.walkforward import _bootstrap_ci, run_walk_forward

ARMS = ("allokator", "allokator_regime", "gleichgewichtung")

# Trailing-Fenster für die Regime-Klassifikation am Stichtag (Jahre) —
# gleiche Größenordnung wie allocator.current_regime (lookback_years=2).
_REGIME_LOOKBACK_YEARS = 2


# ── Ergebnis-Datentypen ──────────────────────────────────────────────────────

@dataclass
class ArmResult:
    """OOS-Ergebnis eines Arms in einem Meta-Fenster."""
    plan_size: int              # Strategien im Plan (0 = bewusst flat)
    total_return: float
    max_drawdown: float
    sharpe: float
    n_trades: int


@dataclass
class MetaWindow:
    as_of: str                  # Stichtag (Selektion sah nur Daten davor)
    oos_start: str
    oos_end: str
    regime: str                 # Regime am Stichtag (trailing Fenster)
    n_active: int               # ACTIVE Strategien in der Stichtags-Registry
    verdicts: Dict[str, str]    # Strategie → Walk-Forward-Verdikt am Stichtag
    arms: Dict[str, ArmResult] = field(default_factory=dict)


@dataclass
class MetaBacktestReport:
    n_windows: int
    windows: List[MetaWindow] = field(default_factory=list)
    # je Arm: Aggregat über die Meta-Fenster
    summary: Dict[str, Dict] = field(default_factory=dict)
    # Paired-Differenzen (je Meta-Fenster) + Bootstrap-CI (90 %):
    #   "allokator_vs_gleich"  – hilft die Registry-Selektion ggü. Baseline?
    #   "regime_vs_allokator"  – bringt der Regime-Faktor zusätzlich etwas?
    diffs: Dict[str, Dict] = field(default_factory=dict)


# ── Loader-Wrapper (kausale Schnitte) ────────────────────────────────────────

def _cutoff_loader(base_loader: Callable[[str, int], object], end: pd.Timestamp):
    """Nur Vergangenheit: schneidet jeden geladenen df strikt auf < end zu.
    Die Selektion (Walk-Forward + Registry) darf den Stichtag nicht sehen."""
    def _loader(ticker: str, years: int):
        df = base_loader(ticker, years)
        if df is None:
            return None
        sub = df.loc[df.index < end]
        return sub if len(sub) >= 60 else None
    return _loader


def _window_loader(base_loader: Callable[[str, int], object],
                   start: pd.Timestamp, end: pd.Timestamp):
    """Nur das OOS-Fenster [start, end) — gleicher 'None = überspringen'-
    Vertrag wie stress_test._crisis_loader."""
    def _loader(ticker: str, years: int):
        df = base_loader(ticker, years)
        if df is None:
            return None
        sub = df.loc[(df.index >= start) & (df.index < end)]
        return sub if len(sub) >= 60 else None
    return _loader


# ── Bausteine ────────────────────────────────────────────────────────────────

def _regime_at(full: Dict[str, pd.DataFrame], as_of: pd.Timestamp) -> str:
    """Regime am Stichtag aus dem trailing Fenster (nur Vergangenheit)."""
    lo = as_of - pd.DateOffset(years=_REGIME_LOOKBACK_YEARS)
    dfs = {}
    for t, df in full.items():
        sub = df.loc[(df.index >= lo) & (df.index < as_of)]
        if len(sub) >= 60:
            dfs[t] = sub
    return classify_window(dfs)


def _flat_arm() -> ArmResult:
    return ArmResult(plan_size=0, total_return=0.0, max_drawdown=0.0,
                     sharpe=0.0, n_trades=0)


def _arm_from(plan: List[Dict], result: Optional[PortfolioBacktestResult]) -> ArmResult:
    if result is None:
        return _flat_arm()
    return ArmResult(plan_size=len(plan), total_return=result.total_return,
                     max_drawdown=result.max_drawdown, sharpe=result.sharpe,
                     n_trades=result.n_trades_taken)


def _summarize(windows: List[MetaWindow]) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    import statistics as st
    summary: Dict[str, Dict] = {}
    for arm in ARMS:
        rets = [w.arms[arm].total_return for w in windows]
        dds = [w.arms[arm].max_drawdown for w in windows]
        summary[arm] = {
            "mean_return": round(st.mean(rets), 4),
            "median_return": round(st.median(rets), 4),
            "mean_max_drawdown": round(st.mean(dds), 4),
            "worst_max_drawdown": round(min(dds), 4),
            "n_flat": sum(1 for w in windows if w.arms[arm].plan_size == 0),
            "n_trades": sum(w.arms[arm].n_trades for w in windows),
        }

    def _paired(a: str, b: str) -> Dict:
        d = [w.arms[a].total_return - w.arms[b].total_return for w in windows]
        lo, hi, p_le0 = _bootstrap_ci(d)
        import statistics as st2
        return {"mean_diff": round(st2.mean(d), 4), "ci_lo": round(lo, 4),
                "ci_hi": round(hi, 4), "p_le0": round(p_le0, 3),
                "per_window": [round(x, 4) for x in d]}

    diffs = {
        "allokator_vs_gleich": _paired("allokator", "gleichgewichtung"),
        "regime_vs_allokator": _paired("allokator_regime", "allokator"),
    }
    return summary, diffs


# ── Öffentliche API ──────────────────────────────────────────────────────────

def run_meta_backtest(
    strategies: List[str],
    universe: List[str],
    total_years: int = 20,
    selection_years: int = 8,
    oos_years: int = 2,
    step_years: int = 2,
    train_years: int = 4,
    test_years: int = 2,
    max_combos: int = 60,
    loader: Callable[[str, int], object] = data_loader.load,
    max_weight: float = 0.6,
    **portfolio_kwargs,
) -> MetaBacktestReport:
    """Rollierender Meta-Walk-Forward über den Allokator selbst.

    `selection_years` = Mindest-Historie vor dem ersten Stichtag (muss
    >= train_years + test_years sein, sonst hat der innere Walk-Forward kein
    einziges Fenster und die Registry ist konstruktionsbedingt leer).
    `portfolio_kwargs` gehen unverändert an run_portfolio_backtest()
    (initial_capital, max_positions, stock_relations, …)."""
    if selection_years < train_years + test_years:
        raise ValueError("selection_years muss >= train_years + test_years sein")

    # Volle Historie einmal laden; alle Schnitte danach sind lokal & kausal.
    full: Dict[str, pd.DataFrame] = {}
    for t in universe:
        df = loader(t, total_years)
        if df is not None and len(df) >= 252:
            full[t] = df
    if not full:
        return MetaBacktestReport(n_windows=0)

    starts = min(df.index.min() for df in full.values())
    ends = max(df.index.max() for df in full.values())
    cached_loader = lambda ticker, years: full.get(ticker)  # noqa: E731

    windows: List[MetaWindow] = []
    as_of = starts + pd.DateOffset(years=selection_years)
    oos_td = pd.DateOffset(years=oos_years)
    step_td = pd.DateOffset(years=step_years)

    while as_of + oos_td <= ends:
        sel_loader = _cutoff_loader(cached_loader, as_of)

        # 1. Selektion nachstellen: Walk-Forward je Familie NUR auf Vergangenheit.
        # Innerer Schritt = Testfensterlänge (nicht-überlappende OOS-Fenster,
        # wie in allen bisherigen Walk-Forward-Läufen) — unabhängig vom
        # Meta-Schritt `step_years`.
        reports = [run_walk_forward(
            name, list(full), total_years=total_years,
            train_years=train_years, test_years=test_years,
            step_years=test_years, max_combos=max_combos, loader=sel_loader,
        ) for name in strategies]
        registry = build_registry(reports)
        regime = _regime_at(full, as_of)

        # 2. Drei Arme; leerer Plan = ehrlich flat (weight_plan-Vertrag).
        plans = {
            "allokator": weight_plan(max_weight=max_weight, registry=registry),
            "allokator_regime": weight_plan(max_weight=max_weight,
                                            regime=regime, registry=registry),
            "gleichgewichtung": equal_weight_plan(list(strategies)),
        }

        # 3. OOS-Bewertung [as_of, as_of+oos) durch den Portfolio-Backtest.
        oos_loader = _window_loader(cached_loader, as_of, as_of + oos_td)
        arms: Dict[str, ArmResult] = {}
        for arm, plan in plans.items():
            if not plan:
                arms[arm] = _flat_arm()
                continue
            res = run_portfolio_backtest(plan, list(full), years=total_years,
                                         loader=oos_loader, **portfolio_kwargs)
            arms[arm] = _arm_from(plan, res)

        windows.append(MetaWindow(
            as_of=str(as_of.date()),
            oos_start=str(as_of.date()),
            oos_end=str((as_of + oos_td).date()),
            regime=regime,
            n_active=int(registry.get("n_active", 0)),
            verdicts={r.strategy: r.verdict for r in reports},
            arms=arms,
        ))
        as_of = as_of + step_td

    if not windows:
        return MetaBacktestReport(n_windows=0)
    summary, diffs = _summarize(windows)
    return MetaBacktestReport(n_windows=len(windows), windows=windows,
                              summary=summary, diffs=diffs)
