#!/usr/bin/env python3
"""
Historische S&P-500-Zusammensetzung laden — Deep-Research Phase 1 / Vision V0.3
(Roadmap 6.2b).

Lädt den freien, gepflegten Datensatz von github.com/fja05680/sp500 (CSV mit
je einer Zeile pro Änderungsdatum: date, tickers) und legt ihn lokal ab, damit
strategy_lab.universe.constituents_at() ohne Netzzugriff arbeiten kann.

Reines Sammel-Skript, kein Bot-Wiring. Ersetzt NICHT den Kurs-Survivorship-Fix
(6.2c, Bezahlquelle) — das hier ist nur die MITGLIEDERLISTE über die Zeit, die
Kurse delisteter Titel fehlen bei yfinance weiterhin (siehe universe.py-Docstring).

Usage:
  python -m scripts.sp500_membership_download
  python -m scripts.sp500_membership_download --dest data/sp500_membership.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SOURCE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv"
)
DEFAULT_DEST = Path(__file__).parent.parent / "data" / "sp500_membership.csv"


def main() -> None:
    ap = argparse.ArgumentParser(description="Historische S&P-500-Mitgliederliste laden")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--url", default=SOURCE_URL)
    args = ap.parse_args()

    from system.http import http_get
    dest = Path(args.dest)

    print(f"Lade {args.url} …")
    resp = http_get(args.url, timeout=30)
    resp.raise_for_status()
    text = resp.text
    if not text.startswith("date,tickers"):
        print("ABBRUCH: unerwartetes Format (Header != 'date,tickers') — "
              "Quelle hat sich evtl. geändert, Skript nicht blind vertrauen.")
        sys.exit(1)

    from strategy_lab.universe import parse_membership_csv
    df = parse_membership_csv(text)              # validiert + normalisiert vorab
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".csv.tmp")
    tmp.write_text(text)
    tmp.replace(dest)
    print(f"Fertig: {len(df)} Stichtage ({df['date'].min().date()} … "
          f"{df['date'].max().date()}) → {dest}")


if __name__ == "__main__":
    main()
