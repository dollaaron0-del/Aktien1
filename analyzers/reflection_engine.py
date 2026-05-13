"""
Reflection Engine – Kontinuierliches Lernen aus eigenen Trades

Zwei Modi:
  1. CONTINUOUS_MEMO:  Kurze Lessons-Learned-Notiz (1-2 Absätze), die in
                       Claudes System-Prompt eingespeist wird. Wird nach
                       jedem geschlossenen Trade neu generiert.

  2. MONTHLY_REVIEW:   Ausführliche Selbsteinschätzung am Monatsende:
                       Was lief gut, was schlecht, was wird angepasst.
                       Manuell via --reflect oder automatisch am 1. des Monats.

Beide werden mit Claude generiert, gestützt auf:
  - Closed-Trade-Statistik aus PerformanceTracker
  - Trade-Stories aus TradeJournal
  - Exit-Reason vs P&L Korrelationen
  - Source-Accuracy pro Ticker
"""

import os
import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import anthropic

from config import config
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal


REFLECTION_DB = os.path.join(os.path.dirname(__file__), "..", "data", "reflections.db")


_SYSTEM_PROMPT_MEMO = """Du bist ein Trading-Coach, der einen automatisierten Swing-Trading-Bot anleitet.
Aus den realen Trades und Statistiken der letzten Wochen formulierst du eine knappe
Lessons-Learned-Notiz für den Bot (max 200 Wörter). Sie wird in den System-Prompt
zukünftiger Analysen eingespeist – also konkret, handlungsleitend, ohne Floskeln.

Antworte als reiner Text, keine Markdown-Header.
"""

_SYSTEM_PROMPT_REVIEW = """Du bist ein strenger Trading-Coach, der den letzten Monat eines
automatisierten Swing-Trading-Bots reviewt. Schreibe eine ehrliche Selbsteinschätzung
mit konkreten Beispielen aus den Trades. Struktur:

1. WAS LIEF GUT (3-5 Bullet Points mit Trade-Beispielen)
2. WAS LIEF SCHLECHT (3-5 Bullet Points mit Trade-Beispielen)
3. WURZELURSACHEN (warum die schlechten Trades passiert sind)
4. KONKRETE ANPASSUNGEN (welche Parameter/Verhalten ändern)
5. PROGNOSE NÄCHSTER MONAT (was wir erwarten)

Antworte als strukturierter Text mit den 5 Abschnitten, max 600 Wörter.
"""


