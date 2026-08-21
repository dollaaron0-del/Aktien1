"""
Combinatorial Purged Cross-Validation (Roadmap 6.4c) — zweite Validierungs-
Achse NEBEN dem sequenziellen Walk-Forward (walkforward.py), nicht dessen
Ersatz.

WARUM EINE ZWEITE ACHSE: Walk-Forward testet nur EINE Reihenfolge von
Train/Test-Fenstern (immer vorwärts in der Zeit). Ein Verdikt, das nur auf
dieser einen Abfolge beruht, kann Glück/Pech der konkreten Fenstergrenzen
sein. CPCV (Bailey/López de Prado) prüft dieselbe Strategie stattdessen über
VIELE verschiedene Kombinationen, welcher Zeitblock als Test dient — robuster
gegen genau diesen Zufall.

VEREINFACHUNG GEGENÜBER DEM ORIGINALPAPIER (ehrlich benannt, wie schon bei
Šidák-statt-DSR in anti_overfit.py): das Original rekombiniert die k-von-N-
Testblöcke zu mehreren vollständigen "Backtest-Pfaden" (Path Reconstruction).
Hier wird stattdessen – konsistent mit dem bestehenden Walk-Forward-
Aggregat – jede Testblock-Kombination als EIGENES unabhängiges OOS-Ergebnis
behandelt und über walkforward._aggregate_report() genauso ausgewertet wie
Walk-Forward-Fenster (Bootstrap-CI, Šidák-Gate, ROBUST/FRAGILE/OVERFIT).
Das ist statistisch konservativer (keine Pfad-Rekonstruktion, die Korrelation
zwischen Pfaden verschleiern könnte), nicht großzügiger.

PURGING + EMBARGO (der eigentliche Kern von CPCV, ohne den ein Block-CV
Information zwischen Train und Test lecken lässt):
  - PURGE: Trainingsdaten, die direkt VOR einem Testblock enden, werden um
    `purge_days` verkürzt – ein Signal von dort könnte sonst einen Trade
    eröffnen, der noch in den Testblock hineinläuft (max_hold_days bis zu
    60 Tage in den bestehenden Strategien).
  - EMBARGO: Trainingsdaten, die direkt NACH einem Testblock beginnen,
    werden um `embargo_days` verzögert – gegen serielle Korrelation, die
    sonst Information aus dem Test in unmittelbar folgende Trainingsdaten
    durchsickern lässt.
  Beides wird NICHT durch Herausschneiden von Zeilen aus der Mitte einer
  Preis-Serie umgesetzt (das würde Indikatoren an der Nahtstelle verfälschen
  – TechnicalIndicators braucht kontinuierliche Bars), sondern durch
  Verkürzen der Segment-RÄNDER, exakt wie es Walk-Forward mit seinen
  Fenstergrenzen ohnehin schon macht.

Reine numpy/pandas-Analyse, kein LLM, kein Live-Eingriff. Nutzt bewusst
dieselbe Grid-Search-, Aggregations- und Parallelisierungs-Maschinerie wie
walkforward.py (6.3-Worker) statt sie zu duplizieren.
"""
from __future__ import annotations

import itertools
import logging
import random
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from backtesting import data_loader
from backtesting.metrics import TickerMetrics, aggregate, compute
from strategy_lab.regime import classify_window
from strategy_lab.strategies import Strategy, get
from strategy_lab.walkforward import (_MIN_TRADES_TRAIN, WalkForwardReport,
                                      WindowEval, _aggregate_report,
                                      _param_combos, _resolve_workers)

log = logging.getLogger(__name__)

Block = Tuple[pd.Timestamp, pd.Timestamp]


def make_blocks(start, end, n_blocks: int) -> List[Block]:
    """Teilt [start, end] in n_blocks gleich lange, lückenlos anschließende
    Zeitblöcke (kalendarisch, wie walkforward.py's DateOffset-Fenster)."""
    if n_blocks < 2:
        raise ValueError("n_blocks muss >= 2 sein")
    edges = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), periods=n_blocks + 1)
    return [(edges[i], edges[i + 1]) for i in range(n_blocks)]


