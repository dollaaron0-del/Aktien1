#!/usr/bin/env bash
# Wartet auf das Ende von aktien-metabacktest-nightly.service, dann startet
# CPCV mit dem 17.7.-Bugfix (_merge_contiguous) neu — der urspruengliche
# Lauf ist an der doppelt gezaehlten Blockgrenze abgestuerzt.
set -u
cd /opt/Aktien

DEADLINE_EPOCH=$(date -d "2026-07-18 05:30:00" +%s)

while systemctl is-active --quiet aktien-metabacktest-nightly.service; do
  now=$(date +%s)
  if [ "$now" -ge "$DEADLINE_EPOCH" ]; then
    echo "ABBRUCH: Meta-Backtest laeuft noch, aber Deadline 05:30 erreicht - CPCV-Retry wird NICHT mehr gestartet."
    exit 0
  fi
  sleep 30
done

now=$(date +%s)
remaining=$(( DEADLINE_EPOCH - now ))
if [ "$remaining" -le 600 ]; then
  echo "ABBRUCH: Meta-Backtest fertig, aber weniger als 10 Min. bis zur 05:30-Deadline uebrig - CPCV-Retry wird NICHT mehr gestartet."
  exit 0
fi

timeout_min=$(( remaining / 60 ))
if [ "$timeout_min" -gt 180 ]; then
  timeout_min=180
fi

echo "Meta-Backtest fertig - starte CPCV-Retry (mit Bugfix) mit ${timeout_min}min Zeitbudget bis Deadline 05:30."
exec timeout "${timeout_min}m" venv/bin/python -m scripts.cpcv --total 20 --n-blocks 6 --test-blocks 2 --max-combos 100 --workers 0
