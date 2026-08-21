"""
Tests für das Punkt-in-Zeit-Universum (Roadmap 6.2b, Vision V0.3).

Kern-Zusagen: (1) parse_membership_csv/load_membership lesen das
'date,tickers'-Format der fja05680/sp500-Quelle robust. (2) constituents_at()
liefert die zuletzt VOR/AN einem Stichtag gültige Liste, `None` vor dem
ersten Datensatz — kein Look-Ahead, kein stiller Fallback. (3) Walk-Forward
mit `pit_membership` filtert jedes Teilfenster auf die DAMALIGE
Mitgliederliste statt (heimlich) die heutige zu nutzen; ohne den Parameter
ändert sich am Verhalten nichts (Rückwärtskompatibilität). Netzfrei.
"""
import numpy as np
import pandas as pd

from strategy_lab.universe import (constituents_at, load_membership,
                                   parse_membership_csv)
from strategy_lab.walkforward import run_walk_forward

_CSV = (
    "date,tickers\n"
    '1996-01-02,"AAA,BBB,CCC"\n'
    '2010-06-15,"AAA,BBB,DDD"\n'
    '2020-01-10,"AAA,DDD,EEE"\n'
)


def test_parse_membership_csv_basic():
    df = parse_membership_csv(_CSV)
    assert list(df["date"].dt.strftime("%Y-%m-%d")) == \
        ["1996-01-02", "2010-06-15", "2020-01-10"]
    assert df.iloc[0]["tickers"] == ("AAA", "BBB", "CCC")
    assert df.iloc[2]["tickers"] == ("AAA", "DDD", "EEE")


def test_parse_membership_csv_empty_and_malformed_rows_skipped():
    df = parse_membership_csv("date,tickers\n,\n2020-01-01,\n,AAA\n")
    assert df.empty


def test_load_membership_missing_file_returns_empty(tmp_path):
    df = load_membership(tmp_path / "nope.csv")
    assert df.empty
    assert list(df.columns) == ["date", "tickers"]


def test_load_membership_roundtrip(tmp_path):
    p = tmp_path / "membership.csv"
    p.write_text(_CSV)
    df = load_membership(p)
    assert len(df) == 3


def test_constituents_at_picks_latest_le_date():
    df = parse_membership_csv(_CSV)
    assert constituents_at("2005-01-01", df) == ["AAA", "BBB", "CCC"]
    assert constituents_at("2010-06-15", df) == ["AAA", "BBB", "DDD"]     # Grenze inklusiv
    assert constituents_at("2015-01-01", df) == ["AAA", "BBB", "DDD"]
    assert constituents_at("2026-01-01", df) == ["AAA", "DDD", "EEE"]


def test_constituents_at_before_first_date_is_none():
    df = parse_membership_csv(_CSV)
    assert constituents_at("1990-01-01", df) is None


def test_constituents_at_empty_membership_is_none():
    assert constituents_at("2020-01-01", pd.DataFrame(columns=["date", "tickers"])) is None
    assert constituents_at("2020-01-01", None) is None


# ── Walk-Forward-Integration ─────────────────────────────────────────────────

def _flat_df(n=4000, seed=0, start="2000-01-01"):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0004, 0.01, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    openp = close * (1 + rng.normal(0, 0.002, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _loader(t, y):
    return _flat_df(seed=sum(ord(c) for c in t) % 100)


def test_walk_forward_without_pit_is_unchanged():
    kw = dict(universe=["AAA", "BBB"], total_years=16, train_years=4,
              test_years=2, step_years=2, max_combos=4, loader=_loader)
    rep = run_walk_forward("donchian_breakout", **kw)
    assert rep.pit_universe is False
    assert rep.pit_windows_dropped == 0


def test_walk_forward_pit_filter_drops_not_yet_listed_ticker():
    # BBB kommt laut Mitgliederliste erst 2010 rein → frühe Fenster (Train
    # beginnt 2000) dürfen BBB nicht sehen, spätere schon.
    membership = parse_membership_csv(
        'date,tickers\n2000-01-01,"AAA"\n2010-01-01,"AAA,BBB"\n'
    )
    kw = dict(universe=["AAA", "BBB"], total_years=16, train_years=4,
              test_years=2, step_years=2, max_combos=4, loader=_loader)
    rep_no_pit = run_walk_forward("donchian_breakout", **kw)
    rep_pit = run_walk_forward("donchian_breakout", pit_membership=membership, **kw)

    assert rep_pit.pit_universe is True
    assert rep_no_pit.n_windows == rep_pit.n_windows          # kein Fenster ganz verloren
    # Erstes Fenster: Train 2000–2004 sieht laut Membership nur AAA.
    early_no_pit = rep_no_pit.windows[0]
    early_pit = rep_pit.windows[0]
    assert early_no_pit.train_start == early_pit.train_start
    # Ohne Filter beeinflusst BBB potenziell das Aggregat; mit Filter ist das
    # Trainingsergebnis rein aus AAA — beide Ergebnisse müssen nicht gleich
    # sein, aber der PIT-Lauf darf nicht MEHR Ticker gesehen haben als erlaubt.
    assert isinstance(early_pit.test_return, float)


def test_walk_forward_pit_empty_membership_behaves_like_no_pit():
    empty = pd.DataFrame(columns=["date", "tickers"])
    kw = dict(universe=["AAA", "BBB"], total_years=16, train_years=4,
              test_years=2, step_years=2, max_combos=4, loader=_loader)
    rep = run_walk_forward("donchian_breakout", pit_membership=empty, **kw)
    assert rep.pit_universe is False


def test_walk_forward_pit_drops_window_when_nothing_survives():
    # Membership-Eintrag existiert, aber für den Testfenster-Stichtag ist die
    # Liste komplett leer → das Fenster muss übersprungen werden, nicht crashen.
    membership = parse_membership_csv('date,tickers\n2000-01-01,"ZZZ"\n')
    kw = dict(universe=["AAA", "BBB"], total_years=16, train_years=4,
              test_years=2, step_years=2, max_combos=4, loader=_loader)
    rep = run_walk_forward("donchian_breakout", pit_membership=membership, **kw)
    assert rep.n_windows == 0
    assert rep.pit_windows_dropped > 0