def path_combos(n_blocks: int, test_blocks: int, max_paths: int,
                seed: int = 0) -> List[Tuple[int, ...]]:
    """Alle C(n_blocks, test_blocks)-Kombinationen, deterministisch auf
    max_paths gekappt (gleiches Muster wie walkforward._param_combos)."""
    all_combos = list(itertools.combinations(range(n_blocks), test_blocks))
    if len(all_combos) > max_paths:
        random.Random(seed).shuffle(all_combos)
        all_combos = all_combos[:max_paths]
    return all_combos


def _merge_contiguous(blocks: List[Block], indices: set) -> List[Block]:
    """Merged aufeinanderfolgende Block-Indizes zu je EINEM zusammenhängenden
    Segment. Notwendig, weil `make_blocks()` Blöcke bewusst lückenlos
    aneinanderstoßen lässt (`blocks[i][1] == blocks[i+1][0]`, für die
    Train-Segment-Logik unten gewollt) — würden zwei angrenzende Testblöcke
    stattdessen UNABHÄNGIG voneinander gesliced (je `_slice_segments`-Aufruf
    inklusive beider Enden), tauchte ihre gemeinsame Grenze doppelt auf und
    ließ `pd.concat` später mit "duplicate labels" abstürzen (gefunden 17.7.
    im ersten echten Mehrkern-CPCV-Lauf, test_blocks=2)."""
    ordered = sorted(indices)
    out: List[Block] = []
    run_start: Optional[int] = None
    prev: Optional[int] = None
    for i in ordered:
        if prev is None or i != prev + 1:
            if run_start is not None:
                out.append((blocks[run_start][0], blocks[prev][1]))
            run_start = i
        prev = i
    if run_start is not None:
        out.append((blocks[run_start][0], blocks[prev][1]))
    return out


def _train_segments(blocks: List[Block], test_idx: set,
                    purge: pd.Timedelta, embargo: pd.Timedelta) -> List[Block]:
    """Zusammenhängende Nicht-Test-Blöcke zu Segmenten gruppiert; an jeder
    Kante, die direkt an einen Testblock grenzt, um Purge (Ende, Richtung
    Testblock rechts) bzw. Embargo (Anfang, Richtung Testblock links)
    verkürzt. Rand der Gesamt-Historie (kein Nachbar) bleibt unangetastet."""
    n = len(blocks)
    non_test = [i for i in range(n) if i not in test_idx]
    runs: List[List[int]] = []
    cur: List[int] = []
    for i in non_test:
        if cur and i != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)

    out: List[Block] = []
    for run in runs:
        first, last = run[0], run[-1]
        seg_start, seg_end = blocks[first][0], blocks[last][1]
        if first - 1 >= 0 and (first - 1) in test_idx:
            seg_start = seg_start + embargo         # Segment startet direkt nach Test
        if last + 1 < n and (last + 1) in test_idx:
            seg_end = seg_end - purge                # Segment endet direkt vor Test
        if seg_end > seg_start:
            out.append((seg_start, seg_end))
    return out


def _slice_segments(full: Dict[str, pd.DataFrame],
                    segments: List[Block]) -> Dict[str, List[pd.DataFrame]]:
    """Pro Ticker je Segment ein kontinuierlicher Chunk (>=60 Bars), sonst
    übersprungen. Mehrere Chunks je Ticker sind erlaubt (mehrere Segmente)."""
    out: Dict[str, List[pd.DataFrame]] = {}
    for t, df in full.items():
        chunks = [sub for s, e in segments
                 if len(sub := df.loc[s:e]) >= 60]
        if chunks:
            out[t] = chunks
    return out


def _fmt_segments(segments: List[Block]) -> str:
    return "+".join(f"{s.date()}..{e.date()}" for s, e in segments) or "∅"


def _evaluate_multi(strategy: Strategy, chunks_by_ticker: Dict[str, List[pd.DataFrame]],
                    params: Dict) -> TickerMetrics:
    """Wie walkforward._evaluate_dfs, aber je Ticker über mehrere (durch
    Purge/Embargo getrennte) Chunks statt einem einzigen DataFrame."""
    per: List[TickerMetrics] = []
    for ticker, chunks in chunks_by_ticker.items():
        for df in chunks:
            trades = strategy.runner(df, ticker, params)
            per.append(compute(trades, ticker, years=max(len(df) / 252.0, 0.1)))
    return aggregate(per)


