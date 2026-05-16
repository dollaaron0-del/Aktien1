"""
Bot-Punktesystem (0–100)

Jeder abgeschlossene Trade gibt Punkte oder zieht sie ab.
Je höher der Margin-Tier, desto stärker werden Punkte multipliziert –
gute Trades zählen mehr, schlechte aber auch.

Der Score verändert das VERHALTEN des Bots direkt:
  Niedriger Score → Bot wird eingeschränkt (Karotte entzogen)
  Hoher Score     → Bot bekommt mehr Freiheiten (Karotte näher)

Score-Stufen und ihre Auswirkungen:
   0–24  Eingeschränkt:  Kaufschwelle +0.05, Positionen −2, Größe −30%
  25–39  Lernend:        Kaufschwelle +0.03, Positionen −1, Größe −15%
  40–59  Standard:       Keine Änderung (Baseline)
  60–74  Stark:          Kaufschwelle −0.02, Positionen +1, Größe +10%
  75–89  Exzellent:      Kaufschwelle −0.04, Positionen +2, Größe +25%
  90–100 Elite:          Kaufschwelle −0.06, Positionen +3, Größe +40%

Persistenz: data/bot_score.json
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "bot_score.json")

# Tier → Punktemultiplikator
_TIER_MULTIPLIER = {0: 1.0, 1: 1.1, 2: 1.25, 3: 1.5, 4: 2.0}

# Meilensteine: Score → (Label, Beschreibung, Belohnung als .env-Hinweis)
MILESTONES: Dict[int, Tuple[str, str, str]] = {
    10:  ("🌱 Lernphase",   "Erste erfolgreiche Trades abgeschlossen",
          ""),
    25:  ("📈 Solide",      "Bot handelt zuverlässig",
          "Empfehlung: AUTO_SCAN_WATCHLIST=true freischalten"),
    40:  ("💪 Gut",         "Überdurchschnittliche Performance",
          "Empfehlung: USE_KELLY_SIZING=true für optimale Positionsgrößen"),
    60:  ("🚀 Stark",       "Starke, konstante Performance",
          "Empfehlung: USE_MARGIN=true – Bot ist Tier-1-Kandidat"),
    75:  ("⭐ Exzellent",   "Top 25% Performance",
          "Empfehlung: ENABLE_SOCIAL_SCAN=true für mehr Signale"),
    90:  ("🏆 Elite",       "Außergewöhnliche Performance",
          "Empfehlung: SCAN_MAX_PICKS=5 – mehr Chancen nutzen"),
   100:  ("👑 Meister",     "Maximale Performance – alle Systeme optimal",
          ""),
}


@dataclass(frozen=True)
class ScoreModifier:
    """Verhaltensänderungen basierend auf aktuellem Score."""
    score_range:       str    # Label der Stufe
    threshold_adj:     float  # Kaufschwellen-Anpassung (±)
    position_count_adj: int   # Zusätzliche/weniger Positionen erlaubt
    position_size_mult: float # Multiplikator auf Positionsgröße (1.0 = keine Änderung)
    hold_days_mult:    float  # Haltedauer-Multiplikator
    description:       str


# Score-Stufen → Verhaltensänderungen
_SCORE_LEVELS: list[tuple[int, ScoreModifier]] = [
    (90, ScoreModifier("Elite",          -0.06, +3, 1.40, 1.20,
                       "Maximale Freiheit: mehr Positionen, größere Größen, niedrigere Schwelle")),
    (75, ScoreModifier("Exzellent",      -0.04, +2, 1.25, 1.10,
                       "Erweiterter Spielraum: mehr Signale erlaubt")),
    (60, ScoreModifier("Stark",          -0.02, +1, 1.10, 1.05,
                       "Leicht mehr Spielraum: eine zusätzliche Position")),
    (40, ScoreModifier("Standard",        0.00,  0, 1.00, 1.00,
                       "Normale Parameter – Baseline")),
    (25, ScoreModifier("Lernend",        +0.03, -1, 0.85, 0.90,
                       "Leicht eingeschränkt: etwas konservativer")),
    ( 0, ScoreModifier("Eingeschränkt",  +0.05, -2, 0.70, 0.80,
                       "Stark eingeschränkt: Bot muss erst wieder Vertrauen aufbauen")),
]


def get_modifiers(score: float) -> ScoreModifier:
    """Gibt die aktiven Verhaltens-Modifier für einen gegebenen Score zurück."""
    for threshold, mod in _SCORE_LEVELS:
        if score >= threshold:
            return mod
    return _SCORE_LEVELS[-1][1]


@dataclass
class ScoreEntry:
    date:        str
    ticker:      str
    delta:       float       # Punkte-Änderung (positiv oder negativ)
    score_after: float
    tier:        int
    multiplier:  float
    reason:      str         # kurze Erklärung


@dataclass
class BotScore:
    current:          float = 50.0
    peak:             float = 50.0
    trades_scored:    int   = 0
    total_earned:     float = 0.0
    total_lost:       float = 0.0
    milestones:       List[int]       = field(default_factory=list)
    history:          List[ScoreEntry] = field(default_factory=list)

    @property
    def label(self) -> str:
        for threshold in sorted(MILESTONES.keys(), reverse=True):
            if self.current >= threshold:
                return MILESTONES[threshold][0]
        return "⚪ Neuling"

    @property
    def bar(self) -> str:
        filled = round(self.current / 10)
        return "█" * filled + "░" * (10 - filled)

    def to_text(self) -> str:
        mod = get_modifiers(self.current)
        thr_sign = f"{mod.threshold_adj:+.2f}" if mod.threshold_adj != 0 else "±0.00"
        pos_sign = f"{mod.position_count_adj:+d}" if mod.position_count_adj != 0 else "±0"
        size_pct = f"{(mod.position_size_mult - 1) * 100:+.0f}%"
        lines = [
            "=== BOT-SCORE ===",
            f"Score:   {self.current:.1f}/100  {self.bar}",
            f"Status:  {self.label}",
            f"Peak:    {self.peak:.1f}  |  Trades: {self.trades_scored}",
            f"Verdient: +{self.total_earned:.1f} Pkt  |  Verloren: -{self.total_lost:.1f} Pkt",
            "",
            f"Aktive Verhaltens-Modifier ({mod.score_range}):",
            f"  Kaufschwelle:    {thr_sign}  (niedriger = mehr Signale erlaubt)",
            f"  Max. Positionen: {pos_sign}",
            f"  Positionsgröße:  {size_pct}",
            f"  Haltedauer:      {(mod.hold_days_mult - 1)*100:+.0f}%",
            f"  → {mod.description}",
        ]
        if self.history:
            lines += ["", "Letzte 10 Trades:"]
            for e in self.history[-10:]:
                sign = "+" if e.delta >= 0 else ""
                lines.append(
                    f"  {e.date[:10]}  {e.ticker:<6}  "
                    f"{sign}{e.delta:.1f} Pkt (×{e.multiplier:.2f})  →  {e.score_after:.1f}  | {e.reason}"
                )
        reached = [MILESTONES[m][0] for m in sorted(self.milestones)]
        if reached:
            lines += ["", f"Meilensteine: {', '.join(reached)}"]
        lines.append("=" * 20)
        return "\n".join(lines)


class BotScorer:
    """Verwaltet den Bot-Score. Nach jedem Trade aufrufen."""

    def __init__(self):
        self._state = self._load()

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def record_trade(
        self,
        ticker: str,
        return_pct: float,
        confidence: str,
        exit_reason: str,        # "stop_loss" | "take_profit" | "thesis_broken" | ...
        current_tier: int = 0,
    ) -> Tuple[float, List[int]]:
        """
        Wertet einen Trade aus.
        Gibt (delta, neu_erreichte_meilensteine) zurück.
        """
        base   = _base_points(return_pct, confidence, exit_reason)
        mult   = _TIER_MULTIPLIER.get(current_tier, 1.0)
        delta  = round(base * mult, 2)
        reason = _build_reason(return_pct, confidence, exit_reason, base, mult)

        old_score = self._state.current
        new_score = max(0.0, min(100.0, old_score + delta))

        self._state.current       = new_score
        self._state.peak          = max(self._state.peak, new_score)
        self._state.trades_scored += 1

        if delta >= 0:
            self._state.total_earned = round(self._state.total_earned + delta, 2)
        else:
            self._state.total_lost   = round(self._state.total_lost   - delta, 2)

        entry = ScoreEntry(
            date=datetime.utcnow().isoformat()[:16],
            ticker=ticker,
            delta=delta,
            score_after=new_score,
            tier=current_tier,
            multiplier=mult,
            reason=reason,
        )
        self._state.history.append(entry)
        if len(self._state.history) > 200:
            self._state.history = self._state.history[-200:]

        # Meilensteine prüfen
        new_milestones = []
        for threshold in MILESTONES:
            if threshold not in self._state.milestones and new_score >= threshold:
                self._state.milestones.append(threshold)
                new_milestones.append(threshold)

        self._save()
        log.info("BotScorer: %s  %+.1f Pkt (×%.2f) → %.1f/100", ticker, delta, mult, new_score)
        return delta, new_milestones

    def get(self) -> BotScore:
        return self._state

    def to_telegram_milestone(self, threshold: int) -> str:
        label, desc, reward = MILESTONES[threshold]
        lines = [
            f"{label} *Score-Meilenstein {threshold} erreicht!*",
            f"_{desc}_",
            f"Aktueller Score: *{self._state.current:.1f}/100*",
        ]
        if reward:
            lines += ["", f"💡 {reward}"]
        return "\n".join(lines)

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> BotScore:
        try:
            with open(_FILE) as f:
                data = json.load(f)
            history = [ScoreEntry(**e) for e in data.pop("history", [])]
            state   = BotScore(**{k: v for k, v in data.items() if k != "history"})
            state.history = history
            return state
        except Exception:
            return BotScore()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            payload = asdict(self._state)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _FILE)
        except Exception as e:
            log.warning("BotScorer: Speicherfehler – %s", e)


# ── Punkt-Berechnung ──────────────────────────────────────────────────────────

def _base_points(return_pct: float, confidence: str, exit_reason: str) -> float:
    """Rohe Punkte vor Tier-Multiplikator."""
    if return_pct > 0:
        # Gewinn: logarithmische Skala – 2% = ~1.5pt, 10% = ~5pt, 20% = ~8pt
        import math
        base = min(10.0, 2.5 * math.log1p(return_pct / 3))

        # Boni
        if exit_reason == "take_profit":
            base += 2.0                          # Ziel erreicht
        if confidence == "HIGH":
            base += 1.0                          # HIGH-Konfidenz-Treffer
        elif confidence == "LOW":
            base -= 0.5                          # LOW-Konfidenz-Glückstreffer

    else:
        # Verlust: lineare Skala – 3% = -2pt, 7% = -4pt, 15% = -7pt
        loss = abs(return_pct)
        base = -min(10.0, 1.0 + loss * 0.45)

        # Abzüge
        if exit_reason == "stop_loss":
            base -= 1.0                          # SL getroffen
        if confidence == "HIGH":
            base -= 0.5                          # HIGH-Konfidenz-Fehler wiegt schwerer

    return round(base, 2)


def _build_reason(return_pct: float, confidence: str, exit_reason: str,
                  base: float, mult: float) -> str:
    exit_map = {
        "take_profit":    "TP erreicht",
        "stop_loss":      "SL getroffen",
        "thesis_broken":  "These gebrochen",
        "hold_expired":   "Haltedauer abgelaufen",
        "sentiment_sell": "Sentiment gedreht",
    }
    exit_label = exit_map.get(exit_reason, exit_reason or "manuell")
    return (
        f"{'+' if return_pct >= 0 else ''}{return_pct:.1f}% | "
        f"{confidence} | {exit_label} | "
        f"Basis {base:+.1f}pt × {mult:.2f}"
    )
