"""
NewsTrustFilter – bewertet Nachrichten-Glaubwürdigkeit und erkennt Manipulation.

Prüft fünf Dimensionen:
1. Quellen-Reputation  – bekannte Nachrichtenagenturen > anonyme Blogs
2. Domain-Spoofing     – bloomberg-news.co ≠ bloomberg.com → sofort verworfen
3. Koordinierungs-Muster – viele identische Artikel in kurzer Zeit = Pump-Signal
4. Manipulations-Indikatoren – extreme Sprache, anonyme Quellen, Preisziele in Headline
5. Einzelquellen-Alarm – große Kursbehauptung die nur eine einzige Quelle hat
6. Recycelte News      – alter Artikel neu veröffentlicht (Zeitstempel-Anomalie)
7. KI-Muster           – gleichförmige Massenpublikation ohne redaktionelle Tiefe

Gibt pro Artikel einen Trust-Score zurück (0.0–1.0) und filtert Artikel
unter dem konfigurierbaren Schwellwert (Standard: 0.25) vor Claude heraus.

Kein API-Aufruf, rein regelbasiert → null Mehrkosten.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Set
from urllib.parse import urlparse

from logger import get_logger

log = get_logger(__name__)

# ── Quellen-Reputation ─────────────────────────────────────────────────────────
# Score 1.0 = maximale Vertrauenswürdigkeit (Primärquellen)
_SOURCE_SCORES: Dict[str, float] = {
    # Tier 1 – Primärquellen / Nachrichtenagenturen
    "reuters.com":          1.0,
    "bloomberg.com":        1.0,
    "apnews.com":           1.0,
    "ft.com":               0.95,
    "wsj.com":              0.95,
    "sec.gov":              1.0,
    "federalreserve.gov":   1.0,
    "ecb.europa.eu":        1.0,
    "bls.gov":              1.0,     # Bureau of Labor Statistics
    "census.gov":           1.0,
    "aaii.com":             0.80,   # AAII Investor Sentiment Survey
    "dgap.de":              0.95,   # Deutsche Pflichtmitteilungen / Ad-hoc
    "presseportal.de":      0.80,   # Deutsche Pressemitteilungen
    "boerse.de":            0.75,   # Deutsche Börsennachrichten
    "finanznachrichten.de": 0.75,   # Deutsche Finanznachrichten
    "handelsblatt.com":     0.90,   # Führendes deutsches Wirtschaftsblatt
    "manager-magazin.de":   0.85,   # Deutsches Wirtschaftsmagazin
    "boerse-frankfurt.de":  0.90,   # Frankfurter Wertpapierbörse
    # Tier 2 – Etablierte Finanzmedien
    "cnbc.com":             0.85,
    "marketwatch.com":      0.85,
    "barrons.com":          0.85,
    "seekingalpha.com":     0.70,
    "thestreet.com":        0.75,
    "investopedia.com":     0.70,
    "fool.com":             0.65,
    "zacks.com":            0.75,
    "benzinga.com":         0.70,
    "yahoo.com":            0.70,
    "finance.yahoo.com":    0.70,
    "businesswire.com":     0.80,   # PR Newswire – offiziell aber PR
    "prnewswire.com":       0.75,
    "globenewswire.com":    0.75,
    # Tier 3 – Social / Community (niedriger Basiswert)
    "reddit.com":           0.40,
    "twitter.com":          0.35,
    "x.com":                0.35,
    "stocktwits.com":       0.40,
}

# Bekannte Marken die häufig gefälscht werden (Domain-Spoofing-Schutz)
# Wenn der Artikel-Domain diese Marke enthält aber NICHT die echte Domain ist → Betrug
_TRUSTED_BRANDS: Dict[str, str] = {
    "reuters":    "reuters.com",
    "bloomberg":  "bloomberg.com",
    "wsj":        "wsj.com",
    "ft":         "ft.com",
    "cnbc":       "cnbc.com",
    "sec":        "sec.gov",
    "apnews":     "apnews.com",
    "marketwatch": "marketwatch.com",
    "barrons":    "barrons.com",
}

# ── Manipulations-Signale in Headlines ────────────────────────────────────────
_PUMP_PATTERNS = [
    r"\b(moon|moonshot|rocket|100x|1000%|guaranteed|secret|insiders know)\b",
    r"\bwhy .+ will (explode|skyrocket|moon)\b",
    r"\b(BREAKING|URGENT|EXCLUSIVE):\s+.+(buy|invest|opportunity)\b",
    r"(price target|PT) \$\d+",    # Anonyme Preisziele in Headlines
    r"\b(once in a lifetime|can't miss|act now|limited time)\b",
]
_DUMP_PATTERNS = [
    r"\b(massive sell-off|collapse|crash|bankrupt|fraud|scam|ponzi)\b",
    r"\bshort .+ to \$0\b",
    r"\bDO NOT (buy|hold|invest)\b",
]

# Muster die auf KI-Massenproduktion hinweisen (gleichförmige Struktur)
_AI_GEN_PATTERNS = [
    r"^(in (a|the) (recent|latest|new) (report|filing|statement|announcement),)",
    r"(it is (worth|important) (noting|mentioning) that)",
    r"(as of (the time of|this) writing)",
    r"(this article (was|is) (generated|written|produced) (by|with) (ai|artificial intelligence))",
]

# Ab wie vielen gleichen Quellen in einem Batch gilt es als Koordinierung
_COORDINATION_THRESHOLD = int(os.getenv("NEWS_COORDINATION_THRESHOLD", "5"))
# Max. Alter eines Artikels in Stunden (ältere = möglicherweise recycelt)
_MAX_AGE_HOURS = float(os.getenv("NEWS_MAX_AGE_HOURS", "48"))
# Artikel älter als _STALE_AGE_HOURS bekommen erhöhte Penalty
_STALE_AGE_HOURS = 48.0
# Artikel älter als _DEAD_AGE_HOURS werden sofort mit Score 0.0 markiert
_DEAD_AGE_HOURS = 168.0   # 7 Tage

# Ticker-zu-Firmenname-Mapping für Relevanz-Filter (Top-50 Tickers)
_TICKER_ALIASES: Dict[str, List[str]] = {
    "AAPL":  ["Apple"],
    "MSFT":  ["Microsoft"],
    "GOOGL": ["Google", "Alphabet"],
    "GOOG":  ["Google", "Alphabet"],
    "AMZN":  ["Amazon"],
    "NVDA":  ["Nvidia", "NVIDIA"],
    "TSLA":  ["Tesla"],
    "META":  ["Meta", "Facebook"],
    "NFLX":  ["Netflix"],
    "AMD":   ["AMD", "Advanced Micro"],
    "INTC":  ["Intel"],
    "QCOM":  ["Qualcomm"],
    "AVGO":  ["Broadcom"],
    "ORCL":  ["Oracle"],
    "CRM":   ["Salesforce"],
    "ADBE":  ["Adobe"],
    "IBM":   ["IBM"],
    "CSCO":  ["Cisco"],
    "NOW":   ["ServiceNow"],
    "JPM":   ["JPMorgan", "JP Morgan"],
    "BAC":   ["Bank of America"],
    "WFC":   ["Wells Fargo"],
    "GS":    ["Goldman Sachs", "Goldman"],
    "MS":    ["Morgan Stanley"],
    "JNJ":   ["Johnson & Johnson", "Johnson and Johnson"],
    "PFE":   ["Pfizer"],
    "MRK":   ["Merck"],
    "ABBV":  ["AbbVie"],
    "LLY":   ["Eli Lilly", "Lilly"],
    "BMY":   ["Bristol-Myers", "Bristol Myers"],
    "UNH":   ["UnitedHealth", "United Health"],
    "XOM":   ["ExxonMobil", "Exxon Mobil", "Exxon"],
    "CVX":   ["Chevron"],
    "HD":    ["Home Depot"],
    "WMT":   ["Walmart"],
    "COST":  ["Costco"],
    "KO":    ["Coca-Cola", "Coca Cola"],
    "PEP":   ["PepsiCo", "Pepsi"],
    "MCD":   ["McDonald's", "McDonalds"],
    "SBUX":  ["Starbucks"],
    "NKE":   ["Nike"],
    "DIS":   ["Disney"],
    "V":     ["Visa"],
    "MA":    ["Mastercard"],
    "PYPL":  ["PayPal"],
    "SQ":    ["Block", "Square"],
    "SPY":   ["S&P 500", "SPY"],
    "QQQ":   ["Nasdaq", "QQQ"],
}


def _min_trust_score() -> float:
    """Liest NEWS_MIN_TRUST_SCORE dynamisch – reagiert auf .env-Änderungen ohne Neustart."""
    return float(os.getenv("NEWS_MIN_TRUST_SCORE", "0.15"))


class NewsTrustFilter:

    def filter(self, items: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        Filtert eine Liste von Nachrichtenartikeln nach Glaubwürdigkeit.
        Gibt (gefilterte_liste, bericht) zurück.
        """
        if not items:
            return [], {}

        scored = [(item, self._score(item)) for item in items]

        # Koordinierungs-Check
        domains = [self._extract_domain(item.get("url") or item.get("source") or "") for item in items]
        domain_counts = Counter(d for d in domains if d)
        coordinated_domains = {d for d, c in domain_counts.items() if c >= _COORDINATION_THRESHOLD}

        if coordinated_domains:
            log.info(
                "NewsTrust: Koordinierungs-Muster – %s (je >=%d Artikel)",
                ", ".join(coordinated_domains), _COORDINATION_THRESHOLD,
            )
            new_scored = []
            for item, score in scored:
                domain = self._extract_domain(item.get("url") or item.get("source") or "")
                if domain in coordinated_domains:
                    score = max(0.0, score - 0.20)
                new_scored.append((item, score))
            scored = new_scored

        # Einzelquellen-Alarm: Behauptungen die nur 1 Quelle hat werden abgewertet
        scored = self._penalize_single_source(scored)

        _threshold = _min_trust_score()
        kept    = [(item, s) for item, s in scored if s >= _threshold]
        dropped = [(item, s) for item, s in scored if s < _threshold]

        spoof_titles   = [item.get("title", "")[:60] for item, s in scored
                          if self._is_domain_spoof(item)]
        recycled_titles = [item.get("title", "")[:60] for item, s in scored
                           if self._is_recycled(item)]

        if dropped:
            log.info(
                "NewsTrust: %d/%d Artikel gefiltert (Trust < %.2f): %s",
                len(dropped), len(items), _threshold,
                ", ".join((item.get("title") or "")[:40] for item, _ in dropped[:3]),
            )
        if spoof_titles:
            log.warning("NewsTrust: %d Domain-Spoofing-Verdacht: %s",
                        len(spoof_titles), spoof_titles[:2])
        if recycled_titles:
            log.info("NewsTrust: %d recycelte/alte Artikel erkannt", len(recycled_titles))

        report = {
            "total":             len(items),
            "kept":              len(kept),
            "dropped":           len(dropped),
            "coordinated":       list(coordinated_domains),
            "domain_spoofs":     len(spoof_titles),
            "recycled":          len(recycled_titles),
            "avg_trust":         round(sum(s for _, s in scored) / len(scored), 2) if scored else 0.0,
            "low_trust_titles":  [(item.get("title") or "")[:60] for item, _ in dropped[:5]],
        }

        return [item for item, _ in kept], report

    def score_item(self, item: Dict) -> float:
        return self._score(item)

    def filter_by_relevance(self, items: List[Dict], ticker: str) -> List[Dict]:
        """
        Filtert Artikel nach Ticker-Relevanz.

        Behält nur Artikel die den Ticker-Namen oder einen bekannten Firmennamen
        im Titel oder Text enthalten. Artikel ohne URL werden behalten wenn kein
        Ticker-Mapping vorhanden ist (fail-safe).

        Args:
            items:  Liste von Nachrichtenartikeln
            ticker: Aktien-Ticker (z.B. "AAPL")

        Returns:
            Gefilterte Liste (nur relevante Artikel)
        """
        ticker_upper = ticker.upper()
        aliases      = _TICKER_ALIASES.get(ticker_upper, [])

        # Alle Suchbegriffe: Ticker selbst + bekannte Firmennamen
        search_terms = [ticker_upper.lower()] + [a.lower() for a in aliases]

        if not search_terms:
            # Kein Mapping vorhanden → alle Artikel behalten (fail-safe)
            return items

        kept: List[Dict] = []
        for item in items:
            title = (item.get("title") or "").lower()
            text  = (item.get("text") or item.get("body") or item.get("summary") or "").lower()
            combined = title + " " + text

            if any(term in combined for term in search_terms):
                kept.append(item)
            elif not item.get("url"):
                # Artikel ohne URL behalten (kein verlässlicher Relevanz-Check möglich)
                kept.append(item)

        dropped = len(items) - len(kept)
        if dropped > 0:
            log.info(
                "NewsTrust.filter_by_relevance [%s]: %d/%d Artikel als irrelevant entfernt",
                ticker, dropped, len(items),
            )
        return kept

    # ── Intern ───────────────────────────────────────────────────────────────

    def _score(self, item: Dict) -> float:
        title  = (item.get("title")  or "").lower()
        source = (item.get("source") or item.get("url") or "").lower()

        # 1. Domain-Spoofing → sofort 0.0 (wird verworfen)
        if self._is_domain_spoof(item):
            log.warning("NewsTrust: Domain-Spoofing erkannt – '%s' (%s)",
                        item.get("title", "")[:50], source[:60])
            return 0.0

        # 2. Quellen-Reputation
        domain     = self._extract_domain(source)
        base_score = _SOURCE_SCORES.get(domain, 0.55)

        # 3. Manipulations-Muster
        pump_hits = sum(1 for p in _PUMP_PATTERNS if re.search(p, title, re.I))
        dump_hits = sum(1 for p in _DUMP_PATTERNS if re.search(p, title, re.I))
        manip_penalty = (pump_hits + dump_hits) * 0.15

        # 4. Anonyme / unsichere Quellen
        if any(x in source for x in ("blog", "substack", "medium.com", "wordpress")):
            base_score = min(base_score, 0.45)

        # 5. Sehr kurze Titel → Clickbait-Verdacht
        if len(title) < 50 and domain not in _SOURCE_SCORES:
            base_score = min(base_score, 0.40)

        # 6. Recycelte News → Abzug; Artikel > 7 Tage → sofort Score 0.0
        if self._is_dead_old(item):
            return 0.0
        if self._is_recycled(item):
            base_score = max(0.0, base_score - 0.35)

        # 7. KI-generierter Text → leichter Abzug
        body = (item.get("body") or item.get("text") or item.get("summary") or "").lower()
        if self._looks_ai_generated(title + " " + body):
            base_score = max(0.0, base_score - 0.10)

        score = max(0.0, min(1.0, base_score - manip_penalty))
        return round(score, 3)

    def _is_domain_spoof(self, item: Dict) -> bool:
        """
        Erkennt gefälschte Domains die echte Marken imitieren.
        z.B. 'bloomberg-finance.net', 'reuters-today.com' → Spoof
        """
        url = (item.get("url") or item.get("source") or "").lower()
        domain = self._extract_domain(url)
        if not domain:
            return False
        for brand, real_domain in _TRUSTED_BRANDS.items():
            if brand in domain and domain != real_domain and not domain.endswith(f".{real_domain}"):
                return True
        return False

    def _is_recycled(self, item: Dict) -> bool:
        """
        Erkennt alte Artikel die mit neuem Datum republiziert werden.
        Prüft: published_at vs. fetched_at (wenn vorhanden) > MAX_AGE_HOURS.

        Penalty-Stufen:
          > 48h:  -0.35 (wird in _score() angewendet)
          > 7d:   sofort Score 0.0 (direkt hier markiert über _dead_age Flag)
        """
        published = item.get("published_at") or item.get("published") or item.get("date")
        if not published:
            return False
        try:
            if isinstance(published, str):
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            else:
                return False
            now = datetime.now(timezone.utc)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_hours = (now - pub_dt).total_seconds() / 3600
            return age_hours > _MAX_AGE_HOURS
        except Exception:
            return False

    def _is_dead_old(self, item: Dict) -> bool:
        """Artikel älter als 7 Tage (168h) werden sofort verworfen (Score 0.0)."""
        published = item.get("published_at") or item.get("published") or item.get("date")
        if not published:
            return False
        try:
            if isinstance(published, str):
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            else:
                return False
            now = datetime.now(timezone.utc)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_hours = (now - pub_dt).total_seconds() / 3600
            return age_hours > _DEAD_AGE_HOURS
        except Exception:
            return False

    def _looks_ai_generated(self, text: str) -> bool:
        """Erkennt typische Muster von KI-generierten Massenartikeln."""
        hits = sum(1 for p in _AI_GEN_PATTERNS if re.search(p, text, re.I))
        return hits >= 2

    def _penalize_single_source(
        self, scored: List[Tuple[Dict, float]]
    ) -> List[Tuple[Dict, float]]:
        """
        Wenn eine Behauptung (ähnlicher Titel) nur von einer einzigen
        niedrig-vertrauenswürdigen Quelle kommt → zusätzlicher Abzug.
        """
        # Gruppen nach vereinfachtem Titel (erste 40 Zeichen)
        title_groups: Dict[str, List[int]] = {}
        for i, (item, _) in enumerate(scored):
            key = (item.get("title") or "")[:40].lower().strip()
            if key:
                title_groups.setdefault(key, []).append(i)

        result = list(scored)
        for key, indices in title_groups.items():
            if len(indices) == 1:
                idx = indices[0]
                item, score = result[idx]
                domain = self._extract_domain(
                    item.get("url") or item.get("source") or ""
                )
                # Nur abwerten wenn die einzige Quelle kein Tier-1/2-Medium ist
                src_score = _SOURCE_SCORES.get(domain, 0.55)
                if src_score < 0.75:
                    result[idx] = (item, max(0.0, score - 0.15))
        return result

    @staticmethod
    def _extract_domain(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            domain = parsed.netloc.lower().lstrip("www.")
            return domain
        except Exception:
            return url.lower()[:50]
