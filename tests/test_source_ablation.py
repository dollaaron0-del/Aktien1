"""
Tests für scripts/source_ablation.py (Roadmap 2.4) — Bootstrap-Diff-Statistik,
die Treffer/Kein-Treffer-Aufteilung je Quelle und den DB-Join. Netzfrei.
"""
import sqlite3

import numpy as np
import pytest

from scripts.source_ablation import (
    _bootstrap_diff_ci,
    _ablate_sources,
    _load_joined_trades,
)


def _trade(pnl, **breakdown):
    return {"pnl_pct": pnl, "breakdown": breakdown}


# ── _bootstrap_diff_ci ───────────────────────────────────────────────────────

def test_bootstrap_diff_ci_clear_positive_effect():
    rng = np.random.default_rng(1)
    hit = [3.0] * 20
    miss = [-1.0] * 20
    d = _bootstrap_diff_ci(hit, miss, rng, iters=2000)
    assert d["n_hit"] == 20 and d["n_miss"] == 20
    assert d["diff"] == pytest.approx(4.0)
    assert d["lo"] > 0                 # CI klar über 0 -> Quelle hilft
    assert d["p_le0"] == 0.0


def test_bootstrap_diff_ci_no_effect_ci_spans_zero():
    rng = np.random.default_rng(2)
    hit = [1.0, -1.0, 2.0, -2.0, 0.5, -0.5, 1.5, -1.5, 0.0, 0.2] * 3
    miss = list(hit)  # identische Verteilung -> Differenz sollte um 0 liegen
    d = _bootstrap_diff_ci(hit, miss, rng, iters=2000)
    assert d["lo"] < 0 < d["hi"]


# ── _ablate_sources ───────────────────────────────────────────────────────────

def test_ablate_sources_ok_status_with_enough_data():
    rng = np.random.default_rng(3)
    trades = [_trade(3.0, sec_8k=1) for _ in range(12)] + [_trade(-1.0, sec_8k=0) for _ in range(12)]
    results = _ablate_sources(trades, rng, min_n=10)
    r = next(x for x in results if x["source"] == "sec_8k")
    assert r["status"] == "ok"
    assert r["n_hit"] == 12 and r["n_miss"] == 12
    assert r["diff"] == pytest.approx(4.0)


def test_ablate_sources_insufficient_below_min_n():
    rng = np.random.default_rng(4)
    trades = [_trade(1.0, insider=1) for _ in range(3)] + [_trade(0.0, insider=0) for _ in range(20)]
    results = _ablate_sources(trades, rng, min_n=10)
    r = next(x for x in results if x["source"] == "insider")
    assert r["status"] == "insufficient"
    assert r["n_hit"] == 3


def test_ablate_sources_no_variance_when_source_always_fires():
    rng = np.random.default_rng(5)
    trades = [_trade(1.0, yahoo=1) for _ in range(15)]
    results = _ablate_sources(trades, rng, min_n=10)
    r = next(x for x in results if x["source"] == "yahoo")
    assert r["status"] == "no_variance"
    assert r["n_miss"] == 0


def test_ablate_sources_collects_all_sources_across_trades():
    rng = np.random.default_rng(6)
    trades = [_trade(1.0, a=1), _trade(2.0, b=1)]
    results = _ablate_sources(trades, rng, min_n=1)
    names = {r["source"] for r in results}
    assert names == {"a", "b"}


# ── _load_joined_trades ───────────────────────────────────────────────────────

def _make_exp_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE decisions (ticker TEXT, decided_at TEXT, pnl_pct REAL, "
                "outcome TEXT, label_source TEXT)")
    return conn


def _make_al_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE analyses (ticker TEXT, analyzed_at TEXT, sources_breakdown TEXT)")
    return conn


def test_load_joined_trades_matches_on_ticker_and_timestamp():
    exp = _make_exp_db()
    al = _make_al_db()
    exp.execute("INSERT INTO decisions VALUES ('AAPL', 'T1', 2.5, 'WIN', 'backfill')")
    al.execute("INSERT INTO analyses VALUES ('AAPL', 'T1', '{\"yahoo\": 5, \"sec_8k\": 0}')")
    trades = _load_joined_trades(exp, al)
    assert len(trades) == 1
    assert trades[0]["breakdown"] == {"yahoo": 5, "sec_8k": 0}
    assert trades[0]["pnl_pct"] == 2.5


def test_load_joined_trades_skips_unmatched_timestamp():
    exp = _make_exp_db()
    al = _make_al_db()
    exp.execute("INSERT INTO decisions VALUES ('AAPL', 'T1', 2.5, 'WIN', 'backfill')")
    al.execute("INSERT INTO analyses VALUES ('AAPL', 'T2', '{\"yahoo\": 5}')")
    trades = _load_joined_trades(exp, al)
    assert trades == []


def test_load_joined_trades_skips_null_pnl_and_missing_breakdown():
    exp = _make_exp_db()
    al = _make_al_db()
    exp.execute("INSERT INTO decisions VALUES ('AAPL', 'T1', NULL, 'WIN', 'backfill')")
    exp.execute("INSERT INTO decisions VALUES ('MSFT', 'T2', 1.0, 'LOSS', 'backfill')")
    al.execute("INSERT INTO analyses VALUES ('AAPL', 'T1', '{\"yahoo\": 5}')")
    al.execute("INSERT INTO analyses VALUES ('MSFT', 'T2', NULL)")
    trades = _load_joined_trades(exp, al)
    assert trades == []


def test_load_joined_trades_filters_by_label_source():
    exp = _make_exp_db()
    al = _make_al_db()
    exp.execute("INSERT INTO decisions VALUES ('AAPL', 'T1', 2.5, 'WIN', 'backfill')")
    exp.execute("INSERT INTO decisions VALUES ('AAPL', 'T2', 1.0, 'WIN', 'backfill_hypo')")
    al.execute("INSERT INTO analyses VALUES ('AAPL', 'T1', '{\"yahoo\": 5}')")
    al.execute("INSERT INTO analyses VALUES ('AAPL', 'T2', '{\"yahoo\": 5}')")
    trades = _load_joined_trades(exp, al, label_sources={"backfill"})
    assert len(trades) == 1
    assert trades[0]["decided_at"] == "T1"
