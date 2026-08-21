"""CSV/PDF export of trades and monthly reports."""
import csv
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional

from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal


REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "reports")


class Exporter:
    def __init__(self, tracker: PerformanceTracker, journal: Optional[TradeJournal] = None):
        self.tracker = tracker
        self.journal = journal
        os.makedirs(REPORTS_DIR, exist_ok=True)

    def export_trades_csv(self, path: Optional[str] = None) -> str:
        """Exports all closed trades for tax/analysis purposes."""
        if path is None:
            path = os.path.join(REPORTS_DIR, f"trades_{datetime.now(timezone.utc).replace(tzinfo=None).date()}.csv")
        cursor = self.tracker._conn.execute(
            """SELECT ticker, entry_date, entry_price, sell_date, sell_price,
                      actual_hold_days, actual_return_pct, sell_reason,
                      sell_reason_category, predicted_target_price, sentiment_score
               FROM predictions
               WHERE sell_date IS NOT NULL
               ORDER BY sell_date"""
        )
        rows = [dict(r) for r in cursor.fetchall()]
        if not rows:
            return ""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return path

    def export_pdf_report(self, path: Optional[str] = None) -> Optional[str]:
        """Generates PDF monthly report. Falls back to TXT if reportlab missing."""
        if path is None:
            path = os.path.join(REPORTS_DIR, f"report_{datetime.now(timezone.utc).replace(tzinfo=None).date()}.pdf")
        report = self.tracker.get_accuracy_report()
        recent = self.tracker.get_recent_trades(20)
        exits = self.tracker.get_exit_reason_stats()

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import cm
        except ImportError:
            txt_path = path.replace(".pdf", ".txt")
            self._write_text_report(txt_path, report, recent, exits)
            return txt_path

        c = canvas.Canvas(path, pagesize=A4)
        width, height = A4
        y = height - 2 * cm
        c.setFont("Helvetica-Bold", 16)
        c.drawString(2 * cm, y, f"Trading-Report – {datetime.now(timezone.utc).replace(tzinfo=None).date()}")
        y -= 1 * cm
        c.setFont("Helvetica", 11)
        for k, v in report.items():
            c.drawString(2 * cm, y, f"{k}: {v}")
            y -= 0.5 * cm
        y -= 0.5 * cm
        c.setFont("Helvetica-Bold", 13)
        c.drawString(2 * cm, y, "Exit-Statistik")
        y -= 0.6 * cm
        c.setFont("Helvetica", 10)
        for e in exits:
            c.drawString(
                2 * cm, y,
                f"{e['category']}: {e['trades']} Trades, ⌀ {e['avg_return_pct']}%, Win {e['win_rate_pct']}%"
            )
            y -= 0.5 * cm
        y -= 0.5 * cm
        c.setFont("Helvetica-Bold", 13)
        c.drawString(2 * cm, y, "Letzte 20 Trades")
        y -= 0.6 * cm
        c.setFont("Helvetica", 9)
        for t in recent:
            line = (
                f"{t['ticker']}: {t['entry_price']:.2f}→{t['sell_price']:.2f} "
                f"({t['actual_return_pct']:+.2f}%, {t['actual_hold_days']}d, "
                f"{t['sell_reason_category']})"
            )
            c.drawString(2 * cm, y, line)
            y -= 0.45 * cm
            if y < 2 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Helvetica", 9)
        c.save()
        return path

    def _write_text_report(self, path, report, recent, exits):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Trading-Report – {datetime.now(timezone.utc).replace(tzinfo=None).date()}\n")
            f.write("=" * 50 + "\n\n")
            f.write("Accuracy:\n")
            for k, v in report.items():
                f.write(f"  {k}: {v}\n")
            f.write("\nExit-Statistik:\n")
            for e in exits:
                f.write(
                    f"  {e['category']}: {e['trades']} Trades, "
                    f"⌀ {e['avg_return_pct']}%, Win {e['win_rate_pct']}%\n"
                )
            f.write("\nLetzte Trades:\n")
            for t in recent:
                f.write(
                    f"  {t['ticker']}: {t['entry_price']:.2f}→{t['sell_price']:.2f} "
                    f"({t['actual_return_pct']:+.2f}%)\n"
                )
