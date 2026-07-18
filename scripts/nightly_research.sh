#!/usr/bin/env bash
# scripts/nightly_research.sh — Roadmap 6.7 Intensiv-Fahrplan.
#
# Orchestriert die rechenintensiven Strategie-Lab-Läufe als feste nächtliche
# Routine statt Hand-Anstoß: jede Nacht Walk-Forward (Registry-Refresh),
# sonntags zusätzlich Meta-Backtest + CPCV, am 1. des Monats zusätzlich
# Quellen-Ablation + Stress-Test. Harte Sicherheits-Deadline (05:30), damit
# nichts in den täglichen Bot-Betrieb (06:00 IPO-Check, 08:30 Morgenbericht)
# hineinläuft — ein Schritt, der die Deadline reißt, wird übersprungen statt
# verspätet gestartet; laufende Läufe werden aber nicht mitten in einem
# Schritt abgewürgt, sondern bekommen als Zeitbudget nur die Restzeit bis
# zur Deadline (gedeckelt auf 180 Min./Schritt).
#
# DEADLINE_OVERRIDE (Env, "YYYY-MM-DD HH:MM:SS"): für Tests/Trockenlauf, um
# sofort den Skip-Pfad zu erzwingen statt echte Stunden zu warten.
set -u
cd /opt/Aktien

REPORT_DIR="reports"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/nightly_research_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$REPORT_FILE") 2>&1

echo "=== Nightly Research Start: $(date) ==="

if [ -n "${DEADLINE_OVERRIDE:-}" ]; then
  DEADLINE_EPOCH=$(date -d "$DEADLINE_OVERRIDE" +%s)
else
  DEADLINE_EPOCH=$(date -d "today 05:30" +%s)
  now=$(date +%s)
  if [ "$now" -ge "$DEADLINE_EPOCH" ]; then
    DEADLINE_EPOCH=$(date -d "tomorrow 05:30" +%s)
  fi
fi
echo "Deadline: $(date -d "@$DEADLINE_EPOCH")"

run_with_deadline() {
  local name="$1"; shift
  local now remaining timeout_min
  now=$(date +%s)
  remaining=$(( DEADLINE_EPOCH - now ))
  if [ "$remaining" -le 600 ]; then
    echo "SKIP $name: weniger als 10 Min. bis zur Deadline uebrig."
    return 1
  fi
  timeout_min=$(( remaining / 60 ))
  if [ "$timeout_min" -gt 180 ]; then timeout_min=180; fi
  echo "=== $name (Zeitbudget ${timeout_min}min) ==="
  timeout "${timeout_min}m" "$@"
  echo "=== $name Ende: $(date) ==="
}

DOW=$(date +%u)   # 1=Montag ... 7=Sonntag
DOM=$(date +%d)

if [ "$DOW" = "7" ]; then
  # Wöchentlich (6.7b): Allokator-Prüfung, mehr rollierende Fenster als der
  # tägliche Walk-Forward.
  run_with_deadline "Meta-Backtest" venv/bin/python -m scripts.meta_backtest \
    --step-years 1 --max-combos 60 --workers 0
  run_with_deadline "CPCV" venv/bin/python -m scripts.cpcv \
    --total 20 --n-blocks 6 --test-blocks 2 --max-combos 60 --workers 0
else
  # Täglich (6.7a): Registry-Refresh.
  run_with_deadline "Walk-Forward" venv/bin/python -m scripts.walk_forward \
    --total 20 --max-combos 60 --workers 0 --holdout 2
fi

if [ "$DOM" = "01" ]; then
  # Monatlich (6.7c): Quellen-Wirkung + Krisen-Vergleich.
  run_with_deadline "Quellen-Ablation" venv/bin/python -m scripts.source_ablation
  run_with_deadline "Stress-Test" venv/bin/python -m scripts.stress_test
fi

# Rotation: Reports älter als 30 Tage entfernen (Muster scripts/backup.sh).
find "$REPORT_DIR" -name "nightly_research_*.log" -mtime +30 -delete

echo "=== Nightly Research Ende: $(date) ==="
