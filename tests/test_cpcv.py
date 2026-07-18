"""
Tests für CPCV (Roadmap 6.4c, zweite Anti-Overfit-Achse neben Walk-Forward).

Kern-Zusagen: (1) make_blocks partitioniert lückenlos und gleich lang.
(2) path_combos liefert alle C(n,k)-Kombinationen, deterministisch gekappt.
(3) _train_segments trennt Train sauber von jedem Testblock — mit Purge auf
der linken (End-)Seite und Embargo auf der rechten (Start-)Seite jeder
Testblock-Grenze, Ränder der Gesamt-Historie bleiben unangetastet. DAS ist
der eigentliche Kern von CPCV: KEIN Trainingsdatum darf im Purge/Embargo-
Sicherheitsabstand zu einem Testblock liegen. (4) run_cpcv liefert ein
plausibles Report-Objekt, ist deterministisch und liefert mit workers>1
identische Ergebnisse wie seriell. Netzfrei, synthetische Historie.
"""
import numpy as np
import pandas as pd
import pytest

from strategy_lab.cpcv import (_evaluate_multi, _merge_contiguous,
                               _slice_segments, _train_segments, make_blocks,
                               path_combos, run_cpcv)
from strategy_lab.strategies import get


def test_make_blocks_partitions_without_gaps_or_overlap():
    blocks = make_blocks("2000-01-01", "2020-01-01", 5)
    assert len(blocks) == 5
    assert blocks[0][0] == pd.Timestamp("2000-01-01")
    assert blocks[-1][1] == pd.Timestamp("2020-01-01")
    for i in range(4):
        assert blocks[i][1] == blocks[i + 1][0]        # lückenlos anschließend
    # etwa gleich lang
    lengths = [(e - s).days for s, e in blocks]
    assert max(lengths) - min(lengths) <= 1


def test_make_blocks_rejects_too_few_blocks():
    with pytest.raises(ValueError):
        make_blocks("2000-01-01", "2010-01-01", 1)


def test_path_combos_all_k_of_n():
    combos = path_combos(n_blocks=5, test_blocks=2, max_paths=100)
    assert len(combos) == 10                            # C(5,2)
    assert len(set(combos)) == 10                        # keine Duplikate
    assert all(len(c) == 2 for c in combos)


def test_path_combos_caps_deterministically():
    a = path_combos(n_blocks=10, test_blocks=2, max_paths=5, seed=42)
    b = path_combos(n_blocks=10, test_blocks=2, max_paths=5, seed=42)
    assert len(a) == 5
    assert a == b                                         # gleicher Seed -> gleiche Auswahl


# ── Purge/Embargo: der eigentliche Kern ──────────────────────────────────────

_BLOCKS = make_blocks("2000-01-01", "2010-01-01", 5)   # 5 Blöcke à ~2 Jahre
_PURGE = pd.Timedelta(days=10)
_EMBARGO = pd.Timedelta(days=5)


def _assert_no_overlap_with_margin(train_segments, test_block, purge, embargo):
    """Kein Trainings-Segment darf in [test_start-purge, test_end+embargo] hineinragen."""
    ts, te = test_block
    forbidden_lo, forbidden_hi = ts - purge, te + embargo
    for s, e in train_segments:
        assert not (s < forbidden_hi and e > forbidden_lo), \
            f"Segment ({s},{e}) verletzt Purge/Embargo um Testblock ({ts},{te})"


def test_train_segments_middle_test_block_purged_both_sides():
    segs = _train_segments(_BLOCKS, {2}, _PURGE, _EMBARGO)
    assert len(segs) == 2                                 # vor + nach dem Testblock
    _assert_no_overlap_with_margin(segs, _BLOCKS[2], _PURGE, _EMBARGO)
    # linkes Segment endet PURGE vor Testblock-Start, rechtes startet EMBARGO nach Testblock-Ende
    assert segs[0][1] == _BLOCKS[2][0] - _PURGE
    assert segs[1][0] == _BLOCKS[2][1] + _EMBARGO


def test_train_segments_leftmost_test_block_no_left_margin_needed():
    segs = _train_segments(_BLOCKS, {0}, _PURGE, _EMBARGO)
    assert len(segs) == 1                                 # nur "danach", kein "davor"
    assert segs[0][0] == _BLOCKS[0][1] + _EMBARGO          # Rand der Historie unangetastet sonst
    _assert_no_overlap_with_margin(segs, _BLOCKS[0], _PURGE, _EMBARGO)


