"""
Walk-Forward Backtester – teilt historische Daten in überlappende Zeitfenster
und prüft ob die Strategie-Parameter über die Zeit stabil sind.

Kein Look-Ahead-Bias: Optimierung erfolgt ausschließlich auf Trainings-Daten;
Test-Daten bleiben bis zur Evaluierung unberührt.

Konzept:
    |--- Trainings-Fenster (6 Mo) ---|-- Test-Fenster (3 Mo) --|
              schiebt sich 3 Monate weiter
                       |--- Trainings-Fenster ---|-- Test-Fenster --|
                                        ...
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import yfinance as yf
from dateutil.relativedelta import relativedelta

log = logging.getLogger(__name__)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class WindowResult:
    start_date:   str
    end_date:     str
    phase:        str          # "TRAIN" | "TEST"
    sl_pct:       float
    tp_pct:       float
    hold_days:    int
    total_return: float        # in %
    win_rate:     float        # in %
    num_trades:   int
    max_drawdown: float


@dataclass
class WalkForwardResult:
    windows:                  List[WindowResult]
    avg_test_return:          float
    avg_train_return:         float
    walk_forward_efficiency:  float   # avg_test / avg_train
    best_params:              Dict
    stability_score:          float   # 0–100
    regime_breakdown:         Dict    # Return pro Regime
    recommendation:           str
    period_analyzed:          str
    total_windows:            int


# ── Haupt-Klasse ──────────────────────────────────────────────────────────────

class WalkForwardBacktester:
    def __init__(
        self,
        tickers: List[str],
        initial_capital: float = 10_000.0,
        train_months: int = 6,
        test_months: int = 3,
        total_years: int = 3,
        sl_range: List[float] = None,
        tp_range: List[float] = None,
        hold_range: List[int] = None,
        sentiment_threshold: float = 0.65,
        max_position_pct: float = 0.20,
        slippage_pct: float = 0.001,
        commission_pct: float = 0.001,
    ):
        self.tickers = tickers
        self.initial_capital = initial_capital
        self.train_months = train_months
        self.test_months = test_months
        self.total_years = total_years
        self.sl_range = sl_range if sl_range is not None else [0.05, 0.07, 0.09]
        self.tp_range = tp_range if tp_range is not None else [0.15, 0.20, 0.25]
        self.hold_range = hold_range if hold_range is not None else [10, 14, 21]
        self.threshold = sentiment_threshold
        self.max_pos_pct = max_position_pct
        self.slippage_pct = slippage_pct
        self.commission_pct = commission_pct

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> WalkForwardResult:
        """Lädt Daten, erstellt Fenster, optimiert und testet jedes Fenster."""
        log.info("Walk-Forward: lade %d Ticker …", len(self.tickers))
        all_data = self._load_data()
        if not all_data:
            raise RuntimeError("Keine historischen Kursdaten geladen.")

        dates = sorted(
            set.intersection(*[set(df.index.tolist()) for df in all_data.values()])
        )
        if len(dates) < 60:
            raise RuntimeError("Zu wenige gemeinsame Handelstage für Walk-Forward.")

        windows_list = self._build_windows(dates)
        log.info("Walk-Forward: %d Fensterpaar(e) erstellt.", len(windows_list))

        results: List[WindowResult] = []

        n_combos = len(self.sl_range) * len(self.tp_range) * len(self.hold_range)
        max_workers = max(1, min(n_combos, os.cpu_count() or 4))

        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            for train_dates, test_dates in windows_list:
                # --- Trainings-Fenster: Grid-Search (parallel über alle Kerne) ---
                try:
                    best_params, train_res = self._optimize_on_window(all_data, train_dates, pool)
                    results.append(train_res)
                except Exception as exc:
                    log.warning("Train-Fenster %s fehlgeschlagen: %s",
                                train_dates[0].strftime("%Y-%m"), exc)
                    continue

                # --- Test-Fenster: Validierung mit besten Parametern ---
                try:
                    test_res = self._run_window(
                        all_data, test_dates,
                        sl_pct=best_params["sl_pct"],
                        tp_pct=best_params["tp_pct"],
                        hold_days=best_params["hold_days"],
                        phase="TEST",
                    )
                    results.append(test_res)
                except Exception as exc:
                    log.warning("Test-Fenster %s fehlgeschlagen: %s",
                                test_dates[0].strftime("%Y-%m"), exc)

        return self._aggregate(results, dates)

    # ── Interne Helfer ────────────────────────────────────────────────────────

    def _load_data(self) -> Dict:
        period = f"{self.total_years}y"
        all_data: Dict = {}
        for t in self.tickers:
            try:
                df = yf.Ticker(t).history(period=period)
                if not df.empty and len(df) > 30:
                    all_data[t] = df
            except Exception as exc:
                log.warning("Daten für %s nicht geladen: %s", t, exc)
        return all_data

    def _build_windows(self, dates: List) -> List[Tuple[List, List]]:
        """
        Gibt Liste von (train_dates, test_dates) zurück.
        Schrittweite = test_months.
        """
        first = dates[0]
        last = dates[-1]
        windows = []
        cursor = first

        while True:
            train_end = cursor + relativedelta(months=self.train_months)
            test_end = train_end + relativedelta(months=self.test_months)
            if test_end > last:
                break

            train_dates = [d for d in dates if cursor <= d < train_end]
            test_dates = [d for d in dates if train_end <= d < test_end]

            if len(train_dates) >= 20 and len(test_dates) >= 10:
                windows.append((train_dates, test_dates))

            cursor += relativedelta(months=self.test_months)

        return windows

    def _optimize_on_window(
        self, all_data: Dict, train_dates: List, pool: ProcessPoolExecutor
    ) -> Tuple[Dict, WindowResult]:
        """
        Grid-Search über sl × tp × hold auf dem Trainings-Fenster.
        Jede Parameterkombination ist unabhängig → läuft parallel über den
        übergebenen Prozess-Pool (nutzt alle verfügbaren CPU-Kerne).
        """
        combos = [
            (sl, tp, hold)
            for sl in self.sl_range
            for tp in self.tp_range
            for hold in self.hold_range
        ]

        futures = {
            pool.submit(self._run_window, all_data, train_dates, sl, tp, hold, "TRAIN"): (sl, tp, hold)
            for sl, tp, hold in combos
        }

        best_score = float("-inf")
        best_params: Dict = {
            "sl_pct": self.sl_range[0],
            "tp_pct": self.tp_range[0],
            "hold_days": self.hold_range[0],
        }
        best_result: Optional[WindowResult] = None

        for fut in as_completed(futures):
            sl, tp, hold = futures[fut]
            try:
                res = fut.result()
            except Exception:
                continue

            score = (
                res.total_return
                * (res.win_rate / 50)
                * (1 - res.max_drawdown / 50)
            )
            if score > best_score:
                best_score = score
                best_params = {"sl_pct": sl, "tp_pct": tp, "hold_days": hold}
                best_result = res

        if best_result is None:
            raise RuntimeError("Keine gültige Parameterkombination gefunden.")

        return best_params, best_result

    def _run_window(
        self,
        all_data: Dict,
        dates: List,
        sl_pct: float,
        tp_pct: float,
        hold_days: int,
        phase: str = "TRAIN",
    ) -> WindowResult:
        """Simuliert die Strategie auf einem Zeitfenster (keine externen Klassen)."""
        if len(dates) < 22:
            raise ValueError("Fenster zu kurz für Simulation.")

        cash = self.initial_capital
        positions: Dict = {}
        trades: List[Dict] = []
        equity_curve: List[float] = []

        for i, date in enumerate(dates):
            if i < 21:
                equity_curve.append(cash)
                continue

            for ticker, df in all_data.items():
                if date not in df.index:
                    continue
                row = df.loc[date]
                price = float(row["Close"])

                # Exit-Logik
                if ticker in positions:
                    pos = positions[ticker]
                    days_held = (date - pos["entry_date"]).days
                    exit_reason = None
                    if price <= pos["stop_loss"]:
                        exit_reason = "stop_loss"
                    elif price >= pos["take_profit"]:
                        exit_reason = "take_profit"
                    elif days_held >= hold_days:
                        exit_reason = "hold_expired"
                    if exit_reason:
                        exit_price = price * (1 - self.slippage_pct)
                        commission = pos["shares"] * exit_price * self.commission_pct
                        proceeds = pos["shares"] * exit_price - commission
                        pnl = proceeds - pos["cost"]
                        cash += proceeds
                        trades.append({"pnl": pnl, "cost": pos["cost"]})
                        del positions[ticker]
                        continue

                # Entry-Logik – Sentiment-Proxy (kein Look-Ahead-Bias)
                if ticker not in positions:
                    window_df = df.loc[:date].tail(21)
                    if len(window_df) < 21:
                        continue
                    momentum = (
                        price - float(window_df["Close"].iloc[0])
                    ) / float(window_df["Close"].iloc[0])
                    avg_vol = float(window_df["Volume"].iloc[:-1].mean()) or 1.0
                    vol_ratio = float(row["Volume"]) / avg_vol
                    score = self._sentiment_proxy(momentum, vol_ratio)
                    if score < self.threshold:
                        continue

                    pos_value = self._positions_value(positions, all_data, date)
                    invest = min(
                        cash * 0.95,
                        (cash + pos_value) * self.max_pos_pct,
                    )
                    if invest < 100:
                        continue

                    entry_price = price * (1 + self.slippage_pct)
                    commission = invest * self.commission_pct
                    net_invest = invest - commission
                    shares = net_invest / entry_price
                    cost = shares * entry_price + commission
                    cash -= cost
                    positions[ticker] = {
                        "entry_date": date,
                        "entry_price": entry_price,
                        "shares": shares,
                        "cost": cost,
                        "stop_loss": entry_price * (1 - sl_pct),
                        "take_profit": entry_price * (1 + tp_pct),
                    }

            equity_curve.append(
                cash + self._positions_value(positions, all_data, date)
            )

        # Offene Positionen am Fenster-Ende schliessen
        if dates:
            last_date = dates[-1]
            for ticker, pos in list(positions.items()):
                if last_date in all_data[ticker].index:
                    price = float(all_data[ticker].loc[last_date]["Close"])
                    exit_price = price * (1 - self.slippage_pct)
                    commission = pos["shares"] * exit_price * self.commission_pct
                    proceeds = pos["shares"] * exit_price - commission
                    cash += proceeds
                    trades.append({"pnl": proceeds - pos["cost"], "cost": pos["cost"]})

        final = cash
        total_return = (final - self.initial_capital) / self.initial_capital * 100
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / len(trades) * 100 if trades else 0.0
        max_dd = self._max_drawdown(equity_curve)

        return WindowResult(
            start_date=dates[0].strftime("%Y-%m-%d"),
            end_date=dates[-1].strftime("%Y-%m-%d"),
            phase=phase,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            hold_days=hold_days,
            total_return=round(total_return, 2),
            win_rate=round(win_rate, 1),
            num_trades=len(trades),
            max_drawdown=round(max_dd, 2),
        )

    # ── Aggregation ───────────────────────────────────────────────────────────

    def _aggregate(self, results: List[WindowResult], all_dates: List) -> WalkForwardResult:
        train_windows = [w for w in results if w.phase == "TRAIN"]
        test_windows = [w for w in results if w.phase == "TEST"]

        avg_train = (
            sum(w.total_return for w in train_windows) / len(train_windows)
            if train_windows else 0.0
        )
        avg_test = (
            sum(w.total_return for w in test_windows) / len(test_windows)
            if test_windows else 0.0
        )

        wfe = (avg_test / avg_train) if avg_train > 0 else 0.0

        # Stabilität: wie oft wurde der gleiche Parameter-Satz gewählt
        stability_score = 0.0
        best_params: Dict = {}
        if train_windows:
            param_counts: Counter = Counter(
                (w.sl_pct, w.tp_pct, w.hold_days) for w in train_windows
            )
            top = param_counts.most_common(1)[0]
            stability_score = top[1] / len(train_windows) * 100
            best_params = {
                "sl_pct": top[0][0],
                "tp_pct": top[0][1],
                "hold_days": top[0][2],
            }

        # Regime-Breakdown via VIX-Proxy (Volatilität der Test-Fenster)
        regime_breakdown = self._regime_breakdown(test_windows)

        # Empfehlung
        if wfe >= 0.80:
            recommendation = "STABILE STRATEGIE – Parameter generalisieren gut"
        elif wfe >= 0.50:
            recommendation = "LEICHTES OVERFITTING – Parameter mit Vorsicht nutzen"
        else:
            recommendation = (
                "STARKES OVERFITTING – Parameter nicht auf echte Daten übertragen"
            )

        period_analyzed = (
            f"{all_dates[0].strftime('%Y-%m')} bis "
            f"{all_dates[-1].strftime('%Y-%m')} ({self.total_years} Jahre)"
            if all_dates else "unbekannt"
        )

        return WalkForwardResult(
            windows=results,
            avg_test_return=round(avg_test, 2),
            avg_train_return=round(avg_train, 2),
            walk_forward_efficiency=round(wfe, 3),
            best_params=best_params,
            stability_score=round(stability_score, 1),
            regime_breakdown=regime_breakdown,
            recommendation=recommendation,
            period_analyzed=period_analyzed,
            total_windows=len(test_windows),
        )

    def _regime_breakdown(self, test_windows: List[WindowResult]) -> Dict:
        """
        Ordnet Test-Fenster einem Regime zu anhand eines VIX-Proxys.
        Proxy: Ø Max-Drawdown des Fensters als Volatilitäts-Indikator.
          < 5   → BULL
          5–15  → NEUTRAL
          > 15  → BEAR
        """
        breakdown: Dict[str, List[float]] = {"BULL": [], "NEUTRAL": [], "BEAR": []}
        for w in test_windows:
            if w.max_drawdown < 5:
                breakdown["BULL"].append(w.total_return)
            elif w.max_drawdown <= 15:
                breakdown["NEUTRAL"].append(w.total_return)
            else:
                breakdown["BEAR"].append(w.total_return)
        return {
            regime: round(sum(v) / len(v), 2) if v else None
            for regime, v in breakdown.items()
        }

    # ── Strategie-Hilfsfunktionen ─────────────────────────────────────────────

    def _sentiment_proxy(self, momentum: float, vol_ratio: float) -> float:
        """Maps momentum (-1..1) + vol_ratio (0..5+) auf einen 0..1 Score."""
        m_score = max(0, min(1, (momentum + 0.05) * 5))
        v_score = max(0, min(1, (vol_ratio - 0.8) / 2))
        return 0.6 * m_score + 0.4 * v_score

    def _positions_value(self, positions: Dict, all_data: Dict, date) -> float:
        total = 0.0
        for ticker, pos in positions.items():
            if date in all_data[ticker].index:
                total += pos["shares"] * float(all_data[ticker].loc[date]["Close"])
        return total

    def _max_drawdown(self, curve: List[float]) -> float:
        if not curve:
            return 0.0
        peak = curve[0]
        max_dd = 0.0
        for equity in curve:
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)
