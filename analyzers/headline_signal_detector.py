"""
HeadlineSignalDetector – Scannt allgemeine Börsennachrichten ohne Ticker-Vorgabe.

Ziel: Aktien frühzeitig entdecken BEVOR sie auf der Watchlist stehen.
Workflow:
  1. Stündlich: allgemeine Markt-Nachrichten laden (RSS + NewsAPI + Yahoo)
  2. Ticker aus Schlagzeilen extrahieren (Regex + Firmenname-Mapping)
  3. Signal-Typ und Stärke bestimmen (M&A, FDA, Earnings-Überraschung, etc.)
  4. Starke Signale → BenchList (werden beim nächsten Zyklus von Claude bewertet)
  5. Sehr starke Signale → Telegram sofort

Signal-Kategorien (Stärke 0–1):
  ACQUISITION  0.90  "acquired", "merger", "takeover", "buyout"
  FDA_APPROVAL 0.88  "FDA approves", "approval granted", "cleared by FDA"
  EARNINGS_BIG 0.80  "beats estimates", "record earnings", "raises guidance"
  CONTRACT_WIN 0.75  "awarded contract", "wins deal", "$Xbn contract"
  BREAKOUT     0.72  "all-time high", "52-week high", "record revenue"
  SPIN_OFF     0.70  "spinoff", "spin-off", "separates", "divests"
  UPGRADE      0.65  "upgrades to buy", "price target raised", "overweight"
  DOWNGRADE    0.30  "downgrades", "price target cut", "underperform"
  EARNINGS_BAD 0.20  "misses estimates", "lowers guidance", "below expectations"
"""
from __future__ import annotations

import re
import json
import os
import time
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests

from logger import get_logger

log = get_logger(__name__)

_STATE_FILE = os.path.join("data", "headline_scanner_state.json")

# ── Signal-Definitionen ───────────────────────────────────────────────────────

@dataclass
class HeadlineSignal:
    ticker:      str
    signal_type: str
    score:       float
    headline:    str
    source:      str
    detected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


_SIGNAL_PATTERNS: List[Tuple[str, float, List[str]]] = [
    # (signal_type, score, keyword_list)
    ("ACQUISITION",   0.90, [
        "acquir", "merger", "takeover", "buyout", "to be acquired",
        "acquisition", "agreed to buy", "purchase agreement",
    ]),
    ("FDA_APPROVAL",  0.88, [
        "fda approv", "fda clears", "fda grants", "approved by fda",
        "nda approved", "bla approved", "regulatory approval",
    ]),
    ("EARNINGS_BEAT", 0.80, [
        "beats estimate", "beat estimate", "record earnings", "record revenue",
        "raises guidance", "raised guidance", "raises full-year", "raises annual",
        "record profit", "above consensus", "tops estimate", "exceeded estimate",
        "lifts guidance", "lifted guidance", "increases guidance",
    ]),
    ("CONTRACT_WIN",  0.75, [
        "awarded contract", "wins contract", "secures contract",
        "billion contract", "million contract", "government contract",
        "defense contract", "awarded deal",
    ]),
    ("BREAKOUT",      0.72, [
        "all-time high", "52-week high", "record high", "new high",
        "record revenue", "revenue record",
    ]),
    ("SPIN_OFF",      0.70, [
        "spin-off", "spinoff", "spin off", "separates unit",
        "divests", "carve-out", "ipo planned",
    ]),
    ("UPGRADE",       0.65, [
        "upgraded to buy", "upgrades to buy", "raised to buy",
        "price target raised", "price target increased", "overweight initiated",
        "outperform initiated", "strong buy",
    ]),
    ("SHORT_SQUEEZE", 0.68, [
        "short squeeze", "short interest drops", "covering shorts",
        "heavily shorted", "most shorted",
    ]),
    ("PARTNERSHIP",   0.65, [
        "partnership with", "strategic alliance", "joint venture",
        "collaboration with", "licensing agreement",
    ]),
    ("DOWNGRADE",     0.28, [
        "downgraded", "downgrade to sell", "price target cut",
        "price target lowered", "underperform", "underweight",
    ]),
    ("EARNINGS_MISS", 0.20, [
        "misses estimate", "missed estimate", "below estimate",
        "lowers guidance", "lowered guidance", "disappointing earnings",
        "below consensus", "shortfall",
    ]),
    ("RECALL",        0.15, [
        "product recall", "recalls its", "safety recall",
        "voluntary recall", "fda recall", "drug recall",
        "fda warning", "safety warning",
    ]),
]