def test_train_segments_rightmost_test_block_no_right_margin_needed():
    segs = _train_segments(_BLOCKS, {4}, _PURGE, _EMBARGO)
    assert len(segs) == 1
    assert segs[0][1] == _BLOCKS[4][0] - _PURGE
    _assert_no_overlap_with_margin(segs, _BLOCKS[4], _PURGE, _EMBARGO)


def test_train_segments_multiple_noncontiguous_test_blocks():
    segs = _train_segments(_BLOCKS, {1, 3}, _PURGE, _EMBARGO)
    assert len(segs) == 3                                 # vor Block1, zwischen 1&3, nach Block3
    for tb in (_BLOCKS[1], _BLOCKS[3]):
        _assert_no_overlap_with_margin(segs, tb, _PURGE, _EMBARGO)


def test_train_segments_adjacent_test_blocks_merge_into_one_gap():
    segs = _train_segments(_BLOCKS, {1, 2}, _PURGE, _EMBARGO)
    assert len(segs) == 2                                 # vor Block1, nach Block2 — kein Rest dazwischen
    for tb in (_BLOCKS[1], _BLOCKS[2]):
        _assert_no_overlap_with_margin(segs, tb, _PURGE, _EMBARGO)


def test_train_segments_large_margin_can_erase_a_segment():
    huge = pd.Timedelta(days=100_000)
    segs = _train_segments(_BLOCKS, {2}, huge, huge)
    assert segs == []                                     # beide Nachbarsegmente wegradiert


# ── _merge_contiguous (17.7.-Fix: doppelt gezählte Blockgrenze) ─────────────

def test_merge_contiguous_merges_adjacent_indices_into_one_segment():
    segs = _merge_contiguous(_BLOCKS, {1, 2})
    assert segs == [(_BLOCKS[1][0], _BLOCKS[2][1])]


def test_merge_contiguous_keeps_noncontiguous_indices_separate():
    segs = _merge_contiguous(_BLOCKS, {0, 2})
    assert segs == [_BLOCKS[0], _BLOCKS[2]]


def test_merge_contiguous_single_index():
    segs = _merge_contiguous(_BLOCKS, {3})
    assert segs == [_BLOCKS[3]]


def test_merge_contiguous_three_in_a_row():
    segs = _merge_contiguous(_BLOCKS, {1, 2, 3})
    assert segs == [(_BLOCKS[1][0], _BLOCKS[3][1])]


def test_slice_segments_of_merged_adjacent_blocks_has_no_duplicate_dates():
    """Regressions-Test für den 17.7.-Fund: zwei UNABHÄNGIG (nicht gemerged)
    geslicete angrenzende Blöcke teilen sich `blocks[i][1] == blocks[i+1][0]`
    (make_blocks() laesst sie bewusst lückenlos aneinanderstoßen) — ohne
    _merge_contiguous taucht dieses Datum in beiden Chunks auf und lässt
    pd.concat später mit "duplicate labels" abstürzen."""
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    df = pd.DataFrame({"Close": np.arange(500, dtype=float)}, index=idx)
    blocks = [(idx[0], idx[100]), (idx[100], idx[200]), (idx[200], idx[300])]

    naive = _slice_segments({"AAA": df}, [blocks[0], blocks[1]])
    assert pd.concat(naive["AAA"]).index.duplicated().any()   # der Fehlerfall

    merged = _slice_segments({"AAA": df}, _merge_contiguous(blocks, {0, 1}))
    concatenated = pd.concat(merged["AAA"])
    assert not concatenated.index.duplicated().any()


def test_slice_segments_filters_short_chunks():
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    df = pd.DataFrame({"Close": np.arange(500, dtype=float)}, index=idx)
    long_seg = (idx[0], idx[200])                          # ausreichend Bars
    short_seg = (idx[201], idx[205])                       # < 60 Bars
    out = _slice_segments({"AAA": df}, [long_seg, short_seg])
    assert list(out.keys()) == ["AAA"]
    assert len(out["AAA"]) == 1                            # nur das lange Segment übernommen