def run_cpcv(
    strategy: Strategy | str,
    universe: List[str],
    total_years: int = 20,
    n_blocks: int = 6,
    test_blocks: int = 1,
    purge_days: int = 10,
    embargo_days: int = 5,
    max_combos: int = 60,
    max_paths: int = 30,
    loader: Callable[[str, int], object] = data_loader.load,
    workers: Optional[int] = None,
) -> WalkForwardReport:
    if isinstance(strategy, str):
        strategy = get(strategy)
    if n_blocks < 3:
        raise ValueError("n_blocks muss >= 3 sein (sonst bleibt nach Purge/Embargo kein Train)")
    if not (1 <= test_blocks < n_blocks):
        raise ValueError("test_blocks muss zwischen 1 und n_blocks-1 liegen")

    full: Dict[str, pd.DataFrame] = {}
    for t in universe:
        df = loader(t, total_years)
        if df is not None and len(df) >= 252:
            full[t] = df
    if not full:
        return WalkForwardReport(strategy.name, 0, 0, 0, 0, 0, 0, 0, 0, "OVERFIT")

    starts = min(df.index.min() for df in full.values())
    ends = max(df.index.max() for df in full.values())
    blocks = make_blocks(starts, ends, n_blocks)
    purge = pd.Timedelta(days=purge_days)
    embargo = pd.Timedelta(days=embargo_days)

    combos = _param_combos(strategy.param_space, max_combos)
    paths = path_combos(n_blocks, test_blocks, max_paths)

    # Parallel-Grid-Search wie walkforward.py (6.3): ein Pool über alle Pfade
    # hinweg, Fail-open auf seriell bei jedem Pool-Fehler.
    n_workers = _resolve_workers(workers)
    pool: Optional[ProcessPoolExecutor] = None
    if n_workers > 1 and len(combos) > 1:
        try:
            pool = ProcessPoolExecutor(max_workers=min(n_workers, len(combos)))
        except Exception as e:                      # pragma: no cover - env-abhängig
            log.warning("CPCV: ProcessPool nicht verfügbar (%s), seriell.", e)
            pool = None

    windows: List[WindowEval] = []
    try:
        for test_idx in paths:
            test_idx_set = set(test_idx)
            test_segments = _merge_contiguous(blocks, test_idx_set)
            train_segments = _train_segments(blocks, test_idx_set, purge, embargo)
            if not train_segments or not test_segments:
                continue
            train_chunks = _slice_segments(full, train_segments)
            test_chunks = _slice_segments(full, test_segments)
            if not train_chunks or not test_chunks:
                continue

            metrics: Optional[List[TickerMetrics]] = None
            if pool is not None:
                try:
                    metrics = list(pool.map(
                        _evaluate_multi,
                        itertools.repeat(strategy), itertools.repeat(train_chunks), combos))
                except Exception as e:
                    log.warning("CPCV: Pool-Lauf fehlgeschlagen (%s), Rest seriell.", e)
                    pool.shutdown(wait=False, cancel_futures=True)
                    pool = None
            if metrics is None:
                metrics = [_evaluate_multi(strategy, train_chunks, p) for p in combos]

            best, best_score, best_train_ret = None, float("-inf"), 0.0
            for params, m in zip(combos, metrics):
                score = m.total_return if m.n_trades >= _MIN_TRADES_TRAIN else float("-inf")
                if score > best_score:
                    best, best_score, best_train_ret = params, score, m.total_return
            if best is None:
                continue

            tm = _evaluate_multi(strategy, test_chunks, best)
            test_concat = {t: pd.concat(chunks) for t, chunks in test_chunks.items() if chunks}
            windows.append(WindowEval(
                train_start=_fmt_segments(train_segments), train_end="",
                test_start=_fmt_segments(test_segments), test_end="",
                best_params=best, train_return=round(best_train_ret, 4),
                test_return=round(tm.total_return, 4), test_sharpe=round(tm.sharpe, 3),
                test_trades=tm.n_trades, test_win_rate=round(tm.win_rate, 4),
                regime=classify_window(test_concat),
                test_max_drawdown=round(tm.max_drawdown, 4),
            ))
    finally:
        if pool is not None:
            pool.shutdown()

    return _aggregate_report(strategy.name, windows, n_combos=len(combos))
