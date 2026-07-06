"""
portfolio/dividend_tracker.py – Dividenden-Tracking für das Portfolio.

Erfasst erhaltene Dividenden, berechnet Total Return (Kurs + Dividenden)
und zeigt bevorstehende Ex-Dividenden-Termine aus yfinance.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "dividends.db")


@dataclass
class DividendPayment:
    ticker: str
    amount_per_share: float
    total_amount: float
    ex_date: str
    pay_date: Optional[str]
    currency: str = "USD"
    shares_held: float = 0.0


class DividendTracker:
    """Verfolgt Dividenden-Einnahmen und bevorstehende Ex-Div-Termine."""

    def __init__(self):
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS dividends (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                ex_date         TEXT NOT NULL,
                pay_date        TEXT,
                amount_per_share REAL NOT NULL,
                shares_held     REAL NOT NULL DEFAULT 0,
                total_amount    REAL NOT NULL,
                currency        TEXT NOT NULL DEFAULT 'USD',
                recorded_at     TEXT NOT NULL,
                UNIQUE(ticker, ex_date)
            );
        """)
        self._conn.commit()

    def record_dividend(
        self,
        ticker: str,
        amount_per_share: float,
        shares_held: float,
        ex_date: str,
        pay_date: Optional[str] = None,
        currency: str = "USD",
    ) -> None:
        total = round(amount_per_share * shares_held, 4)
        self._conn.execute(
            """INSERT OR IGNORE INTO dividends
               (ticker, ex_date, pay_date, amount_per_share, shares_held,
                total_amount, currency, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, ex_date, pay_date, amount_per_share, shares_held,
             total, currency, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        self._conn.commit()
        log.info("Dividende: %s %.4f/Aktie × %.0f = %.2f %s", ticker, amount_per_share, shares_held, total, currency)

    def fetch_upcoming_dividends(
        self,
        tickers: List[str],
        positions: Dict[str, float],  # {ticker: shares}
    ) -> List[DividendPayment]:
        """Ruft bevorstehende Ex-Div-Termine von yfinance ab."""
        results: List[DividendPayment] = []
        try:
            import yfinance as yf
        except ImportError:
            return results

        today = date.today().isoformat()
        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                cal = t.calendar
                if not cal:
                    continue
                ex_date_raw = cal.get("Ex-Dividend Date")
                if not ex_date_raw:
                    continue
                ex_str = ex_date_raw.strftime("%Y-%m-%d") if hasattr(ex_date_raw, "strftime") else str(ex_date_raw)[:10]
                if ex_str < today:
                    continue

                info = t.info or {}
                div_rate = float(info.get("dividendRate") or 0)
                if not div_rate:
                    div_rate = float(info.get("lastDividendValue") or 0)
                if not div_rate:
                    continue

                div_per_quarter = round(div_rate / 4, 4)
                shares = positions.get(ticker, 0.0)
                currency = info.get("currency", "USD")
                pay_raw = cal.get("Dividend Date")
                pay_str = pay_raw.strftime("%Y-%m-%d") if pay_raw and hasattr(pay_raw, "strftime") else None

                results.append(DividendPayment(
                    ticker=ticker,
                    amount_per_share=div_per_quarter,
                    total_amount=round(div_per_quarter * shares, 2),
                    ex_date=ex_str,
                    pay_date=pay_str,
                    currency=currency,
                    shares_held=shares,
                ))
            except Exception as e:
                log.debug("Dividende %s: %s", ticker, e)

        results.sort(key=lambda d: d.ex_date)
        return results

    def get_total_income(self, ticker: Optional[str] = None, year: Optional[int] = None) -> float:
        """Gesamte Dividenden-Einnahmen, optional gefiltert."""
        query = "SELECT SUM(total_amount) FROM dividends WHERE 1=1"
        params: list = []
        if ticker:
            query += " AND ticker=?"
            params.append(ticker)
        if year:
            query += " AND ex_date LIKE ?"
            params.append(f"{year}-%")
        row = self._conn.execute(query, params).fetchone()
        return round(float(row[0] or 0), 2)

    def get_dividend_history(self, ticker: Optional[str] = None) -> List[Dict]:
        query = """SELECT ticker, ex_date, pay_date, amount_per_share, shares_held,
                          total_amount, currency FROM dividends"""
        params: list = []
        if ticker:
            query += " WHERE ticker=?"
            params.append(ticker)
        query += " ORDER BY ex_date DESC"
        return [dict(r) for r in self._conn.execute(query, params).fetchall()]

    def get_yearly_summary(self, year: int) -> Dict:
        rows = self._conn.execute(
            """SELECT ticker, SUM(total_amount) as total, currency
               FROM dividends WHERE ex_date LIKE ?
               GROUP BY ticker, currency ORDER BY total DESC""",
            (f"{year}-%",),
        ).fetchall()
        breakdown = [{"ticker": r["ticker"], "total": round(r["total"], 2), "currency": r["currency"]} for r in rows]
        return {
            "year": year,
            "total_income": round(sum(b["total"] for b in breakdown), 2),
            "breakdown": breakdown,
            "entries": len(breakdown),
        }

    def get_total_return(self, ticker: str, entry_price: float, current_price: float, shares: float) -> Dict:
        """Total Return: (Kursgewinn + Dividenden) / Einstiegswert."""
        price_gain = (current_price - entry_price) * shares
        div_income = self.get_total_income(ticker=ticker)
        total_gain = price_gain + div_income
        total_return_pct = (total_gain / (entry_price * shares) * 100) if entry_price * shares > 0 else 0.0
        return {
            "ticker": ticker,
            "price_gain": round(price_gain, 2),
            "dividend_income": round(div_income, 2),
            "total_gain": round(total_gain, 2),
            "total_return_pct": round(total_return_pct, 2),
        }
