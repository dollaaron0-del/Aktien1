"""
European/German news collector via Google News RSS in multiple languages.
For tickers ending in .DE, .PA, .AS, .MI etc., German/French/Dutch sources
deliver more relevant signals than English-only feeds.
"""
import urllib.parse
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
import requests
from system.http import http_get


EXCHANGE_LANG = {
    ".DE": ("de", "DE"), ".F": ("de", "DE"), ".MU": ("de", "DE"), ".BE": ("de", "DE"),
    ".VI": ("de", "AT"),
    ".SW": ("de", "CH"),
    ".PA": ("fr", "FR"), ".BR": ("fr", "BE"),
    ".AS": ("nl", "NL"),
    ".MI": ("it", "IT"),
    ".MC": ("es", "ES"),
    ".ST": ("sv", "SE"),
    ".HE": ("fi", "FI"),
    ".OL": ("no", "NO"),
    ".L": ("en", "GB"),
}


class EuropeanNewsCollector:
    def __init__(self, lookback_hours: int = 72, max_items: int = 15):
        self.lookback_hours = lookback_hours
        self.max_items = max_items

    def collect(self, ticker: str) -> List[Dict]:
        suffix = self._get_suffix(ticker)
        if not suffix:
            return []
        lang, country = EXCHANGE_LANG.get(suffix, ("en", "US"))
        query = ticker.replace(suffix, "")
        url = (
            f"https://news.google.com/rss/search?"
            f"q={urllib.parse.quote(query + ' Aktie')}"
            f"&hl={lang}&gl={country}&ceid={country}:{lang}"
        )
        try:
            r = http_get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return []
            return self._parse_rss(r.text, ticker, lang)
        except Exception:
            return []

    def _get_suffix(self, ticker: str) -> Optional[str]:
        if "." not in ticker:
            return None
        return "." + ticker.split(".")[-1]

    def _parse_rss(self, xml_text: str, ticker: str, lang: str) -> List[Dict]:
        items: List[Dict] = []
        cutoff = datetime.utcnow() - timedelta(hours=self.lookback_hours)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []
        channel = root.find("channel")
        if channel is None:
            return []
        for item in channel.findall("item")[: self.max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate") or ""
            desc = item.findtext("description") or ""
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            try:
                pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
            except Exception:
                pub_dt = datetime.utcnow()
            if pub_dt < cutoff:
                continue
            items.append({
                "title": title,
                "summary": desc[:500],
                "url": link,
                "published": pub_dt.isoformat(),
                "source": f"european_news_{lang}",
            })
        return items
