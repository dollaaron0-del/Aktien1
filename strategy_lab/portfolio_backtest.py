"""
Portfolio-Level-Backtest (Roadmap 2.2) – realistische Equity-Kurven statt
per-Ticker-Durchschnitt.

`backtesting/metrics.py::compute()`/`aggregate()` bewerten Trades bisher pro
Ticker isoliert ("$1 pro Trade, gleichgewichtet") und mitteln die Kennzahlen
über die Ticker – ohne gemeinsamen Cash-Pool, ohne Positions-Limit, ohne
Korrelations-Kappung, ohne Vol-Targeting. In der Realität ist Kapital endlich:
nicht jedes Signal auf jedem Ticker kann gleichzeitig genommen werden.

Dieses Modul simuliert stattdessen EIN Portfolio über einen `weight_plan()`-
förmigen Plan (Strategie → Gewicht) hinweg: sammelt die (bereits bestehenden,
unveränderten) Trade-Listen jeder (Strategie, Ticker)-Kombination über
`Strategy.runner()`, verschmilzt sie chronologisch zu Open/Close-Events und
simuliert Cash/Positions-Limit/Themen-Kappung/Vol-Targeting event-getrieben.

Bewusst KEIN tägliches Mark-to-Market: `Trade` trägt nur Entry/Exit-Datum und
einen finalen, geblendeten `return_pct` (kein Kurs-Pfad dazwischen) – die
Equity-Kurve wird daher wie im übrigen Repo üblich auf Trade-Ebene (bei jedem
CLOSE) fortgeschrieben, nicht auf Tagesbasis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from backtesting import data_loader
from backtesting.engine import Trade, _atr
from strategy_lab.strategies import get


# ── Konfiguration ────────────────────────────────────────────────────────────

@dataclass
class VolTargetConfig:
    """Portfolio-seitiges Vol-Targeting – spiegelt die Formel/Form von
    strategy/swing_strategy.py::_atr_vol_multiplier (Live-Bot), aber als
    eigene Config (kein Bezug zu BacktestConfig – das ist Exit-Logik pro
    Trade, hier geht es um Positionsgröße auf Portfolio-Ebene)."""
    atr_period: int = 14
    target_pct: float = 0.025    # "normale" Tages-ATR = 2.5 % (wie Live-Bot)
    size_floor: float = 0.50
    size_cap: float = 1.50
    enabled: bool = True


# ── Ergebnis-Datentypen ──────────────────────────────────────────────────────

@dataclass
class PortfolioTrade:
    trade: Trade
    strategy: str
    weight: float
    ticker: str
    df: pd.DataFrame   # Referenz (keine Kopie) – für kausale ATR-am-Entry-Abfrage


@dataclass
class SkipReasons:
    cash: int = 0
    max_positions: int = 0
    theme_cap: int = 0


@dataclass
class PortfolioBacktestResult:
    initial_capital: float
    final_equity: float
    total_return: float
    max_drawdown: float              # Peak-to-Trough auf der Dollar-Equity-Kurve
    sharpe: float                    # datumsbasiert (Tages-Resampling) – NICHT
                                      # dieselbe Formel wie TickerMetrics.sharpe
                                      # (dort trade-count-basiert)
    cagr: float
    n_trades_taken: int
    n_trades_skipped: int
    skip_reasons: SkipReasons
    peak_concurrent_positions: int
    capital_utilization: float       # zeitgewichteter Ø deployed$/equity
    equity_curve: List[Tuple[pd.Timestamp, float]] = field(default_factory=list)
    trades_taken: List[PortfolioTrade] = field(default_factory=list)
    trades_skipped: List[Tuple[PortfolioTrade, str]] = field(default_factory=list)


# ── Plan-Konstruktion ────────────────────────────────────────────────────────

def equal_weight_plan(strategy_names: List[str]) -> List[Dict]:
    """Naiver Vergleichs-Plan (Roadmap 2.2 Punkt 7): Gleichgewichtung über die
    übergebenen Strategien, Default-Parameter. KEIN Ersatz für
    strategy_lab.allocator.weight_plan() – nur die Baseline für den Vergleich."""
    n = len(strategy_names)
    if n == 0:
        return []
    w = 1.0 / n
    return [{"strategy": s, "params": {}, "weight": w, "risk_fraction": w, "regime_fit": None}
            for s in strategy_names]


# ── Bausteine ────────────────────────────────────────────────────────────────

def _vol_multiplier(df: pd.DataFrame, entry_idx: int, cfg: VolTargetConfig) -> float:
    """Kausaler Vol-Targeting-Multiplikator am Entry-Balken. Fail-open (1.0)
    bei jedem Fehler/fehlendem ATR – gleiche Haltung wie der Live-Bot."""
    if not cfg.enabled:
        return 1.0
    try:
        atr = float(_atr(df, cfg.atr_period).iloc[entry_idx])
        price = float(df["Close"].iloc[entry_idx])
        if not math.isfinite(atr) or atr <= 0 or price <= 0:
            return 1.0
        mult = cfg.target_pct / (atr / price)
        return max(cfg.size_floor, min(cfg.size_cap, mult))
    except Exception:
        return 1.0


def _theme_cap_violated(open_tickers, candidate: str, relations, cap: int,
                         _theme_cache: Optional[Dict[str, List[str]]] = None) -> bool:
    """True, wenn das Öffnen von `candidate` irgendein gemeinsames Thema mit
    den bereits offenen Positionen über `cap` heben würde. Ticker ohne
    Themen-Zugehörigkeit werden NIE geblockt."""
    def _themes(t: str) -> List[str]:
        if _theme_cache is None:
            return relations.get_themes(t)
        if t not in _theme_cache:
            _theme_cache[t] = relations.get_themes(t)
        return _theme_cache[t]

    candidate_themes = set(_themes(candidate))
    if not candidate_themes:
        return False
    for theme in candidate_themes:
        count = sum(1 for t in open_tickers if theme in _themes(t))
        if count >= cap:
            return True
    return False


@dataclass
class _Event:
    date: pd.Timestamp
    kind: str      # "OPEN" | "CLOSE"
    trade_id: int


def _build_events(trades: List[PortfolioTrade]) -> List[_Event]:
    """Zwei Events je Trade, sortiert nach (Datum, CLOSE-vor-OPEN, trade_id) –
    so wird an einem Tag zuerst Kapital durch einen Exit frei, bevor ein neuer
    Entry am selben Tag darum konkurriert."""
    events: List[_Event] = []
    for i, pt in enumerate(trades):
        events.append(_Event(pt.trade.entry_date, "OPEN", i))
        if pt.trade.exit_date is not None:
            # engine._simulate schließt Trades immer zwangsweise (force_close_
            # at_boundary=True) – exit_date sollte nie None sein; Guard bleibt
            # defensiv für händisch gebaute Test-Trades.
            events.append(_Event(pt.trade.exit_date, "CLOSE", i))
    events.sort(key=lambda e: (e.date, 0 if e.kind == "CLOSE" else 1, e.trade_id))
    return events


def _collect_trades(
    plan: List[Dict],
    universe: List[str],
    years: int,
    loader: Callable[[str, int], object],
) -> List[PortfolioTrade]:
    df_cache: Dict[str, Optional[pd.DataFrame]] = {}
    out: List[PortfolioTrade] = []
    for entry in plan:
        strat = get(entry["strategy"])
        weight = entry["weight"]
        params = entry.get("params") or {}
        for ticker in universe:
            if ticker not in df_cache:
                df = loader(ticker, years)
                df_cache[ticker] = df if (df is not None and len(df) >= 60) else None
            df = df_cache[ticker]
            if df is None:
                continue
            for t in strat.runner(df, ticker, params):
                out.append(PortfolioTrade(trade=t, strategy=entry["strategy"],
                                          weight=weight, ticker=ticker, df=df))
    return out


def _sharpe_and_cagr(equity_curve: List[Tuple[pd.Timestamp, float]],
                     initial_capital: float, final_equity: float) -> Tuple[float, float]:
    if len(equity_curve) < 2:
        return 0.0, 0.0
    start_date, end_date = equity_curve[0][0], equity_curve[-1][0]
    days = (end_date - start_date).days
    cagr = ((final_equity / initial_capital) ** (365.25 / days) - 1) if days > 0 else 0.0

    idx = pd.DatetimeIndex([p[0] for p in equity_curve])
    vals = pd.Series([p[1] for p in equity_curve], index=idx)
    vals = vals[~vals.index.duplicated(keep="last")].sort_index()
    daily = vals.reindex(pd.date_range(vals.index[0], vals.index[-1], freq="B")).ffill()
    rets = daily.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=1) == 0:
        return 0.0, cagr
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252))
    return sharpe, cagr


def _max_drawdown(equity_curve: List[Tuple[pd.Timestamp, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    worst = 0.0
    for _, val in equity_curve:
        peak = max(peak, val)
        if peak > 0:
            worst = min(worst, (val - peak) / peak)
    return worst


def _simulate_events(
    events: List[_Event],
    trades: List[PortfolioTrade],
    initial_capital: float,
    max_positions: int,
    max_positions_per_theme: int,
    vol_target_cfg: VolTargetConfig,
    relations,
) -> PortfolioBacktestResult:
    cash = initial_capital
    open_positions: Dict[int, Dict] = {}   # trade_id -> {"dollars", "ticker"}
    equity_curve: List[Tuple[pd.Timestamp, float]] = []
    skip_reasons = SkipReasons()
    trades_taken: List[PortfolioTrade] = []
    trades_skipped: List[Tuple[PortfolioTrade, str]] = []
    peak_concurrent = 0
    theme_cache: Dict[str, List[str]] = {}

    utilization_weighted_sum = 0.0
    utilization_total_days = 0.0
    last_event_date: Optional[pd.Timestamp] = None

    def _deployed() -> float:
        return sum(p["dollars"] for p in open_positions.values())

    def _record_utilization(date: pd.Timestamp, equity: float) -> None:
        nonlocal utilization_weighted_sum, utilization_total_days, last_event_date
        if last_event_date is not None and equity > 0:
            gap_days = max((date - last_event_date).days, 0)
            if gap_days > 0:
                util = _deployed() / equity
                utilization_weighted_sum += util * gap_days
                utilization_total_days += gap_days
        last_event_date = date

    for event in events:
        pt = trades[event.trade_id]

        if event.kind == "CLOSE":
            if event.trade_id not in open_positions:
                continue   # bei OPEN bereits geskippt
            equity_before = cash + _deployed()
            _record_utilization(event.date, equity_before)
            pos = open_positions.pop(event.trade_id)
            cash += pos["dollars"] * (1 + pt.trade.return_pct)
            equity_curve.append((event.date, cash + _deployed()))
            continue

        # OPEN
        current_equity = cash + _deployed()
        _record_utilization(event.date, current_equity)

        entry_idx = pt.df.index.get_loc(pt.trade.entry_date)
        vol_mult = _vol_multiplier(pt.df, entry_idx, vol_target_cfg)
        target = pt.weight * current_equity * vol_mult

        if len(open_positions) >= max_positions:
            skip_reasons.max_positions += 1
            trades_skipped.append((pt, "max_positions"))
            continue

        open_tickers = {op["ticker"] for op in open_positions.values()}
        if _theme_cap_violated(open_tickers, pt.ticker, relations,
                               max_positions_per_theme, theme_cache):
            skip_reasons.theme_cap += 1
            trades_skipped.append((pt, "theme_cap"))
            continue

        dollars = min(target, cash)
        if dollars <= 0:
            skip_reasons.cash += 1
            trades_skipped.append((pt, "cash"))
            continue

        cash -= dollars
        open_positions[event.trade_id] = {"dollars": dollars, "ticker": pt.ticker}
        trades_taken.append(pt)
        peak_concurrent = max(peak_concurrent, len(open_positions))
        equity_curve.append((event.date, cash + _deployed()))

    final_equity = cash + _deployed()
    total_return = final_equity / initial_capital - 1 if initial_capital > 0 else 0.0
    sharpe, cagr = _sharpe_and_cagr(equity_curve, initial_capital, final_equity)
    max_dd = _max_drawdown([(equity_curve[0][0], initial_capital)] + equity_curve) if equity_curve else 0.0
    utilization = (utilization_weighted_sum / utilization_total_days) if utilization_total_days > 0 else 0.0

    return PortfolioBacktestResult(
        initial_capital=initial_capital, final_equity=final_equity,
        total_return=total_return, max_drawdown=max_dd, sharpe=sharpe, cagr=cagr,
        n_trades_taken=len(trades_taken), n_trades_skipped=len(trades_skipped),
        skip_reasons=skip_reasons, peak_concurrent_positions=peak_concurrent,
        capital_utilization=utilization, equity_curve=equity_curve,
        trades_taken=trades_taken, trades_skipped=trades_skipped,
    )


# ── Öffentliche API ──────────────────────────────────────────────────────────

def run_portfolio_backtest(
    plan: List[Dict],
    universe: List[str],
    years: int = 15,
    loader: Callable[[str, int], object] = data_loader.load,
    initial_capital: float = 100_000.0,
    max_positions: int = 10,
    max_positions_per_theme: int = 3,
    vol_target_cfg: Optional[VolTargetConfig] = None,
    stock_relations=None,
) -> PortfolioBacktestResult:
    """Simuliert EIN Portfolio über den `plan` (weight_plan()-förmig:
    [{"strategy","params","weight",...}]) hinweg – Cash-Constraint,
    Max-Positionen, Themen-Kappung (Korrelations-Proxy), Vol-Targeting.
    `plan=[]` (z.B. weil der Allokator gerade keine ACTIVE Strategie hat)
    liefert ein flaches Ergebnis (final_equity == initial_capital), kein Crash."""
    if vol_target_cfg is None:
        vol_target_cfg = VolTargetConfig()
    if stock_relations is None:
        from analyzers.stock_relations import StockRelations
        stock_relations = StockRelations()

    trades = _collect_trades(plan, universe, years, loader)
    if not trades:
        return PortfolioBacktestResult(
            initial_capital=initial_capital, final_equity=initial_capital,
            total_return=0.0, max_drawdown=0.0, sharpe=0.0, cagr=0.0,
            n_trades_taken=0, n_trades_skipped=0, skip_reasons=SkipReasons(),
            peak_concurrent_positions=0, capital_utilization=0.0,
        )

    events = _build_events(trades)
    return _simulate_events(events, trades, initial_capital, max_positions,
                            max_positions_per_theme, vol_target_cfg, stock_relations)
