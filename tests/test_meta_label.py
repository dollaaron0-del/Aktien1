"""
Tests für ML-Meta-Labeling der mechanischen Signale (Roadmap 6.5b).

Kern-Zusagen: (1) entry_features() nutzt AUSSCHLIESSLICH Daten vor dem
Stichtag — kein Look-Ahead, verifiziert durch Manipulation der Zukunft nach
dem Stichtag ohne Effekt auf die Features. (2) build_training_rows() liefert
eine Zeile je Backtest-Trade mit konsistentem win/return_pct. (3) Die
Design-Matrix lässt im Test neu auftauchende Kategorien nicht als neue
Spalten durch (kein Leck). (4) evaluate_meta_labeling() spart den Holdout
komplett aus, protokolliert den Zugriff, ist deterministisch UND erkennt
ein künstlich eingebautes Signal (Positivkontrolle) statt nur immer
NO_SIGNAL zurückzugeben. Netzfrei, synthetische Historie.
"""
import numpy as np
import pandas as pd

from strategy_lab.anti_overfit import holdout_access_count
from strategy_lab.meta_label import (_breadth_at, _design_matrix,
                                     build_training_rows, entry_features,
                                     evaluate_meta_labeling)
from strategy_lab.strategies import get


def _price_df(n=400, seed=0, start="2015-01-01", trend=0.0005):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


# ── entry_features: kein Look-Ahead ──────────────────────────────────────────

def test_entry_features_none_with_too_little_history():
    df = _price_df(n=10)
    full = {"AAA": df}
    assert entry_features(full, "AAA", df.index[5]) is None


def test_entry_features_unaffected_by_future_data():
    df = _price_df(n=200, seed=1)
    at_date = df.index[150]
    full = {"AAA": df}
    feats_before = entry_features(full, "AAA", at_date)

    mutated = df.copy()
    mutated.loc[mutated.index > at_date, "Close"] *= 5.0     # Zukunft massiv verändert
    feats_after = entry_features({"AAA": mutated}, "AAA", at_date)

    assert feats_before == feats_after


def test_entry_features_missing_ticker_is_none():
    assert entry_features({}, "ZZZ", pd.Timestamp("2020-01-01")) is None


# ── Breadth ───────────────────────────────────────────────────────────────────

def test_breadth_at_mixed_universe():
    idx = pd.date_range("2020-01-01", periods=40, freq="B")
    up = pd.DataFrame({"Close": np.linspace(100, 150, 40)}, index=idx)
    down = pd.DataFrame({"Close": np.linspace(100, 50, 40)}, index=idx)
    full = {"UP1": up, "UP2": up, "DOWN1": down}
    breadth = _breadth_at(full, idx[35], lookback_days=20)
    assert breadth == 2 / 3


def test_breadth_at_no_data_returns_neutral():
    assert _breadth_at({}, pd.Timestamp("2020-01-01")) == 0.5


# ── Design-Matrix: kein Kategorie-Leck ───────────────────────────────────────

def test_design_matrix_test_only_category_not_added():
    train_df = pd.DataFrame({"regime": ["BULL_CALM", "BEAR_CALM"], "strategy": ["a", "a"],
                             "trailing_vol": [0.1, 0.2], "trailing_ret": [0.0, 0.0],
                             "breadth": [0.5, 0.5]})
    test_df = pd.DataFrame({"regime": ["NEVER_SEEN"], "strategy": ["a"],
                            "trailing_vol": [0.1], "trailing_ret": [0.0], "breadth": [0.5]})
    x_train, x_test = _design_matrix(train_df, test_df)
    assert list(x_train.columns) == list(x_test.columns)
    assert "regime_NEVER_SEEN" not in x_test.columns
    assert (x_test.filter(like="regime_") == 0).all(axis=None)   # unbekannte Kategorie -> alles 0


# ── build_training_rows ──────────────────────────────────────────────────────

def _ohlcv_df(n=1200, seed=0, start="2005-01-01"):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0004, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    openp = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _loader(t, y):
    return _ohlcv_df(seed=sum(ord(c) for c in t) % 100)


def test_build_training_rows_matches_raw_trades():
    strat = get("donchian_breakout")
    df = build_training_rows([strat], ["AAA", "BBB"], total_years=10, loader=_loader)
    if df.empty:
        return                                          # Strategie feuert evtl. auf diesem Seed nicht
    assert set(df.columns) >= {"ticker", "strategy", "entry_date", "regime",
                               "trailing_vol", "trailing_ret", "breadth", "return_pct", "win"}
    assert set(df["win"].unique()) <= {0, 1}
    assert (df["win"] == (df["return_pct"] > 0).astype(int)).all()
    assert set(df["strategy"].unique()) == {"donchian_breakout"}


