"""
Tests für den Parallel-Walk-Forward (Roadmap 6.3).

Kern-Zusage: workers>1 liefert BIT-IDENTISCHE Reports zur seriellen Schleife
(pool.map erhält die Kombo-Reihenfolge, Auswahl per striktem >). Netzfrei,
synthetische Historie per injiziertem Loader.
"""
import os
import pickle
from dataclasses import asdict

import numpy as np
import pandas as pd

from strategy_lab.strategies import get
from strategy_lab.walkforward import _resolve_workers, run_walk_forward


def _long_df(n=3000, trend=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2005-01-01", periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    openp = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _loader(t, y):
    # Modul-Ebene (picklebar wäre nicht nötig — Loader läuft nur im Parent),
    # aber deterministisch je Ticker.
    return _long_df(seed=sum(ord(c) for c in t) % 100)


# Fest gepinnt statt all_names(): andere Tests registrieren Wegwerf-Strategien
# mit Closures in der globalen REGISTRY — die müssen nicht picklebar sein.
_REAL_FAMILIES = ["baseline_swing", "donchian_breakout", "rsi_meanrev", "ts_momentum"]


def test_all_strategies_picklable():
    # Voraussetzung für multiprocessing: das ganze Strategy-Objekt (inkl.
    # signal, seit _FiresToday-Klasse statt Closure) muss durch pickle gehen.
    for name in _REAL_FAMILIES:
        s = get(name)
        assert pickle.loads(pickle.dumps(s)).name == name


def test_parallel_identical_to_serial():
    kw = dict(universe=["AAA", "BBB"], total_years=12, train_years=4,
              test_years=2, step_years=2, max_combos=6, loader=_loader)
    seriell = run_walk_forward("donchian_breakout", workers=1, **kw)
    parallel = run_walk_forward("donchian_breakout", workers=2, **kw)
    assert seriell.n_windows >= 1
    assert asdict(parallel) == asdict(seriell)


def test_resolve_workers_default_and_env(monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_WORKERS", raising=False)
    assert _resolve_workers(None) == 1          # Default: seriell
    assert _resolve_workers(3) == 3             # explizit gewinnt
    monkeypatch.setenv("STRATEGY_LAB_WORKERS", "4")
    assert _resolve_workers(None) == 4          # ENV greift bei None
    monkeypatch.setenv("STRATEGY_LAB_WORKERS", "quatsch")
    assert _resolve_workers(None) == 1          # kaputte ENV → seriell


def test_resolve_workers_auto():
    n = _resolve_workers(0)                     # 0 = auto (Kerne − 1)
    assert 1 <= n <= (os.cpu_count() or 2)
