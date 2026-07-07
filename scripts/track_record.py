#!/usr/bin/env python3
"""
Track-Record-Report — operationalisiert das Evidenz-Gate (Langfrist-Ziel 1).

Liest den gelabelten Lern-Datensatz (data/experience.db über ExperienceStore) und
beantwortet die harte Frage: **Hat das System eine risikoadjustierte Kante, und
schlägt es Buy&Hold — mit Konfidenz, nicht nur als Punktschätzung?**

Kern:
  * Bootstrap-95%-CI auf die mittlere Trade-Rendite (kein t-Test → robust gegen
    die nicht-normalen, fat-tailed Trade-Renditen; scipy nicht nötig).
  * Paired vs-Buy&Hold: je Trade die Index-Rendite über dasselbe Haltefenster
    (regionaler Index je Ticker-Suffix, via backtesting.data_loader gecacht) →
    Excess-Rendite; Bootstrap-CI auf den Mittelwert der Excess-Renditen.
  * Aufschlüsselung je Regime (Edge-CI je Regime — Ziel-1-Anforderung).
  * **Kodierte Meilenstein-Gates** (n Live-Trades, Edge-CI>0, schlägt B&H,
    Edge je Regime) → maschinelle PASS/FAIL-Verdikte statt Bauchgefühl.

Bewusst ehrlich: der Backfill ist Papier (kein Slippage/Liquidität); der
Meilenstein zählt daher standardmäßig NUR echte Live-Trades (label_source='live').
Backfill liefert die Mechanik-Sicht, nicht die Evidenz fürs Kapital-Gate.

Usage:
  python -m scripts.track_record                 # echte Entscheidungen (backfill+live)
  python -m scripts.track_record --hypo-only      # nur kontrafaktische HOLD/SKIP
  python -m scripts.track_record --no-benchmark   # ohne Netz, nur absolute Sicht
  python -m scripts.track_record --min-live 100   # Gate-Schwelle überschreiben
"""
from __future__ import annotations

import argparse
import bisect
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore  # noqa: E402

# ── Gate-Schwellen (kodierte Meilensteine, Ziel 1) ───────────────────────────
# Kein Kapital-Schritt ohne diese Evidenz. Schwellen bewusst konservativ.
N_MIN_LIVE = 100        # gelabelte Live-Trades für statistische Aussagekraft
N_MIN_REGIME = 30       # Mindest-n, damit ein Regime ein eigenes Edge-Gate bekommt
CI_LEVEL = 0.95         # Konfidenzniveau der Bootstrap-Intervalle
BOOTSTRAP_ITERS = 20000
SEED = 20260707         # reproduzierbare Verdikte

# Regionaler B&H-Index je Ticker-Suffix (Buy&Hold-Alternative = den Markt halten).
_INDEX_BY_SUFFIX = {
    "DE": "^GDAXI", "F": "^GDAXI", "PA": "^FCHI", "AS": "^AEX",
    "MC": "^IBEX", "SW": "^SSMI", "L": "^FTSE", "MI": "^STOXX50E",
    "BR": "^BFX", "LS": "^STOXX50E", "HE": "^STOXX50E", "VI": "^ATX",
    "ST": "^OMX", "CO": "^OMXC25", "OL": "^STOXX50E", "AT": "^ATX",
}
_INDEX_US = "^GSPC"
_INDEX_EU_FALLBACK = "^STOXX50E"


def _benchmark_ticker(ticker: str) -> str:
    """Regionaler Index für den B&H-Vergleich (US-Default, EU nach Suffix)."""
    if "." in ticker:
        suffix = ticker.rsplit(".", 1)[1].upper()
        return _INDEX_BY_SUFFIX.get(suffix, _INDEX_EU_FALLBACK)
    return _INDEX_US


