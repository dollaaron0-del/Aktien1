"""
Walk-Forward-Selektion (Roadmap Phase 3) – das Anti-Overfit-Herz.

Für jede Strategie-Familie:
  1. Historie in rollende Train/Test-Fenster teilen (z.B. Train 4J | Test 2J, Schritt 2J).
  2. Auf dem TRAIN-Fenster den param_space grid-searchen → beste Parameter (in-sample).
  3. Diese Parameter auf dem darauffolgenden TEST-Fenster bewerten (OUT-OF-SAMPLE).
  4. Über alle Fenster nach ROBUSTHEIT ranken – nicht nach Bestwert:
     Median/Worst-OOS-Return, %-positive-Fenster, Walk-Forward-Effizienz
     (avg_test/avg_train) und Parameter-Stabilität.

Kein Look-Ahead: Optimierung NUR auf Train, Test bleibt bis zur Bewertung unberührt.
Reine numpy/pandas-Analyse, kein LLM, kein Live-Eingriff.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from backtesting import data_loader
from backtesting.metrics import TickerMetrics, compute, aggregate
from strategy_lab.regime import classify_window, regime_breakdown, robust_regimes
from strategy_lab.strategies import Strategy, get

# Mindest-Trades, damit ein Fenster-Ergebnis überhaupt zählt (sonst Rauschen).
_MIN_TRADES_TRAIN = 10
_MIN_TRADES_TEST = 3


@dataclass
class WindowEval:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: Dict
    train_return: float
    test_return: float
    test_sharpe: float
    test_trades: int
    test_win_rate: float
    regime: str = ""           # Markt-Regime des Testfensters (Phase 4-Rest)


@dataclass
class WalkForwardReport:
    strategy: str
    n_windows: int
    median_test_return: float
    worst_test_return: float
    pct_positive_windows: float
    avg_train_return: float
    avg_test_return: float
    wf_efficiency: float        # avg_test / avg_train (≈1 gut, ≪1 = overfit)
    param_stability: float      # Anteil Fenster mit dem häufigsten Parametersatz
    verdict: str                # ROBUST | FRAGILE | OVERFIT
    windows: List[WindowEval] = field(default_factory=list)
    regime_breakdown: Dict = field(default_factory=dict)   # je Regime: n/Median/%pos
    robust_regimes: List[str] = field(default_factory=list)


# ── Hilfen ──────────────────────────────────────────────────────────────────
def _param_combos(space: Dict[str, List], max_combos: int, seed: int = 0) -> List[Dict]:
    if not space:
        return [{}]
    keys = list(space)
    all_combos = [dict(zip(keys, vals)) for vals in itertools.product(*[space[k] for k in keys])]
    if len(all_combos) > max_combos:
        random.Random(seed).shuffle(all_combos)
        all_combos = all_combos[:max_combos]
    return all_combos


def _evaluate_dfs(strategy: Strategy, dfs: Dict[str, pd.DataFrame], params: Dict) -> TickerMetrics:
    """Bewertet die Strategie auf vorgeschnittenen DataFrames → Portfolio-Aggregat."""
    per: List[TickerMetrics] = []
    for ticker, df in dfs.items():
        if df is None or len(df) < 60:
            continue
        trades = strategy.runner(df, ticker, params)
        per.append(compute(trades, ticker, years=max(len(df) / 252.0, 0.1)))
    return aggregate(per)


def _slice(dfs: Dict[str, pd.DataFrame], start, end) -> Dict[str, pd.DataFrame]:
    out = {}
    for t, df in dfs.items():
        if df is None:
            continue
        sub = df.loc[start:end]
        if len(sub) >= 60:
            out[t] = sub
    return out


def run_walk_forward(
    strategy: Strategy | str,
    universe: List[str],
    total_years: int = 20,
    train_years: int = 4,
    test_years: int = 2,
    step_years: int = 2,
    max_combos: int = 60,
    loader: Callable[[str, int], object] = data_loader.load,
) -> WalkForwardReport:
    if isinstance(strategy, str):
        strategy = get(strategy)

    # Volle Historie einmal laden, dann pro Fenster slicen.
    full: Dict[str, pd.DataFrame] = {}
    for t in universe:
        df = loader(t, total_years)
        if df is not None and len(df) >= 252:
            full[t] = df
    if not full:
        return WalkForwardReport(strategy.name, 0, 0, 0, 0, 0, 0, 0, 0, "OVERFIT")

    # Gemeinsame Zeitachse (frühestes/spätestes Datum über alle Ticker).
    starts = min(df.index.min() for df in full.values())
    ends = max(df.index.max() for df in full.values())

    combos = _param_combos(strategy.param_space, max_combos)
    windows: List[WindowEval] = []

    cur = starts
    train_td = pd.DateOffset(years=train_years)
    test_td = pd.DateOffset(years=test_years)
    step_td = pd.DateOffset(years=step_years)
    while cur + train_td + test_td <= ends:
        tr_s, tr_e = cur, cur + train_td
        te_s, te_e = tr_e, tr_e + test_td
        train_dfs = _slice(full, tr_s, tr_e)
        test_dfs = _slice(full, te_s, te_e)
        cur = cur + step_td
        if not train_dfs or not test_dfs:
            continue

        # Grid-Search auf TRAIN: bester Parametersatz nach total_return (mit Mindest-Trades).
        best, best_score, best_train_ret = None, float("-inf"), 0.0
        for params in combos:
            m = _evaluate_dfs(strategy, train_dfs, params)
            score = m.total_return if m.n_trades >= _MIN_TRADES_TRAIN else float("-inf")
            if score > best_score:
                best, best_score, best_train_ret = params, score, m.total_return
        if best is None:
            continue

        # OOS-Bewertung auf TEST mit den auf Train gewählten Parametern.
        tm = _evaluate_dfs(strategy, test_dfs, best)
        windows.append(WindowEval(
            train_start=str(tr_s.date()), train_end=str(tr_e.date()),
            test_start=str(te_s.date()), test_end=str(te_e.date()),
            best_params=best, train_return=round(best_train_ret, 4),
            test_return=round(tm.total_return, 4), test_sharpe=round(tm.sharpe, 3),
            test_trades=tm.n_trades, test_win_rate=round(tm.win_rate, 4),
            regime=classify_window(test_dfs),
        ))

    return _aggregate_report(strategy.name, windows)


def _aggregate_report(name: str, windows: List[WindowEval]) -> WalkForwardReport:
    if not windows:
        return WalkForwardReport(name, 0, 0, 0, 0, 0, 0, 0, 0, "OVERFIT")
    import statistics as st
    test_rets = [w.test_return for w in windows]
    train_rets = [w.train_return for w in windows]
    avg_test = st.mean(test_rets)
    avg_train = st.mean(train_rets)
    pct_pos = sum(1 for r in test_rets if r > 0) / len(test_rets)
    wf_eff = (avg_test / avg_train) if avg_train > 0 else 0.0
    # Parameter-Stabilität: Anteil des häufigsten Parametersatzes.
    from collections import Counter
    keyed = Counter(tuple(sorted(w.best_params.items())) for w in windows)
    stability = keyed.most_common(1)[0][1] / len(windows)

    # Robustheits-Verdikt (konservativ).
    if avg_test <= 0 or pct_pos < 0.4:
        verdict = "OVERFIT"
    elif wf_eff >= 0.5 and pct_pos >= 0.6:
        verdict = "ROBUST"
    else:
        verdict = "FRAGILE"

    breakdown = regime_breakdown(windows)
    return WalkForwardReport(
        strategy=name, n_windows=len(windows),
        median_test_return=round(st.median(test_rets), 4),
        worst_test_return=round(min(test_rets), 4),
        pct_positive_windows=round(pct_pos, 3),
        avg_train_return=round(avg_train, 4),
        avg_test_return=round(avg_test, 4),
        wf_efficiency=round(wf_eff, 3),
        param_stability=round(stability, 3),
        verdict=verdict, windows=windows,
        regime_breakdown=breakdown,
        robust_regimes=robust_regimes(breakdown),
    )