# Ticker-Blacklist: Wörter die wie Ticker aussehen aber keine sind
_WORD_BLACKLIST = {
    "A", "I", "IT", "AT", "BE", "BY", "OR", "AND", "FOR", "THE", "IN",
    "IS", "TO", "OF", "ON", "AS", "CEO", "CFO", "COO", "IPO", "ETF",
    "USA", "GDP", "FED", "CPI", "NYSE", "SEC", "FDA", "ETF", "USD",
    "EUR", "GBP", "JPY", "AI", "EV", "US", "UK", "EU", "Q1", "Q2",
    "Q3", "Q4", "YOY", "QOQ", "YTD", "EPS", "PE", "PEG", "NDA", "BLA",
}

# Bekannte Firmenname → Ticker (ergänzt durch yfinance Lookup für unbekannte)
_NAME_TO_TICKER: Dict[str, str] = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT",
    "amazon": "AMZN", "alphabet": "GOOGL", "google": "GOOGL",
    "meta": "META", "tesla": "TSLA", "netflix": "NFLX",
    "amd": "AMD", "intel": "INTC", "qualcomm": "QCOM",
    "salesforce": "CRM", "oracle": "ORCL", "sap": "SAP.DE",
    "asml": "ASML", "lvmh": "MC.PA", "siemens": "SIE.DE",
    "rheinmetall": "RHM.DE", "airbus": "AIR.PA", "biontech": "BNTX",
    "moderna": "MRNA", "pfizer": "PFE", "merck": "MRK",
    "novo nordisk": "NVO", "eli lilly": "LLY", "abbvie": "ABBV",
    "berkshire": "BRK-B", "jpmorgan": "JPM", "goldman sachs": "GS",
    "morgan stanley": "MS", "blackrock": "BLK",
    "shopify": "SHOP", "snowflake": "SNOW", "crowdstrike": "CRWD",
    "palantir": "PLTR", "coinbase": "COIN", "robinhood": "HOOD",
    "arm holdings": "ARM", "supermicro": "SMCI",
}

