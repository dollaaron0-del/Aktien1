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
import logging
import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtesting import data_loader
from backtesting.metrics import TickerMetrics, compute, aggregate
from strategy_lab.anti_overfit import (BASE_ALPHA, log_holdout_access,
                                       passes_multiple_testing, sidak_alpha)
from strategy_lab.regime import classify_window, regime_breakdown, robust_regimes
from strategy_lab.strategies import Strategy, get

# Mindest-Trades, damit ein Fenster-Ergebnis überhaupt zählt (sonst Rauschen).
_MIN_TRADES_TRAIN = 10
_MIN_TRADES_TEST = 3

# Bootstrap über die (wenigen) OOS-Fenster-Ergebnisse (Roadmap 4.2): macht aus
# Punkt-Verdikten Konfidenz-Verdikte. Deterministisch (fester Seed). Ein
# Strategie-Verdikt gilt nur dann als ROBUST, wenn die Kante SIGNIFIKANT positiv
# ist (Bootstrap-CI-Untergrenze > 0), nicht bloß im Mittel positiv-durch-Glück.
_BOOTSTRAP_ITERS = 2000
_BOOTSTRAP_CI = 0.90
_BOOTSTRAP_SEED = 20260712

log = logging.getLogger(__name__)


def _resolve_workers(workers: Optional[int]) -> int:
    """Parallelität der Grid-Search (Roadmap 6.3). None → ENV
    STRATEGY_LAB_WORKERS (Default 1 = seriell, exakt altes Verhalten);
    0 oder negativ → Kernzahl − 1 (ein Kern bleibt frei)."""
    if workers is None:
        try:
            workers = int(os.getenv("STRATEGY_LAB_WORKERS", "1"))
        except ValueError:
            workers = 1
    if workers <= 0:
        workers = max((os.cpu_count() or 2) - 1, 1)
    return workers