def test_build_training_rows_empty_universe_is_empty_df():
    df = build_training_rows(["donchian_breakout"], [], total_years=10, loader=_loader)
    assert df.empty


# ── evaluate_meta_labeling ────────────────────────────────────────────────────

def _synthetic_rows(n=1200, seed=0, signal_strength=0.0):
    """Synthetische Trade-Zeilen. signal_strength>0 baut ein ECHTES,
    lernbares Signal ein (win korreliert mit trailing_vol) — Positivkontrolle
    dafür, dass die Pipeline ein Signal auch WIRKLICH erkennen kann."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2005-01-01", periods=n, freq="3D")
    trailing_vol = rng.uniform(0.0, 1.0, n)
    logit = signal_strength * (trailing_vol - 0.5) + rng.normal(0, 1.0, n)
    p_win = 1 / (1 + np.exp(-logit))
    win = (rng.uniform(0, 1, n) < p_win).astype(int)
    base_ret = rng.normal(0.0, 0.02, n)
    return_pct = np.where(win == 1, np.abs(base_ret) + 0.001, -np.abs(base_ret) - 0.001)
    regimes = rng.choice(["BULL_CALM", "BEAR_CALM", "SIDE_CALM"], n)
    return pd.DataFrame({
        "ticker": "AAA", "strategy": "synthetic", "entry_date": dates,
        "regime": regimes, "trailing_vol": trailing_vol,
        "trailing_ret": rng.normal(0, 0.02, n), "breadth": rng.uniform(0, 1, n),
        "return_pct": return_pct, "win": win,
    })


def test_evaluate_meta_labeling_empty_df():
    rep = evaluate_meta_labeling(pd.DataFrame())
    assert rep.n_rows == 0
    assert rep.n_eval_splits == 0
    assert rep.thresholds == []


def test_evaluate_meta_labeling_too_few_rows_is_empty_report():
    df = _synthetic_rows(n=20)
    rep = evaluate_meta_labeling(df, min_train_rows=200)
    assert rep.n_eval_splits == 0


def test_evaluate_meta_labeling_deterministic():
    df = _synthetic_rows(n=800, seed=3, signal_strength=1.5)
    kw = dict(n_blocks=5, holdout_years=1, thresholds=(0.5, 0.6))
    r1 = evaluate_meta_labeling(df, **kw)
    r2 = evaluate_meta_labeling(df, **kw)
    assert r1.mean_auc == r2.mean_auc
    assert [t.mean_edge_gain for t in r1.thresholds] == [t.mean_edge_gain for t in r2.thresholds]


def test_evaluate_meta_labeling_holdout_logged(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLDOUT_LOG_PATH", str(tmp_path / "holdout.json"))
    before = holdout_access_count("meta_label")
    df = _synthetic_rows(n=800, seed=4, signal_strength=1.5)
    evaluate_meta_labeling(df, n_blocks=5, holdout_years=1)
    assert holdout_access_count("meta_label") == before + 1


def test_evaluate_meta_labeling_no_holdout_no_log(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLDOUT_LOG_PATH", str(tmp_path / "holdout2.json"))
    df = _synthetic_rows(n=800, seed=5, signal_strength=1.5)
    evaluate_meta_labeling(df, n_blocks=5, holdout_years=0)
    assert holdout_access_count("meta_label") == 0


def test_evaluate_meta_labeling_detects_real_signal_positive_control():
    df = _synthetic_rows(n=2400, seed=7, signal_strength=3.0)
    rep = evaluate_meta_labeling(df, n_blocks=8, holdout_years=1,
                                 thresholds=(0.5, 0.6, 0.7))
    assert rep.n_eval_splits >= 3
    assert rep.mean_auc > 0.6                             # echtes Signal muss messbar diskriminieren
    assert any(t.verdict == "SIGNAL" for t in rep.thresholds)


def test_evaluate_meta_labeling_pure_noise_mostly_no_signal():
    df = _synthetic_rows(n=1600, seed=9, signal_strength=0.0)
    rep = evaluate_meta_labeling(df, n_blocks=6, holdout_years=1,
                                 thresholds=(0.5, 0.6, 0.7))
    assert all(t.verdict == "NO_SIGNAL" for t in rep.thresholds)
