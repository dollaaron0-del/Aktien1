"""
collectors/wikipedia_views_collector.py – Wikipedia-Aufmerksamkeit als Proxy.

These: Ein Sprung der täglichen Wikipedia-Abrufe zum Firmen-Artikel zeigt
erhöhte öffentliche Aufmerksamkeit (Retail-Interesse, Nachrichtenlage). Liefert
pro Ticker höchstens EIN Item, wenn der letzte Tag klar über dem 30-Tage-Median
liegt.

Quelle: Wikimedia REST Pageviews API (frei, kein Key). Pflicht: aussagekräftiger
User-Agent. Cache 12h/Ticker. Fail-safe → [].
"""
from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timedelta
from statistics import median
from typing import Dict, List

from logger import get_logger
from system.http import http_get
from collectors._company import company_name

log = get_logger(__name__)

_API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/all-agents/{article}/daily/{start}/{end}")
_HEADERS = {"User-Agent": "RufloTradingBot/1.0 (research; contact via repo)"}
_CACHE_TTL_S = 12 * 3600
_SPIKE_RATIO = 2.0      # letzter Tag ≥ 2× Median → meldenswert
_MIN_VIEWS = 300        # absolute Untergrenze, damit Mini-Artikel kein Rauschen sind


class WikipediaViewsCollector:
    def __init__(self):
        self._cache: Dict[str, tuple] = {}

    def collect(self, ticker: str) -> List[Dict]:
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
            return cached[0]
        try:
            items = self._query(ticker)
        except Exception as e:
            log.debug("[%s] Wikipedia-Views: %s", ticker, e)
            items = []
        self._cache[ticker] = (items, time.time())
        return items

    def _query(self, ticker: str) -> List[Dict]:
        name = company_name(ticker)
        if not name:
            return []
        article = urllib.parse.quote(name.replace(" ", "_"), safe="")
        end = datetime.utcnow().date()
        start = end - timedelta(days=35)
        url = _API.format(article=article,
                          start=start.strftime("%Y%m%d"),
                          end=end.strftime("%Y%m%d"))
        resp = http_get(url, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        items = (resp.json() or {}).get("items", []) or []
        views = [int(it.get("views", 0)) for it in items]
        if len(views) < 10:
            return []
        cur = views[-1]
        base = median(views[:-1]) or 1
        ratio = cur / base
        if cur < _MIN_VIEWS or ratio < _SPIKE_RATIO:
            return []
        return [{
            "source": "WikipediaViews",
            "ticker": ticker,
            "title": f"Wikipedia-Abrufe für {name} springen an (×{ratio:.1f} vs. 30T-Median)",
            "text": (f"Tägliche Wikipedia-Aufrufe des Artikels '{name}' bei {cur} "
                     f"gegenüber Median {base:.0f}. Aufmerksamkeits-Spike – häufig "
                     f"mit Nachrichtenlage/Retail-Interesse korreliert."),
            "url": f"https://en.wikipedia.org/wiki/{article}",
            "published_at": datetime.utcnow().isoformat(),
            "priority": "NORMAL",
        }]