def _bootstrap_ci(values: List[float], ci: float = _BOOTSTRAP_CI,
                  iters: int = _BOOTSTRAP_ITERS,
                  seed: int = _BOOTSTRAP_SEED) -> Tuple[float, float, float]:
    """Bootstrap-CI auf den Mittelwert einer kleinen Stichprobe (numpy, kein
    scipy). Gibt (lo, hi, p_le0) zurück. n=0 → (0,0,1); n=1 → degeneriert auf
    den Einzelwert (CI-Breite 0), damit konstruierte Ein-Fenster-Läufe stabil
    bleiben."""
    x = np.asarray([v for v in values if v is not None], dtype=float)
    n = len(x)
    if n == 0:
        return (0.0, 0.0, 1.0)
    if n == 1:
        return (float(x[0]), float(x[0]), 1.0 if x[0] <= 0 else 0.0)
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, n, size=(iters, n))].mean(axis=1)
    lo = float(np.quantile(means, (1 - ci) / 2))
    hi = float(np.quantile(means, 1 - (1 - ci) / 2))
    return (lo, hi, float((means <= 0).mean()))


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
    test_max_drawdown: float = 0.0   # Roadmap 2.1: primäre Erfolgsmetrik des Exit-Labs


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
    avg_test_max_drawdown: float = 0.0     # Roadmap 2.1: Ø MaxDD über alle Testfenster
    worst_test_max_drawdown: float = 0.0   # Roadmap 2.1: schlechtestes Testfenster
    windows: List[WindowEval] = field(default_factory=list)
    regime_breakdown: Dict = field(default_factory=dict)   # je Regime: n/Median/%pos
    robust_regimes: List[str] = field(default_factory=list)
    # Roadmap 4.2: Bootstrap-Konfidenz statt Punkt-Verdikt.
    test_return_ci_lo: float = 0.0     # CI-Untergrenze der mittleren OOS-Rendite
    test_return_ci_hi: float = 0.0
    test_return_p_le0: float = 1.0     # Bootstrap-P(Ø-OOS-Kante ≤ 0)
    max_drawdown_ci_lo: float = 0.0    # CI der mittleren OOS-MaxDD (beide ≤ 0)
    max_drawdown_ci_hi: float = 0.0
    # Roadmap 6.4: Multiple-Testing-Korrektur + Holdout.
    n_combos_tested: int = 1           # Größe der Grid-Search (fließt ins Verdikt)
    alpha_adjusted: float = BASE_ALPHA  # Šidák-Schwelle, die p_le0 schlagen muss
    holdout_years: int = 0             # ausgesparter Daten-Schwanz (0 = keiner)


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
    workers: Optional[int] = None,
    holdout_years: int = 0,
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

    # Roadmap 6.4: Holdout — der jüngste Daten-Schwanz fließt NIE in die Suche.
    # Bewertung darauf nur separat + protokolliert über run_holdout().
    if holdout_years > 0:
        cut = max(df.index.max() for df in full.values()) - pd.DateOffset(years=holdout_years)
        full = {t: df.loc[df.index < cut] for t, df in full.items()}
        full = {t: df for t, df in full.items() if len(df) >= 252}
        if not full:
            return WalkForwardReport(strategy.name, 0, 0, 0, 0, 0, 0, 0, 0,
                                     "OVERFIT", holdout_years=holdout_years)

    # Gemeinsame Zeitachse (frühestes/spätestes Datum über alle Ticker).
    starts = min(df.index.min() for df in full.values())
    ends = max(df.index.max() for df in full.values())

    combos = _param_combos(strategy.param_space, max_combos)
    windows: List[WindowEval] = []

    # Parallel-Grid-Search (Roadmap 6.3): die Kombos eines Fensters sind
    # unabhängig → ProcessPool. Ergebnis ist deterministisch identisch zur
    # seriellen Schleife (pool.map erhält die Reihenfolge, Auswahl nutzt
    # strikt >, also gewinnt weiter der erste Bestwert). Fail-open: jeder
    # Pool-Fehler degradiert auf die serielle Schleife.
    n_workers = _resolve_workers(workers)
    pool: Optional[ProcessPoolExecutor] = None
    if n_workers > 1 and len(combos) > 1:
        try:
            pool = ProcessPoolExecutor(max_workers=min(n_workers, len(combos)))
        except Exception as e:                      # pragma: no cover - env-abhängig
            log.warning("Walk-Forward: ProcessPool nicht verfügbar (%s), seriell.", e)
            pool = None

    try:
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
            metrics: Optional[List[TickerMetrics]] = None
            if pool is not None:
                try:
                    metrics = list(pool.map(
                        _evaluate_dfs,
                        itertools.repeat(strategy), itertools.repeat(train_dfs), combos))
                except Exception as e:
                    log.warning("Walk-Forward: Pool-Lauf fehlgeschlagen (%s), "
                                "Rest seriell.", e)
                    pool.shutdown(wait=False, cancel_futures=True)
                    pool = None
            if metrics is None:
                metrics = [_evaluate_dfs(strategy, train_dfs, p) for p in combos]

            best, best_score, best_train_ret = None, float("-inf"), 0.0
            for params, m in zip(combos, metrics):
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
                test_max_drawdown=round(tm.max_drawdown, 4),
            ))
    finally:
        if pool is not None:
            pool.shutdown()

    return _aggregate_report(strategy.name, windows, n_combos=len(combos),
                             holdout_years=holdout_years)


