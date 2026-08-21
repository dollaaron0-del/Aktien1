"""
Tests für die Live-Trade-Validierung des Meta-Labeling-Modells (Roadmap 6.8c).

Kern-Zusagen: (1) collect_live_rows() liest nur 'live'-Zeilen mit verwertbarem
Outcome, rechnet pnl_pct von Prozent (Store-Konvention) auf Fraktion um.
(2) validate_against_live() trainiert strategie-frei (nur Regime/Vola/Rendite/
Breadth) und liefert ehrlich ZU_WENIG_DATEN statt eines Verdikts unter dem
Mindest-n. (3) Mit genug synthetischen Beispielen und einem künstlich
eingebauten Zusammenhang erkennt es tatsächlich SIGNAL (Positivkontrolle),
kein Immer-NO_SIGNAL. Netzfrei, synthetische Daten.
"""
import numpy as np
import pandas as pd

from strategy_lab.meta_label_validation import (collect_live_rows,
                                                 validate_against_live)


def _price_df(n=400, seed=0, start="2015-01-01", trend=0.0005):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


class _StubStore:
    def __init__(self, rows):
        self._rows = rows

    def iter_labeled(self, label_source=None, recommendation=None):
        for features, out in self._rows:
            yield features, out


def _live_row(ticker="AAPL", decided_at="2020-06-15", pnl_pct=3.5, outcome="WIN"):
    return ({"ticker": ticker, "decided_at": decided_at},
            {"pnl_pct": pnl_pct, "outcome": outcome})


# ── collect_live_rows ────────────────────────────────────────────────────────

def test_collect_live_rows_converts_percent_to_fraction():
    df = _price_df(n=300, seed=2)
    store = _StubStore([_live_row(decided_at=str(df.index[200].date()), pnl_pct=4.0)])
    out = collect_live_rows(store, loader=lambda t, y: df, total_years=20)
    assert len(out) == 1
    assert out.iloc[0]["pnl_pct"] == 0.04
    assert out.iloc[0]["win"] == 1


def test_collect_live_rows_skips_missing_outcome():
    df = _price_df(n=300, seed=2)
    rows = [
        ({"ticker": "AAPL", "decided_at": str(df.index[200].date())}, {"pnl_pct": None, "outcome": None}),
        ({"ticker": "AAPL", "decided_at": str(df.index[201].date())}, {"pnl_pct": 1.0, "outcome": "WIN"}),
    ]
    out = collect_live_rows(_StubStore(rows), loader=lambda t, y: df, total_years=20)
    assert len(out) == 1


def test_collect_live_rows_skips_too_little_history():
    df = _price_df(n=300, seed=2)
    store = _StubStore([_live_row(decided_at=str(df.index[5].date()))])
    out = collect_live_rows(store, loader=lambda t, y: df, total_years=20)
    assert out.empty


def test_collect_live_rows_skips_unknown_ticker():
    store = _StubStore([_live_row(ticker="ZZZ", decided_at="2020-06-15")])
    out = collect_live_rows(store, loader=lambda t, y: None, total_years=20)
    assert out.empty


# ── validate_against_live ────────────────────────────────────────────────────

def _synthetic_frames(n=200, seed=0, signal_strength=0.0):
    """train_df/live_df mit denselben Spalten wie build_training_rows()/
    collect_live_rows() liefern würden. signal_strength>0 baut einen künstlichen
    Zusammenhang zwischen trailing_ret und win/pnl_pct ein (Positivkontrolle)."""
    rng = np.random.default_rng(seed)
    regimes = rng.choice(["BULL", "NEUTRAL", "BEAR"], n)
    trailing_ret = rng.normal(0, 0.05, n)
    trailing_vol = rng.uniform(0.005, 0.03, n)
    breadth = rng.uniform(0.3, 0.7, n)
    p_win = 1 / (1 + np.exp(-(signal_strength * trailing_ret * 20)))
    win = (rng.random(n) < p_win).astype(int)
    pnl_pct = np.where(win == 1, rng.uniform(0.01, 0.05, n), -rng.uniform(0.01, 0.05, n))
    return pd.DataFrame({
        "regime": regimes, "trailing_vol": trailing_vol, "trailing_ret": trailing_ret,
        "breadth": breadth, "pnl_pct": pnl_pct, "win": win,
    })


def test_validate_against_live_empty_inputs_is_empty_report():
    rep = validate_against_live(pd.DataFrame(), pd.DataFrame())
    assert rep.n_train_rows == 0
    assert rep.thresholds == []


def test_validate_against_live_below_min_n_is_honest():
    train_df = _synthetic_frames(n=300, seed=1, signal_strength=3.0)
    live_df = _synthetic_frames(n=5, seed=2, signal_strength=3.0)   # bewusst winzig
    rep = validate_against_live(train_df, live_df, min_n=15)
    assert rep.n_live_scored == 5
    assert all(t.verdict == "ZU_WENIG_DATEN" for t in rep.thresholds)


def test_validate_against_live_detects_real_signal_positive_control():
    train_df = _synthetic_frames(n=2000, seed=10, signal_strength=4.0)
    live_df = _synthetic_frames(n=200, seed=11, signal_strength=4.0)
    rep = validate_against_live(train_df, live_df, thresholds=(0.5, 0.6, 0.7), min_n=15)
    assert rep.auc is not None and rep.auc > 0.55
    verdicts = {t.threshold: t.verdict for t in rep.thresholds}
    assert "SIGNAL" in verdicts.values()


def test_validate_against_live_pure_noise_mostly_no_signal():
    train_df = _synthetic_frames(n=2000, seed=20, signal_strength=0.0)
    live_df = _synthetic_frames(n=200, seed=21, signal_strength=0.0)
    rep = validate_against_live(train_df, live_df, thresholds=(0.5, 0.6, 0.7), min_n=15)
    signals = [t for t in rep.thresholds if t.verdict == "SIGNAL"]
    assert len(signals) == 0
