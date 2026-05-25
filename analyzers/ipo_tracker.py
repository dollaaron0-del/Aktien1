"""
IPO-Tracker – verfolgt Unternehmen vor ihrem Börsenstart.

Aufnahme-Hürde (bewusst streng):
  • Nur manuell kuratierte Kandidaten mit Bewertung ≥ $10 Mrd.
  • Automatische Watchlist-Aufnahme erst ab Bewertung ≥ $25 Mrd.
  • Kein Auto-Scan für unbekannte Unternehmen

Ablauf:
  1. Täglich Nachrichten für jeden Kandidaten über NewsAPI abrufen
  2. Keyword-Sentiment berechnen (ohne Claude-Kosten)
  3. Prüfen ob Ticker bereits an Börse gelistet (yfinance)
  4. Bei IPO: user_request_queue befüllen + Telegram-Meldung
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ipo_tracker.db")

# ── Mindest-Schwellen ─────────────────────────────────────────────────────────
MIN_VALUATION_FOR_TRACKING_B = 10    # unter $10 Mrd. → ignoriert
MIN_VALUATION_FOR_WATCHLIST_B = 25   # ab $25 Mrd. + live → Watchlist
MIN_HYPE_SCORE = 0.35                # mind. 35% positives Sentiment


@dataclass
class IPOCandidate:
    slug: str
    name: str
    search_terms: List[str]
    expected_valuation_b: float
    sector: str
    expected_ticker: Optional[str] = None
    alt_tickers: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def auto_watchlist_eligible(self) -> bool:
        return self.expected_valuation_b >= MIN_VALUATION_FOR_WATCHLIST_B


# ── Kuratierte Kandidatenliste (nur etablierte Unicorns) ──────────────────────
CANDIDATES: Dict[str, IPOCandidate] = {
    "OPENAI": IPOCandidate(
        slug="OPENAI", name="OpenAI",
        search_terms=["OpenAI IPO", "OpenAI stock", "Sam Altman IPO"],
        expected_valuation_b=300,
        sector="KI / Technologie",
        expected_ticker="OAIT",
        alt_tickers=["OPAI", "OAIX"],
        notes="ChatGPT-Entwickler · ~$300 Mrd. Bewertung (2025)",
    ),
    "SPACEX": IPOCandidate(
        slug="SPACEX", name="SpaceX",
        search_terms=["SpaceX IPO", "SpaceX stock market", "SpaceX Börsengang"],
        expected_valuation_b=350,
        sector="Raumfahrt",
        expected_ticker="SPXC",
        alt_tickers=["SPACEX", "SPCX"],
        notes="Falcon 9, Starlink · ~$350 Mrd. Bewertung (2025)",
    ),
    "ANTHROPIC": IPOCandidate(
        slug="ANTHROPIC", name="Anthropic",
        search_terms=["Anthropic IPO", "Anthropic stock", "Anthropic Börsengang"],
        expected_valuation_b=61,
        sector="KI",
        expected_ticker="ANTH",
        notes="Claude-Entwickler · ~$61 Mrd. Bewertung (2025)",
    ),
    "STRIPE": IPOCandidate(
        slug="STRIPE", name="Stripe",
        search_terms=["Stripe IPO", "Stripe stock", "Stripe Nasdaq"],
        expected_valuation_b=70,
        sector="Fintech",
        expected_ticker="STRP",
        alt_tickers=["STRPE"],
        notes="Zahlungsdienstleister · ~$70 Mrd. Bewertung",
    ),
    "DATABRICKS": IPOCandidate(
        slug="DATABRICKS", name="Databricks",
        search_terms=["Databricks IPO", "Databricks stock", "Databricks Nasdaq"],
        expected_valuation_b=62,
        sector="Daten / KI",
        expected_ticker="DBRK",
        notes="Data-Analytics-Plattform · ~$62 Mrd. Bewertung",
    ),
    "KLARNA": IPOCandidate(
        slug="KLARNA", name="Klarna",
        search_terms=["Klarna IPO", "Klarna NYSE", "Klarna stock"],
        expected_valuation_b=15,
        sector="Fintech / BNPL",
        expected_ticker="KLAR",
        notes="Buy-Now-Pay-Later · ~$15 Mrd. Bewertung · NYSE 2025 geplant",
    ),
    "CHIME": IPOCandidate(
        slug="CHIME", name="Chime",
        search_terms=["Chime IPO", "Chime bank IPO", "Chime fintech stock"],
        expected_valuation_b=25,
        sector="Neobank",
        expected_ticker="CHME",
        notes="US-Neobank · ~$25 Mrd. Bewertung",
    ),
    "SHEIN": IPOCandidate(
        slug="SHEIN", name="Shein",
        search_terms=["Shein IPO", "Shein stock market", "Shein London IPO"],
        expected_valuation_b=66,
        sector="E-Commerce / Mode",
        expected_ticker="SHEI",
        notes="Fast-Fashion · ~$66 Mrd. Bewertung · London-IPO angestrebt",
    ),
}


# ── Keyword-Sentiment (ohne Claude-API-Kosten) ────────────────────────────────
_BULL_WORDS = {
    "surge", "soar", "rally", "boom", "record", "bullish", "buy", "opportunity",
    "growth", "profit", "revenue", "beat", "exceed", "launch", "milestone",
    "breakthrough", "valuation", "unicorn", "expand", "strong",
}
_BEAR_WORDS = {
    "crash", "fall", "drop", "decline", "loss", "bearish", "sell", "risk",
    "concern", "delay", "cancel", "lawsuit", "probe", "fraud", "layoff",
    "cut", "miss", "weak", "struggle", "halt",
}


def _keyword_sentiment(texts: List[str]) -> float:
    """Gibt Score 0–1 zurück (0.5 = neutral)."""
    bull = bear = 0
    for text in texts:
        lower = text.lower()
        bull += sum(1 for w in _BULL_WORDS if w in lower)
        bear += sum(1 for w in _BEAR_WORDS if w in lower)
    total = bull + bear
    if total == 0:
        return 0.5
    return bull / total


def _check_ticker_live(ticker: str) -> bool:
    """True wenn yfinance Kursdaten für diesen Ticker liefert."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None)
        return price is not None and price > 0
    except Exception:
        return False


