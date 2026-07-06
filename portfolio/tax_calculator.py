"""
portfolio/tax_calculator.py – Deutsche Abgeltungssteuer-Berechnung (FIFO).

Berechnet Kapitalertragsteuer nach deutschem Steuerrecht:
  - Abgeltungssteuer: 25%
  - Solidaritätszuschlag: 5,5% auf Abgeltungssteuer → Gesamtrate 26,375%
  - Freistellungsauftrag: 1.000 € / Jahr (ab 2023; vorher 801 €)
  - Verlustverrechnung: Verluste mindern Gewinne im gleichen Jahr
  - FIFO: Älteste Kauflose werden zuerst verkauft

Export: CSV-Datei kompatibel mit Steuerberater / Elster-Vorbereitung.
"""

from __future__ import annotations

import csv
import io
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_ABGELTUNGSSTEUER = 0.25
_SOLI_AUFSCHLAG = 0.055
_GESAMTRATE = _ABGELTUNGSSTEUER * (1 + _SOLI_AUFSCHLAG)  # 26,375 %
_FREISTELLUNGSAUFTRAG = 1000.0  # EUR, ab 2023

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tax.db")


@dataclass
class TaxableGain:
    ticker: str
    shares: float
    buy_date: str
    sell_date: str
    cost_basis_eur: float
    proceeds_eur: float
    gross_gain_eur: float
    tax_due_eur: float
    is_taxable: bool


@dataclass
class YearlyTaxSummary:
    year: int
    gross_gains_eur: float
    gross_losses_eur: float
    net_gain_eur: float
    dividend_income_eur: float
    freistellungsauftrag_used_eur: float
    taxable_gain_eur: float
    tax_due_eur: float
    effective_rate_pct: float
    gains: List[TaxableGain] = field(default_factory=list)


