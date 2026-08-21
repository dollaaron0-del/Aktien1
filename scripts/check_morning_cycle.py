#!/usr/bin/env python3
"""Einmaliger Morgen-Check (geplant via systemd-run): verifiziert, ob die
Ollama→BUY→Ausführungs-Pipeline nach den Fixes vom 18.6. im 07:30-Zyklus
echte Signale liefert statt Flächen-SKIP. Schreibt eine Zusammenfassung nach
logs/cycle_check_<DATUM>.txt und schickt sie per Telegram.

Aufruf:  venv/bin/python scripts/check_morning_cycle.py [YYYY-MM-DD]
Default-Datum = heute (Serverzeit).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "bot.log")
PORTF_DB = os.path.join(ROOT, "data", "portfolio.db")
CACHE = os.path.join(ROOT, "data", "analysis_cache.json")


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    timeouts = 0
    full_results: list[str] = []
    orders = 0
    try:
        with open(LOG, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith(day):
                    continue
                if "Ollama Timeout" in line:
                    timeouts += 1
                elif "Ollama full_analysis" in line:
                    m = re.search(r"full_analysis (.+)$", line)
                    if m:
                        full_results.append(m.group(1).strip())
                elif "Order eingereicht" in line:
                    orders += 1
    except FileNotFoundError:
        pass

    buys = [r for r in full_results if r.split("]:")[-1].strip().startswith("BUY")]

    # Portfolio-Stand + heutige Trades
    n_pos, trades_today = 0, []
    try:
        c = sqlite3.connect(PORTF_DB)
        n_pos = c.execute("select count(*) from positions").fetchone()[0]
        for r in c.execute(
            "select ticker, action, shares, price, timestamp from trades "
            "where timestamp like ? order by id desc",
            (day + "%",),
        ):
            trades_today.append(r)
        c.close()
    except Exception as e:  # noqa: BLE001
        trades_today = [("DB-Fehler", str(e), 0, 0, "")]

    # Empfehlungs-Verteilung im Cache
    dist: Counter = Counter()
    try:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
        for v in cache.values():
            inner = v if isinstance(v, dict) else {}
            dist[inner.get("recommendation")] += 1
    except Exception:
        pass

    n_ok = len(full_results)
    if n_ok == 0:
        verdict = "⚠️ KEINE lokalen Analysen (Ollama tot / nur Claude/SKIP)"
    elif timeouts > n_ok:
        verdict = f"⚠️ noch viele Timeouts ({timeouts} > {n_ok} OK) – Hardware grenzwertig"
    elif buys and orders == 0 and n_pos == 0 and not trades_today:
        verdict = "⚠️ BUY-Signale erzeugt, aber NICHT ausgeführt (alter Fehler)"
    else:
        verdict = "✅ Pipeline OK – lokale Signale, Ausführung intakt"

    lines = [
        f"🔎 <b>Morgen-Check {day} (07:30-Zyklus)</b>",
        f"{verdict}",
        "",
        f"Ollama: {len(full_results)} Analysen OK · {timeouts} Timeouts",
        f"davon BUY: {len(buys)}" + (f" → {', '.join(buys[:5])}" if buys else ""),
        f"Broker-Orders eingereicht: {orders}",
        f"Offene Positionen: {n_pos} · Trades heute: {len(trades_today)}",
    ]
    for t in trades_today[:6]:
        lines.append(f"  • {t[1]} {t[0]} {t[2]}@{t[3]}")
    if dist:
        top = ", ".join(f"{k}:{v}" for k, v in dist.most_common())
        lines.append(f"Cache-Empfehlungen: {top}")
    summary = "\n".join(lines)

    out_path = os.path.join(ROOT, "logs", f"cycle_check_{day}.txt")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(summary)
    except Exception:
        pass

    if os.getenv("CHECK_DRYRUN", "").lower() not in ("1", "true", "yes"):
        try:
            from notifier.telegram_notifier import TelegramNotifier
            TelegramNotifier().send(summary)
        except Exception as e:  # noqa: BLE001
            print("Telegram-Versand fehlgeschlagen:", e)

    print(summary)


if __name__ == "__main__":
    main()