# RSS-Feeds für allgemeine Börsennachrichten (kein API-Key nötig)
_RSS_FEEDS = [
    ("Reuters Business",    "https://feeds.reuters.com/reuters/businessNews"),
    ("Yahoo Finance",       "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch",         "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Seeking Alpha",       "https://seekingalpha.com/market_currents.xml"),
    ("CNBC Markets",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
    ("GlobeNewswire",       "https://www.globenewswire.com/RssFeed/industry/9550"),
    ("PR Newswire Finance", "https://www.prnewswire.com/rss/financial-news-and-business.rss"),
]

_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 StockSentimentBot/1.0"}
_MIN_SCORE_FOR_BENCH  = 0.60   # Ab hier → BenchList
_MIN_SCORE_FOR_NOTIFY = 0.85   # Ab hier → Telegram sofort
_MAX_SIGNALS_PER_RUN  = 15     # Pro Scan-Lauf max. Signale verarbeiten


class HeadlineSignalDetector:
    """
    Scannt allgemeine Marktnachrichten und leitet starke Signale
    automatisch in die BenchList weiter.
    """

    def __init__(self, state_path: str = _STATE_FILE):
        self._state_path = state_path
        os.makedirs("data", exist_ok=True)

    # ── Haupt-Methode ─────────────────────────────────────────────────────────

    def scan(self) -> List[HeadlineSignal]:
        """
        Lädt allgemeine Marktnachrichten, extrahiert Signale und gibt
        alle gefundenen Signale zurück.
        """
        headlines = self._fetch_headlines()
        if not headlines:
            log.debug("HeadlineScanner: keine Schlagzeilen geladen")
            return []

        seen_titles = self._load_seen_titles()
        new_signals: List[HeadlineSignal] = []

        for title, source in headlines:
            key = title.lower()[:80]
            if key in seen_titles:
                continue
            seen_titles.add(key)

            signal = self._detect_signal(title, source)
            if signal:
                new_signals.append(signal)
                if len(new_signals) >= _MAX_SIGNALS_PER_RUN:
                    break

        self._save_seen_titles(seen_titles)

        if new_signals:
            log.info(
                "HeadlineScanner: %d neue Signale gefunden",
                len(new_signals),
            )
        return new_signals

    def process_signals(
        self,
        signals: List[HeadlineSignal],
        notify_fn=None,
    ) -> List[str]:
        """
        Verarbeitet Signale: starke → BenchList, sehr starke → Telegram.
        Gibt Liste der hinzugefügten Ticker zurück.
        """
        if not signals:
            return []

        from analyzers.bench_list import BenchList
        bench = BenchList()
        added: List[str] = []
        notify_msgs: List[str] = []

        for sig in signals:
            if sig.score < _MIN_SCORE_FOR_BENCH:
                continue

            bench.add(
                sig.ticker,
                score=sig.score,
                reason=f"{sig.signal_type}: {sig.headline[:80]}",
            )
            added.append(sig.ticker)

            if sig.score >= _MIN_SCORE_FOR_NOTIFY and notify_fn:
                emoji = _signal_emoji(sig.signal_type)
                notify_msgs.append(
                    f"{emoji} <b>{sig.ticker}</b> – {sig.signal_type}\n"
                    f"<i>{sig.headline[:120]}</i>\n"
                    f"Quelle: {sig.source} | Score: {sig.score:.2f}"
                )

        if notify_msgs and notify_fn:
            header = "📰 <b>Headline-Scanner – Starke Signale</b>\n\n"
            notify_fn(header + "\n\n".join(notify_msgs[:5]))

        if added:
            log.info(
                "HeadlineScanner → BenchList: %s",
                ", ".join(added[:10]),
            )

        return added

    # ── Schlagzeilen laden ────────────────────────────────────────────────────

    def _fetch_headlines(self) -> List[Tuple[str, str]]:
        """Lädt Schlagzeilen aus RSS-Feeds + optional NewsAPI."""
        results: List[Tuple[str, str]] = []

        for feed_name, url in _RSS_FEEDS:
            try:
                resp = requests.get(url, timeout=_TIMEOUT, headers=_HEADERS)
                if resp.status_code != 200:
                    continue
                items = self._parse_rss(resp.text, feed_name)
                results.extend(items)
            except Exception as e:
                log.debug("RSS %s fehlgeschlagen: %s", feed_name, e)

        # NewsAPI allgemeine Finanz-Schlagzeilen (wenn Key vorhanden)
        try:
            from config import config
            if config.newsapi_key:
                from collectors.news_api_collector import NewsAPICollector
                items = NewsAPICollector().collect_general(
                    "stock market earnings acquisition FDA merger",
                    max_results=30, days_back=1,
                )
                for item in items:
                    t = item.get("title") or ""
                    if t:
                        results.append((t, item.get("source", "NewsAPI")))
        except Exception as e:
            log.debug("NewsAPI-Headline-Fetch fehlgeschlagen: %s", e)

        log.debug("HeadlineScanner: %d Schlagzeilen geladen", len(results))
        return results

    def _parse_rss(self, xml_text: str, source: str) -> List[Tuple[str, str]]:
        """Extrahiert Titel aus RSS-XML."""
        import xml.etree.ElementTree as ET
        results = []
        try:
            root = ET.fromstring(xml_text)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            # RSS 2.0
            for item in root.iter("item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    results.append((title_el.text.strip(), source))
            # Atom
            for entry in root.findall(".//atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                if title_el is not None and title_el.text:
                    results.append((title_el.text.strip(), source))
        except Exception:
            pass
        return results

    # ── Signal-Erkennung ──────────────────────────────────────────────────────

    def _detect_signal(self, headline: str, source: str) -> Optional[HeadlineSignal]:
        """Prüft Schlagzeile auf Signal-Muster und extrahiert Ticker."""
        lower = headline.lower()

        matched_type = None
        matched_score = 0.0
        for sig_type, score, keywords in _SIGNAL_PATTERNS:
            if any(kw in lower for kw in keywords):
                if score > matched_score:
                    matched_type = sig_type
                    matched_score = score

        if not matched_type:
            return None

        ticker = self._extract_ticker(headline)
        if not ticker:
            return None

        return HeadlineSignal(
            ticker=ticker,
            signal_type=matched_type,
            score=matched_score,
            headline=headline,
            source=source,
        )

    def _extract_ticker(self, headline: str) -> Optional[str]:
        """
        Extrahiert Ticker aus Schlagzeile.
        Strategie: explizite $TICKER > Grossbuchstaben-Muster > Firmenname-Mapping.
        """
        # 1. Explizite $TICKER Notation (Twitter-Style)
        dollar_match = re.search(r'\$([A-Z]{1,5}(?:\.[A-Z]{1,2})?)', headline)
        if dollar_match:
            t = dollar_match.group(1)
            if t not in _WORD_BLACKLIST:
                return t

        # 2. Ticker in Klammern: "Company Name (TICK)"  oder  "Company (NYSE: TICK)"
        paren_match = re.search(
            r'\((?:NYSE:|NASDAQ:|XTRA:|ETR:)?\s*([A-Z]{2,5}(?:[.\-][A-Z]{1,2})?)\)',
            headline
        )
        if paren_match:
            t = paren_match.group(1)
            if t not in _WORD_BLACKLIST:
                return t

        # 3. Firmenname-Mapping (Wortgrenzen-Check verhindert Substring-Fehler)
        lower = headline.lower()
        for name, ticker in _NAME_TO_TICKER.items():
            # Wortgrenze simulieren: Leerzeichen oder Satzanfang/-ende
            pattern = r'(?<![a-z])' + re.escape(name) + r'(?![a-z])'
            if re.search(pattern, lower):
                return ticker

        # 4. Grossbuchstaben-Muster (letzter Ausweg, fehleranfällig)
        words = re.findall(r'\b([A-Z]{2,5})\b', headline)
        for word in words:
            if word not in _WORD_BLACKLIST and len(word) >= 2:
                return word

        return None

    # ── State-Persistenz ─────────────────────────────────────────────────────

    def _load_seen_titles(self) -> set:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                data = json.load(f)
                # Nur letzte 24h behalten
                cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
                return {k for k, v in data.items() if v >= cutoff}
        except Exception:
            return set()

    def _save_seen_titles(self, seen: set) -> None:
        dirpath = os.path.dirname(self._state_path) or "."
        os.makedirs(dirpath, exist_ok=True)
        now = datetime.utcnow().isoformat()
        try:
            fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: now for k in seen}, f)
            os.replace(tmp, self._state_path)
        except Exception as e:
            log.debug("HeadlineScanner: State speichern fehlgeschlagen: %s", e)


def _signal_emoji(signal_type: str) -> str:
    return {
        "ACQUISITION":   "🤝",
        "FDA_APPROVAL":  "💊",
        "EARNINGS_BEAT": "📈",
        "CONTRACT_WIN":  "📋",
        "BREAKOUT":      "🚀",
        "SPIN_OFF":      "✂️",
        "UPGRADE":       "⬆️",
        "SHORT_SQUEEZE": "⚡",
        "PARTNERSHIP":   "🔗",
        "DOWNGRADE":     "⬇️",
        "EARNINGS_MISS": "📉",
        "RECALL":        "⚠️",
    }.get(signal_type, "📰")
