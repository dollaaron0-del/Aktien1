#!/usr/bin/env bash
# Wartet auf das Ende von aktien-cpcv-nightly.service, dann startet er den
# Meta-Backtest — nacheinander statt parallel (User-Anweisung 17.7. Abend),
# mit hartem Sicherheits-Deadline vor dem 06:00-Bot-Resume.
set -u
cd /opt/Aktien

DEADLINE_EPOCH=$(date -d "2026-07-18 05:30:00" +%s)

while systemctl is-active --quiet aktien-cpcv-nightly.service; do
  now=$(date +%s)
  if [ "$now" -ge "$DEADLINE_EPOCH" ]; then
    echo "ABBRUCH: CPCV laeuft noch, aber Deadline 05:30 erreicht - Meta-Backtest wird NICHT mehr gestartet (Sicherheitsabstand vor 06:00-Bot-Resume)."
    exit 0
  fi
  sleep 30
done

now=$(date +%s)
remaining=$(( DEADLINE_EPOCH - now ))
if [ "$remaining" -le 600 ]; then
  echo "ABBRUCH: CPCV fertig, aber weniger als 10 Min. bis zur 05:30-Deadline uebrig - Meta-Backtest wird NICHT mehr gestartet."
  exit 0
fi

# Timeout in Minuten, gedeckelt auf max. 180 Min., sonst Rest bis Deadline.
timeout_min=$(( remaining / 60 ))
if [ "$timeout_min" -gt 180 ]; then
  timeout_min=180
fi

echo "CPCV fertig - starte Meta-Backtest mit ${timeout_min}min Zeitbudget bis Deadline 05:30."
exec timeout "${timeout_min}m" venv/bin/python -m scripts.meta_backtest --step-years 1 --max-combos 100 --workers 0
