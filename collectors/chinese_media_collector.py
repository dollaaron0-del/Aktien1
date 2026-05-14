"""
ChineseMediaCollector – sammelt chinesische Staatsmedienmeldungen.
China-Regulierung kann US-Aktien massiv treffen (Alibaba, Apple, Tesla).
Quellen: Xinhua RSS (englisch), Global Times RSS, People's Daily RSS.
Warnsignale: "Rectification", "Investigation", "National security".
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from email.utils import parsedate_to_datetime

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

_CHINESE_SOURCES = [
    {
        "name": "Xinhua News",
        "url":  "http://www.xinhuanet.com/english/rss/businessrss.xml",
    },
    {
        "name": "Global Times Business",
        "url":  "https://www.globaltimes.cn/rss/biztech.xml",
    },
    {
        "name": "Global Times Economy",
        "url":  "https://www.globaltimes.cn/rss/economy.xml",
    },
    {
        "name": "South China Morning Post Markets",
        "url":  "https://www.scmp.com/rss/5/feed",
    },
]

# Ticker → Stichwörter die auf China-Exposure hinweisen
_TICKER_CHINA_KEYWORDS = {
    "AAPL": ["Apple", "iPhone", "Foxconn", "China"],
    "TSLA": ["Tesla", "Shanghai", "Gigafactory", "EV"],
    "NVDA": ["NVIDIA", "chip", "export", "semiconductor"],
    "QCOM": ["Qualcomm", "China", "5G"],
    "MU": ["Micron", "DRAM", "China"],
    "BABA": ["Alibaba", "Jack Ma", "Ant Group"],
    "BIDU": ["Baidu", "AI", "China"],
    "JD": ["JD.com", "e-commerce"],
    "NIO": ["NIO", "electric", "China"],
}

# Warnsignale die auf Regulierungsrisiko hinweisen
_WARNING_TERMS = [
    "investigation", "rectification", "national security", "data security",
    "fine", "penalty", "ban", "sanction", "antitrust", "monopoly",
    "Untersuchung", "Sicherheit", "regulat",
]

# Positive Signale
_POSITIVE_TERMS = [
    "expansion", "partnership", "approval", "license", "investment",
    "growth", "record", "subsidy",
]


class ChineseMediaCollector:
    """Überwacht chinesische Staatsmedien auf Regulierungsrisiken."""

    def collect(self, ticker: str, lookback_days: int = 14) -> List[Dict]:
        results = []
        cutoff  = datetime.utcnow() - timedelta(days=lookback_days)
        keywords = _TICKER_CHINA_KEYWORDS.get(ticker.upper(), [ticker, "China"])

        for source in _CHINESE_SOURCES:
            try:
                resp = requests.get(source["url"], headers=_HEADERS, timeout=10)
                if resp.status_code != 200:
                    continue

                try:
                    root = ET.fromstring(resp.content)
                except ET.ParseError:
                    continue

                for item in root.iter("item"):
                    title   = (item.findtext("title") or "").strip()
                    link    = (item.findtext("link") or "").strip()
                    summary = (item.findtext("description") or "").strip()
                    pub     = item.findtext("pubDate") or ""

                    combined = (title + " " + summary)
                    combined_lower = combined.lower()

                    if not any(kw.lower() in combined_lower for kw in keywords):
                        continue

                    try:
                        pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pub_dt = datetime.utcnow()

                    is_warning  = any(t in combined_lower for t in _WARNING_TERMS)
                    is_positive = any(t in combined_lower for t in _POSITIVE_TERMS)

                    if is_warning:
                        tag      = "⚠️ CHINA REGULIERUNGSRISIKO"
                        priority = "HIGH"
                    elif is_positive:
                        tag      = "✅ CHINA POSITIV"
                        priority = "MEDIUM"
                    else:
                        tag      = "ℹ️ CHINA NEWS"
                        priority = "LOW"

                    results.append({
                        "source":       f"Chinesische Staatsmedien ({source['name']})",
                        "ticker":       ticker,
                        "title":        f"{tag}: {title[:70]}",
                        "text":         (
                            f"Chinesische Staatsmedienmeldung zu {ticker}: {title}. "
                            f"{summary[:400] if summary else ''}"
                        ),
                        "url":          link,
                        "published_at": pub_dt.isoformat(),
                        "priority":     priority,
                    })
            except Exception:
                continue

        return results
