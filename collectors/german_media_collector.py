"""
German Media Collector – Handelsblatt, ARD Tagesschau, n-tv, finanzen.net,
Manager Magazin.

Warum deutsche Medien?
  • Handelsblatt berichtet 6–12h früher als englischsprachige Quellen über DAX-Ereignisse
  • ARD Tagesschau Wirtschaft meldet systemrelevante Events (Insolvenz, Regulierung, Politik)
  • finanzen.net liefert ticker-spezifische Analysen auf Deutsch
  • Relevant für ALLE Ticker – auch Apple, NVDA, Tesla werden auf Deutsch diskutiert

Alle Quellen via Google News RSS – kein API-Key erforderlich.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional

import requests
from system.http import http_get
import yfinance as yf

_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockSentimentBot/1.0)"}

# Google News RSS – eingeschränkt auf deutsche Finanzmedien
_GNEWS_DE = (
    "https://news.google.com/rss/search"
    "?q={query}"
    "+site:handelsblatt.com+OR+site:finanzen.net"
    "+OR+site:n-tv.de+OR+site:boerse.de"
    "+OR+site:manager-magazin.de+OR+site:wallstreet-online.de"
    "&hl=de&gl=DE&ceid=DE:de"
)

# Allgemeine Wirtschafts-RSS-Feeds (ticker-unabhängig, für Marktkontext)
_GENERAL_FEEDS: List[tuple] = [
    ("ARD Tagesschau Wirtschaft", "https://www.tagesschau.de/xml/rss2_wirtschaft/"),
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/schlagzeilen"),
    ("n-tv Wirtschaft", "https://www.n-tv.de/rss"),
]

# Domain → Quellname
_DOMAIN_MAP = {
    "handelsblatt.com": "Handelsblatt",
    "tagesschau.de":    "ARD Tagesschau",
    "zdf.de":           "ZDF",
    "n-tv.de":          "n-tv",
    "finanzen.net":     "finanzen.net",
    "boerse.de":        "boerse.de",
    "manager-magazin.de": "Manager Magazin",
    "wallstreet-online.de": "Wallstreet Online",
    "spiegel.de":       "Spiegel Wirtschaft",
    "focus.de":         "Focus Money",
}


class GermanMediaCollector:
    """
    Sammelt deutsche Finanznachrichten für einzelne Ticker sowie allgemeinen
    Marktkontext aus ARD/Handelsblatt.
    """

    def __init__(self, lookback_hours: int = 48, max_items: int = 15):
        self.lookback_hours = lookback_hours
        self.max_items = max_items
        self._cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def collect(self, ticker: str) -> List[Dict]:
        """Ticker-spezifische Suche in deutschen Medien."""
        items: List[Dict] = []

        # 1. Ticker direkt suchen
        items += self._gnews_search(ticker, ticker)

        # 2. Unternehmensname suchen (besser für .DE-Aktien wie „Rheinmetall AG")
        company = self._get_company_name(ticker)
        if company and company.upper() not in ticker.upper():
            items += self._gnews_search(company, ticker)

        return self._dedupe(items)[: self.max_items]

    def collect_market_context(self) -> List[Dict]:
        """
        Allgemeine deutsche Wirtschaftsnachrichten – unabhängig vom Ticker.
        Nützlich für Pre-Market-Briefing und Claude-Systemkontext.
        """
        items: List[Dict] = []
        for source_name, url in _GENERAL_FEEDS:
            items += self._parse_rss(url, source_name, ticker="")
        return self._dedupe(items)[: self.max_items]

    # ── Intern ────────────────────────────────────────────────────────────────

    def _gnews_search(self, query: str, ticker: str) -> List[Dict]:
        encoded = urllib.parse.quote_plus(query)
        url = _GNEWS_DE.format(query=encoded)
        return self._parse_rss(url, "GermanMedia", ticker=ticker)

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
                pub_dt = datetime.utcnow()

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
                "language":     "de",
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
