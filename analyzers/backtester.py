"""
Backtester – simulates the strategy on historical price data.
Uses a simplified sentiment proxy from price momentum and volume (since real news
sentiment isn't available historically), so it primarily validates risk parameters
(SL/TP/hold-days) rather than the Claude-driven sentiment signals.
"""
from datetime import datetime
from typing import List, Dict, Optional
import yfinance as yf


class Backtester:
    def __init__(
        self,
        tickers: List[str],
        initial_capital: float = 10_000.0,
        stop_loss_pct: float = 0.08,
        take_profit_pct: float = 0.25,
        hold_days: int = 14,
        max_position_pct: float = 0.20,
        sentiment_threshold: float = 0.65,
    ):
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.sl_pct = stop_loss_pct
        self.tp_pct = take_profit_pct
        self.hold_days = hold_days
        self.max_pos_pct = max_position_pct
        self.threshold = sentiment_threshold

    def run(self, period: str = "2y") -> Dict:
        """
        Returns dict with: total_return_pct, win_rate, num_trades, equity_curve, trades.
        """
        all_data: Dict[str, "object"] = {}
        for t in self.tickers:
            try:
                df = yf.Ticker(t).history(period=period)
                if not df.empty and len(df) > 30:
                    all_data[t] = df
            except Exception:
                continue

        if not all_data:
            return {"error": "Keine historischen Daten geladen."}

        cash = self.initial_capital
        positions: Dict[str, Dict] = {}
        trades: List[Dict] = []
        equity_curve: List[Dict] = []

        # Common date axis (intersection of all tickers)
        dates = sorted(set.intersection(*[set(df.index.tolist()) for df in all_data.values()]))
        if len(dates) < 30:
            return {"error": "Zu wenige gemeinsame Handelstage."}

        for i, date in enumerate(dates):
            if i < 21:
                continue
            for ticker, df in all_data.items():
                if date not in df.index:
                    continue
                row = df.loc[date]
                price = float(row["Close"])

                # Exit logic
                if ticker in positions:
                    pos = positions[ticker]
                    days_held = (date - pos["entry_date"]).days
                    exit_reason = None
                    if price <= pos["stop_loss"]:
                        exit_reason = "stop_loss"
                    elif price >= pos["take_profit"]:
                        exit_reason = "take_profit"
                    elif days_held >= self.hold_days:
                        exit_reason = "hold_expired"
                    if exit_reason:
                        proceeds = pos["shares"] * price
                        pnl = proceeds - pos["cost"]
                        cash += proceeds
                        trades.append({
                            "ticker": ticker,
                            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                            "exit_date": date.strftime("%Y-%m-%d"),
                            "entry_price": pos["entry_price"],
                            "exit_price": price,
                            "shares": pos["shares"],
                            "pnl": round(pnl, 2),
                            "return_pct": round(pnl / pos["cost"] * 100, 2),
                            "reason": exit_reason,
                        })
                        del positions[ticker]
                        continue

                # Entry logic – sentiment proxy: 21-day momentum + volume surge
                if ticker not in positions:
                    window = df.loc[:date].tail(21)
                    if len(window) < 21:
                        continue
                    momentum = (price - float(window["Close"].iloc[0])) / float(window["Close"].iloc[0])
                    avg_vol = float(window["Volume"].iloc[:-1].mean()) or 1
                    vol_ratio = float(row["Volume"]) / avg_vol
                    score = self._sentiment_proxy(momentum, vol_ratio)
                    if score < self.threshold:
                        continue
                    invest = min(cash * 0.95, (cash + self._positions_value(positions, all_data, date)) * self.max_pos_pct)
                    if invest < 100:
                        continue
                    shares = invest / price
                    cost = shares * price
                    cash -= cost
                    positions[ticker] = {
                        "entry_date": date,
                        "entry_price": price,
                        "shares": shares,
                        "cost": cost,
                        "stop_loss": price * (1 - self.sl_pct),
                        "take_profit": price * (1 + self.tp_pct),
                    }

            total_equity = cash + self._positions_value(positions, all_data, date)
            equity_curve.append({"date": date.strftime("%Y-%m-%d"), "equity": round(total_equity, 2)})

        # Close out remaining positions at last price
        last_date = dates[-1]
        for ticker, pos in list(positions.items()):
            price = float(all_data[ticker].loc[last_date]["Close"])
            proceeds = pos["shares"] * price
            cash += proceeds
            trades.append({
                "ticker": ticker,
                "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                "exit_date": last_date.strftime("%Y-%m-%d"),
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "shares": pos["shares"],
                "pnl": round(proceeds - pos["cost"], 2),
                "return_pct": round((proceeds - pos["cost"]) / pos["cost"] * 100, 2),
                "reason": "end_of_test",
            })

        final = cash
        wins = sum(1 for t in trades if t["pnl"] > 0)
        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(final, 2),
            "total_return_pct": round((final - self.initial_capital) / self.initial_capital * 100, 2),
            "num_trades": len(trades),
            "win_rate_pct": round(wins / len(trades) * 100, 1) if trades else 0,
            "avg_return_pct": round(sum(t["return_pct"] for t in trades) / len(trades), 2) if trades else 0,
            "best_trade": max(trades, key=lambda t: t["return_pct"]) if trades else None,
            "worst_trade": min(trades, key=lambda t: t["return_pct"]) if trades else None,
            "max_drawdown_pct": self._max_drawdown(equity_curve),
            "equity_curve": equity_curve,
            "trades": trades,
        }

    def _sentiment_proxy(self, momentum: float, vol_ratio: float) -> float:
        """Maps momentum (-1..1) + vol_ratio (0..5+) to a 0..1 score."""
        m_score = max(0, min(1, (momentum + 0.05) * 5))
        v_score = max(0, min(1, (vol_ratio - 0.8) / 2))
        return 0.6 * m_score + 0.4 * v_score

    def _positions_value(self, positions: Dict, all_data: Dict, date) -> float:
        total = 0.0
        for ticker, pos in positions.items():
            if date in all_data[ticker].index:
                total += pos["shares"] * float(all_data[ticker].loc[date]["Close"])
        return total

    def _max_drawdown(self, curve: List[Dict]) -> float:
        if not curve:
            return 0.0
        peak = curve[0]["equity"]
        max_dd = 0.0
        for point in curve:
            if point["equity"] > peak:
                peak = point["equity"]
            dd = (peak - point["equity"]) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)
