"""
NewsTrustFilter – bewertet Nachrichten-Glaubwürdigkeit und erkennt Manipulation.

Prüft drei Dimensionen:
1. Quellen-Reputation  – bekannte Nachrichtenagenturen > anonyme Blogs
2. Koordinierungs-Muster – viele identische Artikel in kurzer Zeit = Pump-Signal
3. Manipulations-Indikatoren – extreme Sprache, anonyme Quellen, Preisziele in Headline

Gibt pro Artikel einen Trust-Score zurück (0.0–1.0) und filtert Artikel
unter dem konfigurierbaren Schwellwert (Standard: 0.25) vor Claude heraus.

Kein API-Aufruf, rein regelbasiert → null Mehrkosten.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import List, Dict, Tuple
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

# ── Manipulations-Signale in Headlines ────────────────────────────────────────
_PUMP_PATTERNS = [
    r"\b(moon|moonshot|rocket|100x|1000%|guaranteed|secret|insiders know)\b",
    r"\bwhy .+ will (explode|skyrocket|moon)\b",
    r"\b(BREAKING|URGENT|EXCLUSIVE):\s+.+(buy|invest|opportunity)\b",
    r"(price target|PT) \$\d+",    # Anonyme Preisziele in Headlines
]
_DUMP_PATTERNS = [
    r"\b(massive sell-off|collapse|crash|bankrupt|fraud|scam|ponzi)\b",
    r"\bshort .+ to \$0\b",
    r"\bDO NOT (buy|hold|invest)\b",
]

# Minimaler Trust-Score damit ein Artikel an Claude weitergegeben wird
_MIN_TRUST_SCORE = float(os.getenv("NEWS_MIN_TRUST_SCORE", "0.25"))
# Ab wie vielen gleichen Quellen in einem Batch gilt es als Koordinierung
_COORDINATION_THRESHOLD = int(os.getenv("NEWS_COORDINATION_THRESHOLD", "5"))


class NewsTrustFilter:

    def filter(self, items: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        Filtert eine Liste von Nachrichtenartikeln nach Glaubwürdigkeit.

        Gibt (gefilterte_liste, bericht) zurück.
        Der Bericht enthält Statistiken über gefilterte Artikel und erkannte Muster.
        """
        if not items:
            return [], {}

        scored = [(item, self._score(item)) for item in items]

        # Koordinierungs-Check: wenn > N Artikel derselben Domain → Abzug
        domains = [self._extract_domain(item.get("url") or item.get("source") or "") for item in items]
        domain_counts = Counter(d for d in domains if d)
        coordinated_domains = {d for d, c in domain_counts.items() if c >= _COORDINATION_THRESHOLD}

        if coordinated_domains:
            log.info(
                "NewsTrust: Koordinierungs-Muster erkannt – %s (je >=%d Artikel)",
                ", ".join(coordinated_domains), _COORDINATION_THRESHOLD,
            )
            # Abzug für über-repräsentierte Quellen
            new_scored = []
            for item, score in scored:
                domain = self._extract_domain(item.get("url") or item.get("source") or "")
                if domain in coordinated_domains:
                    score = max(0.0, score - 0.20)
                new_scored.append((item, score))
            scored = new_scored

        kept   = [(item, s) for item, s in scored if s >= _MIN_TRUST_SCORE]
        dropped = [(item, s) for item, s in scored if s < _MIN_TRUST_SCORE]

        if dropped:
            log.info(
                "NewsTrust: %d/%d Artikel gefiltert (Trust < %.2f): %s",
                len(dropped), len(items), _MIN_TRUST_SCORE,
                ", ".join(
                    (item.get("title") or "")[:40]
                    for item, _ in dropped[:3]
                ),
            )

        report = {
            "total":             len(items),
            "kept":              len(kept),
            "dropped":           len(dropped),
            "coordinated":       list(coordinated_domains),
            "avg_trust":         round(sum(s for _, s in scored) / len(scored), 2) if scored else 0.0,
            "low_trust_titles":  [(item.get("title") or "")[:60] for item, _ in dropped[:5]],
        }

        return [item for item, _ in kept], report

    def score_item(self, item: Dict) -> float:
        """Gibt den Trust-Score eines einzelnen Artikels zurück (0.0–1.0)."""
        return self._score(item)

    # ── Intern ───────────────────────────────────────────────────────────────

    def _score(self, item: Dict) -> float:
        title  = (item.get("title")  or "").lower()
        source = (item.get("source") or item.get("url") or "").lower()

        # 1. Quellen-Reputation
        domain      = self._extract_domain(source)
        base_score  = _SOURCE_SCORES.get(domain, 0.55)  # Unbekannte Quelle: 0.55

        # 2. Manipulations-Muster in der Headline
        pump_hits = sum(1 for p in _PUMP_PATTERNS if re.search(p, title, re.I))
        dump_hits = sum(1 for p in _DUMP_PATTERNS if re.search(p, title, re.I))
        manip_penalty = (pump_hits + dump_hits) * 0.15

        # 3. Anonyme / unsichere Quellen
        if any(x in source for x in ("blog", "substack", "medium.com", "wordpress")):
            base_score = min(base_score, 0.45)

        # 4. Sehr kurze Artikel (< 50 Zeichen Titel) → Clickbait-Verdacht
        if len(title) < 50 and source not in _SOURCE_SCORES:
            base_score = min(base_score, 0.40)

        score = max(0.0, min(1.0, base_score - manip_penalty))
        return round(score, 3)

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extrahiert den Domain-Namen aus einer URL oder einem Quellen-String."""
        if not url:
            return ""
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            domain = parsed.netloc.lower().lstrip("www.")
            return domain
        except Exception:
            return url.lower()[:50]
