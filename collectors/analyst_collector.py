"""
AnalystCollector – sammelt Analyst-Upgrades und -Downgrades.
Quellen: Yahoo Finance (via yfinance), Benzinga RSS.
Goldman Sachs stuft Apple auf Buy hoch → starkes Signal.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from system.http import http_get
import yfinance as yf

_BENZINGA_RSS = "https://www.benzinga.com/analyst-ratings/analyst-color/rss"


class AnalystCollector:
    """Sammelt Analyst-Upgrades/-Downgrades für einen Ticker."""

    def collect(self, ticker: str, lookback_days: int = 14) -> List[Dict]:
        results = []
        results += self._collect_yfinance(ticker, lookback_days)
        results += self._collect_benzinga_rss(ticker, lookback_days)
        return results

    def _collect_yfinance(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        try:
            stock = yf.Ticker(ticker)
            upgrades = stock.upgrades_downgrades
            if upgrades is None or upgrades.empty:
                return []

            upgrades = upgrades.reset_index()
            for _, row in upgrades.iterrows():
                try:
                    grade_date = row.get("GradeDate") or row.get("Date")
                    if grade_date is None:
                        continue
                    if hasattr(grade_date, "to_pydatetime"):
                        grade_date = grade_date.to_pydatetime().replace(tzinfo=None)
                    elif isinstance(grade_date, str):
                        grade_date = datetime.strptime(grade_date[:10], "%Y-%m-%d")
                    if grade_date < cutoff:
                        continue
                except Exception:
                    continue

                firm     = row.get("Firm", "Unbekannte Bank")
                to_grade = row.get("ToGrade", "")
                from_grade = row.get("FromGrade", "")
                action   = row.get("Action", "")

                # Klassifizierung
                bullish_grades = {"Buy", "Strong Buy", "Outperform", "Overweight", "Positive"}
                bearish_grades = {"Sell", "Strong Sell", "Underperform", "Underweight", "Negative"}

                if to_grade in bullish_grades:
                    sentiment_tag = "🟢 UPGRADE"
                elif to_grade in bearish_grades:
                    sentiment_tag = "🔴 DOWNGRADE"
                else:
                    sentiment_tag = "⚪ RATINGÄNDERUNG"

                title = f"{sentiment_tag}: {firm} bewertet {ticker} mit '{to_grade}'"
                if from_grade:
                    title += f" (vorher: '{from_grade}')"

                results.append({
                    "source":       f"Analyst Rating / {firm}",
                    "ticker":       ticker,
                    "title":        title,
                    "text":         (
                        f"{firm} hat {ticker} am {grade_date.strftime('%Y-%m-%d')} "
                        f"auf '{to_grade}' gesetzt"
                        + (f" (von '{from_grade}')" if from_grade else "")
                        + f". Aktion: {action}."
                    ),
                    "url":          f"https://finance.yahoo.com/quote/{ticker}/analysis/",
                    "published_at": grade_date.isoformat(),
                    "priority":     "HIGH" if any(
                        top in firm for top in ["Goldman", "Morgan Stanley", "JPMorgan",
                                                "Bank of America", "Citi", "Wells Fargo",
                                                "UBS", "Barclays", "Deutsche"]
                    ) else "MEDIUM",
                })
        except Exception:
            pass
        return results

    def _collect_benzinga_rss(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        try:
            resp = http_get(_BENZINGA_RSS, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                if ticker.upper() not in title.upper():
                    continue
                link    = (item.findtext("link") or "").strip()
                pub     = item.findtext("pubDate") or ""
                summary = (item.findtext("description") or title).strip()
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pub_dt = datetime.utcnow()

                results.append({
                    "source":       "Benzinga Analyst Ratings",
                    "ticker":       ticker,
                    "title":        title,
                    "text":         summary[:500],
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     "MEDIUM",
                })
        except Exception:
            pass
        return results