class ReflectionEngine:
    def __init__(
        self,
        tracker: PerformanceTracker,
        journal: TradeJournal,
    ):
        self.tracker = tracker
        self.journal = journal
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key) if config.anthropic_api_key else None
        os.makedirs(os.path.dirname(REFLECTION_DB), exist_ok=True)
        self._conn = sqlite3.connect(REFLECTION_DB, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS reflections (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT NOT NULL,
                kind          TEXT NOT NULL,   -- 'MEMO' | 'MONTHLY'
                period        TEXT,            -- e.g. '2026-04' for monthly
                content       TEXT NOT NULL,
                trades_used   INTEGER
            );
        """)
        self._conn.commit()

    # ── Continuous learning memo ──────────────────────────────────────────────

    def generate_memo(self) -> Optional[str]:
        """
        Generates a short lessons-learned note (200 words) from recent trades.
        Called after each closed trade. Stored and used in future system prompts.
        """
        if not self._client:
            return None

        recent_trades = self.journal.get_recent_lessons(days=60)
        if len(recent_trades) < 3:
            return None  # Not enough signal yet

        prompt = self._build_memo_prompt(recent_trades)
        try:
            msg = self._client.messages.create(
                model=config.claude_model,
                max_tokens=500,
                system=_SYSTEM_PROMPT_MEMO,
                messages=[{"role": "user", "content": prompt}],
            )
            content = msg.content[0].text.strip()
            self._save("MEMO", content, period=None, trades_used=len(recent_trades))
            return content
        except Exception:
            return None

    def get_active_memo(self) -> Optional[str]:
        cursor = self._conn.execute(
            """SELECT content FROM reflections
               WHERE kind='MEMO'
               ORDER BY created_at DESC LIMIT 1"""
        )
        row = cursor.fetchone()
        return row["content"] if row else None

    def _build_memo_prompt(self, trades: List[Dict]) -> str:
        acc = self.tracker.get_accuracy_report()
        exit_stats = self.tracker.get_exit_reason_stats()
        buckets = self.tracker.get_sentiment_score_buckets()

        lines = [
            f"=== STATISTIK (letzte ~60 Tage, {acc.get('total_closed', 0)} geschlossene Trades) ===",
            f"Win-Rate:           {acc.get('win_rate_pct', 0)}%",
            f"Ø Rendite/Trade:    {acc.get('avg_return_pct', 0):+.2f}%",
            f"Richtungs-Acc:      {acc.get('direction_accuracy_pct', 0)}%",
            f"Zielkurs-Trefferq:  {acc.get('target_hit_pct', 0)}%",
            "",
            "=== EXIT-GRÜNDE ===",
        ]
        for e in exit_stats:
            lines.append(
                f"  {e['category']:20s} {e['trades']:3d} Trades, "
                f"Ø {e['avg_return_pct']:+.2f}%, Win-Rate {e['win_rate_pct']}%"
            )
        lines.append("\n=== SENTIMENT-BUCKETS ===")
        for b in buckets:
            lines.append(
                f"  Score {b['score_range']:12s} {b['trades']:3d} Trades, "
                f"Win {b['win_rate_pct']}%, Ø {b['avg_return_pct']:+.2f}%"
            )

        lines.append("\n=== EINZELNE TRADES (anonymisiert) ===")
        for t in trades[:15]:
            lines.append(
                f"  {t.get('ticker')}: Einstieg ${t.get('entry_price'):.2f} "
                f"(Sentiment {t.get('entry_sentiment', 0):.2f}), "
                f"{t.get('actual_hold_days', '?')}d → Exit ${t.get('exit_price', 0):.2f} "
                f"({t.get('pnl_pct', 0):+.1f}%) – {t.get('exit_reason', '')[:60]}"
            )

        lines.append(
            "\nFormuliere eine kompakte Notiz: Was hat funktioniert, was nicht? "
            "Welche Muster wiederholen sich? Was sollte der Bot bei nächsten Trades beachten?"
        )
        return "\n".join(lines)

    # ── Monthly self-assessment ───────────────────────────────────────────────

    def generate_monthly_review(self, year_month: Optional[str] = None) -> Optional[str]:
        """
        Generates the full monthly self-assessment.
        year_month format: '2026-04' (defaults to last completed month).
        """
        if not self._client:
            return None

        if year_month is None:
            today = datetime.utcnow().date()
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            year_month = last_month_end.strftime("%Y-%m")

        trades_in_month = self._get_trades_in_month(year_month)
        if not trades_in_month:
            return None

        prompt = self._build_review_prompt(year_month, trades_in_month)
        try:
            msg = self._client.messages.create(
                model=config.claude_model,
                max_tokens=2000,
                system=_SYSTEM_PROMPT_REVIEW,
                messages=[{"role": "user", "content": prompt}],
            )
            content = msg.content[0].text.strip()
            self._save("MONTHLY", content, period=year_month, trades_used=len(trades_in_month))
            return content
        except Exception:
            return None

    def get_monthly_reviews(self, limit: int = 12) -> List[Dict]:
        cursor = self._conn.execute(
            """SELECT period, content, created_at, trades_used FROM reflections
               WHERE kind='MONTHLY' ORDER BY period DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def _get_trades_in_month(self, year_month: str) -> List[Dict]:
        start = f"{year_month}-01"
        # End of month: first of next month
        year, mo = year_month.split("-")
        next_mo = (datetime.strptime(start, "%Y-%m-%d").replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_mo.isoformat()

        # Get all tickers with closed trades in this period
        all_stories = self.journal.get_all_trade_summaries(limit=200)
        return [
            s for s in all_stories
            if s.get("exit_date") and start <= s["exit_date"][:10] < end[:10]
        ]

    def _build_review_prompt(self, year_month: str, trades: List[Dict]) -> str:
        wins = [t for t in trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
        total_pnl = sum(t.get("pnl") or 0 for t in trades)

        lines = [
            f"=== MONAT: {year_month} ===",
            f"Trades geschlossen: {len(trades)} "
            f"({len(wins)} Gewinner, {len(losses)} Verlierer)",
            f"Win-Rate: {round(len(wins) / len(trades) * 100, 1)}%",
            f"Gesamt-P&L: {total_pnl:+.2f} USD",
            "",
            "=== GEWINNER ===",
        ]
        for t in wins[:10]:
            lines.append(
                f"  {t['ticker']}: +{t.get('pnl', 0):.2f} USD ({t.get('pnl_pct', 0):+.1f}%, "
                f"{t.get('actual_hold_days', '?')}d) "
                f"– Einstieg-Sentiment {t.get('entry_sentiment', 0):.2f} "
                f"– Exit: {t.get('exit_reason', '')[:80]}"
            )
            if t.get("catalysts"):
                lines.append(f"      Katalysatoren: {', '.join(t['catalysts'][:3])}")

        lines.append("\n=== VERLIERER ===")
        for t in losses[:10]:
            lines.append(
                f"  {t['ticker']}: {t.get('pnl', 0):.2f} USD ({t.get('pnl_pct', 0):+.1f}%, "
                f"{t.get('actual_hold_days', '?')}d) "
                f"– Einstieg-Sentiment {t.get('entry_sentiment', 0):.2f} "
                f"– Exit: {t.get('exit_reason', '')[:80]}"
            )
            if t.get("catalysts"):
                lines.append(f"      Katalysatoren: {', '.join(t['catalysts'][:3])}")
            if t.get("risks"):
                lines.append(f"      Risiken (waren bekannt): {', '.join(t['risks'][:3])}")

        # Add aggregate stats
        exit_stats = self.tracker.get_exit_reason_stats()
        if exit_stats:
            lines.append("\n=== EXIT-MUSTER ===")
            for e in exit_stats:
                lines.append(
                    f"  {e['category']:20s} Ø {e['avg_return_pct']:+.2f}%, "
                    f"{e['trades']} Trades, Win {e['win_rate_pct']}%"
                )

        lines.append(
            f"\nSchreibe die Monats-Selbsteinschätzung für {year_month} "
            "(5 Abschnitte: Gut/Schlecht/Wurzel/Anpassung/Prognose)."
        )
        return "\n".join(lines)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self, kind: str, content: str, period: Optional[str], trades_used: int):
        self._conn.execute(
            """INSERT INTO reflections (created_at, kind, period, content, trades_used)
               VALUES (?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), kind, period, content, trades_used),
        )
        self._conn.commit()
