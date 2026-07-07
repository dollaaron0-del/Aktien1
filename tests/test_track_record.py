"""
Tests für scripts/track_record.py — die Statistik- und Benchmark-Bausteine des
Evidenz-Gate-Reports. Netzfrei: die Index-Reihe wird direkt in den Cache injiziert,
die Bootstrap-Funktionen sind ohnehin rein.
"""
from datetime import date

import numpy as np

from scripts.track_record import (
    _bootstrap_mean_ci,
    _summary,
    _benchmark_ticker,
    _BenchmarkCache,
    _evaluate_gates,
)


# ── Bootstrap-CI ──────────────────────────────────────────────────────────────
def test_bootstrap_ci_positive_series_excludes_zero():
    rng = np.random.default_rng(1)
    d = _bootstrap_mean_ci([2.0] * 40, rng, iters=2000)
    assert d["n"] == 40
    assert d["lo"] > 0 and d["hi"] > 0        # klar positive Reihe → CI über 0
    assert d["p_le0"] == 0.0


def test_bootstrap_ci_negative_series_p_le0_high():
    rng = np.random.default_rng(2)
    d = _bootstrap_mean_ci([-1.0, -2.0, -0.5, -3.0, -1.5, -2.5], rng, iters=2000)
    assert d["hi"] < 0
    assert d["p_le0"] == 1.0                    # nie ein positiver Bootstrap-Mittelwert


def test_bootstrap_ci_edge_cases():
    rng = np.random.default_rng(3)
    assert _bootstrap_mean_ci([], rng)["n"] == 0
    single = _bootstrap_mean_ci([1.5], rng)
    assert single["n"] == 1 and not np.isfinite(single["lo"])   # n<2 → keine CI


# ── Summary / MaxDD ───────────────────────────────────────────────────────────
def test_summary_winrate_and_drawdown():
    s = _summary([10.0, -5.0, 10.0, -5.0])
    assert s["n"] == 4 and s["wins"] == 2 and s["win_rate"] == 0.5
    assert s["max_dd"] < 0                       # es gab zwischenzeitliche Verluste
    # sequentielle Equity: 1.1*0.95*1.1*0.95 - 1
    expected = (1.10 * 0.95 * 1.10 * 0.95 - 1) * 100
    assert abs(s["compounded"] - expected) < 1e-6


def test_summary_empty():
    assert _summary([])["n"] == 0


# ── Benchmark-Zuordnung ───────────────────────────────────────────────────────
def test_benchmark_ticker_mapping():
    assert _benchmark_ticker("LLY") == "^GSPC"          # US-Default
    assert _benchmark_ticker("SAP.DE") == "^GDAXI"
    assert _benchmark_ticker("AIR.PA") == "^FCHI"
    assert _benchmark_ticker("XYZ.ZZ") == "^STOXX50E"   # unbekanntes EU-Suffix → Fallback


def test_benchmark_window_return_injected_series():
    cache = _BenchmarkCache()
    # Reihe direkt injizieren → kein data_loader/Netz nötig.
    dates = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)]
    closes = np.array([100.0, 101.0, 103.0, 106.0])
    cache._series["^GSPC"] = (dates, closes)
    # Entry am 1., 2 Tage halten → Schluss am 3.: (103/100 - 1)*100 = 3%
    r = cache.window_return("AAPL", date(2026, 6, 1), 2)
    assert abs(r - 3.0) < 1e-9
    # Datum vor Reihenbeginn → None (fail-open)
    assert cache.window_return("AAPL", date(2026, 5, 1), 1) is None


def test_benchmark_missing_index_returns_none():
    cache = _BenchmarkCache()
    cache._series["^GDAXI"] = None                # als 'nicht ladbar' markiert
    assert cache.window_return("SAP.DE", date(2026, 6, 1), 2) is None


# ── Gates ─────────────────────────────────────────────────────────────────────
def _mk_trades(n_live, n_backfill, regime="BULL"):
    t = [{"source": "live", "regime": regime} for _ in range(n_live)]
    t += [{"source": "backfill", "regime": regime} for _ in range(n_backfill)]
    return t


def test_gates_all_fail_when_no_live_and_negative_edge():
    trades = _mk_trades(0, 56)
    overall = {"lo": -3.0, "hi": -0.5, "mean": -1.9, "n": 56}
    excess = {"lo": -2.4, "hi": -0.1, "mean": -1.2, "n": 56}
    regime_stats = {"BULL": {"ci": {"lo": -3.0}, "summary": {"n": 56}}}
    gates = _evaluate_gates(trades, overall, excess, regime_stats, min_live=100)
    assert all(ok is False for _, ok, _ in gates)


def test_gates_all_pass_when_evidence_present():
    trades = _mk_trades(120, 0)
    overall = {"lo": 0.5, "hi": 2.0, "mean": 1.2, "n": 120}
    excess = {"lo": 0.3, "hi": 1.5, "mean": 0.9, "n": 120}
    regime_stats = {"BULL": {"ci": {"lo": 0.4}, "summary": {"n": 120}}}
    gates = _evaluate_gates(trades, overall, excess, regime_stats, min_live=100)
    assert all(ok is True for _, ok, _ in gates)


def test_gates_regime_gate_needs_min_n():
    trades = _mk_trades(120, 0)
    overall = {"lo": 0.5, "hi": 2.0, "mean": 1.2, "n": 120}
    excess = {"lo": 0.3, "hi": 1.5, "mean": 0.9, "n": 120}
    # Regime mit Edge>0 aber zu kleiner Stichprobe → Regime-Gate FAIL (kein qualifiziertes Regime)
    regime_stats = {"BULL": {"ci": {"lo": 0.4}, "summary": {"n": 5}}}
    gates = _evaluate_gates(trades, overall, excess, regime_stats, min_live=100)
    regime_gate = [g for g in gates if g[0].startswith("Edge je Regime")][0]
    assert regime_gate[1] is False
