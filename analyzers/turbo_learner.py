"""
TurboLearner – analysiert abgeschlossene Turbo-Trades und leitet daraus
optimale Parameter für den normalen Modus ab.

Ablauf:
  1. Liest Turbo-Trades UND normale Trades aus performance.db
  2. Vergleicht: schlagen Turbo-Parameter den normalen Modus?
  3. Nur wenn Turbo besser ist → Parameter in .env schreiben
  4. apply_to_config() liest .env-Werte und übernimmt sie sofort

Gesperrte Bereiche: Parameter werden nur dann angepasst wenn:
  - >= MIN_TRADES Turbo-Trades abgeschlossen
  - Turbo avg_return > Normal avg_return ODER Turbo win_rate > Normal win_rate + 5%
  - Sicherheitsgrenzen werden nie überschritten (z.B. SL min 4%, max 20%)
"""
import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_DB_PATH     = os.path.join(os.path.dirname(__file__), "..", "data", "performance.db")
_PARAMS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "turbo_learned_params.json")
_ENV_PATH    = os.path.join(os.path.dirname(__file__), "..", ".env")

MIN_TRADES   = 10    # Mindestanzahl Turbo-Trades
MIN_WIN_RATE = 0.50  # Bucket gilt als gut

# Sicherheitsgrenzen für gelernte Parameter (verhindert irrsinnige Werte)
_BOUNDS = {
    "BUY_THRESHOLD":    (0.40, 0.82),
    "STOP_LOSS_PCT":    (0.04, 0.20),
    "TAKE_PROFIT_PCT":  (0.08, 0.60),
    "MAX_POSITION_PCT": (0.05, 0.40),
}


