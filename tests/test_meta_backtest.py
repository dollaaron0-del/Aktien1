"""
Tests für Roadmap 4.1 (Meta-Backtest des Allokators) — strategy_lab/meta_backtest.py.

Netzfrei: synthetische Historie per injiziertem Loader, winzige Test-Familien
mit deterministischen Runnern (in der globalen REGISTRY registriert und nach
dem Test wieder entfernt). Prüft vor allem die KAUSALITÄT (Selektion sieht nur
Vergangenheit, OOS nur das Folgefenster) und die Ehrlichkeit (leerer Plan =
flat statt erfundener Trades).
"""
import numpy as np
import pandas as pd
import pytest

from backtesting.engine import Trade
from strategy_lab.allocator import weight_plan
from strategy_lab.meta_backtest import (
    ARMS, _cutoff_loader, _window_loader, run_meta_backtest,
)
from strategy_lab.strategies import REGISTRY, Strategy, register


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _long_df(n=4000, trend=0.0004, seed=0, start="2006-01-02"):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 1e6),
    }, index=idx)


def _quarterly_trades(df, ticker, return_pct, hold_days=20, offset=5):
    """Deterministischer Runner: alle ~63 Bars ein Trade mit festem Return.
    Trades ausschließlich innerhalb des übergebenen df (kausal per Konstruktion).
    `offset` staffelt Familien zeitlich — sonst verdrängt der Cash-Constraint
    im Portfolio-Backtest die später einsortierte Familie komplett und die
    Gleichgewichtung würde den Verlierer nie real finanzieren."""
    trades = []
    i = offset
    while i + hold_days < len(df):
        trades.append(Trade(
            ticker=ticker, entry_date=df.index[i], entry_price=float(df["Close"].iloc[i]),
            exit_date=df.index[i + hold_days], exit_price=0.0,
            return_pct=return_pct, exit_reason="time_stop",
        ))
        i += 63
    return trades


@pytest.fixture
def fake_families():
    """Zwei Mini-Familien: 'meta_winner' (konstant +2 % je Trade → wird ROBUST),
    'meta_loser' (konstant −2 % → wird OVERFIT). Kein param_space → der innere
    Grid-Search testet genau eine Kombi, Tests bleiben schnell."""
    names = []
    for name, ret, off in (("meta_winner", 0.02, 5), ("meta_loser", -0.02, 35)):
        register(Strategy(
            name=name, description="test double",
            runner=(lambda r, o: lambda df, ticker, params:
                    _quarterly_trades(df, ticker, r, offset=o))(ret, off),
        ))
        names.append(name)
    yield names
    for n in names:
        REGISTRY.pop(n, None)


class _StubRelations:
    def get_themes(self, ticker):
        return []


def _run(names, tickers=("AAA", "BBB"), **kw):
    loader = lambda t, y: _long_df(seed=abs(hash(t)) % 100)
    # train == test (2J/2J): bei konstant positiven Trade-Returns wäre die
    # Walk-Forward-Effizienz sonst konstruktionsbedingt < 0.5 (Zinseszins:
    # (x−1)/(x²−1) < 0.5) und der Gewinner nie ROBUST.
    defaults = dict(total_years=20, selection_years=8, oos_years=2, step_years=4,
                    train_years=2, test_years=2, stock_relations=_StubRelations())
    defaults.update(kw)
    return run_meta_backtest(list(names), list(tickers), loader=loader, **defaults)


# ── Loader-Wrapper: Kausalität ───────────────────────────────────────────────

def test_cutoff_loader_strictly_before_end():
    df = _long_df(n=300)
    cut = df.index[200]
    sub = _cutoff_loader(lambda t, y: df, cut)("AAA", 20)
    assert sub.index.max() < cut
    assert len(sub) == 200


def test_window_loader_half_open_interval():
    df = _long_df(n=300)
    start, end = df.index[100], df.index[250]
    sub = _window_loader(lambda t, y: df, start, end)("AAA", 20)
    assert sub.index.min() == start
    assert sub.index.max() < end


def test_loaders_return_none_on_short_slice():
    df = _long_df(n=300)
    assert _cutoff_loader(lambda t, y: df, df.index[10])("AAA", 20) is None
    assert _window_loader(lambda t, y: None, df.index[0], df.index[10])("AAA", 20) is None


# ── weight_plan: In-Memory-Registry-Naht ─────────────────────────────────────

