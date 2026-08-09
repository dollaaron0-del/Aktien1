"""
Tests für das Anti-Overfit-Protokoll (Roadmap 6.4).

Kern-Zusagen: (1) Die Größe der Grid-Search fließt ins Verdikt ein — dieselbe
OOS-Bilanz, die bei 1 Kombo ROBUST wäre, fällt bei 60 Kombos auf FRAGILE,
wenn die Signifikanz die Šidák-Schwelle nicht überlebt. (2) holdout_years
spart den jüngsten Daten-Schwanz komplett von der Suche aus; run_holdout()
bewertet feste Parameter darauf und protokolliert jeden Zugriff.
Netzfrei, synthetische Historie per injiziertem Loader.
"""
import json

import numpy as np
import pandas as pd

from strategy_lab.anti_overfit import (BASE_ALPHA, block_bootstrap_ci,
                                       holdout_access_count,
                                       passes_multiple_testing, sidak_alpha)
from strategy_lab.walkforward import (WindowEval, _aggregate_report,
                                      run_holdout, run_walk_forward)


# ── Šidák-Mathematik ─────────────────────────────────────────────────────────
def test_sidak_alpha_basics():
    assert sidak_alpha(1) == BASE_ALPHA           # n=1: unverändert
    assert sidak_alpha(0) == BASE_ALPHA           # defensiv: min. 1 Versuch
    a24, a60, a10k = sidak_alpha(24), sidak_alpha(60), sidak_alpha(10_000)
    assert BASE_ALPHA > a24 > a60 > a10k > 0      # monoton strenger
    assert a60 < 0.001                            # 60 Kombos: ~0.00086


def test_passes_multiple_testing():
    assert passes_multiple_testing(0.0, 10_000)       # perfekte Signifikanz hält
    assert passes_multiple_testing(0.04, 1)           # n=1: Basis-Alpha reicht
    assert not passes_multiple_testing(0.04, 60)      # n=60: fällt durch


# ── Verdikt: n Kombos fließt ein ─────────────────────────────────────────────
def _windows(rets):
    # wf_eff=1 (train=test), pct_pos hoch genug — nur die Signifikanz entscheidet.
    return [WindowEval(train_start="2010-01-01", train_end="2014-01-01",
                       test_start="2014-01-01", test_end="2016-01-01",
                       best_params={"a": 1}, train_return=r, test_return=r,
                       test_sharpe=1.0, test_trades=10, test_win_rate=0.6,
                       test_max_drawdown=-0.1) for r in rets]


def test_verdict_flips_with_search_size():
    # p_le0 = 0.0075 (deterministischer Bootstrap-Seed): signifikant bei n=1,
    # nicht mehr nach Šidák für n=60.
    rets = [0.10, 0.06, -0.03, 0.08, 0.05]
    small = _aggregate_report("s", _windows(rets), n_combos=1)
    large = _aggregate_report("s", _windows(rets), n_combos=60)
    assert small.verdict == "ROBUST"
    assert large.verdict == "FRAGILE"
    assert large.n_combos_tested == 60
    assert 0 < large.alpha_adjusted < small.alpha_adjusted == BASE_ALPHA


def test_strong_edge_survives_large_search():
    # Durchweg positive Fenster (p_le0=0) bleiben auch bei großem Suchraum ROBUST.
    rep = _aggregate_report("s", _windows([0.10, 0.08, 0.12, 0.06, 0.09]),
                            n_combos=10_000)
    assert rep.verdict == "ROBUST"


def test_aggregate_report_backward_compatible():
    # Alte Aufrufform (ohne n_combos) verhält sich wie n=1.
    rep = _aggregate_report("s", _windows([0.10, 0.06, -0.03, 0.08, 0.05]))
    assert rep.verdict == "ROBUST"
    assert rep.n_combos_tested == 1 and rep.holdout_years == 0


# ── Holdout ──────────────────────────────────────────────────────────────────
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
    return _long_df(seed=sum(ord(c) for c in t) % 100)


