import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from logger import get_logger

log = get_logger(__name__)

PORTFOLIO_DB = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.db")

_db_lock = threading.Lock()


@dataclass
class Position:
    ticker: str
    shares: float
    entry_price: float
    entry_date: str
    stop_loss: float
    take_profit: float
    target_hold_days: int
    rationale: str = ""
    # Thesis tracking: used daily to detect if the original buy reason is still valid
    entry_catalysts: List[str] = field(default_factory=list)
    currency: str = "USD"           # Handelswährung (EUR/GBP/CHF/USD)
    fx_rate_at_entry: float = 1.0   # FX-Rate zur Basiswährung bei Kauf
    partial_tp_count: int = 0       # Anzahl ausgelöster Partial-TP-Stufen (0/1/2)

    @property
    def entry_value(self) -> float:
        return self.shares * self.entry_price


@dataclass
class Trade:
    ticker: str
    action: str              # "BUY" | "SELL"
    shares: float
    price: float
    timestamp: str
    pnl: float = 0.0
    reason: str = ""
    currency: str = "USD"
    fx_rate: float = 1.0


class Portfolio:
    def __init__(self, initial_capital: float = 10000.0):
        os.makedirs(os.path.dirname(PORTFOLIO_DB), exist_ok=True)
        self._conn = sqlite3.connect(PORTFOLIO_DB, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._setup_schema(initial_capital)
        self._migrate_schema()
        self._migrate_from_json()

    def _setup_schema(self, initial_capital: float) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS positions (
                    ticker TEXT PRIMARY KEY,
                    shares REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_date TEXT NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    target_hold_days INTEGER NOT NULL,
                    rationale TEXT DEFAULT '',
                    entry_catalysts TEXT DEFAULT '[]',
                    currency TEXT DEFAULT 'USD',
                    fx_rate_at_entry REAL DEFAULT 1.0
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    shares REAL NOT NULL,
                    price REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    pnl REAL DEFAULT 0.0,
                    reason TEXT DEFAULT '',
                    currency TEXT DEFAULT 'USD',
                    fx_rate REAL DEFAULT 1.0
                );
            """)
            # Only insert default cash if portfolio_meta is empty
            row = self._conn.execute(
                "SELECT value FROM portfolio_meta WHERE key='cash'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO portfolio_meta (key, value) VALUES ('cash', ?)",
                    (str(initial_capital),),
                )
                self._conn.execute(
                    "INSERT INTO portfolio_meta (key, value) VALUES ('created_at', ?)",
                    (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),),
                )

    def _migrate_schema(self) -> None:
        """Adds new columns to existing DBs without breaking old installs."""
        for stmt in [
            "ALTER TABLE positions ADD COLUMN currency TEXT DEFAULT 'USD'",
            "ALTER TABLE positions ADD COLUMN fx_rate_at_entry REAL DEFAULT 1.0",
            "ALTER TABLE positions ADD COLUMN partial_tp_taken INTEGER DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN currency TEXT DEFAULT 'USD'",
            "ALTER TABLE trades ADD COLUMN fx_rate REAL DEFAULT 1.0",
        ]:
            try:
                self._conn.execute(stmt)
            except Exception:
                pass  # Column already exists
        self._conn.commit()

    def _migrate_from_json(self) -> None:
        json_file = os.path.join(os.path.dirname(PORTFOLIO_DB), "portfolio.json")
        if not os.path.exists(json_file):
            return

        # Only migrate if positions table is still empty (fresh DB)
        existing = self._conn.execute("SELECT COUNT(*) FROM portfolio_meta WHERE key='migrated'").fetchone()[0]
        if existing:
            return

        try:
            with open(json_file) as f:
                data = json.load(f)

            with _db_lock:
                with self._conn:
                    # Migrate cash
                    cash = data.get("cash", 0.0)
                    self._conn.execute(
                        "UPDATE portfolio_meta SET value=? WHERE key='cash'",
                        (str(cash),),
                    )
                    created_at = data.get("created_at", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
                    self._conn.execute(
                        "UPDATE portfolio_meta SET value=? WHERE key='created_at'",
                        (created_at,),
                    )

                    # Migrate positions
                    for ticker, pos in data.get("positions", {}).items():
                        catalysts = pos.get("entry_catalysts", [])
                        self._conn.execute(
                            """
                            INSERT OR REPLACE INTO positions
                                (ticker, shares, entry_price, entry_date, stop_loss,
                                 take_profit, target_hold_days, rationale, entry_catalysts)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                ticker,
                                pos["shares"],
                                pos["entry_price"],
                                pos["entry_date"],
                                pos["stop_loss"],
                                pos["take_profit"],
                                pos["target_hold_days"],
                                pos.get("rationale", ""),
                                json.dumps(catalysts),
                            ),
                        )

                    # Migrate trades
                    for trade in data.get("trades", []):
                        self._conn.execute(
                            """
                            INSERT INTO trades
                                (ticker, action, shares, price, timestamp, pnl, reason)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                trade["ticker"],
                                trade["action"],
                                trade["shares"],
                                trade["price"],
                                trade["timestamp"],
                                trade.get("pnl", 0.0),
                                trade.get("reason", ""),
                            ),
                        )

                    # Mark migration done
                    self._conn.execute(
                        "INSERT INTO portfolio_meta (key, value) VALUES ('migrated', 'true')"
                    )

            os.rename(json_file, json_file + ".migrated")
            log.info("Portfolio: JSON-Daten erfolgreich nach SQLite migriert.")
        except Exception as e:
            log.error("Portfolio: Migration fehlgeschlagen – %s", e)

    def _get_cash(self) -> float:
        row = self._conn.execute(
            "SELECT value FROM portfolio_meta WHERE key='cash'"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def _set_cash(self, value: float) -> None:
        self._conn.execute(
            "UPDATE portfolio_meta SET value=? WHERE key='cash'",
            (str(value),),
        )

    @property
    def cash(self) -> float:
        return self._get_cash()

    def set_cash(self, value: float) -> None:
        """Setzt den Cash-Stand direkt (z.B. nach Broker-Sync oder Portfolio-Reset)."""
        with _db_lock:
            with self._conn:
                self._set_cash(max(0.0, value))

    def get_position(self, ticker: str) -> Optional[Position]:
        row = self._conn.execute(
            """
            SELECT ticker, shares, entry_price, entry_date, stop_loss,
                   take_profit, target_hold_days, rationale, entry_catalysts,
                   currency, fx_rate_at_entry, partial_tp_taken
            FROM positions WHERE ticker=?
            """,
            (ticker,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_position(row)

    def all_positions(self) -> Dict[str, Position]:
        rows = self._conn.execute(
            """
            SELECT ticker, shares, entry_price, entry_date, stop_loss,
                   take_profit, target_hold_days, rationale, entry_catalysts,
                   currency, fx_rate_at_entry, partial_tp_taken
            FROM positions
            """
        ).fetchall()
        return {row[0]: self._row_to_position(row) for row in rows}

    def open_position(self, position: Position, force: bool = False) -> None:
        """force=True überspringt den Cash-Gate: für Fälle, in denen der Broker
        die Order bereits ausgeführt hat (z.B. gefüllte IBKR-Limit-Order) und
        das Geld real schon ausgegeben ist – ein Cash-Mangel im Buch darf dann
        die Buchung nicht dauerhaft verhindern (sonst bleibt die Position für
        immer unverbucht, während der Broker sie längst hält)."""
        cost = position.entry_value
        with _db_lock:
            with self._conn:
                current_cash = self._get_cash()
                if cost > current_cash and not force:
                    raise ValueError(
                        f"Nicht genug Cash: benötigt ${cost:.2f}, verfügbar ${current_cash:.2f}"
                    )
                self._set_cash(current_cash - cost)
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO positions
                        (ticker, shares, entry_price, entry_date, stop_loss,
                         take_profit, target_hold_days, rationale, entry_catalysts,
                         currency, fx_rate_at_entry, partial_tp_taken)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position.ticker,
                        position.shares,
                        position.entry_price,
                        position.entry_date,
                        position.stop_loss,
                        position.take_profit,
                        position.target_hold_days,
                        position.rationale,
                        json.dumps(position.entry_catalysts),
                        position.currency,
                        position.fx_rate_at_entry,
                        position.partial_tp_count,
                    ),
                )
                self._insert_trade(
                    ticker=position.ticker,
                    action="BUY",
                    shares=position.shares,
                    price=position.entry_price,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    pnl=0.0,
                    reason=position.rationale,
                    currency=position.currency,
                    fx_rate=position.fx_rate_at_entry,
                )

    def close_position(self, ticker: str, current_price: float, reason: str = "") -> float:
        with _db_lock:
            with self._conn:
                pos = self.get_position(ticker)
                if not pos:
                    raise ValueError(f"Keine offene Position für {ticker}")
                proceeds = pos.shares * current_price
                pnl = proceeds - pos.entry_value
                self._set_cash(self._get_cash() + proceeds)
                self._conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
                self._insert_trade(
                    ticker=ticker,
                    action="SELL",
                    shares=pos.shares,
                    price=current_price,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    pnl=pnl,
                    reason=reason,
                    currency=pos.currency,
                    fx_rate=pos.fx_rate_at_entry,
                )
        return pnl

    def update_position_state(self, ticker: str, stop_loss: Optional[float] = None) -> None:
        """Einzelne Positions-Felder außerhalb eines Trades aktualisieren
        (aktuell nur Trailing-SL-Ratchet)."""
        if stop_loss is None:
            return
        with _db_lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE positions SET stop_loss=? WHERE ticker=?",
                    (stop_loss, ticker),
                )

    def update_partial_tp(
        self,
        ticker: str,
        new_shares: float,
        new_stop_loss: float,
        sell_shares: float,
        sell_price: float,
        pnl: float,
        new_count: int = 1,
        currency: str = "USD",
        fx_rate: float = 1.0,
    ) -> None:
        """Partial Take-Profit: reduziert Position, aktualisiert SL, bucht Erlös."""
        with _db_lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE positions SET shares=?, stop_loss=?, partial_tp_taken=? WHERE ticker=?",
                    (new_shares, new_stop_loss, new_count, ticker),
                )
                self._set_cash(self._get_cash() + sell_shares * sell_price)
                self._insert_trade(
                    ticker=ticker,
                    action="SELL",
                    shares=sell_shares,
                    price=sell_price,
                    timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    pnl=pnl,
                    reason="Partial-TP",
                    currency=currency,
                    fx_rate=fx_rate,
                )

    def total_value(self, prices: Dict[str, float]) -> float:
        total = self._get_cash()
        for ticker, pos in self.all_positions().items():
            price = prices.get(ticker, pos.entry_price)
            total += pos.shares * price
        return total

    def trade_history(self) -> List[Trade]:
        rows = self._conn.execute(
            """
            SELECT ticker, action, shares, price, timestamp, pnl, reason, currency, fx_rate
            FROM trades ORDER BY id ASC
            """
        ).fetchall()
        return [
            Trade(
                ticker=row[0],
                action=row[1],
                shares=row[2],
                price=row[3],
                timestamp=row[4],
                pnl=row[5],
                reason=row[6],
                currency=row[7] or "USD",
                fx_rate=float(row[8] or 1.0),
            )
            for row in rows
        ]

    def get_multicurrency_summary(self, prices: Dict[str, float]) -> Dict:
        """Unrealized P&L aufgeschlüsselt nach Handelswährung."""
        try:
            import yfinance as yf
            fx: Dict[str, float] = {"USD": 1.0}
            for ccy, sym in [("EUR", "EURUSD=X"), ("GBP", "GBPUSD=X"),
                              ("CHF", "CHFUSD=X"), ("SEK", "SEKUSD=X"),
                              ("NOK", "NOKUSD=X"), ("DKK", "DKKUSD=X")]:
                try:
                    fx[ccy] = float(yf.Ticker(sym).info.get("regularMarketPrice") or 1.0)
                except Exception:
                    fx[ccy] = 1.0
        except ImportError:
            fx = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CHF": 1.12, "SEK": 0.095}

        by_ccy: Dict[str, Dict] = {}
        for ticker, pos in self.all_positions().items():
            price = prices.get(ticker, pos.entry_price)
            pnl_local = (price - pos.entry_price) * pos.shares
            ccy = pos.currency or "USD"
            rate = fx.get(ccy, 1.0)
            if ccy not in by_ccy:
                by_ccy[ccy] = {"pnl_local": 0.0, "pnl_usd": 0.0, "tickers": [], "fx_rate": rate}
            by_ccy[ccy]["pnl_local"] += pnl_local
            by_ccy[ccy]["pnl_usd"] += pnl_local * rate
            by_ccy[ccy]["tickers"].append(ticker)

        return {
            "fx_rates": {k: round(v, 4) for k, v in fx.items()},
            "by_currency": {
                ccy: {
                    "pnl_local": round(d["pnl_local"], 2),
                    "pnl_usd": round(d["pnl_usd"], 2),
                    "fx_rate": round(d["fx_rate"], 4),
                    "tickers": d["tickers"],
                }
                for ccy, d in by_ccy.items()
            },
            "total_pnl_usd": round(sum(d["pnl_usd"] for d in by_ccy.values()), 2),
        }

    def _insert_trade(
        self,
        ticker: str,
        action: str,
        shares: float,
        price: float,
        timestamp: str,
        pnl: float,
        reason: str,
        currency: str = "USD",
        fx_rate: float = 1.0,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO trades (ticker, action, shares, price, timestamp, pnl, reason, currency, fx_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, action, shares, price, timestamp, pnl, reason, currency, fx_rate),
        )

    @staticmethod
    def _row_to_position(row) -> Position:
        (
            ticker, shares, entry_price, entry_date, stop_loss,
            take_profit, target_hold_days, rationale, entry_catalysts_json,
        ) = row[:9]
        try:
            catalysts = json.loads(entry_catalysts_json) if entry_catalysts_json else []
        except (json.JSONDecodeError, TypeError):
            catalysts = []
        col_names = row.keys() if hasattr(row, "keys") else []
        currency = row["currency"] if "currency" in col_names else "USD"
        fx_rate = row["fx_rate_at_entry"] if "fx_rate_at_entry" in col_names else 1.0
        partial_tp_count = int(row["partial_tp_taken"] or 0) if "partial_tp_taken" in col_names else 0
        return Position(
            ticker=ticker,
            shares=shares,
            entry_price=entry_price,
            entry_date=entry_date,
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=int(target_hold_days),
            rationale=rationale or "",
            entry_catalysts=catalysts,
            currency=currency or "USD",
            fx_rate_at_entry=float(fx_rate or 1.0),
            partial_tp_count=partial_tp_count,
        )