def test_slice_segments_drops_ticker_with_no_valid_chunk():
    idx = pd.date_range("2010-01-01", periods=500, freq="B")
    df = pd.DataFrame({"Close": np.arange(500, dtype=float)}, index=idx)
    out = _slice_segments({"AAA": df}, [(idx[0], idx[5])])  # zu kurz
    assert out == {}


# ── run_cpcv: End-to-End auf synthetischer Historie ──────────────────────────

def _synth_df(n=3500, seed=0, start="2003-01-01"):
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
    return _synth_df(seed=sum(ord(c) for c in t) % 100)


def test_evaluate_multi_combines_chunks_across_tickers():
    strat = get("donchian_breakout")
    params = dict(strat.param_space)
    params = {k: v[0] for k, v in params.items()}
    df = _synth_df(seed=1)
    chunks = {"AAA": [df.iloc[:1800], df.iloc[2000:]], "BBB": [df.iloc[:1800]]}
    m = _evaluate_multi(strat, chunks, params)
    assert m.ticker == "PORTFOLIO"


def test_run_cpcv_end_to_end_basic_shape():
    rep = run_cpcv("donchian_breakout", ["AAA", "BBB"], total_years=16,
                   n_blocks=5, test_blocks=1, purge_days=10, embargo_days=5,
                   max_combos=4, max_paths=5, loader=_loader)
    assert rep.strategy == "donchian_breakout"
    assert rep.n_windows >= 1
    assert rep.verdict in ("ROBUST", "FRAGILE", "OVERFIT")
    assert rep.n_combos_tested == 4


def test_run_cpcv_test_blocks_2_does_not_crash_on_adjacent_paths():
    """Regressions-Test für den 17.7.-Fund (erster echter Mehrkern-Lauf mit
    test_blocks=2 stürzte in classify_window()/pd.concat mit "duplicate
    labels" ab, sobald ein Pfad zwei ANGRENZENDE Blöcke als Test wählte).
    max_paths bewusst hoch genug, dass mit n_blocks=6 mehrere der
    C(6,2)=15 Kombinationen angrenzend sind (z.B. (0,1),(1,2),...)."""
    rep = run_cpcv("donchian_breakout", ["AAA", "BBB"], total_years=16,
                   n_blocks=6, test_blocks=2, purge_days=10, embargo_days=5,
                   max_combos=4, max_paths=15, loader=_loader)
    assert rep.n_windows >= 1
    assert rep.verdict in ("ROBUST", "FRAGILE", "OVERFIT")


def test_run_cpcv_deterministic():
    kw = dict(total_years=16, n_blocks=5, test_blocks=1, purge_days=10,
              embargo_days=5, max_combos=4, max_paths=5, loader=_loader)
    r1 = run_cpcv("donchian_breakout", ["AAA", "BBB"], **kw)
    r2 = run_cpcv("donchian_breakout", ["AAA", "BBB"], **kw)
    assert r1.n_windows == r2.n_windows
    assert r1.avg_test_return == r2.avg_test_return
    assert [w.test_return for w in r1.windows] == [w.test_return for w in r2.windows]


def test_run_cpcv_rejects_bad_block_config():
    with pytest.raises(ValueError):
        run_cpcv("donchian_breakout", ["AAA"], n_blocks=2, loader=_loader)
    with pytest.raises(ValueError):
        run_cpcv("donchian_breakout", ["AAA"], n_blocks=5, test_blocks=5, loader=_loader)


def test_run_cpcv_parallel_matches_serial():
    kw = dict(total_years=16, n_blocks=5, test_blocks=1, purge_days=10,
              embargo_days=5, max_combos=6, max_paths=5, loader=_loader)
    seriell = run_cpcv("donchian_breakout", ["AAA", "BBB"], workers=1, **kw)
    parallel = run_cpcv("donchian_breakout", ["AAA", "BBB"], workers=2, **kw)
    assert seriell.n_windows >= 1
    from dataclasses import asdict
    assert asdict(parallel) == asdict(seriell)


def test_run_cpcv_no_universe_data_is_overfit_empty():
    rep = run_cpcv("donchian_breakout", ["ZZZ"], loader=lambda t, y: None)
    assert rep.n_windows == 0
    assert rep.verdict == "OVERFIT"
