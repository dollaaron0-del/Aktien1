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
from datetime import datetime, timezone
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


# ── Persönliche Bestleistungen ─────────────────────────────────────────────────

@dataclass
class PersonalRecord:
    value:     float
    date:      str
    ticker:    str = ""   # bei Trade-Rekorden
    times_beaten: int = 0


@dataclass
class PersonalBests:
    """Verfolgt die eigenen Rekorde des Bots – Wettbewerb gegen sich selbst."""
    best_win_rate_20:    Optional[PersonalRecord] = None   # Beste Win-Rate (rollend 20)
    best_avg_return_20:  Optional[PersonalRecord] = None   # Beste Ø-Rendite (rollend 20)
    best_streak:         Optional[PersonalRecord] = None   # Längste Gewinnserie
    best_single_trade:   Optional[PersonalRecord] = None   # Bester Einzel-Trade (%)
    best_score_velocity: Optional[PersonalRecord] = None   # Stärkster Score-Anstieg (10 Trades)


def _check_record(
    current_val: float,
    record: Optional[PersonalRecord],
    ticker: str = "",
) -> Tuple[bool, float, Optional[PersonalRecord]]:
    """
    Vergleicht Wert mit bestehendem Rekord.
    Gibt (is_new_record, improvement_pct, updated_record) zurück.
    """
    today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    if record is None or current_val > record.value:
        improvement = (
            (current_val - record.value) / max(abs(record.value), 0.001) * 100
            if record else 0.0
        )
        new_rec = PersonalRecord(
            value=current_val,
            date=today,
            ticker=ticker,
            times_beaten=(record.times_beaten + 1) if record else 0,
        )
        return True, improvement, new_rec
    return False, 0.0, record


def _near_record(current_val: float, record: Optional[PersonalRecord], margin: float = 0.05) -> bool:
    """True wenn aktueller Wert innerhalb von `margin` Prozent des Rekords liegt."""
    if record is None or current_val <= 0:
        return False
    return current_val >= record.value * (1 - margin)


