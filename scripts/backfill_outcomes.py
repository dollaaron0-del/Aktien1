#!/usr/bin/env python3
"""
Backfill Outcomes — labelt historische Analysen mit echten Kursen.

Aus analysis_log.db (Features, kein Ergebnis) werden gelabelte Beispiele:
für jede handelbare Analyse simulieren wir "was wäre passiert, wären wir zum
Analysezeitpunkt eingestiegen?" — unter den echten Exit-Regeln des Bots
(stop_loss_pct / take_profit_pct / max Halten = suggested_hold).

WICHTIG (ehrlicher Caveat): Das sind Papier-Ergebnisse ohne Slippage,
Liquidität oder Teilfills. Sie landen mit label_source='backfill' im Store,
strikt getrennt von echten live-Trades.

Usage:
  python -m scripts.backfill_outcomes              # alle handelbaren Analysen
  python -m scripts.backfill_outcomes --limit 50   # nur die ersten 50
  python -m scripts.backfill_outcomes --dry-run    # nichts schreiben, nur Stats
  python -m scripts.backfill_outcomes --hold 20    # Default-Haltedauer (Bars)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore  # noqa: E402

_ANALYSIS_DB = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_log.db")
_PRICE_CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "backfill_prices")

# Exit-Regeln: aus config, mit konservativem Fallback falls Import scheitert.
try:
    from config import config as _cfg
    _SL_PCT = float(getattr(_cfg, "stop_loss_pct", 0.07))
    _TP_PCT = float(getattr(_cfg, "take_profit_pct", 0.20))
except Exception:  # pragma: no cover - defensiv
    _SL_PCT, _TP_PCT = 0.07, 0.20

_DEFAULT_HOLD = 20  # Trading-Bars, wenn suggested_hold fehlt


# ── Reine Simulation (netzfrei, testbar) ──────────────────────────────────────
def normalize_direction(direction: str, recommendation: str = "") -> str:
    """Mappt diverse Schreibweisen auf 'LONG' / 'SHORT'."""
    d = (direction or "").strip().upper()
    if d in ("LONG", "UP", "BULL", "BULLISH"):
        return "LONG"
    if d in ("SHORT", "DOWN", "BEAR", "BEARISH"):
        return "SHORT"
    # Fallback über die Empfehlung
    r = (recommendation or "").strip().upper()
    return "SHORT" if r == "SELL" else "LONG"


def simulate_outcome(
    bars: List[Dict],
    direction: str,
    sl_pct: float = _SL_PCT,
    tp_pct: float = _TP_PCT,
    max_hold: int = _DEFAULT_HOLD,
    entry_price: Optional[float] = None,
) -> Optional[Dict]:
    """Simuliert ein Trade-Ergebnis aus OHLC-Bars.

    bars: Liste von Dicts mit open/high/low/close (chronologisch), bars[0] ist die
    Einstiegs-Bar (Entry = deren close, sofern entry_price nicht gesetzt).
    Liefert ein Outcome-Dict oder None, wenn zu wenig/unbrauchbare Daten.

    Konvention bei Doppel-Trigger in einer Bar: konservativ → Stop-Loss zuerst.
    """
    if not bars:
        return None
    direction = normalize_direction(direction)

    entry = float(entry_price) if entry_price is not None else _f(bars[0].get("close"))
    if entry is None or entry <= 0:
        return None

    long = direction == "LONG"
    tp_level = entry * (1 + tp_pct) if long else entry * (1 - tp_pct)
    sl_level = entry * (1 - sl_pct) if long else entry * (1 + sl_pct)

    future = bars[1:]
    if not future:
        return None
    horizon = min(max_hold, len(future))

    mfe = 0.0  # max favorable excursion (%)
    mae = 0.0  # max adverse excursion (%)
    exit_price = None
    exit_reason = None
    hold_days = 0

    for i in range(horizon):
        bar = future[i]
        hi, lo = _f(bar.get("high")), _f(bar.get("low"))
        if hi is None or lo is None:
            continue
        hold_days = i + 1

        # Excursions relativ zum Entry (richtungsabhängig)
        if long:
            mfe = max(mfe, (hi - entry) / entry * 100)
            mae = min(mae, (lo - entry) / entry * 100)
            hit_sl = lo <= sl_level
            hit_tp = hi >= tp_level
        else:
            mfe = max(mfe, (entry - lo) / entry * 100)
            mae = min(mae, (entry - hi) / entry * 100)
            hit_sl = hi >= sl_level
            hit_tp = lo <= tp_level

        if hit_sl:  # konservativ: SL vor TP, wenn beide in derselben Bar
            exit_price, exit_reason = sl_level, "SL"
            break
        if hit_tp:
            exit_price, exit_reason = tp_level, "TP"
            break

    if exit_price is None:  # Zeit-Exit am letzten gehaltenen Bar-Close
        last = future[horizon - 1]
        exit_price = _f(last.get("close"))
        exit_reason = "TIME"
        hold_days = horizon
        if exit_price is None:
            return None

    pnl_pct = ((exit_price - entry) / entry * 100) if long else ((entry - exit_price) / entry * 100)
    # Exit-Bar (gleiche Logik für SL/TP/TIME): future[hold_days-1]. Datum optional.
    exit_bar = future[hold_days - 1] if 0 < hold_days <= len(future) else None
    return {
        "entry_price": round(entry, 6),
        "exit_price": round(float(exit_price), 6),
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 4),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "hold_days": hold_days,
        "outcome": "WIN" if pnl_pct > 0 else "LOSS",
        "exit_date": exit_bar.get("date") if exit_bar else None,
    }


def _f(x) -> Optional[float]:
    """float-Cast mit NaN/Inf-Guard (yfinance-NaN-Falle)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ── Kurs-Beschaffung (yfinance, mit Datei-Cache) ───────────────────────────────
