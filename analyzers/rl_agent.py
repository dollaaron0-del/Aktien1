"""
RL Agent – Kontextueller Bandit mit linearer Policy für Trade-Entscheidungen

Algorithmus: Epsilon-Greedy Linear Policy Gradient
  - Kein neuronales Netz – stattdessen dot product (state · weights) als expected value
  - Update-Regel: w += lr * reward * state  (REINFORCE / Policy Gradient)
  - Epsilon-Greedy Exploration: 10% zufällige Entscheidungen, 90% Policy
  - Nach 50 Trades: Epsilon sinkt auf 0.05 (mehr Exploitation, weniger Exploration)

State Space (6 Features, alle normalisiert auf sinnvolle Bereiche):
  sentiment_score    – Claude-Score 0.0-1.0
  vix_level          – VIX / 50, normalisiert 0.0-1.0
  momentum_5d        – 5-Tage-Momentum normalisiert -1..1
  news_velocity      – Artikel letzte 24h / 20
  confidence_encoded – HIGH=1.0, MEDIUM=0.5, LOW=0.0
  regime_encoded     – BULL=1.0, NEUTRAL=0.5, BEAR=0.0, CRISIS=-0.5

Persistenz: data/rl_weights.json (atomic write via tempfile + os.replace)
"""
from __future__ import annotations

import json
import math
import os
import random
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Tuple

from logger import get_logger

log = get_logger(__name__)

# ── Pfade ─────────────────────────────────────────────────────────────────────

_WEIGHTS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rl_weights.json")

# ── Hyperparameter ─────────────────────────────────────────────────────────────

_N_FEATURES          = 6
_EPSILON_INITIAL     = 0.10   # 10% Exploration zu Beginn
_EPSILON_REDUCED     = 0.05   # 5% nach >= 50 Trades (mehr Exploitation)
_EPSILON_THRESHOLD   = 50     # Anzahl Trades bis zur Epsilon-Reduktion
_LEARNING_RATE       = 0.01
_BUY_THRESHOLD       = 0.0    # expected_value > 0 → kaufen
_MIN_TRADES          = 10     # Erst nach 10 Trades lernt der Agent aktiv

# Reward-Faktoren
_FAST_WIN_BONUS      = 0.5    # Bonus wenn Gewinn in < hold_days/2 erzielt
_STOP_LOSS_MALUS     = 0.3    # Malus wenn Stop-Loss ausgelöst

# Position-Size-Modifier Stufen
_MODIFIERS = [0.5, 0.75, 1.0, 1.25]

# Feature-Namen für Erklärungen
_FEATURE_NAMES = [
    "sentiment_score",
    "vix_level",
    "momentum_5d",
    "news_velocity",
    "confidence_encoded",
    "regime_encoded",
]


# ── State Dataclass ────────────────────────────────────────────────────────────

@dataclass
class RLState:
    """
    Marktbeschreibung zum Zeitpunkt der Kaufentscheidung.
    Alle Felder sind normalisiert – direkt als numpy-Array verwendbar.
    """
    sentiment_score:    float   # 0.0 – 1.0 (Claude-Sentiment)
    vix_level:          float   # VIX / 50, normalisiert 0.0 – 1.0
    momentum_5d:        float   # 5-Tage-Momentum normalisiert -1..1
    news_velocity:      float   # Artikel / 24h / 20
    confidence_encoded: float   # HIGH=1.0, MEDIUM=0.5, LOW=0.0
    regime_encoded:     float   # BULL=1.0, NEUTRAL=0.5, BEAR=0.0, CRISIS=-0.5

    def to_array(self) -> "np.ndarray":  # type: ignore[name-defined]
        """Gibt den State als numpy-Array zurück (6 Features)."""
        import numpy as np
        return np.array([
            self.sentiment_score,
            self.vix_level,
            self.momentum_5d,
            self.news_velocity,
            self.confidence_encoded,
            self.regime_encoded,
        ], dtype=float)

    @staticmethod
    def encode_confidence(confidence: str) -> float:
        """Konvertiert Confidence-String zu numerischem Feature."""
        return {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.0}.get(confidence.upper(), 0.5)

    @staticmethod
    def encode_regime(regime: str) -> float:
        """Konvertiert Market-Regime-String zu numerischem Feature."""
        return {
            "BULL": 1.0, "NEUTRAL": 0.5,
            "BEAR": 0.0, "CRISIS": -0.5,
        }.get(regime.upper(), 0.5)


