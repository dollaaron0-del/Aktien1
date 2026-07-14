"""
analyzers/sentiment_forward_study.py – Sentiment-Forward-Study (Roadmap 3.1).

Prüft, ob der von der KI vergebene sentiment_score tatsächlich forward-
prädiktiv ist (Edge je Score-Bucket + Information Coefficient), statt nur
implizit über recommendation/BUY-Boden zu wirken. Datenquelle: derselbe
gelabelte Datensatz wie scripts/track_record.py (ExperienceStore,
data/experience.db), hier aber sentiment_score statt nur den Handelsausgang
betrachtet.

EHRLICHKEITS-PROTOKOLL wie überall im strategy_lab: Bucket-Grenzen sind FEST
vorab an buy_threshold (Default 0.65) orientiert, nicht datenabhängig
gesucht — sonst wäre das selbst eine Form von Data-Dredging (vgl.
w52_high-Fenster, Roadmap 5.1). label_source wird durchgereicht, weil
backfill_hypo (kontrafaktisch simuliert) und live (real gehandelt) nicht
dieselbe Evidenzqualität haben — track_record.py zählt seine Meilensteine
deshalb ebenfalls nur aus label_source='live'.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from analyzers.experience_store import ExperienceStore

# Feste, an buy_threshold orientierte Buckets statt datenabhängiger Quantile.
_BUCKETS: Tuple[Tuple[float, float, str], ...] = (
    (0.0, 0.3, "<0.30"),
    (0.3, 0.5, "0.30–0.50"),
    (0.5, 0.65, "0.50–0.65"),
    (0.65, 0.8, "0.65–0.80"),
    (0.8, 1.01, "≥0.80"),
)

_DEFAULT_ITERS = 20000
_DEFAULT_SEED = 20260714


def _bucket_label(score: float) -> Optional[str]:
    for lo, hi, label in _BUCKETS:
        if lo <= score < hi:
            return label
    return None


def load_labeled_scores(
    store: ExperienceStore, sources: Optional[set] = None
) -> List[Dict]:
    """Gelabelte Entscheidungen mit gültigem sentiment_score + pnl_pct."""
    rows = []
    for feat, out in store.iter_labeled():
        src = out.get("label_source")
        if sources is not None and src not in sources:
            continue
        pnl = out.get("pnl_pct")
        score = feat.get("sentiment_score")
        if pnl is None or score is None:
            continue
        try:
            score = float(score)
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(score) and np.isfinite(pnl)):
            continue
        rows.append({
            "ticker": feat.get("ticker") or "?",
            "decided_at": feat.get("decided_at") or "",
            "sentiment_score": score,
            "pnl_pct": pnl,
            "outcome": out.get("outcome"),
            "source": src,
        })
    return rows


def bucket_edges(rows: List[Dict], rng: np.random.Generator,
                  iters: int = _DEFAULT_ITERS) -> Dict[str, Dict]:
    """Edge je Score-Bucket — Bootstrap-CI wie scripts.track_record._bootstrap_mean_ci
    (bewusst dieselbe Funktion statt einer eigenen Kopie)."""
    from scripts.track_record import _bootstrap_mean_ci

    by_bucket: Dict[str, List[float]] = {label: [] for _, _, label in _BUCKETS}
    for r in rows:
        label = _bucket_label(r["sentiment_score"])
        if label is not None:
            by_bucket[label].append(r["pnl_pct"])
    out = {}
    for label, rets in by_bucket.items():
        ci = _bootstrap_mean_ci(rets, rng, iters=iters)
        wins = sum(1 for x in rets if x > 0)
        out[label] = {**ci, "win_rate": (wins / len(rets)) if rets else float("nan")}
    return out


def _rank_avg(a: np.ndarray) -> np.ndarray:
    """Rang mit Mittelwert bei Ties (wie scipy.stats.rankdata(method='average')),
    ohne scipy-Abhängigkeit — dieselbe Begründung wie in track_record.py: numpy
    reicht. Vollständig vektorisiert (kein Python-Loop je Bootstrap-Iteration)."""
    order = np.argsort(a, kind="mergesort")
    raw = np.empty(len(a))
    raw[order] = np.arange(1, len(a) + 1)
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, raw)
    return (sums / counts)[inv]


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rank_avg(a), _rank_avg(b)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def information_coefficient(rows: List[Dict], rng: np.random.Generator,
                            iters: int = _DEFAULT_ITERS) -> Dict:
    """Spearman-Rangkorrelation sentiment_score↔pnl_pct + Paar-Bootstrap-CI +
    einseitiges P(IC≤0). Rangkorrelation statt Pearson: robust gegen die
    fat-tailed, nicht-normalen Trade-Renditen (dieselbe Begründung wie der
    Bootstrap-statt-t-Test-Ansatz in track_record.py). n<3 → keine sinnvolle
    Korrelation."""
    scores = np.asarray([r["sentiment_score"] for r in rows], dtype=float)
    pnls = np.asarray([r["pnl_pct"] for r in rows], dtype=float)
    n = scores.size
    if n < 3:
        return {"n": n, "ic": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "p_le0": float("nan")}

    ic = _spearman(scores, pnls)
    idx = rng.integers(0, n, size=(iters, n))
    ics = np.array([_spearman(scores[i], pnls[i]) for i in idx])
    finite = ics[np.isfinite(ics)]
    if finite.size == 0:
        return {"n": n, "ic": ic, "lo": float("nan"), "hi": float("nan"),
                "p_le0": float("nan")}
    lo, hi = np.percentile(finite, [2.5, 97.5])
    return {"n": n, "ic": ic, "lo": float(lo), "hi": float(hi),
            "p_le0": float((finite <= 0).mean())}


def run_study(
    store: Optional[ExperienceStore] = None,
    sources: Optional[set] = None,
    iters: int = _DEFAULT_ITERS,
    seed: int = _DEFAULT_SEED,
) -> Dict:
    """Kompletter Studien-Lauf: Buckets + Information Coefficient, gesamt und
    je label_source (live/backfill/backfill_hypo getrennt ausgewiesen — nicht
    dieselbe Evidenzqualität, siehe Moduldoc)."""
    close_after = store is None
    store = store or ExperienceStore()
    try:
        rng = np.random.default_rng(seed)
        rows = load_labeled_scores(store, sources=sources)
        by_source: Dict[str, int] = {}
        for r in rows:
            src = r["source"] or "?"
            by_source[src] = by_source.get(src, 0) + 1
        return {
            "n_total": len(rows),
            "by_source": by_source,
            "buckets": bucket_edges(rows, rng, iters=iters),
            "information_coefficient": information_coefficient(rows, rng, iters=iters),
        }
    finally:
        if close_after:
            store.close()
