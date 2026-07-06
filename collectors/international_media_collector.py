"""
International Media Collector – Reuters, BBC Business, CNBC, Financial Times,
Bloomberg, Wall Street Journal, MarketWatch, Nikkei Asia, Economic Times (India),
Les Echos (France).

Tier 1 – Allgemeine Feeds (direkt, ticker-unabhängig):
  Reuters Business, BBC Business, CNBC Top News, MarketWatch

Tier 2 – Ticker-Suche via Google News (kein API-Key):
  Englisch: FT, Bloomberg, WSJ, Reuters, CNBC, MarketWatch
  Asien:    Nikkei Asia, Economic Times, South China Morning Post
  Sonstige: Les Echos (FR), The Guardian Business

Reuters erscheint typisch 2–6h früher als Yahoo Finance.
FT/Bloomberg decken institutionelle Sicht ab, die Retail-Kanäle übersehen.

Kein API-Key erforderlich.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional

import requests
from system.http import http_get
import yfinance as yf

_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockSentimentBot/1.0)"}

# Google News RSS – englische Premium-Outlets
_GNEWS_EN = (
    "https://news.google.com/rss/search"
    "?q={query}"
    "+site:reuters.com+OR+site:ft.com+OR+site:bloomberg.com"
    "+OR+site:wsj.com+OR+site:cnbc.com+OR+site:marketwatch.com"
    "+OR+site:theguardian.com/business"
    "&hl=en-US&gl=US&ceid=US:en"
)

# Google News RSS – asiatische Outlets
_GNEWS_ASIA = (
    "https://news.google.com/rss/search"
    "?q={query}"
    "+site:asia.nikkei.com+OR+site:economictimes.indiatimes.com"
    "+OR+site:scmp.com+OR+site:businesstimes.com.sg"
    "&hl=en-US&gl=US&ceid=US:en"
)

# Direkte RSS-Feeds (Tier 1 – immer geladen, für allgemeinen Marktkontext)
_GENERAL_FEEDS: List[tuple] = [
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews"),
    ("BBC Business",       "http://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC Top News",      "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch",        "https://feeds.marketwatch.com/marketwatch/topstories/"),
]

# Domain → Quellname für schöne Labels
_DOMAIN_MAP = {
    "reuters.com":               "Reuters",
    "bbc.co.uk":                 "BBC Business",
    "bbc.com":                   "BBC Business",
    "cnbc.com":                  "CNBC",
    "ft.com":                    "Financial Times",
    "bloomberg.com":             "Bloomberg",
    "wsj.com":                   "Wall Street Journal",
    "marketwatch.com":           "MarketWatch",
    "theguardian.com":           "The Guardian Business",
    "asia.nikkei.com":           "Nikkei Asia",
    "nikkei.com":                "Nikkei",
    "economictimes.indiatimes.com": "Economic Times",
    "scmp.com":                  "South China Morning Post",
    "businesstimes.com.sg":      "Business Times (SG)",
    "lesechos.fr":               "Les Echos",
}


class InternationalMediaCollector:
    """
    Sammelt internationale Finanznachrichten für einzelne Ticker und
    als allgemeinen Marktkontext.
    """

    def __init__(self, lookback_hours: int = 48, max_items: int = 15):
        self.lookback_hours = lookback_hours
        self.max_items = max_items
        self._cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=lookback_hours)

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def collect(self, ticker: str) -> List[Dict]:
        """Ticker-spezifische Suche in internationalen Premium-Medien."""
        items: List[Dict] = []

        # 1. Ticker direkt suchen (englische Outlets)
        items += self._gnews_search(ticker, ticker, region="en")

        # 2. Firmenname suchen (präziser für z.B. „Rheinmetall")
        company = self._get_company_name(ticker)
        if company and company.upper() not in ticker.upper():
            items += self._gnews_search(company, ticker, region="en")

        # 3. Asiatische Outlets (relevant für alle Ticker mit Asia-Exposure)
        items += self._gnews_search(ticker, ticker, region="asia")

        return self._dedupe(items)[: self.max_items]

    def collect_market_context(self) -> List[Dict]:
        """
        Allgemeine internationale Wirtschaftsnachrichten ohne Ticker-Bezug.
        Reuters, BBC, CNBC, MarketWatch – für Pre-Market-Briefing.
        """
        items: List[Dict] = []
        for source_name, url in _GENERAL_FEEDS:
            items += self._parse_rss(url, source_name, ticker="")
        return self._dedupe(items)[: self.max_items]

    # ── Intern ────────────────────────────────────────────────────────────────

    def _gnews_search(self, query: str, ticker: str, region: str = "en") -> List[Dict]:
        encoded = urllib.parse.quote_plus(query)
        template = _GNEWS_ASIA if region == "asia" else _GNEWS_EN
        url = template.format(query=encoded)
        return self._parse_rss(url, "InternationalMedia", ticker=ticker)

    def _parse_rss(self, url: str, default_source: str, ticker: str) -> List[Dict]:
        try:
            r = http_get(url, timeout=_TIMEOUT, headers=_HEADERS)
            if r.status_code != 200:
                return []
        except Exception:
            return []

        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            return []

        channel = root.find("channel")
        if channel is None:
            return []

        items: List[Dict] = []
        for item in channel.findall("item")[:25]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = item.findtext("pubDate") or ""
            desc  = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()

            if not title:
                continue

            try:
                pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
            except Exception:
                pub_dt = datetime.now(timezone.utc).replace(tzinfo=None)

            if pub_dt < self._cutoff:
                continue

            source = self._source_from_url(link) or default_source

            items.append({
                "source":       source,
                "ticker":       ticker,
                "title":        title,
                "text":         f"{title}. {desc[:400]}",
                "url":          link,
                "published_at": pub_dt.isoformat(),
                "language":     "en",
            })

        return items

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _source_from_url(url: str) -> Optional[str]:
        for domain, name in _DOMAIN_MAP.items():
            if domain in url:
                return name
        return None

    @staticmethod
    def _get_company_name(ticker: str) -> Optional[str]:
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName") or info.get("shortName") or ""
            for suffix in [
                ", Inc.", " Inc.", ", SE", " SE", " AG", ", AG",
                " GmbH", ", LLC", " Corp.", " Corporation",
                " Limited", " Ltd.", " Holdings", " Co.", " PLC",
                " NV", " SA", " SPA",
            ]:
                name = name.replace(suffix, "")
            return name.strip() or None
        except Exception:
            return None

    @staticmethod
    def _dedupe(items: List[Dict]) -> List[Dict]:
        seen: set = set()
        unique: List[Dict] = []
        for item in items:
            key = (item.get("title") or "").lower()[:80]
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
