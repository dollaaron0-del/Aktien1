"""
OrderLog – persistenter Order-Lifecycle-Log (Roadmap 1.5f).

Jede OrderResult, die PaperBroker/IBKRBroker aus buy()/sell()/buy_crypto()/
sell_crypto() zurückgeben, wird hier protokolliert – über den log_order()-
Decorator EINMAL um die vier Methoden gelegt, statt an jedem der zahlreichen
Aufrufer (TradeExecutor, HedgeStrategy, ShortStrategy, EarningsStrategy) oder
jedem einzelnen return-Pfad innerhalb der Broker-Methoden (IBKR hat mehrere
Fehler-Returns pro Methode). Der Decorator sitzt AUSSEN um die Methode, sieht
also jedes Ergebnis, egal welcher interne return-Pfad gegriffen hat – ohne die
Methodenkörper selbst anzufassen (geringeres Risiko in echtem Handelscode).

Fail-open: ein Logging-Fehler darf eine Order nie verhindern oder verändern.
"""
from __future__ import annotations

import functools
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "order_log.db")


class OrderLog:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL,
                ticker      TEXT,
                action      TEXT,      -- BUY | SELL
                mode        TEXT,      -- paper | ibkr
                status      TEXT,      -- filled | error | cancelled
                shares      REAL,
                fill_price  REAL,
                order_id    INTEGER,
                partial     INTEGER,
                reason      TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);
        """)
        self._conn.commit()
        self._migrate_locked()

    def _migrate_locked(self) -> None:
        """Idempotente Spalten-Migration (Muster: analyzers/decision_log.py).

        market_price (Roadmap 5.3): Referenzpreis zum Entscheidungszeitpunkt,
        von ibkr_broker._place_order() durchgereicht – Basis für die
        Slippage-Kalibrierung aus echten Fills. NULL bei Alt-Zeilen und beim
        PaperBroker (der berechnet Slippage bereits intern anders, s.
        _calc_slippage)."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(orders)")}
        if "market_price" not in have:
            self._conn.execute("ALTER TABLE orders ADD COLUMN market_price REAL")
        self._conn.commit()

    def record(self, order_result: Dict, action: str) -> Optional[int]:
        try:
            d = order_result or {}
            cur = self._conn.execute(
                """INSERT INTO orders
                   (ts, ticker, action, mode, status, shares, fill_price,
                    order_id, partial, reason, market_price)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    d.get("ticker"),
                    action,
                    d.get("mode"),
                    d.get("status"),
                    d.get("shares"),
                    d.get("fill_price"),
                    d.get("order_id"),
                    1 if d.get("partial") else 0,
                    d.get("reason"),
                    d.get("market_price"),
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception:
            return None

    def recent(self, limit: int = 50) -> List[Dict]:
        try:
            rows = self._conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


_instance: Optional[OrderLog] = None


def get_order_log() -> OrderLog:
    global _instance
    if _instance is None:
        _instance = OrderLog()
    return _instance


def log_order(action: str):
    """Decorator für Broker-Methoden (buy/sell/buy_crypto/sell_crypto):
    protokolliert das zurückgegebene OrderResult-Dict, unabhängig davon,
    welcher interne return-Pfad gegriffen hat. Fail-open und
    ergebnis-transparent – wirft der Logging-Aufruf, geht das Order-Ergebnis
    trotzdem unverändert an den Aufrufer zurück."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            result = fn(self, *args, **kwargs)
            try:
                get_order_log().record(result, action)
            except Exception:
                pass
            return result
        return wrapper
    return deco
