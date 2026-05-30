"""
BotDataBridge – einheitliche Datenschicht für alle Bot-Module.

Liest read-only aus allen Datastores und liefert einen konsistenten
TickerState. Verwendet dieselbe Empfehlung-Priorität wie der Runner:

  1. SignalQueue (pending BUY)   – höchste Priorität
  2. AnalysisCache (< 24h)       – schnellster Lookup
  3. AnalysisLog (letzte Analyse) – vollständige Historie

Alle Dashboard-Komponenten sollten über diese Bridge lesen statt
direkt auf einzelne Stores zuzugreifen.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@dataclass
class TickerState:
    ticker:               str
    recommendation:       str          # BUY | HOLD | SELL | SKIP | UNKNOWN
    score:                float        # Sentiment 0.0–1.0
    confidence:           str          # HIGH | MEDIUM | LOW | UNKNOWN
    direction:            str          # BULLISH | NEUTRAL | BEARISH
    analyzed_at:          str          # ISO timestamp oder ""
    # Signal-Status
    is_pending_buy:       bool
    signal_expires_at:    str
    # Bedingte Einstiege
    has_conditional_entry: bool
    conditional_trigger:  Optional[float]
    # Watchlist
    in_watchlist:         bool
    watchlist_score:      float        # technischer Score 0–100
    # Radar-Kandidat (Geo/Insider/Social)
    in_bench_list:        bool
    # Themen-Cluster
    themes:               List[str] = field(default_factory=list)
    # Quelle für recommendation
    rec_source:           str = "none"


class BotDataBridge:
    """
    Read-only Aggregation aller Bot-Datenstores.
    Instanz einmal pro Request erstellen – lädt jede Quelle lazy und
    hält sie für die Lebensdauer der Instanz gecacht.
    """

    def __init__(self):
        self._pending:   Optional[Dict[str, dict]] = None
        self._cache:     Optional[Dict[str, dict]] = None
        self._log:       Optional[Dict[str, dict]] = None
        self._watchlist: Optional[set]             = None
        self._wl_scores: Optional[Dict[str, float]] = None
        self._bench:     Optional[set]             = None
        self._cond:      Optional[Dict[str, float]] = None
        self._themes_fn  = None

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def get_ticker_state(self, ticker: str) -> TickerState:
        """Einheitlicher State für einen Ticker aus allen Quellen."""
        t    = ticker.upper()
        pend = self._load_pending()
        cach = self._load_cache()
        log  = self._load_log()
        wl   = self._load_watchlist()
        wls  = self._load_wl_scores()
        ben  = self._load_bench()
        con  = self._load_conditional()
        gt   = self._load_themes_fn()

        base = dict(
            has_conditional_entry=t in con,
            conditional_trigger=con.get(t),
            in_watchlist=t in wl,
            watchlist_score=wls.get(t, 0.0),
            in_bench_list=t in ben,
            themes=gt(t),
        )

        # Priorität 1: ausstehende Signal-Queue
        if t in pend:
            s = pend[t]
            return TickerState(
                ticker=t,
                recommendation="BUY",
                score=s.get("sentiment_score", 0.0),
                confidence=s.get("confidence", "MEDIUM"),
                direction=s.get("direction", "BULLISH"),
                analyzed_at=s.get("created_at", ""),
                is_pending_buy=True,
                signal_expires_at=s.get("expires_at", ""),
                rec_source="signal_queue", **base,
            )

        # Priorität 2: frischer Analysis-Cache (< 24 h)
        if t in cach:
            c = cach[t]
            return TickerState(
                ticker=t,
                recommendation=c.get("recommendation", "UNKNOWN"),
                score=c.get("sentiment_score", 0.0),
                confidence=c.get("confidence", "UNKNOWN"),
                direction=c.get("direction", "NEUTRAL"),
                analyzed_at=c.get("updated_at", ""),
                is_pending_buy=False,
                signal_expires_at="",
                rec_source="analysis_cache", **base,
            )

        # Priorität 3: Analysis-Log (auch ältere Analysen)
        if t in log:
            e = log[t]
            return TickerState(
                ticker=t,
                recommendation=e.get("recommendation", "UNKNOWN"),
                score=float(e.get("sentiment_score") or 0.0),
                confidence=e.get("confidence", "UNKNOWN"),
                direction=e.get("direction", "NEUTRAL"),
                analyzed_at=e.get("analyzed_at", ""),
                is_pending_buy=False,
                signal_expires_at="",
                rec_source="analysis_log", **base,
            )

        return TickerState(
            ticker=t, recommendation="UNKNOWN", score=0.0,
            confidence="UNKNOWN", direction="NEUTRAL", analyzed_at="",
            is_pending_buy=False, signal_expires_at="",
            rec_source="none", **base,
        )

    def get_all_states(self) -> Dict[str, TickerState]:
        """State aller bekannten Ticker (aus Signal-Queue + Cache + Log)."""
        tickers = (
            set(self._load_pending().keys())
            | set(self._load_cache().keys())
            | set(self._load_log().keys())
        )
        return {t: self.get_ticker_state(t) for t in tickers}

    def get_all_tickers(self) -> List[str]:
        return sorted(self.get_all_states().keys())

    # ── Private Lazy-Loader ───────────────────────────────────────────────────

    def _load_pending(self) -> Dict[str, dict]:
        if self._pending is None:
            try:
                from portfolio.signal_queue import SignalQueue
                self._pending = {
                    s["ticker"].upper(): s for s in SignalQueue().get_pending()
                }
            except Exception:
                self._pending = {}
        return self._pending

    def _load_cache(self) -> Dict[str, dict]:
        if self._cache is None:
            try:
                path = os.path.join(_DATA_DIR, "analysis_cache.json")
                with open(path) as f:
                    raw = json.load(f)
                self._cache = {k.upper(): v for k, v in raw.items()}
            except Exception:
                self._cache = {}
        return self._cache

    def _load_log(self) -> Dict[str, dict]:
        if self._log is None:
            try:
                from analyzers.analysis_log import AnalysisLog
                entries = AnalysisLog().get_latest_per_ticker(limit=500)
                self._log = {e["ticker"].upper(): dict(e) for e in entries}
            except Exception:
                self._log = {}
        return self._log

    def _load_watchlist(self) -> set:
        if self._watchlist is None:
            try:
                path = os.path.join(_DATA_DIR, "dynamic_watchlist.json")
                with open(path) as f:
                    data = json.load(f)
                self._watchlist = {t.upper() for t in data.get("tickers", [])}
            except Exception:
                self._watchlist = set()
        return self._watchlist

    def _load_wl_scores(self) -> Dict[str, float]:
        if self._wl_scores is None:
            try:
                path = os.path.join(_DATA_DIR, "dynamic_watchlist.json")
                with open(path) as f:
                    data = json.load(f)
                self._wl_scores = {
                    s["ticker"].upper(): s.get("total_score", 0.0)
                    for s in data.get("scored", [])
                }
            except Exception:
                self._wl_scores = {}
        return self._wl_scores

    def _load_bench(self) -> set:
        if self._bench is None:
            try:
                from analyzers.bench_list import BenchList
                self._bench = {e["ticker"].upper() for e in BenchList().get_all()}
            except Exception:
                self._bench = set()
        return self._bench

    def _load_conditional(self) -> Dict[str, float]:
        if self._cond is None:
            try:
                from analyzers.conditional_entry import ConditionalEntryWatcher
                self._cond = {
                    e.ticker.upper(): e.trigger_price
                    for e in ConditionalEntryWatcher().get_active()
                }
            except Exception:
                self._cond = {}
        return self._cond

    def _load_themes_fn(self):
        if self._themes_fn is None:
            try:
                from analyzers.stock_relations import StockRelations
                self._themes_fn = StockRelations().get_themes
            except Exception:
                self._themes_fn = lambda t: []
        return self._themes_fn
