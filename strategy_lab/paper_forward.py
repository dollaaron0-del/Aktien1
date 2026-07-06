"""
Paper-Forward-Validierung (Roadmap-Punkt c) – "erst auf Papier vorwärts, dann live".

Der Walk-Forward (Phase 3) bewertet die Strategien out-of-sample auf *historischen*
Fenstern. Paper-Forward ist der nächste, ehrlichere Schritt: die vom Meta-Allokator
heute ausgewählten Signale werden als PAPIER-Positionen mitgeschrieben und gegen
NUR BEREITS EXISTIERENDE Kursbalken aufgelöst. Zukunft existiert wörtlich nicht →
eine Position bleibt OFFEN, bis echte neue Balken eintreffen. So entsteht über die
Wall-Clock-Zeit ein genuiner Vorwärts-Track – kein Look-Ahead, keine echten Orders.

Kern-Idee (idempotent & deterministisch):
  * Persistiert wird nur das HARTE Faktum je Position: Strategie, Ticker, Signal-Tag,
    Entry (nächster Open nach Signal), Gewicht und ein Exit-Config-Snapshot.
  * Das ERGEBNIS (offen/zu, P&L) wird bei jedem `update` aus den Kursbalken von Entry
    bis `as_of` NEU berechnet (Kurse sind unveränderliche Historie ⇒ reproduzierbar).
  * Die Exit-Mechanik spiegelt 1:1 die getestete `engine._simulate` (TP1/TP2/SL/Time),
    nur dass „noch nicht genug Balken" ⇒ OFFEN statt Zwangs-Schließung bedeutet.

Greift NICHT in den Live-Pfad ein (Bot bewusst pausiert). Loader injizierbar,
netzfrei testbar. Ledger liegt in data/paper_forward.json (gitignored).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import pandas as pd

from backtesting.engine import BacktestConfig
from strategy_lab import allocator

_LEDGER_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "paper_forward.json")

PENDING = "PENDING"   # Signal gefeuert, Entry-Balken noch nicht da
OPEN = "OPEN"         # eingestiegen, Exit noch nicht ausgelöst (Zukunft fehlt)
CLOSED = "CLOSED"     # TP/SL/Time-Stop erreicht

_CFG_FIELDS = set(BacktestConfig.__dataclass_fields__)


def _cfg_from(params: Optional[dict]) -> BacktestConfig:
    return BacktestConfig(**{k: v for k, v in (params or {}).items() if k in _CFG_FIELDS})


def _to_day(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


@dataclass
class PaperPosition:
    strategy: str
    ticker: str
    signal_date: str                       # Tag, an dem die Entry-Flanke feuerte
    weight: float                          # Allokator-Gewicht der Strategie bei Aufnahme
    cfg: Dict                              # Exit-Config-Snapshot (sl/tp/max_hold …)
    status: str = PENDING
    entry_date: Optional[str] = None
    entry_price: Optional[float] = None
    # Ergebnis (bei jedem update neu berechnet, hier nur gecacht)
    tp1_hit: bool = False
    tp2_hit: bool = False
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    return_pct: float = 0.0                # realisiert (geschlossen) ODER MtM (offen)
    exit_reason: str = ""
    last_checked: Optional[str] = None

    def key(self) -> str:
        return f"{self.strategy}:{self.ticker}:{self.signal_date}"


# ── Resolver: spiegelt engine._simulate, kann aber OFFEN bleiben ────────────────

def _entry_index(df: pd.DataFrame, signal_date: str) -> Optional[int]:
    """Index des Entry-Balkens = erster Balken NACH dem Signal-Tag (Entry am Open)."""
    sig = pd.Timestamp(signal_date)
    later = df.index[df.index > sig]
    if len(later) == 0:
        return None
    return int(df.index.get_loc(later[0]))


def _resolve(df: pd.DataFrame, entry_idx: int, cfg: BacktestConfig) -> Dict:
    """Löst eine Position über die vorhandenen Balken ab Entry auf. df ist bereits
    auf `as_of` zugeschnitten (nur existierende Balken). Identische TP1/TP2/SL/Time-
    Mechanik wie engine._simulate – ABER wenn die Haltedauer mangels Balken (Zukunft)
    nicht voll ist, bleibt die Position OFFEN (mark-to-market am letzten Close)."""
    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Open"])

    sl_price = entry_price * (1 - cfg.sl_pct)
    tp1_price = entry_price * (1 + cfg.tp1_pct)
    tp2_price = entry_price * (1 + cfg.tp2_pct)

    realized = 0.0
    remaining = 1.0
    breakeven = False
    tp1_hit = tp2_hit = False

    last_idx = len(df) - 1
    horizon_idx = entry_idx + cfg.max_hold_days          # voller Time-Stop wäre hier
    end_idx = min(horizon_idx, last_idx)

    def _result(status, j, price, reason):
        return {
            "status": status,
            "entry_price": round(entry_price, 4),
            "tp1_hit": tp1_hit, "tp2_hit": tp2_hit,
            "exit_date": _to_day(df.index[j]) if status == CLOSED else None,
            "exit_price": round(price, 4) if status == CLOSED else None,
            "return_pct": round(realized if status == CLOSED
                                else realized + remaining * (price / entry_price - 1), 6),
            "exit_reason": reason,
            "last_checked": _to_day(df.index[j]),
        }

    for j in range(entry_idx, end_idx + 1):
        row = df.iloc[j]
        high, low, close = float(row["High"]), float(row["Low"]), float(row["Close"])

        if not tp1_hit and high >= tp1_price:
            realized += cfg.tp1_frac * cfg.tp1_pct
            remaining -= cfg.tp1_frac
            breakeven = True
            sl_price = entry_price
            tp1_hit = True

        if tp1_hit and not tp2_hit and high >= tp2_price:
            sell = cfg.tp2_frac * remaining
            realized += sell * cfg.tp2_pct
            remaining -= sell
            tp2_hit = True

        if low <= sl_price:
            realized += remaining * (0.0 if breakeven else -cfg.sl_pct)
            return _result(CLOSED, j, sl_price, "SL_breakeven" if breakeven else "SL")

        if j == end_idx:
            if horizon_idx <= last_idx:           # volle Haltedauer erreicht → Time-Stop
                realized += remaining * (close / entry_price - 1)
                return _result(CLOSED, j, close, "time_stop")
            # sonst: Balken sind ausgegangen (Zukunft fehlt) → Position bleibt OFFEN
            return _result(OPEN, j, close, "open")

    # nur erreichbar, wenn entry_idx == last_idx (Entry-Balken selbst, kein Folgebalken)
    last = df.iloc[end_idx]
    return _result(OPEN, end_idx, float(last["Close"]), "open")


# ── Ledger-IO ───────────────────────────────────────────────────────────────────

def load_ledger() -> Dict:
    if os.path.exists(_LEDGER_FILE):
        try:
            return json.load(open(_LEDGER_FILE))
        except Exception:
            pass
    return {"created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "positions": []}


def save_ledger(ledger: Dict) -> str:
    os.makedirs(os.path.dirname(_LEDGER_FILE), exist_ok=True)
    ledger["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with open(_LEDGER_FILE, "w") as fh:
        json.dump(ledger, fh, indent=2)
    return _LEDGER_FILE


def _positions(ledger: Dict) -> List[PaperPosition]:
    return [PaperPosition(**p) for p in ledger.get("positions", [])]


def _store(ledger: Dict, positions: List[PaperPosition]) -> None:
    ledger["positions"] = [asdict(p) for p in positions]


# ── Vorwärts-Schritte ───────────────────────────────────────────────────────────

def record_signals(
    ledger: Dict,
    as_of,
    universe: List[str],
    loader: Callable[[str, int], object],
    plan: Optional[List[Dict]] = None,
    years: int = 2,
) -> int:
    """Schreibt die HEUTE feuernden Allokator-Signale als neue PENDING-Positionen.
    Dedup: pro (Strategie, Ticker) wird keine neue Position eröffnet, solange eine
    nicht geschlossene existiert (eine offene Position je Ticker je Strategie)."""
    plan = plan if plan is not None else allocator.weight_plan()
    if not plan:
        return 0
    weights = {e["strategy"]: e["weight"] for e in plan}
    params = {e["strategy"]: e.get("params", {}) for e in plan}

    positions = _positions(ledger)
    held = {(p.strategy, p.ticker) for p in positions if p.status != CLOSED}

    fired = allocator.current_signals(universe, loader, plan=plan, years=years)
    n_new = 0
    for strat, tickers in fired.items():
        cfg = asdict(_cfg_from(params.get(strat)))
        for t in tickers:
            if (strat, t) in held:
                continue
            positions.append(PaperPosition(
                strategy=strat, ticker=t, signal_date=_to_day(as_of),
                weight=round(weights.get(strat, 0.0), 4), cfg=cfg))
            held.add((strat, t))
            n_new += 1
    _store(ledger, positions)
    return n_new


def update_positions(
    ledger: Dict,
    loader: Callable[[str, int], object],
    as_of=None,
    years: int = 3,
) -> Dict[str, int]:
    """Löst alle nicht geschlossenen Positionen gegen die bis `as_of` vorhandenen
    Balken neu auf. Idempotent: schon geschlossene bleiben unverändert."""
    as_of_ts = pd.Timestamp(as_of) if as_of is not None else None
    positions = _positions(ledger)
    stats = {"opened": 0, "closed": 0, "still_open": 0, "pending": 0}

    for p in positions:
        if p.status == CLOSED:
            continue
        df = loader(p.ticker, years)
        if df is None or len(df) < 2:
            stats["pending" if p.entry_price is None else "still_open"] += 1
            continue
        if as_of_ts is not None:
            df = df[df.index <= as_of_ts]
        entry_idx = _entry_index(df, p.signal_date)
        if entry_idx is None:                 # Entry-Balken existiert noch nicht
            p.status = PENDING
            stats["pending"] += 1
            continue

        res = _resolve(df, entry_idx, _cfg_from(p.cfg))
        was_pending = p.entry_price is None
        p.entry_date = _to_day(df.index[entry_idx])
        p.entry_price = res["entry_price"]
        p.tp1_hit, p.tp2_hit = res["tp1_hit"], res["tp2_hit"]
        p.return_pct = res["return_pct"]
        p.exit_reason = res["exit_reason"]
        p.last_checked = res["last_checked"]
        p.status = res["status"]
        if res["status"] == CLOSED:
            p.exit_date, p.exit_price = res["exit_date"], res["exit_price"]
            stats["closed"] += 1
        else:
            stats["still_open"] += 1
            if was_pending:
                stats["opened"] += 1
    _store(ledger, positions)
    return stats


def _equity_drawdown(closed: List[PaperPosition]) -> float:
    """Max Drawdown der nach Exit chronologisch verketteten Trade-Equity (≤0)."""
    if not closed:
        return 0.0
    ordered = sorted(closed, key=lambda p: (p.exit_date or "", p.entry_date or ""))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in ordered:
        equity *= (1 + p.return_pct)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    return round(max_dd, 6)


def _avg_holding_days(closed: List[PaperPosition]) -> Optional[float]:
    spans = [(pd.Timestamp(p.exit_date) - pd.Timestamp(p.entry_date)).days
             for p in closed if p.entry_date and p.exit_date]
    return round(sum(spans) / len(spans), 1) if spans else None


def summary(ledger: Dict) -> Dict:
    """Aggregierte Kennzahlen über den Paper-Forward-Track. `closed` ist der harte,
    realisierte Teil; `open`/`pending` sind mark-to-market bzw. noch ohne Entry.

    Risiko-Kennzahlen (max_drawdown, sharpe_trade) sind TRADE-LEVEL (nicht
    annualisiert) – wie im übrigen Lab: als Sanity-Gerüst lesen, nicht überbewerten."""
    positions = _positions(ledger)
    closed = [p for p in positions if p.status == CLOSED]
    open_ = [p for p in positions if p.status == OPEN]
    pending = [p for p in positions if p.status == PENDING]

    wins = [p for p in closed if p.return_pct > 0]
    closed_rets = [p.return_pct for p in closed]

    sharpe_trade = 0.0
    if len(closed_rets) >= 2:
        s = pd.Series(closed_rets)
        sd = float(s.std(ddof=1))
        sharpe_trade = round(float(s.mean()) / sd, 4) if sd > 0 else 0.0

    def _w(ps):  # gewichteter Ø-Return (Allokator-Gewicht)
        tw = sum(p.weight for p in ps)
        return round(sum(p.return_pct * p.weight for p in ps) / tw, 6) if tw > 0 else 0.0

    by_strat: Dict[str, Dict] = {}
    for p in positions:
        s = by_strat.setdefault(p.strategy, {"n": 0, "closed": 0, "wins": 0, "ret_sum": 0.0})
        s["n"] += 1
        if p.status == CLOSED:
            s["closed"] += 1
            s["ret_sum"] += p.return_pct
            if p.return_pct > 0:
                s["wins"] += 1
    for s in by_strat.values():
        s["avg_return"] = round(s["ret_sum"] / s["closed"], 6) if s["closed"] else 0.0
        s["win_rate"] = round(s["wins"] / s["closed"], 4) if s["closed"] else 0.0

    return {
        "n_positions": len(positions),
        "n_closed": len(closed),
        "n_open": len(open_),
        "n_pending": len(pending),
        "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        "avg_return_closed": round(sum(closed_rets) / len(closed), 6) if closed else 0.0,
        "weighted_return_closed": _w(closed),
        "open_mtm_weighted": _w(open_),
        "sharpe_trade": sharpe_trade,
        "max_drawdown": _equity_drawdown(closed),
        "avg_holding_days": _avg_holding_days(closed),
        "by_strategy": by_strat,
    }


def benchmark_buy_hold(
    ledger: Dict,
    loader: Callable[[str, int], object],
    years: int = 3,
) -> Optional[Dict]:
    """Naiver Vergleichsmaßstab: gleichgewichtetes Buy&Hold der im Ledger gehandelten
    Ticker über die Spanne [erster Entry, letzter geprüfter Balken]. Macht den Paper-
    Forward-Return einordbar (schlägt er stumpfes Halten?). Loader injizierbar."""
    positions = [p for p in _positions(ledger) if p.entry_date]
    if not positions:
        return None
    start = min(pd.Timestamp(p.entry_date) for p in positions)
    end = max(pd.Timestamp(p.last_checked or p.entry_date) for p in positions)
    tickers = sorted({p.ticker for p in positions})

    rets = []
    for t in tickers:
        df = loader(t, years)
        if df is None or len(df) < 2:
            continue
        win = df[(df.index >= start) & (df.index <= end)]
        if len(win) < 2:
            continue
        rets.append(float(win["Close"].iloc[-1]) / float(win["Close"].iloc[0]) - 1)
    if not rets:
        return None
    return {
        "start": _to_day(start),
        "end": _to_day(end),
        "n_tickers": len(rets),
        "buy_hold_return": round(sum(rets) / len(rets), 6),
    }


# ── Replay-Bootstrap ────────────────────────────────────────────────────────────

def replay(
    universe: List[str],
    base_loader: Callable[[str, int], object],
    plan: List[Dict],
    start,
    end=None,
    history_years: int = 6,
    step_days: int = 1,
    years: int = 2,
) -> Dict:
    """Spult den LIVE-Mechanismus durch ein Holdout-Fenster [start, end] vor: an jedem
    Handelstag werden Signale aufgezeichnet und Positionen gegen einen Truncating-Loader
    (Zukunft abgeschnitten) aufgelöst – exakt dieselben Funktionen wie im Live-Betrieb,
    nur im Schnelldurchlauf. Bootstrappt einen frischen Ledger. KLAR als Replay zu lesen:
    es ersetzt keinen echten Vorwärts-Lauf, sondern füllt den Track aus jüngsten Daten."""
    full = {t: base_loader(t, history_years) for t in universe}
    full = {t: df for t, df in full.items() if df is not None and len(df) > 0}
    if not full:
        return {"created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "positions": [], "mode": "replay"}

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end) if end is not None else max(df.index.max() for df in full.values())

    # Vereinigte Handelstage im Fenster.
    all_days = sorted({d for df in full.values() for d in df.index if start_ts <= d <= end_ts})
    all_days = all_days[::max(1, step_days)]

    state = {"as_of": None}

    def trunc_loader(t: str, _years: int):
        df = full.get(t)
        if df is None:
            return None
        return df[df.index <= state["as_of"]]

    ledger = {"created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "positions": [],
              "mode": "replay", "replay_window": [_to_day(start_ts), _to_day(end_ts)]}
    for day in all_days:
        state["as_of"] = day
        record_signals(ledger, day, list(full), trunc_loader, plan=plan, years=years)
        update_positions(ledger, trunc_loader, as_of=day, years=years)
    return ledger
