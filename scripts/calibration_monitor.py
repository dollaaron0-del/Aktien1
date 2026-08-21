#!/usr/bin/env python3
"""
Kalibrierungs-Monitoring — misst die GÜTE der Kalibrierung über Zeit (Ziel 2).

Die CalibrationModel-Schicht LERNT P(WIN) je Bucket. Dieser Report prüft, ob diese
Vorhersagen auch STIMMEN — die Vorbedingung dafür, Kalibrierung je vom bloßen
Advisory (EntryFilter) zum echten Sizing zu befördern. Ohne belegte Kalibrierung
darf ausgesagte Konfidenz das Positionsvolumen NICHT steuern (Ziel 2).

Ehrlich = **walk-forward, out-of-sample**: für jeden Trade wird das Modell nur auf
den ZEITLICH FRÜHEREN Trades gefittet und dann dieser eine vorhergesagt. So misst
der Brier-Score echte Vorhersagekraft, nicht auswendig-Lernen (in-sample wäre
geschönt). Die Vorhersage ist die Netto-P(Win) des EntryFilter — genau das Signal,
das später das Sizing steuern würde.

Kennzahlen:
  * Brier-Score + Brier-Skill-Score (vs. Klimatologie = immer Basisquote raten).
  * Reliability-Tabelle (vorhergesagt vs. beobachtet je Wahrscheinlichkeits-Band),
    ECE (Expected Calibration Error) und MCE (worst-case Band).
  * AUC (Diskriminierung: trennt das Signal Gewinner von Verlierern?).
  * Drift: frühe vs. jüngste Hälfte der Forward-Vorhersagen → Alarm bei Verschlechterung.
  * Snapshot je Lauf nach data/calibration_monitor.json (Verlauf über Kalenderzeit).

Kodierte Stufenpfad-Gates Advisory→Sizing → maschinelles JA/NEIN.

Usage:
  python -m scripts.calibration_monitor
  python -m scripts.calibration_monitor --dimension sentiment   # nur eine Dimension
  python -m scripts.calibration_monitor --no-store              # Snapshot nicht anhängen
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.calibration import CalibrationModel  # noqa: E402
from analyzers.entry_filter import EntryFilter  # noqa: E402
from analyzers.experience_store import ExperienceStore  # noqa: E402

_MONITOR_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "calibration_monitor.json")
_HISTORY_CAP = 400   # Snapshots im Verlauf begrenzen

# ── Stufenpfad-Gates (Advisory → Sizing) ─────────────────────────────────────
N_MIN_FORWARD = 100    # genug out-of-sample Vorhersagen für eine belastbare Aussage
ECE_MAX = 0.10         # max. Expected Calibration Error für "gut kalibriert"
AUC_MIN = 0.55         # min. Diskriminierung (0.5 = Zufall)
BSS_MIN = 0.0          # Brier-Skill-Score > 0 = schlägt Klimatologie
DRIFT_ECE_DELTA = 0.07  # jüngste Hälfte darf ECE um höchstens so viel verschlechtern
WARMUP = 20            # erst ab so vielen Vortrades vorhersagen (sonst leeres Modell)


# ── Metriken (rein, testbar) ──────────────────────────────────────────────────
def brier(ps: Sequence[float], ys: Sequence[int]) -> float:
    p = np.asarray(ps, dtype=float)
    y = np.asarray(ys, dtype=float)
    return float(np.mean((p - y) ** 2)) if p.size else float("nan")


def brier_skill_score(ps: Sequence[float], ys: Sequence[int]) -> float:
    """1 - Brier/Brier_ref, ref = immer die Basisquote raten. >0 = Modell hat Skill."""
    y = np.asarray(ys, dtype=float)
    if y.size == 0:
        return float("nan")
    base = float(y.mean())
    b_ref = float(np.mean((base - y) ** 2))
    if b_ref == 0:
        return float("nan")
    return 1.0 - brier(ps, ys) / b_ref


def _rankdata(a: np.ndarray) -> np.ndarray:
    """Durchschnittsränge (Ties gemittelt) — ersetzt scipy.stats.rankdata.

    Vollständig vektorisiert (kein Python-Loop über Ties) — wichtig für
    analyzers/sentiment_forward_study.py, das dies pro Bootstrap-Iteration
    (Größenordnung 10.000+) aufruft; hier reuse statt Duplikat."""
    order = np.argsort(a, kind="mergesort")
    raw = np.empty(a.size, dtype=float)
    raw[order] = np.arange(1, a.size + 1)
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, raw)
    return (sums / counts)[inv]


def auc(ps: Sequence[float], ys: Sequence[int]) -> float:
    """ROC-AUC via Mann-Whitney (Ties korrekt gemittelt). nan wenn nur eine Klasse."""
    p = np.asarray(ps, dtype=float)
    y = np.asarray(ys, dtype=int)
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _rankdata(p)
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def reliability_bins(ps: Sequence[float], ys: Sequence[int],
                     width: float = 0.1) -> List[Dict]:
    """Bänder fester Breite (0.0–1.0): je Band vorhergesagt vs. beobachtet + n."""
    p = np.asarray(ps, dtype=float)
    y = np.asarray(ys, dtype=float)
    bins = []
    edges = np.arange(0.0, 1.0 + 1e-9, width)
    for lo, hi in zip(edges[:-1], edges[1:]):
        # letztes Band schließt 1.0 mit ein
        mask = (p >= lo) & (p < hi) if hi < 1.0 else (p >= lo) & (p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        bins.append({
            "lo": float(lo), "hi": float(hi), "n": n,
            "pred": float(p[mask].mean()),
            "obs": float(y[mask].mean()),
        })
    return bins


def ece_mce(bins: List[Dict], total: int) -> Tuple[float, float]:
    """Expected + Maximum Calibration Error aus den Reliability-Bändern."""
    if not bins or total == 0:
        return float("nan"), float("nan")
    ece = sum(b["n"] / total * abs(b["obs"] - b["pred"]) for b in bins)
    mce = max(abs(b["obs"] - b["pred"]) for b in bins)
    return float(ece), float(mce)


# ── Walk-Forward-Vorhersagen ─────────────────────────────────────────────────
def walk_forward(rows: List[Tuple[Dict, Dict]], dimensions: Sequence[str],
                 warmup: int = WARMUP) -> List[Dict]:
    """Out-of-sample: Modell je Schritt nur auf früheren Trades fitten, dann den
    aktuellen vorhersagen. rows müssen zeitlich sortiert sein.

    Rückgabe: Liste {ts, p, y, pnl} für jeden vorhergesagten Trade (ab warmup).
    """
    out = []
    for i in range(warmup, len(rows)):
        feat, outc = rows[i]
        if outc.get("outcome") not in ("WIN", "LOSS"):
            continue
        model = CalibrationModel().fit_rows(rows[:i])
        ef = EntryFilter(model=model, dimensions=tuple(dimensions))
        verdict = ef.evaluate(feat)
        out.append({
            "ts": feat.get("decided_at") or "",
            "p": float(verdict.p_win),
            "y": 1 if outc.get("outcome") == "WIN" else 0,
            "pnl": float(outc.get("pnl_pct") or 0.0),
        })
    return out


def _metrics(preds: List[Dict]) -> Dict:
    ps = [d["p"] for d in preds]
    ys = [d["y"] for d in preds]
    bins = reliability_bins(ps, ys)
    ece, mce = ece_mce(bins, len(preds))
    return {
        "n": len(preds),
        "brier": brier(ps, ys),
        "bss": brier_skill_score(ps, ys),
        "ece": ece, "mce": mce,
        "auc": auc(ps, ys),
        "base_rate": float(np.mean(ys)) if ys else float("nan"),
        "mean_pred": float(np.mean(ps)) if ps else float("nan"),
        "bins": bins,
    }


# ── Drift ─────────────────────────────────────────────────────────────────────
def drift_check(preds: List[Dict]) -> Dict:
    """Frühe vs. jüngste Hälfte der Forward-Vorhersagen: verschlechtert sich die
    Kalibrierung (ECE) über die Zeit? Braucht genug Daten in beiden Hälften."""
    if len(preds) < 2 * 15:
        return {"available": False, "reason": f"zu wenig Daten ({len(preds)})"}
    mid = len(preds) // 2
    early, recent = _metrics(preds[:mid]), _metrics(preds[mid:])
    delta = recent["ece"] - early["ece"]
    return {
        "available": True,
        "early_ece": early["ece"], "recent_ece": recent["ece"],
        "early_brier": early["brier"], "recent_brier": recent["brier"],
        "ece_delta": delta,
        "alarm": bool(np.isfinite(delta) and delta > DRIFT_ECE_DELTA),
    }


# ── Persistenz des Verlaufs ──────────────────────────────────────────────────
def append_snapshot(metrics: Dict, path: str = _MONITOR_FILE) -> None:
    """Hängt eine kompakte Momentaufnahme an data/calibration_monitor.json (atomar).
    So wächst über echte Kalenderläufe ein Drift-Verlauf, sobald der Bot live labelt."""
    snap = {
        "run_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "n": metrics["n"], "brier": round(metrics["brier"], 5),
        "bss": round(metrics["bss"], 5), "ece": round(metrics["ece"], 5),
        "mce": round(metrics["mce"], 5), "auc": round(metrics["auc"], 5),
    }
    history = []
    if os.path.exists(path):
        try:
            history = json.load(open(path)).get("history", [])
        except Exception:
            history = []
    history.append(snap)
    history = history[-_HISTORY_CAP:]
    data_dir = os.path.dirname(path)
    os.makedirs(data_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=data_dir, suffix=".tmp",
                                     delete=False, encoding="utf-8") as tmp:
        json.dump({"history": history}, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, path)


# ── Gates ─────────────────────────────────────────────────────────────────────
def evaluate_gates(metrics: Dict, drift: Dict,
                   n_live: int) -> List[Tuple[str, bool, str]]:
    gates: List[Tuple[str, bool, str]] = []
    n = metrics["n"]

    gates.append((
        f"Stichprobe ≥ {N_MIN_FORWARD} out-of-sample Vorhersagen",
        n >= N_MIN_FORWARD,
        f"{n} Forward-Vorhersagen (davon {n_live} aus Live-Trades)",
    ))

    ece = metrics["ece"]
    gates.append((
        f"Gut kalibriert (ECE ≤ {ECE_MAX})",
        np.isfinite(ece) and ece <= ECE_MAX,
        f"ECE={ece:.3f}" if np.isfinite(ece) else "keine Bänder",
    ))

    bss = metrics["bss"]
    gates.append((
        "Schlägt Klimatologie (Brier-Skill > 0)",
        np.isfinite(bss) and bss > BSS_MIN,
        f"BSS={bss:+.3f}" if np.isfinite(bss) else "n/a",
    ))

    a = metrics["auc"]
    gates.append((
        f"Diskriminierung (AUC ≥ {AUC_MIN})",
        np.isfinite(a) and a >= AUC_MIN,
        f"AUC={a:.3f}" if np.isfinite(a) else "nur eine Klasse",
    ))

    gates.append((
        "Kein Kalibrierungs-Drift",
        drift.get("available", False) and not drift.get("alarm", True),
        (f"ΔECE={drift['ece_delta']:+.3f} (früh {drift['early_ece']:.3f} → "
         f"jüngst {drift['recent_ece']:.3f})" if drift.get("available")
         else drift.get("reason", "n/a")),
    ))
    return gates


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description="Kalibrierungs-Monitoring + Sizing-Gate")
    ap.add_argument("--dimension", default=None,
                    help="nur EINE Dimension monitoren (sonst EntryFilter-Kombi)")
    ap.add_argument("--warmup", type=int, default=WARMUP)
    ap.add_argument("--no-store", action="store_true",
                    help="Snapshot NICHT an data/calibration_monitor.json anhängen")
    args = ap.parse_args()

    dimensions = (args.dimension,) if args.dimension else ("sentiment", "theme", "regime")

    store = ExperienceStore()
    rows = list(store.iter_labeled())
    rows.sort(key=lambda r: r[0].get("decided_at") or "")
    n_live_total = sum(1 for _, o in rows if (o.get("label_source") or "") == "live")

    _p("═" * 64)
    _p(" Kalibrierungs-Monitoring  —  Ziel 2 (Advisory → Sizing)")
    _p("═" * 64)
    _p(f"Dimensionen   : {', '.join(dimensions)}")
    _p(f"gelabelte Zeilen: {len(rows)}  (live {n_live_total})   ·   Warmup {args.warmup}")

    if len(rows) <= args.warmup:
        _p("\n(zu wenig gelabelte Daten für Walk-Forward)")
        store.close()
        return

    preds = walk_forward(rows, dimensions, warmup=args.warmup)
    if not preds:
        _p("\n(keine Forward-Vorhersagen erzeugt)")
        store.close()
        return

    m = _metrics(preds)
    # Live-Anteil der Forward-Vorhersagen (über decided_at der live-Zeilen).
    live_ts = {f.get("decided_at") for f, o in rows
               if (o.get("label_source") or "") == "live"}
    n_live_pred = sum(1 for d in preds if d["ts"] in live_ts)

    _p(f"Forward-Vorhersagen: {m['n']}  (Basisquote {m['base_rate']*100:.1f}% · "
       f"Ø-Vorhersage {m['mean_pred']*100:.1f}%)")

    _p("\n── Güte-Kennzahlen " + "─" * 41)
    _p(f"  Brier-Score      : {m['brier']:.4f}   (niedriger = besser)")
    _p(f"  Brier-Skill (BSS): {m['bss']:+.4f}   (>0 schlägt Basisquote-Raten)")
    _p(f"  ECE / MCE        : {m['ece']:.3f} / {m['mce']:.3f}")
    _p(f"  AUC              : {m['auc']:.3f}   (0.5=Zufall)")

    _p("\n── Reliability (vorhergesagt vs. beobachtet) " + "─" * 15)
    _p(f"  {'Band':<12}{'N':>5}{'Vorhrg.':>10}{'Beobacht.':>11}{'Δ':>8}")
    for b in m["bins"]:
        _p(f"  {b['lo']:.1f}-{b['hi']:.1f}   {b['n']:>5}{b['pred']*100:>9.1f}%"
           f"{b['obs']*100:>10.1f}%{(b['obs']-b['pred'])*100:>+8.1f}")

    drift = drift_check(preds)
    _p("\n── Drift (frühe vs. jüngste Hälfte) " + "─" * 24)
    if drift.get("available"):
        _p(f"  ECE  früh {drift['early_ece']:.3f} → jüngst {drift['recent_ece']:.3f}  "
           f"(Δ {drift['ece_delta']:+.3f})")
        _p(f"  Brier früh {drift['early_brier']:.4f} → jüngst {drift['recent_brier']:.4f}")
        _p(f"  {'⚠ DRIFT-ALARM' if drift['alarm'] else '✓ stabil'}")
    else:
        _p(f"  {drift.get('reason')}")

    gates = evaluate_gates(m, drift, n_live_pred)
    _p("\n" + "═" * 64)
    _p(" SIZING-FREIGABE-GATES (Kalibrierung darf Volumen steuern = ALLE grün)")
    _p("═" * 64)
    for name, ok, detail in gates:
        _p(f"  [{'✓ PASS' if ok else '✗ FAIL'}]  {name}")
        _p(f"           → {detail}")
    all_ok = all(ok for _, ok, _ in gates)
    _p("")
    if all_ok:
        _p("  ►► SIZING FREIGEGEBEN — Kalibrierung darf das Positionsvolumen steuern.")
    else:
        n_fail = sum(1 for _, ok, _ in gates if not ok)
        _p(f"  ►► NUR ADVISORY — {n_fail} offene(s) Gate(s). Kalibrierung bleibt beratend "
           "(kein Sizing).")
    _p("")

    if not args.no_store:
        append_snapshot(m)
        _p(f"Snapshot angehängt → {os.path.relpath(_MONITOR_FILE, os.getcwd())} "
           f"(Verlauf für Drift über Kalenderzeit)")
    store.close()


if __name__ == "__main__":
    main()
