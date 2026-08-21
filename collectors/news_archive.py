import sqlite3
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict

ARCHIVE_DB = os.path.join(os.path.dirname(__file__), "..", "data", "news_archive.db")


class NewsArchive:
    """
    Stores all collected news items in SQLite for up to 30 days.
    Provides historical context so Claude can compare current news
    against past coverage and detect trend changes.
    """

    def __init__(self):
        os.makedirs(os.path.dirname(ARCHIVE_DB), exist_ok=True)
        self._conn = sqlite3.connect(ARCHIVE_DB, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS news_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT NOT NULL,
                source      TEXT NOT NULL,
                title       TEXT NOT NULL,
                text        TEXT,
                url         TEXT,
                published_at TEXT,
                collected_at TEXT NOT NULL,
                UNIQUE(ticker, title)
            )
        """)
        self._conn.commit()

    def store(self, ticker: str, items: List[Dict]):
        """Saves new items; silently ignores duplicates (same ticker+title)."""
        collected_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        for item in items:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO news_items
                       (ticker, source, title, text, url, published_at, collected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker,
                        item.get("source", "Unbekannt"),
                        (item.get("title") or "")[:500],
                        (item.get("text") or "")[:3000],
                        item.get("url", ""),
                        item.get("published_at", ""),
                        collected_at,
                    ),
                )
            except Exception:
                pass
        self._conn.commit()

    def get_history(self, ticker: str, days: int = 30, exclude_titles: set = None) -> List[Dict]:
        """
        Returns up to 80 archived items for the given ticker from the last `days` days.
        Optionally excludes titles that are already in the current news batch.
        """
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT source, title, text, published_at
               FROM news_items
               WHERE ticker=? AND collected_at >= ?
               ORDER BY published_at DESC
               LIMIT 80""",
            (ticker, cutoff),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        if exclude_titles:
            rows = [r for r in rows if r["title"] not in exclude_titles]
        return rows

    def cleanup_old(self, keep_days: int = 32):
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=keep_days)).isoformat()
        self._conn.execute("DELETE FROM news_items WHERE collected_at < ?", (cutoff,))
        self._conn.commit()
