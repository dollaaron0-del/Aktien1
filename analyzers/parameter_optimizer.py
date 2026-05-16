"""
ParameterOptimizer – analysiert Trade-History und schlägt bessere Parameter vor.

Logik:
  1. Liest abgeschlossene Trades aus PerformanceTracker
  2. Analysiert: Win-Rate, Stop-Loss-Trefferquote, TP-Erreichungsrate,
     Haltedauer-Abweichung, Sentiment-Bucket-Performance
  3. Generiert konkrete Vorschläge mit Begründung und Konfidenz
  4. Kann Vorschläge direkt in .env schreiben (mit Backup)

Aufruf:
  python main.py --optimize          → Vorschläge anzeigen
  python main.py --optimize --apply  → Vorschläge in .env schreiben
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_MIN_TRADES = 15          # Mindestanzahl Trades für sinnvolle Analyse
_ENV_FILE   = os.path.join(os.path.dirname(__file__), "..", ".env")


@dataclass
class Suggestion:
    parameter:   str        # .env Schlüssel, z.B. "BUY_THRESHOLD"
    current_val: str        # aktueller Wert als String
    suggested_val: str      # vorgeschlagener Wert
    reason:      str        # verständliche Erklärung
    confidence:  str        # "HOCH" | "MITTEL" | "NIEDRIG"
    impact:      str        # "RISIKO" | "RENDITE" | "STABILITÄT"
    expected:    str        # was sich verbessern sollte

    def to_text(self) -> str:
        return (
            f"[{self.confidence}] {self.parameter}\n"
            f"  Aktuell:    {self.current_val}\n"
            f"  Vorschlag:  {self.suggested_val}\n"
            f"  Grund:      {self.reason}\n"
            f"  Erwartet:   {self.expected}"
        )


@dataclass
class OptimizationReport:
    total_trades:  int
    analyzed_at:   str
    suggestions:   List[Suggestion] = field(default_factory=list)
    observations:  List[str]        = field(default_factory=list)

    @property
    def has_suggestions(self) -> bool:
        return len(self.suggestions) > 0

    def to_text(self) -> str:
        lines = [
            f"=== PARAMETER-OPTIMIERUNG ({self.analyzed_at}) ===",
            f"Analysierte Trades: {self.total_trades}",
            "",
        ]
        if self.observations:
            lines.append("Beobachtungen:")
            for o in self.observations:
                lines.append(f"  • {o}")
            lines.append("")

        if self.suggestions:
            lines.append(f"Vorschläge ({len(self.suggestions)}):")
            for i, s in enumerate(self.suggestions, 1):
                lines.append(f"\n{i}. {s.to_text()}")
        else:
            lines.append("Keine Änderungen empfohlen – Parameter sind gut eingestellt.")

        lines.append("\n" + "=" * 40)
        return "\n".join(lines)

    def to_telegram(self) -> str:
        if not self.suggestions:
            return (
                f"✅ *Parameter-Check* nach {self.total_trades} Trades\n"
                f"Alle Parameter sind gut eingestellt – keine Änderung nötig."
            )
        lines = [
            f"🔧 *Parameter-Optimierung* nach {self.total_trades} Trades",
            f"{len(self.suggestions)} Vorschlag/Vorschläge:",
            "",
        ]
        for s in self.suggestions:
            icon = {"HOCH": "🔴", "MITTEL": "🟡", "NIEDRIG": "🟢"}.get(s.confidence, "•")
            lines.append(
                f"{icon} `{s.parameter}`: {s.current_val} → *{s.suggested_val}*\n"
                f"   {s.reason}"
            )
        lines.append("\n`python main.py --optimize` für Details")
        lines.append("`python main.py --optimize --apply` zum Anwenden")
        return "\n".join(lines)


class ParameterOptimizer:

    def __init__(self, tracker, config=None):
        self._tracker = tracker
        self._config  = config

    def analyze(self) -> OptimizationReport:
        report_data = self._tracker.get_accuracy_report()
        total = report_data.get("total_closed", 0)

        if total < _MIN_TRADES:
            return OptimizationReport(
                total_trades=total,
                analyzed_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                observations=[
                    f"Noch nicht genug Trades für Optimierung ({total}/{_MIN_TRADES} abgeschlossen).",
                    "Mindestens 15 abgeschlossene Trades werden benötigt.",
                ],
            )

        exit_stats   = {r["category"]: r for r in self._tracker.get_exit_reason_stats()}
        buckets      = self._tracker.get_sentiment_score_buckets()
        recent       = self._tracker.get_recent_trades(n=30)

        suggestions:  List[Suggestion] = []
        observations: List[str]        = []

        win_rate    = report_data.get("win_rate_pct", 50.0) / 100
        avg_return  = report_data.get("avg_return_pct", 0.0)
        avg_hold    = report_data.get("avg_hold_days_actual", 0.0)

        # ── Aktuelle .env-Werte lesen ─────────────────────────────────────────
        env = _read_env()
        current_threshold = float(env.get("BUY_THRESHOLD",    "0.65"))
        current_stoploss  = float(env.get("STOP_LOSS_PCT",    "0.07"))
        current_takeprofit= float(env.get("TAKE_PROFIT_PCT",  "0.20"))
        current_min_src   = int(  env.get("MIN_SOURCES",      "2"))

        # ── 1. Stop-Loss-Trefferquote ─────────────────────────────────────────
        sl_stats = exit_stats.get("stop_loss", {})
        sl_trades = sl_stats.get("trades", 0)
        sl_rate = sl_trades / total if total > 0 else 0.0

        if sl_rate > 0.45:
            observations.append(
                f"{sl_rate*100:.0f}% aller Trades treffen den Stop-Loss "
                f"({sl_trades}/{total}) – Einstiegsqualität prüfen."
            )
            if win_rate < 0.45:
                new_threshold = min(round(current_threshold + 0.05, 2), 0.85)
                suggestions.append(Suggestion(
                    parameter="BUY_THRESHOLD",
                    current_val=str(current_threshold),
                    suggested_val=str(new_threshold),
                    reason=(
                        f"Win-Rate {win_rate*100:.0f}% und Stop-Loss-Rate {sl_rate*100:.0f}% "
                        f"deuten auf zu niedrige Kaufschwelle hin – Bot kauft bei zu schwachen Signalen."
                    ),
                    confidence="HOCH",
                    impact="STABILITÄT",
                    expected="Weniger Trades, aber höhere Trefferquote pro Trade.",
                ))
        elif sl_rate < 0.15 and win_rate > 0.65:
            observations.append(
                f"Stop-Loss-Rate sehr niedrig ({sl_rate*100:.0f}%) bei guter Win-Rate – "
                f"Strategie läuft gut."
            )

        # ── 2. Take-Profit-Erreichungsrate ────────────────────────────────────
        tp_stats  = exit_stats.get("take_profit", {})
        tp_trades = tp_stats.get("trades", 0)
        tp_rate   = tp_trades / total if total > 0 else 0.0
        tp_avg_return = tp_stats.get("avg_return_pct", 0.0)

        # Trades die weit unter Take-Profit schließen
        under_tp_trades = [
            t for t in recent
            if (t.get("actual_return_pct") or 0) > 0
            and (t.get("actual_return_pct") or 0) < current_takeprofit * 100 * 0.4
        ]
        under_tp_rate = len(under_tp_trades) / max(1, len([t for t in recent if (t.get("actual_return_pct") or 0) > 0]))

        if tp_rate < 0.20 and win_rate > 0.50 and avg_return > 0:
            new_tp = round(current_takeprofit - 0.03, 2)
            if new_tp >= 0.10:
                observations.append(
                    f"Take-Profit wird selten erreicht ({tp_rate*100:.0f}%) – "
                    f"Gewinne werden oft vor TP genommen oder drehen um."
                )
                suggestions.append(Suggestion(
                    parameter="TAKE_PROFIT_PCT",
                    current_val=f"{current_takeprofit:.0%}",
                    suggested_val=f"{new_tp:.0%}",
                    reason=(
                        f"Take-Profit ({current_takeprofit:.0%}) wird nur in {tp_rate*100:.0f}% der Trades erreicht. "
                        f"Geringeres Ziel sichert mehr Gewinne."
                    ),
                    confidence="MITTEL",
                    impact="RENDITE",
                    expected=f"Mehr Trades schließen im Gewinn, Ø-Rendite stabilisiert sich.",
                ))

        if tp_rate > 0.55:
            observations.append(
                f"Take-Profit sehr häufig erreicht ({tp_rate*100:.0f}%) – "
                f"TP könnte etwas angehoben werden für mehr Upside."
            )
            if win_rate > 0.65:
                new_tp = round(min(current_takeprofit + 0.03, 0.35), 2)
                suggestions.append(Suggestion(
                    parameter="TAKE_PROFIT_PCT",
                    current_val=f"{current_takeprofit:.0%}",
                    suggested_val=f"{new_tp:.0%}",
                    reason=(
                        f"TP wird in {tp_rate*100:.0f}% erreicht bei {win_rate*100:.0f}% Win-Rate – "
                        f"Signale sind stark genug für höheres Ziel."
                    ),
                    confidence="NIEDRIG",
                    impact="RENDITE",
                    expected="Höhere Gewinne pro Trade bei gleichbleibender Win-Rate.",
                ))

        # ── 3. Stop-Loss zu groß/klein ────────────────────────────────────────
        if sl_trades >= 5:
            # Durchschnittlicher Verlust bei Stop-Loss-Trades
            sl_losses = [
                abs(t.get("actual_return_pct") or 0)
                for t in recent
                if t.get("sell_reason_category") == "stop_loss"
            ]
            if sl_losses:
                avg_sl_loss = sum(sl_losses) / len(sl_losses)
                configured_sl = current_stoploss * 100

                if avg_sl_loss > configured_sl * 1.4:
                    observations.append(
                        f"Durchschnittlicher Stop-Loss-Verlust ({avg_sl_loss:.1f}%) "
                        f"deutlich höher als konfiguriert ({configured_sl:.0f}%) – "
                        f"Stop-Loss greift zu spät oder wird übersprungen."
                    )
                elif avg_sl_loss < configured_sl * 0.5 and sl_rate > 0.3:
                    new_sl = round(max(current_stoploss - 0.01, 0.04), 2)
                    suggestions.append(Suggestion(
                        parameter="STOP_LOSS_PCT",
                        current_val=f"{current_stoploss:.0%}",
                        suggested_val=f"{new_sl:.0%}",
                        reason=(
                            f"Durchschnittlicher Verlust bei SL-Trades nur {avg_sl_loss:.1f}% "
                            f"– engerer Stop würde Verluste weiter begrenzen."
                        ),
                        confidence="NIEDRIG",
                        impact="RISIKO",
                        expected="Kleinere Einzelverluste, evtl. etwas mehr SL-Treffer.",
                    ))

        # ── 4. Sentiment-Bucket-Analyse ───────────────────────────────────────
        best_bucket  = None
        worst_bucket = None
        for b in buckets:
            if b["trades"] >= 3:
                if best_bucket is None or b["win_rate_pct"] > best_bucket["win_rate_pct"]:
                    best_bucket = b
                if worst_bucket is None or b["win_rate_pct"] < worst_bucket["win_rate_pct"]:
                    worst_bucket = b

        if worst_bucket and worst_bucket["win_rate_pct"] < 35 and worst_bucket["trades"] >= 3:
            # Der schwächste Bucket performt schlecht → Schwelle anheben
            low_range  = worst_bucket["score_range"]
            low_start  = float(low_range.split("–")[0])
            if low_start <= current_threshold + 0.05:
                new_thr = round(min(low_start + 0.05, 0.85), 2)
                if new_thr > current_threshold:
                    already_suggested = any(s.parameter == "BUY_THRESHOLD" for s in suggestions)
                    if not already_suggested:
                        suggestions.append(Suggestion(
                            parameter="BUY_THRESHOLD",
                            current_val=str(current_threshold),
                            suggested_val=str(new_thr),
                            reason=(
                                f"Sentiment-Bereich {low_range} hat nur {worst_bucket['win_rate_pct']}% "
                                f"Win-Rate bei {worst_bucket['trades']} Trades – diese Signale sind unzuverlässig."
                            ),
                            confidence="MITTEL",
                            impact="STABILITÄT",
                            expected=f"Schwache Signale ({low_range}) werden gefiltert.",
                        ))

        if best_bucket:
            observations.append(
                f"Stärkster Sentiment-Bereich: {best_bucket['score_range']} "
                f"({best_bucket['win_rate_pct']}% Win-Rate, {best_bucket['trades']} Trades)."
            )

        # ── 5. Quellen-Mindestanzahl ──────────────────────────────────────────
        if win_rate < 0.42 and current_min_src < 3:
            suggestions.append(Suggestion(
                parameter="MIN_SOURCES",
                current_val=str(current_min_src),
                suggested_val="3",
                reason=(
                    f"Win-Rate {win_rate*100:.0f}% – mehr bestätigende Quellen "
                    f"reduzieren Fehlsignale."
                ),
                confidence="MITTEL",
                impact="STABILITÄT",
                expected="Weniger, aber zuverlässigere Kaufsignale.",
            ))
        elif win_rate > 0.65 and current_min_src > 2:
            suggestions.append(Suggestion(
                parameter="MIN_SOURCES",
                current_val=str(current_min_src),
                suggested_val="2",
                reason=(
                    f"Win-Rate {win_rate*100:.0f}% sehr stark – Quellen-Hürde "
                    f"kann etwas gelockert werden für mehr Handelsfrequenz."
                ),
                confidence="NIEDRIG",
                impact="RENDITE",
                expected="Mehr Trades bei weiterhin guter Qualität.",
            ))

        # ── Abschluss-Beobachtungen ───────────────────────────────────────────
        observations.append(
            f"Ø-Rendite pro Trade: {avg_return:+.2f}% | "
            f"Win-Rate: {win_rate*100:.0f}% | "
            f"Ø Haltedauer: {avg_hold:.1f} Tage"
        )

        return OptimizationReport(
            total_trades=total,
            analyzed_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            suggestions=suggestions,
            observations=observations,
        )

    def apply(self, report: OptimizationReport) -> List[str]:
        """Schreibt Vorschläge in .env. Backup wird automatisch erstellt."""
        if not report.suggestions:
            return ["Keine Änderungen anzuwenden."]

        # Backup
        backup_path = _ENV_FILE + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(_ENV_FILE, backup_path)
        except Exception as e:
            log.warning("Backup fehlgeschlagen: %s", e)

        env_text = ""
        try:
            with open(_ENV_FILE) as f:
                env_text = f.read()
        except FileNotFoundError:
            env_text = ""

        applied = []
        for s in report.suggestions:
            key = s.parameter
            # Wert-Format normalisieren
            if "%" in s.suggested_val:
                # z.B. "7%" → "0.07"
                numeric = float(s.suggested_val.replace("%", "")) / 100
                new_raw = str(numeric)
            else:
                new_raw = s.suggested_val

            pattern = rf"^({re.escape(key)}\s*=\s*).*$"
            replacement = rf"\g<1>{new_raw}"
            new_text, count = re.subn(pattern, replacement, env_text, flags=re.MULTILINE)
            if count == 0:
                new_text = env_text + f"\n{key}={new_raw}"
            env_text = new_text
            applied.append(f"  {key}: {s.current_val} → {new_raw}")
            log.info("ParameterOptimizer: %s = %s", key, new_raw)

        try:
            with open(_ENV_FILE, "w") as f:
                f.write(env_text)
        except Exception as e:
            log.error("ParameterOptimizer: Schreibfehler – %s", e)
            return [f"Fehler beim Schreiben: {e}"]

        return [f"Backup: {backup_path}"] + applied


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_env() -> Dict[str, str]:
    result = {}
    try:
        with open(_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return result
