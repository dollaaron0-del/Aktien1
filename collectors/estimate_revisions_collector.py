"""
collectors/estimate_revisions_collector.py – Analysten-Schätzungs-Revisionen.

Revisions-Momentum (Analysten heben/senken ihre EPS-Schätzungen) ist einer der
robustesten dokumentierten Alpha-Faktoren: Aktien mit netto positiven
Aufwärtsrevisionen tendieren zur Outperformance (Post-Earnings-Drift verwandt).

Ihr hattet Analyst-*Ratings* (Momentaufnahme), aber nicht den Revisions-*Trend*.
Quelle: yfinance (eps_revisions = Anzahl Auf-/Abwärtsrevisionen, eps_trend =
Schätzungsverlauf). Stock-only, 1-Tag-Cache, fail-safe [].

Konfig (.env):
  REVISIONS_MIN_NET   (Standard: 4)  netto Auf-/Abwärtsrevisionen (30d) für Signal
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_MIN_NET   = int(os.getenv("REVISIONS_MIN_NET", "4"))
_CACHE_TTL = 24 * 3600
_PERIODS   = ("0q", "+1q", "0y")   # aktuelles + nächstes Quartal + laufendes Jahr


class EstimateRevisionsCollector:
    def __init__(self):
        self._cache: Dict[str, tuple] = {}

    def collect(self, ticker: str) -> List[Dict]:
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL:
            return cached[0]
        try:
            import yfinance as yf
            tk = yf.Ticker(ticker)
            rev   = tk.eps_revisions
            trend = tk.eps_trend
        except Exception as e:
            log.debug("[%s] Estimate-Revisions Abruf: %s", ticker, e)
            return []
        items = self._signal_from(ticker, rev, trend)
        self._cache[ticker] = (items, time.time())
        return items

    # ── reine Parsing-/Signal-Logik (testbar ohne Netz) ───────────────────────
    def _signal_from(self, ticker: str, rev, trend) -> List[Dict]:
        net = self._net_revisions(rev)
        trend_pct = self._trend_pct(trend)
        if net is None or abs(net) < _MIN_NET:
            return []
        # Trend muss in dieselbe Richtung zeigen (oder unbekannt sein), sonst
        # widersprüchliches Signal → kein Item.
        if trend_pct is not None:
            if net > 0 and trend_pct < -0.5:
                return []
            if net < 0 and trend_pct > 0.5:
                return []

        from datetime import datetime, timezone
        bullish = net > 0
        direction = "Aufwärts" if bullish else "Abwärts"
        prio = "HIGH" if abs(net) >= _MIN_NET * 2 else "MEDIUM"
        tp = f" | Jahres-EPS-Trend {trend_pct:+.1f}%" if trend_pct is not None else ""
        return [{
            "source":       "EstimateRevisions",
            "ticker":       ticker,
            "title":        f"Analysten-Schätzungen: netto {net:+d} {direction}-Revisionen (30T){tp}",
            "text":         (f"Netto {net:+d} EPS-Revisionen über 30 Tage "
                             f"({'mehr Anhebungen' if bullish else 'mehr Senkungen'}). "
                             f"Revisions-Momentum ist ein etablierter Alpha-Faktor."),
            "published_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "priority":     prio,
            "sentiment_hint": "bullish" if bullish else "bearish",
        }]

    @staticmethod
    def _net_revisions(rev) -> Optional[int]:
        """Summe (upLast30days - downLast30days) über die relevanten Perioden."""
        if rev is None or getattr(rev, "empty", True):
            return None
        up_col   = _first_col(rev, ("upLast30days", "upLast30Days"))
        down_col = _first_col(rev, ("downLast30days", "downLast30Days"))
        if up_col is None or down_col is None:
            return None
        total = 0
        found = False
        for period in _PERIODS:
            if period in rev.index:
                try:
                    total += int(rev.loc[period, up_col]) - int(rev.loc[period, down_col])
                    found = True
                except (ValueError, TypeError, KeyError):
                    continue
        return total if found else None

    @staticmethod
    def _trend_pct(trend) -> Optional[float]:
        """Veränderung der laufenden Jahres-EPS-Schätzung ggü. vor 30 Tagen."""
        if trend is None or getattr(trend, "empty", True):
            return None
        if "0y" not in trend.index:
            return None
        cur_col = _first_col(trend, ("current",))
        old_col = _first_col(trend, ("30daysAgo", "30DaysAgo"))
        if cur_col is None or old_col is None:
            return None
        try:
            cur = float(trend.loc["0y", cur_col])
            old = float(trend.loc["0y", old_col])
            if old == 0:
                return None
            return (cur - old) / abs(old) * 100
        except (ValueError, TypeError, KeyError):
            return None


def _first_col(df, names):
    for n in names:
        if n in getattr(df, "columns", []):
            return n
    return None
