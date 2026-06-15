"""
Press Release Wire Collector – GlobeNewswire, PRNewswire, BusinessWire

Warum Pressemitteilungen eine Frühquelle sind:
  Unternehmen veröffentlichen Material Events zuerst über Presseagenturen
  (GlobeNewswire, PRNewswire, BusinessWire), bevor Redaktionen reagieren.
  Typischer Zeitvorsprung gegenüber Yahoo Finance / Reuters: 30 Min – 4 Stunden.

Quellen:
  - GlobeNewswire:   RSS-Feed mit Firmenname-Suche (am besten strukturiert)
  - PRNewswire:      Google News RSS-Suche (kein eigenes Such-API)
  - BusinessWire:    Google News RSS-Suche

Strategie: Firmenname wird via yfinance aufgelöst, dann parallele RSS-Suche.
"""

import requests
from system.http import http_get
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
import yfinance as yf

_TIMEOUT = 12
_HEADERS = {"User-Agent": "StockSentimentBot/1.0 (educational use)"}

# GlobeNewswire company RSS (most structured)
_GLOBE_SEARCH = (
    "https://www.globenewswire.com/RssFeed/company/{company_encoded}"
)
# Google News RSS: covers PRNewswire + BusinessWire + many other wires
_GNEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q={query}+site:prnewswire.com+OR+site:businesswire.com"
    "+OR+site:globenewswire.com&hl=en-US&gl=US&ceid=US:en"
)


class WireCollector:
    """
    Fetches press releases from GlobeNewswire and PR/Business wire services
    via Google News RSS. Returns press releases that typically appear hours
    before mainstream financial media coverage.
    """

    def __init__(self, lookback_days: int = 7):
        self.lookback_days = lookback_days
        self._cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    def collect(self, ticker: str) -> List[Dict]:
        company_name = self._get_company_name(ticker)
        if not company_name:
            return []

        items: List[Dict] = []
        items += self._fetch_globenewswire(ticker, company_name)
        items += self._fetch_gnews_wires(ticker, company_name)

        # Deduplicate by title
        seen: set = set()
        unique = []
        for item in items:
            key = (item.get("title") or "").lower()[:80]
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    # ── GlobeNewswire ─────────────────────────────────────────────────────────

    def _fetch_globenewswire(self, ticker: str, company_name: str) -> List[Dict]:
        """
        GlobeNewswire has per-company RSS feeds by exact company name.
        URL: https://www.globenewswire.com/RssFeed/company/{encoded_name}
        """
        import urllib.parse
        encoded = urllib.parse.quote_plus(company_name)
        url = _GLOBE_SEARCH.format(company_encoded=encoded)
        return self._parse_rss(ticker, url, source="GlobeNewswire")

    # ── Google News RSS (PRNewswire + BusinessWire) ───────────────────────────

    def _fetch_gnews_wires(self, ticker: str, company_name: str) -> List[Dict]:
        """
        Uses Google News RSS to search for press releases from wire services.
        Covers PRNewswire, BusinessWire, and GlobeNewswire in one call.
        """
        import urllib.parse
        # Quote with ticker for disambiguation (e.g. "Apple AAPL")
        query = urllib.parse.quote_plus(f'"{company_name}" {ticker}')
        url = _GNEWS_RSS.format(query=query)
        return self._parse_rss(ticker, url, source="PressRelease-Wire")

    # ── RSS parser ────────────────────────────────────────────────────────────

    def _parse_rss(self, ticker: str, url: str, source: str) -> List[Dict]:
        try:
            resp = http_get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception:
            return []

        results = []
        try:
            root = ET.fromstring(resp.content)
            # Handle both RSS and Atom namespaces
            ns = ""
            if root.tag.startswith("{"):
                ns_uri = root.tag.split("}")[0].strip("{")
                ns = f"{{{ns_uri}}}"

            items = root.findall(f".//{ns}item") or root.findall(".//item")

            for item in items[:20]:
                title   = self._text(item, "title")
                link    = self._text(item, "link")
                pub_raw = self._text(item, "pubDate")
                desc    = self._text(item, "description") or ""

                if not title:
                    continue

                pub_dt = self._parse_pub_date(pub_raw)
                if pub_dt and pub_dt < self._cutoff:
                    continue

                # Clean HTML from description
                desc_clean = self._strip_html(desc)[:400]
                date_str = pub_dt.strftime("%Y-%m-%d") if pub_dt else (pub_raw or "")[:10]

                # Detect wire source from URL
                actual_source = source
                if link:
                    if "prnewswire" in link:
                        actual_source = "PRNewswire"
                    elif "businesswire" in link:
                        actual_source = "BusinessWire"
                    elif "globenewswire" in link:
                        actual_source = "GlobeNewswire"

                results.append({
                    "source": actual_source,
                    "ticker": ticker,
                    "title": title,
                    "text": (
                        f"Pressemitteilung ({actual_source}): {title}. "
                        f"{desc_clean}. "
                        f"Veröffentlicht: {date_str}. "
                        f"Pressemitteilungen erscheinen 30 Min–4 Stunden früher als "
                        f"Nachrichtenartikel auf Yahoo Finance oder Reuters."
                    ),
                    "published_at": date_str,
                    "url": link or "",
                })

        except ET.ParseError:
            pass

        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_company_name(ticker: str) -> Optional[str]:
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName") or info.get("shortName") or ""
            # Strip legal suffixes for better search match
            for suffix in [", Inc.", " Inc.", ", LLC", " LLC", " Corp.", ", Corp.",
                           " Corporation", " Limited", " Ltd.", " Holdings", " Co."]:
                name = name.replace(suffix, "")
            return name.strip() or None
        except Exception:
            return None

    @staticmethod
    def _text(element, tag: str) -> Optional[str]:
        node = element.find(tag)
        if node is not None and node.text:
            return node.text.strip()
        return None

    @staticmethod
    def _parse_pub_date(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return parsedate_to_datetime(raw).replace(tzinfo=None)
        except Exception:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw[:19], fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _strip_html(text: str) -> str:
        import re
        return re.sub(r"<[^>]+>", " ", text).strip()
