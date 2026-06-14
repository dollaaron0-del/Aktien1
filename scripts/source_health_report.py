#!/usr/bin/env python3
"""
scripts/source_health_report.py – Quellen-Health-Report (Informationsdichte).

Wertet aus analysis_log.db aus, welche Collectors real beitragen und welche
über die letzten Tage NICHTS geliefert haben (tot = fehlender API-Key oder
defekt). Macht den blinden Fleck „Bot glaubt multi-source, läuft real auf
wenigen Quellen" sichtbar. Meldet per Telegram (wöchentlich via Timer).

Aufruf:
  venv/bin/python scripts/source_health_report.py            # sendet Telegram
  venv/bin/python scripts/source_health_report.py --dry-run  # nur Konsole
  venv/bin/python scripts/source_health_report.py --days 14
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _days_arg(default: int = 7) -> int:
    if "--days" in sys.argv:
        try:
            return int(sys.argv[sys.argv.index("--days") + 1])
        except (ValueError, IndexError):
            pass
    return default


def build_report(days: int = 7) -> str:
    from analyzers.analysis_log import AnalysisLog
    h = AnalysisLog().source_health(days=days)

    lines = [f"📡 <b>Quellen-Health (letzte {days} Tage)</b>"]
    if h["n_analyses"] == 0:
        lines.append("\nKeine Analysen im Zeitfenster – keine Aussage möglich.")
        return "\n".join(lines)

    lines.append(f"Analysen: {h['n_analyses']}"
                 + ("" if h["reliable"] else "  ⚠️ <i>(wenige – Aussage vorläufig)</i>"))
    lines.append(f"✅ Gesund: {len(h['healthy'])}  ·  🟡 Schwach: {len(h['weak'])}  ·  ❌ Tot: {len(h['dead'])}")

    if h["dead"]:
        lines.append("\n❌ <b>Tote Quellen (0 Treffer – API-Key/defekt prüfen):</b>")
        lines.append("  " + ", ".join(h["dead"]))
    if h["weak"]:
        lines.append("\n🟡 <b>Schwach (&lt;10% der Analysen):</b>")
        lines.append("  " + ", ".join(h["weak"]))
    if not h["dead"] and not h["weak"]:
        lines.append("\nAlle beitragenden Quellen gesund. 👍")
    return "\n".join(lines)


def main() -> int:
    days = _days_arg()
    report = build_report(days)
    print(report.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("&lt;", "<"))
    if "--dry-run" not in sys.argv:
        try:
            from notifier.telegram_notifier import TelegramNotifier
            TelegramNotifier().send(report)
        except Exception as e:
            print(f"[Telegram fehlgeschlagen: {e}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
