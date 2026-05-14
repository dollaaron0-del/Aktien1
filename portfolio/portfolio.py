import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

from logger import get_logger

log = get_logger(__name__)

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.json")


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


@dataclass
class PortfolioState:
    cash: float
    positions: Dict[str, dict] = field(default_factory=dict)
    trades: List[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class Portfolio:
    def __init__(self, initial_capital: float = 10000.0):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        self._state = self._load(initial_capital)

    def _load(self, initial_capital: float) -> PortfolioState:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE) as f:
                data = json.load(f)
            # Backward-compat: add entry_catalysts if missing
            for pos in data.get("positions", {}).values():
                pos.setdefault("entry_catalysts", [])
            return PortfolioState(**data)
        return PortfolioState(cash=initial_capital)

    def _save(self):
        """Atomar schreiben: erst Temp-Datei, dann umbenennen – crash-sicher."""
        tmp_path = None
        try:
            dir_ = os.path.dirname(DATA_FILE)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=dir_, suffix=".tmp", delete=False
            ) as tmp:
                json.dump(asdict(self._state), tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, DATA_FILE)
        except Exception as e:
            log.error("Portfolio: Speicherfehler – %s", e)
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @property
    def cash(self) -> float:
        return self._state.cash

    def get_position(self, ticker: str) -> Optional[Position]:
        raw = self._state.positions.get(ticker)
        if raw:
            return Position(**raw)
        return None

    def all_positions(self) -> Dict[str, Position]:
        return {t: Position(**v) for t, v in self._state.positions.items()}

    def open_position(self, position: Position):
        cost = position.entry_value
        if cost > self._state.cash:
            raise ValueError(
                f"Nicht genug Cash: benötigt ${cost:.2f}, verfügbar ${self._state.cash:.2f}"
            )
        self._state.cash -= cost
        self._state.positions[position.ticker] = asdict(position)
        self._record_trade(Trade(
            ticker=position.ticker,
            action="BUY",
            shares=position.shares,
            price=position.entry_price,
            timestamp=datetime.utcnow().isoformat(),
            reason=position.rationale,
        ))
        self._save()

    def close_position(self, ticker: str, current_price: float, reason: str = "") -> float:
        pos = self.get_position(ticker)
        if not pos:
            raise ValueError(f"Keine offene Position für {ticker}")
        proceeds = pos.shares * current_price
        pnl = proceeds - pos.entry_value
        self._state.cash += proceeds
        del self._state.positions[ticker]
        self._record_trade(Trade(
            ticker=ticker,
            action="SELL",
            shares=pos.shares,
            price=current_price,
            timestamp=datetime.utcnow().isoformat(),
            pnl=pnl,
            reason=reason,
        ))
        self._save()
        return pnl

    def total_value(self, prices: Dict[str, float]) -> float:
        total = self._state.cash
        for ticker, pos_data in self._state.positions.items():
            pos = Position(**pos_data)
            price = prices.get(ticker, pos.entry_price)
            total += pos.shares * price
        return total

    def trade_history(self) -> List[Trade]:
        return [Trade(**t) for t in self._state.trades]

    def _record_trade(self, trade: Trade):
        self._state.trades.append(asdict(trade))