def test_holdout_excluded_from_search():
    kw = dict(universe=["AAA", "BBB"], total_years=12, train_years=4,
              test_years=2, step_years=2, max_combos=4, loader=_loader)
    with_ho = run_walk_forward("donchian_breakout", holdout_years=2, **kw)
    without = run_walk_forward("donchian_breakout", **kw)
    assert with_ho.holdout_years == 2 and without.holdout_years == 0
    assert with_ho.n_windows >= 1
    # Kein Suchfenster ragt in den Holdout: letztes Datum der Daten ist
    # ~2016-06; Cutoff = Ende − 2J.
    data_end = _long_df().index.max()
    cut = data_end - pd.DateOffset(years=2)
    assert all(pd.Timestamp(w.test_end) < cut for w in with_ho.windows)
    # Ohne Holdout reicht mindestens ein Fenster über den Cutoff hinaus.
    assert any(pd.Timestamp(w.test_end) >= cut for w in without.windows)


def test_run_holdout_evaluates_tail_and_logs(tmp_path, monkeypatch):
    log_path = tmp_path / "holdout_access.json"
    monkeypatch.setenv("HOLDOUT_LOG_PATH", str(log_path))
    m = run_holdout("donchian_breakout", ["AAA", "BBB"],
                    params={"lookback": 40, "exit_lookback": 20},
                    holdout_years=2, total_years=12, loader=_loader,
                    note="test")
    assert m is not None  # TickerMetrics-Aggregat, auch bei 0 Trades gültig
    entries = json.loads(log_path.read_text())
    assert len(entries) == 1
    e = entries[0]
    assert e["strategy"] == "donchian_breakout" and e["note"] == "test"
    # Protokollierter Zeitraum = jüngste 2 Jahre der Daten.
    data_end = _long_df().index.max()
    assert e["holdout_end"] == str(data_end.date())
    # Zähler sieht den Zugriff; zweiter Zugriff wird angehängt.
    assert holdout_access_count() == 1
    run_holdout("donchian_breakout", ["AAA"], params={}, holdout_years=2,
                total_years=12, loader=_loader)
    assert holdout_access_count("donchian_breakout") == 2


# ── Block-Bootstrap (Roadmap 6.8d) ───────────────────────────────────────────

def test_block_bootstrap_ci_empty_and_single_value_edge_cases():
    assert block_bootstrap_ci([]) == (0.0, 0.0, 1.0)
    lo, hi, p = block_bootstrap_ci([0.03])
    assert lo == hi == 0.03 and p == 0.0
    lo, hi, p = block_bootstrap_ci([-0.01])
    assert lo == hi == -0.01 and p == 1.0


def test_block_bootstrap_ci_mean_matches_input_on_iid_data():
    rng = np.random.default_rng(1)
    values = list(rng.normal(0.01, 0.02, 500))
    lo, hi, _ = block_bootstrap_ci(values, block_size=5, iters=1000, seed=2)
    assert lo < np.mean(values) < hi


def test_block_bootstrap_ci_wider_than_iid_bootstrap_on_autocorrelated_data():
    """Positivkontrolle: bei künstlich eingebauten Gewinn-/Verlust-Serien
    (Blöcke gleichen Vorzeichens) muss die Block-CI breiter sein als die
    i.i.d.-CI auf denselben Werten — genau die 'härtere Validierung', die
    6.8d verlangt, nicht nur eine andere Zahl."""
    from strategy_lab.walkforward import _bootstrap_ci

    rng = np.random.default_rng(3)
    # 40 Blöcke à 10 Werte mit stark korreliertem Blockvorzeichen (Regime-Serien).
    blocks = []
    for _ in range(40):
        sign = rng.choice([-1, 1])
        blocks.extend(sign * rng.uniform(0.005, 0.03, 10))
    values = blocks

    iid_lo, iid_hi, _ = _bootstrap_ci(values, iters=1000)
    blk_lo, blk_hi, _ = block_bootstrap_ci(values, block_size=10, iters=1000, seed=4)
    assert (blk_hi - blk_lo) > (iid_hi - iid_lo)


def test_block_bootstrap_ci_block_size_capped_at_n():
    lo, hi, p_le0 = block_bootstrap_ci([0.01, 0.02, -0.01], block_size=100, iters=200)
    assert lo <= hi
    assert 0.0 <= p_le0 <= 1.0