# ── Statistik ─────────────────────────────────────────────────────────────────
def _bootstrap_mean_ci(x: List[float], rng: np.random.Generator,
                       iters: int = BOOTSTRAP_ITERS,
                       ci: float = CI_LEVEL) -> Dict:
    """Bootstrap-CI auf den Mittelwert + einseitige P(Mittelwert ≤ 0).

    Gibt mean, lo, hi (CI-Grenzen) und p_le0 (Anteil Bootstrap-Mittel ≤ 0 =
    Evidenz gegen eine positive Kante) zurück. n<2 → keine sinnvolle CI.
    """
    arr = np.asarray(x, dtype=float)
    n = arr.size
    if n == 0:
        return {"n": 0, "mean": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "p_le0": float("nan")}
    if n < 2:
        return {"n": n, "mean": float(arr[0]), "lo": float("nan"),
                "hi": float("nan"), "p_le0": float("nan")}
    idx = rng.integers(0, n, size=(iters, n))
    means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(means, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {"n": n, "mean": float(arr.mean()), "lo": float(lo),
            "hi": float(hi), "p_le0": float((means <= 0).mean())}


def _summary(returns: List[float]) -> Dict:
    """Absolute Kennzahlen einer Trade-Rendite-Reihe (in %, sequentielle Ordnung)."""
    arr = np.asarray(returns, dtype=float)
    n = arr.size
    if n == 0:
        return {"n": 0}
    wins = int((arr > 0).sum())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    # Sequentielle Equity (1 Position, voll investiert) — Track-Record-Proxy,
    # KEIN Portfolio-Backtest (das ist Roadmap 2.2). MaxDD daraus als Ziel-3-Frühwarnung.
    equity = np.cumprod(1 + arr / 100.0)
    peak = np.maximum.accumulate(equity)
    max_dd = float((equity / peak - 1).min() * 100)
    return {
        "n": n,
        "wins": wins,
        "win_rate": wins / n,
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "std": std,
        "sharpe_pt": float(arr.mean() / std) if std > 0 else float("nan"),
        "compounded": float((equity[-1] - 1) * 100),
        "max_dd": max_dd,
    }


# ── Benchmark (Index-Rendite je Haltefenster) ────────────────────────────────
class _BenchmarkCache:
    """Lädt Index-Tagesschluss je Bedarf (data_loader, parquet-gecacht) und
    beantwortet Fenster-Renditen lokal. Komplett fail-open: fehlt ein Index oder
    ein Datum, liefert window_return None → der Trade fällt aus dem paired Test,
    bleibt aber in der absoluten Sicht."""

    def __init__(self) -> None:
        self._series: Dict[str, Optional[Tuple[List[date], np.ndarray]]] = {}

    def _load(self, index: str) -> Optional[Tuple[List[date], np.ndarray]]:
        if index in self._series:
            return self._series[index]
        series = None
        try:
            from backtesting import data_loader
            df = data_loader.load(index, years=1)
            if df is not None and "Close" in df.columns and len(df):
                dates = [d.date() for d in df.index.to_pydatetime()]
                closes = df["Close"].to_numpy(dtype=float)
                order = np.argsort(dates)
                dates = [dates[i] for i in order]
                closes = closes[order]
                series = (dates, closes)
        except Exception:
            series = None
        self._series[index] = series
        return series

    def window_return(self, ticker: str, entry: date, hold_days: int) -> Optional[float]:
        series = self._load(_benchmark_ticker(ticker))
        if not series:
            return None
        dates, closes = series
        exit_day = entry + timedelta(days=max(0, hold_days))
        i_entry = bisect.bisect_right(dates, entry) - 1   # letzter Schluss ≤ entry
        i_exit = bisect.bisect_right(dates, exit_day) - 1  # letzter Schluss ≤ exit
        if i_entry < 0 or i_exit < 0 or closes[i_entry] <= 0:
            return None
        return (closes[i_exit] / closes[i_entry] - 1) * 100


# ── Datenzugriff ──────────────────────────────────────────────────────────────
def _load_trades(store: ExperienceStore, sources: Optional[set]) -> List[Dict]:
    trades = []
    for feat, out in store.iter_labeled():
        src = out.get("label_source")
        if sources is not None and src not in sources:
            continue
        pnl = out.get("pnl_pct")
        if pnl is None:
            continue
        entry_day = None
        try:
            entry_day = date.fromisoformat((feat.get("decided_at") or "")[:10])
        except ValueError:
            pass
        trades.append({
            "ticker": feat.get("ticker") or "?",
            "decided_at": feat.get("decided_at") or "",
            "entry_day": entry_day,
            "regime": feat.get("regime") or "n/a",
            "hold_days": int(out.get("hold_days") or 0),
            "pnl_pct": float(pnl),
            "source": src,
            "outcome": out.get("outcome"),
        })
    trades.sort(key=lambda t: t["decided_at"])
    return trades


# ── Ausgabe ───────────────────────────────────────────────────────────────────
def _p(s: str = "") -> None:
    print(s)


def _fmt_ci(d: Dict, unit: str = "%") -> str:
    if not np.isfinite(d.get("lo", float("nan"))):
        return f"{d['mean']:+.2f}{unit}  (CI n/a, n={d['n']})"
    return (f"{d['mean']:+.2f}{unit}  95%-CI [{d['lo']:+.2f}, {d['hi']:+.2f}]"
            f"  P(≤0)={d['p_le0']*100:.0f}%")


def _regime_table(trades: List[Dict], rng: np.random.Generator) -> Dict[str, Dict]:
    by_regime: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        by_regime[t["regime"]].append(t)
    _p("\n── Edge je Regime " + "─" * 42)
    _p(f"{'Regime':<10}{'N':>5}{'Win%':>7}{'Ø-Rendite (Bootstrap-CI)':>40}")
    result = {}
    for rg in sorted(by_regime, key=lambda r: -len(by_regime[r])):
        rows = by_regime[rg]
        rets = [t["pnl_pct"] for t in rows]
        ci = _bootstrap_mean_ci(rets, rng)
        s = _summary(rets)
        result[rg] = {"ci": ci, "summary": s}
        ci_txt = (f"{ci['mean']:+.2f}% [{ci['lo']:+.2f},{ci['hi']:+.2f}]"
                  if np.isfinite(ci.get("lo", float("nan")))
                  else f"{ci['mean']:+.2f}% (CI n/a)")
        _p(f"{rg:<10}{s['n']:>5}{s['win_rate']*100:>6.1f}%{ci_txt:>40}")
    return result


# ── Gates ─────────────────────────────────────────────────────────────────────
def _evaluate_gates(trades: List[Dict], overall_ci: Dict, excess_ci: Optional[Dict],
                    regime_stats: Dict[str, Dict], min_live: int) -> List[Tuple[str, bool, str]]:
    n_live = sum(1 for t in trades if t["source"] == "live")
    gates: List[Tuple[str, bool, str]] = []

    # Gate 1 — Stichprobe: genug echte Live-Trades (Papier zählt nicht fürs Kapital-Gate).
    gates.append((
        f"Stichprobe ≥ {min_live} Live-Trades",
        n_live >= min_live,
        f"{n_live} Live-Trades gelabelt" + ("" if n_live else " (nur Backfill vorhanden)"),
    ))

    # Gate 2 — Kante > 0: untere CI-Grenze der mittleren Rendite über 0.
    lo = overall_ci.get("lo", float("nan"))
    gates.append((
        "Ø-Rendite signifikant > 0 (CI-Untergrenze)",
        np.isfinite(lo) and lo > 0,
        (f"CI-Untergrenze {lo:+.2f}%" if np.isfinite(lo) else "zu wenig Daten für CI"),
    ))

    # Gate 3 — schlägt B&H: paired Excess-Rendite signifikant > 0.
    if excess_ci is None:
        gates.append(("Schlägt Buy&Hold (Excess-CI > 0)", False,
                      "keine Benchmark-Daten (--no-benchmark oder Offline)"))
    else:
        elo = excess_ci.get("lo", float("nan"))
        gates.append((
            "Schlägt Buy&Hold (Excess-CI > 0)",
            np.isfinite(elo) and elo > 0,
            (f"Excess-CI-Untergrenze {elo:+.2f}% (n={excess_ci['n']} mit Benchmark)"
             if np.isfinite(elo) else "zu wenig gepaarte Daten"),
        ))

    # Gate 4 — Kante je Regime: jedes hinreichend große Regime muss Edge>0 zeigen.
    qualifying = {rg: st for rg, st in regime_stats.items()
                  if st["summary"].get("n", 0) >= N_MIN_REGIME}
    if not qualifying:
        gates.append((f"Edge je Regime (n≥{N_MIN_REGIME})", False,
                      f"kein Regime erreicht n≥{N_MIN_REGIME} (aktuell nur "
                      + ", ".join(f"{rg}:{st['summary']['n']}" for rg, st in regime_stats.items()) + ")"))
    else:
        failed = [rg for rg, st in qualifying.items()
                  if not (np.isfinite(st["ci"].get("lo", float("nan"))) and st["ci"]["lo"] > 0)]
        gates.append((
            f"Edge je Regime (n≥{N_MIN_REGIME})",
            len(failed) == 0,
            "alle qualifizierten Regime mit Edge>0" if not failed
            else f"Edge>0 NICHT belegt in: {', '.join(failed)}",
        ))
    return gates


def main() -> None:
    ap = argparse.ArgumentParser(description="Track-Record-Report + Evidenz-Gates")
    ap.add_argument("--source", default=None,
                    help="nur eine label_source (z.B. backfill | live | backfill_hypo)")
    ap.add_argument("--include-hypo", action="store_true",
                    help="kontrafaktische HOLD/SKIP (backfill_hypo) mit einbeziehen")
    ap.add_argument("--hypo-only", action="store_true",
                    help="NUR backfill_hypo betrachten")
    ap.add_argument("--no-benchmark", action="store_true",
                    help="ohne Netz: vs-B&H-Achse überspringen")
    ap.add_argument("--min-live", type=int, default=N_MIN_LIVE,
                    help=f"Gate-Schwelle Live-Trades (Default {N_MIN_LIVE})")
    args = ap.parse_args()

    # Quellen-Auswahl: standardmäßig die ECHTEN Entscheidungen (backfill + live).
    if args.source:
        sources: Optional[set] = {args.source}
    elif args.hypo_only:
        sources = {"backfill_hypo"}
    elif args.include_hypo:
        sources = None  # alles
    else:
        sources = {"backfill", "live"}

    rng = np.random.default_rng(SEED)
    store = ExperienceStore()
    trades = _load_trades(store, sources)

    _p("═" * 64)
    _p(" Track-Record-Report  —  Evidenz-Gate (Ziel 1)")
    _p("═" * 64)
    src_label = args.source or ("backfill_hypo" if args.hypo_only
                                else "ALLE" if args.include_hypo else "backfill+live")
    _p(f"Quelle        : {src_label}")

    if not trades:
        _p("\n(keine passenden gelabelten Trades — erst backfill_outcomes / Live-Trades)")
        store.close()
        return

    n_live = sum(1 for t in trades if t["source"] == "live")
    n_bf = sum(1 for t in trades if t["source"] and t["source"].startswith("backfill"))
    dr = (trades[0]["decided_at"][:10], trades[-1]["decided_at"][:10])
    _p(f"Trades        : {len(trades)}  (live {n_live} / backfill {n_bf})")
    _p(f"Zeitraum      : {dr[0]} → {dr[1]}")

    # ── Absolute Track-Record-Sicht ─────────────────────────────────────────
    rets = [t["pnl_pct"] for t in trades]
    s = _summary(rets)
    overall_ci = _bootstrap_mean_ci(rets, rng)
    _p("\n── Absolute Kennzahlen " + "─" * 37)
    _p(f"  Win-Rate         : {s['win_rate']*100:.1f}%  ({s['wins']}/{s['n']})")
    _p(f"  Ø-Rendite/Trade  : {_fmt_ci(overall_ci)}")
    _p(f"  Median/Trade     : {s['median']:+.2f}%")
    _p(f"  Sharpe (pro Trade): {s['sharpe_pt']:.2f}   (σ={s['std']:.2f}%)")
    _p(f"  Kumuliert (seq.) : {s['compounded']:+.2f}%   ·   MaxDD {s['max_dd']:.2f}%")
    _p("  (sequentiell/1 Position — Track-Record-Proxy, kein Portfolio-Backtest)")

    # ── vs Buy&Hold ─────────────────────────────────────────────────────────
    excess_ci: Optional[Dict] = None
    if not args.no_benchmark:
        cache = _BenchmarkCache()
        excess, matched = [], 0
        for t in trades:
            if t["entry_day"] is None:
                continue
            br = cache.window_return(t["ticker"], t["entry_day"], t["hold_days"])
            if br is None:
                continue
            excess.append(t["pnl_pct"] - br)
            matched += 1
        _p("\n── vs Buy&Hold (Index über dasselbe Haltefenster) " + "─" * 10)
        if matched >= 2:
            excess_ci = _bootstrap_mean_ci(excess, rng)
            cov = matched / len(trades) * 100
            _p(f"  Ø-Excess/Trade   : {_fmt_ci(excess_ci)}")
            _p(f"  Benchmark-Abdeckung: {matched}/{len(trades)} Trades ({cov:.0f}%)")
        else:
            _p(f"  zu wenig Benchmark-Treffer ({matched}) — vs-B&H übersprungen")
    else:
        _p("\n── vs Buy&Hold: übersprungen (--no-benchmark) ")

    # ── Regime ──────────────────────────────────────────────────────────────
    regime_stats = _regime_table(trades, rng)

    # ── Gates ───────────────────────────────────────────────────────────────
    gates = _evaluate_gates(trades, overall_ci, excess_ci, regime_stats, args.min_live)
    _p("\n" + "═" * 64)
    _p(" MEILENSTEIN-GATES (Kapital-Freigabe = ALLE grün)")
    _p("═" * 64)
    for name, ok, detail in gates:
        _p(f"  [{'✓ PASS' if ok else '✗ FAIL'}]  {name}")
        _p(f"           → {detail}")
    all_ok = all(ok for _, ok, _ in gates)
    _p("")
    if all_ok:
        _p("  ►► EVIDENZ-GATE ERREICHT — Kapital-Schritt statistisch gedeckt.")
    else:
        n_fail = sum(1 for _, ok, _ in gates if not ok)
        _p(f"  ►► EVIDENZ-GATE NICHT ERREICHT — {n_fail} offene(s) Gate(s). "
           "Kein Kapital-Schritt.")
    _p("")
    store.close()


if __name__ == "__main__":
    main()