def _load_bars(ticker: str, start: str) -> List[Dict]:
    """Lädt Tages-OHLC ab `start` (YYYY-MM-DD), cached pro Ticker in data/."""
    os.makedirs(_PRICE_CACHE, exist_ok=True)
    cache_file = os.path.join(_PRICE_CACHE, f"{ticker.replace('/', '_')}.json")
    cached: List[Dict] = []
    if os.path.exists(cache_file):
        try:
            cached = json.load(open(cache_file))
        except Exception:
            cached = []
    if cached and cached[0].get("date", "9999") <= start:
        return [b for b in cached if b.get("date", "") >= start]

    import yfinance as yf
    try:
        hist = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    except Exception as exc:
        print(f"  ! {ticker}: yfinance-Fehler {exc}")
        return []
    bars = []
    for idx, row in hist.iterrows():
        bars.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": _f(row.get("Open")),
            "high": _f(row.get("High")),
            "low": _f(row.get("Low")),
            "close": _f(row.get("Close")),
        })
    if bars:
        try:
            json.dump(bars, open(cache_file, "w"))
        except Exception:
            pass
    return bars


def _is_acted(recommendation: str) -> bool:
    """True für tatsächlich gehandelte Empfehlungen (BUY/SELL)."""
    return (recommendation or "").strip().upper() in ("BUY", "SELL")


def _read_analyses(limit: Optional[int], include_holds: bool = False) -> List[Dict]:
    conn = sqlite3.connect(_ANALYSIS_DB)
    conn.row_factory = sqlite3.Row
    if include_holds:
        # HOLD-Varianten (z.B. "HOLD (mit enger Überwachung)") via LIKE einfangen.
        sql = (
            "SELECT * FROM analyses WHERE recommendation IN ('BUY','SELL','SKIP') "
            "OR recommendation LIKE 'HOLD%' ORDER BY analyzed_at"
        )
    else:
        sql = "SELECT * FROM analyses WHERE recommendation IN ('BUY','SELL') ORDER BY analyzed_at"
    rows = conn.execute(sql).fetchall()
    conn.close()
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