# ── Datenbank ─────────────────────────────────────────────────────────────────

class IPOTracker:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS ipo_sentiment (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                slug          TEXT NOT NULL,
                checked_at    TEXT NOT NULL,
                articles_count INTEGER DEFAULT 0,
                hype_score    REAL DEFAULT 0.5,
                is_live       INTEGER DEFAULT 0,
                live_ticker   TEXT DEFAULT '',
                headlines     TEXT DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_ipo_slug ON ipo_sentiment(slug);
            CREATE TABLE IF NOT EXISTS ipo_live_events (
                slug        TEXT PRIMARY KEY,
                ticker      TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                notified    INTEGER DEFAULT 0
            );
        """)
        self._db.commit()

    # ── Sentiment-Update ─────────────────────────────────────────────────────

    def update_candidate(self, slug: str) -> Optional[Dict]:
        """Aktualisiert Nachrichten-Sentiment für einen Kandidaten."""
        candidate = CANDIDATES.get(slug)
        if not candidate:
            return None

        from collectors.news_api_collector import NewsAPICollector
        collector = NewsAPICollector()

        articles = []
        for term in candidate.search_terms:
            articles.extend(collector.collect_general(term, max_results=15, days_back=7))

        # Deduplizieren nach Titel
        seen: set = set()
        unique = []
        for a in articles:
            key = (a.get("title") or "")[:80].lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(a)

        texts = [(a.get("title") or "") + " " + (a.get("summary") or "") for a in unique]
        hype_score = _keyword_sentiment(texts)
        headlines = [a.get("title", "") for a in unique[:6]]

        # IPO-Check: testen ob Ticker bereits live ist
        is_live = False
        live_ticker = ""
        all_tickers = ([candidate.expected_ticker] if candidate.expected_ticker else []) + candidate.alt_tickers
        for t in all_tickers:
            if _check_ticker_live(t):
                is_live = True
                live_ticker = t
                break

        self._db.execute(
            """INSERT INTO ipo_sentiment
               (slug, checked_at, articles_count, hype_score, is_live, live_ticker, headlines)
               VALUES (?,?,?,?,?,?,?)""",
            (
                slug,
                datetime.utcnow().isoformat(),
                len(unique),
                round(hype_score, 3),
                int(is_live),
                live_ticker,
                json.dumps(headlines),
            ),
        )
        self._db.commit()

        return {
            "slug": slug,
            "is_live": is_live,
            "live_ticker": live_ticker,
            "hype_score": hype_score,
            "articles_count": len(unique),
            "headlines": headlines,
        }

    def run_daily_check(self) -> List[Dict]:
        """Prüft alle Kandidaten. Gibt Liste von IPO-Events zurück (neu live gegangen)."""
        new_ipos = []
        for slug, candidate in CANDIDATES.items():
            if candidate.expected_valuation_b < MIN_VALUATION_FOR_TRACKING_B:
                continue
            try:
                result = self.update_candidate(slug)
                if not result:
                    continue
                if result["is_live"] and result["live_ticker"]:
                    existing = self._db.execute(
                        "SELECT slug FROM ipo_live_events WHERE slug=?", (slug,)
                    ).fetchone()
                    if not existing:
                        # Neues IPO erkannt!
                        self._db.execute(
                            "INSERT OR IGNORE INTO ipo_live_events (slug, ticker, detected_at) VALUES (?,?,?)",
                            (slug, result["live_ticker"], datetime.utcnow().isoformat()),
                        )
                        self._db.commit()
                        if candidate.auto_watchlist_eligible:
                            import analyzers.user_request_queue as _urq
                            _urq.add_ticker(result["live_ticker"])
                        new_ipos.append({**result, "candidate": candidate})
            except Exception:
                pass
        return new_ipos

    # ── Abfragen für Dashboard ────────────────────────────────────────────────

    def get_pipeline(self) -> List[Dict]:
        """Gibt aktuellen Stand aller Kandidaten für Dashboard zurück."""
        rows = []
        for slug, cand in CANDIDATES.items():
            # Letzten Eintrag laden
            row = self._db.execute(
                "SELECT * FROM ipo_sentiment WHERE slug=? ORDER BY checked_at DESC LIMIT 1",
                (slug,),
            ).fetchone()
            live_event = self._db.execute(
                "SELECT * FROM ipo_live_events WHERE slug=?", (slug,)
            ).fetchone()
            rows.append({
                "slug":           slug,
                "name":           cand.name,
                "sector":         cand.sector,
                "valuation_b":    cand.expected_valuation_b,
                "notes":          cand.notes,
                "auto_eligible":  cand.auto_watchlist_eligible,
                "hype_score":     round(row["hype_score"], 3) if row else None,
                "articles_7d":    row["articles_count"] if row else 0,
                "headlines":      json.loads(row["headlines"]) if row else [],
                "last_checked":   row["checked_at"][:16] if row else "–",
                "is_live":        bool(live_event),
                "live_ticker":    live_event["ticker"] if live_event else None,
                "live_since":     live_event["detected_at"][:10] if live_event else None,
            })
        rows.sort(key=lambda r: r["valuation_b"], reverse=True)
        return rows

    def mark_notified(self, slug: str) -> None:
        self._db.execute(
            "UPDATE ipo_live_events SET notified=1 WHERE slug=?", (slug,)
        )
        self._db.commit()

    def get_pending_notifications(self) -> List[Dict]:
        """Neue IPOs die noch nicht per Telegram gemeldet wurden."""
        rows = self._db.execute(
            "SELECT e.slug, e.ticker, e.detected_at "
            "FROM ipo_live_events e WHERE e.notified=0"
        ).fetchall()
        result = []
        for r in rows:
            cand = CANDIDATES.get(r["slug"])
            if cand:
                result.append({
                    "slug": r["slug"],
                    "ticker": r["ticker"],
                    "detected_at": r["detected_at"],
                    "candidate": cand,
                })
        return result
