"""
PromptArchive – KI-Prompt-Archiv (Roadmap 1.4d).

Speichert vollen System-/User-Prompt + Antworttext je ECHTEM Claude-Aufruf,
verkettet über analysis_id mit analysis_log (Roadmap 1.4b). Basis für
Entscheidungs-Replay (Roadmap 4.5): "warum hat er das gekauft?" wird damit
reproduzierbar statt Log-Archäologie. Ollama-/Frugal-Routen liefern keinen
Eintrag (nur Claude – teuerste Stufe, siehe claude_analyzer.raw_response).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "prompt_archive.db")


class PromptArchive:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS prompts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id    INTEGER,
                ticker         TEXT,
                created_at     TEXT,
                model          TEXT,
                system_prompt  TEXT,
                user_prompt    TEXT,
                response_text  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_prompts_analysis_id
                ON prompts(analysis_id);
        """)
        self._conn.commit()

    def store(
        self, analysis_id: Optional[int], ticker: str, model: str,
        system_prompt: str, user_prompt: str, response_text: str,
    ) -> Optional[int]:
        """Fail-open: ein Archiv-Fehler darf die laufende Analyse nie
        abreißen lassen – nur loggen tut der Aufrufer."""
        try:
            cur = self._conn.execute(
                """INSERT INTO prompts
                   (analysis_id, ticker, created_at, model,
                    system_prompt, user_prompt, response_text)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    analysis_id, ticker,
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    model, system_prompt, user_prompt, response_text,
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception:
            return None

    def get_by_analysis_id(self, analysis_id: int) -> Optional[Dict]:
        try:
            row = self._conn.execute(
                "SELECT * FROM prompts WHERE analysis_id = ? ORDER BY id DESC LIMIT 1",
                (analysis_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
