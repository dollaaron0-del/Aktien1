"""
Stündlicher Social-Scan – sammelt Reddit + StockTwits ohne Claude-Analyse.

Speichert Roh-Sentiment-Pulse in SQLite und kann Alarm schlagen wenn
ein Ticker plötzlich stark diskutiert wird (Volumen-Spike in Meinungen).
"""
import sqlite3
import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "social_pulse.db")

BULLISH_KEYWORDS = {
    "buy", "long", "bullish", "moon", "rocket", "calls", "upside", "breakout",
    "surge", "rally", "beat", "record", "growth", "strong", "upgraded",
    "kauf", "bullisch", "steigt", "hoch", "top", "gewinn",
}
BEARISH_KEYWORDS = {
    "sell", "short", "bearish", "puts", "crash", "drop", "downside", "dump",
    "miss", "weak", "downgrade", "cut", "loss", "verkauf", "bärisch", "fällt",
}


class SocialPulseDB:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS social_pulse (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                scanned_at    TEXT NOT NULL,
                source        TEXT NOT NULL,
                mention_count INTEGER NOT NULL,
                bullish_count INTEGER NOT NULL,
                bearish_count INTEGER NOT NULL,
                neutral_count INTEGER NOT NULL,
                pulse_score   REAL NOT NULL,  -- -1.0 (sehr bärisch) bis +1.0 (sehr bullisch)
                top_mentions  TEXT            -- JSON: list of short summaries
            );
            CREATE INDEX IF NOT EXISTS idx_pulse_ticker ON social_pulse(ticker, scanned_at);
        """)
        self._conn.commit()

    def record(
        self,
        ticker: str,
        source: str,
        items: List[Dict],
    ):
        """Analyzes and stores a social pulse snapshot."""
        bull = bear = neutral = 0
        top: List[str] = []
        for item in items:
            text = ((item.get("title") or "") + " " + (item.get("summary") or "")).lower()
            words = set(re.findall(r'\b\w+\b', text))
            b_hits = words & BULLISH_KEYWORDS
            s_hits = words & BEARISH_KEYWORDS
            if b_hits and not s_hits:
                bull += 1
            elif s_hits and not b_hits:
                bear += 1
            else:
                neutral += 1
            if len(top) < 5 and item.get("title"):
                top.append(item["title"][:100])

        total = bull + bear + neutral or 1
        score = (bull - bear) / total

        self._conn.execute(
            """INSERT INTO social_pulse
               (ticker, scanned_at, source, mention_count, bullish_count,
                bearish_count, neutral_count, pulse_score, top_mentions)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ticker,
                datetime.utcnow().isoformat(),
                source,
                len(items),
                bull, bear, neutral,
                round(score, 3),
                json.dumps(top),
            ),
        )
        self._conn.commit()

    def get_recent(self, ticker: str, hours: int = 24) -> List[Dict]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        cursor = self._conn.execute(
            """SELECT * FROM social_pulse
               WHERE ticker=? AND scanned_at > ?
               ORDER BY scanned_at DESC""",
            (ticker, cutoff),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        for r in rows:
            r["top_mentions"] = json.loads(r.get("top_mentions") or "[]")
        return rows

    def get_pulse_summary(self, hours: int = 6) -> List[Dict]:
        """Returns aggregated pulse per ticker for the last N hours, sorted by activity."""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        cursor = self._conn.execute(
            """SELECT ticker,
                      SUM(mention_count) as total_mentions,
                      AVG(pulse_score) as avg_score,
                      SUM(bullish_count) as bull,
                      SUM(bearish_count) as bear,
                      MAX(scanned_at) as latest
               FROM social_pulse
               WHERE scanned_at > ?
               GROUP BY ticker
               ORDER BY total_mentions DESC""",
            (cutoff,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_spikes(self, hours: int = 2, min_mentions: int = 5) -> List[Dict]:
        """Returns tickers with unusual mention spikes in the last N hours."""
        recent = self.get_pulse_summary(hours=hours)
        baseline = self.get_pulse_summary(hours=hours * 6)
        base_map = {r["ticker"]: r["total_mentions"] for r in baseline}
        spikes = []
        for r in recent:
            t = r["ticker"]
            base = base_map.get(t, 1)
            ratio = r["total_mentions"] / base
            if r["total_mentions"] >= min_mentions and ratio >= 2.0:
                spikes.append({
                    **r,
                    "spike_ratio": round(ratio, 1),
                })
        return sorted(spikes, key=lambda x: x["spike_ratio"], reverse=True)

    def cleanup(self, keep_days: int = 7):
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat()
        self._conn.execute("DELETE FROM social_pulse WHERE scanned_at < ?", (cutoff,))
        self._conn.commit()
