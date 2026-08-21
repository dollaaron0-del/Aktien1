"""
CalibrationModel – erste Lern-Schicht auf dem ExperienceStore.

Lernt empirische Trefferwahrscheinlichkeit (P(WIN)) und erwartete Kante
(avg pnl_pct) je Feature-Bucket – mit **Beta-Binomial-Shrinkage zur Globalprior**.
Das ist der Punkt: bei wenig Daten wird ein Bucket-Schätzer zur Gesamt-Quote
zurückgezogen, statt aus 3 Trades 0%/100% zu behaupten. So degradiert das Modell
sauber bei kleinem N.

Bewusst KEIN neuronales Netz / kein RL – reine, nachvollziehbare Statistik. Die
Schicht ist standalone: sie liest den ExperienceStore und persistiert ihr Modell
nach data/calibration.json. Sie greift NICHT in den (pausierten) Live-Pfad ein.

Motivation: Der Backfill zeigte, dass sentiment_score NICHT monoton kalibriert ist
(Bucket 0.6–0.7 schlechter als 0.5–0.6 und 0.7–0.8). Genau das macht dieses Modell
sichtbar und nutzbar.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_MODEL_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "calibration.json")

# Pseudo-Beobachtungen für die Shrinkage (äquivalente Stichprobengröße der Prior).
# Höher = stärkeres Zurückziehen kleiner Buckets zur Globalquote.
_PRIOR_STRENGTH = 12.0
# Ab wie vielen Beobachtungen gilt ein Bucket-Schätzer als belastbar.
_MIN_SUPPORT = 8

# Feature-Dimensionen, die kalibriert werden: name -> Bucketizer
def _sentiment_bucket(f: Dict) -> Optional[str]:
    s = f.get("sentiment_score")
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    lo = int(v * 10) / 10
    return f"{lo:.1f}-{lo + 0.1:.1f}"


def _confidence_bucket(f: Dict) -> Optional[str]:
    c = (f.get("confidence") or "").upper()
    return c or None


def _debate_bucket(f: Dict) -> Optional[str]:
    d = (f.get("debate_winner") or "").upper()
    return d or None


def _theme_bucket(f: Dict) -> Optional[str]:
    """Erstes Thema des Tickers (Sektor-Proxy) via stock_relations. None, wenn
    der Ticker keinem Thema zugeordnet ist oder das Modul fehlt."""
    ticker = f.get("ticker")
    if not ticker:
        return None
    try:
        from analyzers.stock_relations import _TICKER_TO_THEMES
    except Exception:
        return None
    themes = _TICKER_TO_THEMES.get(str(ticker).upper())
    return themes[0] if themes else None


def _regime_bucket(f: Dict) -> Optional[str]:
    """Marktregime zum Entscheidungszeitpunkt (BULL/NEUTRAL/BEAR/CRISIS). None,
    wenn nicht erfasst (z.B. Papier-Backfill) → Bucket wird schlicht ignoriert.
    Macht den Befund aus [[paper-forward-erster-befund]] lernbar: eine Mechanik
    kann speziell in EINEM Regime verlieren."""
    r = (f.get("regime") or "").strip().upper()
    return r or None


def _macro_bias_bucket(f: Dict) -> Optional[str]:
    """Makro-Bias (-1..+1) grob in Risk-Off/Neutral/Risk-On gebändert. Grobe
    Bänder, damit die Buckets bei kleinem N belastbar bleiben. None, wenn kein
    Zahlenwert erfasst wurde."""
    v = f.get("macro_bias")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x <= -0.33:
        return "RISK_OFF"
    if x >= 0.33:
        return "RISK_ON"
    return "NEUTRAL"


_DIMENSIONS = {
    "sentiment": _sentiment_bucket,
    "confidence": _confidence_bucket,
    "debate_winner": _debate_bucket,
    "theme": _theme_bucket,
    "regime": _regime_bucket,
    "macro_bias": _macro_bias_bucket,
}


@dataclass
class BucketStat:
    n: int
    wins: int
    raw_win_rate: float       # ungeglättet
    win_rate: float           # nach Shrinkage zur Globalprior
    avg_pnl: float            # nach Shrinkage
    raw_avg_pnl: float
    reliable: bool            # n >= _MIN_SUPPORT


@dataclass
class CalibrationResult:
    p_win: float              # geschätzte Trefferwahrscheinlichkeit
    expected_edge: float      # erwartete Kante (% P&L), geshrinkt
    n_support: int            # Beobachtungen im genutzten Bucket
    reliable: bool            # genug Daten?
    basis: str                # welche Dimension/Bucket genutzt wurde


class CalibrationModel:
    def __init__(self, model_file: str = _MODEL_FILE):
        self.model_file = model_file
        self.global_win_rate: float = 0.5
        self.global_avg_pnl: float = 0.0
        self.n_total: int = 0
        self.fitted_at: Optional[str] = None
        # dim -> {bucket -> BucketStat}
        self.tables: Dict[str, Dict[str, BucketStat]] = {}

    # ── Lernen ──────────────────────────────────────────────────────────────────
    def fit(self, store, prefer_live: bool = True, min_live: int = 30) -> "CalibrationModel":
        """Lernt die Kalibrierungstabellen aus einem ExperienceStore.

        prefer_live (Default): der mechanische Papier-Backfill (7%SL/20%TP, Exit-
        Regime ohne nachgewiesene Kante) bildet das reale Bot-Verhalten NICHT ab,
        soll aber bei wenig Live-Daten nicht ersatzlos fehlen. Statt einer harten
        Kante bei ``min_live`` (die das Modell schlagartig umspringen ließ) wird der
        Backfill jetzt GEWICHTET ausgeblendet: sein Gewicht rampt linear von 1.0
        (0 Live-Outcomes) auf 0.0 (>= min_live Live-Outcomes). Live-Zeilen zählen
        immer voll. Endpunkte bleiben identisch zum alten Verhalten, der Übergang
        ist glatt.
        """
        if not prefer_live:
            rows = list(store.iter_labeled())
            return self.fit_rows(rows)

        # Partition über ALLE gelabelten Zeilen: 'live' vs. Rest (backfill UND
        # backfill_hypo). Wichtig: nicht nur label_source='backfill' ziehen —
        # sonst fielen die hypothetischen HOLD/SKIP-Labels still unter den Tisch.
        all_rows = list(store.iter_labeled())
        live = [(f, o) for f, o in all_rows if (o.get("label_source") or "") == "live"]
        backfill = [(f, o) for f, o in all_rows if (o.get("label_source") or "") != "live"]
        n_live = len(live)
        # Backfill-Gewicht: 1.0 → 0.0 während Live von 0 → min_live wächst.
        bf_w = max(0.0, 1.0 - (n_live / min_live)) if min_live > 0 else 0.0
        rows = live + backfill
        weights = [1.0] * n_live + [bf_w] * len(backfill)
        log.info("CalibrationModel: fitte auf %d Live + %d Backfill (Backfill-Gewicht %.2f)",
                 n_live, len(backfill), bf_w)
        return self.fit_rows(rows, weights=weights)

    def fit_rows(self, rows: List, weights: Optional[List[float]] = None) -> "CalibrationModel":
        """Wie fit(), aber auf einer Liste von (features, outcome)-Tupeln (testbar).

        weights (optional, parallel zu rows): Zeilen-Gewichte für die geschätzten
        Kennzahlen (Globalquoten + Bucket-Schätzer). Fehlt es, zählt jede Zeile 1.0.
        Die Roh-Zählungen (``n``, ``wins``, ``reliable``) bleiben ungewichtet – sie
        beschreiben, wie viele echte Beobachtungen ein Bucket hat, nicht ihr Gewicht.
        """
        n = len(rows)
        if weights is None:
            weights = [1.0] * n
        wpairs = list(zip(rows, weights))
        w_total = sum(weights)
        w_wins = sum(w for (_, o), w in wpairs if o.get("outcome") == "WIN")
        w_pnl_sum = sum(w * float(o["pnl_pct"]) for (_, o), w in wpairs if o.get("pnl_pct") is not None)
        w_pnl_n = sum(w for (_, o), w in wpairs if o.get("pnl_pct") is not None)
        self.n_total = n
        self.global_win_rate = (w_wins / w_total) if w_total else 0.5
        self.global_avg_pnl = (w_pnl_sum / w_pnl_n) if w_pnl_n else 0.0

        self.tables = {}
        k = _PRIOR_STRENGTH
        for dim, bucketize in _DIMENSIONS.items():
            agg: Dict[str, Dict] = {}
            for (f, o), w in wpairs:
                b = bucketize(f)
                if b is None:
                    continue
                a = agg.setdefault(b, {"n": 0, "wins": 0, "pnl_n": 0, "pnl_sum": 0.0,
                                       "w": 0.0, "w_wins": 0.0, "w_pnl_sum": 0.0, "w_pnl_n": 0.0})
                a["n"] += 1                       # ungewichtete Roh-Zählung
                a["w"] += w
                is_win = o.get("outcome") == "WIN"
                if is_win:
                    a["wins"] += 1
                    a["w_wins"] += w
                if o.get("pnl_pct") is not None:
                    a["pnl_n"] += 1
                    a["pnl_sum"] += float(o["pnl_pct"])
                    a["w_pnl_sum"] += w * float(o["pnl_pct"])
                    a["w_pnl_n"] += w
            table: Dict[str, BucketStat] = {}
            for b, a in agg.items():
                nn, ww = a["n"], a["wins"]
                raw_wr = ww / nn if nn else 0.0
                # Gewichtete Beta-Binomial-Shrinkage zur (gewichteten) Globalquote.
                shr_wr = (a["w_wins"] + k * self.global_win_rate) / (a["w"] + k) if (a["w"] + k) else self.global_win_rate
                raw_pnl = (a["pnl_sum"] / a["pnl_n"]) if a["pnl_n"] else 0.0
                shr_pnl = (a["w_pnl_sum"] + k * self.global_avg_pnl) / (a["w_pnl_n"] + k) if (a["w_pnl_n"] + k) else 0.0
                table[b] = BucketStat(
                    n=nn, wins=ww,
                    raw_win_rate=round(raw_wr, 4), win_rate=round(shr_wr, 4),
                    avg_pnl=round(shr_pnl, 4), raw_avg_pnl=round(raw_pnl, 4),
                    reliable=nn >= _MIN_SUPPORT,
                )
            self.tables[dim] = table

        self.fitted_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        return self

    # ── Anwenden ──────────────────────────────────────────────────────────────
    def calibrate(self, features: Dict, dimension: str = "sentiment") -> CalibrationResult:
        """Liefert kalibrierte P(WIN) + erwartete Kante für eine (künftige) Entscheidung.

        Fällt auf die Globalquote zurück, wenn der Bucket unbekannt/leer ist.
        """
        bucketize = _DIMENSIONS.get(dimension)
        if bucketize is None:
            raise ValueError(f"unbekannte Dimension: {dimension}")
        b = bucketize(features)
        stat = self.tables.get(dimension, {}).get(b) if b else None
        if stat is None:
            return CalibrationResult(
                p_win=round(self.global_win_rate, 4),
                expected_edge=round(self.global_avg_pnl, 4),
                n_support=0, reliable=False, basis=f"{dimension}=global",
            )
        return CalibrationResult(
            p_win=stat.win_rate, expected_edge=stat.avg_pnl,
            n_support=stat.n, reliable=stat.reliable,
            basis=f"{dimension}={b}",
        )

    def summary(self) -> Dict:
        return {
            "n_total": self.n_total,
            "global_win_rate": round(self.global_win_rate, 4),
            "global_avg_pnl": round(self.global_avg_pnl, 4),
            "fitted_at": self.fitted_at,
            "dimensions": {d: len(t) for d, t in self.tables.items()},
        }

    # ── Persistenz (atomic, analog rl_agent) ────────────────────────────────────
    def save(self) -> None:
        data_dir = os.path.dirname(self.model_file)
        os.makedirs(data_dir, exist_ok=True)
        payload = {
            "global_win_rate": self.global_win_rate,
            "global_avg_pnl": self.global_avg_pnl,
            "n_total": self.n_total,
            "fitted_at": self.fitted_at,
            "prior_strength": _PRIOR_STRENGTH,
            "min_support": _MIN_SUPPORT,
            "tables": {d: {b: asdict(s) for b, s in t.items()} for d, t in self.tables.items()},
        }
        with tempfile.NamedTemporaryFile(mode="w", dir=data_dir, suffix=".tmp",
                                         delete=False, encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, self.model_file)

    def load(self) -> bool:
        if not os.path.exists(self.model_file):
            return False
        try:
            d = json.load(open(self.model_file))
        except Exception as exc:  # pragma: no cover - defensiv
            log.warning("CalibrationModel.load fehlgeschlagen: %s", exc)
            return False
        self.global_win_rate = d.get("global_win_rate", 0.5)
        self.global_avg_pnl = d.get("global_avg_pnl", 0.0)
        self.n_total = d.get("n_total", 0)
        self.fitted_at = d.get("fitted_at")
        self.tables = {
            dim: {b: BucketStat(**s) for b, s in t.items()}
            for dim, t in d.get("tables", {}).items()
        }
        return True


# ── Auto-Re-Fit (schließt den Lern-Kreis) ────────────────────────────────────────
def _age_hours(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds() / 3600.0


def auto_refit(max_age_hours: float = 24.0, store=None) -> Optional["CalibrationModel"]:
    """Re-fittet data/calibration.json aus dem ExperienceStore, wenn veraltet.

    Gibt das neu gefittete Modell zurück (oder None, wenn nichts zu tun / kein Store).
    Fail-safe: jeder Fehler → None, niemals Exception in den Aufrufer. Gedacht für den
    Bot-Start (main.py), damit gesammelte Live-Outcomes ohne Handarbeit (≤ max_age_hours)
    in das vom EntryFilter genutzte Modell zurückfließen.
    """
    try:
        existing = CalibrationModel()
        if existing.load():
            age = _age_hours(existing.fitted_at)
            if age is not None and age < max_age_hours:
                return None  # noch frisch genug
        if store is None:
            from analyzers.experience_store import ExperienceStore
            store = ExperienceStore()
        model = CalibrationModel().fit(store)
        if model.n_total <= 0:
            return None  # keine gelabelten Daten → altes Modell behalten
        model.save()
        log.info("CalibrationModel: Auto-Re-Fit (n=%d, fitted_at=%s)",
                 model.n_total, model.fitted_at)
        return model
    except Exception as exc:  # pragma: no cover - defensiv
        log.debug("auto_refit übersprungen: %s", exc)
        return None