def _record_bonus(is_new: bool, improvement_pct: float, near: bool) -> Tuple[float, str]:
    """Berechnet Bonus-Punkte für Rekord-Ereignisse."""
    if is_new:
        base = 3.0
        if improvement_pct > 25:
            base += 3.0
        elif improvement_pct > 10:
            base += 1.5
        return base, f"🏆 Neuer Rekord! (+{improvement_pct:.0f}%)"
    if near:
        return 0.5, "📈 Nahe am Rekord"
    return 0.0, ""


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
    milestones:       List[int]         = field(default_factory=list)
    history:          List[ScoreEntry]  = field(default_factory=list)
    personal_bests:   PersonalBests     = field(default_factory=PersonalBests)

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
            f"  Kaufschwelle:    {thr_sign}",
            f"  Max. Positionen: {pos_sign}",
            f"  Positionsgröße:  {size_pct}",
            f"  Haltedauer:      {(mod.hold_days_mult - 1)*100:+.0f}%",
        ]
        # Persönliche Bestleistungen
        pb = self.personal_bests
        lines += ["", "Persönliche Rekorde:"]
        def _pr(label, rec):
            if rec is None:
                return f"  {label:<22} –"
            return f"  {label:<22} {rec.value:.1f}  ({rec.date}  ×{rec.times_beaten} gebrochen)"
        lines.append(_pr("Win-Rate 20 Trades:",    pb.best_win_rate_20))
        lines.append(_pr("Ø-Rendite 20 Trades:",   pb.best_avg_return_20))
        lines.append(_pr("Gewinnserie:",            pb.best_streak))
        lines.append(_pr("Bester Einzel-Trade %:",  pb.best_single_trade))
        lines.append(_pr("Score-Anstieg (10 Tr.):", pb.best_score_velocity))

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
        exit_reason: str,
        current_tier: int = 0,
        tracker=None,            # PerformanceTracker für rollende Metriken
    ) -> Tuple[float, List[int], List[str]]:
        """
        Wertet einen Trade aus.
        Gibt (delta, neue_meilensteine, rekord_nachrichten) zurück.
        """
        base   = _base_points(return_pct, confidence, exit_reason)
        mult   = _TIER_MULTIPLIER.get(current_tier, 1.0)
        delta  = round(base * mult, 2)

        # ── Persönliche Rekorde prüfen ─────────────────────────────────────
        record_msgs: List[str] = []
        record_bonus = 0.0
        pb = self._state.personal_bests

        # 1. Bester Einzel-Trade
        if return_pct > 0:
            old_best = pb.best_single_trade
            is_new, imp, pb.best_single_trade = _check_record(return_pct, old_best, ticker)
            bonus, msg = _record_bonus(is_new, imp, _near_record(return_pct, old_best))
            if msg:
                record_bonus += bonus
                record_msgs.append(f"Bester Trade: {msg} {return_pct:.1f}%")

        # 2. Rollende Metriken (nur wenn Tracker vorhanden)
        if tracker:
            recent = tracker.get_recent_trades(n=20)
            if len(recent) >= 5:
                # Win-Rate letzte 20
                wr = _win_rate_from_list(recent) * 100
                old_wr = pb.best_win_rate_20
                is_new, imp, pb.best_win_rate_20 = _check_record(wr, old_wr)
                bonus, msg = _record_bonus(is_new, imp, _near_record(wr, old_wr))
                if msg:
                    record_bonus += bonus
                    record_msgs.append(f"Win-Rate: {msg} {wr:.0f}%")

                # Ø-Rendite letzte 20
                avg_r = sum(t.get("actual_return_pct") or 0 for t in recent) / len(recent)
                if avg_r > 0:
                    old_avg = pb.best_avg_return_20
                    is_new, imp, pb.best_avg_return_20 = _check_record(avg_r, old_avg)
                    bonus, msg = _record_bonus(is_new, imp, _near_record(avg_r, old_avg))
                    if msg:
                        record_bonus += bonus
                        record_msgs.append(f"Ø-Rendite: {msg} {avg_r:.1f}%")

            # Gewinnserie
            streak = _current_streak(tracker.get_recent_trades(n=50))
            if streak > 0:
                old_streak = pb.best_streak
                is_new, imp, pb.best_streak = _check_record(float(streak), old_streak)
                bonus, msg = _record_bonus(is_new, imp, _near_record(streak, old_streak))
                if msg:
                    record_bonus += bonus
                    record_msgs.append(f"Gewinnserie: {msg} {streak} Trades")

        # 3. Score-Velocity (Anstieg über letzte 10 Scores)
        if len(self._state.history) >= 10:
            old_s = self._state.history[-10].score_after
            velocity = self._state.current - old_s + delta   # inkl. aktueller Trade
            if velocity > 0:
                old_vel = pb.best_score_velocity
                is_new, imp, pb.best_score_velocity = _check_record(velocity, old_vel)
                bonus, msg = _record_bonus(is_new, imp, _near_record(velocity, old_vel))
                if msg:
                    record_bonus += bonus
                    record_msgs.append(f"Score-Anstieg: {msg} +{velocity:.1f} Pkt")

        # Rekord-Bonus addieren (nicht durch Tier multipliziert – fair bleiben)
        total_delta = round(delta + record_bonus, 2)
        reason = _build_reason(return_pct, confidence, exit_reason, base, mult, record_bonus)

        old_score = self._state.current
        new_score = max(0.0, min(100.0, old_score + total_delta))

        self._state.current       = new_score
        self._state.peak          = max(self._state.peak, new_score)
        self._state.trades_scored += 1
        self._state.personal_bests = pb

        if total_delta >= 0:
            self._state.total_earned = round(self._state.total_earned + total_delta, 2)
        else:
            self._state.total_lost   = round(self._state.total_lost   - total_delta, 2)

        entry = ScoreEntry(
            date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat()[:16],
            ticker=ticker,
            delta=total_delta,
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
        log.info(
            "BotScorer: %s  %+.1f Pkt (×%.2f, Rekord +%.1f) → %.1f/100",
            ticker, delta, mult, record_bonus, new_score,
        )
        return total_delta, new_milestones, record_msgs

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

    def to_telegram_record(self, msgs: List[str]) -> str:
        lines = ["🏆 *Persönlicher Rekord!*", ""]
        lines += [f"  • {m}" for m in msgs]
        lines += ["", f"Score: *{self._state.current:.1f}/100*"]
        return "\n".join(lines)

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> BotScore:
        try:
            with open(_FILE) as f:
                data = json.load(f)
            history = [ScoreEntry(**e) for e in data.pop("history", [])]
            # PersonalBests deserialisieren
            pb_raw  = data.pop("personal_bests", {})
            pb      = PersonalBests()
            for field_name in ("best_win_rate_20", "best_avg_return_20",
                               "best_streak", "best_single_trade", "best_score_velocity"):
                raw = pb_raw.get(field_name)
                if raw:
                    setattr(pb, field_name, PersonalRecord(**raw))
            state   = BotScore(**{k: v for k, v in data.items()
                                  if k not in ("history", "personal_bests")})
            state.history       = history
            state.personal_bests = pb
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
                  base: float, mult: float, record_bonus: float = 0.0) -> str:
    exit_map = {
        "take_profit":    "TP erreicht",
        "stop_loss":      "SL getroffen",
        "thesis_broken":  "These gebrochen",
        "hold_expired":   "Haltedauer abgelaufen",
        "sentiment_sell": "Sentiment gedreht",
    }
    exit_label = exit_map.get(exit_reason, exit_reason or "manuell")
    rec_str = f" + Rekord +{record_bonus:.1f}pt" if record_bonus > 0 else ""
    return (
        f"{'+' if return_pct >= 0 else ''}{return_pct:.1f}% | "
        f"{confidence} | {exit_label} | "
        f"Basis {base:+.1f}pt × {mult:.2f}{rec_str}"
    )


def _win_rate_from_list(trades: List[Dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if (t.get("actual_return_pct") or 0) > 0)
    return wins / len(trades)


def _current_streak(trades: List[Dict]) -> int:
    """Aktuelle laufende Gewinnserie vom neuesten Trade."""
    streak = 0
    for t in trades:
        if (t.get("actual_return_pct") or 0) > 0:
            streak += 1
        else:
            break
    return streak