def _aggregate_report(name: str, windows: List[WindowEval], n_combos: int = 1,
                      holdout_years: int = 0) -> WalkForwardReport:
    if not windows:
        return WalkForwardReport(name, 0, 0, 0, 0, 0, 0, 0, 0, "OVERFIT",
                                 n_combos_tested=max(n_combos, 1),
                                 holdout_years=holdout_years)
    import statistics as st
    test_rets = [w.test_return for w in windows]
    train_rets = [w.train_return for w in windows]
    avg_test = st.mean(test_rets)
    avg_train = st.mean(train_rets)
    pct_pos = sum(1 for r in test_rets if r > 0) / len(test_rets)
    dds = [w.test_max_drawdown for w in windows]
    avg_dd = st.mean(dds)
    worst_dd = min(dds)
    wf_eff = (avg_test / avg_train) if avg_train > 0 else 0.0
    # Parameter-Stabilität: Anteil des häufigsten Parametersatzes.
    from collections import Counter
    keyed = Counter(tuple(sorted(w.best_params.items())) for w in windows)
    stability = keyed.most_common(1)[0][1] / len(windows)

    # Bootstrap-Konfidenz über die Fenster (Roadmap 4.2).
    ret_lo, ret_hi, p_le0 = _bootstrap_ci(test_rets)
    dd_lo, dd_hi, _ = _bootstrap_ci(dds)

    # Robustheits-Verdikt (konservativ + konfidenzbewusst): ROBUST verlangt
    # eine SIGNIFIKANT positive OOS-Kante (Bootstrap-CI-Untergrenze > 0, 4.2)
    # UND — Roadmap 6.4 — dass die Signifikanz die Šidák-korrigierte Schwelle
    # für die Größe der Grid-Search überlebt: wer n Kombos testet, findet auch
    # in Rauschen "p < 0.05"; die Schwelle sinkt deshalb mit n. Bei kleinen
    # Suchräumen (n≈1) ist das Gate praktisch deckungsgleich mit 4.2.
    alpha_adj = sidak_alpha(n_combos)
    if avg_test <= 0 or pct_pos < 0.4:
        verdict = "OVERFIT"
    elif (wf_eff >= 0.5 and pct_pos >= 0.6 and ret_lo > 0
          and passes_multiple_testing(p_le0, n_combos)):
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
        verdict=verdict,
        avg_test_max_drawdown=round(avg_dd, 4),
        worst_test_max_drawdown=round(worst_dd, 4),
        windows=windows,
        regime_breakdown=breakdown,
        robust_regimes=robust_regimes(breakdown),
        test_return_ci_lo=round(ret_lo, 4),
        test_return_ci_hi=round(ret_hi, 4),
        test_return_p_le0=round(p_le0, 3),
        max_drawdown_ci_lo=round(dd_lo, 4),
        max_drawdown_ci_hi=round(dd_hi, 4),
        n_combos_tested=max(n_combos, 1),
        alpha_adjusted=round(alpha_adj, 6),
        holdout_years=holdout_years,
    )


def run_holdout(
    strategy: Strategy | str,
    universe: List[str],
    params: Dict,
    holdout_years: int = 2,
    total_years: int = 20,
    loader: Callable[[str, int], object] = data_loader.load,
    note: str = "",
) -> TickerMetrics:
    """Roadmap 6.4: bewertet FESTE Parameter (z.B. die modalen Parameter aus
    der Promotion-Registry) EINMALIG auf dem Holdout-Schwanz, den
    run_walk_forward(holdout_years=…) nie durchsucht hat. Jeder Aufruf wird
    nach data/holdout_access.json protokolliert — das Fenster nutzt sich durch
    Anfassen ab (Disziplin-Ziel: ≤1×/Quartal). Kein Grid-Search hier, bewusst:
    genau EIN Parametersatz, sonst wäre es wieder Suche."""
    if isinstance(strategy, str):
        strategy = get(strategy)
    full: Dict[str, pd.DataFrame] = {}
    for t in universe:
        df = loader(t, total_years)
        if df is not None and len(df) >= 252:
            full[t] = df
    if not full:
        return aggregate([])
    ends = max(df.index.max() for df in full.values())
    cut = ends - pd.DateOffset(years=holdout_years)
    tail: Dict[str, pd.DataFrame] = {}
    for t, df in full.items():
        sub = df.loc[df.index >= cut]
        if len(sub) >= 60:
            tail[t] = sub
    metrics = _evaluate_dfs(strategy, tail, params)
    log_holdout_access(strategy.name, str(cut.date()), str(ends.date()), note)
    return metrics
