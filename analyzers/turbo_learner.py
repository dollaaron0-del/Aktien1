"""
TurboLearner – analysiert abgeschlossene Turbo-Trades und leitet daraus
optimale Parameter für den normalen Modus ab.

Ablauf:
  1. Liest alle Turbo-Trades aus performance.db
  2. Findet die besten Sentiment-Schwellen, Konfidenz-Level, Hold-Zeiten
  3. Speichert Empfehlungen in data/turbo_learned_params.json
  4. apply_to_config() übernimmt sie in die laufende Config

Die gelernten Parameter wirken nur wenn >= MIN_TRADES Turbo-Trades vorliegen.
"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "data", "performance.db")
_PARAMS_PATH  = os.path.join(os.path.dirname(__file__), "..", "data", "turbo_learned_params.json")
MIN_TRADES    = 10   # Mindestanzahl Turbo-Trades für eine Empfehlung
MIN_WIN_RATE  = 0.50 # Schwelle ab der ein Bucket als "gut" gilt


class TurboLearner:

    # ── Datenbankzugriff ──────────────────────────────────────────────────────

    def _get_turbo_trades(self) -> List[Dict]:
        if not os.path.exists(_DB_PATH):
            return []
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT sentiment_score, confidence, actual_return_pct,
                          actual_hold_days, sell_reason_category
                   FROM predictions
                   WHERE mode = 'turbo' AND sell_date IS NOT NULL
                     AND actual_return_pct IS NOT NULL"""
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Analyse-Helfer ────────────────────────────────────────────────────────

    @staticmethod
    def _bucket_score(score: float) -> str:
        if score < 0.50:  return "0.45–0.50"
        if score < 0.55:  return "0.50–0.55"
        if score < 0.60:  return "0.55–0.60"
        if score < 0.65:  return "0.60–0.65"
        if score < 0.70:  return "0.65–0.70"
        if score < 0.75:  return "0.70–0.75"
        if score < 0.80:  return "0.75–0.80"
        if score < 0.85:  return "0.80–0.85"
        return "0.85–1.00"

    @staticmethod
    def _bucket_stats(returns: List[float]) -> Dict:
        if not returns:
            return {}
        wins = sum(1 for r in returns if r > 0)
        return {
            "trades":       len(returns),
            "win_rate":     round(wins / len(returns), 3),
            "avg_return":   round(sum(returns) / len(returns), 2),
            "total_return": round(sum(returns), 2),
        }

    def _analyse_threshold(self, trades: List[Dict]) -> Optional[float]:
        """
        Findet die niedrigste Score-Schwelle bei der win_rate >= MIN_WIN_RATE
        und avg_return > 0. Das ist der empfohlene neue buy_threshold.
        """
        buckets: Dict[str, List[float]] = {}
        for t in trades:
            b = self._bucket_score(t["sentiment_score"] or 0)
            buckets.setdefault(b, []).append(t["actual_return_pct"])

        # Bucket-Grenzen aufsteigend sortiert
        order = [
            "0.45–0.50", "0.50–0.55", "0.55–0.60", "0.60–0.65",
            "0.65–0.70", "0.70–0.75", "0.75–0.80", "0.80–0.85", "0.85–1.00",
        ]
        # Niedrigste profitable Schwelle finden (kumulativ von oben nach unten)
        best_threshold = None
        for label in reversed(order):
            returns = buckets.get(label, [])
            if len(returns) < 3:
                continue
            stats = self._bucket_stats(returns)
            if stats["win_rate"] >= MIN_WIN_RATE and stats["avg_return"] > 0:
                low = float(label.split("–")[0])
                best_threshold = low
            else:
                break  # sobald ein Bucket schlecht ist, nicht weiter senken
        return best_threshold

    def _analyse_confidence(self, trades: List[Dict]) -> Optional[bool]:
        """Gibt True zurück wenn LOW-Confidence-Trades im Schnitt profitabel sind."""
        low_conf = [t for t in trades if (t.get("confidence") or "").upper() == "LOW"]
        if len(low_conf) < 3:
            return None
        stats = self._bucket_stats([t["actual_return_pct"] for t in low_conf])
        return stats["win_rate"] >= MIN_WIN_RATE and stats["avg_return"] > 0

    def _analyse_hold_days(self, trades: List[Dict]) -> Optional[int]:
        """Mittlere Haltedauer der profitablen Turbo-Trades."""
        profitable = [
            t["actual_hold_days"] for t in trades
            if t["actual_return_pct"] > 0 and t["actual_hold_days"]
        ]
        if len(profitable) < 3:
            return None
        return round(sum(profitable) / len(profitable))

    def _analyse_exit_reasons(self, trades: List[Dict]) -> Dict[str, float]:
        """Durchschnitts-Return pro Exit-Kategorie."""
        cats: Dict[str, List[float]] = {}
        for t in trades:
            cat = t.get("sell_reason_category") or "unknown"
            cats.setdefault(cat, []).append(t["actual_return_pct"])
        return {cat: round(sum(v) / len(v), 2) for cat, v in cats.items()}

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def analyze(self) -> Optional[Dict]:
        """
        Führt die vollständige Analyse durch.
        Gibt None zurück wenn nicht genug Trades vorliegen.
        """
        trades = self._get_turbo_trades()
        if len(trades) < MIN_TRADES:
            return None

        all_returns = [t["actual_return_pct"] for t in trades]
        wins        = sum(1 for r in all_returns if r > 0)
        overall     = self._bucket_stats(all_returns)

        new_threshold    = self._analyse_threshold(trades)
        low_conf_ok      = self._analyse_confidence(trades)
        best_hold        = self._analyse_hold_days(trades)
        exit_stats       = self._analyse_exit_reasons(trades)

        result = {
            "generated_at":        datetime.utcnow().isoformat(),
            "turbo_trades_total":  len(trades),
            "overall_win_rate":    overall["win_rate"],
            "overall_avg_return":  overall["avg_return"],
            # Empfehlungen
            "recommended_buy_threshold":    new_threshold,
            "low_confidence_profitable":    low_conf_ok,
            "recommended_avg_hold_days":    best_hold,
            "exit_reason_avg_returns":      exit_stats,
            # Abgeleitete Einstellungen für normalen Modus
            "apply": {
                "buy_threshold": new_threshold,
                "accept_low_confidence": low_conf_ok,
            },
        }
        return result

    def save(self, result: Dict) -> str:
        os.makedirs(os.path.dirname(_PARAMS_PATH), exist_ok=True)
        with open(_PARAMS_PATH, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        return _PARAMS_PATH

    @staticmethod
    def load() -> Optional[Dict]:
        if not os.path.exists(_PARAMS_PATH):
            return None
        try:
            with open(_PARAMS_PATH) as f:
                return json.load(f)
        except Exception:
            return None

    def apply_to_config(self, cfg) -> List[str]:
        """
        Lädt die gelernten Parameter und wendet sie auf die laufende Config an.
        Gibt eine Liste der vorgenommenen Änderungen zurück.
        """
        params = self.load()
        if not params:
            return []

        changes = []
        apply   = params.get("apply", {})

        new_thresh = apply.get("buy_threshold")
        if new_thresh and 0.40 <= new_thresh <= 0.85:
            old = cfg.buy_threshold
            cfg.buy_threshold = round(new_thresh, 2)
            changes.append(f"buy_threshold: {old:.2f} → {cfg.buy_threshold:.2f} (Turbo-Lernwert)")

        return changes

    def analyze_and_save(self) -> Optional[Dict]:
        """Kompletter Ablauf: analysieren und speichern."""
        result = self.analyze()
        if result:
            self.save(result)
        return result

    def summary_text(self, result: Dict) -> str:
        """Lesbarer Text für Telegram/Log."""
        n      = result["turbo_trades_total"]
        wr     = result["overall_win_rate"] * 100
        avg_r  = result["overall_avg_return"]
        thresh = result.get("recommended_buy_threshold")
        hold   = result.get("recommended_avg_hold_days")
        lc_ok  = result.get("low_confidence_profitable")

        lines = [
            f"<b>🧠 Turbo-Lernbericht ({n} Trades)</b>",
            f"Win-Rate: {wr:.1f}%  |  Ø Return: {avg_r:+.2f}%",
        ]
        if thresh:
            lines.append(f"Empfohlene Kaufschwelle: {thresh:.2f} (normal: 0.65)")
        if hold:
            lines.append(f"Optimale Haltedauer: ~{hold} Tage")
        if lc_ok is not None:
            lc_text = "✅ LOW-Konfidenz profitabel" if lc_ok else "❌ LOW-Konfidenz nicht empfohlen"
            lines.append(lc_text)

        exit_stats = result.get("exit_reason_avg_returns", {})
        if exit_stats:
            lines.append("Exit-Analyse:")
            for cat, avg in sorted(exit_stats.items(), key=lambda x: -x[1]):
                lines.append(f"  • {cat}: {avg:+.2f}%")

        apply = result.get("apply", {})
        if any(v is not None for v in apply.values()):
            lines.append("→ Parameter wurden für normalen Modus übernommen.")

        return "\n".join(lines)
