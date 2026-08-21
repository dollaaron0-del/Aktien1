#!/usr/bin/env python3
"""
scripts/first_run_check.py – Einmaliger Gegencheck des ersten Analyse-Zyklus
nach dem Bot-Neustart + sources_used-Crash-Fix (2026-06-14).

Prüft die lokalen Logs und die Signal-Queue und schickt das Ergebnis per
Telegram. Mit --dry-run wird nur auf stdout ausgegeben (kein Telegram).

Aufruf:
  venv/bin/python scripts/first_run_check.py            # sendet Telegram
  venv/bin/python scripts/first_run_check.py --dry-run  # nur Konsole
"""
from __future__ import annotations

import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "bot.log")

# Marker, die auf einen erfolgreichen Zyklus bzw. einen Crash hindeuten
_CYCLE_MARKERS = ("Analyse-Watchlist", "Analyse-Zyklus")
_MACRO_MARKER = "Makro-Kontext"
_CRASH_MARKERS = (
    "FATAL", "type 'dict' is not supported", "Error binding parameter",
    "ungefangene Exception",
)


def _read_log_lines() -> list[str]:
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _analyze(lines: list[str], today: str) -> dict:
    # Zyklus heute gelaufen? (Datum steht in der console.rule-Zeile + Makro-Logzeile)
    cycle_today = [
        l for l in lines
        if today in l and any(m in l for m in _CYCLE_MARKERS)
    ]
    # "Analyse-Watchlist: N Aktien" hat nur Uhrzeit-Präfix → separat zählen,
    # aber nur als Zusatzindiz (kann von gestern sein), daher Makro-Marker führend.
    macro_today = [l for l in lines if today in l and _MACRO_MARKER in l]
    crashes_today = [
        l for l in lines
        if today in l and any(m in l for m in _CRASH_MARKERS)
    ]
    watchlist_any = [l for l in lines if "Analyse-Watchlist" in l]
    return {
        "cycle_today": cycle_today,
        "macro_today": macro_today,
        "crashes_today": crashes_today,
        "watchlist_last": watchlist_any[-1] if watchlist_any else "",
    }


def _queue_state() -> dict:
    try:
        from portfolio.signal_queue import SignalQueue
        q = SignalQueue()
        hist = q.get_history(8)
        return {"pending": q.count_pending(), "history": hist, "error": None}
    except Exception as e:
        return {"pending": None, "history": [], "error": str(e)}


def build_report(today: str) -> str:
    lines = _read_log_lines()
    a = _analyze(lines, today)
    q = _queue_state()

    ran = bool(a["macro_today"] or a["cycle_today"])
    crashed = bool(a["crashes_today"])

    if ran and not crashed:
        head = "✅ <b>Erster Lauf OK</b>"
    elif ran and crashed:
        head = "⚠️ <b>Lauf lief, aber mit Fehlern</b>"
    elif not ran and not crashed:
        head = "❓ <b>Noch kein Analyse-Zyklus heute erkannt</b>"
    else:
        head = "❌ <b>Crash erkannt, kein sauberer Lauf</b>"

    out = [f"{head} – Gegencheck {today}", "━━━━━━━━━━━━━━━━━━━━"]

    out.append(f"🔄 Zyklus heute: {'ja' if a['cycle_today'] or a['macro_today'] else 'nein'}")
    out.append(f"📊 Makro-Brief aktiv: {'ja' if a['macro_today'] else 'nein'}")
    out.append(
        f"💥 Crash/FATAL heute: {'JA – ' + str(len(a['crashes_today'])) + ' Zeilen' if crashed else 'nein'}"
    )
    if crashed:
        # erste Crash-Zeile gekürzt mitschicken
        out.append(f"   <code>{a['crashes_today'][0][:160]}</code>")

    if q["error"]:
        out.append(f"📥 Signal-Queue: Fehler beim Lesen ({q['error'][:80]})")
    else:
        out.append(f"📥 Signal-Queue: {q['pending']} ausstehend")
        today_hist = [h for h in q["history"] if (h.get('created_at') or '')[:10] == today]
        if today_hist:
            tickers = ", ".join(f"{h['ticker']}({h['status']})" for h in today_hist[:5])
            out.append(f"   heute: {tickers}")
        elif q["history"]:
            last = q["history"][0]
            out.append(f"   letzter Eintrag: {last['ticker']} {(last.get('created_at') or '')[:16]}")

    if a["watchlist_last"]:
        out.append(f"📋 {a['watchlist_last'].split('] ')[-1][:120]}")

    return "\n".join(out)


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    today = date.today().isoformat()
    report = build_report(today)

    print(report)

    if not dry_run:
        try:
            from notifier.telegram_notifier import TelegramNotifier
            TelegramNotifier().send(report)
            print("\n[gesendet via Telegram]")
        except Exception as e:
            print(f"\n[Telegram-Versand fehlgeschlagen: {e}]")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
