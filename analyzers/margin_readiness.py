"""
Progressives Margin-Tier-System

Der Bot verdient sich höhere Hebel durch konstante Performance.
Schlechte Phasen führen zur automatischen Rückstufung.

Tiers (USE_MARGIN=true vorausgesetzt):
  Tier 0 – Kein Hebel   (1.00×) – Voraussetzungen nicht erfüllt
  Tier 1 – Einsteiger   (1.25×) – 20 Trades, 60% Win-Rate
  Tier 2 – Standard     (1.50×) – 35 Trades, 63% Win-Rate
  Tier 3 – Fortgeschr.  (1.75×) – 55 Trades, 67% Win-Rate
  Tier 4 – Experte      (2.00×) – 80 Trades, 72% Win-Rate

Auto-Downgrade:
  - Win-Rate letzte 10 Trades fällt >15% unter Tier-Minimum → ein Tier runter
  - 4+ aufeinanderfolgende Verluste → sofort Tier 0 (Pause)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

# ── Tier-Definitionen ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TierSpec:
    level:          int
    factor:         float
    label:          str
    min_trades:     int
    min_win_rate:   float   # letzte 20 Trades
    min_avg_return: float   # % pro Trade (alle Trades)
    max_consec_loss: int
    max_sl_rate:    float   # SL-Treffer / alle Trades

_TIERS: List[TierSpec] = [
    TierSpec(0, 1.00, "Kein Hebel",    0,  0.00, 0.0, 99, 1.00),  # immer erreichbar
    TierSpec(1, 1.25, "Einsteiger",   20,  0.60, 2.5,  3, 0.35),
    TierSpec(2, 1.50, "Standard",     35,  0.63, 3.5,  3, 0.30),
    TierSpec(3, 1.75, "Fortgeschr.",  55,  0.67, 4.5,  2, 0.25),
    TierSpec(4, 2.00, "Experte",      80,  0.72, 6.0,  2, 0.20),
]

# Downgrade-Auslöser: Win-Rate letzte 10 Trades fällt um diesen Wert unter Tier-Min
_DOWNGRADE_WR_DROP   = 0.15
_EMERGENCY_LOSS_STREAK = 4   # aufeinanderfolgende Verluste → sofort Tier 0

# Cache: Tier-Berechnung maximal alle 60 Minuten neu
_CACHE_TTL = 3600
_cache: Dict = {}


@dataclass
class TierProgress:
    """Fortschritt zum nächsten Tier."""
    criterion:  str
    current:    str
    needed:     str
    done:       bool


@dataclass
class MarginTierResult:
    active_tier:        TierSpec
    next_tier:          Optional[TierSpec]
    downgrade_reason:   str                       # "" = kein Downgrade aktiv
    progress:           List[TierProgress]        # Kriterien zum nächsten Tier
    all_tiers_status:   List[Tuple[TierSpec, bool]]  # (tier, unlocked)
    analyzed_at:        str
    total_trades:       int
    recent_win_rate:    float    # letzte 20 Trades
    avg_return:         float
    consec_losses:      int
    sl_rate:            float

    @property
    def factor(self) -> float:
        return self.active_tier.factor

    @property
    def at_max(self) -> bool:
        return self.active_tier.level == _TIERS[-1].level

    def to_text(self) -> str:
        tier_icons = {0: "⛔", 1: "🟢", 2: "🔵", 3: "🟣", 4: "🏆"}
        lines = [
            "=== PROGRESSIVER HEBEL-TRACKER ===",
            "",
            f"Aktiver Tier:  {tier_icons.get(self.active_tier.level,'?')} "
            f"Tier {self.active_tier.level} – {self.active_tier.label} "
            f"({self.active_tier.factor:.2f}×)",
        ]

        if self.downgrade_reason:
            lines.append(f"⚠  Rückstufung aktiv: {self.downgrade_reason}")

        lines += [
            "",
            "Tier-Übersicht:",
        ]
        for spec, unlocked in self.all_tiers_status:
            if spec.level == 0:
                continue
            icon  = tier_icons.get(spec.level, "?")
            check = "✓" if unlocked else "✗"
            active_marker = " ◄ AKTIV" if spec.level == self.active_tier.level else ""
            lines.append(
                f"  [{check}] {icon} Tier {spec.level} ({spec.factor:.2f}×) – "
                f"{spec.label}{active_marker}"
            )

        lines += ["", f"Aktuelle Performance:"]
        lines.append(f"  Trades gesamt:     {self.total_trades}")
        lines.append(f"  Win-Rate (letzte 20): {self.recent_win_rate*100:.0f}%")
        lines.append(f"  Ø-Rendite/Trade:   {self.avg_return:+.2f}%")
        lines.append(f"  Aktuelle Verlustserie: {self.consec_losses}")
        lines.append(f"  Stop-Loss-Rate:    {self.sl_rate*100:.0f}%")

        if self.next_tier and not self.at_max:
            lines += ["", f"Nächstes Tier: {self.next_tier.label} ({self.next_tier.factor:.2f}×)"]
            for p in self.progress:
                tick = "✓" if p.done else "✗"
                lines.append(f"  [{tick}] {p.criterion}: {p.current} → Ziel {p.needed}")

        lines.append("\n" + "=" * 35)
        return "\n".join(lines)

    def to_telegram(self, prev_level: int = -1) -> str:
        tier_icons = {0: "⛔", 1: "🟢", 2: "🔵", 3: "🟣", 4: "🏆"}
        icon = tier_icons.get(self.active_tier.level, "?")

        if prev_level >= 0 and self.active_tier.level > prev_level:
            header = f"🎉 *Hebel hochgestuft!* Tier {self.active_tier.level} freigeschaltet!"
        elif prev_level >= 0 and self.active_tier.level < prev_level:
            header = f"⚠️ *Hebel reduziert* auf Tier {self.active_tier.level}"
        else:
            header = f"{icon} *Margin-Status: Tier {self.active_tier.level}*"

        lines = [
            header,
            f"Hebel: *{self.active_tier.factor:.2f}×* – {self.active_tier.label}",
            "",
            f"Win-Rate (letzte 20): {self.recent_win_rate*100:.0f}%",
            f"Ø-Rendite/Trade: {self.avg_return:+.2f}%",
        ]
        if self.downgrade_reason:
            lines.append(f"⚠ {self.downgrade_reason}")
        if self.next_tier and not self.at_max:
            lines.append(f"\nNächstes Tier: {self.next_tier.factor:.2f}× bei {self.next_tier.min_win_rate*100:.0f}% Win-Rate")
        return "\n".join(lines)


class MarginTierTracker:
    """Berechnet den aktuell gültigen Margin-Tier basierend auf Trade-History."""

    def __init__(self, tracker):
        self._tracker = tracker

    def get_active_tier(self, use_cache: bool = True) -> MarginTierResult:
        now = time.monotonic()
        if use_cache and "result" in _cache and now - _cache.get("ts", 0) < _CACHE_TTL:
            return _cache["result"]

        result = self._compute()
        _cache["result"] = result
        _cache["ts"]     = now
        return result

    def invalidate_cache(self) -> None:
        _cache.clear()

    def _compute(self) -> MarginTierResult:
        report     = self._tracker.get_accuracy_report()
        total      = report.get("total_closed", 0)
        avg_return = report.get("avg_return_pct", 0.0)
        recent20   = self._tracker.get_recent_trades(n=20)
        recent10   = self._tracker.get_recent_trades(n=10)
        exit_stats = {r["category"]: r for r in self._tracker.get_exit_reason_stats()}

        # Win-Rates
        recent_wr20 = _win_rate(recent20)
        recent_wr10 = _win_rate(recent10)

        # SL-Rate (gesamt)
        sl_trades = exit_stats.get("stop_loss", {}).get("trades", 0)
        sl_rate   = sl_trades / max(total, 1)

        # Aufeinanderfolgende Verluste (letzte 20)
        consec = _max_consecutive_losses(recent20)

        # ── Emergency-Downgrade: 4+ Verluste in Folge ────────────────────────
        downgrade_reason = ""
        emergency = _current_consecutive_losses(recent20) >= _EMERGENCY_LOSS_STREAK
        if emergency:
            downgrade_reason = (
                f"{_current_consecutive_losses(recent20)} aufeinanderfolgende Verluste – "
                f"Hebel temporär deaktiviert."
            )

        # ── Höchsten erfüllten Tier ermitteln ─────────────────────────────────
        unlocked = [False] * len(_TIERS)
        unlocked[0] = True  # Tier 0 immer

        for spec in _TIERS[1:]:
            wr_ok  = recent_wr20    >= spec.min_win_rate
            ret_ok = avg_return     >= spec.min_avg_return
            tr_ok  = total          >= spec.min_trades
            cl_ok  = consec         <= spec.max_consec_loss
            sl_ok  = sl_rate        <= spec.max_sl_rate
            unlocked[spec.level] = wr_ok and ret_ok and tr_ok and cl_ok and sl_ok

        # Höchsten freigeschalteten Tier finden
        earned_level = max(i for i, u in enumerate(unlocked) if u)

        # ── Win-Rate-Downgrade: letzte 10 Trades viel schlechter ─────────────
        if earned_level > 0 and not emergency:
            current_spec = _TIERS[earned_level]
            if recent_wr10 < current_spec.min_win_rate - _DOWNGRADE_WR_DROP:
                earned_level = max(0, earned_level - 1)
                downgrade_reason = (
                    f"Win-Rate letzte 10 Trades ({recent_wr10*100:.0f}%) unter "
                    f"Tier-Minimum – temporär zurückgestuft."
                )

        if emergency:
            earned_level = 0

        active_tier = _TIERS[earned_level]
        next_tier   = _TIERS[earned_level + 1] if earned_level < len(_TIERS) - 1 else None

        # ── Fortschritt zum nächsten Tier ─────────────────────────────────────
        progress: List[TierProgress] = []
        if next_tier:
            progress = [
                TierProgress("Trades",       str(total),                   f">= {next_tier.min_trades}",       total >= next_tier.min_trades),
                TierProgress("Win-Rate",      f"{recent_wr20*100:.0f}%",   f">= {next_tier.min_win_rate*100:.0f}%", recent_wr20 >= next_tier.min_win_rate),
                TierProgress("Ø-Rendite",     f"{avg_return:+.2f}%",       f">= {next_tier.min_avg_return:.1f}%",   avg_return  >= next_tier.min_avg_return),
                TierProgress("Verlustserie",  str(consec),                 f"<= {next_tier.max_consec_loss}",   consec      <= next_tier.max_consec_loss),
                TierProgress("SL-Rate",       f"{sl_rate*100:.0f}%",       f"<= {next_tier.max_sl_rate*100:.0f}%",  sl_rate     <= next_tier.max_sl_rate),
            ]

        all_tiers_status = [(_TIERS[i], unlocked[i]) for i in range(len(_TIERS))]

        return MarginTierResult(
            active_tier=active_tier,
            next_tier=next_tier,
            downgrade_reason=downgrade_reason,
            progress=progress,
            all_tiers_status=all_tiers_status,
            analyzed_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            total_trades=total,
            recent_win_rate=recent_wr20,
            avg_return=avg_return,
            consec_losses=consec,
            sl_rate=sl_rate,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _win_rate(trades: List[Dict]) -> float:
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if (t.get("actual_return_pct") or 0) > 0)
    return wins / len(trades)


def _max_consecutive_losses(trades: List[Dict]) -> int:
    max_streak = streak = 0
    for t in trades:
        if (t.get("actual_return_pct") or 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _current_consecutive_losses(trades: List[Dict]) -> int:
    """Aktuell laufende Verlustserie (von neuesten Trades)."""
    streak = 0
    for t in trades:
        if (t.get("actual_return_pct") or 0) < 0:
            streak += 1
        else:
            break
    return streak