def test_weight_plan_uses_in_memory_registry_not_disk():
    registry = {"entries": [
        {"strategy": "meta_winner", "status": "ACTIVE", "weight": 1.0, "params": {}},
        {"strategy": "meta_loser", "status": "REJECTED", "weight": 0.0, "params": {}},
    ]}
    plan = weight_plan(registry=registry)
    assert [e["strategy"] for e in plan] == ["meta_winner"]
    # leere In-Memory-Registry heißt leer — auch wenn auf Platte etwas läge
    assert weight_plan(registry={"entries": []}) == []


# ── run_meta_backtest ────────────────────────────────────────────────────────

def test_meta_backtest_schedule_and_causality(fake_families):
    rep = _run(fake_families)
    assert rep.n_windows >= 2
    for w in rep.windows:
        # OOS-Fenster liegt strikt NACH dem Stichtag
        assert w.oos_start == w.as_of
        assert pd.Timestamp(w.oos_end) > pd.Timestamp(w.as_of)
        assert set(w.arms) == set(ARMS)
        assert set(w.verdicts) == set(fake_families)
    # Stichtage schreiten mit step_years fort
    d0, d1 = pd.Timestamp(rep.windows[0].as_of), pd.Timestamp(rep.windows[1].as_of)
    assert (d1 - d0).days >= 4 * 360


def test_meta_backtest_promotes_winner_and_beats_flat(fake_families):
    rep = _run(fake_families)
    for w in rep.windows:
        # der konstante Gewinner wird aus reiner Vergangenheit ROBUST/ACTIVE …
        assert w.verdicts["meta_winner"] == "ROBUST"
        assert w.verdicts["meta_loser"] == "OVERFIT"
        assert w.n_active == 1
        # … und der Allokator-Arm handelt ihn OOS profitabel
        assert w.arms["allokator"].plan_size == 1
        assert w.arms["allokator"].n_trades > 0
        assert w.arms["allokator"].total_return > 0
        # Gleichgewichtung schleppt den Verlierer mit → schlechter als Allokator
        assert w.arms["gleichgewichtung"].plan_size == 2
        assert w.arms["allokator"].total_return > w.arms["gleichgewichtung"].total_return
    d = rep.diffs["allokator_vs_gleich"]
    assert d["mean_diff"] > 0
    assert len(d["per_window"]) == rep.n_windows


def test_meta_backtest_empty_registry_is_flat():
    """Nur der Verlierer registriert → Registry hat 0 ACTIVE → Allokator-Arme
    ehrlich flat (0 Trades, Return 0), Gleichgewichtung handelt trotzdem."""
    register(Strategy(name="meta_only_loser", description="test double",
                      runner=lambda df, ticker, params: _quarterly_trades(df, ticker, -0.02)))
    try:
        rep = _run(["meta_only_loser"])
        assert rep.n_windows >= 1
        for w in rep.windows:
            assert w.n_active == 0
            for arm in ("allokator", "allokator_regime"):
                a = w.arms[arm]
                assert (a.plan_size, a.n_trades, a.total_return) == (0, 0, 0.0)
            assert w.arms["gleichgewichtung"].n_trades > 0
        assert rep.summary["allokator"]["n_flat"] == rep.n_windows
    finally:
        REGISTRY.pop("meta_only_loser", None)


def test_meta_backtest_summary_and_diff_fields(fake_families):
    rep = _run(fake_families)
    for arm in ARMS:
        s = rep.summary[arm]
        for key in ("mean_return", "median_return", "mean_max_drawdown",
                    "worst_max_drawdown", "n_flat", "n_trades"):
            assert key in s
    for key in ("allokator_vs_gleich", "regime_vs_allokator"):
        d = rep.diffs[key]
        assert d["ci_lo"] <= d["mean_diff"] <= d["ci_hi"]
        assert 0.0 <= d["p_le0"] <= 1.0


def test_meta_backtest_no_data_or_too_little_history():
    assert run_meta_backtest(["baseline_swing"], ["X"], loader=lambda t, y: None).n_windows == 0
    # Historie kürzer als selection_years + oos_years → 0 Meta-Fenster
    short = lambda t, y: _long_df(n=500)
    rep = run_meta_backtest(["baseline_swing"], ["AAA"], loader=short,
                            selection_years=8, oos_years=2,
                            stock_relations=_StubRelations())
    assert rep.n_windows == 0


def test_meta_backtest_rejects_impossible_selection_span():
    with pytest.raises(ValueError):
        run_meta_backtest(["baseline_swing"], ["AAA"], selection_years=3,
                          train_years=4, test_years=2, loader=lambda t, y: None)