class TurboLearner:

    # ── Datenbankzugriff ──────────────────────────────────────────────────────

    def _get_trades(self, mode: str) -> List[Dict]:
        if not os.path.exists(_DB_PATH):
            return []
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT sentiment_score, confidence, actual_return_pct,
                          actual_hold_days, sell_reason_category,
                          entry_price, sell_price
                   FROM predictions
                   WHERE mode = ? AND sell_date IS NOT NULL
                     AND actual_return_pct IS NOT NULL""",
                (mode,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    # ── Analyse-Helfer ────────────────────────────────────────────────────────

    @staticmethod
    def _bucket_score(score: float) -> str:
        for lo, hi in [(0.45, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65),
                       (0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 0.85)]:
            if score < hi:
                return f"{lo:.2f}–{hi:.2f}"
        return "0.85–1.00"

    @staticmethod
    def _stats(returns: List[float]) -> Dict:
        if not returns:
            return {"trades": 0, "win_rate": 0.0, "avg_return": 0.0}
        wins = sum(1 for r in returns if r > 0)
        return {
            "trades":     len(returns),
            "win_rate":   round(wins / len(returns), 3),
            "avg_return": round(sum(returns) / len(returns), 2),
        }

    def _best_threshold(self, trades: List[Dict]) -> Optional[float]:
        buckets: Dict[str, List[float]] = {}
        for t in trades:
            b = self._bucket_score(t["sentiment_score"] or 0)
            buckets.setdefault(b, []).append(t["actual_return_pct"])

        order = [
            "0.45–0.50", "0.50–0.55", "0.55–0.60", "0.60–0.65",
            "0.65–0.70", "0.70–0.75", "0.75–0.80", "0.80–0.85", "0.85–1.00",
        ]
        best = None
        for label in reversed(order):
            rets = buckets.get(label, [])
            if len(rets) < 3:
                continue
            s = self._stats(rets)
            if s["win_rate"] >= MIN_WIN_RATE and s["avg_return"] > 0:
                best = float(label.split("–")[0])
            else:
                break
        return best

    def _best_sl_tp(self, trades: List[Dict]) -> Tuple[Optional[float], Optional[float]]:
        """
        Leitet SL und TP aus den Trades ab:
        - SL: mittlerer Verlust der Stop-Loss-Exits (gibt an wie viel Raum nötig war)
        - TP: mittlerer Return der Take-Profit-Exits
        """
        sl_exits = [abs(t["actual_return_pct"]) / 100
                    for t in trades
                    if (t.get("sell_reason_category") or "") == "stop_loss"
                    and t["actual_return_pct"] is not None]
        tp_exits = [t["actual_return_pct"] / 100
                    for t in trades
                    if (t.get("sell_reason_category") or "") == "take_profit"
                    and t["actual_return_pct"] is not None]

        best_sl = round(sum(sl_exits) / len(sl_exits) * 1.15, 3) if len(sl_exits) >= 3 else None
        best_tp = round(sum(tp_exits) / len(tp_exits) * 0.90, 3) if len(tp_exits) >= 3 else None
        return best_sl, best_tp

    def _best_position_size(self, trades: List[Dict]) -> Optional[float]:
        """
        Wenn profitable Trades durchschnittlich besser als Verlusttrades sind:
        empfiehlt eine leicht erhöhte Positionsgröße (bis max 30%).
        """
        profitable = [t for t in trades if t["actual_return_pct"] > 0]
        losing     = [t for t in trades if t["actual_return_pct"] <= 0]
        if len(profitable) < 3 or len(losing) < 3:
            return None
        avg_win  = sum(t["actual_return_pct"] for t in profitable) / len(profitable)
        avg_loss = abs(sum(t["actual_return_pct"] for t in losing)  / len(losing))
        if avg_win <= 0 or avg_loss <= 0:
            return None
        # Kelly-ähnliche Schätzung: f = win_rate - (1-win_rate)/odds
        win_rate = len(profitable) / len(trades)
        odds     = avg_win / avg_loss
        kelly    = win_rate - (1 - win_rate) / odds
        # Halbe Kelly, auf [0.10, 0.30] begrenzt
        return round(max(0.10, min(0.30, kelly * 0.5)), 2) if kelly > 0 else None

    def _beats_normal(self, turbo: Dict, normal: Dict) -> bool:
        """True wenn Turbo-Statistiken den normalen Modus messbar schlagen."""
        if normal["trades"] < 5:
            return True  # Zu wenig Vergleichsdaten → Turbo-Empfehlungen trotzdem anwenden
        return (
            turbo["avg_return"] > normal["avg_return"]
            or turbo["win_rate"] > normal["win_rate"] + 0.05
        )

    # ── .env Persistenz ───────────────────────────────────────────────────────

    def _write_env(self, updates: Dict[str, str]) -> List[str]:
        """
        Schreibt gelernte Parameter dauerhaft in .env.
        Bestehende Zeilen werden überschrieben, neue am Ende hinzugefügt.
        Gibt eine Liste der geschriebenen Zeilen zurück.
        """
        if not os.path.exists(_ENV_PATH):
            return []

        with open(_ENV_PATH, "r") as f:
            lines = f.readlines()

        written = []
        remaining = dict(updates)

        new_lines = []
        for line in lines:
            updated = False
            for key in list(remaining.keys()):
                # Match KEY= or KEY =
                if re.match(rf"^\s*{re.escape(key)}\s*=", line):
                    new_lines.append(f"{key}={remaining.pop(key)}\n")
                    written.append(key)
                    updated = True
                    break
            if not updated:
                new_lines.append(line)

        # Neue Schlüssel die noch nicht in .env waren
        if remaining:
            new_lines.append("\n# Turbo-Lernwerte (auto-generiert)\n")
            for key, val in remaining.items():
                new_lines.append(f"{key}={val}\n")
                written.append(key)

        with open(_ENV_PATH, "w") as f:
            f.writelines(new_lines)

        return written

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def analyze(self) -> Optional[Dict]:
        turbo_trades  = self._get_trades("turbo")
        normal_trades = self._get_trades("normal") + self._get_trades("exploration")

        if len(turbo_trades) < MIN_TRADES:
            return None

        turbo_returns  = [t["actual_return_pct"] for t in turbo_trades]
        normal_returns = [t["actual_return_pct"] for t in normal_trades]

        turbo_stats  = self._stats(turbo_returns)
        normal_stats = self._stats(normal_returns)
        beats_normal = self._beats_normal(turbo_stats, normal_stats)

        new_threshold = self._best_threshold(turbo_trades)
        new_sl, new_tp = self._best_sl_tp(turbo_trades)
        new_pos_size  = self._best_position_size(turbo_trades)

        low_conf = [t for t in turbo_trades if (t.get("confidence") or "").upper() == "LOW"]
        low_conf_ok = None
        if len(low_conf) >= 3:
            lcs = self._stats([t["actual_return_pct"] for t in low_conf])
            low_conf_ok = lcs["win_rate"] >= MIN_WIN_RATE and lcs["avg_return"] > 0

        profitable_hold = [t["actual_hold_days"] for t in turbo_trades
                           if t["actual_return_pct"] > 0 and t["actual_hold_days"]]
        best_hold = round(sum(profitable_hold) / len(profitable_hold)) if len(profitable_hold) >= 3 else None

        exit_stats = {}
        for t in turbo_trades:
            cat = t.get("sell_reason_category") or "unknown"
            exit_stats.setdefault(cat, []).append(t["actual_return_pct"])
        exit_avgs = {c: round(sum(v) / len(v), 2) for c, v in exit_stats.items()}

        # Nur Parameter übernehmen die innerhalb der Sicherheitsgrenzen liegen
        def _clamp(val, key):
            if val is None:
                return None
            lo, hi = _BOUNDS[key]
            return round(max(lo, min(hi, val)), 3)

        apply = {}
        if beats_normal:
            if new_threshold:
                apply["buy_threshold"] = _clamp(new_threshold, "BUY_THRESHOLD")
            if new_sl:
                apply["stop_loss_pct"] = _clamp(new_sl, "STOP_LOSS_PCT")
            if new_tp:
                apply["take_profit_pct"] = _clamp(new_tp, "TAKE_PROFIT_PCT")
            if new_pos_size:
                apply["max_position_pct"] = _clamp(new_pos_size, "MAX_POSITION_PCT")

        return {
            "generated_at":        datetime.utcnow().isoformat(),
            "turbo_trades_total":  len(turbo_trades),
            "normal_trades_total": len(normal_trades),
            "turbo_stats":         turbo_stats,
            "normal_stats":        normal_stats,
            "beats_normal":        beats_normal,
            "recommended_buy_threshold":  new_threshold,
            "recommended_stop_loss_pct":  new_sl,
            "recommended_take_profit_pct": new_tp,
            "recommended_max_position_pct": new_pos_size,
            "low_confidence_profitable":  low_conf_ok,
            "recommended_avg_hold_days":  best_hold,
            "exit_reason_avg_returns":    exit_avgs,
            "apply":                      apply,
        }

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
        Lädt gelernte Parameter, wendet sie auf die laufende Config an
        UND schreibt sie persistent in .env (wirkt ab dem nächsten Neustart).
        """
        params = self.load()
        if not params:
            return []

        apply   = params.get("apply", {})
        changes = []
        env_updates: Dict[str, str] = {}

        mapping = [
            ("buy_threshold",    "buy_threshold",    "BUY_THRESHOLD",    0.40, 0.82),
            ("stop_loss_pct",    "stop_loss_pct",    "STOP_LOSS_PCT",    0.04, 0.20),
            ("take_profit_pct",  "take_profit_pct",  "TAKE_PROFIT_PCT",  0.08, 0.60),
            ("max_position_pct", "max_position_pct", "MAX_POSITION_PCT", 0.05, 0.40),
        ]

        for apply_key, cfg_attr, env_key, lo, hi in mapping:
            val = apply.get(apply_key)
            if val is None:
                continue
            val = max(lo, min(hi, float(val)))
            old = getattr(cfg, cfg_attr, None)
            if old is not None and abs(old - val) < 0.001:
                continue  # keine Änderung
            setattr(cfg, cfg_attr, round(val, 3))
            env_updates[env_key] = str(round(val, 3))
            changes.append(
                f"{cfg_attr}: {old:.3f} → {val:.3f}"
            )

        if env_updates:
            self._write_env(env_updates)

        return changes

    def analyze_and_save(self) -> Optional[Dict]:
        result = self.analyze()
        if result:
            self.save(result)
        return result

    def summary_text(self, result: Dict) -> str:
        n      = result["turbo_trades_total"]
        nn     = result["normal_trades_total"]
        ts     = result["turbo_stats"]
        ns     = result["normal_stats"]
        beats  = result["beats_normal"]
        hold   = result.get("recommended_avg_hold_days")
        lc_ok  = result.get("low_confidence_profitable")
        apply  = result.get("apply", {})

        verdict = "✅ Turbo schlägt Normal-Modus" if beats else "❌ Normal-Modus ist besser"

        lines = [
            f"<b>🧠 Turbo-Lernbericht</b>",
            f"Turbo: {n} Trades | Win {ts['win_rate']*100:.1f}% | Ø {ts['avg_return']:+.2f}%",
            f"Normal: {nn} Trades | Win {ns['win_rate']*100:.1f}% | Ø {ns['avg_return']:+.2f}%",
            verdict,
        ]

        if beats and apply:
            lines.append("\n<b>Übernommene Parameter (auch in .env gespeichert):</b>")
            labels = {
                "buy_threshold":    "Kaufschwelle",
                "stop_loss_pct":    "Stop-Loss",
                "take_profit_pct":  "Take-Profit",
                "max_position_pct": "Positionsgröße",
            }
            for key, val in apply.items():
                if val is not None:
                    pct = f"{val*100:.1f}%" if key != "buy_threshold" else f"{val:.2f}"
                    lines.append(f"  • {labels.get(key, key)}: {pct}")
        elif not beats:
            lines.append("→ Keine Parameter geändert (Turbo war nicht besser als Normal).")

        if hold:
            lines.append(f"Optimale Haltedauer: ~{hold} Tage (profitable Turbo-Trades)")
        if lc_ok is not None:
            lines.append("✅ LOW-Konfidenz war profitabel" if lc_ok else "❌ LOW-Konfidenz war nicht profitabel")

        exit_stats = result.get("exit_reason_avg_returns", {})
        if exit_stats:
            lines.append("\nExit-Analyse:")
            for cat, avg in sorted(exit_stats.items(), key=lambda x: -x[1]):
                lines.append(f"  • {cat}: {avg:+.2f}%")

        return "\n".join(lines)
