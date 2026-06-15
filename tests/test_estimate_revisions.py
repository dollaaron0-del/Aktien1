"""
Tests für collectors/estimate_revisions_collector.py – Revisions-Momentum.
"""
import pandas as pd

import collectors.estimate_revisions_collector as erc
from collectors.estimate_revisions_collector import EstimateRevisionsCollector


def _rev(up30_by_period, down30_by_period):
    rows = {}
    for p in ("0q", "+1q", "0y", "+1y"):
        rows[p] = {"upLast30days": up30_by_period.get(p, 0),
                   "downLast30days": down30_by_period.get(p, 0)}
    return pd.DataFrame.from_dict(rows, orient="index")


def _trend(cur_0y, old_0y):
    return pd.DataFrame.from_dict(
        {"0y": {"current": cur_0y, "30daysAgo": old_0y}}, orient="index")


def test_strong_upward_revisions_bullish():
    c = EstimateRevisionsCollector()
    rev = _rev({"0q": 6, "+1q": 5, "0y": 7}, {"0q": 1, "+1q": 1, "0y": 1})
    items = c._signal_from("XYZ", rev, _trend(3.1, 3.0))
    assert len(items) == 1
    assert items[0]["sentiment_hint"] == "bullish"
    assert items[0]["priority"] == "HIGH"           # net >= 2*MIN
    assert "Aufwärts" in items[0]["title"]


def test_downward_revisions_bearish():
    c = EstimateRevisionsCollector()
    rev = _rev({"0q": 1, "+1q": 0, "0y": 1}, {"0q": 5, "+1q": 4, "0y": 3})
    items = c._signal_from("XYZ", rev, _trend(2.9, 3.0))
    assert items and items[0]["sentiment_hint"] == "bearish"


def test_below_threshold_no_signal():
    c = EstimateRevisionsCollector()
    rev = _rev({"0q": 2}, {"0q": 1})                 # net +1 < MIN
    assert c._signal_from("XYZ", rev, None) == []


def test_conflicting_trend_suppresses():
    c = EstimateRevisionsCollector()
    rev = _rev({"0q": 6, "0y": 6}, {"0q": 1, "0y": 1})   # net +10 bullish
    # aber Jahres-Trend deutlich runter → widersprüchlich → kein Signal
    assert c._signal_from("XYZ", rev, _trend(2.5, 3.0)) == []


def test_missing_data_safe():
    c = EstimateRevisionsCollector()
    assert c._signal_from("XYZ", None, None) == []
    assert c._net_revisions(pd.DataFrame()) is None