# ── Orchestrierung ─────────────────────────────────────────────────────────────
def run(limit: Optional[int] = None, dry_run: bool = False,
        default_hold: int = _DEFAULT_HOLD, include_holds: bool = False,
        dedup: bool = True) -> Dict:
    analyses = _read_analyses(limit, include_holds=include_holds)
    n_acted = sum(1 for a in analyses if _is_acted(a.get("recommendation", "")))
    print(f"Analysen gesamt: {len(analyses)}  (gehandelt BUY/SELL: {n_acted}"
          f"{', hypothetisch HOLD/SKIP: %d' % (len(analyses) - n_acted) if include_holds else ''})")
    print(f"Exit-Regeln: SL={_SL_PCT:.0%}  TP={_TP_PCT:.0%}  Default-Hold={default_hold} Bars")
    if include_holds:
        print("HOLD/SKIP werden als hypothetische LONG-Einstiege gelabelt "
              "(label_source='backfill_hypo' – getrennt von echten Entscheidungen).")
    if dedup:
        print("Dedup AN: pro (Ticker, Quelle) nur 1 Einstieg, solange Position offen "
              "(verhindert Mehrfachzählung gleicher Signale, wie der echte Bot).")
    if dry_run:
        print("(dry-run – es wird nichts geschrieben)\n")

    store = None if dry_run else ExperienceStore()
    counts = {"labeled": 0, "no_data": 0, "WIN": 0, "LOSS": 0, "acted": 0, "hypo": 0, "deduped": 0}
    reasons: Dict[str, int] = {}
    # Pro (Ticker, label_source): Datum, bis zu dem eine Position noch offen ist.
    blocked_until: Dict[tuple, str] = {}

    for a in analyses:
        ticker = a["ticker"]
        decided_at = a["analyzed_at"]
        start_date = decided_at[:10]
        rec = a.get("recommendation", "")
        acted = _is_acted(rec)
        # Gehandelt: echte Richtung. Hypothetisch (HOLD/SKIP): als LONG annehmen.
        direction = normalize_direction(a.get("direction", ""), rec) if acted else "LONG"
        label_source = "backfill" if acted else "backfill_hypo"

        # Dedup: Signal überspringen, wenn auf diesem Ticker noch eine Position offen ist.
        key = (ticker, label_source)
        if dedup and start_date < blocked_until.get(key, ""):
            counts["deduped"] += 1
            continue

        bars = _load_bars(ticker, start_date)
        bars = [b for b in bars if b.get("date", "") >= start_date]
        hold = int(a.get("suggested_hold") or 0) or default_hold

        outcome = simulate_outcome(bars, direction=direction, max_hold=hold)
        if outcome is None:
            counts["no_data"] += 1
            continue

        # Position bis zum Exit-Datum als offen markieren (Dedup-Fenster).
        if dedup and outcome.get("exit_date"):
            blocked_until[key] = outcome["exit_date"]

        counts["labeled"] += 1
        counts[outcome["outcome"]] += 1
        counts["acted" if acted else "hypo"] += 1
        reasons[outcome["exit_reason"]] = reasons.get(outcome["exit_reason"], 0) + 1

        if not dry_run:
            feat = {
                "decided_at": decided_at,
                "ticker": ticker,
                "recommendation": rec,
                "direction": direction,
                "sentiment_score": a.get("sentiment_score"),
                "confidence": a.get("confidence"),
                "debate_winner": a.get("debate_winner"),
                "target_price": a.get("target_price"),
                "suggested_hold": a.get("suggested_hold"),
                "sources_used": a.get("sources_used"),
                "key_catalysts": a.get("key_catalysts"),
                "risk_factors": a.get("risk_factors"),
            }
            did = store.upsert_decision(feat)
            store.attach_outcome(did, {**outcome, "label_source": label_source})

    print("\n── Ergebnis ───────────────────────────────")
    print(f"gelabelt : {counts['labeled']}  (WIN {counts['WIN']} / LOSS {counts['LOSS']})")
    print(f"davon    : gehandelt {counts['acted']} / hypothetisch {counts['hypo']}")
    print(f"dedupliziert übersprungen: {counts['deduped']}")
    print(f"kein Kurs: {counts['no_data']}")
    print(f"Exit-Gründe: {reasons}")
    if counts["labeled"]:
        wr = counts["WIN"] / counts["labeled"]
        print(f"Win-Rate (Papier): {wr:.1%}")
    if store:
        print("\nStore-Stats:", store.stats())
        store.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill outcomes für analysis_log.db")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Analysen")
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben")
    ap.add_argument("--hold", type=int, default=_DEFAULT_HOLD, help="Default-Haltedauer in Bars")
    ap.add_argument("--include-holds", action="store_true",
                    help="HOLD/SKIP als hypothetische LONGs mitlabeln (backfill_hypo)")
    ap.add_argument("--no-dedup", action="store_true",
                    help="Mehrfachsignale pro Ticker NICHT zusammenfassen (Roh-Modus)")
    args = ap.parse_args()
    run(limit=args.limit, dry_run=args.dry_run, default_hold=args.hold,
        include_holds=args.include_holds, dedup=not args.no_dedup)


if __name__ == "__main__":
    main()
