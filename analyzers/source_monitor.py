"""
analyzers/source_monitor.py – Live-Quellen-Health pro Analyse-Zyklus.

Der bestehende AnalysisLog.source_health() bewertet Quellen rückblickend über
7 Tage (für den Report-Skript). Diese Klasse ergänzt eine SOFORTIGE Erkennung:
fällt eine Quelle, die normalerweise liefert, in EINEM Zyklus zyklusweit auf 0
(oder wirft Fehler), gibt es direkt einen (gedrosselten) Telegram-Hinweis –
statt dass eine plötzlich tote/blockierte Quelle tagelang unbemerkt bleibt.

Mechanik:
- Pro Zyklus werden je Quelle die Artikel-Summe (über alle Ticker) und etwaige
  Collector-Fehler gesammelt.
- Eine gleitende Baseline (EWMA, nur über gesunde Zyklen aktualisiert) hält das
  „normale" Niveau jeder Quelle.
- Alarm, wenn eine Quelle mit relevanter Baseline zyklusweit 0 liefert ODER
  Fehler hatte. Pro Quelle max. 1 Alarm / Cooldown; erholt sich die Quelle,
  wird der Throttle zurückgesetzt.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_STATE_FILE   = Path("data/source_monitor.json")
_THROTTLE_H   = 12      # gleiche Quelle höchstens alle 12h erneut melden
_EWMA_ALPHA   = 0.3     # Gewicht des neuen Zyklus in der Baseline
_MIN_BASELINE = 1.0     # nur Quellen mit relevanter Historie alarmieren


class SourceHealthMonitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}
        self._errors: Dict[str, int] = {}
        self._seen: set = set()

    # ── Zyklus-Erfassung (thread-safe; collect_news läuft parallel) ───────────
    def start_cycle(self) -> None:
        with self._lock:
            self._counts, self._errors, self._seen = {}, {}, set()

    def note_result(self, source: str, count: int) -> None:
        with self._lock:
            self._seen.add(source)
            self._counts[source] = self._counts.get(source, 0) + int(count or 0)

    def note_error(self, source: str) -> None:
        with self._lock:
            self._seen.add(source)
            self._errors[source] = self._errors.get(source, 0) + 1

    # ── Auswertung am Zyklus-Ende ─────────────────────────────────────────────
    def finalize_cycle(self, notifier=None) -> List[Dict]:
        with self._lock:
            counts, errors, seen = dict(self._counts), dict(self._errors), set(self._seen)
        if not seen:
            return []

        state     = self._load()
        baselines = state.get("baselines", {})
        alerted   = state.get("alerted", {})
        now       = datetime.now(timezone.utc)
        degraded: List[Dict] = []

        for src in seen:
            total = counts.get(src, 0)
            err   = errors.get(src, 0)
            base  = float(baselines.get(src, 0.0))

            is_degraded = err > 0 or (base >= _MIN_BASELINE and total == 0)
            if is_degraded:
                last = alerted.get(src)
                send = True
                if last:
                    try:
                        if (now - datetime.fromisoformat(last)).total_seconds() < _THROTTLE_H * 3600:
                            send = False
                    except Exception:
                        pass
                if send:
                    degraded.append({"source": src, "total": total,
                                     "errors": err, "baseline": round(base, 1)})
                    alerted[src] = now.isoformat()
            else:
                alerted.pop(src, None)   # erholt → künftige Fehler sofort melden

            # Baseline nur über gesunde Zyklen fortschreiben, damit ein Ausfall
            # das „normale" Niveau nicht wegfrisst und der Alarm bestehen bleibt.
            if total > 0:
                baselines[src] = round((1 - _EWMA_ALPHA) * base + _EWMA_ALPHA * total, 3)

        state["baselines"], state["alerted"] = baselines, alerted
        self._save(state)

        if degraded:
            names = ", ".join(d["source"] for d in degraded)
            log.warning("Quellen-Health: degradiert diesen Zyklus → %s", names)
            if notifier is not None:
                self._alert(degraded, notifier)
        return degraded

    # ── intern ────────────────────────────────────────────────────────────────
    @staticmethod
    def _alert(degraded: List[Dict], notifier) -> None:
        lines = ["⚠️ <b>Quellen-Health: Ausfall erkannt</b>", ""]
        for d in degraded:
            if d["errors"]:
                lines.append(f"❌ <b>{d['source']}</b>: {d['errors']} Fehler "
                             f"(Normalniveau ~{d['baseline']})")
            else:
                lines.append(f"🔇 <b>{d['source']}</b>: 0 Artikel diesen Zyklus "
                             f"(Normalniveau ~{d['baseline']})")
        lines.append("")
        lines.append("Quelle prüfen (Rate-Limit / API-Key / Endpoint).")
        try:
            notifier.send("\n".join(lines))
        except Exception as e:
            log.debug("Quellen-Health-Alert senden fehlgeschlagen: %s", e)

    @staticmethod
    def _load() -> Dict:
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            return {}

    @staticmethod
    def _save(state: Dict) -> None:
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            log.debug("Quellen-Monitor-State schreiben fehlgeschlagen: %s", e)


_monitor: Optional[SourceHealthMonitor] = None


def get_monitor() -> SourceHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SourceHealthMonitor()
    return _monitor