# ── RLAgent ───────────────────────────────────────────────────────────────────

class RLAgent:
    """
    Epsilon-Greedy Linear Policy Agent für binäre Kauf-Entscheidungen.

    Kernidee: Ein Gewichtsvektor w (6 Dimensionen) lernt, welche
    Markt-Features positiv mit zukünftigen Returns korrelieren.
    expected_value = dot(w, state)
    Ist expected_value > 0 → kaufen. Sonst → nicht kaufen.

    Das Update folgt dem einfachen Policy-Gradient (REINFORCE):
      w += lr * reward * state
    Das verstärkt Features, die bei Gewinnen aktiv waren,
    und schwächt Features, die bei Verlusten aktiv waren.
    """

    def __init__(self) -> None:
        self._weights: List[float] = self._load_weights()
        self._epsilon: float = _EPSILON_INITIAL
        self._lr: float = _LEARNING_RATE
        self._min_trades_for_learning: int = _MIN_TRADES
        self._trade_count: int = 0
        self._reward_history: List[float] = []

    # ── Entscheidung ──────────────────────────────────────────────────────────

    def should_buy(self, state: RLState) -> Tuple[bool, float]:
        """
        Entscheidet ob gekauft werden soll und gibt den empfohlenen
        Position-Size-Modifier zurück.

        Returns:
            (kaufen: bool, modifier: float)
            modifier ist eine der Stufen: 0.5, 0.75, 1.0, 1.25

        Logik:
          - Epsilon-Greedy: mit Wahrscheinlichkeit epsilon → zufällige Entscheidung
          - Sonst: expected_value = dot(weights, state); kaufen wenn > threshold
          - Modifier wird proportional zum expected_value gewählt
        """
        # Fallback wenn numpy nicht verfügbar
        try:
            import numpy as np
        except ImportError:
            log.warning("RLAgent: numpy nicht verfügbar – Fallback (immer kaufen, modifier=1.0)")
            return True, 1.0

        try:
            state_arr = state.to_array()
            w = np.array(self._weights, dtype=float)

            # Epsilon-Greedy Exploration
            if random.random() < self._epsilon:
                buy = random.random() > 0.5
                modifier = random.choice(_MODIFIERS)
                log.debug("RLAgent: Exploration – buy=%s modifier=%.2f", buy, modifier)
                return buy, modifier

            # Policy: linearer Score
            expected_value = float(np.dot(w, state_arr))
            buy = expected_value > _BUY_THRESHOLD

            # Modifier: je stärker das Signal, desto größer die Position
            modifier = self._value_to_modifier(expected_value)

            log.debug(
                "RLAgent: expected_value=%.4f buy=%s modifier=%.2f",
                expected_value, buy, modifier,
            )
            return buy, modifier

        except Exception as exc:
            log.warning("RLAgent.should_buy Fehler: %s – Fallback", exc)
            return True, 1.0

    def _value_to_modifier(self, expected_value: float) -> float:
        """
        Mappt expected_value auf einen Position-Size-Modifier.
        Starkes positives Signal → größere Position.
        """
        if expected_value >= 0.5:
            return 1.25
        elif expected_value >= 0.2:
            return 1.0
        elif expected_value >= 0.0:
            return 0.75
        else:
            return 0.5

    # ── Lernen ────────────────────────────────────────────────────────────────

    def record_outcome(self, state: RLState, reward: float) -> None:
        """
        Policy-Gradient-Update nach einem abgeschlossenen Trade.

        Update-Regel: w += lr * reward * state
          - Positives reward → Gewichte in Richtung state verschieben
            (diese Features in ähnlicher Situation erneut bevorzugen)
          - Negatives reward → Gewichte weg von state verschieben
            (diese Features in ähnlicher Situation vermeiden)

        Args:
            state:  Marktbeschreibung zum Kaufzeitpunkt
            reward: Normalisierte Belohnung -1.0 bis +1.0
        """
        self._trade_count += 1
        self._reward_history.append(reward)

        # Erst nach _min_trades_for_learning aktiv lernen
        if self._trade_count < self._min_trades_for_learning:
            log.debug(
                "RLAgent: Trade %d/%d – noch kein Update (Warm-up)",
                self._trade_count, self._min_trades_for_learning,
            )
            self._save_weights()
            return

        # Nach 50 Trades: Epsilon reduzieren (mehr Exploitation)
        if self._trade_count == _EPSILON_THRESHOLD:
            self._epsilon = _EPSILON_REDUCED
            log.info("RLAgent: %d Trades erreicht – Epsilon → %.2f", _EPSILON_THRESHOLD, self._epsilon)

        try:
            import numpy as np
            state_arr = state.to_array()
            w = np.array(self._weights, dtype=float)

            # REINFORCE Policy Gradient Update
            delta = self._lr * reward * state_arr
            w_new = w + delta

            self._weights = w_new.tolist()
            log.info(
                "RL Update: reward=%+.4f, w_delta=%s",
                reward,
                [f"{d:+.5f}" for d in delta.tolist()],
            )

        except Exception as exc:
            log.warning("RLAgent.record_outcome Update-Fehler: %s", exc)

        self._save_weights()

    @staticmethod
    def compute_reward(
        return_pct: float,
        hold_days: int,
        planned_hold_days: int,
        stop_loss_triggered: bool,
    ) -> float:
        """
        Berechnet die normalisierte Belohnung aus Trade-Ergebnis.

        Formel:
          base    = return_pct / 10   (10% Return → reward=1.0)
          bonus   = +0.5× wenn Gewinn in < hold_days/2 erzielt
          malus   = -0.3× wenn Stop-Loss ausgelöst
          reward  = clip(base + bonus + malus, -1.0, 1.0)

        Args:
            return_pct:          Realisierter Return in %
            hold_days:           Tatsächliche Haltedauer in Tagen
            planned_hold_days:   Geplante Haltedauer aus Trade-Entry
            stop_loss_triggered: True wenn Exit durch Stop-Loss

        Returns:
            Normalisierte Belohnung im Bereich [-1.0, +1.0]
        """
        # Basis: Return normalisiert (10% = max reward)
        base = return_pct / 10.0

        # Bonus für schnelle Gewinne
        bonus = 0.0
        if return_pct > 0 and planned_hold_days > 0:
            if hold_days < planned_hold_days / 2:
                bonus = _FAST_WIN_BONUS * abs(base)

        # Malus für Stop-Loss-Auslösung
        malus = 0.0
        if stop_loss_triggered:
            malus = -_STOP_LOSS_MALUS * abs(base)

        # Normalisieren auf [-1.0, +1.0]
        reward = base + bonus + malus
        return max(-1.0, min(1.0, reward))

    # ── Erklärung & Statistik ─────────────────────────────────────────────────

    def explain(self, state: RLState) -> str:
        """
        Erklärt welche Features den Kauf (oder Nicht-Kauf) treiben.

        Berechnet den Beitrag jedes Features: contribution_i = w_i * state_i
        Zeigt die Top-Treiber und den Gesamtscore.

        Returns:
            Lesbarer String mit Feature-Beiträgen.
        """
        try:
            import numpy as np
            state_arr = state.to_array()
            w = np.array(self._weights, dtype=float)
            contributions = (w * state_arr).tolist()
            expected_value = float(np.dot(w, state_arr))

            lines = [f"RL Explain | expected_value={expected_value:+.4f} | buy={expected_value > _BUY_THRESHOLD}"]
            sorted_features = sorted(
                zip(_FEATURE_NAMES, contributions, state_arr.tolist()),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            for name, contrib, feat_val in sorted_features:
                direction = "↑ bullish" if contrib > 0 else "↓ bearish"
                lines.append(f"  {name:<22} val={feat_val:.3f}  contrib={contrib:+.4f}  {direction}")
            return "\n".join(lines)

        except Exception as exc:
            return f"RLAgent.explain Fehler: {exc}"

    def get_stats(self) -> Dict:
        """
        Gibt Lernstatistiken zurück.

        Returns:
            dict mit: trades, avg_reward, epsilon, weights,
                      top_features (nach absolutem Gewicht sortiert)
        """
        avg_reward = (
            sum(self._reward_history) / len(self._reward_history)
            if self._reward_history else 0.0
        )
        top_features = sorted(
            zip(_FEATURE_NAMES, self._weights),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        return {
            "trades":       self._trade_count,
            "avg_reward":   round(avg_reward, 4),
            "epsilon":      self._epsilon,
            "weights":      dict(zip(_FEATURE_NAMES, self._weights)),
            "top_features": [(name, round(w, 5)) for name, w in top_features],
            "learning_active": self._trade_count >= self._min_trades_for_learning,
        }

    def train_on_history(self, journal_path: str) -> int:
        """
        Offline-Training auf historischen Trade-Daten aus dem Trade-Journal (DB).

        Liest alle abgeschlossenen Trades (ENTRY + EXIT Paare) aus der SQLite-DB,
        konvertiert sie in (state, reward) Tupel und führt Policy-Gradient-Updates durch.

        Args:
            journal_path: Pfad zur trade_journal.db

        Returns:
            Anzahl verarbeiteter Trades
        """
        import sqlite3

        trained = 0
        try:
            conn = sqlite3.connect(journal_path)
            conn.row_factory = sqlite3.Row

            # Alle ENTRY-Events laden
            entries = {
                row["ticker"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM trade_events WHERE event_type='ENTRY' ORDER BY event_date ASC"
                ).fetchall()
            }

            # Alle EXIT-Events laden
            exits = conn.execute(
                "SELECT * FROM trade_events WHERE event_type='EXIT' ORDER BY event_date ASC"
            ).fetchall()

            conn.close()

            for exit_row in exits:
                ticker = exit_row["ticker"]
                entry = entries.get(ticker)
                if not entry:
                    continue

                # State aus Entry-Daten rekonstruieren
                state = self._state_from_entry(entry)

                # Reward aus Exit-Daten berechnen
                pnl_pct          = float(exit_row["pnl_pct"] or 0.0)
                hold_days        = int(exit_row["hold_days"] or 0)
                planned_hold     = int(entry.get("hold_days") or 14)
                stop_triggered   = "stop" in str(exit_row["reason"] or "").lower()

                reward = self.compute_reward(pnl_pct, hold_days, planned_hold, stop_triggered)
                self.record_outcome(state, reward)
                trained += 1

            log.info("RLAgent.train_on_history: %d Trades verarbeitet", trained)

        except Exception as exc:
            log.warning("RLAgent.train_on_history Fehler: %s", exc)

        return trained

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _state_from_entry(self, entry: Dict) -> RLState:
        """Rekonstruiert RLState aus Journal-Entry. Fehlende Felder = neutrale Defaults."""
        sentiment  = float(entry.get("sentiment") or 0.5)
        confidence = str(entry.get("confidence") or "MEDIUM")
        direction  = str(entry.get("direction") or "NEUTRAL").lower()
        regime     = "BULL" if "bull" in direction else ("BEAR" if "bear" in direction else "NEUTRAL")
        return RLState(
            sentiment_score=    max(0.0, min(1.0, sentiment)),
            vix_level=          0.3,   # neutraler Default (~VIX 15)
            momentum_5d=        0.0,   # kein Momentum im Journal
            news_velocity=      0.5,   # mittlere News-Aktivität
            confidence_encoded= RLState.encode_confidence(confidence),
            regime_encoded=     RLState.encode_regime(regime),
        )

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load_weights(self) -> List[float]:
        """
        Lädt Gewichte aus data/rl_weights.json.
        Initialisiert mit kleinen Zufallswerten falls Datei nicht existiert.
        """
        try:
            with open(_WEIGHTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            weights = data.get("weights", [])
            if len(weights) == _N_FEATURES:
                self._trade_count    = data.get("trade_count", 0)
                self._reward_history = data.get("reward_history", [])[-200:]  # max 200 behalten
                # Epsilon ggf. bereits reduziert
                if self._trade_count >= _EPSILON_THRESHOLD:
                    self._epsilon = _EPSILON_REDUCED
                log.info("RLAgent: Gewichte geladen (trade_count=%d)", self._trade_count)
                return weights
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("RLAgent: Gewichte-Ladefehler – %s", exc)

        # Initialisierung mit kleinen Zufallswerten (leichter Optimismus)
        log.info("RLAgent: Neue Gewichte initialisiert (kein gespeicherter State)")
        return [random.uniform(0.0, 0.1) for _ in range(_N_FEATURES)]

    def _save_weights(self) -> None:
        """
        Speichert Gewichte atomic mit tempfile + os.replace.
        Verhindert korrupte Dateien bei Programmabbruch.
        """
        try:
            data_dir = os.path.dirname(_WEIGHTS_FILE)
            os.makedirs(data_dir, exist_ok=True)

            payload = {
                "weights":        self._weights,
                "trade_count":    self._trade_count,
                "epsilon":        self._epsilon,
                "reward_history": self._reward_history[-200:],
                "feature_names":  _FEATURE_NAMES,
            }

            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=data_dir,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name

            os.replace(tmp_path, _WEIGHTS_FILE)

        except Exception as exc:
            log.warning("RLAgent: Gewichte-Speicherfehler – %s", exc)