class TaxCalculator:
    """
    FIFO-basierter Abgeltungssteuer-Rechner.

    Verwendung:
        tc = TaxCalculator()
        tc.add_lot("AAPL", shares=10, cost_per_share_eur=150.0, buy_date="2025-01-10")
        gain = tc.record_sale("AAPL", shares_sold=10, proceeds_eur=1800.0, sell_date="2025-06-01")
        summary = tc.get_yearly_summary(2025)
        print(tc.export_csv(2025))
    """

    def __init__(self):
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tax_lots (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT NOT NULL,
                buy_date          TEXT NOT NULL,
                shares            REAL NOT NULL,
                cost_per_share    REAL NOT NULL,
                remaining_shares  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS realized_gains (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT NOT NULL,
                sell_date    TEXT NOT NULL,
                shares       REAL NOT NULL,
                buy_date     TEXT NOT NULL,
                cost_basis   REAL NOT NULL,
                proceeds     REAL NOT NULL,
                gross_gain   REAL NOT NULL,
                recorded_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS freistellung_usage (
                year         INTEGER PRIMARY KEY,
                used_amount  REAL NOT NULL DEFAULT 0
            );
        """)
        self._conn.commit()

    # ── Lot-Verwaltung ─────────────────────────────────────────────────────────

    def add_lot(
        self,
        ticker: str,
        shares: float,
        cost_per_share_eur: float,
        buy_date: str,
    ) -> None:
        """Neues FIFO-Los beim Kauf erfassen."""
        self._conn.execute(
            """INSERT INTO tax_lots
               (ticker, buy_date, shares, cost_per_share, remaining_shares)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker, buy_date, shares, cost_per_share_eur, shares),
        )
        self._conn.commit()
        log.debug("Steuer-Los: %s %.4f Aktien à %.2f EUR (%s)", ticker, shares, cost_per_share_eur, buy_date)

    def record_sale(
        self,
        ticker: str,
        shares_sold: float,
        proceeds_eur: float,
        sell_date: str,
    ) -> TaxableGain:
        """
        FIFO-Verkauf: älteste Lots zuerst aufbrauchen.
        Berechnet Steuer inkl. Freistellungsauftrag.
        """
        lots = self._conn.execute(
            """SELECT id, buy_date, remaining_shares, cost_per_share
               FROM tax_lots WHERE ticker=? AND remaining_shares > 0.0001
               ORDER BY buy_date ASC""",
            (ticker,),
        ).fetchall()

        remaining = shares_sold
        total_cost = 0.0
        first_buy_date = lots[0]["buy_date"] if lots else sell_date

        for lot in lots:
            if remaining <= 0:
                break
            use = min(float(lot["remaining_shares"]), remaining)
            total_cost += use * float(lot["cost_per_share"])
            remaining -= use
            new_rem = float(lot["remaining_shares"]) - use
            self._conn.execute(
                "UPDATE tax_lots SET remaining_shares=? WHERE id=?",
                (new_rem, lot["id"]),
            )

        gross_gain = proceeds_eur - total_cost
        year = int(sell_date[:4])

        # Freistellungsauftrag berücksichtigen
        frei_used = self._get_freistellung_used(year)
        available = max(0.0, _FREISTELLUNGSAUFTRAG - frei_used)
        if gross_gain > 0 and available > 0:
            sheltered = min(gross_gain, available)
            self._set_freistellung_used(year, frei_used + sheltered)
            taxable = max(0.0, gross_gain - sheltered)
        elif gross_gain > 0:
            taxable = gross_gain
        else:
            taxable = 0.0

        tax_due = round(taxable * _GESAMTRATE, 2)

        self._conn.execute(
            """INSERT INTO realized_gains
               (ticker, sell_date, shares, buy_date, cost_basis, proceeds, gross_gain, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, sell_date, shares_sold, first_buy_date,
             total_cost, proceeds_eur, gross_gain, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        self._conn.commit()

        return TaxableGain(
            ticker=ticker,
            shares=shares_sold,
            buy_date=first_buy_date,
            sell_date=sell_date,
            cost_basis_eur=round(total_cost, 2),
            proceeds_eur=round(proceeds_eur, 2),
            gross_gain_eur=round(gross_gain, 2),
            tax_due_eur=tax_due,
            is_taxable=taxable > 0,
        )

    # ── Jahresübersicht ────────────────────────────────────────────────────────

    def get_yearly_summary(
        self, year: int, dividend_income_eur: float = 0.0
    ) -> YearlyTaxSummary:
        rows = self._conn.execute(
            """SELECT ticker, sell_date, buy_date, shares, cost_basis, proceeds, gross_gain
               FROM realized_gains WHERE sell_date LIKE ?""",
            (f"{year}-%",),
        ).fetchall()

        gross_gains = sum(float(r["gross_gain"]) for r in rows if float(r["gross_gain"]) >= 0)
        gross_losses = sum(float(r["gross_gain"]) for r in rows if float(r["gross_gain"]) < 0)
        net_gain = gross_gains + gross_losses + dividend_income_eur
        frei_used = self._get_freistellung_used(year)
        taxable = max(0.0, net_gain - frei_used) if net_gain > 0 else 0.0
        tax_due = round(taxable * _GESAMTRATE, 2)
        eff_rate = round(tax_due / net_gain * 100, 2) if net_gain > 0 else 0.0

        gains_list = [
            TaxableGain(
                ticker=r["ticker"],
                shares=float(r["shares"]),
                buy_date=r["buy_date"],
                sell_date=r["sell_date"],
                cost_basis_eur=round(float(r["cost_basis"]), 2),
                proceeds_eur=round(float(r["proceeds"]), 2),
                gross_gain_eur=round(float(r["gross_gain"]), 2),
                tax_due_eur=0.0,
                is_taxable=float(r["gross_gain"]) > 0,
            )
            for r in rows
        ]

        return YearlyTaxSummary(
            year=year,
            gross_gains_eur=round(gross_gains, 2),
            gross_losses_eur=round(gross_losses, 2),
            net_gain_eur=round(net_gain, 2),
            dividend_income_eur=round(dividend_income_eur, 2),
            freistellungsauftrag_used_eur=round(frei_used, 2),
            taxable_gain_eur=round(taxable, 2),
            tax_due_eur=tax_due,
            effective_rate_pct=eff_rate,
            gains=gains_list,
        )

    # ── CSV-Export ─────────────────────────────────────────────────────────────

    def export_csv(self, year: int, dividend_income_eur: float = 0.0) -> str:
        """CSV-String für Steuerberater / Elster-Vorbereitung."""
        s = self.get_yearly_summary(year, dividend_income_eur)
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["Ticker", "Kauf-Datum", "Verkauf-Datum", "Anteile",
                    "Einstandswert (€)", "Erlöse (€)", "Gewinn/Verlust (€)"])
        for g in s.gains:
            w.writerow([
                g.ticker, g.buy_date, g.sell_date,
                f"{g.shares:.4f}", f"{g.cost_basis_eur:.2f}",
                f"{g.proceeds_eur:.2f}", f"{g.gross_gain_eur:.2f}",
            ])
        w.writerow([])
        w.writerow(["Kapitalerträge (Gewinne)", "", "", "", "", "", f"{s.gross_gains_eur:.2f}"])
        w.writerow(["Verluste", "", "", "", "", "", f"{s.gross_losses_eur:.2f}"])
        w.writerow(["Dividenden", "", "", "", "", "", f"{s.dividend_income_eur:.2f}"])
        w.writerow(["Netto-Kapitalertrag", "", "", "", "", "", f"{s.net_gain_eur:.2f}"])
        w.writerow(["Freistellungsauftrag genutzt", "", "", "", "", "", f"{s.freistellungsauftrag_used_eur:.2f}"])
        w.writerow(["Zu versteuernder Ertrag", "", "", "", "", "", f"{s.taxable_gain_eur:.2f}"])
        w.writerow([f"Abgeltungssteuer ({_GESAMTRATE*100:.3f}%)", "", "", "", "", "", f"{s.tax_due_eur:.2f}"])
        return out.getvalue()

    # ── Freistellungsauftrag ───────────────────────────────────────────────────

    def get_freistellung_status(self, year: Optional[int] = None) -> Dict:
        y = year or datetime.now(timezone.utc).replace(tzinfo=None).year
        used = self._get_freistellung_used(y)
        return {
            "year": y,
            "freistellungsauftrag_eur": _FREISTELLUNGSAUFTRAG,
            "used_eur": round(used, 2),
            "remaining_eur": round(max(0.0, _FREISTELLUNGSAUFTRAG - used), 2),
            "pct_used": round(used / _FREISTELLUNGSAUFTRAG * 100, 1),
        }

    def _get_freistellung_used(self, year: int) -> float:
        row = self._conn.execute(
            "SELECT used_amount FROM freistellung_usage WHERE year=?", (year,)
        ).fetchone()
        return float(row["used_amount"]) if row else 0.0

    def _set_freistellung_used(self, year: int, amount: float) -> None:
        self._conn.execute(
            """INSERT INTO freistellung_usage (year, used_amount) VALUES (?, ?)
               ON CONFLICT(year) DO UPDATE SET used_amount=excluded.used_amount""",
            (year, amount),
        )
        self._conn.commit()
