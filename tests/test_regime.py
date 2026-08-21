"""
Tests für strategy_lab.regime (Roadmap Phase 4-Rest – Regime-Tagging).

Netzfrei: synthetische Kursfenster mit gesteuertem Trend/Vola, plus
konstruierte WindowEvals für die Aufschlüsselung.
"""
import numpy as np
import pandas as pd

from strategy_lab.regime import (
    apply_hysteresis, classify_window, count_transitions,
    regime_breakdown, robust_regimes, track_regime,
)
from strategy_lab.walkforward import WindowEval


def _df(n=300, drift=0.0, vol=0.008, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


def test_classify_bull_calm():
    # kräftige Drift (~+30%/Jahr trotz Vola-Drag), niedrige Vola → BULL_CALM
    dfs = {"A": _df(drift=0.0015, vol=0.006, seed=1),
           "B": _df(drift=0.0015, vol=0.006, seed=2)}
    assert classify_window(dfs) == "BULL_CALM"


def test_classify_bear_volatile():
    dfs = {"A": _df(drift=-0.0015, vol=0.03, seed=3)}
    label = classify_window(dfs)
    assert label == "BEAR_VOLATILE"


def test_classify_sideways():
    # Drift 0, langes Fenster + mehrere Ticker → realisierter Return nahe 0
    # (mittelt Seed-Glück weg) → weder BULL noch BEAR.
    dfs = {t: _df(n=750, drift=0.0, vol=0.007, seed=s)
           for t, s in zip("ABCD", range(10, 14))}
    assert classify_window(dfs).startswith("SIDE_")


def _df_from_rets(rets, start="2010-01-01"):
    close = 100 * np.cumprod(1 + np.asarray(rets))
    idx = pd.date_range(start, periods=len(rets), freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


def test_crash_recovery_window_not_bull_calm():
    # Die 2020–22-Falle: rauf, tiefer Crash, Erholung zu netto-POSITIV.
    # Start-zu-Ende ist positiv, aber zeitlich crashdurchzogen → darf kein
    # sauberer BULL_CALM sein (geschärfte Klassifikation). Deterministischer
    # Pfad mit leichtem Rauschen, damit der Net-Gewinn garantiert ist.
    rng = np.random.default_rng(7)
    path = np.concatenate([
        np.linspace(100, 135, 200),   # ruhiger Aufschwung
        np.linspace(135, 85, 50),     # harter Crash (~-37% vom Hoch)
        np.linspace(85, 150, 300),    # Erholung über das alte Hoch → netto +50%
    ])
    close = path * (1 + rng.normal(0, 0.003, len(path)))  # leichtes Rauschen
    df = pd.DataFrame({"Close": close},
                      index=pd.date_range("2010-01-01", periods=len(close), freq="B"))
    net = df["Close"].iloc[-1] / df["Close"].iloc[0] - 1
    label = classify_window({"A": df})
    assert net > 0                              # netto positiv …
    assert label != "BULL_CALM"                 # … aber NICHT als ruhiger Bulle gelabelt
    assert "VOLATILE" in label or label.startswith("SIDE") or label.startswith("BEAR")


def test_classify_unknown_on_thin_data():
    assert classify_window({}) == "UNKNOWN"
    assert classify_window({"A": _df(n=5)}) == "UNKNOWN"


def test_regime_breakdown_and_robust():
    def _w(regime, ret):
        return WindowEval("", "", "", "", {}, 1.0, ret, 0.0, 5, 0.5, regime=regime)
    windows = [
        _w("BULL_CALM", 0.3), _w("BULL_CALM", 0.1),     # 2× positiv
        _w("BEAR_VOLATILE", -0.2),                       # 1× negativ
        _w("SIDE_CALM", 0.05), _w("SIDE_CALM", -0.1),    # gemischt, Median < 0
    ]
    bd = regime_breakdown(windows)
    assert bd["BULL_CALM"]["n"] == 2
    assert bd["BULL_CALM"]["pct_positive"] == 1.0
    assert bd["BEAR_VOLATILE"]["median_test_return"] == -0.2

    rr = robust_regimes(bd, min_n=2)
    assert "BULL_CALM" in rr                  # 2 Fenster, Median > 0
    assert "BEAR_VOLATILE" not in rr          # nur 1 Fenster (< min_n)
    assert "SIDE_CALM" not in rr              # Median < 0


def test_breakdown_empty_regime_defaults_unknown():
    w = WindowEval("", "", "", "", {}, 1.0, 0.2, 0.0, 5, 0.5)  # regime=""
    bd = regime_breakdown([w])
    assert "UNKNOWN" in bd


# ── Regime-Übergangsmodell (Roadmap 4.3) ────────────────────────────────────

def _long_trend_df(n=1400, drift=0.0008, vol=0.01, seed=0, start="2010-01-01"):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


def _loader_factory(dfs: dict):
    def loader(ticker, years):
        return dfs.get(ticker)
    return loader


def test_track_regime_point_in_time_no_lookahead():
    df = _long_trend_df(n=1000, drift=0.0012, vol=0.006, seed=1)
    loader = _loader_factory({"AAA": df})
    start = df.index[600]
    end = df.index[650]
    series_before = track_regime(["AAA"], loader, start=start, end=end,
                                 lookback_years=2, step_days=10)

    mutated = df.copy()
    mutated.loc[mutated.index > end, "Close"] *= 10.0     # Zukunft massiv verändert
    loader2 = _loader_factory({"AAA": mutated})
    series_after = track_regime(["AAA"], loader2, start=start, end=end,
                                lookback_years=2, step_days=10)
    assert series_before == series_after


def test_track_regime_only_uses_trailing_window():
    # Erste Hälfte Bärenmarkt, zweite Hälfte Bullenmarkt — ein Stichtag kurz
    # NACH dem Wechsel darf noch nicht "BULL" zeigen (Trailing-Fenster ist
    # noch überwiegend Bär), ein Stichtag deutlich SPÄTER schon.
    bear = np.linspace(150, 80, 500)
    bull = np.linspace(80, 200, 500)
    close = np.concatenate([bear, bull])
    idx = pd.date_range("2010-01-01", periods=len(close), freq="B")
    df = pd.DataFrame({"Close": close}, index=idx)
    loader = _loader_factory({"AAA": df})

    just_after = idx[520]     # ~20 Handelstage nach der Wende
    much_later = idx[950]     # ~450 Handelstage nach der Wende
    series = track_regime(["AAA"], loader, start=just_after, end=much_later,
                          lookback_years=2, step_days=50)
    assert series[0]["regime"].startswith(("BEAR", "SIDE"))    # kurz nach der Wende
    assert series[-1]["regime"].startswith("BULL")             # deutlich später


def test_track_regime_empty_universe():
    assert track_regime([], _loader_factory({}), start="2020-01-01") == []


def test_track_regime_no_data_for_any_ticker():
    loader = _loader_factory({})
    assert track_regime(["ZZZ"], loader, start="2020-01-01") == []


def test_apply_hysteresis_empty_is_empty():
    assert apply_hysteresis([]) == []


def test_apply_hysteresis_filters_short_blips():
    seq = [{"regime": r} for r in
          ["BULL_CALM", "BULL_CALM", "SIDE_CALM", "BULL_CALM", "BULL_CALM",
           "BEAR_CALM", "BEAR_CALM", "BEAR_CALM", "BEAR_CALM"]]
    out = apply_hysteresis(seq, min_confirm=3)
    smoothed = [row["regime"] for row in out]
    # Der einzelne SIDE_CALM-Ausreißer (nur 1× in Folge) darf nicht durchschlagen —
    # das bestätigte Label bleibt BULL_CALM, bis BEAR_CALM 3× in Folge auftritt
    # (ab Index 5; bestätigt ab Index 7).
    assert smoothed == ["BULL_CALM"] * 7 + ["BEAR_CALM"] * 2
    assert "SIDE_CALM" not in smoothed
    # regime_raw bleibt unverändert das Original.
    assert [row["regime_raw"] for row in out] == [r["regime"] for r in seq]


def test_apply_hysteresis_confirms_sustained_change():
    seq = [{"regime": "BULL_CALM"}] * 3 + [{"regime": "BEAR_CALM"}] * 5
    out = apply_hysteresis(seq, min_confirm=3)
    smoothed = [row["regime"] for row in out]
    # Nach 3 aufeinanderfolgenden BEAR-Messungen (Index 3,4,5) wechselt das
    # bestätigte Label ab Index 5 (0-basiert) auf BEAR_CALM.
    assert smoothed[:5] == ["BULL_CALM"] * 5
    assert smoothed[5:] == ["BEAR_CALM"] * 3


def test_apply_hysteresis_min_confirm_1_is_noop():
    seq = [{"regime": r} for r in ["BULL_CALM", "SIDE_CALM", "BULL_CALM", "BEAR_CALM"]]
    out = apply_hysteresis(seq, min_confirm=1)
    assert [row["regime"] for row in out] == [r["regime"] for r in seq]


def test_hysteresis_reduces_or_equals_transitions_never_increases():
    rng = np.random.default_rng(42)
    labels = ["BULL_CALM", "SIDE_CALM", "BEAR_CALM"]
    seq = [{"regime": labels[i]} for i in rng.integers(0, 3, 200)]
    raw_trans = count_transitions(seq)
    for mc in (2, 3, 5, 8):
        smoothed = apply_hysteresis(seq, min_confirm=mc)
        assert count_transitions(smoothed) <= raw_trans


def test_count_transitions_basic():
    seq = [{"regime": "A"}, {"regime": "A"}, {"regime": "B"}, {"regime": "B"}, {"regime": "A"}]
    assert count_transitions(seq) == 2
    assert count_transitions([]) == 0
    assert count_transitions([{"regime": "A"}]) == 0
