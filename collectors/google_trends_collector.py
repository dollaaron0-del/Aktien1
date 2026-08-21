"""
collectors/google_trends_collector.py – Such-Interesse als Nachfrage-Proxy.

These: Springt das Google-Suchinteresse zu einer Marke/Firma sprunghaft an,
ist das ein früher Hinweis auf Konsum-/Aufmerksamkeits-Schübe (Produkt-Launch,
viraler Moment, Krise). Liefert pro Ticker höchstens EIN Item – nur wenn die
letzte Woche klar über dem eigenen 3-Monats-Median liegt (sonst Rauschen).

Quelle: Google Trends via pytrends (frei, kein Key). Bewusst stark gedrosselt
gecacht (12h/Ticker), da Google Trends aggressiv rate-limitet. Fail-safe → [].
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List

from logger import get_logger
from collectors._company import company_name

log = get_logger(__name__)

_CACHE_TTL_S = 12 * 3600
_SPIKE_RATIO = 1.6      # letzte Woche ≥ 1.6× Median → meldenswert
_MIN_LEVEL = 25         # absolute Untergrenze (0–100), damit Mini-Schwankungen raus


class GoogleTrendsCollector:
    def __init__(self):
        self._cache: Dict[str, tuple] = {}   # ticker → (items, ts)

    def collect(self, ticker: str) -> List[Dict]:
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
            return cached[0]
        try:
            items = self._query(ticker)
        except Exception as e:
            log.debug("[%s] Google-Trends: %s", ticker, e)
            items = []
        self._cache[ticker] = (items, time.time())
        return items

    def _query(self, ticker: str) -> List[Dict]:
        kw = company_name(ticker) or ticker
        from pytrends.request import TrendReq
        from statistics import median

        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        pt.build_payload([kw], timeframe="today 3-m")
        df = pt.interest_over_time()
        if df is None or df.empty or kw not in df.columns:
            return []
        series = [int(v) for v in df[kw].tolist() if v is not None]
        if len(series) < 6:
            return []
        cur = series[-1]
        base = median(series[:-1]) or 1
        ratio = cur / base
        if cur < _MIN_LEVEL or ratio < _SPIKE_RATIO:
            return []
        return [{
            "source": "GoogleTrends",
            "ticker": ticker,
            "title": f"Such-Interesse für {kw} springt an (×{ratio:.1f} vs. 3M-Median)",
            "text": (f"Google-Trends-Index für '{kw}' aktuell {cur}/100 gegenüber "
                     f"Median {base:.0f}. Sprunghaft erhöhte Suchnachfrage ist ein "
                     f"früher Aufmerksamkeits-/Nachfrage-Hinweis – Ursache (Launch, "
                     f"viraler Moment, Krise) prüfen."),
            "url": f"https://trends.google.com/trends/explore?q={kw.replace(' ', '%20')}",
            "published_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "priority": "NORMAL",
        }]
